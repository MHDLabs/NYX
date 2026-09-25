"""
Logging configuration for the NYX client.

Uses the standard library logging module for zero external dependencies
at the skeleton stage. When the full dependency set is installed,
structlog can be layered on top without changing call sites
(get_logger returns a compatible interface).

Whitepaper alignment:
  - Operational simplicity and developer experience.
  - Defense-in-depth: logs never contain private keys, plaintext E2EE
    content, or recovery mnemonics.
"""

from __future__ import annotations

import logging
import sys
from typing import Any


_CONFIGURED = False


def configure_logging(
    level: str = "INFO",
    json_logs: bool = False,
) -> None:
    """
    Configure process-wide logging.

    Parameters
    ----------
    level:
        One of DEBUG, INFO, WARNING, ERROR, CRITICAL.
    json_logs:
        Reserved for future structured JSON output. Currently ignored
        so that the skeleton runs with zero third-party packages.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_level = getattr(logging, level.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(log_level)

    # ISO-ish timestamp + level + logger name + message
    fmt = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
    datefmt = "%Y-%m-%dT%H:%M:%SZ"
    handler.setFormatter(logging.Formatter(fmt=fmt, datefmt=datefmt))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level)

    # Quiet noisy third-party loggers once they appear
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    _CONFIGURED = True


class _BoundLogger:
    """
    Thin wrapper that accepts both classic %/f-string messages and
    keyword context (the style used by structlog). Extra kwargs are
    appended to the message so that future migration is painless.
    """

    def __init__(self, name: str) -> None:
        self._logger = logging.getLogger(name)

    def _format(self, event: str, **kwargs: Any) -> str:
        if not kwargs:
            return event
        extras = " ".join(f"{k}={v!r}" for k, v in kwargs.items())
        return f"{event} {extras}"

    def debug(self, event: str, **kwargs: Any) -> None:
        self._logger.debug(self._format(event, **kwargs))

    def info(self, event: str, **kwargs: Any) -> None:
        self._logger.info(self._format(event, **kwargs))

    def warning(self, event: str, **kwargs: Any) -> None:
        self._logger.warning(self._format(event, **kwargs))

    def error(self, event: str, **kwargs: Any) -> None:
        self._logger.error(self._format(event, **kwargs))

    def critical(self, event: str, **kwargs: Any) -> None:
        self._logger.critical(self._format(event, **kwargs))

    def exception(self, event: str, **kwargs: Any) -> None:
        self._logger.exception(self._format(event, **kwargs))


def get_logger(name: str) -> _BoundLogger:
    """Return a logger compatible with future structlog migration."""
    return _BoundLogger(name)
