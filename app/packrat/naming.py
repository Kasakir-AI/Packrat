"""File naming and folder layout from post metadata, with Windows-safe sanitizing.

Templates use {field} or {field:N} (N = max items for multi-valued fields), e.g.
    name   "{source_name}_{id}.{ext}"     (default; total name <= 60 chars, source_name cut first)
    folder "{director}/{rating}"          ('/' separates folder levels; "" = flat)
e6ai does not keep the original upload filename (neither the API nor the export has one), so
{source_name} is a best-effort stem taken from the post's source URLs; without one it is the id.
Every name contains the post id (it is appended if the template has neither {id} nor {md5}),
so two posts can never map to the same file. md5 verification never depends on the name.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

# e6ai tag category numbers (tags.csv.gz `category` column). Verified 2026-11-04 by joining the
# live tags export against categorized API posts: 0 general, 1 director, 3 franchise, 4 character,
# 5 species, 7 meta. 2 contributor ("*_(editor)" tags), 6 invalid and 8 lore ("*_(lore)") were
# confirmed from the tag names in the export. Unknown numbers become "cat<N>" (never an error).
CATEGORY_NAMES = {0: "general", 1: "director", 2: "contributor", 3: "franchise", 4: "character",
                  5: "species", 6: "invalid", 7: "meta", 8: "lore"}
RATINGS = {"s": "safe", "q": "questionable", "e": "explicit"}
MULTI_FIELDS = {"director", "contributor", "franchise", "character", "species", "general", "meta",
                "lore", "invalid", "tags", "artist", "copyright"}   # 3.0: artist/copyright = e621's words
SCALAR_FIELDS = {"id", "md5", "ext", "source_name", "rating", "rating_letter", "rating_word", "score", "fav_count",
                 "width", "height", "year", "month", "day", "date", "md5_ab", "md5_cd"}
# Director-category tags that are warnings/flags rather than people (seen in the live export).
DEFAULT_DIRECTOR_EXCLUDE = ("conditional_dnp", "avoid_posting", "sound_warning", "loud_sound_warning",
                            "epilepsy_warning", "eyestrain_warning", "jumpscare_warning", "3rd-party-edit")
NAME_PRESETS = {
    "default": "{source_name}_{id}.{ext}",
    "detailed": "{id}_{director}_{character}_{species}_{rating}.{ext}",
    "full": "{id}_{director}_{franchise}_{character}_{species}_{rating}_{date}.{ext}",
    "short": "{id}_{director}.{ext}",
    "minimal": "{id}.{ext}",
    "md5": "{md5}.{ext}",
}
SHORT_NAME_MAX = 60     # default cap for templates using {source_name}
LONG_NAME_MAX = 120     # default cap for metadata templates
MEDIA_EXTS = {"jpg", "jpeg", "jpe", "jfif", "png", "apng", "gif", "webp", "avif", "jxl", "heic", "heif",
              "bmp", "tif", "tiff", "psd", "webm", "mp4", "m4v", "mov", "mkv", "avi", "wmv", "flv", "swf"}
FOLDER_PRESETS = {
    "director-rating": "{director}/{rating}", "director": "{director}", "director-year": "{director}/{year}",
    "year-month": "{year}/{month}", "franchise": "{franchise}/{director}", "species": "{species}/{director}",
    "rating": "{rating}/{director}", "ext": "{ext}/{director}", "flat": "", "md5": "{md5_ab}/{md5_cd}",
}
MULTI_DIRECTOR = ("first", "multi", "copy", "hardlink")
FILES_DIR = "files"

_FIELD = re.compile(r"\{([a-z][a-z0-9_]*)(?::([0-9]{1,3}))?\}")
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f-\x9f]')
_BIDI = re.compile("[\u200e\u200f\u202a-\u202e\u2066-\u2069\u061c\ufeff]")  # direction overrides etc.
_RESERVED = {"con", "prn", "aux", "nul", "conin$", "conout$", "clock$",
             *(f"{d}{n}" for d in ("com", "lpt") for n in "0123456789\u00b9\u00b2\u00b3")}


def category_name(n) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "uncategorized"
    return CATEGORY_NAMES.get(n, f"cat{n}")


def _u16(s: str) -> int:
    """Length as NTFS counts it (UTF-16 code units; emoji count 2)."""
    return len(s.encode("utf-16-le")) // 2


def truncate(s: str, limit: int) -> str:
    while s and _u16(s) > limit:
        s = s[:-1]
    while s and (unicodedata.combining(s[-1]) or s[-1] in "\u200d"):  # no dangling accent / ZWJ
        s = s[:-1]
    return s


def sanitize_component(s: str, limit: int = 120, placeholder: str = "unknown") -> str:
    """One Windows-safe path component: NFC, no illegal/control/bidi chars, no trailing dot or
    space, not a reserved device name (CON, NUL, COM1, LPT¹, ...), at most `limit` UTF-16 units."""
    s = unicodedata.normalize("NFC", str(s))
    s = _BIDI.sub("", s)
    s = _ILLEGAL.sub("_", s)
    s = truncate(s.strip(), limit).rstrip(" .")
    if not s or set(s) <= {"."}:
        s = placeholder
    if s.split(".")[0].rstrip(" ").lower() in _RESERVED:
        s = truncate("_" + s, limit)
    return s


def _segment(url: str) -> str:
    try:
        path = urlsplit(url.strip()).path     # drops ?query and #fragment
    except ValueError:
        return ""
    return unquote(path.rstrip("/").rsplit("/", 1)[-1]).strip()


def _clean_stem(stem: str) -> str:
    stem = _ILLEGAL.sub("_", re.sub(r"\s+", "_", stem))
    return stem.strip("._- ") if re.search(r"\w", stem) else ""


def source_name(sources) -> str | None:
    """Best-effort original filename stem from a post's sources (list, or newline-separated string).
    1st choice: the first source whose last path segment is a media file name ("abc.png" -> "abc").
    2nd choice: the first source with any usable last path segment (e.g. ".../status/1234" -> "1234").
    Returns None when nothing usable is found (the caller then uses the post id)."""
    if isinstance(sources, str):
        sources = sources.splitlines()
    urls = [u for u in (sources or []) if isinstance(u, str) and u.strip()]
    for u in urls:
        seg = _segment(u)
        if "." in seg:
            stem, ext = seg.rsplit(".", 1)
            if ext.lower() in MEDIA_EXTS and _clean_stem(stem):
                return _clean_stem(stem)
    for u in urls:
        seg = _segment(u)
        if "//" not in u and "/" not in seg and not u.lower().startswith(("http", "www.")):
            continue  # plain text, not a URL
        if urlsplit(u.strip()).path.strip("/") == "":
            continue  # bare domain, no path
        stem = seg.rsplit(".", 1)[0] if re.fullmatch(r".+\.[A-Za-z0-9]{1,5}", seg) else seg
        if _clean_stem(stem):
            return _clean_stem(stem)
    return None


# ----------------------------------------------------------------------------
@dataclass
class PostInfo:
    id: int
    md5: str | None = None
    ext: str | None = None
    rating: str | None = None
    score: int | None = None
    fav_count: int | None = None
    width: int | None = None
    height: int | None = None
    created_at: str | None = None
    cats: dict = field(default_factory=dict)     # category name -> [tags]
    categorized: bool = True                       # False: flat tags only (tags export not loaded)
    sources: list = field(default_factory=list)

    @classmethod
    def from_row(cls, row, lookup=None) -> "PostInfo":
        """row = (id, md5, ext, rating, score, fav_count, width, height, created_at, tags, tag_cats, json)."""
        pid, md5, ext, rating, score, fav, w, h, created, flat, tag_cats, raw = row
        cats, categorized = None, True
        if tag_cats:
            try:
                cats = json.loads(tag_cats)
            except ValueError:
                cats = None
        if not isinstance(cats, dict):
            cats = categorize_api_tags(raw)
        if cats is None:
            cats, categorized = categorize_flat(flat, lookup)
        return cls(pid, md5, ext, rating, score, fav, w, h, created, cats, categorized, sources_of(raw))

    def values(self, name: str) -> list[str]:
        if name == "tags":
            out = set()
            for v in self.cats.values():
                out.update(v)
            return sorted(out)
        name = {"artist": "director", "copyright": "franchise"}.get(name, name)
        return list(self.cats.get(name) or [])


def sources_of(raw_json: str | None) -> list:
    """Source URLs of a stored post: API "sources" (list) or export "source" (newline-separated)."""
    try:
        post = json.loads(raw_json) if raw_json else {}
    except ValueError:
        return []
    if not isinstance(post, dict):
        return []
    src = post.get("sources", post.get("source"))
    if isinstance(src, str):
        src = src.splitlines()
    return [u for u in src if isinstance(u, str)] if isinstance(src, list) else []


def categorize_api_tags(raw_json: str | None) -> dict | None:
    """Categorized tags from a stored API post ({"tags": {"director": [...], ...}}), else None."""
    if not raw_json:
        return None
    try:
        post = json.loads(raw_json)
    except ValueError:
        return None
    tags = post.get("tags") if isinstance(post, dict) else None
    if not isinstance(tags, dict):
        return None
    out = {}
    for k, v in tags.items():
        if isinstance(v, list):
            vals = [t for t in v if isinstance(t, str) and t]
            if vals:
                # 3.0: e621 calls categories 1 and 3 "artist" / "copyright"; the engine's canonical
                # names are e6ai's "director" / "franchise" (see siteprofile.py)
                k = _API_ALIASES.get(str(k), str(k))
                out.setdefault(k, []).extend(x for x in vals if x not in out.get(k, ()))
    return out


_API_ALIASES = {"artist": "director", "copyright": "franchise"}


def categorize_flat(flat: str | None, lookup) -> tuple[dict, bool]:
    """Split a flat tag string with a name->category lookup (dict or callable). Tags the lookup
    doesn't know go to "uncategorized". Returns (cats, categorized?)."""
    names = (flat or "").split()
    if not names:
        return {}, True
    if lookup is None:
        return {"uncategorized": names}, False
    found = lookup(names) if callable(lookup) else {n: lookup[n] for n in names if n in lookup}
    out: dict[str, list[str]] = {}
    for n in names:
        out.setdefault(category_name(found[n]) if n in found else "uncategorized", []).append(n)
    return out, bool(found)


