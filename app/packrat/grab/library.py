"""data/library.sqlite3: everything Browse / Downloads remember between sessions.

  md5s          the MD5 list: every file Packrat saved (md5 -> path), so a file is never downloaded twice,
                from any site (Grabber's md5s.txt / md5s.sqlite)
  saved         one row per saved file: site, post id, md5, path, when (history, "already downloaded")
  favorites     favorite searches: name (tags), priority, last viewed, image, post-filter, sites, monitor
  later         "kept for later" searches
  monitors      monitored searches: sites, interval, delay, last check, what was seen, notify / download
  history       searches you made (for the search box and the History menu)
  batch         download lists (groups = searches, uniques = single images) with their progress
  words         tags seen in results, for auto-complete
  state         small values (open tabs, window layout ...)

SQLite in WAL mode: the window and a download run in the background can use it at the same time.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from .. import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS md5s (md5 TEXT PRIMARY KEY, path TEXT NOT NULL, at REAL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS saved (site TEXT NOT NULL, post_id TEXT NOT NULL, md5 TEXT, path TEXT, at REAL,
                                  PRIMARY KEY (site, post_id, path)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS saved_md5 ON saved(md5);
CREATE TABLE IF NOT EXISTS favorites (name TEXT PRIMARY KEY, note INTEGER DEFAULT 50, last_viewed REAL,
    image TEXT, postfilter TEXT DEFAULT '', sites TEXT DEFAULT '[]', monitor INTEGER, added REAL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS later (name TEXT PRIMARY KEY, added REAL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS monitors (id INTEGER PRIMARY KEY, search TEXT NOT NULL, sites TEXT NOT NULL,
    interval_min INTEGER DEFAULT 1440, delay_min INTEGER DEFAULT 0, last_check REAL, next_check REAL,
    seen TEXT DEFAULT '{}', new_count INTEGER DEFAULT 0, total_new INTEGER DEFAULT 0, postfilter TEXT DEFAULT '',
    notify INTEGER DEFAULT 1, download INTEGER DEFAULT 1, path TEXT DEFAULT '', filename TEXT DEFAULT '',
    blacklisted INTEGER DEFAULT 0, active INTEGER DEFAULT 1, last_error TEXT DEFAULT '', added REAL);
CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY, search TEXT, sites TEXT, at REAL);
CREATE TABLE IF NOT EXISTS batch (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, data TEXT NOT NULL,
    status TEXT DEFAULT 'pending', progress TEXT DEFAULT '{}', added REAL, position INTEGER);
CREATE TABLE IF NOT EXISTS words (word TEXT PRIMARY KEY, uses INTEGER DEFAULT 0) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT) WITHOUT ROWID;
"""


