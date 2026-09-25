"""
Application layer.

Whitepaper: Messaging, Identity, Sync, Search, AI, Plugins.
MVP: E2EE, MessagingService, Commands, NyxApp facade.
"""

from nyx_client.core.e2ee import DMSession, open_dm_session, generate_dm_keypair
from nyx_client.core.messaging import MessagingService, DecryptedMessage
from nyx_client.core.commands import (
    CommandContext,
    CommandResult,
    CommandRegistry,
    registry,
)
from nyx_client.core.app import NyxApp

__all__ = [
    "DMSession",
    "open_dm_session",
    "generate_dm_keypair",
    "MessagingService",
    "DecryptedMessage",
    "CommandContext",
    "CommandResult",
    "CommandRegistry",
    "registry",
    "NyxApp",
]
