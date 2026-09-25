"""
Storage layer.

Whitepaper: Local DB (SQLite) + Encrypted Cache + Config + Queue.
Sensitive columns encrypted at application layer (AEAD).
Extension point for SQLCipher when available.
"""

from nyx_client.storage.db import Database
from nyx_client.storage.profile import ProfileStore, derive_profile_key_from_passphrase
from nyx_client.storage.messages import MessageStore, StoredMessage
from nyx_client.storage.contacts import ContactStore, Contact
from nyx_client.storage.user_prefs import UserPrefs, UserProfile
from nyx_client.storage.rooms import RoomStore, Room

__all__ = [
    "Database",
    "ProfileStore",
    "derive_profile_key_from_passphrase",
    "MessageStore",
    "StoredMessage",
    "ContactStore",
    "Contact",
    "UserPrefs",
    "UserProfile",
    "RoomStore",
    "Room",
]
