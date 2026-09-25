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
import os
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
        self.relay_base_url: str = ""
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
        url = (manifest.artifact_url or manifest.artifact or "").strip()
        if url and not url.startswith("http"):
            base = (self.relay_base_url or "").rstrip("/")
            if not base:
                raise ValueError(
                    "artifact_url is relative and no relay base URL is set — connect first"
                )
            url = base + (url if url.startswith("/") else "/" + url)
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
        Extract package and pip-install into the current Python environment.
        Also stages under data_dir/updates/current for reference.
        """
        import subprocess
        import sys
        self.state = UpdateState.STAGING
        current = self.update_dir / "current"
        backup = self.update_dir / "previous"
        extract_to = self.update_dir / "extract" / manifest.version
        try:
            if current.exists():
                if backup.exists():
                    shutil.rmtree(backup, ignore_errors=True)
                current.rename(backup)
            current.mkdir(parents=True, exist_ok=True)
            if extract_to.exists():
                shutil.rmtree(extract_to, ignore_errors=True)
            extract_to.mkdir(parents=True, exist_ok=True)

            name = artifact_path.name.lower()
            if name.endswith(".tar.gz") or name.endswith(".tgz"):
                import tarfile
                with tarfile.open(artifact_path, "r:gz") as tf:
                    tf.extractall(extract_to)
            elif name.endswith(".zip"):
                import zipfile
                with zipfile.ZipFile(artifact_path, "r") as zf:
                    zf.extractall(extract_to)
            else:
                shutil.copy2(artifact_path, current / artifact_path.name)

            # Find package root (directory containing pyproject.toml or setup.py)
            pkg_root = None
            for root, dirs, files in os.walk(extract_to):
                if "pyproject.toml" in files or "setup.py" in files or "nyx_client" in dirs:
                    pkg_root = Path(root)
                    if "pyproject.toml" in files or "setup.py" in files:
                        break
            if pkg_root is None:
                pkg_root = extract_to

            # Copy tree to updates/current
            for item in pkg_root.iterdir():
                dest = current / item.name
                if item.is_dir():
                    if dest.exists():
                        shutil.rmtree(dest, ignore_errors=True)
                    shutil.copytree(item, dest)
                else:
                    shutil.copy2(item, dest)

            install_target = str(pkg_root)
            pip_ok = False
            last_err = ""
            for args in (
                [sys.executable, "-m", "pip", "install", "--upgrade", "--force-reinstall",
                 "--no-deps", install_target],
                [sys.executable, "-m", "pip", "install", "--upgrade", "--force-reinstall",
                 "--user", "--no-deps", install_target],
                [sys.executable, "-m", "pip", "install", "-e", install_target],
            ):
                try:
                    proc = subprocess.run(
                        args, capture_output=True, text=True, timeout=180,
                    )
                    if proc.returncode == 0:
                        pip_ok = True
                        break
                    last_err = (proc.stderr or proc.stdout or "")[-400:]
                except Exception as exc:
                    last_err = str(exc)

            # Always copy package tree next to a .pth so next run can find it
            shadow = self.update_dir / "site"
            shadow.mkdir(parents=True, exist_ok=True)
            pkg_src = pkg_root / "nyx_client"
            if pkg_src.is_dir():
                dest = shadow / "nyx_client"
                if dest.exists():
                    shutil.rmtree(dest, ignore_errors=True)
                shutil.copytree(pkg_src, dest)
                pth = shadow / "nyx_update.pth"
                pth.write_text(str(shadow) + "\n")
                # inject into current process
                if str(shadow) not in sys.path:
                    sys.path.insert(0, str(shadow))

            (self.update_dir / "INSTALLED_VERSION").write_text(manifest.version + "\n")
            (self.update_dir / "LAST_INSTALL.txt").write_text(
                f"version={manifest.version}\n"
                f"pip_ok={pip_ok}\n"
                f"path={install_target}\n"
                f"shadow={shadow}\n"
                f"restart the client now\n"
                f"pip_err={last_err[:300]}\n"
            )
            log.info(
                "update.installed",
                version=manifest.version,
                path=install_target,
                pip_ok=pip_ok,
            )
            self.state = UpdateState.IDLE
            if not pip_ok:
                log.warning("update.pip_soft_fail", error=last_err[:200])
        except Exception as exc:
            self.state = UpdateState.ERROR
            log.warning("update.install_failed", error=str(exc))
            raise

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
        keys = dict(self.release_public_keys or {})
        if not keys:
            log.warning("update.no_release_keys_configured")
            return False
        return verify_manifest_signature(m, keys)

    @staticmethod
    def _http_get_json(url: str, timeout: float = 15.0) -> dict:
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "nyx-client/0.1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
