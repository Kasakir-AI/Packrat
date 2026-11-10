"""Post-filters and the blacklist (Grabber's search syntax applied to results).

    tag1 tag2           every tag present (AND); * wildcards: *_hair
    -tag                not present
    rating:safe / s     ratings (first letter compared, like Grabber)
    id:>1000 score:<=5  comparisons  >  >=  <  <=   ranges  a..b  ..b  a..
    date:2019-09-02..2019-09-07  (yyyy-MM-dd or MM/dd/yyyy)     age:<1d  (y mo w d h mi s)
    filesize:>=1mb      sizes in b / kb / mb / gb
    source:twitter.com  source starts with
    %token%             the token exists and is not empty;  %artist:count%:>2  expressions
    grabber:alreadyExists | inMd5List | downloaded | favorited | monitored | blacklisted
    advanced:           ( ) & | as in conditional file names, e.g. (cat | dog) & -rating:e

match(tokens) returns "" when the filter matches, else the reason (Grabber's exact messages).
"""
from __future__ import annotations

import fnmatch
import re
from datetime import datetime, timedelta, timezone

from .filename import Executor, Parser, Var, tval


def build_filter(text: str):
    invert = False
    if text.startswith("-"):
        text, invert = text[1:], True
    if text.startswith("%") and text.endswith("%") and len(text) > 1 and text.count("%") == 2:
        return TokenFilter(text[1:-1], invert)
    colon = text.rfind(":") if text.startswith("%") else text.find(":")
    if colon != -1:
        if 0 < colon < len(text) - 1 and text[colon + 1] != ":":
            return MetaFilter(text[:colon].lower(), text[colon + 1:].lower(), invert)
        text = text.replace("::", ":")
    if text.strip():
        return TagFilter(text.strip(), invert)
    return None


class TagFilter:
    def __init__(self, tag: str, invert: bool = False):
        self.tag, self.invert = tag, invert
        self.rx = re.compile(fnmatch.translate(tag), re.I) if "*" in tag else None

    def to_string(self, escape=True) -> str:
        return ("-" if self.invert else "") + (self.tag.replace(":", "::") if escape else self.tag)

    def matches_tag(self, name: str) -> bool:
        return bool(self.rx.match(name)) if self.rx else name == self.tag

    def match(self, tokens: dict, invert: bool = False) -> str:
        if self.invert:
            invert = not invert
        tags = [str(t) for t in (tval(tokens.get("allos")) or [])]
        cond = any(self.matches_tag(t) for t in tags)
        if not cond and not invert:
            return f'image does not contains "{self.tag}"'
        if cond and invert:
            return f'image contains "{self.tag}"'
        return ""


class TokenFilter:
    def __init__(self, token: str, invert: bool = False):
        self.token, self.invert = token, invert

    def to_string(self, escape=True) -> str:
        return ("-" if self.invert else "") + f"%{self.token}%"

    def match(self, tokens: dict, invert: bool = False) -> str:
        from .filename import is_empty
        if self.invert:
            invert = not invert
        cond = self.token in tokens and not is_empty(tokens[self.token])
        if cond and invert:
            return f'image has a "{self.token}" token'
        if not cond and not invert:
            return f'image does not have a "{self.token}" token'
        return ""


def _to_date(text: str):
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def parse_file_size(text: str) -> int:
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kmgt]?i?b?)?\s*", text.lower())
    if not m:
        try:
            return int(text)
        except ValueError:
            return 0
    val, unit = float(m.group(1)), (m.group(2) or "").rstrip("b").rstrip("i")
    mult = {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3, "t": 1024 ** 4}.get(unit, 1)
    return int(round(val * mult))


def _age_to_date(text: str, now: datetime):
    m = re.fullmatch(r"(\d+)(\w+)", text)
    if not m:
        return None
    n, t = int(m.group(1)), m.group(2)
    if t.startswith("y"):
        try:
            return now.replace(year=now.year - n)
        except ValueError:
            return now - timedelta(days=365 * n)
    if t.startswith("mo"):
        y, mth = divmod(now.month - 1 - n, 12)
        try:
            return now.replace(year=now.year + y, month=mth + 1)
        except ValueError:
            return now - timedelta(days=30 * n)
    if t.startswith("w"):
        return now - timedelta(days=7 * n)
    if t.startswith("d"):
        return now - timedelta(days=n)
    if t.startswith("h"):
        return now - timedelta(hours=n)
    if t.startswith("mi"):
        return now - timedelta(minutes=n)
    if t.startswith("s"):
        return now - timedelta(seconds=n)
    return None


def range_check(conv, inp, val: str) -> bool:
    try:
        if val.startswith("..") or val.startswith("<="):
            r = conv(val[2:])
            return r is not None and inp <= r
        if val.endswith(".."):
            r = conv(val[:-2])
            return r is not None and inp >= r
        if val.startswith(">="):
            r = conv(val[2:])
            return r is not None and inp >= r
        if val.startswith("<"):
            r = conv(val[1:])
            return r is not None and inp < r
        if val.startswith(">"):
            r = conv(val[1:])
            return r is not None and inp > r
        if ".." in val:
            a, b = val.split("..", 1)
            ra, rb = conv(a), conv(b)
            return ra is not None and rb is not None and ra <= inp <= rb
        r = conv(val)
        return r is not None and inp == r
    except TypeError:
        return False


def _int(s):
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return 0


