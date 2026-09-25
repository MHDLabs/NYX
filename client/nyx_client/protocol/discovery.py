"""
Server discovery and multi-relay selection.

Whitepaper Sections 14-15:
  - Bootstrap list (bundled)
  - Local configuration preferences
  - Discovery endpoints on relays
  - Measure latency / reachability
  - Composite scoring (latency, uptime, reputation, capacity, ...)
  - Keep top alternatives for failover

Server list can be refreshed by any connected relay via
GET /api/v3/trust/servers or GET /api/v3/discovery/servers.
"""

from __future__ import annotations

import json
import socket
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from nyx_client.config.logging import get_logger

log = get_logger(__name__)

# Bundled bootstrap (signed list would be verified in production)
DEFAULT_BOOTSTRAP: List[dict] = [
    {
        "id": "relay1",
        "endpoint": "nyx://relay1.nyx.network",
        "trust_level": 2,
        "reputation": 0.8,
    },
]


@dataclass
class ServerInfo:
    id: str
    endpoint: str
    trust_level: int = 0
    reputation: float = 0.0
    latency_ms: float = 9999.0
    packet_loss: float = 1.0
    uptime: float = 0.0
    capacity: float = 0.5
    last_seen: float = 0.0
    reachable: bool = False
    score: float = 0.0
    source: str = "bootstrap"  # bootstrap | config | discovery

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ServerInfo":
        return cls(
            id=str(data.get("id") or data.get("relay_id") or data.get("endpoint", "unknown")),
            endpoint=str(data.get("endpoint") or data.get("url") or ""),
            trust_level=int(data.get("trust_level", 0)),
            reputation=float(data.get("reputation", 0.0)),
            latency_ms=float(data.get("latency_ms", 9999.0)),
            packet_loss=float(data.get("packet_loss", 1.0)),
            uptime=float(data.get("uptime", 0.0)),
            capacity=float(data.get("capacity", data.get("available_capacity_ratio", 0.5))),
            last_seen=float(data.get("last_seen", 0.0)),
            reachable=bool(data.get("reachable", False)),
            score=float(data.get("score", 0.0)),
            source=str(data.get("source", "discovery")),
        )


def normalize_endpoint(endpoint: str) -> str:
    if endpoint.startswith("nyx://"):
        return "https://" + endpoint[len("nyx://"):]
    if not endpoint.startswith("http://") and not endpoint.startswith("https://"):
        return "https://" + endpoint
    return endpoint


def measure_latency(endpoint: str, timeout: float = 3.0) -> tuple[bool, float]:
    """
    TCP/TLS handshake timing as latency proxy (whitepaper Section 15).
    Returns (reachable, latency_ms).
    """
    url = normalize_endpoint(endpoint)
    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not host:
        return False, 9999.0

    start = time.perf_counter()
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        if parsed.scheme == "https":
            ctx = ssl.create_default_context()
            try:
                sock = ctx.wrap_socket(sock, server_hostname=host)
            except ssl.SSLError:
                # Still count TCP success with high latency penalty path
                sock.close()
                ms = (time.perf_counter() - start) * 1000
                return True, ms + 500  # TLS failed but host reachable
        sock.close()
        ms = (time.perf_counter() - start) * 1000
        return True, ms
    except OSError:
        return False, 9999.0


def composite_score(server: ServerInfo, weights: Optional[dict] = None) -> float:
    """
    Whitepaper Section 14 composite server selection score.
    Higher is better.
    """
    w = weights or {
        "latency": 0.20,
        "packetloss": 0.10,
        "uptime": 0.15,
        "reputation": 0.20,
        "capacity": 0.10,
        "trust": 0.15,
        "failures": 0.05,
        "abuse": 0.05,
    }

    # normalize latency: 0ms -> 1.0, 2000ms -> 0.0
    lat = max(0.0, min(1.0, 1.0 - (server.latency_ms / 2000.0)))
    pl = 1.0 - max(0.0, min(1.0, server.packet_loss))
    up = max(0.0, min(1.0, server.uptime))
    rep = max(0.0, min(1.0, server.reputation))
    cap = max(0.0, min(1.0, server.capacity))
    trust = max(0.0, min(1.0, server.trust_level / 4.0))
    fail_pen = 0.0 if server.reachable else 1.0

    score = (
        w["latency"] * lat
        + w["packetloss"] * pl
        + w["uptime"] * up
        + w["reputation"] * rep
        + w["capacity"] * cap
        + w["trust"] * trust
        - w["failures"] * fail_pen
    )
    return score


