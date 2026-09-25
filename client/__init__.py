"""NYX Client Package"""

__version__ = "0.0.6"

try:
    from .ui import ReplUI, NyxTUI
    from .commands import CommandContext, CommandRegistry, registry
    from .db import NYXDatabase
    from .crypto import Identity
    from .config import load_settings
except ImportError:
    from ui import ReplUI, NyxTUI
    from commands import CommandContext, CommandRegistry, registry
    from db import NYXDatabase
    from crypto import Identity
    from config import load_settings

__all__ = [
    "__version__",
    "ReplUI",
    "NyxTUI",
    "CommandContext",
    "CommandRegistry",
    "registry",
    "NYXDatabase",
    "Identity",
    "load_settings",
]