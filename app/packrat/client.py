"""HTTP client for the whole-site archive (e6ai / e621): User-Agent, optional basic auth, API rate limit,
backoff, 403 stop.

1.6: the API (JSON) keeps its own limiter; file downloads from the static host go through
StaticFetcher (one requests session per worker thread, no per-request limit, see fast.py)."""
from __future__ import annotations

import logging
import random
import threading
import time
from email.utils import parsedate_to_datetime

import requests
from requests.adapters import HTTPAdapter

from . import __version__, siteprofile
from . import redact
from .events import control, emit

log = logging.getLogger("packrat")

BASE_URL = "https://e6ai.net"   # default site; the active one is siteprofile.current().base_url
DEFAULT_RATE = 1.0         # API requests per second (user-settable, no hard cap since 1.6)
API_RATE_GUIDE = 2.0       # e621-family API docs ask for at most ~2 requests/s (informational only)
RETRY_STATUSES = {408, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524, 525, 526, 529}
MIN_RATE = 0.25            # 429 slow-down floor (requests per second)
MAX_DELAY = 300.0          # longest single back-off wait
MAX_RETRY_AFTER = 900.0    # longest Retry-After we honor in one go
DEFAULT_MAX_OUTAGE_H = 12.0
# programming/config errors: retrying can't help
_FATAL_REQUEST_ERRORS = (requests.exceptions.InvalidURL, requests.exceptions.InvalidSchema,
                         requests.exceptions.MissingSchema, requests.exceptions.InvalidHeader)
BROWSER_UA_MARKERS = ("mozilla", "chrome", "safari", "applewebkit", "gecko/", "edg/", "opera")


class ForbiddenError(RuntimeError):
    """Raised on HTTP 403. The archiver stops instead of retrying."""


class NotFoundError(RuntimeError):
    pass


class AuthError(RuntimeError):
    """Raised on HTTP 401: the login name / API key pair was not accepted. Never retried."""


class RateLimiter:
    """Limiter for API requests (and for file URLs that are not on the static host)."""

    def __init__(self, rate: float):
        rate = float(rate)
        if not rate > 0:
            raise ValueError("rate must be > 0")
        if rate > API_RATE_GUIDE:  # 1.6: a note, not a clamp
            log.info("API rate %.2f/s is above the ~%g/s the e621-family API docs ask for", rate, API_RATE_GUIDE)
        self.rate = rate
        self.interval = 1.0 / rate
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()  # monotonic: wall-clock changes / DST can't break pacing
            if now < self._next:
                control.sleep(self._next - now)
                now = time.monotonic()
            self._next = now + self.interval

    def slow_down(self) -> None:
        """After HTTP 429: halve the rate for the rest of the run (floor MIN_RATE)."""
        new = max(MIN_RATE, self.rate / 2)
        if new < self.rate:
            log.warning("server asked us to slow down (429): rate %.2f/s -> %.2f/s for this run", self.rate, new)
            self.rate, self.interval = new, 1.0 / new


def retry_after_seconds(value: str | None) -> float | None:
    """Retry-After is either delta-seconds or an HTTP date (RFC 9110). None if absent/unparseable."""
    if not value:
        return None
    v = value.strip()
    if v.isdigit():
        return min(float(v), MAX_RETRY_AFTER)
    try:
        dt = parsedate_to_datetime(v)
    except (TypeError, ValueError, IndexError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        from datetime import timezone
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, min(dt.timestamp() - time.time(), MAX_RETRY_AFTER))  # clamp: local clock may be off


def build_user_agent(username: str | None, override: str | None = None, site: str | None = None) -> str:
    """3.0: 'Packrat/3.0 (by NAME on e6ai)' - the site's API rules ask for the project name and your username."""
    if override:
        return override.strip()
    if not username or not username.strip():
        raise ValueError(
            "A username is required for the User-Agent. Pass --username, set PACKRAT_USERNAME, "
            "or put username= in the config file (or pass a full --user-agent)."
        )
    major_minor = ".".join(__version__.split(".")[:2])
    label = (siteprofile.get(site) if site else siteprofile.current()).label
    return f"Packrat/{major_minor} (by {username.strip()} on {label})"


