"""Run Grabber source models (compiled JavaScript) inside Packrat.

Grabber describes every supported site with a small TypeScript "model" (src/sites/<Source>/model.ts):
functions that build request URLs and parse the site's answers (JSON, XML, RSS or HTML). Packrat ships
those models compiled to JavaScript (tools/build_sources.py) and runs them with QuickJS through the
`dukpy` package, so every Grabber source - and any source from a Grabber source registry - works with
Grabber's own parsing code.

The C++ side of Grabber gives the models a `Grabber` object (regexMatches, parseXML, parseHTML,
htmlDecode) and `console`; Grabber's helper.js adds the rest (regexToImages, mapFields, pageUrl ...).
This module provides the same functions from Python:

  regexMatches(regex, txt)  Qt/PCRE regex with named groups; names ending in _<n> collapse (name_2 -> name),
                            group numbers are set too; only non-empty named groups are set (as in Grabber)
  parseXML(txt)             the Qt DOM shape: {"@attributes": {...}, "#text": ..., child: obj | [obj, ...]}
  parseHTML(html, frag)     a document object: find(css), outerHTML(), innerHTML(), innerText(), tag(),
                            attr(name), path(), pathIds(), parent()  (BeautifulSoup + soupsieve)
  htmlDecode(txt)

One QuickJS context per source type (models declare top-level constants, so they cannot share one); a
lock makes each context single-threaded. Errors in a model never escape as Python exceptions from
call(): they come back as {"error": ...} like Grabber's "Uncaught exception at line ..." messages.
"""
from __future__ import annotations

import html as _html
import json
import logging
import re
import threading
from pathlib import Path

log = logging.getLogger("packrat.grab")

_NAMED = re.compile(r"\(\?<(?![=!])([A-Za-z_][A-Za-z0-9_]*)>")
_rx_cache: dict[str, re.Pattern | None] = {}
_rx_lock = threading.Lock()


class JsUnavailable(RuntimeError):
    """The JavaScript engine (dukpy) is not installed."""


def _engine():
    try:
        import dukpy  # noqa: F401
    except ImportError as e:  # pragma: no cover - dukpy is a normal dependency since 3.0
        raise JsUnavailable("The JavaScript engine for site sources is missing: install it with "
                            "'pip install dukpy' (the launcher does this for you).") from e
    return dukpy


def compile_qt_regex(rx: str) -> re.Pattern | None:
    """Qt QRegularExpression (PCRE2) pattern -> Python re, DotMatchesEverything like Grabber.
    Returns None (and logs once) when the pattern cannot be used: Qt also returns no match then."""
    with _rx_lock:
        if rx in _rx_cache:
            return _rx_cache[rx]
    py = _NAMED.sub(r"(?P<\1>", rx)
    pat = None
    try:
        pat = re.compile(py, re.S)
    except re.error:
        try:  # the optional 'regex' module understands more PCRE syntax (\p{L}, ...)
            import regex  # type: ignore
            pat = regex.compile(py, regex.S | regex.V0)
        except Exception as e:  # noqa: BLE001
            log.warning("source regex not usable (%s): %.120s", e, rx)
            pat = None
    with _rx_lock:
        _rx_cache[rx] = pat
    return pat


def regex_matches(rx: str, txt: str) -> list[dict]:
    pat = compile_qt_regex(rx)
    if pat is None or not isinstance(txt, str):
        return []
    out = []
    for m in pat.finditer(txt):
        d: dict = {}
        for name, val in m.groupdict().items():
            if not val:
                continue
            base, _, num = name.rpartition("_")
            key = base if base and num.isdigit() else name
            d[key] = val
        for i in range(0, (m.re.groups or 0) + 1):
            g = m.group(i)
            d[str(i)] = g if g is not None else ""
        out.append(d)
    return out


# ---- XML: Qt QDomDocument shape ---------------------------------------------------------------
def parse_xml(txt: str):
    from xml.dom import minidom
    from xml.parsers.expat import ExpatError
    if not isinstance(txt, str):
        return None
    try:
        doc = minidom.parseString(txt.encode("utf-8"))
    except (ExpatError, ValueError) as e:
        log.warning("Error parsing XML file: %s", e)
        return None
    return _xml_rec(doc)


