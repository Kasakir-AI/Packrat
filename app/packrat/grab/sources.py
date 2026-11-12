"""Sources (Grabber model types) and sites (one host each), with network politeness and logins.

  SourceType  a Grabber model: "Danbooru (2.0)", "Gelbooru (0.2)", "E621" ...  (bundled in packrat/sources,
              plus user sources in data/sources, e.g. installed from a source registry)
  Site        one host of a source type: danbooru.donmai.us, gelbooru.com, e621.net, e6ai.net ...
              settings = Packrat defaults < the source's <host>/defaults.ini < your changes (data/sites.json)

Every request a site makes goes through Site.request(): its User-Agent / headers / cookies / referer,
the per-kind interval (page, details, image, thumbnail; Grabber's defaults 1 s, 1 s, 1 s, 0 s), the
"max simultaneous downloads" limit, the proxy, retries with Retry-After, and the login (URL fields,
HTTP Basic, form POST/GET with cookie or redirect check, OAuth 2, OAuth 1).
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlsplit

import requests
from requests.adapters import HTTPAdapter

from .. import __version__, paths
from . import qtini, secrets
from .image import Image
from .jsrt import SourceRuntime
from .tags import Tag, TagDatabase, load_type_file, make_tags, save_type_file

log = logging.getLogger("packrat.grab")
API_ORDER_NAMES = {"json": "JSON", "xml": "XML", "html": "Regex", "regex": "Regex", "rss": "RSS"}
E621_FAMILY = ("e621.net", "e926.net", "e6ai.net")
BROWSER_UA_MARKERS = ("mozilla", "chrome", "safari", "applewebkit", "gecko/", "edg/", "opera")
DEFAULT_THROTTLE = {"page": 1.0, "details": 1.0, "image": 1.0, "thumbnail": 0.0, "retry": 60.0}
RETRY_STATUSES = {408, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}
SECRET_FIELDS = ("password", "apiKey", "consumerSecret", "accessToken", "refreshToken", "salt",
                 "accessTokenSecret")


class SiteError(RuntimeError):
    pass


class Forbidden(SiteError):
    pass


@dataclass
class PageResult:
    site: "Site"
    search: str
    page: int
    images: list = field(default_factory=list)
    tags: list = field(default_factory=list)
    wiki: str = ""
    page_count: int = -1
    image_count: int = -1
    url_next: str = ""
    url_prev: str = ""
    error: str = ""
    api: str = ""
    url: str = ""
    filtered: int = 0
    status: int = 0
    notes: list = field(default_factory=list)   # what Packrat did for this site (searchwords.translate)

    def info(self) -> dict:
        """'previous' page information for cursor paging (Grabber PageInformation)."""
        ids = [i.id for i in self.images if isinstance(i.id, int) and i.id > 0]
        dates = [i.date for i in self.images if i.date is not None]
        return {"page": self.page, "minId": min(ids) if ids else 0, "maxId": max(ids) if ids else 0,
                "minDate": min(dates).isoformat() if dates else "", "maxDate": max(dates).isoformat() if dates else ""}


def previous_js(info: dict | None):
    if not info or not info.get("page"):
        return None
    mi, ma = int(info.get("minId") or 0), int(info.get("maxId") or 0)
    return {"page": info["page"], "minIdM1": str(mi - 1), "minId": str(mi), "maxId": str(ma),
            "maxIdP1": str(ma + 1), "minDate": info.get("minDate") or "", "maxDate": info.get("maxDate") or ""}


class SourceType:
    def __init__(self, folder: Path, helper: Path, user: bool = False):
        self.folder = Path(folder)
        self.dir_name = self.folder.name
        self.helper = helper
        self.user = user
        self._rt = None
        self._lock = threading.Lock()
        self.default_sites = _read_list(self.folder / "sites.txt")
        self._defaults: dict[str, dict] = {}
        self.supported = _read_list(self.folder / "supported.txt")

    @property
    def rt(self) -> SourceRuntime:
        with self._lock:
            if self._rt is None:
                self._rt = SourceRuntime(self.folder / "model.js", self.helper, self.dir_name)
            return self._rt

    @property
    def meta(self) -> dict:
        return self.rt.meta

    @property
    def name(self) -> str:
        try:
            return self.meta.get("name") or self.dir_name
        except Exception:  # noqa: BLE001
            return self.dir_name

    @property
    def icon(self) -> Path | None:
        p = self.folder / "icon.png"
        return p if p.is_file() else None

    def site_defaults(self, host: str) -> dict:
        with self._lock:
            if host not in self._defaults:
                self._defaults[host] = qtini.load(self.folder / _host_dir(host) / "defaults.ini")
            return self._defaults[host]

    def site_type_ids(self, host: str) -> dict[int, str]:
        return load_type_file(self.folder / _host_dir(host) / "tag-types.txt")


def _host_dir(host: str) -> str:
    return host.replace("/", "_")


def _read_list(p: Path) -> list[str]:
    try:
        return [ln.strip() for ln in p.read_text("utf-8").splitlines() if ln.strip()]
    except OSError:
        return []


class _Throttle:
    def __init__(self):
        self._next: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, kind: str, interval: float, cancel: threading.Event | None = None) -> None:
        if interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            at = max(now, self._next.get(kind, 0.0))
            self._next[kind] = at + interval
        delay = at - time.monotonic()
        while delay > 0:
            if cancel is not None and cancel.is_set():
                raise SiteError("cancelled")
            step = min(delay, 0.25)
            time.sleep(step)
            delay -= step


class Site:
    """One host. Thread-safe: a requests.Session per thread sharing one cookie jar."""

    def __init__(self, source: SourceType, host: str, store: "SiteStore"):
        self.source = source
        self.host = host.strip().rstrip("/")
        self.key = self.host
        self.store = store
        self._local = threading.local()
        self.cookies = requests.cookies.RequestsCookieJar()
        self._throttle = _Throttle()
        self._lock = threading.RLock()
        self._sem = None
        self._sem_n = 0
        self.logged_in = False
        self.login_error = ""
        self._login_tried = False
        self._type_ids: dict[int, str] | None = None
        self._tagdb: TagDatabase | None = None
        self.consecutive_403 = 0
        self._load_cookies()

    # ---- settings -----------------------------------------------------------------------------
    def setting(self, key: str, default=None):
        user = self.store.user_settings(self.host)
        if key in user:
            return user[key]
        d = self.source.site_defaults(self.host)
        if key in d:
            return d[key]
        return default

    def set_setting(self, key: str, value) -> None:
        self.store.set_user_setting(self.host, key, value)

    @property
    def name(self) -> str:
        return str(self.setting("name") or self.host)

    @property
    def ssl(self) -> bool:
        return qtini.to_bool(self.setting("ssl"), True)

    @property
    def base_url(self) -> str:
        return ("https://" if self.ssl else "http://") + self.host

    def is_e621_family(self) -> bool:
        return self.host.split("/")[0].lower() in E621_FAMILY or self.source.dir_name == "E621"

    def auth_value(self, field_id: str) -> str:
        ov = getattr(self, "auth_override", None)   # command line -u / -w: this run only, never saved
        if ov and field_id in ov:
            return ov[field_id]
        if field_id in SECRET_FIELDS:
            v = secrets.get_secret(self.host, field_id)
            if v:
                secrets.register_redaction(v)
                return v
        v = self.setting(f"auth/{field_id}")
        return "" if v is None else str(v)

    def set_auth_value(self, field_id: str, value: str) -> bool:
        if field_id in SECRET_FIELDS:
            ok = secrets.set_secret(self.host, field_id, value)
            # source defaults (e.g. Pixiv's consumerSecret) stay in defaults.ini; only user values move
            return ok
        self.set_setting(f"auth/{field_id}", value)
        return True

    def throttle(self, kind: str) -> float:
        default = DEFAULT_THROTTLE.get(kind, 1.0)
        if self.is_e621_family() and kind in ("image", "thumbnail"):
            default = 0.0   # static file host: no per-request limit (the API keeps 1 s)
        v = self.setting(f"download/throttle_{kind}", default)
        try:
            return max(0.0, float(v))
        except (TypeError, ValueError):
            return default

    def max_simultaneous(self) -> int:
        return max(1, qtini.to_int(self.setting("download/simultaneous", 10), 10))

    def slot(self):
        n = self.max_simultaneous()
        with self._lock:
            if self._sem is None or self._sem_n != n:
                self._sem, self._sem_n = threading.BoundedSemaphore(n), n
            return self._sem

    def user_agent(self) -> str:
        from .settings import shared
        st = shared()
        username = (st.get("username") or "").strip() or _archive_username() or "anonymous"
        ua = self.setting("Headers/User-Agent")
        if self.is_e621_family():
            ua = "Packrat/%version% (by %username% on %host%)"
        if not ua:
            ua = (st.get("userAgent") or "").strip() or "Packrat/%version% (by %username%)"
        mm = ".".join(__version__.split(".")[:2])
        ua = str(ua).replace("%version%", mm).replace("%username%", username).replace("%host%", self.host)
        if "%userAgentID%" in ua:
            uid = self.setting("userAgentID")
            if not uid:
                import uuid
                uid = str(uuid.uuid4())
                self.set_setting("userAgentID", uid)
            ua = ua.replace("%userAgentID%", str(uid))
        if self.is_e621_family() and any(m in ua.lower() for m in BROWSER_UA_MARKERS):
            ua = f"Packrat/{mm} (by {username} on {self.host})"
        return ua

    def headers(self) -> dict:
        h = {"User-Agent": self.user_agent(), "Accept": "*/*"}
        for k, v in _settings_group(self.source.site_defaults(self.host), "Headers").items():
            if k.lower() != "user-agent":
                h[k] = str(v)
        for k, v in (self.store.user_settings(self.host).get("headers") or {}).items():
            if k.lower() == "user-agent" and self.is_e621_family():
                continue
            h[k] = str(v)
        return h

    def _load_cookies(self):
        vals = self.setting("cookies") or []
        if isinstance(vals, str):
            vals = [vals]
        for c in vals:
            if "=" in str(c):
                k, v = str(c).split("=", 1)
                self.cookies.set(k.strip(), v.strip(), domain=self.host.split("/")[0])
        for k, v in (self.store.user_settings(self.host).get("cookie_values") or {}).items():
            self.cookies.set(k, v, domain=self.host.split("/")[0])
        saved = secrets.get_secret(self.host, "session_cookies")
        if saved:
            try:
                for c in json.loads(saved):
                    self.cookies.set(c["name"], c["value"], domain=c.get("domain") or self.host.split("/")[0],
                                     path=c.get("path") or "/")
            except (ValueError, KeyError, TypeError):
                pass

    def save_session_cookies(self) -> None:
        data = [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path} for c in self.cookies]
        secrets.set_secret(self.host, "session_cookies", json.dumps(data))

    # ---- URLs ---------------------------------------------------------------------------------
    def fix_url(self, url: str, old: str | None = None) -> str:
        if not url:
            return ""
        url = str(url).strip()
        proto = "https" if self.ssl else "http"
        if url.startswith("//"):
            return f"{proto}:{url}"
        host = self.host
        if url.startswith("/"):
            base = host[host.index("/"):] if "/" in host else ""
            right = url[len(base):] if base and url.startswith(base) else url
            return f"{proto}://{host}{right}"
        if not url.startswith("http"):
            if old:
                from urllib.parse import urljoin
                return urljoin(old, url)
            return f"{proto}://{host}/{url}"
        if url.startswith("http://") and self.ssl and host in url:
            return "https://" + url[7:]
        return url

    def owns_url(self, url: str) -> bool:
        """The address is on this site or one of its subdomains (static1.e621.net for e621.net, cdn.donmai.us for
        danbooru.donmai.us). Login headers go only there, never to a file host elsewhere."""
        host = (urlsplit(url).hostname or "").lower()
        mine = self.host.split("/")[0].split(":")[0].lower()
        if not host or host == mine:
            return True
        if mine.replace(".", "").isdigit():
            return False
        labels = mine.split(".")
        keep = 3 if len(labels) >= 3 and len(labels[-1]) == 2 and len(labels[-2]) <= 3 else 2   # x.co.uk, x.com.au
        base = ".".join(labels[-keep:])
        return host == base or host.endswith("." + base)

    def fix_login_url(self, url: str) -> str:
        """Grabber UrlLogin::complementUrl: add the URL auth fields (login=..&api_key=..)."""
        auth = self._active_auth()
        if not auth or auth.get("type") != "url" or not url:
            return url
        has_q = "?" in url
        parts = []
        for f in auth.get("fields") or []:
            key = f.get("key")
            if not key:
                continue
            val = self._field_value(f)
            if val:
                parts.append(f"{key}={quote(val, safe='')}")
                if f.get("id") in SECRET_FIELDS:
                    secrets.register_redaction(val)
        if not parts:
            return url
        return url + ("&" if has_q else "?") + "&".join(parts)

    def _field_value(self, f: dict) -> str:
        t = f.get("type")
        if t == "const":
            return str(f.get("value") or "")
        if t == "hash":
            pseudo, password = self.auth_value("pseudo"), self.auth_value("password")
            salt = (self.auth_value("salt") or "").replace("--your-password--", "--%password%--")
            if len(password) >= 32:
                return password
            data = password
            tmpl = f.get("salt") or ""
            if tmpl and (pseudo or password):
                data = (tmpl.replace("%salt%", salt).replace("%pseudo%", pseudo)
                        .replace("%pseudo:lower%", pseudo.lower()).replace("%password%", password))
            if not data:
                return ""
            algo = hashlib.sha1 if (f.get("hash") or "sha1") == "sha1" else hashlib.md5
            return algo(data.encode("utf-8")).hexdigest()
        v = self.auth_value(f.get("id") or "")
        return v or str(f.get("def") or "")

    def page_url_for(self, img: Image) -> str:
        """Browser URL of an image (Grabber: the details URL of the first API able to load details;
        Packrat prefers the HTML one, so 'Open in browser' opens a page, not JSON)."""
        apis = self.source.meta.get("apis", {})
        order = sorted(apis, key=lambda k: 0 if apis[k].get("name") == "Regex" else 1)
        for k in order:
            if apis[k]["has"].get("details"):
                r = self.source.rt.call(["apis", k, "details", "url"], str(img.id), img.md5,
                                        {"baseUrl": self.base_url, "loggedIn": self.logged_in}, img.identity or {})
                u = _url_of(r.get("result")) if "result" in r else ""
                if u:
                    return self.fix_url(u)
        return ""

    # ---- tags ---------------------------------------------------------------------------------
    def type_ids(self) -> dict[int, str]:
        if self._type_ids is None:
            ids = self.source.site_type_ids(self.host)
            user = load_type_file(paths.data_dir() / "tag-types" / f"{_safe(self.host)}.txt")
            ids.update(user)
            self._type_ids = ids
        return self._type_ids

    def tag_db(self) -> TagDatabase:
        if self._tagdb is None:
            self._tagdb = TagDatabase(self.host)
        return self._tagdb

    def tag_db_types(self, names: list[str]) -> dict:
        db = self.tag_db()
        return db.types_for(names) if db.exists() else {}

    def load_tag_types(self, force: bool = False) -> dict[int, str]:
        """Ask the site for its tag type list (APIs with tagTypes) and cache it in data/tag-types/."""
        if self.type_ids() and not force:
            return self._type_ids
        for k in self.api_keys(require_auth=False):
            a = self.source.meta["apis"][k]
            if not a["has"].get("tagTypes"):
                continue
            res = self._call_endpoint(k, "tagTypes", (), "details")
            if res.get("types"):
                ids = {int(t["id"]): str(t["name"]).lower() for t in res["types"] if str(t.get("id", "")).lstrip("-").isdigit()}
                if ids:
                    save_type_file(paths.data_dir() / "tag-types" / f"{_safe(self.host)}.txt", ids)
                    self._type_ids = None
                    return self.type_ids()
        return self.type_ids()

    # ---- API selection ------------------------------------------------------------------------
    def api_keys(self, require_auth: bool = True) -> list[str]:
        """API keys of the model in the order chosen for this site (Source 1..4), usable now."""
        apis = self.source.meta.get("apis", {})
        by_name: dict[str, list[str]] = {}
        for k, a in apis.items():
            by_name.setdefault((a.get("name") or "").lower(), []).append(k)
        if qtini.to_bool(self.setting("sources/usedefault"), True):
            from .settings import shared
            st = shared()
            order = [st.get(f"source_{i}") for i in range(1, 5)]
        else:
            order = [self.setting(f"sources/source_{i}", "") for i in range(1, 5)]
        out = []
        for o in order:
            nm = API_ORDER_NAMES.get(str(o or "").lower(), "")
            for k in by_name.get(nm.lower(), []):
                if k not in out:
                    out.append(k)
        # APIs not in the order list are still used as a last resort (Grabber does the same)
        for k in apis:
            if k not in out:
                out.append(k)
        if require_auth:
            active = self._active_auth_key()
            out = [k for k in out if not apis[k].get("auth") or (self.logged_in and active in apis[k]["auth"])]
        return out

    def _opts(self, limit: int = 20, page: int = 1) -> dict:
        return {"page": page, "limit": limit, "baseUrl": self.base_url, "loggedIn": self.logged_in}

    # ---- network ------------------------------------------------------------------------------
    def session(self) -> requests.Session:
        s = getattr(self._local, "s", None)
        if s is None:
            s = requests.Session()
            ad = HTTPAdapter(pool_connections=4, pool_maxsize=16, max_retries=0)
            s.mount("https://", ad)
            s.mount("http://", ad)
            s.cookies = self.cookies
            self._local.s = s
        prox = proxies()
        s.proxies = prox or {}
        s.trust_env = prox is None
        return s

    def referer(self, kind: str, page_url: str = "", img: Image | None = None) -> str | None:
        ref = self.setting({"thumbnail": "referer_preview", "image": "referer_image"}.get(kind, "referer"),
                           None) or self.setting("referer", "none")
        ref = str(ref or "none").lower()
        if ref == "host":
            return self.base_url + "/"
        if ref == "page" and page_url:
            return self.fix_url(page_url)
        if ref == "details" and img is not None and img.page_url:
            return img.page_url
        if ref == "image" and img is not None:
            return img.file_url
        return None

    def request(self, url: str, kind: str = "page", *, method: str = "GET", data=None, headers=None,
                stream: bool = False, referer: str | None = None, cancel: threading.Event | None = None,
                retries: int = 2, timeout: float | None = None, allow_redirects: bool = True):
        """One polite request. Returns the requests.Response (caller closes it when streaming).
        Raises Forbidden on HTTP 403 for pages (never retried), SiteError on network failure."""
        from .settings import shared
        to = float(timeout or shared().get("timeout") or 30)
        url = self.fix_url(url)
        h = self.headers()
        if referer:
            h["Referer"] = referer
        h.update(headers or {})
        own = self.owns_url(url)
        if own:
            self._apply_auth_headers(h)
        auth = self._active_auth()
        if (own and auth and auth.get("type") == "oauth1" and "Authorization" not in (headers or {})
                and self.auth_value("accessToken") and self.auth_value("accessTokenSecret")):
            from .login import oauth1_header
            h["Authorization"] = oauth1_header(method, url, self.auth_value("consumerKey"),
                                               self.auth_value("consumerSecret"), self.auth_value("accessToken"),
                                               self.auth_value("accessTokenSecret"))
        attempt = 0
        while True:
            self._throttle.wait(kind, self.throttle(kind), cancel)
            try:
                r = self.session().request(method, url, data=data, headers=h, stream=stream, timeout=(15, to),
                                           allow_redirects=allow_redirects)
            except requests.RequestException as e:
                attempt += 1
                if attempt > retries:
                    raise SiteError(f"{self.name}: network error for {_short(url)}: {e.__class__.__name__}") from e
                _sleep(min(30, 2 ** attempt), cancel)
                continue
            if r.status_code in RETRY_STATUSES and attempt < retries:
                ra = r.headers.get("Retry-After")
                r.close()
                attempt += 1
                wait = self.throttle("retry")
                try:
                    wait = max(float(ra), 1.0) if ra and ra.strip().isdigit() else wait
                except ValueError:
                    pass
                wait = min(wait, 120.0 if kind in ("page", "details") else 300.0)
                log.warning("%s: HTTP %s for %s, waiting %.0f s (retry %d/%d)", self.name, r.status_code,
                            _short(url), wait, attempt, retries)
                _sleep(wait, cancel)
                continue
            if r.status_code == 401 and own and self._oauth_refreshable() and attempt == 0:
                r.close()
                attempt += 1
                if self._oauth_refresh():
                    self._apply_auth_headers(h)
                    continue
            if r.status_code == 403:
                self.consecutive_403 += 1
            elif r.status_code < 400:
                self.consecutive_403 = 0
            return r

    def get_text(self, url: str, kind: str = "page", **kw) -> tuple[int, str, str]:
        """(status, text, final_url)."""
        r = self.request(url, kind, **kw)
        try:
            r.encoding = r.encoding or "utf-8"
            if r.encoding.lower() == "iso-8859-1" and "charset" not in (r.headers.get("Content-Type") or "").lower():
                r.encoding = r.apparent_encoding or "utf-8"
            return r.status_code, r.text, r.url
        finally:
            r.close()

    def _do_url_object(self, obj, kind: str, cancel=None) -> tuple[int, str, str]:
        """Fetch what a model's url() returned: a string or {url, method, data, headers} / {error}."""
        if isinstance(obj, dict):
            if obj.get("error"):
                raise SiteError(str(obj["error"]))
            url = obj.get("url") or ""
            method = (obj.get("method") or "GET").upper()
            headers = dict(obj.get("headers") or {})
            data = obj.get("data")
            if data is not None and not isinstance(data, str):
                data = json.dumps(data)
                headers.setdefault("Content-Type", "application/json")
            url = self.fix_url(self.fix_login_url(url))
            return self.get_text(url, kind, method=method, data=data, headers=headers, cancel=cancel)
        url = self.fix_url(self.fix_login_url(str(obj or "")))
        if not url:
            raise SiteError("empty URL")
        return self.get_text(url, kind, referer=self.referer(kind), cancel=cancel)

    def _call_endpoint(self, api_key: str, part: str, url_args: tuple, kind: str, cancel=None) -> dict:
        rt = self.source.rt
        r = rt.call(["apis", api_key, part, "url"], *url_args)
        if "__error" in r:
            return {"error": f"model error in {part}.url: {r['__error']}"}
        if "__missing" in r:
            return {"error": f"this API has no {part}"}
        try:
            status, text, final = self._do_url_object(r.get("result"), kind, cancel)
        except SiteError as e:
            return {"error": str(e)}
        flags = self.source.meta["apis"][api_key].get("flags", {})
        if status >= 400 and not flags.get(f"{part}.parseErrors"):
            return {"error": f"HTTP {status}", "status": status, "url": final}
        p = rt.call(["apis", api_key, part, "parse"], text, status)
        if "__error" in p:
            if status >= 400:      # an error page the script choked on: the status says more
                return {"error": f"HTTP {status}", "status": status, "url": final}
            return {"error": f"Uncaught exception at line {p.get('line') or '?'}: {p['__error']}", "status": status,
                    "url": final}
        res = p.get("result")
        if isinstance(res, dict):
            if status >= 400 and not res.get("error") and not res.get("images") and not res.get("tags"):
                # the site answered with an error page the source couldn't read (e.g. Danbooru's 503
                # "Downbooru"): say so instead of showing an empty page
                return {"error": f"HTTP {status}", "status": status, "url": final}
            res.setdefault("status", status)
            res.setdefault("url", final)
            return res
        return {"result": res, "status": status, "url": final}

    # ---- operations ---------------------------------------------------------------------------
    def search(self, search: str, page: int = 1, limit: int = 20, previous: dict | None = None,
               cancel: threading.Event | None = None) -> PageResult:
        self.ensure_login()
        added = str(self.setting("search/add", "") or "").split()
        removed = set(str(self.setting("search/remove", "") or "").split())
        words = [w for w in search.split() if w not in removed] + [w for w in added if w not in search.split()]
        q = " ".join(words)
        tr = None
        from .settings import shared
        if shared().get("Search/translateWords", True):
            from .searchwords import translate
            tr = translate(q, self)      # order:popular, rating:sfw ... in this site's own words
            q = tr.query
        errors = []
        for k in self.api_keys():
            a = self.source.meta["apis"][k]
            if not a["has"].get("search"):
                continue
            lim = limit
            if a.get("forcedLimit"):
                lim = int(a["forcedLimit"])
            elif a.get("maxLimit"):
                lim = min(lim, int(a["maxLimit"]))
            query = {"search": q, "page": page}
            if a.get("flags", {}).get("search.parseInput") and q.strip():
                query["parsedSearch"] = self._parsed_search(q)
            res = self._call_endpoint(k, "search", (query, self._opts(lim, page), previous_js(previous)), "page", cancel)
            if res.get("error"):
                errors.append(f"{a.get('name')}: {res['error']}")
                if str(res["error"]).startswith("HTTP 403"):
                    break
                continue
            pr = self._page_from(res, search, page, k, lim)
            if tr is not None:
                pr.notes = tr.notes
                if tr.checks:            # what the site couldn't do itself
                    kept = [im for im in pr.images if tr.keeps(im)]
                    pr.filtered += len(pr.images) - len(kept)
                    pr.images = kept
            return pr
        return PageResult(self, search, page, error="; ".join(errors) or "no usable API for this site")

    def _parsed_search(self, q: str):
        ops = q.split()
        ids = {}
        node = {"name": ops[0]}
        for t in ops[1:]:
            node = {"operator": "and", "left": node, "right": {"name": t, **({"id": ids[t]} if t in ids else {})}}
        return node

    def _page_from(self, res: dict, search: str, page: int, api_key: str, limit: int) -> PageResult:
        pr = PageResult(self, search, page, api=self.source.meta["apis"][api_key].get("name", api_key),
                        url=res.get("url", ""), status=res.get("status", 0))
        type_ids = self.type_ids()
        for i, raw in enumerate(res.get("images") or []):
            img = Image.from_model(self, raw, position=(page - 1) * limit + i + 1, search=search.split(), type_ids=type_ids)
            if img is not None and (img.preview_url or img.name or img.file_url):
                pr.images.append(img)
        pr.tags = make_tags(res.get("tags") or [], type_ids)
        if pr.tags:
            try:
                self.tag_db().add([t for t in pr.tags if t.type])
            except Exception:  # noqa: BLE001 - the tag cache must never break a search
                pass
        pr.wiki = str(res.get("wiki") or "").replace('href="/', f'href="{self.base_url}/')
        for a, b in (("imageCount", "image_count"), ("pageCount", "page_count")):
            try:
                v = res.get(a)
                if v not in (None, ""):
                    setattr(pr, b, int(str(v).replace(",", "")))
            except (TypeError, ValueError):
                pass
        pr.url_next = str(res.get("urlNextPage") or "")
        pr.url_prev = str(res.get("urlPrevPage") or "")
        return pr

    def details(self, img: Image, cancel=None) -> Image:
        """Load an image's details page (tags with types, real file URL, date, sources, pools)."""
        self.ensure_login()
        if img.details_endpoint:
            return self._details_endpoint(img, cancel)
        for k in self.api_keys():
            a = self.source.meta["apis"][k]
            if not a["has"].get("details"):
                continue
            res = self._call_endpoint(k, "details", (str(img.id), img.md5, {"baseUrl": self.base_url,
                                                                            "loggedIn": self.logged_in},
                                                     img.identity or {}), "details", cancel)
            if res.get("error"):
                log.debug("%s details via %s: %s", self.name, a.get("name"), res["error"])
                continue
            if a.get("flags", {}).get("details.fullResults"):
                full = Image.from_model(self, res, position=img.position, search=img.search, type_ids=self.type_ids())
                if full is not None:
                    img.merge_full(full)
                    return img
            img.merge_details(self, res, self.type_ids())
            return img
        img.details_loaded = True
        return img

    def _details_endpoint(self, img: Image, cancel=None) -> Image:
        ep = img.details_endpoint or {}
        name = ep.get("endpoint")
        for k in self.api_keys():
            eps = self.source.meta["apis"][k].get("endpoints", {})
            if name not in eps:
                continue
            res = self.endpoint(k, name, ep.get("input") or {}, cancel=cancel)
            if res.get("error"):
                continue
            imgs = res.get("images")
            if imgs:
                img.data["endpoint_images"] = imgs
            full = Image.from_model(self, res, position=img.position, search=img.search, type_ids=self.type_ids())
            if full is not None and (full.file_url or full.tags):
                img.merge_full(full)
            img.details_loaded = True
            return img
        img.details_loaded = True
        return img

    def endpoint(self, api_key: str, name: str, input_: dict, page: int = 1, limit: int = 20, previous=None,
                 cancel=None) -> dict:
        rt = self.source.rt
        r = rt.call(["apis", api_key, "endpoints", name, "url"], input_, self._opts(limit, page), previous_js(previous))
        if "__error" in r or "__missing" in r:
            return {"error": r.get("__error") or "no such endpoint"}
        try:
            status, text, final = self._do_url_object(r.get("result"), "page", cancel)
        except SiteError as e:
            return {"error": str(e)}
        p = rt.call(["apis", api_key, "endpoints", name, "parse"], text, status)
        if "__error" in p:
            return {"error": p["__error"]}
        res = p.get("result")
        return res if isinstance(res, dict) else {"result": res}

    def gallery(self, gal: Image, page: int = 1, limit: int = 20, cancel=None) -> PageResult:
        """Images inside a gallery (pools on some boorus, E-Hentai galleries, Pixiv albums ...)."""
        self.ensure_login()
        errors = []
        for k in self.api_keys():
            a = self.source.meta["apis"][k]
            if not a["has"].get("gallery"):
                continue
            q = {"id": str(gal.id), "md5": gal.md5, "identity": gal.identity or {}, "page": page}
            res = self._call_endpoint(k, "gallery", (q, self._opts(limit, page)), "page", cancel)
            if res.get("error"):
                errors.append(res["error"])
                continue
            pr = self._page_from(res, " ".join(gal.search), page, k, limit)
            for im in pr.images:
                im.parent_gallery = gal
            return pr
        if gal.data.get("endpoint_images"):
            pr = PageResult(self, " ".join(gal.search), page)
            for i, raw in enumerate(gal.data["endpoint_images"]):
                im = Image.from_model(self, raw, position=i + 1, search=gal.search, type_ids=self.type_ids())
                if im:
                    im.parent_gallery = gal
                    pr.images.append(im)
            return pr
        return PageResult(self, " ".join(gal.search), page, error="; ".join(errors) or "galleries not supported")

    def tags_page(self, page: int = 1, limit: int = 100, order: str = "count", cancel=None) -> tuple[list[Tag], str]:
        self.ensure_login()
        for k in self.api_keys():
            a = self.source.meta["apis"][k]
            if not a["has"].get("tags"):
                continue
            res = self._call_endpoint(k, "tags", ({"page": page, "order": order}, self._opts(limit, page)), "page", cancel)
            if res.get("error"):
                continue
            return make_tags(res.get("tags") or [], self.type_ids()), ""
        return [], "this site cannot list tags"

    def check(self) -> bool | None:
        for k in self.api_keys(require_auth=False):
            if self.source.meta["apis"][k]["has"].get("check"):
                res = self._call_endpoint(k, "check", (), "page")
                v = res.get("result", res.get("error") is None)
                return bool(v) if not res.get("error") else None
        return None

    # ---- login --------------------------------------------------------------------------------
    def login_type(self) -> str:
        t = self.setting("login/type")
        auth = self.source.meta.get("auth", {})
        if t in auth:
            return t
        # Grabber names: url / post / get / http_basic / oauth2 / oauth1 -> the first auth of that type
        for k, v in auth.items():
            if v.get("type") == t:
                return k
        return ""

    def _active_auth_key(self) -> str:
        return self.login_type()

    def _active_auth(self) -> dict | None:
        k = self.login_type()
        return self.source.meta.get("auth", {}).get(k) if k else None

    def has_credentials(self) -> bool:
        auth = self._active_auth()
        if not auth:
            return False
        t = auth.get("type")
        if t in ("url", "get", "post"):
            return any(self._field_value(f) for f in (auth.get("fields") or []) if f.get("type") not in ("const",))
        if t == "http_basic":
            return bool(self.auth_value("pseudo") and self.auth_value(auth.get("passwordType") or "password"))
        if t == "oauth2":
            return bool(self.auth_value("refreshToken") or self.auth_value("accessToken")
                        or self.auth_value("pseudo") or auth.get("authType") == "client_credentials")
        if t == "oauth1":
            return bool(self.auth_value("accessToken"))
        return False

    def ensure_login(self) -> None:
        if self._login_tried:
            return
        with self._lock:
            if self._login_tried:
                return
            self._login_tried = True
            if self._active_auth() and self.has_credentials():
                try:
                    ok, msg = self.login()
                    if not ok:
                        log.warning("%s: login failed: %s", self.name, msg)
                except Exception as e:  # noqa: BLE001
                    self.login_error = str(e)
                    log.warning("%s: login failed: %s", self.name, e)

    def reset_login(self) -> None:
        self._login_tried = False
        self.logged_in = False
        self.login_error = ""

    def login(self) -> tuple[bool, str]:
        from . import login as L
        auth = self._active_auth()
        if not auth:
            return False, "no login method selected for this site"
        ok, msg = L.login(self, auth)
        self.logged_in = bool(ok)
        self.login_error = "" if ok else msg
        self._login_tried = True
        return self.logged_in, msg

    def _apply_auth_headers(self, h: dict) -> None:
        auth = self._active_auth()
        if not auth or not self.has_credentials():
            return
        t = auth.get("type")
        if t == "http_basic":
            user = self.auth_value("pseudo")
            pw = self.auth_value(auth.get("passwordType") or "password")
            if user and pw:
                tok = base64.b64encode(f"{user}:{pw}".encode()).decode()
                h["Authorization"] = f"{auth.get('tokenType') or 'Basic'} {tok}"
        elif t == "oauth2":
            at = self.auth_value("accessToken")
            if at:
                h["Authorization"] = f"Bearer {at}"
        elif t == "oauth1":
            pass  # signed per request in login.oauth1_sign (Twitter); kept for completeness

    def _oauth_refreshable(self) -> bool:
        auth = self._active_auth()
        return bool(auth and auth.get("type") == "oauth2" and self.auth_value("refreshToken"))

    def _oauth_refresh(self) -> bool:
        from . import login as L
        try:
            return L.oauth2_refresh(self, self._active_auth())
        except Exception as e:  # noqa: BLE001
            log.warning("%s: token refresh failed: %s", self.name, e)
            return False


