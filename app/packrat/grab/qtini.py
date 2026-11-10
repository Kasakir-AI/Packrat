"""Read and write Qt QSettings INI files (Grabber's defaults.ini / settings.ini / site settings).

Qt specifics handled: [General] keys have no prefix, other sections prefix their keys ("login/type");
'\\' inside a key is a sub-group separator ("post\\url" in [login] -> "login/post/url"); quoted values
with escapes; unquoted commas make a string list ("a", "b" -> ["a", "b"]); @Variant(...) values are
kept as raw strings; %XX escapes in key names.
"""
from __future__ import annotations

import re
from pathlib import Path


def _unescape_key(k: str) -> str:
    k = k.strip().replace("\\", "/")
    return re.sub(r"%([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), k)


def _parse_value(raw: str):
    raw = raw.strip()
    if raw == "":
        return ""
    parts, cur, quoted, i, was_quoted = [], [], False, 0, False
    while i < len(raw):
        c = raw[i]
        if quoted:
            if c == "\\" and i + 1 < len(raw):
                nxt = raw[i + 1]
                cur.append({"n": "\n", "t": "\t", "r": "\r", "0": "\0", '"': '"', "\\": "\\"}.get(nxt, nxt))
                i += 2
                continue
            if c == '"':
                quoted = False
                i += 1
                continue
            cur.append(c)
        else:
            if c == '"':
                quoted = was_quoted = True
            elif c == ",":
                parts.append("".join(cur).strip() if not was_quoted else "".join(cur))
                cur, was_quoted = [], False
                i += 1
                while i < len(raw) and raw[i] == " ":
                    i += 1
                continue
            elif c == "\\" and i + 1 < len(raw):
                nxt = raw[i + 1]
                cur.append({"n": "\n", "t": "\t", "r": "\r"}.get(nxt, nxt))
                i += 2
                continue
            else:
                cur.append(c)
        i += 1
    last = "".join(cur)
    parts.append(last if was_quoted else last.strip())
    return parts if len(parts) > 1 else parts[0]


def loads(text: str) -> dict:
    out: dict = {}
    section = ""
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith((";", "#")):
            continue
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1].strip()
            section = "" if section.lower() == "general" else _unescape_key(section)
            continue
        if "=" not in s:
            continue
        k, v = s.split("=", 1)
        key = _unescape_key(k)
        out[f"{section}/{key}" if section else key] = _parse_value(v)
    return out


def load(path) -> dict:
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        return loads(p.read_text("utf-8-sig"))
    except (OSError, UnicodeDecodeError):
        try:
            return loads(p.read_text("latin-1"))
        except OSError:
            return {}


def to_bool(v, default=False) -> bool:
    if isinstance(v, bool):
        return v
    if v is None or v == "":
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def to_int(v, default=0) -> int:
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return default