class Client:
    def __init__(
        self,
        user_agent: str,
        rate: float = DEFAULT_RATE,
        login: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        max_retries: int | None = None,
        timeout: tuple[float, float] = (15.0, 120.0),
        max_outage_hours: float = DEFAULT_MAX_OUTAGE_H,
    ):
        ua_low = user_agent.lower()
        if any(b in ua_low for b in BROWSER_UA_MARKERS):
            raise ValueError("Refusing to use a browser-like User-Agent (the site bans these for API tools).")
        self.base_url = (base_url or siteprofile.current().base_url).rstrip("/")
        self.limiter = RateLimiter(rate)
        self.max_retries = max_retries          # None: limited by time (max_outage_hours) only
        self.max_outage_s = float(max_outage_hours) * 3600
        self.timeout = timeout
        self.request_count = 0
        self.s = requests.Session()  # keep-alive: connections are reused across requests
        adapter = HTTPAdapter(pool_connections=2, pool_maxsize=2, max_retries=0)
        self.s.mount("https://", adapter)
        self.s.mount("http://", adapter)
        self.s.headers.update({"User-Agent": user_agent})
        self.login = login if (login and api_key) else None
        self._auth = None
        if login and api_key:
            redact.register(api_key)
            # Sent only to the API host (base_url), never to the static file / export hosts.
            self._auth = (login, api_key)
        elif login or api_key:
            raise ValueError("Set both PACKRAT_LOGIN and PACKRAT_API_KEY for authenticated access, or neither.")

    @property
    def authenticated(self) -> bool:
        return self._auth is not None

    def _auth_for(self, url: str):
        if self._auth is None:
            return None
        return self._auth if url.startswith(self.base_url + "/") or url == self.base_url else None

    def close(self) -> None:
        self.s.close()

    # ------------------------------------------------------------------
    def _backoff(self, attempt: int, waited: float, url: str, why: str, retry_after: str | None = None) -> float:
        """Sleep before retry `attempt`; returns the delay. Gives up only after max_outage_hours of
        waiting (a home connection down overnight is waited out, not treated as fatal)."""
        delay = min(MAX_DELAY, 2.0 ** min(attempt, 12)) + random.uniform(0, 1)
        ra = retry_after_seconds(retry_after)
        if ra is not None:
            delay = max(delay, ra)
        if (self.max_retries is not None and attempt > self.max_retries) or waited + delay > self.max_outage_s:
            raise RuntimeError(f"Giving up on {url}: still failing after {attempt - 1} retries over "
                               f"{waited / 3600:.1f} h ({why}). Rerun to resume.")
        log.warning("%s on %s; retry %d in %.0fs (waiting %.0f min so far)", why, url, attempt, delay, waited / 60)
        emit("net", state="waiting", why=why, retry_in=delay, waited=waited)
        control.sleep(delay)
        return delay

    def _request(self, url: str, *, params=None, stream=False, accept="application/json") -> requests.Response:
        """One logical GET: rate limited, retried on 429/5xx/network errors, stops on 403."""
        attempt, waited = 0, 0.0
        while True:
            control.checkpoint()  # Pause takes effect before the next request; Stop raises here
            self.limiter.wait()
            self.request_count += 1
            emit("request", count=self.request_count)
            try:
                r = self.s.get(url, params=params, stream=stream, timeout=self.timeout,
                               headers={"Accept": accept}, auth=self._auth_for(url))
            except _FATAL_REQUEST_ERRORS:
                raise
            except requests.RequestException as e:  # DNS failure, refused, reset, timeout, SSL, ...
                attempt += 1
                waited += self._backoff(attempt, waited, url, f"network error: {e.__class__.__name__}")
                continue
            if r.status_code == 403:
                body = "" if stream else r.text[:300]
                final_url = r.url
                r.close()
                raise ForbiddenError(
                    f"HTTP 403 Forbidden for {final_url}. Stopping all requests. "
                    f"Check your User-Agent/credentials and the site's rules. Body: {body!r}"
                )
            if r.status_code == 401:
                r.close()
                who = self.login or "(anonymous)"
                raise AuthError(
                    f"HTTP 401 for {url}: {siteprofile.current().label} did not accept the sign-in for login '{who}'. "
                    "Check the login name and API key (the key is not your password). Stopping."
                )
            if r.status_code == 404:
                r.close()
                raise NotFoundError(url)
            if r.status_code < 400:
                if attempt:
                    log.info("connection back after %.0f min", waited / 60)
                    emit("net", state="ok")
                return r
            if r.status_code not in RETRY_STATUSES:
                text = "" if stream else r.text[:300]
                r.close()
                raise RuntimeError(f"HTTP {r.status_code} for {r.url}: {text!r}")
            retry_after = r.headers.get("Retry-After")
            r.close()
            if r.status_code == 429:
                self.limiter.slow_down()
            attempt += 1
            waited += self._backoff(attempt, waited, url, f"HTTP {r.status_code}", retry_after)

    def get_json(self, path: str, params: dict | None = None):
        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"
        waited = 0.0
        for attempt in range(1, 4):
            r = self._request(url, params=params)
            try:
                return r.json()
            except ValueError:
                # e.g. an HTML maintenance page served with status 200
                snippet = r.text[:120]
                if attempt == 3:
                    raise RuntimeError(f"Non-JSON response from {url}: {snippet!r}")
                waited += self._backoff(attempt, waited, url, "non-JSON response")
            except requests.RequestException as e:  # body broke while reading
                if attempt == 3:
                    raise RuntimeError(f"response body from {url} kept breaking: {e}") from e
                waited += self._backoff(attempt, waited, url, f"broken response: {e.__class__.__name__}")
            finally:
                r.close()

    def stream(self, url: str) -> requests.Response:
        return self._request(url, stream=True, accept="*/*")

    # ------------------------------------------------------------------
    def posts(self, page: str | None, limit: int = 320, tags: str | None = None) -> list[dict]:
        params: dict = {"limit": limit}
        if page:
            params["page"] = page
        if tags:
            params["tags"] = tags
        data = self.get_json("/posts.json", params)
        posts = data.get("posts", []) if isinstance(data, dict) else data
        if not isinstance(posts, list):
            raise RuntimeError(f"unexpected /posts.json response (no post list): {str(data)[:200]!r}")
        valid = [p for p in posts if isinstance(p, dict) and isinstance(p.get("id"), int)
                 and not isinstance(p.get("id"), bool)]
        if len(valid) < len(posts):
            if not valid:  # never mistake a garbled page for "no more posts"
                raise RuntimeError("every post on this page is malformed (no integer id); rerun later")
            log.warning("ignored %d malformed post objects on page %s", len(posts) - len(valid), page)
        return valid


