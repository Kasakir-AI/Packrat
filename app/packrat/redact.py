"""Keep the API key out of logs, tracebacks, the panel log pane and the command preview.

The key travels only in the PACKRAT_API_KEY environment variable and the HTTP Authorization header.
Every value registered here is replaced by '***' wherever text is written out.
"""
from __future__ import annotations

import logging
import sys

MASK = "***"
_SECRETS: set[str] = set()


def register(secret: str | None) -> None:
    """Remember a secret so redact() masks it. Very short values are ignored (they would mask
    ordinary words); e6ai API keys are 24 characters."""
    if secret and len(secret.strip()) >= 6:
        _SECRETS.add(secret.strip())


def forget_all() -> None:
    _SECRETS.clear()


def redact(text, extra: str | None = None):
    """Return text with every registered secret (and `extra`, if given) replaced by '***'."""
    if not isinstance(text, str):
        return text
    secrets = set(_SECRETS)
    if extra and len(extra.strip()) >= 6:
        secrets.add(extra.strip())
    for s in sorted(secrets, key=len, reverse=True):
        if s in text:
            text = text.replace(s, MASK)
    return text


class RedactingFormatter(logging.Formatter):
    """A Formatter whose output (message, arguments and traceback text) never contains a secret."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def install_excepthook() -> None:
    """Uncaught tracebacks go to stderr without logging; mask secrets there too."""
    if getattr(sys.excepthook, "_e6ai_redacting", False):
        return
    import traceback

    def hook(etype, value, tb):
        sys.stderr.write(redact("".join(traceback.format_exception(etype, value, tb))))

    hook._e6ai_redacting = True  # type: ignore[attr-defined]
    sys.excepthook = hook
