"""Tags, tag types and the local tag database (Grabber's tag loader).

Type names follow Grabber (artist, copyright, character, species, meta, lore, contributor, model,
photo_set, circle, general, ...), so filename tokens like %artist% work on every site. Site words are
mapped onto them like Grabber's Tag::GetType does, plus e6ai's director / franchise.
"""
from __future__ import annotations

import html
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .. import paths

TYPE_ALIASES = {
    "idol": "model", "series": "copyright", "mangaka": "artist", "origin": "artist", "game": "copyright",
    "studio": "circle", "source": "general", "genre": "general", "character group": "general",
    "oc": "character", "medium": "meta", "metadata": "meta",
    # Packrat additions
    "director": "artist", "franchise": "copyright", "artists": "artist", "characters": "character",
    "copyrights": "copyright", "tag": "general", "tags": "general", "faults": "meta", "invalid": "invalid",
    "fandom": "copyright", "rating": "meta",
}
KNOWN_TYPES = ("artist", "copyright", "character", "species", "model", "photo_set", "circle", "meta", "lore",
               "contributor", "invalid", "general")
_PREFIX_TYPES = {0: "artist", 1: "copyright", 2: "character", 3: "model", 4: "species", 5: "meta",
                 6: "unknown", 7: "oc"}
DEFAULT_TYPE_IDS = {0: "general", 1: "artist", 3: "copyright", 4: "character", 5: "model", 6: "photo_set"}


def normalize_type(t, ids: dict | None = None) -> str:
    """'' for unknown; otherwise a lowercase Grabber type name."""
    if t is None:
        return ""
    if isinstance(t, (int, float)) and not isinstance(t, bool):
        n = int(t)
        return normalize_type((ids or DEFAULT_TYPE_IDS).get(n, ""), None) if (ids or DEFAULT_TYPE_IDS).get(n) else ""
    s = str(t).strip().lower()
    if not s or s == "unknown":
        return ""
    if ", " in s:
        s = s.split(", ")[0].strip()
    if s.isdigit() and ids is not None:
        return normalize_type(ids.get(int(s), ""), None)
    if " " in s and s not in TYPE_ALIASES:
        s = s.split(" ")[0]
    return TYPE_ALIASES.get(s, s)


@dataclass
class Tag:
    name: str
    type: str = ""
    count: int = 0
    id: int = 0
    related: list = field(default_factory=list)

    def __post_init__(self):
        self.name = html.unescape(str(self.name)).replace(" ", "_")
        if not self.type:
            if self.name.endswith("(artist)"):
                self.type = "artist"
            elif ":" in self.name:
                pre, rest = self.name.split(":", 1)
                pre_t = normalize_type(pre)
                if pre_t in _PREFIX_TYPES.values() and rest:
                    self.type, self.name = normalize_type(pre_t), rest

    @property
    def text(self) -> str:
        return self.name

    def to_json(self) -> dict:
        d = {"name": self.name}
        if self.type:
            d["type"] = self.type
        if self.count:
            d["count"] = self.count
        if self.id:
            d["id"] = self.id
        return d

    @classmethod
    def from_json(cls, d) -> "Tag":
        if isinstance(d, str):
            return cls(d)
        return cls(d.get("name", ""), d.get("type", ""), int(d.get("count") or 0), int(d.get("id") or 0))


def make_tags(raw, type_ids: dict | None = None) -> list[Tag]:
    """Tags returned by a model (strings, or {id, name, count, type, typeId, related})."""
    out: list[Tag] = []
    if isinstance(raw, str):
        raw = raw.split()
    for t in raw or []:
        if isinstance(t, str):
            if t.strip():
                out.append(Tag(t))
            continue
        if not isinstance(t, dict) or not t.get("name"):
            continue
        typ = ""
        if t.get("type") not in (None, ""):
            v = t["type"]
            typ = normalize_type(v, type_ids) if not isinstance(v, (int, float)) else normalize_type(int(v), type_ids or {})
        if not typ and t.get("typeId") not in (None, ""):
            try:
                typ = normalize_type((type_ids or {}).get(int(t["typeId"]), ""))
            except (TypeError, ValueError):
                typ = ""
        rel = t.get("related") or []
        if isinstance(rel, str):
            rel = rel.split()
        try:
            cnt = int(t.get("count") or 0)
        except (TypeError, ValueError):
            cnt = 0
        try:
            tid = int(t.get("id") or 0)
        except (TypeError, ValueError):
            tid = 0
        out.append(Tag(str(t["name"]), typ, cnt, tid, list(rel)))
    return out