class MetaFilter:
    def __init__(self, type_: str, val: str, invert: bool = False):
        self.type, self.val, self.invert = type_, val, invert

    def to_string(self, escape=True) -> str:
        return ("-" if self.invert else "") + f"{self.type}:{self.val}"

    def _res(self, cond: bool, invert: bool, no: str, yes: str) -> str:
        if not cond and not invert:
            return no
        if cond and invert:
            return yes
        return ""

    def match(self, tokens: dict, invert: bool = False) -> str:
        if self.invert:
            invert = not invert
        t, v = self.type, self.val
        if t == "grabber":
            vals = [str(x).lower() for x in (tval(tokens.get("grabber")) or [])]
            return self._res(v.lower() in vals, invert, f'image is not "{v}"', f'image is "{v}"')
        if t == "age":
            date = tval(tokens.get("date"))
            if not isinstance(date, datetime):
                return "An image needs a date to be filtered by age"
            now = tval(tokens.get("TESTS_now")) or datetime.now(timezone.utc)
            cond = range_check(lambda s: _age_to_date(s, now), date, v)
            return self._res(not cond, invert, f"image's {t} does not match", f"image's {t} match")
        if t.startswith("%") and t.endswith("%"):
            p = Parser(t)
            var = p._variable()
            inp = Executor(tokens, _NoSettings()).variable(var.name, var.opts) if isinstance(var, Var) else ""
            try:
                num = int(inp)
                cond = range_check(_int, num, v)
            except ValueError:
                cond = inp == v
            return self._res(cond, invert, f"expression '{t}' ('{inp}') does not match",
                             f"expression '{t}' ('{inp}') match")
        if t not in tokens:
            avail = '", "'.join(sorted(tokens))
            return f'unknown type "{t}" (available types: "{avail}")'
        tok = tval(tokens[t])
        if isinstance(tok, datetime):
            plain = _to_date(v)
            if plain is not None:   # date:2016-08-18 = that whole day (Grabber compares with midnight)
                cond = tok.astimezone(timezone.utc).date() == plain.date() or tok == plain
            else:
                cond = range_check(_to_date, tok, v)
            return self._res(cond, invert, f"image's {t} does not match", f"image's {t} match")
        if (isinstance(tok, (int, float)) and not isinstance(tok, bool)) or t == "score":
            if t == "score":
                try:
                    num = round(float(tok) * 1000)
                except (TypeError, ValueError):
                    num = 0
                cond = range_check(lambda s: round(float(s) * 1000), num, v)
            elif t == "filesize":
                cond = range_check(parse_file_size, int(tok), v)
            else:
                cond = range_check(_int, int(tok), v)
            return self._res(cond, invert, f"image's {t} does not match", f"image's {t} match")
        if t == "rating":
            assoc = {"g": "general", "s": "safe", "q": "questionable", "e": "explicit"}
            val = assoc.get(v, v)
            cond = bool(val) and str(tok or "").lower().startswith(val[0])
            return self._res(cond, invert, f'image is not "{val}"', f'image is "{val}"')
        if t == "source":
            src = tok[0] if isinstance(tok, list) and tok else tok
            cond = str(src or "").lower().startswith(v)
            return self._res(cond, invert, f'image\'s source does not starts with "{v}"',
                             f'image\'s source starts with "{v}"')
        if isinstance(tok, list):
            cond = v in [str(x).lower() for x in tok]
        else:
            cond = str(tok if tok is not None else "").lower() == v
        return self._res(cond, invert, f"image's {t} does not match", f"image's {t} match")


class _NoSettings:
    def get(self, key, default=None):
        return default


class PostFilter:
    """Several filters (AND), or an advanced expression with ( ) & |."""

    def __init__(self, text: str | list):
        if isinstance(text, list):
            text = " ".join(text)
        self.text = (text or "").strip()
        self.filters = []
        self.ast = None
        self.error = ""
        if not self.text:
            return
        if re.search(r"[()&|]", self.text):
            p = Parser(self.text)
            self.ast = p.parse_condition()
            self.error = p.error
        else:
            for part in self.text.split():
                f = build_filter(part)
                if f is not None:
                    self.filters.append(f)

    def __bool__(self):
        return bool(self.filters or self.ast is not None)

    def match(self, tokens: dict) -> list[str]:
        out = []
        if self.ast is not None:
            from .filename import CondEval
            if not CondEval(tokens, _NoSettings()).run(self.ast):
                out.append("post-filter")
        for f in self.filters:
            r = f.match(tokens)
            if r:
                out.append(r)
        return out


class Blacklist:
    """One rule per line; a line with several filters needs all of them."""

    def __init__(self, lines):
        if isinstance(lines, str):
            lines = lines.splitlines()
        self.rules = []
        for ln in lines or []:
            fs = [f for f in (build_filter(w) for w in str(ln).split()) if f is not None]
            if fs:
                self.rules.append(fs)

    def __bool__(self):
        return bool(self.rules)

    def match(self, tokens: dict, invert: bool = False) -> list[str]:
        detected = []
        for fs in self.rules:
            if all(f.match(tokens, invert) == "" for f in fs):
                detected.append(" ".join(f.to_string(False) for f in fs))
        return detected

    def add(self, line: str) -> None:
        fs = [f for f in (build_filter(w) for w in line.split()) if f is not None]
        if fs:
            self.rules.append(fs)

class TagFilterList:
    """Removed tags: plain names or * wildcards."""

    def __init__(self, items):
        self.plain = set()
        self.rx = []
        for it in items or []:
            it = str(it).strip()
            if not it:
                continue
            if "*" in it:
                self.rx.append(re.compile(fnmatch.translate(it), re.I))
            else:
                self.plain.add(it.lower())

    def matches(self, name: str) -> bool:
        n = name.lower()
        return n in self.plain or any(r.match(name) for r in self.rx)
