"""Copy every saved image into your own booru (a "local booru"), with its tags, rating and source.

Grabber does this with command-line scripts you install yourself (PHP for Shimmie, Node.js for Danbooru
and Szurubooru, curl for MyImouto). Packrat has the uploads built in: pick the kind, enter the address
and your login in Settings > Commands > Local booru, and press Test.

  szurubooru   POST /api/posts/ (Token auth); tag categories are created / updated to match
  danbooru     POST /uploads.json then /posts.json (API key, Basic auth)
  shimmie      log in, then POST /upload (multipart data0)
  moebooru     POST /post/create (MyImouto, Moebooru: user[name] / user[password])

Upload problems never stop a download: they are logged and counted, the file stays saved. An image the
booru already has (same MD5) counts as done.
"""
from __future__ import annotations

import base64
import json
import logging
import threading

import requests

log = logging.getLogger("packrat.grab")
KINDS = (("", "Off"), ("szurubooru", "Szurubooru"), ("danbooru", "Danbooru (2.x)"), ("shimmie", "Shimmie"),
         ("moebooru", "MyImouto / Moebooru"))
SZURU_RATING = {"": "safe", "g": "safe", "general": "safe", "safe": "safe", "s": "sketchy", "sensitive": "sketchy",
                "q": "sketchy", "questionable": "sketchy", "e": "unsafe", "explicit": "unsafe", "unknown": "safe"}


class UploadError(Exception):
    pass