def split_tags_string(val: str) -> list[Tag]:
    """Grabber's ImageFactory::parseTags: a flat tag string (spaces, or ', ' lists); 'user:', 'score:',
    'rating:' prefixes become tokens and are returned separately."""
    raw = re.sub(r"[\r\n\t]+", " ", val or "")
    commas = raw.count(", ")
    spaces = raw.count(" ")
    parts = (raw.split(", ") if commas >= 10 or (commas > 0 and (spaces - commas) // commas < 2)
             else raw.split(" "))
    return [Tag(p.replace("&amp;", "&")) for p in parts if p]


def load_type_file(path: Path) -> dict[int, str]:
    """tag-types.txt: 'id,name' lines."""
    out = {}
    try:
        for line in Path(path).read_text("utf-8").splitlines():
            if "," in line:
                a, b = line.split(",", 1)
                if a.strip().lstrip("-").isdigit():
                    out[int(a)] = b.strip().lower()
    except OSError:
        pass
    return out


def save_type_file(path: Path, types: dict[int, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{k},{v}\n" for k, v in sorted(types.items())), "utf-8")


class TagDatabase:
    """Per-site SQLite list of tag name -> (type, count): filled by the tag loader and by searches."""

    def __init__(self, host: str):
        self.path = paths.data_dir() / "tags" / (re.sub(r"[^A-Za-z0-9._-]+", "_", host) + ".sqlite3")
        self._lock = threading.Lock()
        self._con = None

    def _db(self):
        if self._con is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._con = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
            self._con.execute("PRAGMA journal_mode=WAL")
            self._con.execute("CREATE TABLE IF NOT EXISTS tags (name TEXT PRIMARY KEY, type TEXT, count INTEGER, "
                              "id INTEGER) WITHOUT ROWID")
        return self._con

    def exists(self) -> bool:
        return self.path.is_file()

    def count(self) -> int:
        with self._lock:
            return self._db().execute("SELECT COUNT(*) FROM tags").fetchone()[0]

    def types_for(self, names: list[str]) -> dict[str, str]:
        if not names or not self.path.is_file():
            return {}
        out = {}
        with self._lock:
            db = self._db()
            for k in range(0, len(names), 500):
                part = names[k:k + 500]
                for n, t in db.execute(f"SELECT name, type FROM tags WHERE name IN ({','.join('?' * len(part))}) "
                                       "AND type != ''", part):
                    out[n] = t
        return out

    def add(self, tags: list[Tag], only_typed: bool = True) -> int:
        rows = [(t.name, t.type, t.count, t.id) for t in tags if t.name and (t.type or not only_typed)]
        if not rows:
            return 0
        with self._lock:
            db = self._db()
            db.executemany("INSERT INTO tags(name, type, count, id) VALUES(?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
                           "type=CASE WHEN excluded.type != '' THEN excluded.type ELSE tags.type END, "
                           "count=CASE WHEN excluded.count > 0 THEN excluded.count ELSE tags.count END, "
                           "id=CASE WHEN excluded.id > 0 THEN excluded.id ELSE tags.id END", rows)
            db.commit()
        return len(rows)

    def complete(self, prefix: str, limit: int = 20) -> list[tuple[str, str, int]]:
        if not self.path.is_file() or not prefix:
            return []
        with self._lock:
            esc = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            return self._db().execute("SELECT name, type, count FROM tags WHERE name LIKE ? ESCAPE '\\' "
                                      "ORDER BY count DESC LIMIT ?", (esc + "%", limit)).fetchall()

    def close(self):
        with self._lock:
            if self._con is not None:
                self._con.close()
                self._con = None
