"""Grabber's file name language, in Python.

    %token%                       %md5%.%ext%     %artist%/%copyright%/%md5%.%ext%
    %token:opt=val,flag%          maxlength, unsafe, underscores, spaces, escape, htmlescape, raw,
                                  length, precision, separator, count, sort, case, includenamespace,
                                  excludenamespace, ignorenamespace, format, timezone, noext (for %num%)
    %gallery.name%                sub-objects
    <text %token% text>           shown only if every token / tag inside exists ("legacy" conditionals)
    <"tag"text>  <!"tag"text>     tag conditions (inverted with !); -"tag" / -%token% = test but don't print
    <cond?then:else>              cond: "tag", %token%, !, &, |, ( ); filters like rating:safe work too
    javascript:expression         the whole name computed in JavaScript (tokens are variables)
    ^ escapes, << and >> are literal < and >

Parsing and evaluation follow Grabber's FilenameParser / FilenameExecutionVisitor / FilenameConditionVisitor
step by step (its unit tests are ported in tests/test_filename.py). Path fixing is Packrat's own: every
folder and file name is made Windows-safe (no <>:"/\\|?*, reserved names, trailing dots), and the full path
is kept under the length limit by shortening names - never by dropping the id/md5 part's extension.
"""
from __future__ import annotations

import html
import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("packrat.grab")
ESC = "^"


# ================================================================================================
# Tokens
class TagList(list):
    """A list token with Grabber's per-category defaults (what to do with several values, if empty...)."""

    def __init__(self, values=(), what="", empty="", multiple=""):
        super().__init__(values)
        self.what, self.empty, self.multiple = what, empty, multiple


class Lazy:
    """A token computed only when used (md5_forced, grabber)."""

    def __init__(self, fn):
        self.fn, self._done, self._v = fn, False, None
        self._lock = threading.Lock()

    def get(self):
        with self._lock:
            if not self._done:
                try:
                    self._v = self.fn()
                except Exception as e:  # noqa: BLE001
                    log.debug("lazy token failed: %s", e)
                    self._v = ""
                self._done = True
            return self._v


def tval(v):
    return v.get() if isinstance(v, Lazy) else v


def is_empty(v) -> bool:
    v = tval(v)
    if v is None:
        return True
    if isinstance(v, (str, list, tuple, dict)):
        return len(v) == 0
    if isinstance(v, bool):
        return not v
    if isinstance(v, (int, float)):
        return v == 0
    return False


CATEGORY_DEFAULTS = {  # Grabber Image::generateTokens
    "general": ("keepAll", "", ""), "artist": ("keepAll", "anonymous", "multiple artists"),
    "contributor": ("keepAll", "none", "multiple"), "copyright": ("keepAll", "misc", "crossover"),
    "character": ("keepAll", "unknown", "group"), "model": ("keepAll", "unknown", "multiple"),
    "photo_set": ("keepAll", "unknown", "multiple"), "species": ("keepAll", "unknown", "multiple"),
    "meta": ("keepAll", "none", "multiple"), "lore": ("keepAll", "none", "multiple"),
}


