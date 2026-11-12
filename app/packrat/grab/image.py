"""One search result ("image", "post" or "gallery") from any site, and its filename tokens.

Built from what a Grabber model returns (IImage), following Grabber's ImageFactory / Image constructor:
file / sample / preview URLs and sizes, medias, tags with types, tokens such as rating / score / author,
the page URL, the extension (guessed from the URL, the 'ext' field, or tags like animated_gif).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit

from .tags import Tag, make_tags, normalize_type, split_tags_string

RATINGS = {"g": "general", "s": "safe", "q": "questionable", "e": "explicit"}
VIDEO_EXTS = {"webm", "mp4", "m4v", "mov", "mkv", "avi", "wmv", "flv", "swf"}
ANIMATED_TAGS = {"gif", "animated_gif", "mp4", "animated_png", "webm", "animated", "video"}


def extension_of(url: str) -> str:
    try:
        path = urlsplit(url or "").path
    except ValueError:
        return ""
    name = path.rsplit("/", 1)[-1]
    if "." not in name:
        return ""
    ext = name.rsplit(".", 1)[-1].lower()
    return ext if re.fullmatch(r"[a-z0-9]{1,5}", ext) else ""


def set_extension(url: str, ext: str) -> str:
    try:
        sp = urlsplit(url)
    except ValueError:
        return url
    path = sp.path
    if "." in path.rsplit("/", 1)[-1]:
        path = path.rsplit(".", 1)[0] + "." + ext
    else:
        path = path + "." + ext
    return sp._replace(path=path).geturl()


def remove_cache_buster(url: str) -> str:
    """Grabber: drop a trailing ?<digits> (cache buster) from file URLs."""
    return re.sub(r"\?\d+$", "", url or "")


def parse_date(val) -> datetime | None:
    if val in (None, ""):
        return None
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        try:
            v = float(val)
            if v > 1e11:  # milliseconds
                v /= 1000
            return datetime.fromtimestamp(v, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    s = str(val).strip()
    if s.isdigit():
        return parse_date(int(s))
    s2 = s.replace("Z", "+00:00")
    for cand in (s2, s2.replace(" ", "T", 1)):
        try:
            d = datetime.fromisoformat(cand)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    from email.utils import parsedate_to_datetime
    try:
        d = parsedate_to_datetime(s)
        if d:
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M",
                "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y %H:%M", "%b %d, %Y %I:%M %p", "%B %d, %Y",
                "%d %B %Y", "%d %b %Y", "%Y.%m.%d %H:%M", "%Y.%m.%d"):
        try:
            d = datetime.strptime(s, fmt)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    m = re.search(r"(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago", s)
    if m:
        from datetime import timedelta
        n, unit = int(m.group(1)), m.group(2)
        secs = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800, "month": 2592000,
                "year": 31536000}[unit]
        return datetime.now(timezone.utc) - timedelta(seconds=n * secs)
    return None


@dataclass
class Media:
    url: str
    type: str = ""        # full | sample | preview | ""
    width: int = 0
    height: int = 0
    file_size: int = 0
    bitrate: int = 0


@dataclass
class Image:
    site_key: str                    # Site.key (host)
    id: int | str = 0
    md5: str = ""
    file_url: str = ""
    sample_url: str = ""
    preview_url: str = ""
    page_url: str = ""
    width: int = 0
    height: int = 0
    file_size: int = 0
    sample_width: int = 0
    sample_height: int = 0
    preview_width: int = 0
    preview_height: int = 0
    preview_rect: tuple = ()       # (x, y, w, h) inside a sprite sheet (E-Hentai gallery pages); empty = whole image
    ext: str = ""
    name: str = ""
    sources: list = field(default_factory=list)
    tags: list = field(default_factory=list)          # list[Tag]
    data: dict = field(default_factory=dict)          # extra tokens: rating, score, date, author, ...
    identity: dict = field(default_factory=dict)
    details_endpoint: dict | None = None
    medias: list = field(default_factory=list)        # list[Media]
    is_gallery: bool = False
    gallery_count: int = -1
    position: int = 0
    search: list = field(default_factory=list)        # the query that found it (words)
    parent_gallery: "Image | None" = None
    details_loaded: bool = False
    raw: dict = field(default_factory=dict)

    # ---- construction from a model result ---------------------------------------------------
    @classmethod
    def from_model(cls, site, raw: dict, position: int = 0, search=None, type_ids=None) -> "Image | None":
        if not isinstance(raw, dict):
            return None
        d: dict = {}
        identity, data, tags, medias = {}, {}, None, []
        for key, val in raw.items():
            if val is None:
                continue
            if key == "identity" and isinstance(val, dict):
                identity = val
            elif key in ("tags_obj",) or (key == "tags" and isinstance(val, list)):
                tags = make_tags(val, type_ids)
            elif key == "tokens" and isinstance(val, dict):
                for k, v in val.items():
                    if isinstance(v, str) and v.startswith("date:"):
                        v = parse_date(v[5:]) or v
                    elif isinstance(v, str) and v.startswith("b64:"):
                        import base64
                        try:
                            v = base64.b64decode(v[4:]).hex()
                        except ValueError:
                            pass
                    data[k] = v
            elif key == "medias" and isinstance(val, list):
                for m in val:
                    if isinstance(m, dict) and m.get("url"):
                        medias.append(Media(site.fix_url(str(m["url"])), str(m.get("type") or ""),
                                            _int(m.get("width")), _int(m.get("height")), _int(m.get("file_size")),
                                            _int(m.get("bitrate"))))
            elif key == "details_endpoint" and isinstance(val, dict):
                d["__details_endpoint"] = val
            elif isinstance(val, list):
                d[key] = "\n".join(map(str, val)) if key == "sources" else " ".join(map(str, val))
            elif isinstance(val, bool):
                d[key] = "true" if val else "false"
            else:
                d[key] = str(val)
        if not d and not medias:
            return None
        img = cls(site.key, raw=raw)
        img.identity = identity
        img.details_endpoint = d.pop("__details_endpoint", None)
        img._apply(site, d, data, tags, medias, position, search, type_ids)
        return img

    def _apply(self, site, d: dict, data: dict, tags, medias, position, search, type_ids):
        # ImageFactory transforms
        for k, tok in (("parent_id", "parentid"), ("creator_id", "authorid")):
            if d.get(k) and tok not in data:
                data[tok] = _int(d[k])
        for k in ("author", "status", "score"):
            if d.get(k) and k not in data:
                data[k] = d[k]
        for k in ("has_children", "has_note", "has_comments"):
            if d.get(k) and k not in data:
                data[k] = d[k] == "true"
        if d.get("rating") and "rating" not in data:
            r = d["rating"].strip()
            data["rating"] = RATINGS.get(r.lower(), r.lower())
        typed = []
        for tt in ("general", "artist", "character", "copyright", "model", "species", "meta"):
            if d.get(f"tags_{tt}"):
                typed += [Tag(t.replace("&amp;", "&"), tt) for t in d[f"tags_{tt}"].split()]
        if tags is None and typed:
            tags = typed
        if tags is None and d.get("tags"):
            tags = []
            for t in split_tags_string(d["tags"]):
                low = t.name.lower()
                if low.startswith("user:"):
                    data["author"] = t.name[5:]
                elif low.startswith("score:"):
                    data["score"] = _int(t.name[6:])
                elif low.startswith("rating:"):
                    r = t.name[7:]
                    data["rating"] = RATINGS.get(r.lower(), r.lower())
                elif low.startswith("size:"):
                    continue
                else:
                    tags.append(t)
        if d.get("created_at") and "date" not in data:
            data["date"] = parse_date(d["created_at"])
            data["date_raw"] = d["created_at"]
        if d.get("date") and "date" not in data:
            data["date"] = parse_date(d["date"])
            data["date_raw"] = d["date"]
        if data.get("date") is None:
            data.pop("date", None)
        self.tags = tags or []
        self.is_gallery = d.get("type") == "gallery"
        self.md5 = (d.get("md5") or "").strip().lower()
        if not self.md5 and re.fullmatch(r"[0-9a-fA-F]{32}", str(d.get("hash") or "").strip()):
            self.md5 = str(d["hash"]).strip().lower()   # Gelbooru-style APIs call it "hash"
        self.name = d.get("name", "")
        self.search = list(search) if search is not None else (d["search"].split(" ") if d.get("search") else [])
        idv = d.get("id", "")
        self.id = _int(idv) if str(idv).isdigit() else (idv or 0)
        if d.get("sources"):
            self.sources = [s for s in d["sources"].split("\n") if s]
        elif d.get("source"):
            self.sources = [d["source"]]
        self.gallery_count = _int(d.get("gallery_count"), -1)
        rect = str(d.get("preview_rect") or "").split(";")
        if len(rect) == 4 and all(x.strip().lstrip("-").isdigit() for x in rect):
            x, y, w, h = (int(v) for v in rect)
            if w > 0 and h > 0:
                self.preview_rect = (x, y, w, h)
        self.position = _int(d.get("position"), 0) or position
        for prefix, attr in (("", "file"), ("sample_", "sample"), ("preview_", "preview")):
            key = "file_url" if not prefix else prefix + "url"
            url = remove_cache_buster(site.fix_url(d[key])) if d.get(key) else ""
            setattr(self, f"{attr}_url", url)
            w, h = _int(d.get(prefix + "width")), _int(d.get(prefix + "height"))
            if attr == "file":
                self.width, self.height = (w, h) if w > 0 and h > 0 else (0, 0)
                self.file_size = _int(d.get("file_size"))
            else:
                setattr(self, f"{attr}_width", w if w > 0 and h > 0 else 0)
                setattr(self, f"{attr}_height", h if w > 0 and h > 0 else 0)
        self.medias = medias
        self._apply_medias()
        if d.get("page_url"):
            # never with the login fields (login=..&api_key=..): page_url ends up in .json sidecars, log files, file
            # names, the clipboard and Referer headers. Requests made from it get them in Site._do_url_object.
            self.page_url = site.fix_url(d["page_url"])
        self.data = data
        # complete missing tag types from the site's tag database
        unknown = [t.name for t in self.tags if not t.type]
        if unknown:
            types = site.tag_db_types(unknown)
            for t in self.tags:
                if not t.type and t.name in types:
                    t.type = normalize_type(types[t.name])
        # extension
        ext = extension_of(self.file_url)
        if d.get("ext"):
            real = d["ext"].lower().lstrip(".")
            if real != ext and self.file_url:
                self.file_url = set_extension(self.file_url, real)
            self.ext = real
        else:
            self.ext = ext
            if ext == "jpg" and self.preview_url:
                fixed = False
                prev_ext = extension_of(self.preview_url)
                if self.sample_url:
                    s_ext = extension_of(self.sample_url)
                    if s_ext not in ("jpg", "png", ext) and prev_ext == ext and s_ext:
                        self.file_url = set_extension(self.file_url, s_ext)
                        self.ext = s_ext
                        fixed = True
                if not fixed:
                    guess = self._guess_ext_from_tags(ext)
                    if guess:
                        self.file_url = set_extension(self.file_url, guess)
                        self.ext = guess
            elif ext == "webm" and self.has_tag("mp4"):
                self.file_url = set_extension(self.file_url, "mp4")
                self.ext = "mp4"
        if not self.page_url:
            self.page_url = site.page_url_for(self)
        if not self.ext and self.file_url:
            self.ext = extension_of(self.file_url)
        if not self.md5 and self.file_url:   # most boorus name the file by its MD5: duplicates are known early
            m = re.search(r"/([0-9a-f]{32})\.[A-Za-z0-9]{2,5}(?:\?|$)", self.file_url)
            if m:
                self.md5 = m.group(1)

    def _guess_ext_from_tags(self, ext):
        if (self.has_tag("swf") or self.has_tag("flash")) and ext != "swf":
            return "swf"
        if (self.has_tag("gif") or self.has_tag("animated_gif")) and ext not in ("webm", "mp4"):
            return "gif"
        if self.has_tag("mp4") and ext not in ("gif", "webm"):
            return "mp4"
        if self.has_tag("animated_png") and ext not in ("webm", "mp4"):
            return "png"
        if (self.has_tag("webm") or self.has_tag("animated")) and ext not in ("gif", "mp4"):
            return "webm"
        return None

    def _apply_medias(self):
        if not self.medias:
            return
        sizes = {"preview": (self.preview_width, self.preview_height), "sample": (self.sample_width, self.sample_height),
                 "full": (self.width, self.height)}

        def bigger(a, b):
            return a[0] * a[1] > b[0] * b[1]

        def in_range(s, lo, hi):
            return max(s) >= lo and max(s) <= hi

        for m in self.medias:
            size = (m.width, m.height)
            t = {"full": "full", "sample": "sample", "preview": "preview"}.get(m.type, "")
            if t:
                self._set_size(t, m)
                sizes[t] = size
                continue
            if sizes["preview"] == (0, 0) or (in_range(size, 150, 300) and (bigger(size, sizes["preview"])
                                                                          or not in_range(sizes["preview"], 150, 300))):
                self._set_size("preview", m)
                sizes["preview"] = size
            if in_range(size, 500, 1500) and bigger(size, sizes["sample"]):
                self._set_size("sample", m)
                sizes["sample"] = size
            if bigger(size, sizes["full"]) or not self.file_url:
                self._set_size("full", m)
                sizes["full"] = size

    def _set_size(self, kind, m: Media):
        if kind == "full":
            self.file_url, self.width, self.height = m.url, m.width, m.height
            self.file_size = m.file_size or self.file_size
        elif kind == "sample":
            self.sample_url, self.sample_width, self.sample_height = m.url, m.width, m.height
        else:
            self.preview_url, self.preview_width, self.preview_height = m.url, m.width, m.height

    # ---- queries ------------------------------------------------------------------------------
    def has_tag(self, name: str) -> bool:
        n = name.lower().replace(" ", "_")
        return any(t.name.lower() == n for t in self.tags)

    def tag_names(self) -> list[str]:
        return [t.name for t in self.tags]

    @property
    def rating(self) -> str:
        return str(self.data.get("rating") or "")

    @property
    def score(self):
        return self.data.get("score")

    @property
    def date(self) -> datetime | None:
        d = self.data.get("date")
        return d if isinstance(d, datetime) else None

    @property
    def is_video(self) -> bool:
        return (self.ext or "").lower() in VIDEO_EXTS

    @property
    def is_animated(self) -> bool:
        return self.is_video or (self.ext or "").lower() == "gif" or any(self.has_tag(t) for t in ANIMATED_TAGS)

    def key(self) -> str:
        """Unique per site: 'host/id' (or md5 / file URL when the site has no ids)."""
        return f"{self.site_key}/{self.id or self.md5 or self.file_url}"

    def url_for(self, size: str = "full") -> str:
        if size == "sample":
            return self.sample_url or self.file_url
        if size == "preview":
            return self.preview_url or self.sample_url
        return self.file_url

    def filename_from_url(self) -> str:
        try:
            name = urlsplit(self.file_url).path.rsplit("/", 1)[-1]
        except ValueError:
            return ""
        stem = name.rsplit(".", 1)[0] if "." in name else name
        return unquote(stem)

    # ---- details merge ------------------------------------------------------------------------
    def merge_details(self, site, det: dict, type_ids=None) -> None:
        """Merge a details answer: either a full image (fullResults) or {tags, imageUrl, createdAt, sources,
        pools}."""
        if not isinstance(det, dict):
            return
        if det.get("tags"):
            t = make_tags(det["tags"], type_ids)
            if t:
                self.tags = t
        url = det.get("imageUrl") or det.get("file_url")
        if url:
            url = remove_cache_buster(site.fix_url(url))
            self.file_url = url
            ext = extension_of(url)
            if ext:
                self.ext = ext
        if det.get("createdAt"):
            dt = parse_date(det["createdAt"])
            if dt:
                self.data["date"] = dt
        if det.get("sources"):
            self.sources = [s for s in det["sources"] if s]
        elif det.get("source"):
            self.sources = [det["source"]]
        if det.get("pools"):
            self.data["pools"] = det["pools"]
        self.details_loaded = True

    def merge_full(self, other: "Image") -> None:
        for f in ("md5", "file_url", "sample_url", "preview_url", "width", "height", "file_size", "ext", "name"):
            v = getattr(other, f)
            if v:
                setattr(self, f, v)
        if other.tags:
            self.tags = other.tags
        if other.sources:
            self.sources = other.sources
        self.data.update({k: v for k, v in other.data.items() if v not in (None, "")})
        if other.medias:
            self.medias = other.medias
        self.details_loaded = True

    # ---- persistence (download lists, kept results) -------------------------------------------
    def to_json(self) -> dict:
        out = {f: getattr(self, f) for f in ("site_key", "id", "md5", "file_url", "sample_url", "preview_url",
                                             "page_url", "width", "height", "file_size", "ext", "name",
                                             "sources", "identity", "is_gallery", "gallery_count", "position",
                                             "search", "details_loaded")}
        out["tags"] = [t.to_json() for t in self.tags]
        out["data"] = {k: (("date:" + v.isoformat()) if isinstance(v, datetime) else v) for k, v in self.data.items()
                       if isinstance(v, (str, int, float, bool, datetime, list, dict)) or v is None}
        if self.details_endpoint:
            out["details_endpoint"] = self.details_endpoint
        if self.parent_gallery is not None:
            out["parent_gallery"] = self.parent_gallery.to_json()
        if self.preview_rect:
            out["preview_rect"] = list(self.preview_rect)
        return out

    @classmethod
    def from_json(cls, d: dict) -> "Image":
        img = cls(d.get("site_key", ""))
        for f in ("id", "md5", "file_url", "sample_url", "preview_url", "page_url", "width", "height", "file_size",
                  "ext", "name", "sources", "identity", "is_gallery", "gallery_count", "position", "search",
                  "details_loaded"):
            if f in d:
                setattr(img, f, d[f])
        img.tags = [Tag.from_json(t) for t in d.get("tags", [])]
        data = {}
        for k, v in (d.get("data") or {}).items():
            if isinstance(v, str) and v.startswith("date:"):
                v = parse_date(v[5:]) or v
            data[k] = v
        img.data = data
        img.details_endpoint = d.get("details_endpoint")
        if isinstance(d.get("preview_rect"), list) and len(d["preview_rect"]) == 4:
            img.preview_rect = tuple(int(v) for v in d["preview_rect"])
        if d.get("parent_gallery"):
            img.parent_gallery = cls.from_json(d["parent_gallery"])
        return img


def _int(v, default: int = 0) -> int:
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return default