def _xml_rec(node):
    from xml.dom import Node
    t = node.nodeType
    if t in (Node.TEXT_NODE, Node.CDATA_SECTION_NODE):
        return node.nodeValue
    obj: dict = {}
    if t == Node.ELEMENT_NODE and node.attributes is not None and node.attributes.length:
        obj["@attributes"] = {a.name: a.value for a in node.attributes.values()}
    for child in node.childNodes:
        ct = child.nodeType
        if ct == Node.TEXT_NODE and not child.nodeValue.strip():
            continue  # QDomDocument drops whitespace-only text nodes
        if ct == Node.COMMENT_NODE:
            continue
        name = {Node.TEXT_NODE: "#text", Node.CDATA_SECTION_NODE: "#cdata-section"}.get(ct, child.nodeName)
        val = _xml_rec(child)
        if name not in obj:
            obj[name] = val
        else:
            if not isinstance(obj[name], list) or name == "@attributes":
                obj[name] = [obj[name]]
            obj[name].append(val)
    return obj


# ---- HTML documents ---------------------------------------------------------------------------
class _HtmlRegistry:
    """Nodes handed to JavaScript by number; cleared after every top-level call."""

    def __init__(self):
        self.nodes: list = []

    def add(self, node) -> int:
        self.nodes.append(node)
        return len(self.nodes) - 1

    def parse(self, src, fragment=False):
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            log.error("HTML parsing needs the 'beautifulsoup4' package")
            return None
        if not isinstance(src, str):
            return None
        soup = BeautifulSoup(src, "html.parser")
        return self.add(soup)

    def op(self, method, h, arg=None):
        try:
            n = self.nodes[int(h)]
        except (IndexError, TypeError, ValueError):
            return None
        if method == "find":
            try:
                found = n.select(arg)
            except Exception as e:  # noqa: BLE001 - bad selector: Grabber returns an empty list
                log.debug("css selector %r: %s", arg, e)
                return []
            return [self.add(x) for x in found]
        if method == "outerHTML":
            return str(n) if getattr(n, "name", None) != "[document]" else n.decode_contents()
        if method == "innerHTML":
            return n.decode_contents()
        if method == "innerText":
            return n.get_text()
        if method == "tag":
            return n.name if n.name != "[document]" else "html"
        if method == "attr":
            v = n.get(arg) if hasattr(n, "get") else None
            if isinstance(v, list):
                v = " ".join(v)
            return v
        if method in ("path", "pathIds"):
            chain = [n] + [p for p in n.parents if getattr(p, "name", None) and p.name != "[document]"]
            chain.reverse()
            if method == "path":
                return [c.name for c in chain if c.name != "[document]"]
            return [str(c.get("id") or "") for c in chain if c.name != "[document]"]
        if method == "parent":
            p = n.parent
            return None if p is None else self.add(p)
        return None


PRELUDE = r"""
var console = {
  _s: function (a) { var o = []; for (var i = 0; i < a.length; i++) { var x = a[i];
        o.push(typeof x === "object" ? JSON.stringify(x) : String(x)); } return o.join(" "); },
  debug: function () { call_python("__log", "debug", console._s(arguments)); },
  log: function () { call_python("__log", "info", console._s(arguments)); },
  info: function () { call_python("__log", "info", console._s(arguments)); },
  warn: function () { call_python("__log", "warning", console._s(arguments)); },
  error: function () { call_python("__log", "error", console._s(arguments)); }
};
var Grabber = {};
Grabber.regexMatches = function (rx, txt) { return call_python("__rx", String(rx), String(txt)) || []; };
Grabber.htmlDecode = function (txt) { return call_python("__html_decode", String(txt)); };
Grabber.parseXML = function (txt) { var r = call_python("__xml", String(txt)); return r === null ? undefined : r; };
function __PackratNode(h) { this.__h = h; }
__PackratNode.prototype.find = function (css) {
  var hs = call_python("__html_op", "find", this.__h, String(css)) || [];
  var out = []; for (var i = 0; i < hs.length; i++) { out.push(new __PackratNode(hs[i])); } return out; };
__PackratNode.prototype.outerHTML = function () { return call_python("__html_op", "outerHTML", this.__h); };
__PackratNode.prototype.innerHTML = function () { return call_python("__html_op", "innerHTML", this.__h); };
__PackratNode.prototype.innerText = function () { return call_python("__html_op", "innerText", this.__h); };
__PackratNode.prototype.tag = function () { return call_python("__html_op", "tag", this.__h); };
__PackratNode.prototype.attr = function (a) { var v = call_python("__html_op", "attr", this.__h, String(a));
  return v === null ? undefined : v; };
__PackratNode.prototype.path = function () { return call_python("__html_op", "path", this.__h); };
__PackratNode.prototype.pathIds = function () { return call_python("__html_op", "pathIds", this.__h); };
__PackratNode.prototype.parent = function () { var h = call_python("__html_op", "parent", this.__h);
  return h === null ? undefined : new __PackratNode(h); };
Grabber.parseHTML = function (src, fragment) { var h = call_python("__html_parse", String(src), !!fragment);
  return h === null ? undefined : new __PackratNode(h); };
"""