def build_tokens(img, st, *, site=None, ignored=None, removed=None, extra: dict | None = None,
                 custom: dict | None = None) -> dict:
    """Tokens for an image (Grabber Image::generateTokens + Downloadable::tokens).
    st: settings with .get(key, default). ignored: tag names without type; removed: tag filters."""
    from .filters import TagFilterList
    tokens: dict = {}
    ignored = {t.lower() for t in (ignored if ignored is not None else _lines(st, "ignoredtags"))}
    removed_f = TagFilterList(removed if removed is not None else _lines(st, "removedtags"))
    search = list(img.search or [])
    m = re.search(r"pool:(\d+)", " ".join(search))
    tokens["pool"] = m.group(1) if m else ""
    tokens["filename"] = img.filename_from_url()
    tokens["website"] = img.site_key
    tokens["websitename"] = site.name if site is not None else img.site_key
    tokens["md5"] = img.md5 or ""
    tokens["md5_forced"] = Lazy(lambda: img.md5 or _md5_of_saved(img))
    tokens["id"] = img.id if isinstance(img.id, int) else (str(img.id) if img.id else 0)
    tokens["height"] = int(img.height or 0)
    tokens["width"] = int(img.width or 0)
    tokens["mpixels"] = int(img.width or 0) * int(img.height or 0)
    tokens["ratio"] = 1 if img.width == img.height else (img.width / img.height if img.height else 0.0)
    tokens["url_file"] = img.file_url or ""
    tokens["url_original"] = img.file_url or ""
    tokens["url_sample"] = img.sample_url or ""
    tokens["url_thumbnail"] = img.preview_url or ""
    tokens["url_page"] = img.page_url or ""
    tokens["source"] = img.sources[0] if img.sources else ""
    tokens["sources"] = list(img.sources or [])
    tokens["filesize"] = int(img.file_size or 0)
    tokens["name"] = img.name or ""
    tokens["position"] = img.position if img.position and img.position > 0 else ""
    for i in range(max(10, len(search))):
        tokens[f"search_{i + 1}"] = search[i] if i < len(search) else ""
    tokens["search"] = " ".join(search)
    allos = [t.name.replace(" ", "_") for t in img.tags]
    details: dict[str, list] = {}
    for t in img.tags:
        if removed_f.matches(t.name):
            continue
        typ = "general" if (t.name.lower() in ignored or not t.type) else t.type
        details.setdefault(typ, []).append(t.name)
        details.setdefault("alls", []).append(t.name)
        details.setdefault("alls_namespaces", []).append(t.type or "general")
    if _bool(st.get("Save/copyright_useshorter", True)):
        cops: list[str] = []
        for cop in details.get("copyright", []):
            found = False
            for i, c in enumerate(cops):
                if c[:len(cop)] == cop[:len(c)]:
                    if len(cop) < len(c):
                        cops[i] = cop
                    found = True
            if not found:
                cops.append(cop)
        details["copyright"] = cops
    for cat, (what, empty, multi) in CATEGORY_DEFAULTS.items():
        vals = details.get(cat, [])
        if cat == "model":
            vals = vals + details.get("idol", [])
        tokens[cat] = TagList(vals, what, empty, multi)
    for typ, vals in details.items():  # other types (circle, invalid ...) as plain lists
        if typ not in tokens and typ not in ("alls", "alls_namespaces"):
            tokens[typ] = TagList(vals)
    tokens["allos"] = allos
    tokens["allo"] = " ".join(allos)
    tokens["tags"] = list(img.tags)
    tokens["all"] = details.get("alls", [])
    tokens["all_namespaces"] = details.get("alls_namespaces", [])
    ext = (img.ext or "").lower()
    if _bool(st.get("Save/noJpeg", True)) and ext == "jpeg":
        ext = "jpg"
    tokens["ext"] = ext or "jpg"
    tokens["filetype"] = ext or "jpg"
    if img.parent_gallery is not None:
        g = img.parent_gallery
        tokens["gallery"] = Lazy(lambda: build_tokens(g, st, site=site, ignored=ignored, removed=removed))
    for k, v in (img.data or {}).items():
        if k in ("endpoint_images", "pools") and not isinstance(v, (str, int)):
            continue
        tokens[k] = v
    tokens.setdefault("rating", "unknown")
    if tokens.get("rating") in ("", None):
        tokens["rating"] = "unknown"
    # custom tokens
    customs = custom if custom is not None else customs_from_settings(st)
    for name, wanted in customs.items():
        low = {w.lower() for w in wanted}
        tokens[name] = [t.name for t in img.tags if t.name.lower() in low]
    tokens["grabber_branch"] = "release"
    from .. import __version__
    tokens["grabber_version"] = __version__
    if extra:
        tokens.update(extra)
    return tokens


def customs_from_settings(st) -> dict[str, list[str]]:
    out = {}
    for c in st.get("Customs") or []:
        if isinstance(c, dict) and c.get("name"):
            out[str(c["name"])] = [w for w in re.split(r"[ \n]+", str(c.get("tags") or "")) if w]
    return out


def conditionals_from_settings(st) -> list[dict]:
    return [c for c in (st.get("Filenames") or []) if isinstance(c, dict)]


def _lines(st, key):
    v = st.get(key) or ""
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    out = []
    for ln in str(v).splitlines():
        out += [w for w in ln.split() if w]
    return out


def _bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _md5_of_saved(img) -> str:
    p = getattr(img, "saved_path", "") or ""
    if not p or not Path(p).is_file():
        return ""
    import hashlib
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ================================================================================================
# AST
@dataclass
class Text:
    text: str


@dataclass
class Var:
    name: str
    opts: dict


@dataclass
class JS:
    script: str


@dataclass
class Root:
    exprs: list


@dataclass
class Conditional:
    cond: object
    if_true: object = None
    if_false: object = None


@dataclass
class CTag:
    tag: str
    filter: object = None


@dataclass
class CToken:
    name: str
    opts: dict


@dataclass
class CInvert:
    node: object


@dataclass
class CIgnore:
    node: object


@dataclass
class COp:
    op: str          # "and" | "or"
    left: object
    right: object


@dataclass
class CJS:
    script: str


class ParseError(ValueError):
    pass


