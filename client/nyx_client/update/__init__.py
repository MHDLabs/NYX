"""
Update subsystem (whitepaper Sections 24-25).
"""

from nyx_client.update.updater import (
    UpdateClient,
    UpdateManifest,
    UpdateCheckResult,
    UpdateState,
    version_greater,
    verify_manifest_signature,
    verify_artifact_hash,
)

__all__ = [
    "UpdateClient",
    "UpdateManifest",
    "UpdateCheckResult",
    "UpdateState",
    "version_greater",
    "verify_manifest_signature",
    "verify_artifact_hash",
]