class ServerDirectory:
    """
    Maintains and refreshes the known-server list.
    Persists to data_dir/servers.json.
    """

    def __init__(self, data_dir: Path, bootstrap: Optional[List[dict]] = None) -> None:
        self.data_dir = data_dir
        self.path = data_dir / "servers.json"
        self.servers: Dict[str, ServerInfo] = {}
        self._load(bootstrap or DEFAULT_BOOTSTRAP)

    def _load(self, bootstrap: List[dict]) -> None:
        if self.path.is_file():
            try:
                raw = json.loads(self.path.read_text())
                for item in raw.get("servers", []):
                    s = ServerInfo.from_dict(item)
                    if s.endpoint:
                        self.servers[s.endpoint] = s
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("discovery.load_failed", error=str(exc))
        if not self.servers:
            for item in bootstrap:
                s = ServerInfo.from_dict({**item, "source": "bootstrap"})
                if s.endpoint:
                    self.servers[s.endpoint] = s

    def save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": time.time(),
            "servers": [s.to_dict() for s in self.servers.values()],
        }
        self.path.write_text(json.dumps(payload, indent=2))

    def upsert(self, info: ServerInfo) -> None:
        existing = self.servers.get(info.endpoint)
        if existing:
            # Merge: keep better reputation / trust from either source
            info.reputation = max(info.reputation, existing.reputation)
            info.trust_level = max(info.trust_level, existing.trust_level)
            if existing.latency_ms < info.latency_ms and existing.reachable:
                info.latency_ms = existing.latency_ms
                info.reachable = existing.reachable
        self.servers[info.endpoint] = info

    def merge_discovery(self, items: List[dict], source: str = "discovery") -> int:
        """Merge server entries advertised by a relay. Returns count added/updated."""
        n = 0
        for item in items:
            try:
                s = ServerInfo.from_dict({**item, "source": source})
                if not s.endpoint:
                    continue
                self.upsert(s)
                n += 1
            except (TypeError, ValueError):
                continue
        if n:
            self.save()
        log.info("discovery.merged", count=n, source=source)
        return n

    def probe_all(self, timeout: float = 3.0) -> None:
        for ep, server in list(self.servers.items()):
            ok, ms = measure_latency(ep, timeout=timeout)
            server.reachable = ok
            server.latency_ms = ms
            server.last_seen = time.time() if ok else server.last_seen
            server.score = composite_score(server)
        self.save()

    def ranked(self, min_trust: int = 0, only_reachable: bool = False) -> List[ServerInfo]:
        items = list(self.servers.values())
        if only_reachable:
            items = [s for s in items if s.reachable]
        items = [s for s in items if s.trust_level >= min_trust]
        for s in items:
            s.score = composite_score(s)
        items.sort(key=lambda s: s.score, reverse=True)
        return items

    def best(self, min_trust: int = 0) -> Optional[ServerInfo]:
        ranked = self.ranked(min_trust=min_trust)
        # Prefer reachable; if none reachable, return best known for offline config
        reachable = [s for s in ranked if s.reachable]
        if reachable:
            return reachable[0]
        return ranked[0] if ranked else None

    def alternatives(self, n: int = 5, min_trust: int = 0) -> List[ServerInfo]:
        return self.ranked(min_trust=min_trust)[:n]

    def fetch_from_relay(self, endpoint: str, timeout: float = 10.0) -> int:
        """
        Ask a relay for its known server list.
        Tries /api/v3/discovery/servers then /api/v3/trust/servers.
        """
        base = normalize_endpoint(endpoint).rstrip("/")
        headers = {"Accept": "application/json", "User-Agent": "nyx-client/0.1.0"}
        for path in ("/api/v3/discovery/servers", "/api/v3/trust/servers"):
            url = base + path
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = json.loads(resp.read().decode())
                items = data if isinstance(data, list) else data.get("servers", [])
                if items:
                    return self.merge_discovery(items, source="relay:" + endpoint)
            except Exception as exc:
                log.debug("discovery.fetch_failed", url=url, error=str(exc))
                continue
        return 0