class Parser:
    def __init__(self, s: str):
        self.s = s
        self.i = 0
        self.depth = 0
        self.error = ""

    def parse_root(self):
        try:
            exprs = []
            while not self._finished():
                exprs.append(self._expr())
            return Root(exprs)
        except ParseError as e:
            self.error = str(e)
            return None

    def parse_condition(self):
        try:
            return self._condition_node()
        except ParseError as e:
            self.error = str(e)
            return None

    # -- helpers
    def _peek(self):
        return self.s[self.i] if self.i < len(self.s) else ""

    def _finished(self):
        return self.i >= len(self.s)

    def _skip_spaces(self):
        while not self._finished() and self._peek().isspace():
            self.i += 1

    def _is_escape(self, idx):
        c = self.s[idx]
        return c == ESC or (c in "<>" and idx < len(self.s) - 1 and self.s[idx + 1] == c)

    def _index_of(self, chars, maximum=-1):
        esc = False
        limit = len(self.s) if maximum < 0 else min(maximum, len(self.s))
        for idx in range(self.i, limit):
            c = self.s[idx]
            is_esc = self._is_escape(idx)
            if is_esc and not esc:
                esc = True
            elif c in chars and not esc:
                return idx
            if not is_esc and esc:
                esc = False
        return -1

    def _read_until(self, chars, allow_end=False):
        out = []
        esc = False
        while not self._finished():
            c = self.s[self.i]
            is_esc = self._is_escape(self.i)
            if is_esc and not esc:
                esc = True
            else:
                if c in chars and not esc:
                    return "".join(out)
                out.append(c)
            if not is_esc and esc:
                esc = False
            self.i += 1
        if not allow_end:
            raise ParseError("Unexpected EOF")
        return "".join(out)

    # -- grammar
    def _exprs(self, chars):
        exprs = []
        while not self._finished() and self._peek() not in chars:
            exprs.append(self._expr(chars))
        return exprs[0] if len(exprs) == 1 else Root(exprs)

    def _expr(self, add_chars=()):
        if self.s[self.i:self.i + 11] == "javascript:":
            self.i += 11
            start, self.i = self.i, len(self.s)
            return JS(self.s[start:])
        until = ["<", "%", *add_chars]
        txt = self._read_until(until, True)
        if txt == "":
            p = self._peek()
            if p == "<" and (self.i >= len(self.s) - 1 or self.s[self.i + 1] != "<"):
                return self._conditional()
            if p == "%":
                return self._variable()
        return Text(txt)

    def _variable(self) -> Var:
        self.i += 1  # %
        name = self._read_until([":", "%"])
        opts: dict = {}
        while self._peek() != "%":
            self.i += 1  # : or ,
            opt = self._read_until(["=", ",", "%"])
            val = ""
            if self._peek() == "=":
                self.i += 1
                val = self._read_until([",", "%"])
            opts.pop(opt, None)
            opts[opt] = val
        self.i += 1  # %
        return Var(name, opts)

    def _conditional(self) -> Conditional:
        self.i += 1  # <
        end = self._index_of([">"])
        sep = self._index_of(["?"], end)
        if sep < 0:
            exprs, conds = [], []
            while self._peek() != ">":
                if self._finished():
                    raise ParseError("Unexpected EOF")
                stop = [">", "-", "!", '"', "%"]
                stop_cond = ['"', "%"]
                not_stop = self._peek() not in stop
                if not_stop:
                    exprs.append(self._expr(stop))
                if (self._peek() in ("-", "!") and self.i + 1 < len(self.s)
                        and (self.s[self.i + 1] not in stop_cond or self.s[self.i + 1] == ">")):
                    xpr = self._expr([">", '"', "%"])
                    if not_stop and isinstance(xpr, Text) and exprs and isinstance(exprs[-1], Text):
                        exprs[-1].text += xpr.text
                    else:
                        exprs.append(xpr)
                if self._peek() != ">":
                    if self._finished():
                        raise ParseError("Unexpected EOF")
                    c = self._single_condition(True)
                    conds.append(c)
                    exprs.append(c)
            if not conds:
                raise ParseError("No condition found in conditional")
            cond = conds.pop(0)
            while conds:
                cond = COp("and", cond, conds.pop(0))
            if_true = exprs[0] if len(exprs) == 1 else Root(exprs)
            if_false = None
        else:
            cond = self._condition_node()
            if self._finished() or self._peek() != "?":
                raise ParseError("Expected '?' after condition")
            self.i += 1
            if_true = self._exprs([":", ">"])
            if_false = None
            if self._peek() == ":":
                self.i += 1
                if_false = self._exprs([">"])
            if self._finished() or self._peek() != ">":
                raise ParseError("Expected '>' at the end of conditional")
        self.i += 1  # >
        return Conditional(cond, if_true, if_false)

    def _condition_node(self):
        if self.s[self.i:self.i + 11] == "javascript:":
            self.i += 11
            start, self.i = self.i, len(self.s)
            return CJS(self.s[start:])
        self._skip_spaces()
        ops: list[str] = []
        terms: list = [self._single_condition()]
        while not self._finished():
            self._skip_spaces()
            p = self._peek()
            c = "&" if p in ('"', "%") else p
            if c not in ("&", "|"):
                break
            prec = 2 if c == "&" else 1
            while ops:
                if prec > (2 if ops[-1] == "&" else 1):
                    break
                b, a = terms.pop(), terms.pop()
                terms.append(COp("and" if ops.pop() == "&" else "or", a, b))
            ops.append(c)
            if p == c:
                self.i += 1
                self._skip_spaces()
            terms.append(self._single_condition())
        while ops:
            b, a = terms.pop(), terms.pop()
            terms.append(COp("and" if ops.pop() == "&" else "or", a, b))
        self._skip_spaces()
        return terms.pop() if terms else None

    def _single_condition(self, legacy=False):
        c = self._peek()
        if c == "(":
            self.i += 1
            self.depth += 1
            r = self._condition_node()
            if self._finished() or self._peek() != ")":
                raise ParseError("Expected ')' after condition in parenthesis")
            self.i += 1
            self.depth -= 1
            return r
        if legacy and c == "-":
            self.i += 1
            return CIgnore(self._single_condition(True))
        if c in ("!", "-"):
            self.i += 1
            return CInvert(self._single_condition())
        if c == "%":
            v = self._variable()
            return CToken(v.name, v.opts)
        if c == '"':
            return self._condition_tag(True)
        if not legacy:
            return self._condition_tag(False)
        raise ParseError("Expected '!', '%' or '\"' for condition")

    def _condition_tag(self, quotes: bool) -> CTag:
        if quotes:
            self.i += 1
            tag = self._read_until(['"'])
            self.i += 1
        else:
            stops = [" ", "&", "|", "?"] + ([")"] if self.depth else [])
            tag = self._read_until(stops, True)
        from .filters import build_filter
        return CTag(tag, build_filter(tag))