DISPATCH = r"""
var __packrat = {
  get: function (path) { var o = exports.source; for (var i = 0; i < path.length; i++) {
      if (o === undefined || o === null) { return undefined; } o = o[path[i]]; } return o; },
  call: function (path, args) {
    var f = __packrat.get(path);
    if (typeof f !== "function") { return { "__missing": true }; }
    try {
      var parent = __packrat.get(path.slice(0, path.length - 1));
      var r = f.apply(parent, args || []);
      return { "result": r === undefined ? null : r };
    } catch (e) {
      return { "__error": (e && e.message) ? String(e.message) : String(e),
               "line": (e && e.lineNumber) || null, "stack": (e && e.stack) ? String(e.stack) : null };
    }
  },
  fn: function (o) { return typeof o === "function"; },
  meta: function (m) { var out = {}; for (var k in (m || {})) { var f = m[k] || {}; var c = {};
      for (var j in f) { if (typeof f[j] !== "function") { c[j] = f[j]; } else { c[j + "_fn"] = true; } }
      out[k] = c; } return out; },
  describe: function () {
    var s = exports.source || {}; var apis = {};
    for (var k in (s.apis || {})) {
      var a = s.apis[k] || {}; var d = { name: a.name, auth: a.auth || [], maxLimit: a.maxLimit,
        forcedLimit: a.forcedLimit, forcedTokens: a.forcedTokens || [], has: {}, flags: {}, endpoints: {} };
      var parts = ["search", "details", "gallery", "tagTypes", "tags", "check"];
      for (var i = 0; i < parts.length; i++) { var p = a[parts[i]];
        d.has[parts[i]] = !!(p && typeof p === "object" && typeof p.url === "function");
        if (parts[i] === "tagTypes" && p === false) { d.flags.tagTypesDisabled = true; }
        if (p && typeof p === "object") { d.flags[parts[i] + ".parseErrors"] = !!p.parseErrors;
          if (p.parseInput) { d.flags[parts[i] + ".parseInput"] = true; }
          if (p.fullResults) { d.flags[parts[i] + ".fullResults"] = true; } } }
      for (var e in (a.endpoints || {})) { var ep = a.endpoints[e] || {};
        d.endpoints[e] = { name: ep.name || e, auth: ep.auth, parseErrors: !!ep.parseErrors,
                           input: __packrat.meta(ep.input) }; }
      apis[k] = d;
    }
    var auth = {};
    for (var an in (s.auth || {})) { var av = s.auth[an] || {}; var c = {};
      for (var ak in av) { if (typeof av[ak] !== "function") { c[ak] = av[ak]; } } auth[an] = c; }
    return { name: s.name, modifiers: s.modifiers || [], tokens: s.tokens || [], forcedTokens: s.forcedTokens || [],
             tagFormat: s.tagFormat || null, searchFormat: s.searchFormat || null, meta: __packrat.meta(s.meta),
             auth: auth, apis: apis };
  }
};
"""


_EXPORT_DECL = re.compile(r"^export\s+(?:default\s+)?(var|let|const|function\*?|class)\s+([A-Za-z_$][\w$]*)", re.M)
_EXPORT_LIST = re.compile(r"^export\s*\{([^}]*)\}\s*;?", re.M)