# --- 1.6: static file downloads ------------------------------------------------------------------
STATIC_BLOCK_STATUSES = {403, 429, 503}


class StaticBlocked(RuntimeError):
    """403 / 429 / 503 from the static file host: this file failed; the worker backs off."""

    def __init__(self, status: int, url: str, retry_after: float | None = None):
        super().__init__(f"HTTP {status} from {url}")
        self.status, self.url, self.retry_after = status, url, retry_after


class StaticFetcher:
    """GETs for file downloads, one keep-alive requests.Session per worker thread.

    No rate limiter and no auth (credentials only ever go to the API host). The descriptive
    User-Agent is kept. A URL on the API host itself is routed through Client.stream() so the API
    limiter still applies to it."""

    def __init__(self, user_agent: str, api_client: "Client | None" = None,
                 timeout: tuple[float, float] = (15.0, 120.0)):
        if any(b in user_agent.lower() for b in BROWSER_UA_MARKERS):
            raise ValueError("Refusing to use a browser-like User-Agent (the site bans these for API tools).")
        self.user_agent, self.api, self.timeout = user_agent, api_client, timeout
        self._local = threading.local()
        self._sessions: list = []
        self._lock = threading.Lock()
        self.request_count = 0

    def session(self) -> requests.Session:
        s = getattr(self._local, "s", None)
        if s is None:
            s = requests.Session()
            adapter = HTTPAdapter(pool_connections=4, pool_maxsize=4, max_retries=0)
            s.mount("https://", adapter)
            s.mount("http://", adapter)
            s.headers.update({"User-Agent": self.user_agent, "Accept": "*/*"})
            self._local.s = s
            with self._lock:
                self._sessions.append(s)
        return s

    def _is_api_host(self, url: str) -> bool:
        return bool(self.api) and url.startswith(self.api.base_url + "/")

    def get(self, url: str) -> requests.Response:
        """Streaming GET. 404 -> NotFoundError; 403/429/503 -> StaticBlocked; other HTTP errors
        -> RuntimeError (5xx: requests.HTTPError so the caller retries); network errors propagate.
        Pause is handled by the download workers; Stop is honoured here."""
        control.check_stop()
        if self._is_api_host(url):
            return self.api.stream(url)
        with self._lock:
            self.request_count += 1
        r = self.session().get(url, stream=True, timeout=self.timeout)
        if r.status_code < 400:
            return r
        status = r.status_code
        ra = retry_after_seconds(r.headers.get("Retry-After"))
        r.close()
        if status == 404:
            raise NotFoundError(url)
        if status in STATIC_BLOCK_STATUSES:
            raise StaticBlocked(status, url, ra)
        if status in RETRY_STATUSES:
            raise requests.HTTPError(f"HTTP {status} from {url}")
        raise RuntimeError(f"HTTP {status} from {url}")

    def close(self) -> None:
        with self._lock:
            for s in self._sessions:
                s.close()
            self._sessions.clear()
