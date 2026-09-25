"""
Directory helpers: resolve display names and public profiles for users.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from nyx_client.storage.contacts import Contact, ContactStore
from nyx_client.storage.user_prefs import UserPrefs


@dataclass(frozen=True)
class PublicProfile:
    """View of a user suitable for UI (no private keys)."""

    identity_id: str
    display_name: str
    bio: str
    trusted: bool
    is_self: bool = False


class Directory:
    def __init__(
        self,
        contacts: ContactStore,
        prefs: Optional[UserPrefs] = None,
        self_id: Optional[str] = None,
    ) -> None:
        self._contacts = contacts
        self._prefs = prefs
        self._self_id = self_id

    def set_self(self, identity_id: str, prefs: Optional[UserPrefs] = None) -> None:
        self._self_id = identity_id
        if prefs is not None:
            self._prefs = prefs

    def display_name(self, identity_id: str) -> str:
        if self._self_id and identity_id == self._self_id and self._prefs:
            local = self._prefs.get_profile().display_name
            if local:
                return local
        c = self._contacts.get(identity_id)
        if c and c.display_name:
            return c.display_name
        # Short identity fallback
        if identity_id.startswith("nyx1") and len(identity_id) > 16:
            return identity_id[:12] + "..." + identity_id[-6:]
        return identity_id[:28]

    def profile(self, identity_id: str) -> PublicProfile:
        is_self = bool(self._self_id and identity_id == self._self_id)
        if is_self and self._prefs:
            p = self._prefs.get_profile()
            return PublicProfile(
                identity_id=identity_id,
                display_name=p.display_name or self.display_name(identity_id),
                bio=p.bio or "",
                trusted=True,
                is_self=True,
            )
        c = self._contacts.get(identity_id)
        if c is None:
            return PublicProfile(
                identity_id=identity_id,
                display_name=self.display_name(identity_id),
                bio="",
                trusted=False,
                is_self=False,
            )
        return PublicProfile(
            identity_id=c.identity_id,
            display_name=c.display_name or self.display_name(identity_id),
            bio=(c.notes or ""),
            trusted=c.trusted,
            is_self=False,
        )

    def set_remote_profile(
        self,
        identity_id: str,
        display_name: Optional[str] = None,
        bio: Optional[str] = None,
        public_key: Optional[bytes] = None,
        trusted: bool = False,
    ) -> Contact:
        """Save/update another user's visible profile locally."""
        return self._contacts.upsert(
            identity_id,
            public_key=public_key,
            display_name=display_name,
            notes=bio,
            trusted=trusted,
        )