# ----------------------------------------------------------------------------
def parse_template(t: str, what: str) -> list:
    """-> list of literal strings and (field, limit) tuples. Raises ValueError on mistakes."""
    parts, pos = [], 0
    for m in _FIELD.finditer(t):
        parts.append(t[pos:m.start()])
        name, lim = m.group(1), m.group(2)
        if name not in MULTI_FIELDS | SCALAR_FIELDS:
            raise ValueError(f"{what}: unknown field {{{name}}}. Fields: "
                             + ", ".join(sorted(MULTI_FIELDS | SCALAR_FIELDS)))
        if lim and name not in MULTI_FIELDS:
            raise ValueError(f"{what}: {{{name}:{lim}}} - ':N' only applies to tag fields")
        parts.append((name, int(lim) if lim else None))
        pos = m.end()
    parts.append(t[pos:])
    for p in parts:
        if isinstance(p, str) and ("{" in p or "}" in p):
            raise ValueError(f"{what}: stray brace in {t!r} (fields look like {{director}} or {{tags:5}})")
    return [p for p in parts if p != ""]


@dataclass
class Naming:
    name_template: str = NAME_PRESETS["default"]
    folder_template: str = FOLDER_PRESETS["director-rating"]
    multi_director: str = "first"
    max_items: int = 3
    sep: str = ","
    empty: str = "unknown"
    rating_style: str = "word"
    name_max: int = 0          # 0 = auto: 60 if the template uses {source_name}, else 120
    max_path: int = 240
    director_exclude: tuple = DEFAULT_DIRECTOR_EXCLUDE

    def __post_init__(self):
        self.name_template = NAME_PRESETS.get(self.name_template, self.name_template)
        self.folder_template = FOLDER_PRESETS.get(self.folder_template, self.folder_template)
        if self.multi_director not in MULTI_DIRECTOR:
            raise ValueError(f"multi_director must be one of {', '.join(MULTI_DIRECTOR)}")
        if self.rating_style not in ("word", "letter"):
            raise ValueError("rating_style must be 'word' or 'letter'")
        if not self.name_max:
            self.name_max = SHORT_NAME_MAX if "{source_name}" in self.name_template else LONG_NAME_MAX
        if not 1 <= self.max_items <= 50 or not 30 <= self.name_max <= 200 or self.max_path < 100:
            raise ValueError("max_items must be 1-50, name_max 30-200, max_path >= 100")
        if _ILLEGAL.search(self.sep) or _ILLEGAL.search(self.empty):
            raise ValueError("multi_sep / empty placeholder may not contain <>:\"/\\|?* or control characters")
        t = self.name_template.strip()
        if "/" in t or "\\" in t:
            raise ValueError("name_template may not contain / or \\ (use folder_template for folders)")
        if t.endswith(".{ext}"):
            t = t[:-len(".{ext}")]
        if "{ext}" in t:
            raise ValueError("name_template: put {ext} only at the end, as .{ext}")
        self._name = parse_template(t, "name_template")
        if not any(isinstance(p, tuple) and p[0] in ("id", "md5") for p in self._name):
            self._name += ["_", ("id", None)]  # uniqueness guarantee
        self._id_first = isinstance(self._name[0], tuple) and self._name[0][0] in ("id", "md5")
        levels = [x for x in re.split(r"[/\\]", self.folder_template.strip()) if x.strip()]
        if len(levels) > 6:
            raise ValueError("folder_template: at most 6 folder levels")
        self._folders = [parse_template(x, "folder_template") for x in levels]
        self._uses_cats = any(isinstance(p, tuple) and p[0] in MULTI_FIELDS
                              for p in self._name + [q for lv in self._folders for q in lv])

    @property
    def uses_categories(self) -> bool:
        return self._uses_cats

    def describe(self) -> dict:
        return {"name_template": self.name_template, "folder_template": self.folder_template,
                "multi_director": self.multi_director, "max_items": self.max_items, "sep": self.sep,
                "rating_style": self.rating_style, "director_exclude": list(self.director_exclude)}

    # -- values ------------------------------------------------------------
    def directors(self, info: PostInfo) -> list[str]:
        ex = set(self.director_exclude)
        return [d for d in info.values("director") if d not in ex]

    def _join(self, vals: list[str], limit: int | None) -> str:
        n = limit or self.max_items
        s = self.sep.join(vals[:n])
        return s + (f"+{len(vals) - n}" if len(vals) > n else "")

    def _scalar(self, name: str, info: PostInfo) -> str:
        r = (info.rating or "").lower()[:1]
        date = re.match(r"(\d{4})-(\d{2})-(\d{2})", info.created_at or "")
        v = {
            "id": info.id, "md5": info.md5, "ext": info.ext, "score": info.score, "fav_count": info.fav_count,
            "width": info.width, "height": info.height,
            "rating": RATINGS.get(r) if self.rating_style == "word" else r,
            "rating_word": RATINGS.get(r), "rating_letter": r,
            "year": date and date.group(1), "month": date and date.group(2), "day": date and date.group(3),
            "date": date and date.group(0),
            "md5_ab": info.md5 and info.md5[0:2], "md5_cd": info.md5 and info.md5[2:4],
            "source_name": source_name(info.sources),   # None -> dropped; the name keeps {id}
        }[name]
        return "" if v is None else str(v)

    def _render(self, parts, info: PostInfo, folder: bool, director: list[str] | None,
                source_room: int | None = None) -> str:
        out = []
        for p in parts:
            if isinstance(p, str):
                out.append(p)
                continue
            name, lim = p
            if name in ("director", "artist"):
                val = self._join(self.directors(info) if director is None else director, lim)
            elif name in MULTI_FIELDS:
                val = self._join(info.values(name), lim)
            else:
                val = self._scalar(name, info)
                if name == "source_name":  # never a placeholder: missing -> "", name is then just the id
                    val = sanitize_component(val, 10_000, "") if val else ""
                    out.append(val if source_room is None else truncate(val, source_room).rstrip(" ._-"))
                    continue
            out.append(val or (f"_no_{name}" if folder else self.empty))
        return "".join(out)

    # -- paths -------------------------------------------------------------
    def file_name(self, info: PostInfo, room: int | None = None) -> str:
        ext = info.ext or "bin"
        room = min(room or self.name_max, self.name_max) - len(ext) - 1
        stem = sanitize_component(self._render(self._name, info, False, None).strip("_"), 10_000, str(info.id))
        if _u16(stem) > room and any(p == ("source_name", None) for p in self._name):
            # shorten the source name first so id and the other fields survive
            rest = _u16(sanitize_component(self._render(self._name, info, False, None, 0), 10_000, ""))
            stem = self._render(self._name, info, False, None, max(0, room - rest)).strip("_")
            stem = sanitize_component(stem, 10_000, str(info.id))
        if _u16(stem) > room:
            if self._id_first:
                stem = truncate(stem, room).rstrip(" ._")
            else:  # keep the id so the name stays unique after cutting
                tail = f"_{info.id}"
                stem = truncate(stem, max(1, room - len(tail))).rstrip(" ._") + tail
            stem = sanitize_component(stem, room, str(info.id))
        return f"{stem}.{ext}"

    def folders(self, info: PostInfo, director: list[str] | None, limit: int | None = None) -> list[str]:
        lim = min(limit or self.name_max, self.name_max)
        return [sanitize_component(self._render(lv, info, True, director), lim, "_unknown")
                for lv in self._folders]

    def plan(self, info: PostInfo, out_len: int = 0) -> list[str]:
        """Relative paths ('/'-separated, under files/) for a post: [primary, *extra copies].
        out_len = length of the absolute archive dir, used to keep full paths under max_path."""
        dirs = self.directors(info)
        if self.multi_director == "multi" or not dirs:
            groups = [None]
        elif self.multi_director == "first":
            groups = [dirs[:1]]
        else:  # copy / hardlink: one entry per director
            groups = [[d] for d in dirs]
        return [self._fit(info, g, out_len) for g in groups]

    def _fit(self, info: PostInfo, director, out_len: int) -> str:
        """Shorten (name first, then folder names) until the absolute path fits max_path."""
        for name_room, folder_room in ((None, None), (60, None), (40, 32), (24, 16), (24, 8)):
            parts = [FILES_DIR, *self.folders(info, director, folder_room), self.file_name(info, name_room)]
            rel = "/".join(parts)
            if out_len + 1 + _u16(rel) <= self.max_path:
                return rel
        return rel  # still long (very deep archive dir): long-path APIs handle it; logged by caller
