"""Passwords, API keys and OAuth tokens for sites (and the proxy / database passwords).

They never go into settings.json or sites.json. With the 'keyring' package (installed with Packrat) they
are kept in the Windows Credential Manager (macOS Keychain, Linux Secret Service); without a usable
keyring backend they are kept in memory for this session only, and the window says so.
"""
from __future__ import annotations

import logging
import threading

log = logging.getLogger("packrat.grab")
SERVICE = "Packrat"
_mem: dict[tuple[str, str], str] = {}
_lock = threading.Lock()
_kr = None
_checked = False


def _keyring():
    global _kr, _checked
    if _checked:
        return _kr
    _checked = True
    try:
        import keyring  # type: ignore
        from keyring.backends import fail  # type: ignore
        kr = keyring.get_keyring()
        if isinstance(kr, fail.Keyring) or getattr(kr, "priority", 1) <= 0:
            _kr = None
        else:
            _kr = keyring
    except Exception:  # noqa: BLE001 - ImportError, broken backend, no D-Bus ...
        _kr = None
    return _kr


def _name(scope: str, key: str) -> str:
    return f"{scope}|{key}"


# Windows keeps at most 2560 bytes per saved credential (1280 characters). Longer values - the cookies of a
# web login, long tokens - are split over several entries: "name" holds a marker with the count, "name#0",
# "name#1", ... hold the pieces.
CHUNK = 1000
MARK = "\x01packrat-parts:"


def _parts_count(v: str) -> int:
    if v and v.startswith(MARK):
        try:
            return max(0, int(v[len(MARK):]))
        except ValueError:
            return 0
    return 0


def get_secret(scope: str, key: str) -> str:
    with _lock:
        if (scope, key) in _mem:
            return _mem[(scope, key)]
    kr = _keyring()
    if kr is None:
        return ""
    name = _name(scope, key)
    try:
        v = kr.get_password(SERVICE, name) or ""
        n = _parts_count(v)
        if n:
            v = "".join(kr.get_password(SERVICE, f"{name}#{i}") or "" for i in range(n))
    except Exception as e:  # noqa: BLE001
        log.debug("keyring read failed: %s", e)
        return ""
    with _lock:
        _mem[(scope, key)] = v
    return v


def set_secret(scope: str, key: str, value: str) -> bool:
    """Returns True when stored persistently."""
    with _lock:
        _mem[(scope, key)] = value or ""
    kr = _keyring()
    if kr is None:
        return False
    name = _name(scope, key)

    def drop(n: str):
        try:
            kr.delete_password(SERVICE, n)
        except Exception:  # noqa: BLE001 - nothing saved
            pass
    try:
        try:
            old = _parts_count(kr.get_password(SERVICE, name) or "")
        except Exception:  # noqa: BLE001
            old = 0
        pieces = [value[i:i + CHUNK] for i in range(0, len(value), CHUNK)] if value and len(value) > CHUNK else []
        if pieces:
            for i, piece in enumerate(pieces):
                kr.set_password(SERVICE, f"{name}#{i}", piece)
            kr.set_password(SERVICE, name, f"{MARK}{len(pieces)}")
        elif value:
            kr.set_password(SERVICE, name, value)
        else:
            drop(name)
        for i in range(len(pieces), old):
            drop(f"{name}#{i}")
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("could not save a password in the system keyring (%s); kept for this session only", e)
        return False


def forget_scope(scope: str, keys) -> None:
    for k in keys:
        set_secret(scope, k, "")


def register_redaction(value: str) -> None:
    if value:
        from .. import redact
        redact.register(value)