# ================================================================================================
# Evaluation
_FORBIDDEN = ("\\", "%", "/", ":", "|", "*", "?", '"', "<", ">")


def clean_variable(val: str, opts: dict, st) -> str:
    if "unsafe" not in opts and "raw" not in opts:
        for ch in _FORBIDDEN:
            val = val.replace(ch, "_")
        for _ in range(3):
            val = val.replace("__", "_")
        val = val.strip()
    if "raw" not in opts and "underscores" not in opts and (not _bool(st.get("Save/replaceblanks", False))
                                                           or "spaces" in opts):
        val = val.replace("_", " ")
    return val


def _js_clean(val: str, name: str, st) -> str:
    if name == "allo":
        return val
    for ch in _FORBIDDEN:
        val = val.replace(ch, "_")
    for _ in range(3):
        val = val.replace("__", "_")
    val = val.strip()
    if not _bool(st.get("Save/replaceblanks", False)):
        val = val.replace("_", " ")
    return val


def separator_for(name: str, st) -> str:
    main = st.get("Save/separator", " ")
    if main is None:
        main = " "
    sep = st.get(f"Save/{name}_sep") or main
    return str(sep).replace("\\n", "\n").replace("\\r", "\r")


def js_variables(tokens: dict, st) -> dict:
    out: dict = {}
    for name, v in tokens.items():
        v = tval(v)
        if isinstance(v, (list, tuple)) and not (v and not isinstance(v[0], str)):
            vals = [str(x) for x in v]
            if name not in ("all", "tags"):
                out[name + "s"] = vals
            out[name] = _js_clean(separator_for(name, st).join(vals), name, st)
        elif isinstance(v, str):
            out[name] = _js_clean(v, name, st)
        elif isinstance(v, dict):
            out[name] = js_variables(v, st)
        elif isinstance(v, datetime):
            out[name] = {"__date__": v.isoformat()}
        elif isinstance(v, (int, float, bool)) or v is None:
            out[name] = v
        elif isinstance(v, list):
            out[name] = [getattr(x, "name", str(x)) for x in v]
    return out


def eval_js(script: str, tokens: dict, st):
    from .jsrt import eval_expression
    variables = js_variables(tokens, st)
    dates = {k: v["__date__"] for k, v in variables.items() if isinstance(v, dict) and "__date__" in v}
    for k in dates:
        variables.pop(k)
    prefix = "".join(f"var {k} = new Date({_q(v)});\n" for k, v in dates.items())
    return eval_expression(prefix + script if prefix else script, variables)


def _q(s: str) -> str:
    import json
    return json.dumps(s)


class Executor:
    def __init__(self, tokens: dict, st, keep_invalid: bool = False, escape=None):
        self.tokens, self.st, self.keep_invalid, self.escape = tokens, st, keep_invalid, escape

    def run(self, node) -> str:
        out: list[str] = []
        self._visit(node, out)
        return "".join(out)

    def _visit(self, n, out):
        if isinstance(n, Root):
            for e in n.exprs:
                self._visit(e, out)
        elif isinstance(n, Text):
            out.append(n.text)
        elif isinstance(n, Var):
            out.append(self.variable(n.name, n.opts))
        elif isinstance(n, Conditional):
            ok = CondEval(self.tokens, self.st).run(n.cond)
            if ok and n.if_true is not None:
                self._visit(n.if_true, out)
            elif not ok and n.if_false is not None:
                self._visit(n.if_false, out)
        elif isinstance(n, CIgnore):
            pass
        elif isinstance(n, CTag):
            out.append(clean_variable(n.tag, {}, self.st))
        elif isinstance(n, CToken):
            out.append(self.variable(n.name, n.opts))
        elif isinstance(n, CInvert):
            self._visit(n.node, out)   # Grabber's base visitor: an inverted tag / token prints its text
        elif isinstance(n, COp):
            self._visit(n.left, out)
            self._visit(n.right, out)
        elif isinstance(n, JS):
            try:
                res = eval_js(n.script, self.tokens, self.st)
            except ValueError as e:
                log.warning("Error in JavaScript evaluation: %s", e)
                return
            out.append("" if res is None else (str(res).lower() if isinstance(res, bool) else _num_str(res)))

    def _lookup(self, full_name: str):
        parts = full_name.split(".")
        ctx = self.tokens
        name = parts.pop(0)
        while parts:
            if name in ctx and isinstance(tval(ctx[name]), dict):
                ctx = tval(ctx[name])
                name = parts.pop(0)
                continue
            return None, False
        if name not in ctx:
            return None, False
        return tval(ctx[name]), True

    def variable(self, full_name: str, opts: dict) -> str:
        val, found = self._lookup(full_name)
        name = full_name.split(".")[-1]
        if not found:
            if self.keep_invalid or name in ("path", "num"):
                s = "".join(k + (("=" + v) if v else "") for k, v in opts.items())
                return f"%{full_name}" + (f":{s}" if s else "") + "%"
            return ""
        clean = False
        if isinstance(val, datetime):
            res = date_to_string(val, opts)
        elif isinstance(val, bool):
            res = "true" if val else "false"
        elif isinstance(val, int):
            res = int_to_string(val, opts)
        elif isinstance(val, float):
            res = float_to_string(val, opts)
        elif isinstance(val, (list, tuple)):
            vals = [getattr(x, "name", x) if not isinstance(x, str) else x for x in val]
            res = self.list_to_string(name, [str(x) for x in vals], opts)
            clean = True
        elif name == "score":
            res = score_to_string(str(val), opts)
        elif isinstance(val, dict):
            res = ""
        else:
            res = "" if val is None else str(val)
        if "maxlength" in opts:
            try:
                res = res[:int(opts["maxlength"])]
            except ValueError:
                pass
        if "htmlescape" in opts:
            res = html.escape(res, quote=True)
        if (name != "allo" and not name.startswith("url_") and name not in
                ("filename", "directory", "old_filename", "old_directory") and not clean):
            res = clean_variable(res, opts, self.st)
        if self.escape is not None and "escape" in opts:
            res = self.escape(res)
        return res

    def list_to_string(self, name: str, val: list[str], opts: dict) -> str:
        if "count" in opts:
            return int_to_string(len(val), opts)
        ignore_ns = "ignorenamespace" in opts
        include_ns = "includenamespace" in opts
        if ignore_ns or include_ns:
            namespaces = list(tval(self.tokens.get("all_namespaces")) or [])
            if ignore_ns:
                ignored = opts["ignorenamespace"].split(" ")
                fv, fns = [], []
                for i, v in enumerate(val):
                    ns = namespaces[i] if name == "all" and i < len(namespaces) else name
                    if ns not in ignored:
                        fv.append(v)
                        fns.append(namespaces[i] if i < len(namespaces) else name)
                val, namespaces = fv, fns
            if include_ns:
                excluded = opts["excludenamespace"].split(" ") if "excludenamespace" in opts else []
                out = []
                for i, v in enumerate(val):
                    ns = namespaces[i] if name == "all" and i < len(namespaces) else name
                    out.append((ns + ":" if ns not in excluded else "") + v)
                val = out
        if "sort" in opts:
            val = sorted(val)
        case = opts.get("case")
        if case in ("lower", "upper_first", "upper", "caps"):
            val = [format_case(t, case) for t in val]
        if not name.startswith("source"):
            val = [clean_variable(t, opts, self.st) for t in val]
        sep = opts["separator"] if "separator" in opts else separator_for(name, self.st)
        sep = sep.replace("\\n", "\n").replace("\\r", "\r")
        return sep.join(val)