class Library:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else paths.data_dir() / "library.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), timeout=60, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        with self._lock:
            self.db.executescript(SCHEMA)
            self.db.commit()

    def close(self):
        with self._lock:
            try:
                self.db.close()
            except sqlite3.Error:
                pass

    def _x(self, sql, args=(), commit=True):
        with self._lock:
            cur = self.db.execute(sql, args)
            if commit:
                self.db.commit()
            return cur

    def _q(self, sql, args=()):
        with self._lock:
            return self.db.execute(sql, args).fetchall()

    # ---- MD5 list -----------------------------------------------------------------------------
    def md5_path(self, md5: str, keep_deleted: bool = False) -> str | None:
        """Where a file with this MD5 was saved; None if unknown. A path whose file is gone is forgotten
        (unless 'Remember files you deleted' is on, then '' is returned: known but not on disk)."""
        if not md5:
            return None
        rows = self._q("SELECT path FROM md5s WHERE md5=?", (md5.lower(),))
        if not rows:
            return None
        p = rows[0][0]
        if p and Path(p).exists():
            return p
        if keep_deleted:
            return ""
        self._x("DELETE FROM md5s WHERE md5=?", (md5.lower(),))
        return None

    def md5_add(self, md5: str, path: str) -> None:
        if md5:
            self._x("INSERT INTO md5s(md5, path, at) VALUES(?,?,?) ON CONFLICT(md5) DO UPDATE SET path=excluded.path, "
                    "at=excluded.at", (md5.lower(), str(path), time.time()))

    def md5_set_path(self, md5: str, path: str) -> None:
        self.md5_add(md5, path)

    def md5_remove(self, md5: str) -> None:
        self._x("DELETE FROM md5s WHERE md5=?", (md5.lower(),))

    def md5_count(self) -> int:
        return self._q("SELECT COUNT(*) FROM md5s")[0][0]

    def md5_import(self, pairs) -> int:
        """[(md5, path)] e.g. from Grabber's md5s.txt (32 hex chars + path per line)."""
        n = 0
        with self._lock:
            for md5, p in pairs:
                if len(md5) == 32:
                    self.db.execute("INSERT OR IGNORE INTO md5s(md5, path, at) VALUES(?,?,?)", (md5.lower(), p, time.time()))
                    n += 1
            self.db.commit()
        return n

    def record_saved(self, site: str, post_id, md5: str, path: str) -> None:
        self._x("INSERT OR REPLACE INTO saved(site, post_id, md5, path, at) VALUES(?,?,?,?,?)",
                (site, str(post_id), (md5 or "").lower(), str(path), time.time()))

    def was_saved(self, site: str, post_id) -> list[str]:
        return [r[0] for r in self._q("SELECT path FROM saved WHERE site=? AND post_id=?", (site, str(post_id)))]

    # ---- favorites ----------------------------------------------------------------------------
    def favorites(self) -> list[dict]:
        out = []
        for name, note, lv, image, pf, sites, mon, added in self._q(
                "SELECT name, note, last_viewed, image, postfilter, sites, monitor, added FROM favorites"):
            out.append({"name": name, "note": note or 0, "last_viewed": lv, "image": image or "", "postfilter": pf or "",
                        "sites": _jl(sites, []), "monitor": mon, "added": added})
        return out

    def favorite(self, name: str) -> dict | None:
        return next((f for f in self.favorites() if f["name"] == name), None)

    def add_favorite(self, name: str, sites=None, note: int = 50) -> None:
        name = " ".join(name.split())
        if not name:
            return
        self._x("INSERT OR IGNORE INTO favorites(name, note, last_viewed, sites, added) VALUES(?,?,?,?,?)",
                (name, note, time.time(), json.dumps(list(sites or [])), time.time()))

    def update_favorite(self, name: str, **kw) -> None:
        if not kw:
            return
        if "sites" in kw:
            kw["sites"] = json.dumps(list(kw["sites"] or []))
        new_name = kw.pop("new_name", None)
        sets = ", ".join(f"{k}=?" for k in kw)
        if sets:
            self._x(f"UPDATE favorites SET {sets} WHERE name=?", (*kw.values(), name))
        if new_name and new_name != name:
            self._x("UPDATE favorites SET name=? WHERE name=?", (new_name, name))

    def remove_favorite(self, name: str) -> None:
        self._x("DELETE FROM favorites WHERE name=?", (name,))

    def is_favorite(self, name: str) -> bool:
        return bool(self._q("SELECT 1 FROM favorites WHERE name=?", (name,)))

    # ---- kept for later -----------------------------------------------------------------------
    def later(self) -> list[str]:
        return [r[0] for r in self._q("SELECT name FROM later ORDER BY added")]

    def add_later(self, name: str) -> None:
        name = " ".join(name.split())
        if name:
            self._x("INSERT OR IGNORE INTO later(name, added) VALUES(?,?)", (name, time.time()))

    def remove_later(self, name: str) -> None:
        self._x("DELETE FROM later WHERE name=?", (name,))

    # ---- monitors -----------------------------------------------------------------------------
    _MON_COLS = ("id", "search", "sites", "interval_min", "delay_min", "last_check", "next_check", "seen", "new_count",
                 "total_new", "postfilter", "notify", "download", "path", "filename", "blacklisted", "active",
                 "last_error", "added")

    def monitors(self) -> list[dict]:
        out = []
        for row in self._q(f"SELECT {', '.join(self._MON_COLS)} FROM monitors ORDER BY id"):
            d = dict(zip(self._MON_COLS, row))
            d["sites"] = _jl(d["sites"], [])
            d["seen"] = _jl(d["seen"], {})
            for b in ("notify", "download", "blacklisted", "active"):
                d[b] = bool(d[b])
            out.append(d)
        return out

    def monitor(self, mid: int) -> dict | None:
        return next((m for m in self.monitors() if m["id"] == mid), None)

    def add_monitor(self, search: str, sites, interval_min=1440, delay_min=0, notify=True, download=True,
                    path="", filename="", postfilter="", blacklisted=False, seen=None) -> int:
        cur = self._x("INSERT INTO monitors(search, sites, interval_min, delay_min, last_check, next_check, seen, "
                      "notify, download, path, filename, postfilter, blacklisted, added) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (search, json.dumps(list(sites)), int(interval_min), int(delay_min), time.time(),
                       time.time() + int(interval_min) * 60, json.dumps(seen or {}), int(notify), int(download),
                       path, filename, postfilter, int(blacklisted), time.time()))
        return cur.lastrowid

    def update_monitor(self, mid: int, **kw) -> None:
        if not kw:
            return
        for k in ("sites", "seen"):
            if k in kw:
                kw[k] = json.dumps(kw[k])
        for k in ("notify", "download", "blacklisted", "active"):
            if k in kw:
                kw[k] = int(bool(kw[k]))
        sets = ", ".join(f"{k}=?" for k in kw)
        self._x(f"UPDATE monitors SET {sets} WHERE id=?", (*kw.values(), mid))

    def remove_monitor(self, mid: int) -> None:
        self._x("DELETE FROM monitors WHERE id=?", (mid,))

    def due_monitors(self, now: float | None = None) -> list[dict]:
        now = now or time.time()
        return [m for m in self.monitors() if m["active"] and (m["next_check"] or 0) <= now]

    # ---- history ------------------------------------------------------------------------------
    def add_history(self, search: str, sites) -> None:
        self._x("INSERT INTO history(search, sites, at) VALUES(?,?,?)", (search, json.dumps(list(sites)), time.time()))
        self._x("DELETE FROM history WHERE id NOT IN (SELECT id FROM history ORDER BY id DESC LIMIT 500)")

    def history(self, limit: int = 100) -> list[dict]:
        return [{"search": s, "sites": _jl(si, []), "at": at} for s, si, at in
                self._q("SELECT search, sites, at FROM history ORDER BY id DESC LIMIT ?", (limit,))]

    def clear_history(self) -> None:
        self._x("DELETE FROM history")

    # ---- words (auto-complete) ----------------------------------------------------------------
    def add_words(self, words) -> None:
        with self._lock:
            self.db.executemany("INSERT INTO words(word, uses) VALUES(?, 1) ON CONFLICT(word) DO UPDATE SET uses=uses+1",
                                [(w,) for w in words if w])
            self.db.commit()

    def complete(self, prefix: str, min_uses: int = 1, limit: int = 20) -> list[str]:
        if not prefix:
            return []
        esc = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return [r[0] for r in self._q("SELECT word FROM words WHERE word LIKE ? ESCAPE '\\' AND uses >= ? "
                                      "ORDER BY uses DESC LIMIT ?", (esc + "%", min_uses, limit))]

    # ---- download lists -----------------------------------------------------------------------
    def batch_add(self, kind: str, data: dict) -> int:
        pos = (self._q("SELECT COALESCE(MAX(position), 0) FROM batch")[0][0] or 0) + 1
        return self._x("INSERT INTO batch(kind, data, status, added, position) VALUES(?,?,?,?,?)",
                       (kind, json.dumps(data, default=str), "pending", time.time(), pos)).lastrowid

    def batch_items(self, ids=None) -> list[dict]:
        q = "SELECT id, kind, data, status, progress, added FROM batch"
        rows = self._q(q + " ORDER BY position, id") if ids is None else self._q(
            q + f" WHERE id IN ({','.join('?' * len(ids))}) ORDER BY position, id", list(ids))
        return [{"id": i, "kind": k, "data": _jl(d, {}), "status": s, "progress": _jl(p, {}), "added": a}
                for i, k, d, s, p, a in rows]

    def batch_update(self, bid: int, status: str | None = None, progress: dict | None = None,
                     data: dict | None = None) -> None:
        sets, args = [], []
        if status is not None:
            sets.append("status=?")
            args.append(status)
        if progress is not None:
            sets.append("progress=?")
            args.append(json.dumps(progress, default=str))
        if data is not None:
            sets.append("data=?")
            args.append(json.dumps(data, default=str))
        if sets:
            self._x(f"UPDATE batch SET {', '.join(sets)} WHERE id=?", (*args, bid))

    def batch_remove(self, ids) -> None:
        ids = list(ids)
        if ids:
            self._x(f"DELETE FROM batch WHERE id IN ({','.join('?' * len(ids))})", ids)

    def batch_clear(self, only_done: bool = False) -> None:
        self._x("DELETE FROM batch" + (" WHERE status='done'" if only_done else ""))

    def batch_move(self, bid: int, delta: int) -> None:
        items = [r[0] for r in self._q("SELECT id FROM batch ORDER BY position, id")]
        if bid not in items:
            return
        i = items.index(bid)
        j = max(0, min(len(items) - 1, i + delta))
        items.insert(j, items.pop(i))
        with self._lock:
            for pos, x in enumerate(items, 1):
                self.db.execute("UPDATE batch SET position=? WHERE id=?", (pos, x))
            self.db.commit()

    # ---- state --------------------------------------------------------------------------------
    def get_state(self, key: str, default=None):
        rows = self._q("SELECT value FROM state WHERE key=?", (key,))
        if not rows:
            return default
        try:
            v = json.loads(rows[0][0])
        except (TypeError, ValueError):
            return default
        return default if v is None else v

    def set_state(self, key: str, value) -> None:
        self._x("INSERT INTO state(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, default=str)))

    # ---- backup -------------------------------------------------------------------------------
    def backup_to(self, dest: Path) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        con = sqlite3.connect(str(tmp))
        try:
            with self._lock:
                self.db.backup(con)
        finally:
            con.close()
        os.replace(tmp, dest)
        return dest


def _jl(s, default):
    try:
        v = json.loads(s) if s else default
        return v if isinstance(v, type(default)) else default
    except (TypeError, ValueError):
        return default


_lib: Library | None = None
_lib_lock = threading.Lock()


def shared() -> Library:
    global _lib
    with _lib_lock:
        if _lib is None:
            _lib = Library()
        return _lib
