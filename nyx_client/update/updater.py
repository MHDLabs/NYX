"""
Software update system for the NYX client.

Whitepaper Sections 24-25:
  - Signed update manifests
  - Artifact hash verification (never blind-trust GitHub)
  - State machine: Idle -> Checking -> Downloading -> Verifying -> Staging
    -> Health Check -> Commit | Rollback
  - Trust chain: offline root key -> release signing key -> manifest

Sources checked (in order):
  1. Connected relay: GET /api/v3/updates/manifest
  2. GitHub Releases API / raw manifest URL from config

Installation is atomic: download to staging, verify, swap, record version.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, List, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from nyx_client import __version__
from nyx_client.config.logging import get_logger

log = get_logger(__name__)


class UpdateState(str, Enum):
    IDLE = "idle"
    CHECKING = "checking"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    STAGING = "staging"
    HEALTH_CHECK = "health_check"
    COMMIT = "commit"
    ROLLBACK = "rollback"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class UpdateManifest:
    product: str
    version: str
    minimum_supported_version: str
    release_channel: str
    artifact: str
    artifact_hash: str  # "blake2b:<hex>" or "sha256:<hex>"
    signature: str      # "ed25519:<hex>"
    signing_key_id: str
    published_at: str
    rollback_reference: str = ""
    artifact_url: str = ""
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UpdateManifest":
        required = [
            "product", "version", "minimum_supported_version",
            "release_channel", "artifact", "artifact_hash",
            "signature", "signing_key_id", "published_at",
        ]
        for k in required:
            if k not in data:
                raise ValueError(f"manifest missing field: {k}")
        return cls(
            product=str(data["product"]),
            version=str(data["version"]),
            minimum_supported_version=str(data["minimum_supported_version"]),
            release_channel=str(data["release_channel"]),
            artifact=str(data["artifact"]),
            artifact_hash=str(data["artifact_hash"]),
            signature=str(data["signature"]),
            signing_key_id=str(data["signing_key_id"]),
            published_at=str(data["published_at"]),
            rollback_reference=str(data.get("rollback_reference", "")),
            artifact_url=str(data.get("artifact_url", "")),
            notes=str(data.get("notes", "")),
        )

    def canonical_bytes(self) -> bytes:
        """Fields covered by the release signature (excludes signature itself)."""
        payload = {
            "product": self.product,
            "version": self.version,
            "minimum_supported_version": self.minimum_supported_version,
            "release_channel": self.release_channel,
            "artifact": self.artifact,
            "artifact_hash": self.artifact_hash,
            "signing_key_id": self.signing_key_id,
            "published_at": self.published_at,
            "rollback_reference": self.rollback_reference,
            "artifact_url": self.artifact_url,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def parse_version(v: str) -> tuple:
    """Parse semver-like version to comparable tuple."""
    parts = []
    for p in v.strip().lstrip("v").split("."):
        num = ""
        for ch in p:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def version_greater(a: str, b: str) -> bool:
    return parse_version(a) > parse_version(b)


def hash_file(path: Path, algo: str = "sha256") -> str:
    h = hashlib.new(algo if algo != "blake2b" else "blake2b")
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_manifest_signature(
    manifest: UpdateManifest,
    public_keys: dict[str, bytes],
) -> bool:
    """
    Verify Ed25519 signature over canonical manifest bytes.

    public_keys maps signing_key_id -> raw 32-byte Ed25519 public key.
    """
    key_bytes = public_keys.get(manifest.signing_key_id)
    if key_bytes is None:
        log.warning("update.unknown_signing_key", key_id=manifest.signing_key_id)
        return False
    sig_hex = manifest.signature
    if sig_hex.startswith("ed25519:"):
        sig_hex = sig_hex[len("ed25519:"):]
    try:
        sig = bytes.fromhex(sig_hex)
        pub = Ed25519PublicKey.from_public_bytes(key_bytes)
        pub.verify(sig, manifest.canonical_bytes())
        return True
    except (ValueError, InvalidSignature) as exc:
        log.warning("update.signature_invalid", error=str(exc))
        return False


def verify_artifact_hash(path: Path, artifact_hash: str) -> bool:
    if ":" in artifact_hash:
        algo, expected = artifact_hash.split(":", 1)
    else:
        algo, expected = "sha256", artifact_hash
    algo = algo.lower().replace("blake3", "blake2b")  # blake3 fallback
    try:
        actual = hash_file(path, algo if algo in hashlib.algorithms_available else "sha256")
    except ValueError:
        actual = hash_file(path, "sha256")
    return actual.lower() == expected.lower()


@dataclass
class UpdateCheckResult:
    current_version: str
    candidate: Optional[UpdateManifest] = None
    source: str = ""
    update_available: bool = False
    error: str = ""


class UpdateClient:
    """
    Checks GitHub + relay for updates, verifies, and installs.
    """

    def __init__(
        self,
        data_dir: Path,
        channel: str = "stable",
        github_manifest_url: str = "",
        release_public_keys: Optional[dict[str, bytes]] = None,
        current_version: str = __version__,
        auto_install: bool = False,
    ) -> None:
        self.data_dir = data_dir
        self.channel = channel
        self.github_manifest_url = github_manifest_url
        self.release_public_keys = release_public_keys or {}
        self.current_version = current_version
        self.auto_install = auto_install
        self.state = UpdateState.IDLE
        self.update_dir = data_dir / "updates"
        self.update_dir.mkdir(parents=True, exist_ok=True)

    def check(
        self,
        relay_manifest: Optional[dict[str, Any]] = None,
        fetch_github: bool = True,
    ) -> UpdateCheckResult:
        """
        Compare local version against relay and/or GitHub manifests.
        Prefers the higher valid version that matches the channel.
        """
        self.state = UpdateState.CHECKING
        candidates: List[tuple[str, UpdateManifest]] = []

        if relay_manifest:
            try:
                m = UpdateManifest.from_dict(relay_manifest)
                if self._acceptable(m) and self._signature_ok(m):
                    candidates.append(("relay", m))
            except (ValueError, TypeError) as exc:
                log.warning("update.relay_manifest_invalid", error=str(exc))

        if fetch_github and self.github_manifest_url:
            try:
                data = self._http_get_json(self.github_manifest_url)
                m = UpdateManifest.from_dict(data)
                if self._acceptable(m) and self._signature_ok(m):
                    candidates.append(("github", m))
            except Exception as exc:
                log.warning("update.github_check_failed", error=str(exc))

        self.state = UpdateState.IDLE
        if not candidates:
            return UpdateCheckResult(
                current_version=self.current_version,
                update_available=False,
            )

        # Pick highest version
        source, best = max(candidates, key=lambda x: parse_version(x[1].version))
        available = version_greater(best.version, self.current_version)
        return UpdateCheckResult(
            current_version=self.current_version,
            candidate=best if available else None,
            source=source if available else "",
            update_available=available,
        )

    def download_and_verify(self, manifest: UpdateManifest) -> Path:
        """Download artifact to staging and verify hash + re-check signature."""
        if not self._signature_ok(manifest):
            self.state = UpdateState.ERROR
            raise ValueError("manifest signature verification failed")

        self.state = UpdateState.DOWNLOADING
        url = manifest.artifact_url or manifest.artifact
        if not url.startswith("http"):
            raise ValueError("artifact_url must be an absolute HTTP(S) URL")

        staging = self.update_dir / "staging"
        staging.mkdir(parents=True, exist_ok=True)
        target = staging / manifest.artifact.split("/")[-1]

        try:
            urllib.request.urlretrieve(url, str(target))
        except Exception as exc:
            self.state = UpdateState.ERROR
            raise ValueError(f"download failed: {exc}") from exc

        self.state = UpdateState.VERIFYING
        if not verify_artifact_hash(target, manifest.artifact_hash):
            target.unlink(missing_ok=True)
            self.state = UpdateState.ERROR
            raise ValueError("artifact hash mismatch — refusing install")

        # Persist verified manifest
        (staging / "manifest.json").write_text(
            json.dumps({
                "product": manifest.product,
                "version": manifest.version,
                "artifact_hash": manifest.artifact_hash,
                "signing_key_id": manifest.signing_key_id,
                "source_verified": True,
            }, indent=2)
        )
        log.info("update.artifact_verified", version=manifest.version, path=str(target))
        return target

    def install(self, artifact_path: Path, manifest: UpdateManifest) -> None:
        """
        Stage -> commit version record.

        Full binary replacement depends on packaging; this records the
        verified update and extracts archives into updates/current when
        the artifact is a tar/zip of the package tree.
        """
        self.state = UpdateState.STAGING
        current = self.update_dir / "current"
        backup = self.update_dir / "previous"

        try:
            if current.exists():
                if backup.exists():
                    shutil.rmtree(backup, ignore_errors=True)
                shutil.move(str(current), str(backup))

            current.mkdir(parents=True, exist_ok=True)
            # If tarball, extract; otherwise copy file
            name = artifact_path.name.lower()
            if name.endswith(".tar.gz") or name.endswith(".tgz"):
                import tarfile
                with tarfile.open(artifact_path, "r:gz") as tf:
                    tf.extractall(current)
            elif name.endswith(".zip"):
                import zipfile
                with zipfile.ZipFile(artifact_path, "r") as zf:
                    zf.extractall(current)
            else:
                shutil.copy2(artifact_path, current / artifact_path.name)

            self.state = UpdateState.HEALTH_CHECK
            # Minimal health: directory non-empty
            if not any(current.iterdir()):
                raise RuntimeError("staged update is empty")

            self.state = UpdateState.COMMIT
            (self.update_dir / "installed_version").write_text(manifest.version)
            log.info("update.installed", version=manifest.version)
            self.state = UpdateState.IDLE
        except Exception as exc:
            self.state = UpdateState.ROLLBACK
            if backup.exists():
                if current.exists():
                    shutil.rmtree(current, ignore_errors=True)
                shutil.move(str(backup), str(current))
            self.state = UpdateState.ERROR
            raise RuntimeError(f"install failed, rolled back: {exc}") from exc

    def _acceptable(self, m: UpdateManifest) -> bool:
        if m.product not in ("nyx-client", "nyx", "nyx_client"):
            return False
        if m.release_channel != self.channel:
            return False
        # Downgrade protection
        if parse_version(m.version) < parse_version(m.minimum_supported_version):
            return False
        return True

    def _signature_ok(self, m: UpdateManifest) -> bool:
        if not self.release_public_keys:
            # No keys configured: accept only for explicit dev channel tests
            log.warning("update.no_release_keys_configured")
            return False
        return verify_manifest_signature(m, self.release_public_keys)

    @staticmethod
    def _http_get_json(url: str, timeout: float = 15.0) -> dict:
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "nyx-client/0.1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