def format_case(tag: str, case: str) -> str:
    words = tag.split("_")
    if case == "lower":
        words = [w.lower() for w in words]
    elif case == "upper_first":
        words = [w.lower() for w in words]
        if words and words[0]:
            words[0] = words[0][0].upper() + words[0][1:]
    elif case == "upper":
        words = [(w[0].upper() + w[1:].lower()) if w else w for w in words]
    elif case == "caps":
        words = [w.upper() for w in words]
    return "_".join(words)


def int_to_string(v: int, opts: dict) -> str:
    if "length" in opts:
        try:
            n = int(opts["length"])
        except ValueError:
            n = 0
        s = str(abs(v)).rjust(max(0, n - (1 if v < 0 else 0)), "0")
        return ("-" if v < 0 else "") + s
    return str(v)


def float_to_string(v: float, opts: dict) -> str:
    try:
        prec = int(opts.get("precision", "6"))
    except ValueError:
        prec = 6
    s = f"{v:.{prec}f}"
    if "length" in opts:
        try:
            s = s.rjust(int(opts["length"]), "0")
        except ValueError:
            pass
    return s


def score_to_string(v: str, opts: dict) -> str:
    if "length" in opts:
        dot = v.find(".")
        try:
            n = int(opts["length"]) + (len(v) - dot if dot >= 0 else 0)
        except ValueError:
            return v
        return v.rjust(n, "0")
    return v


def _num_str(v) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


# ---- Qt date formats ---------------------------------------------------------------------------
_QT_TOKENS = ("dddd", "ddd", "dd", "d", "MMMM", "MMM", "MM", "M", "yyyy", "yy", "HH", "H", "hh", "h", "mm", "m",
              "ss", "s", "zzz", "z", "AP", "ap", "A", "a", "t")


def qt_date_format(dt: datetime, fmt: str) -> str:
    out = []
    i = 0
    ampm = any(x in fmt for x in ("AP", "ap", "A", "a"))
    while i < len(fmt):
        if fmt[i] == "'":
            j = i + 1
            buf = []
            while j < len(fmt):
                if fmt[j] == "'":
                    if j + 1 < len(fmt) and fmt[j + 1] == "'":
                        buf.append("'")
                        j += 2
                        continue
                    break
                buf.append(fmt[j])
                j += 1
            if j == i + 1 and j < len(fmt) and fmt[j] == "'":
                out.append("'")
            else:
                out.append("".join(buf))
            i = j + 1
            continue
        for tok in _QT_TOKENS:
            if fmt.startswith(tok, i):
                out.append(_qt_tok(dt, tok, ampm))
                i += len(tok)
                break
        else:
            out.append(fmt[i])
            i += 1
    return "".join(out)