def _settings_group(d: dict, group: str) -> dict:
    pre = group + "/"
    return {k[len(pre):]: v for k, v in d.items() if k.startswith(pre)}


def _url_of(obj) -> str:
    if isinstance(obj, dict):
        return "" if obj.get("error") else str(obj.get("url") or "")
    return str(obj or "")


def _short(url: str) -> str:
    try:
        sp = urlsplit(url)
        return f"{sp.netloc}{sp.path}"[:120]
    except ValueError:
        return url[:120]


def _safe(host: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", host)


def _sleep(seconds: float, cancel: threading.Event | None):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cancel is not None and cancel.is_set():
            raise SiteError("cancelled")
        from ..events import control
        if control.stopping:
            control.check_stop()
        time.sleep(min(0.25, max(0.0, end - time.monotonic())))


PROXY_OVERRIDE: dict | None = None   # command line --proxy: this run only
_ARCHIVE_USER: list = []


def _archive_username() -> str:
    """The name typed in the Site archive tab (packrat.ini / an old e6ai-archiver.ini), used for the
    User-Agent when Settings > General > 'Your name' is empty."""
    if not _ARCHIVE_USER:
        name = ""
        try:
            import configparser
            for p in [paths.settings_ini(), *paths.legacy_ini_candidates()]:
                if Path(p).is_file():
                    cp = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";", "#"))
                    cp.read(p, encoding="utf-8-sig")
                    for sec in ("packrat", "e6ai-archiver"):
                        if cp.has_option(sec, "username"):
                            name = cp.get(sec, "username").strip()
                            break
                    if name:
                        break
        except Exception:  # noqa: BLE001 - only a convenience
            name = ""
        _ARCHIVE_USER.append(name)
    return _ARCHIVE_USER[0]