class LocalBooru:
    def __init__(self, kind: str, url: str, user: str, secret: str, extra_tags: str = "", timeout: float = 120):
        self.kind = kind
        self.url = (url or "").strip().rstrip("/")
        if self.url and not self.url.startswith(("http://", "https://")):
            self.url = "http://" + self.url
        self.user, self.secret, self.extra = user or "", secret or "", (extra_tags or "").split()
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "Packrat/3.0 (local booru upload)"
        self.s.headers["Accept"] = "application/json"
        self._lock = threading.Lock()
        self._logged_in = False
        self._szuru_tags: dict[str, str] = {}
        if kind == "szurubooru":
            tok = base64.b64encode(f"{self.user}:{self.secret}".encode()).decode()
            self.s.headers["Authorization"] = "Token " + tok
        elif kind == "danbooru":
            self.s.auth = (self.user, self.secret)

    @classmethod
    def from_settings(cls, st) -> "LocalBooru | None":
        kind = st.get("LocalBooru/type") or ""
        if not kind or not st.get("LocalBooru/url"):
            return None
        return cls(kind, st.get("LocalBooru/url"), st.get("LocalBooru/user") or "", st.secret("LocalBooru/password"),
                   st.get("LocalBooru/extraTags") or "")

    # ---- public ------------------------------------------------------------------------------
    def test(self) -> str:
        """Check address and login without uploading. Returns a sentence for the user; raises UploadError."""
        try:
            if self.kind == "szurubooru":
                r = self.s.get(self.url + "/api/info", timeout=20)
                if r.status_code != 200:
                    raise UploadError(f"no Szurubooru API at this address (HTTP {r.status_code})")
                r = self.s.get(self.url + f"/api/user/{requests.utils.quote(self.user)}", timeout=20)
                if r.status_code in (401, 403):
                    raise UploadError("the user name or token was refused")
                return "Connected to Szurubooru."
            if self.kind == "danbooru":
                r = self.s.get(self.url + "/profile.json", timeout=20)
                if r.status_code in (401, 403):
                    raise UploadError("the user name or API key was refused")
                if r.status_code != 200:
                    raise UploadError(f"no Danbooru API at this address (HTTP {r.status_code})")
                return f"Connected to Danbooru as {r.json().get('name', self.user)}."
            if self.kind == "shimmie":
                self._shimmie_login()
                return "Logged in to Shimmie."
            if self.kind == "moebooru":
                r = self.s.get(self.url + "/post.json?limit=1", timeout=20)
                if r.status_code != 200:
                    raise UploadError(f"no Moebooru API at this address (HTTP {r.status_code})")
                return "Moebooru answered (the login is checked on the first upload)."
        except requests.RequestException as e:
            raise UploadError(f"could not reach {self.url}: {e.__class__.__name__}") from e
        raise UploadError(f"unknown booru kind {self.kind!r}")

    def upload(self, path: str, img, rating: str = "", source: str = "") -> str:
        """Upload one file. Returns "uploaded" or "exists"; raises UploadError."""
        tags = [(t.type or "general", t.name) for t in img.tags if t.name] + [("general", t) for t in self.extra]
        try:
            return getattr(self, "_" + self.kind)(path, tags, rating or "", source or "", img)
        except requests.RequestException as e:
            raise UploadError(f"network error: {e.__class__.__name__}") from e

    # ---- kinds ---------------------------------------------------------------------------------
    def _szurubooru(self, path, tags, rating, source, img):
        api = self.url + "/api"
        for cat, name in tags:
            self._szuru_category(api, name, cat)
        meta = {"tags": [n for _, n in tags], "safety": SZURU_RATING.get(rating.lower(), "safe")}
        if source:
            meta["source"] = source
        with open(path, "rb") as f:
            r = self.s.post(api + "/posts/", files={"content": ("image.tmp", f)},
                            data={"metadata": json.dumps(meta)}, timeout=self.timeout)
        if r.status_code == 200:
            return "uploaded"
        body = _safe_json(r)
        if body.get("name") == "PostAlreadyUploadedError":
            return "exists"
        raise UploadError(f"Szurubooru refused the post: {body.get('description') or r.status_code}")

    def _szuru_category(self, api, name, cat):
        cat = {"artist": "artist", "copyright": "copyright", "character": "character", "meta": "meta",
               "species": "species"}.get(cat, "general" if cat in ("general", "") else cat)
        with self._lock:
            if self._szuru_tags.get(name) == cat:
                return
        r = self.s.get(api + f"/tag/{requests.utils.quote(name, safe='')}", timeout=20)
        if r.status_code == 404:
            r2 = self.s.post(api + "/tags", json={"names": [name], "category": cat}, timeout=20)
            if r2.status_code != 200 and cat != "default":
                # the category may not exist on this booru: create the tag without one
                self.s.post(api + "/tags", json={"names": [name], "category": "default"}, timeout=20)
        elif r.status_code == 200:
            t = _safe_json(r)
            if t.get("category") != cat:
                self.s.put(api + f"/tag/{requests.utils.quote(name, safe='')}",
                           json={"version": t.get("version"), "category": cat}, timeout=20)
        with self._lock:
            self._szuru_tags[name] = cat

    def _danbooru(self, path, tags, rating, source, img):
        with open(path, "rb") as f:
            r = self.s.post(self.url + "/uploads.json", files={"upload[files][0]": ("upload.tmp", f)},
                            timeout=self.timeout)
        if r.status_code >= 400:
            raise UploadError(f"Danbooru refused the upload: {_err(r)}")
        up = _safe_json(r)
        assets = up.get("upload_media_assets") or []
        for _ in range(30):
            if assets and assets[0].get("id"):
                break
            import time
            time.sleep(1)
            up = _safe_json(self.s.get(self.url + f"/uploads/{up.get('id')}.json", timeout=20))
            assets = up.get("upload_media_assets") or []
        if not assets:
            raise UploadError("Danbooru didn't process the upload")
        asset = assets[0]
        if asset.get("post_id"):
            return "exists"
        tag_string = " ".join((f"{c}:{n}" if c in ("artist", "copyright", "character", "meta") else n)
                              for c, n in tags)
        r = self.s.post(self.url + "/posts.json", data={"upload_media_asset_id": asset["id"],
                                                         "post[tag_string]": tag_string,
                                                         "post[rating]": (rating[:1] or "q").lower(),
                                                         "post[source]": source}, timeout=self.timeout)
        if r.status_code >= 400:
            if "duplicate" in r.text.lower():
                return "exists"
            raise UploadError(f"Danbooru refused the post: {_err(r)}")
        return "uploaded"

    def _shimmie_login(self):
        if self._logged_in:
            return
        r = self.s.post(self.url + "/user_admin/login", data={"user": self.user, "pass": self.secret}, timeout=30)
        if r.status_code >= 400 or not any("shm_user" in c.name for c in self.s.cookies):
            raise UploadError("Shimmie login failed (check the address, user and password)")
        self._logged_in = True

    def _shimmie(self, path, tags, rating, source, img):
        with self._lock:
            self._shimmie_login()
        data = {"tags": " ".join(f"{c}:{n}" if c not in ("general", "") else n for c, n in tags) or "tagme",
                "source": source}
        if rating:
            data["rating"] = rating[:1].lower()
        with open(path, "rb") as f:
            r = self.s.post(self.url + "/upload", files={"data0": (path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1], f)},
                            data=data, timeout=self.timeout)
        text = r.text
        if "already has hash" in text:
            return "exists"
        if r.status_code >= 400 or ("redirected" not in text.lower() and "/post/view/" not in r.url
                                    and r.history == []):
            raise UploadError(f"Shimmie refused the upload (HTTP {r.status_code})")
        return "uploaded"

    def _moebooru(self, path, tags, rating, source, img):
        data = {"user[name]": self.user, "user[password]": self.secret,
                "post[tags]": " ".join(f"{c}:{n}" if c in ("artist", "copyright", "character", "circle") else n
                                       for c, n in tags),
                "post[rating]": (rating[:1] or "q").lower(), "post[source]": source}
        with open(path, "rb") as f:
            r = self.s.post(self.url + "/post/create.json", files={"post[file]": ("upload", f)}, data=data,
                            timeout=self.timeout)
        body = _safe_json(r)
        if r.status_code < 400 and body.get("success", True):
            return "uploaded"
        if "md5" in str(body.get("reason", "")).lower() or "duplicate" in str(body).lower():
            return "exists"
        raise UploadError(f"the booru refused the post: {body.get('reason') or r.status_code}")


def _safe_json(r) -> dict:
    try:
        v = r.json()
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def _err(r) -> str:
    b = _safe_json(r)
    return str(b.get("message") or b.get("error") or b.get("reason") or f"HTTP {r.status_code}")[:200]