def _qt_tok(dt: datetime, tok: str, ampm: bool) -> str:
    h12 = dt.hour % 12 or 12
    return {
        "d": str(dt.day), "dd": f"{dt.day:02d}", "ddd": dt.strftime("%a"), "dddd": dt.strftime("%A"),
        "M": str(dt.month), "MM": f"{dt.month:02d}", "MMM": dt.strftime("%b"), "MMMM": dt.strftime("%B"),
        "yy": f"{dt.year % 100:02d}", "yyyy": f"{dt.year:04d}",
        "H": str(dt.hour), "HH": f"{dt.hour:02d}",
        "h": str(h12 if ampm else dt.hour), "hh": f"{(h12 if ampm else dt.hour):02d}",
        "m": str(dt.minute), "mm": f"{dt.minute:02d}", "s": str(dt.second), "ss": f"{dt.second:02d}",
        "z": str(dt.microsecond // 1000), "zzz": f"{dt.microsecond // 1000:03d}",
        "AP": "AM" if dt.hour < 12 else "PM", "A": "AM" if dt.hour < 12 else "PM",
        "ap": "am" if dt.hour < 12 else "pm", "a": "am" if dt.hour < 12 else "pm",
        "t": dt.strftime("%Z") or "UTC",
    }[tok]


def date_to_string(dt: datetime, opts: dict) -> str:
    tzname = opts.get("timezone", "")
    if tzname and tzname != "server":
        if tzname == "local":
            dt = dt.astimezone()
        else:
            tz = _parse_tz(tzname)
            if tz is not None:
                dt = dt.astimezone(tz)
            else:
                log.error("Unknown timeZone '%s'", tzname)
    return qt_date_format(dt, opts.get("format", "MM-dd-yyyy HH.mm"))


def _parse_tz(name: str):
    m = re.fullmatch(r"(?:UTC|GMT)?([+-])(\d{1,2}):?(\d{2})?", name.strip())
    if name.strip().upper() in ("UTC", "GMT", "Z"):
        return timezone.utc
    if m:
        sign = 1 if m.group(1) == "+" else -1
        return timezone(sign * timedelta(hours=int(m.group(2)), minutes=int(m.group(3) or 0)))
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - unknown zone / no tzdata
        return None


# ---- conditions --------------------------------------------------------------------------------
class CondEval:
    def __init__(self, tokens: dict, st):
        self.tokens, self.st = tokens, st
        self.tags = [str(t) for t in (tval(tokens.get("allos")) or [])]

    def run(self, node) -> bool:
        if node is None:
            return True
        if isinstance(node, CInvert):
            return not self.run(node.node)
        if isinstance(node, CIgnore):
            return self.run(node.node)
        if isinstance(node, COp):
            left = self.run(node.left)
            if (not left and node.op == "and") or (left and node.op == "or"):
                return left
            return self.run(node.right)
        if isinstance(node, CTag):
            if node.tag in self.tags:
                return True
            return node.filter is not None and node.filter.match(self.tokens) == ""
        if isinstance(node, CToken):
            parts = node.name.split(".")
            ctx = self.tokens
            for p in parts[:-1]:
                v = tval(ctx.get(p)) if isinstance(ctx, dict) else None
                if not isinstance(v, dict):
                    return False
                ctx = v
            return parts[-1] in ctx and not is_empty(ctx[parts[-1]])
        if isinstance(node, CJS):
            try:
                return bool(eval_js(node.script, self.tokens, self.st))
            except ValueError as e:
                log.warning("Error in JavaScript evaluation: %s", e)
                return True
        return True


# ================================================================================================
# Filename
_cache: dict[str, tuple] = {}
_cache_lock = threading.Lock()


def parse_cached(fmt: str):
    with _cache_lock:
        if fmt in _cache:
            return _cache[fmt]
    p = Parser(fmt)
    ast = p.parse_root()
    names = set()
    if ast is not None:
        _collect(ast, names)
    entry = (ast, p.error, names)
    with _cache_lock:
        _cache[fmt] = entry
    return entry


def _collect(n, names: set):
    if isinstance(n, Root):
        for e in n.exprs:
            _collect(e, names)
    elif isinstance(n, Var):
        names.add(n.name.split(".")[0])
    elif isinstance(n, CToken):
        names.add(n.name.split(".")[0])
    elif isinstance(n, Conditional):
        _collect(n.cond, names)
        _collect(n.if_true, names)
        _collect(n.if_false, names)
    elif isinstance(n, (CInvert, CIgnore)):
        _collect(n.node, names)
    elif isinstance(n, COp):
        _collect(n.left, names)
        _collect(n.right, names)


KNOWN_TOKENS = ("tags", "artist", "contributor", "general", "copyright", "character", "model", "photo_set",
                "species", "lore", "meta", "filename", "rating", "md5", "md5_forced", "website", "websitename",
                "ext", "filetype", "all", "id", "search", r"search_(\d+)", "allo", "allos", "date", "score",
                "count", "width", "height", "pool", "url_file", "url_original", "url_sample", "url_thumbnail",
                "url_page", "num", "name", "position", "current_date", "author", "authorid", "parentid", "grabber",
                "grabber_branch", "grabber_version", "source", "sources", "filesize", "mpixels", "ratio", "status",
                "has_children", "has_note", "has_comments", "fav_count", "path", "old_filename", "old_directory",
                "directory", "gallery", "circle", "invalid", "description", "pools", "date_raw")

FLAG_CONDITIONALS, FLAG_FIX, FLAG_CAP, FLAG_FOLDER, FLAG_KEEP_INVALID = 1, 8, 4, 16, 32
DEFAULT_FLAGS = FLAG_CONDITIONALS | FLAG_FIX | FLAG_CAP


@dataclass
class ConditionalFilename:
    condition: str
    filename: str = ""
    folder: str = ""
    ast: object = field(default=None, repr=False)

    def __post_init__(self):
        if self.condition:
            p = Parser(self.condition)
            self.ast = p.parse_condition()
            if p.error:
                log.error("Error parsing condition '%s': %s", self.condition, p.error)
                self.ast = None

    def matches(self, tokens: dict, st) -> bool:
        return self.ast is not None and CondEval(tokens, st).run(self.ast)


class Filename:
    def __init__(self, fmt: str):
        self.format = fmt or ""
        self.ast, self.error, self.used = parse_cached(self.format)
        self.escape = None

    @property
    def is_js(self) -> bool:
        return self.format.startswith("javascript:")

    def uses(self, name: str) -> bool:
        return name in self.used if not self.is_js else name in self.format

    # ---- expansion (several file names for "one file per tag") --------------------------------
    def expand_tokens(self, tokens: dict, st) -> list[dict]:
        out = [dict(tokens)]
        for key in sorted(tokens):   # Grabber walks a QMap: keys in sorted order
            tok = tokens[key]
            if not isinstance(tok, list) or (tok and not isinstance(tok[0], str)):
                continue
            if not self.uses(key):
                continue
            reps = get_replace(key, tok, st)
            n = len(out)
            for i in range(n):
                out[i][key] = reps[0]
                for r in reps[1:]:
                    d = dict(out[i])
                    d[key] = r
                    out.append(d)
        return out

    def path(self, tokens: dict, st, folder: str = "", counter: int = 0, flags: int = DEFAULT_FLAGS,
             conditionals: list | None = None) -> list[str]:
        if self.ast is None:
            return []
        tokens = dict(tokens)
        tokens["count"] = counter
        tokens["current_date"] = datetime.now().astimezone()
        if flags & FLAG_CONDITIONALS:
            conds = conditionals if conditionals is not None else [
                ConditionalFilename(c.get("cond", ""), c.get("fn", ""), c.get("dir", ""))
                for c in conditionals_from_settings(st)]
            override = None
            for c in conds:
                if c.matches(tokens, st):
                    if c.folder:
                        folder = c.folder
                    if c.filename:
                        override = c.filename
            if override:
                return Filename(override).path(tokens, st, folder, counter, flags & ~FLAG_CONDITIONALS, [])
        if not self.format:
            return []
        names = []
        for rep in self.expand_tokens(tokens, st):
            ex = Executor(rep, st, bool(flags & FLAG_KEEP_INVALID), self.escape)
            name = ex.run(self.ast)
            if not name:
                continue
            m = re.search(r"%num(?::[^%]+)?%", name)
            if m:
                name = name[:m.start()] + self._num(name, m, folder, rep, ex) + name[m.end():]
            names.append(name)
        out = []
        for name in names:
            if flags & FLAG_FIX:
                name = re.sub(r" */ *", "/", name.strip())
                name = fix_path(name, folder, int(st.get("Save/limit") or 0) if flags & FLAG_CAP else 0)
            if flags & FLAG_FOLDER:
                name = folder.rstrip("/\\") + "/" + name.lstrip("/") if folder else name
            if flags & FLAG_FIX:
                name = re.sub(r"(.)/{2,}", r"\1/", name.replace("\\", "/"))
            out.append(name)
        return out

    def _num(self, name, m, folder, rep, ex) -> str:
        p = Parser(m.group(0))
        var = p._variable()
        no_ext = "noext" in var.opts
        full = (folder.rstrip("/\\") + "/" if folder else "") + name
        d, _, right = full.replace("\\", "/").rpartition("/")
        pattern = right.replace(m.group(0), "*")
        ext = str(tval(rep.get("ext")) or "")
        if no_ext and pattern.endswith("." + ext):
            pattern = pattern[:-len(ext)] + "*"
        num = 1
        base = Path(d) if d else Path(".")
        if base.is_dir():
            import fnmatch
            files = [f for f in base.iterdir() if f.is_file() and not f.name.endswith((".tmp", ".part"))
                     and fnmatch.fnmatchcase(f.name, pattern)]
            if files:
                key = (lambda f: _natural(f.stem)) if no_ext else (lambda f: _natural(f.name))
                last = max(files, key=key).name
                pos = right.find(m.group(0))
                ln = len(last) - len(right) + len(m.group(0))
                try:
                    num = int(last[pos:pos + ln]) + 1
                except ValueError:
                    num = len(files) + 1
        return int_to_string(num, var.opts)

    # ---- validation --------------------------------------------------------------------------
    def check(self, custom_names=(), additional=()) -> tuple[str, str]:
        """(level, message): level is error | warning | info | ok (Grabber's isValid)."""
        if not self.format:
            return "error", "The file name must not be empty."
        if self.is_js:
            return "warning", "JavaScript names can't be checked in advance."
        if self.error:
            return "error", f"Can't read your file name: {self.error}"
        if not self.format.endswith(".%ext%"):
            return "warning", "Your file name does not end with .%ext%: you may not be able to open saved files."
        if not ({"md5", "id", "num"} & self.used):
            return "warning", ("Your file name is not unique: one image may overwrite another. Use %md5%, which is "
                               "unique for every image.")
        known = list(KNOWN_TOKENS) + list(custom_names) + list(additional)
        for mt in re.finditer(r"%(.+?)%", self.format):
            tok = mt.group(0)
            if not any(re.fullmatch(r"%(?:gallery\.)?" + k + r"(?::[^%]+)?%", tok) for k in known):
                return "warning", f"The %{mt.group(1)}% token does not exist and will not be replaced."
        if "num" in self.used:
            return "warning", "%num% does not play well with simultaneous downloads (set them to 1)."
        if not ({"md5", "website", "websitename", "count"} & self.used) and "id" in self.used:
            return "info", ("%id% is only unique per site: the same id can be another image on another site. Add "
                            "%website% (or use %md5%) if you download from several sites into one folder.")
        return "ok", "Valid file name!"

    def is_valid(self) -> bool:
        level, _ = self.check()
        return level in ("ok", "info") or (level == "warning" and self.is_js)

    def need_temporary_file(self, tokens: dict) -> bool:
        if self.is_js:
            return False
        u = self.used

        def bad(k, num=False):
            v = tval(tokens.get(k))
            return (k not in tokens) or (int(v or 0) <= 0 if num else not v)
        return (("md5" in u and bad("md5")) or ("filesize" in u and bad("filesize", True))
                or ("width" in u and bad("width", True)) or ("height" in u and bad("height", True)))

    def need_exact_tags(self, forced_tokens=(), custom_names=()) -> int:
        """0 = list data is enough, 1 = tag types needed, 2 = details page needed (Grabber needExactTags)."""
        if self.is_js:
            return 2
        if any(t in self.used for t in forced_tokens):
            return 2
        typed = {"artist", "copyright", "character", "model", "species", "meta", "lore", "contributor",
                 "photo_set", "general", "circle"}
        if typed & self.used or set(custom_names) & self.used:
            return 1
        if re.search(r"%all:[^%]*namespace", self.format):
            return 1
        return 0


def get_replace(key: str, value: list, st) -> list:
    """Grabber Filename::getReplace: what a list token becomes (keepAll / keepN / keepNThenAdd /
    replaceAll / multiple)."""
    what_default = getattr(value, "what", "")
    if not what_default:
        return [list(value)]
    vals = list(value)
    g = lambda k, d=None: st.get(f"Save/{key}_{k}", d)  # noqa: E731
    if (g("sort", "original") or "original") == "name":
        vals.sort()
    empty_default = getattr(value, "empty", "")
    multiple_default = getattr(value, "multiple", "")
    if not vals:
        e = g("empty")
        return [empty_default if e is None else str(e)]
    try:
        limit = int(g("multiple_limit", 1) or 1)
    except (TypeError, ValueError):
        limit = 1
    if len(vals) > limit:
        what = g("multiple") or what_default
        if what == "keepAll":
            return [vals]
        if what == "multiple":
            return [v for v in vals]
        if what == "keepN":
            n = max(1, int(g("multiple_keepN", 1) or 1))
            return [vals[:n]]
        if what == "keepNThenAdd":
            n = int(g("multiple_keepNThenAdd_keep", 1) or 1)
            add = str(g("multiple_keepNThenAdd_add", " (+ %count%)") or "")
            add = add.replace("%total%", str(len(vals))).replace("%count%", str(len(vals) - n))
            kept = vals[:max(1, n)]
            if len(vals) > n:
                return [separator_for(key, st).join(kept) + add]
            return [kept]
        v = g("value")
        return [multiple_default if v is None else str(v)]
    return [vals]


def _natural(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


# ---- path fixing --------------------------------------------------------------------------------
def fix_path(name: str, folder: str = "", max_len: int = 0) -> str:
    """Make every component Windows-safe and keep the full path under the limit (default 240 with the
    folder; a name-length limit from 'Max length' applies to the file name)."""
    from ..naming import _u16, sanitize_component, truncate
    parts = [p for p in name.replace("\\", "/").split("/")]
    file = parts.pop() if parts else ""
    dirs = [sanitize_component(p, 120, "_") for p in parts if p.strip() != ""]
    stem, dot, ext = file.rpartition(".")
    if not dot:
        stem, ext = file, ""
    ext = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", ext)[:12]
    stem = sanitize_component(stem, 10_000, "_") if stem else ""
    limit_name = max_len if max_len > 0 else 200
    room = limit_name - (len(ext) + 1 if ext else 0)
    if _u16(stem) > room:
        stem = truncate(stem, max(1, room)).rstrip(" .")
    total_max = 240
    base_len = _u16(folder.rstrip("/\\")) + 1 if folder else 0

    def build():
        return "/".join(dirs + [stem + ("." + ext if ext else "")])
    rel = build()
    guard = 0
    while base_len + _u16(rel) > total_max and guard < 40:
        guard += 1
        longest = max(range(len(dirs) + 1), key=lambda i: _u16(dirs[i]) if i < len(dirs) else _u16(stem))
        if longest < len(dirs) and _u16(dirs[longest]) > 8:
            dirs[longest] = truncate(dirs[longest], max(8, _u16(dirs[longest]) - 10)).rstrip(" .") or "_"
        elif _u16(stem) > 16:
            stem = truncate(stem, max(16, _u16(stem) - 10)).rstrip(" .") or "_"
        else:
            break
        rel = build()
    return rel