def proxies() -> dict | None:
    if PROXY_OVERRIDE:
        return PROXY_OVERRIDE
    from .settings import shared
    st = shared()
    if not st.get("Proxy/use") or st.get("Proxy/useSystem"):
        return None   # the normal behaviour: Windows / environment proxy settings, if any
    host = (st.get("Proxy/hostName") or "").strip()
    if not host:
        return None
    port = st.get("Proxy/port") or 8080
    user = (st.get("Proxy/user") or "").strip()
    pw = st.secret("Proxy/password")
    cred = f"{quote(user, safe='')}:{quote(pw, safe='')}@" if user else ""
    scheme = "socks5h" if st.get("Proxy/type") == "socks5" else "http"
    url = f"{scheme}://{cred}{host}:{port}"
    return {"http": url, "https": url}


# ================================================================================================
class SiteStore:
    """All source types and sites: bundled + user sources, Grabber's default sites, sites you added,
    and your per-site settings (data/sites.json; secrets in the keyring)."""

    def __init__(self, bundled: Path | None = None, user: Path | None = None, sites_file: Path | None = None):
        self.bundled = Path(bundled) if bundled else paths.bundled_sources_dir()
        self.user = Path(user) if user else paths.user_sources_dir()
        self.sites_file = Path(sites_file) if sites_file else paths.data_dir() / "sites.json"
        self._lock = threading.RLock()
        self.types: dict[str, SourceType] = {}
        self.sites: dict[str, Site] = {}
        self._user: dict = {}
        self.reload()

    def reload(self) -> None:
        with self._lock:
            self._user = _load_json(self.sites_file, {"sites": {}, "added": [], "removed": []})
            self._user.setdefault("sites", {})
            self._user.setdefault("added", [])
            self._user.setdefault("removed", [])
            self.types = {}
            helper = self.bundled / "helper.js"
            for base, is_user in ((self.bundled, False), (self.user, True)):
                if not base.is_dir():
                    continue
                for d in sorted(base.iterdir()):
                    if d.is_dir() and (d / "model.js").is_file():
                        self.types[d.name] = SourceType(d, helper if not (d / "helper.js").exists() else d / "helper.js",
                                                        user=is_user)
            self.sites = {}
            for st in self.types.values():
                for host in st.default_sites:
                    if host not in self._user["removed"]:
                        self.sites.setdefault(host, Site(st, host, self))
            for entry in self._user["added"]:
                st = self.types.get(entry.get("type", ""))
                host = entry.get("host", "")
                if st and host:
                    self.sites[host] = Site(st, host, self)

    # user settings
    def user_settings(self, host: str) -> dict:
        with self._lock:
            return self._user["sites"].get(host, {})

    def set_user_setting(self, host: str, key: str, value) -> None:
        with self._lock:
            d = self._user["sites"].setdefault(host, {})
            if value is None:
                d.pop(key, None)
            else:
                d[key] = value
            self._save()

    def _save(self) -> None:
        self.sites_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.sites_file.with_name(self.sites_file.name + ".tmp")
        tmp.write_text(json.dumps(self._user, indent=1, ensure_ascii=False), "utf-8")
        os.replace(tmp, self.sites_file)

    def get(self, host: str) -> Site | None:
        host = (host or "").strip().rstrip("/")
        if host.startswith(("http://", "https://")):
            host = host.split("://", 1)[1].rstrip("/")
        return self.sites.get(host) or self.sites.get(host.lower())

    def all_sites(self) -> list[Site]:
        return sorted(self.sites.values(), key=lambda s: (s.source.name.lower(), s.host))

    def add_site(self, host: str, type_name: str | None = None) -> Site:
        host = host.strip()
        if host.startswith(("http://", "https://")):
            ssl = host.startswith("https://")
            host = host.split("://", 1)[1]
        else:
            ssl = True
        host = host.rstrip("/")
        if not host:
            raise ValueError("enter the site's address, e.g. safebooru.org")
        if type_name is None:
            type_name = self.guess_type(host)
            if not type_name:
                raise ValueError("Could not guess the source type of this site: choose it in the list.")
        if type_name not in self.types:
            raise ValueError(f"unknown source type {type_name!r}")
        with self._lock:
            self._user["added"] = [e for e in self._user["added"] if e.get("host") != host]
            self._user["added"].append({"host": host, "type": type_name})
            if host in self._user["removed"]:
                self._user["removed"].remove(host)
            self._user["sites"].setdefault(host, {})["ssl"] = ssl
            self._save()
            self.sites[host] = Site(self.types[type_name], host, self)
            return self.sites[host]

    def remove_site(self, host: str) -> None:
        with self._lock:
            self._user["added"] = [e for e in self._user["added"] if e.get("host") != host]
            if host not in self._user["removed"] and any(host in t.default_sites for t in self.types.values()):
                self._user["removed"].append(host)
            self._save()
            self.sites.pop(host, None)
        # its passwords, keys and session cookies don't stay behind in the Credential Manager
        secrets.forget_scope(host, [*SECRET_FIELDS, "session_cookies"])

    def guess_type(self, host: str) -> str | None:
        """Grabber's 'Guess': ask each source type's 'check' API (one request each, stops at the first yes)."""
        for st in self.types.values():
            if host in st.supported or host in st.default_sites:
                return st.dir_name
        for st in self.types.values():
            try:
                tmp = Site(st, host, self)
                ok = tmp.check()
            except Exception:  # noqa: BLE001
                ok = None
            if ok:
                return st.dir_name
        return None


def _load_json(p: Path, default):
    try:
        v = json.loads(Path(p).read_text("utf-8"))
        return v if isinstance(v, type(default)) else default
    except (OSError, ValueError):
        return json.loads(json.dumps(default))


_store: SiteStore | None = None
_store_lock = threading.Lock()


def store() -> SiteStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = SiteStore()
        return _store