def esm_to_commonjs(code: str) -> str:
    """Sources from registries are built as ES modules ("export var source = ..."); the engine runs
    scripts, so exports are turned into assignments on the `exports` object the bootstrap provides."""
    if "export" not in code:
        return code
    names = []

    def decl(m):
        names.append(m.group(2))
        return m.group(1) + " " + m.group(2)
    code = _EXPORT_DECL.sub(decl, code)
    tail = []

    def lst(m):
        for part in m.group(1).split(","):
            part = part.strip()
            if not part:
                continue
            src, _, alias = part.partition(" as ")
            tail.append(f"exports.{(alias or src).strip()} = {src.strip()};")
        return ""
    code = _EXPORT_LIST.sub(lst, code)
    extra = [f"exports.{n} = {n};" for n in names] + tail
    return code + "\n" + "\n".join(extra) if extra else code


class SourceRuntime:
    """One Grabber model running in its own QuickJS context."""

    def __init__(self, model_js: str | Path, helper_js: str | Path, name: str = ""):
        self.name = name or Path(model_js).parent.name
        self._lock = threading.RLock()
        self._html = _HtmlRegistry()
        dukpy = _engine()
        self.js = dukpy.JSInterpreter()
        self.js.export_function("__log", self._log)
        self.js.export_function("__rx", regex_matches)
        self.js.export_function("__xml", parse_xml)
        self.js.export_function("__html_decode", lambda t: _html.unescape(t or ""))
        self.js.export_function("__html_parse", self._html.parse)
        self.js.export_function("__html_op", self._html.op)
        model = esm_to_commonjs(Path(model_js).read_text("utf-8-sig"))
        helper = Path(helper_js).read_text("utf-8-sig")
        boot = (PRELUDE + "\n" + helper + "\nvar exports = {};\n(function (exports) {\n" + model
                + "\n})(exports);\n" + DISPATCH + "\ntrue;")
        try:
            self.js.evaljs(boot)
        except Exception as e:  # dukpy.JSRuntimeError (syntax error in a third-party model, ...)
            raise ValueError(f"source '{self.name}' could not be loaded: {str(e).splitlines()[0][:300]}") from None
        self.meta = self.js.evaljs("__packrat.describe()") or {}

    def _log(self, level, msg):
        getattr(log, level if level in ("debug", "info", "warning", "error") else "info")(
            "[%s] %s", self.name, msg)

    def call(self, path: list, *args):
        """Call exports.source.<path>(*args). Returns {"result": value} / {"__missing": True} /
        {"__error": message, "line": n}. Never raises for model errors."""
        with self._lock:
            try:
                res = self.js.evaljs("JSON.stringify(__packrat.call(dukpy.path, dukpy.args))",
                                     path=list(path), args=list(args))
            except Exception as e:  # noqa: BLE001 - host-level failure (bad JSON in args ...)
                res = json.dumps({"__error": f"JavaScript engine error: {str(e).splitlines()[0][:300]}"})
            finally:
                self._html.nodes.clear()
        try:
            return json.loads(res) if isinstance(res, str) else (res or {"__missing": True})
        except ValueError:
            return {"__error": "model returned a value that is not JSON"}

    def value(self, path: list):
        """A plain (non-function) value from the model, e.g. a meta field's options."""
        with self._lock:
            return self.js.evaljs("(function(v){ return (typeof v === 'function') ? null : v; })"
                                  "(__packrat.get(dukpy.path))", path=list(path))


def eval_expression(script: str, variables: dict):
    """Evaluate a JavaScript expression (filename / condition scripts) with the given variables defined.
    Returns the result (JSON-convertible), or raises ValueError with the JavaScript error message."""
    dukpy = _engine()
    js = dukpy.JSInterpreter()
    names = [k for k in variables if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", k)]
    decl = "".join(f"var {k} = dukpy.v[{json.dumps(k)}];\n" for k in names)
    code = decl + "JSON.stringify((function(){ return eval(dukpy.src); })())"
    try:
        out = js.evaljs(code, v={k: variables[k] for k in names}, src=script)
    except Exception as e:  # dukpy.JSRuntimeError
        raise ValueError(str(e).splitlines()[0][:300]) from None
    if out is None:
        return None
    try:
        return json.loads(out)
    except (TypeError, ValueError):
        return out
