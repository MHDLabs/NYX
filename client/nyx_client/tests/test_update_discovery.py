"""Tests for auto-update and multi-server discovery."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from nyx_client.update.updater import (
    UpdateManifest,
    UpdateClient,
    version_greater,
    verify_manifest_signature,
    verify_artifact_hash,
    parse_version,
)
from nyx_client.protocol.discovery import (
    ServerDirectory,
    ServerInfo,
    composite_score,
    measure_latency,
)


def _key_pair():
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes_raw() if hasattr(priv.public_key(), "public_bytes_raw") else priv.public_key().public_bytes(
        encoding=__import__("cryptography.hazmat.primitives.serialization", fromlist=["Encoding"]).Encoding.Raw,
        format=__import__("cryptography.hazmat.primitives.serialization", fromlist=["PublicFormat"]).PublicFormat.Raw,
    )
    return priv, pub


def _signed_manifest(priv, pub_key_id="release_key_test", version="0.2.0"):
    m = UpdateManifest(
        product="nyx-client",
        version=version,
        minimum_supported_version="0.1.0",
        release_channel="stable",
        artifact="nyx-client-0.2.0.tar.gz",
        artifact_hash="sha256:" + "ab" * 32,
        signature="",
        signing_key_id=pub_key_id,
        published_at="2026-01-01T00:00:00Z",
        artifact_url="https://example.com/nyx-client-0.2.0.tar.gz",
    )
    sig = priv.sign(m.canonical_bytes())
    # reconstruct with signature
    return UpdateManifest(
        product=m.product,
        version=m.version,
        minimum_supported_version=m.minimum_supported_version,
        release_channel=m.release_channel,
        artifact=m.artifact,
        artifact_hash=m.artifact_hash,
        signature="ed25519:" + sig.hex(),
        signing_key_id=m.signing_key_id,
        published_at=m.published_at,
        artifact_url=m.artifact_url,
    )


def test_version_compare() -> None:
    assert version_greater("0.2.0", "0.1.0")
    assert not version_greater("0.1.0", "0.2.0")
    assert parse_version("1.2.3") == (1, 2, 3)


def test_manifest_signature_roundtrip() -> None:
    priv, pub = _key_pair()
    m = _signed_manifest(priv)
    assert verify_manifest_signature(m, {"release_key_test": pub})
    # wrong key fails
    _, pub2 = _key_pair()
    assert not verify_manifest_signature(m, {"release_key_test": pub2})


def test_update_check_finds_newer(tmp_path: Path) -> None:
    priv, pub = _key_pair()
    m = _signed_manifest(priv, version="9.9.9")
    client = UpdateClient(
        data_dir=tmp_path,
        release_public_keys={"release_key_test": pub},
        current_version="0.1.0",
    )
    result = client.check(relay_manifest={
        "product": m.product,
        "version": m.version,
        "minimum_supported_version": m.minimum_supported_version,
        "release_channel": m.release_channel,
        "artifact": m.artifact,
        "artifact_hash": m.artifact_hash,
        "signature": m.signature,
        "signing_key_id": m.signing_key_id,
        "published_at": m.published_at,
        "artifact_url": m.artifact_url,
    }, fetch_github=False)
    assert result.update_available
    assert result.candidate is not None
    assert result.candidate.version == "9.9.9"
    assert result.source == "relay"


def test_update_rejects_unsigned(tmp_path: Path) -> None:
    client = UpdateClient(data_dir=tmp_path, release_public_keys={}, current_version="0.1.0")
    result = client.check(relay_manifest={
        "product": "nyx-client",
        "version": "9.0.0",
        "minimum_supported_version": "0.1.0",
        "release_channel": "stable",
        "artifact": "x.tar.gz",
        "artifact_hash": "sha256:00",
        "signature": "ed25519:00",
        "signing_key_id": "k",
        "published_at": "2026-01-01T00:00:00Z",
    }, fetch_github=False)
    assert not result.update_available


def test_artifact_hash(tmp_path: Path) -> None:
    f = tmp_path / "art.bin"
    f.write_bytes(b"hello-nyx")
    import hashlib
    digest = hashlib.sha256(b"hello-nyx").hexdigest()
    assert verify_artifact_hash(f, "sha256:" + digest)
    assert not verify_artifact_hash(f, "sha256:" + "00" * 32)


def test_composite_score_prefers_low_latency() -> None:
    fast = ServerInfo(id="a", endpoint="nyx://fast", latency_ms=20, reputation=0.5, uptime=0.9, trust_level=2, reachable=True)
    slow = ServerInfo(id="b", endpoint="nyx://slow", latency_ms=800, reputation=0.5, uptime=0.9, trust_level=2, reachable=True)
    assert composite_score(fast) > composite_score(slow)


def test_server_directory_persist(tmp_path: Path) -> None:
    d = ServerDirectory(tmp_path)
    d.upsert(ServerInfo(id="r1", endpoint="nyx://r1.example", trust_level=2, reputation=0.7))
    d.save()
    d2 = ServerDirectory(tmp_path)
    assert "nyx://r1.example" in d2.servers
    assert d2.servers["nyx://r1.example"].trust_level == 2


def test_server_directory_merge_and_rank(tmp_path: Path) -> None:
    d = ServerDirectory(tmp_path, bootstrap=[])
    d.merge_discovery([
        {"id": "a", "endpoint": "nyx://a.example", "trust_level": 1, "reputation": 0.4},
        {"id": "b", "endpoint": "nyx://b.example", "trust_level": 3, "reputation": 0.9},
    ])
    d.servers["nyx://a.example"].reachable = True
    d.servers["nyx://a.example"].latency_ms = 100
    d.servers["nyx://b.example"].reachable = True
    d.servers["nyx://b.example"].latency_ms = 50
    ranked = d.ranked()
    assert ranked[0].endpoint == "nyx://b.example"


def test_measure_latency_localhost() -> None:
    # May or may not be reachable; just ensure API returns tuple
    ok, ms = measure_latency("http://127.0.0.1:1", timeout=0.2)
    assert isinstance(ok, bool)
    assert ms >= 0
