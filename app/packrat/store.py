"""Local storage: SQLite (queryable + checkpoint state) and append-only JSONL journals.

posts.jsonl  every API post object as fetched (append-only; the last line per id wins)
files.jsonl  one line per verified file: {"id", "md5", "path", "extra"} (where it was saved)
Both are fsynced before SQLite commits, so a damaged database can be rebuilt from them.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path

from . import __version__, siteprofile
from .naming import PostInfo, categorize_api_tags
from .platform_utils import long_path, safe_ext, safe_replace

log = logging.getLogger("packrat")
SCHEMA_VERSION = 4
DB_NAME = "e6ai.sqlite3"   # e6ai archives (1.x/2.0 and 3.0); other sites: siteprofile.SiteProfile.db_name
SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY,
    created_at TEXT, updated_at TEXT,
    md5 TEXT, ext TEXT, size INTEGER, width INTEGER, height INTEGER,
    file_url TEXT, rating TEXT, score INTEGER, fav_count INTEGER,
    is_deleted INTEGER, change_seq INTEGER,
    tags TEXT,                 -- space-separated, all categories
    file_status TEXT NOT NULL, -- pending | ok | no_url | deleted | missing | md5_mismatch | failed
    file_path TEXT,            -- relative to the archive dir; fixed once the file is saved
    fetched_at REAL,
    json TEXT NOT NULL,        -- raw post object (API) or CSV row (export, "_source":"db_export")
    tag_cats TEXT              -- JSON {"director": [...], "species": [...], ...}
);
CREATE INDEX IF NOT EXISTS posts_md5 ON posts(md5);
CREATE INDEX IF NOT EXISTS posts_status ON posts(file_status, id);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS meta_items (
    kind TEXT, id INTEGER, json TEXT NOT NULL, fetched_at REAL,
    PRIMARY KEY (kind, id)
);
CREATE TABLE IF NOT EXISTS tag_categories (name TEXT PRIMARY KEY, category INTEGER) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS extra_files (post_id INTEGER, path TEXT, PRIMARY KEY (post_id, path)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS verified (post_id INTEGER PRIMARY KEY, md5 TEXT, at REAL);
-- 1.7 last download error per file (for the completeness report); cleared when the file is saved
CREATE TABLE IF NOT EXISTS file_errors (post_id INTEGER PRIMARY KEY, reason TEXT NOT NULL, detail TEXT, at REAL,
    attempts INTEGER NOT NULL DEFAULT 1);
-- 1.3 priority queue: rank is the rowid, so walking it in order is a plain range scan
CREATE TABLE IF NOT EXISTS queue (rank INTEGER PRIMARY KEY, post_id INTEGER NOT NULL UNIQUE, cum_bytes INTEGER);
-- 1.3 pools.csv.gz: pool membership (page order) for every post
CREATE TABLE IF NOT EXISTS pool_posts (post_id INTEGER, pool_id INTEGER, position INTEGER,
                                       PRIMARY KEY (post_id, pool_id)) WITHOUT ROWID;
"""


class SiteMismatchError(ValueError):
    """3.0: the archive folder belongs to another site (one archive folder = one site)."""


class DatabaseTooNewError(RuntimeError):
    """The archive database was written by a newer Packrat / e6ai-archiver (higher schema version)."""

    def __init__(self, path, found: int, written_by):
        self.found, self.written_by = found, written_by
        super().__init__(
            f"The archive database {path} was made by a newer Packrat"
            f" ({written_by or 'unknown version'}, database format {found}); this is version {__version__}"
            f" (format {SCHEMA_VERSION}). Nothing was changed. Use that newer version (or later) for this "
            f"archive folder.")


def refuse_if_newer(db_path: Path) -> None:
    """Raise DatabaseTooNewError if db_path exists and was written by a newer schema (read-only check)."""
    if not (db_path.is_file() and db_path.stat().st_size > 0):
        return
    try:
        con = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error:
        return  # unreadable here: the normal open reports it (or recovers a damaged file)
    try:
        v, app = _db_version(con)
    except sqlite3.Error:
        return
    finally:
        con.close()
    if v is not None and v > SCHEMA_VERSION:
        raise DatabaseTooNewError(db_path, v, app)


def _schema_statements():
    buf = ""
    for line in SCHEMA.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            yield buf.strip()
            buf = ""


def _db_version(db) -> tuple[int | None, str | None]:
    """(schema_version, app version that last opened it) of an existing database; (1, None) if it has
    no state table / no version (1.0)."""
    try:
        rows = dict(db.execute("SELECT key, value FROM state WHERE key IN ('schema_version','app_version')"))
    except sqlite3.OperationalError:
        return 1, None
    try:
        v = int(json.loads(rows.get("schema_version", "1")))
    except (TypeError, ValueError):
        v = 1
    try:
        app = json.loads(rows["app_version"]) if "app_version" in rows else None
    except ValueError:
        app = None
    return v, (str(app) if app else None)


def _pending_changes(db, found_v) -> list[str]:
    """Human-readable list of what opening this database will change (empty = nothing structural)."""
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','index')")}
    out = []
    if (found_v or 1) < SCHEMA_VERSION:
        out.append(f"database format {found_v} -> {SCHEMA_VERSION}")
    for stmt in _schema_statements():
        words = stmt.split()
        name = words[5] if len(words) > 5 and words[2:5] == ["IF", "NOT", "EXISTS"] else None
        if name and name.split("(")[0] not in tables:
            out.append(f"add {words[1].lower()} {name.split('(')[0]}")

    def cols(t):
        return {r[1] for r in db.execute(f"PRAGMA table_info({t})")}
    if "posts" in tables and "parent_id" not in cols("posts"):
        out.append("add column posts.parent_id")
    if "file_errors" in tables and "attempts" not in cols("file_errors"):
        out.append("add column file_errors.attempts")
    return out


RETRYABLE_STATUSES = ("missing", "md5_mismatch", "failed")
META_KINDS = ("pools", "tags", "wiki_pages", "artists")
INFO_COLS = "id, md5, ext, rating, score, fav_count, width, height, created_at, tags, tag_cats, json"

# file_path is never overwritten by a metadata refresh: once saved, a file stays where it is
# (a later tag edit must not silently rename files; `reorganize` moves them on request).
_UPSERT_SQL = """INSERT INTO posts(id,created_at,updated_at,md5,ext,size,width,height,file_url,rating,
       score,fav_count,is_deleted,change_seq,tags,file_status,file_path,fetched_at,json,tag_cats)
   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?)
   ON CONFLICT(id) DO UPDATE SET created_at=excluded.created_at, updated_at=excluded.updated_at,
     md5=excluded.md5, ext=excluded.ext, size=excluded.size, width=excluded.width,
     height=excluded.height, file_url=excluded.file_url, rating=excluded.rating,
     score=excluded.score, fav_count=excluded.fav_count, is_deleted=excluded.is_deleted,
     change_seq=excluded.change_seq, tags=excluded.tags, file_status=excluded.file_status,
     fetched_at=excluded.fetched_at, json=excluded.json, tag_cats=excluded.tag_cats"""


def all_tags(post: dict) -> str:
    tags = post.get("tags")
    if isinstance(tags, str):
        return tags
    if isinstance(tags, list):
        return " ".join(sorted({t for t in tags if isinstance(t, str)}))
    out: set[str] = set()
    for v in (tags or {}).values() if isinstance(tags, dict) else ():
        if isinstance(v, list):
            out.update(t for t in v if isinstance(t, str))
    return " ".join(sorted(out))


def legacy_relpath(md5: str, ext: str) -> str:
    """Where 1.0/1.1 saved files (still recognised: they are moved into the new layout)."""
    return f"data/{md5[0:2]}/{md5[2:4]}/{md5}.{ext}"


def abspath(root: Path, relpath: str) -> Path:
    """OS path for a stored relative path (pathlib joins parts, so Windows gets backslashes)."""
    return long_path(Path(root)).joinpath(*relpath.split("/"))


def _is_md5(s) -> bool:
    return isinstance(s, str) and len(s) == 32 and all(c in "0123456789abcdef" for c in s)


def _int(v):
    if isinstance(v, bool):
        return int(v)
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _is_corrupt(e: BaseException) -> bool:
    m = str(e).lower()
    return isinstance(e, sqlite3.DatabaseError) and any(
        k in m for k in ("malformed", "not a database", "corrupt", "disk image"))


def fix_jsonl_tail(path: Path) -> None:
    """After a power loss the last JSONL line may be cut off; end it so new lines stay parseable."""
    try:
        with open(path, "rb+") as f:
            f.seek(0, os.SEEK_END)
            if f.tell() == 0:
                return
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                f.write(b"\n")
                log.warning("%s did not end with a newline (interrupted write?); repaired", path.name)
    except FileNotFoundError:
        pass


def iter_jsonl(path: Path):
    """Yield dicts from a JSONL file, skipping damaged lines."""
    bad = 0
    try:
        f = open(path, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return
    with f:
        for line in f:
            try:
                obj = json.loads(line)
            except ValueError:
                bad += line.strip() != ""
                continue
            if isinstance(obj, dict):
                yield obj
    if bad:
        log.warning("%s: skipped %d damaged lines", path.name, bad)


class Store:
    def __init__(self, out_dir: str | os.PathLike, site: str | None = None):
        self.out = Path(os.path.abspath(out_dir))
        self.out.mkdir(parents=True, exist_ok=True)
        # 3.0: one archive folder holds one site's archive (e6ai.sqlite3 / e621.sqlite3)
        self.site = siteprofile.get(site) if site else siteprofile.current()
        found, other = siteprofile.find_db(self.out)
        if found is not None and other.key != self.site.key:
            raise SiteMismatchError(
                f"The archive folder {self.out} holds the archive of {other.label} ({found.name}), but the chosen "
                f"site is {self.site.label}. Pick another archive folder for {self.site.label} (one folder per "
                f"site), or switch the site back to {other.label}.")
        self.db_name = self.site.db_name
        self.db_path = self.out / self.db_name
        refuse_if_newer(self.db_path)  # 2.0: before anything in the folder is touched
        for name in ("posts.jsonl", "files.jsonl", *(f"{k}.jsonl" for k in META_KINDS)):
            fix_jsonl_tail(self.out / name)
        self.jsonl = open(self.out / "posts.jsonl", "a", encoding="utf-8", newline="\n")
        self.files_jsonl = open(self.out / "files.jsonl", "a", encoding="utf-8", newline="\n")
        self._jsonl_dirty = False
        self.meta_jsonl: dict[str, object] = {}
        self._last_commit = time.monotonic()
        self._uncommitted = 0
        self.recovered = None
        try:
            try:
                self._open()
            except sqlite3.DatabaseError as e:
                if not _is_corrupt(e):
                    raise
                self.recover(str(e))
        except BaseException:
            # 2.0: a refused / rolled-back upgrade must not leave the journals (or the DB) open
            for f in (self.jsonl, self.files_jsonl):
                try:
                    f.close()
                except OSError:
                    pass
            db = getattr(self, "db", None)
            if db is not None:
                try:
                    db.close()
                except sqlite3.Error:
                    pass
            raise

    def _open(self) -> None:
        # timeout: wait (not fail) if the DB is briefly locked, e.g. by a DB browser or backup tool
        existed = self.db_path.is_file() and self.db_path.stat().st_size > 0
        self.db = sqlite3.connect(str(self.db_path), timeout=60)
        try:
            self.db.execute("PRAGMA journal_mode=WAL")      # readers never block the writer
            self.db.execute("PRAGMA synchronous=NORMAL")    # durable at checkpoints, fast commits in WAL
            self.db.execute("PRAGMA temp_store=MEMORY")
            # 2.0: a database from an older version is upgraded in place, in one transaction, after a
            # backup copy; one from a NEWER version is refused untouched (it may hold things we'd lose).
            found_v, written_by = _db_version(self.db) if existed else (None, None)
            if found_v is not None and found_v > SCHEMA_VERSION:
                raise DatabaseTooNewError(self.db_path, found_v, written_by)
            plan = _pending_changes(self.db, found_v) if existed else []
            if existed and self.get_state_raw("open") == "true":  # last run didn't close cleanly
                log.info("previous run did not shut down cleanly; checking database integrity")
                res = self.db.execute("PRAGMA quick_check").fetchone()[0]
                if res != "ok":
                    raise sqlite3.DatabaseError(f"database disk image is malformed (quick_check: {res})")
            self.backup_path = None
            if existed and (plan or written_by != __version__):
                self.backup_path = self._backup_before_upgrade(written_by, required=bool(plan))
            if plan:
                log.info("upgrading the database from %s to %s: %s", written_by or "an older version",
                         __version__, "; ".join(plan))
            self.db.execute("BEGIN IMMEDIATE")
            try:
                for stmt in _schema_statements():
                    self.db.execute(stmt)
                self._migrate()
                if existed and written_by != __version__:
                    hist = self.get_state("upgrade_history", []) or []
                    hist.append({"from": written_by or "<=1.9.1", "to": __version__, "at": time.time(),
                                 "changes": plan, "backup": str(self.backup_path) if self.backup_path else None})
                    self.set_state("upgrade_history", hist[-20:])
                self.set_state("app_version", __version__)
                self.set_state("open", True)
                self.db.commit()
            except BaseException:
                self.db.rollback()  # all or nothing: the old database stays exactly as it was
                raise
        except BaseException:
            self.db.close()
            raise

    def get_state_raw(self, key: str):
        try:
            row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        except sqlite3.OperationalError:  # no state table yet
            return None
        return row[0] if row else None

    def _backup_before_upgrade(self, written_by, required: bool):
        """Copy the database (consistent, WAL included) to <out>/db-backups/ before it is changed.
        required=True (schema changes pending): no backup -> no upgrade (RuntimeError, nothing touched)."""
        ts = time.strftime("%Y%m%d-%H%M%S")
        dest_dir = self.out / "db-backups"
        dest = dest_dir / f"{self.db_name}.before-{__version__}-from-{written_by or 'pre-2.0'}-{ts}"
        size = sum((self.out / (self.db_name + x)).stat().st_size for x in ("", "-wal")
                   if (self.out / (self.db_name + x)).exists())
        log.info("backing up the database (%.0f MiB) before opening it with version %s ...", size / 2**20,
                 __version__)
        try:
            dest_dir.mkdir(exist_ok=True)
            from .platform_utils import free_bytes
            if free_bytes(dest_dir) < size + 64 * 1024 * 1024:
                raise OSError(f"not enough free space for a {size / 2**20:.0f} MiB copy")
            tmp = dest.with_name(dest.name + ".tmp")
            dst = sqlite3.connect(str(tmp))
            try:
                self.db.backup(dst)
            finally:
                dst.close()
            safe_replace(tmp, dest)
        except (OSError, sqlite3.Error) as e:
            if required:
                raise RuntimeError(f"could not back up the database before upgrading it ({e}); nothing was "
                                   f"changed. Free some space on the archive drive and start again.") from None
            log.warning("could not back up the database (%s); continuing (no schema change is needed)", e)
            return None
        log.info("database backed up before the upgrade to %s: %s", __version__, dest)
        return dest

    # --- corruption recovery -------------------------------------------
    def recover(self, reason: str) -> None:
        """Back up the damaged DB (never deleted) and rebuild it from the JSONL journals."""
        try:
            self.db.close()
        except Exception:
            pass
        ts = time.strftime("%Y%m%d-%H%M%S")
        log.error("database is damaged (%s). Backing it up as %s.corrupt-%s and rebuilding from the "
                  "JSONL journals; no data files are touched.", reason, self.db_name, ts)
        for suffix in ("", "-wal", "-shm"):
            p = self.out / (self.db_name + suffix)
            if p.exists():
                safe_replace(p, self.out / f"{self.db_name}{suffix}.corrupt-{ts}")
        self._open()
        self.recovered = self.rebuild_from_journals()
        log.warning("database rebuilt: %s", self.recovered)

    def rebuild_from_journals(self) -> dict:
        res = {"posts": 0, "files": 0, "meta": 0, "seeded": 0}
        ids, merges = [], 0
        for post in iter_jsonl(self.out / "posts.jsonl"):
            if post.get("_merge"):
                merges += 1  # replayed after the export seed below, with the same merge rules
                continue
            if isinstance(post.get("id"), int) and not isinstance(post.get("id"), bool):
                self.upsert_post(post, journal=False)
                ids.append(post["id"])
                res["posts"] += 1
                if res["posts"] % 5000 == 0:
                    self.db.commit()
        for kind in META_KINDS:
            for item in iter_jsonl(self.out / f"{kind}.jsonl"):
                if isinstance(item.get("id"), int):
                    self.upsert_meta(kind, item, journal=False)
                    res["meta"] += 1
        exp = self.out / "db_export" / "posts.csv.gz"
        if exp.exists():
            from .exports import load_tags_export, seed_from_posts_export
            tags = self.out / "db_export" / "tags.csv.gz"
            if tags.exists():
                load_tags_export(self, tags)
            res["seeded"] = seed_from_posts_export(self, exp)["inserted"]
        if merges:
            for post in iter_jsonl(self.out / "posts.jsonl"):
                if post.pop("_merge", False) and isinstance(post.get("id"), int):
                    self.merge_api_json(post, journal=False)
                    res["posts"] += 1
        for rec in iter_jsonl(self.out / "files.jsonl"):
            # status 'pending': the next download pass re-hashes the file (no request) before trusting it
            cur = self.db.execute("UPDATE posts SET file_path=?, file_status='pending' WHERE id=? AND md5=?",
                                  (rec.get("path"), rec.get("id"), rec.get("md5")))
            for x in rec.get("extra") or []:
                self.db.execute("INSERT OR IGNORE INTO extra_files VALUES(?,?)", (rec.get("id"), x))
            res["files"] += cur.rowcount
        if ids:
            key = "q[]:"
            self.set_state(key + "newest", max(max(ids), self.get_state(key + "newest") or 0))
            if self.get_state(key + "backfill_done_floor") is None:
                self.set_state(key + "cursor", min(ids))
        self.db.commit()
        return res

    def _migrate(self) -> None:
        v = self.get_state("schema_version", 1)
        if v < 2:
            # 1.0 marked metadata-only posts 'skipped', which `download` then ignored (bug).
            self.db.execute("UPDATE posts SET file_status='pending' WHERE file_status='skipped'")
            self.db.execute("DROP INDEX IF EXISTS posts_status")
            self.db.execute("CREATE INDEX posts_status ON posts(file_status, id)")
        if v < 3:
            cols = {r[1] for r in self.db.execute("PRAGMA table_info(posts)")}
            if "tag_cats" not in cols:
                self.db.execute("ALTER TABLE posts ADD COLUMN tag_cats TEXT")
            # 1.1 pre-filled data/aa/bb/<md5>.<ext> for every post; paths are now assigned when the
            # file is saved. Files already saved keep their old path until `reorganize --apply`.
            self.db.execute("UPDATE posts SET file_path=NULL WHERE file_status<>'ok'")
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(posts)")}
        if "parent_id" not in cols:  # 1.3: post relationships (export parent_id / API relationships)
            self.db.execute("ALTER TABLE posts ADD COLUMN parent_id INTEGER")
        fe = {r[1] for r in self.db.execute("PRAGMA table_info(file_errors)")}
        if fe and "attempts" not in fe:  # 1.8: failures so far per file (for the failure log / retry-failed)
            self.db.execute("ALTER TABLE file_errors ADD COLUMN attempts INTEGER NOT NULL DEFAULT 1")
        self.set_state("schema_version", SCHEMA_VERSION)  # committed by _open with everything else

    def close(self):
        if getattr(self, "_failures", None) is not None:
            self._failures.close()
            self._failures = None
        try:
            self.commit()
            try:
                self.set_state("open", False)
                self.db.commit()
                self.db.execute("PRAGMA optimize")
                self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
        finally:
            self.db.close()
            self.jsonl.close()
            self.files_jsonl.close()
            for f in self.meta_jsonl.values():
                f.close()

    # --- state ---------------------------------------------------------
    def get_state(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row[0])
        except (TypeError, ValueError):
            return default

    def del_state(self, key: str) -> None:
        self.db.execute("DELETE FROM state WHERE key=?", (key,))

    def set_state(self, key: str, value) -> None:
        self.db.execute(
            "INSERT INTO state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    def commit(self) -> None:
        """Durable commit: JSONL journals are flushed+fsynced first, then SQLite commits."""
        if self._jsonl_dirty:
            for f in (self.jsonl, self.files_jsonl):
                f.flush()
                os.fsync(f.fileno())
            self._jsonl_dirty = False
        for f in self.meta_jsonl.values():
            f.flush()
        self.db.commit()
        self._last_commit = time.monotonic()
        self._uncommitted = 0

    def maybe_commit(self, every_n: int = 25, every_s: float = 10.0) -> None:
        """Batched commit for per-file status updates (losing a batch only causes a cheap re-hash)."""
        self._uncommitted += 1
        if self._uncommitted >= every_n or time.monotonic() - self._last_commit >= every_s:
            self.commit()

    # --- posts ---------------------------------------------------------
    def upsert_post(self, post: dict, journal: bool = True) -> str:
        """Store an API post object. Returns its resulting file_status."""
        f = post.get("file") if isinstance(post.get("file"), dict) else {}
        md5, ext, url = f.get("md5"), safe_ext(f.get("ext") if isinstance(f.get("ext"), str) else None), f.get("url")
        if not _is_md5(md5):
            md5 = None
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            url = None
        prev = self.db.execute("SELECT file_status, md5 FROM posts WHERE id=?", (post["id"],)).fetchone()
        flags = post.get("flags") if isinstance(post.get("flags"), dict) else {}
        deleted = bool(flags.get("deleted"))
        if not url or not md5 or not ext:
            status = "no_url"
        elif prev and prev[0] == "ok" and prev[1] == md5:
            status = "ok"
        else:
            status = "pending"  # new post, or the file was replaced on the site (new md5)
        raw = json.dumps(post, ensure_ascii=False, separators=(",", ":"))
        if journal:
            self.jsonl.write(raw + "\n")
            self._jsonl_dirty = True
        score = post.get("score")
        if isinstance(score, dict):
            score = score.get("total")
        cats = categorize_api_tags(raw)
        created, updated = post.get("created_at"), post.get("updated_at")
        self.db.execute(_UPSERT_SQL, (
            post["id"], created if isinstance(created, str) else None, updated if isinstance(updated, str) else None,
            md5, ext, _int(f.get("size")), _int(f.get("width")), _int(f.get("height")), url,
            post.get("rating") if isinstance(post.get("rating"), str) else None,
            _int(score), _int(post.get("fav_count")), int(deleted), _int(post.get("change_seq")),
            all_tags(post), status, time.time(), raw,
            json.dumps(cats, ensure_ascii=False, separators=(",", ":")) if cats is not None else None,
        ))
        rel = post.get("relationships") if isinstance(post.get("relationships"), dict) else {}
        if _int(rel.get("parent_id")) is not None:
            self.db.execute("UPDATE posts SET parent_id=? WHERE id=?", (_int(rel.get("parent_id")), post["id"]))
        return status

    def merge_api_json(self, post: dict, journal: bool = True) -> bool:
        """Attach a fresh API object to a post that is already known (e.g. seeded from the export).

        Unlike upsert_post this never drops a working file URL: the API hides file.url for some
        posts when not logged in, while the export-derived static URL still works. Score, favs,
        tags and the raw JSON are refreshed; a replaced file (new valid md5) goes back to pending.
        Journalled with "_merge": true so a rebuild replays it with the same rules.
        """
        prev = self.db.execute("SELECT file_status, md5, ext FROM posts WHERE id=?", (post.get("id"),)).fetchone()
        if prev is None:
            self.upsert_post(post, journal)
            return True
        f = post.get("file") if isinstance(post.get("file"), dict) else {}
        md5, ext, url = f.get("md5"), safe_ext(f.get("ext") if isinstance(f.get("ext"), str) else None), f.get("url")
        flags = post.get("flags") if isinstance(post.get("flags"), dict) else {}
        deleted = bool(flags.get("deleted"))
        status = prev[0]
        if deleted and status not in ("ok",):
            status = "deleted"  # an archived file is never dropped because the post was deleted later
        elif _is_md5(md5) and ext and md5 != prev[1]:
            status = "pending"  # replaced on the site: fetch the new file (the old one is kept aside)
        else:
            md5, ext = prev[1], prev[2]
        if not (isinstance(url, str) and url.startswith(("http://", "https://"))):
            url = None
        if url is None and md5 != prev[1] and md5 and ext:  # hidden URL: derive it, like the export seed
            url = self.site.file_url(md5, ext)
        if status == "no_url" and url and md5 and ext:
            status = "pending"
        raw = json.dumps(post, ensure_ascii=False, separators=(",", ":"))
        if journal:
            self.jsonl.write(json.dumps({"_merge": True, **post}, ensure_ascii=False, separators=(",", ":")) + "\n")
            self._jsonl_dirty = True
        score = post.get("score")
        if isinstance(score, dict):
            score = score.get("total")
        cats = categorize_api_tags(raw)
        rel = post.get("relationships") if isinstance(post.get("relationships"), dict) else {}
        self.db.execute(
            "UPDATE posts SET md5=?, ext=?, size=COALESCE(?, size), file_url=COALESCE(?, file_url), score=COALESCE(?, score), "
            "fav_count=COALESCE(?, fav_count), tags=?, tag_cats=COALESCE(?, tag_cats), is_deleted=?, "
            "change_seq=COALESCE(?, change_seq), parent_id=COALESCE(?, parent_id), file_status=?, json=?, "
            "fetched_at=? WHERE id=?",
            (md5, ext, _int(f.get("size")), url, _int(score), _int(post.get("fav_count")), all_tags(post),
             json.dumps(cats, ensure_ascii=False, separators=(",", ":")) if cats is not None else None,
             int(deleted), _int(post.get("change_seq")), _int(rel.get("parent_id")), status, raw, time.time(),
             post["id"]))
        return False

    def post_info(self, post_id: int) -> PostInfo | None:
        row = self.db.execute(f"SELECT {INFO_COLS} FROM posts WHERE id=?", (post_id,)).fetchone()
        return PostInfo.from_row(row, self.lookup_categories if self.has_tag_categories() else None) if row else None

    def has_tag_categories(self) -> bool:
        if getattr(self, "_has_cats", None) is None:
            self._has_cats = self.db.execute("SELECT 1 FROM tag_categories LIMIT 1").fetchone() is not None
        return self._has_cats

    def lookup_categories(self, names: list[str]) -> dict:
        out = {}
        for i in range(0, len(names), 500):
            chunk = names[i:i + 500]
            out.update(self.db.execute(
                f"SELECT name, category FROM tag_categories WHERE name IN ({','.join('?' * len(chunk))})",
                chunk).fetchall())
        return out

    def iter_pending(self, batch: int = 500, ids=None):
        """Yield pending files newest-first in small keyset batches (constant memory).
        1.8: `ids` limits it to those posts (retry-failed)."""
        cols = "id, md5, ext, size, file_url, file_path"
        if ids is not None:
            ids = sorted({int(i) for i in ids}, reverse=True)
            for k in range(0, len(ids), batch):
                part = ids[k:k + batch]
                yield from self.db.execute(
                    f"SELECT {cols} FROM posts WHERE file_status='pending' AND id IN ({','.join('?' * len(part))}) "
                    "ORDER BY id DESC", part).fetchall()
            return
        last = None
        while True:
            if last is None:
                rows = self.db.execute(f"SELECT {cols} FROM posts WHERE file_status='pending' "
                                       "ORDER BY id DESC LIMIT ?", (batch,)).fetchall()
            else:
                rows = self.db.execute(f"SELECT {cols} FROM posts WHERE file_status='pending' "
                                       "AND id < ? ORDER BY id DESC LIMIT ?", (last, batch)).fetchall()
            if not rows:
                return
            yield from rows
            last = rows[-1][0]

    def pending_summary(self, ids=None) -> tuple[int, int]:
        if ids is None:
            n, b = self.db.execute(
                "SELECT COUNT(*), COALESCE(SUM(size),0) FROM posts WHERE file_status='pending'").fetchone()
            return n, b
        n = b = 0
        ids = list(ids)
        for k in range(0, len(ids), 500):
            part = ids[k:k + 500]
            x, y = self.db.execute("SELECT COUNT(*), COALESCE(SUM(size),0) FROM posts WHERE file_status='pending' "
                                   f"AND id IN ({','.join('?' * len(part))})", part).fetchone()
            n, b = n + x, b + y
        return n, b

    def status_counts(self, ids) -> dict:
        """1.8: {file_status: count} for these posts."""
        out: dict = {}
        ids = list(ids)
        for k in range(0, len(ids), 500):
            part = ids[k:k + 500]
            for st, c in self.db.execute(f"SELECT file_status, COUNT(*) FROM posts WHERE id IN "
                                         f"({','.join('?' * len(part))}) GROUP BY file_status", part):
                out[st] = out.get(st, 0) + c
        return out

    def failed_files(self) -> list:
        """1.8: [(post_id, reason or None, file_status)] of files currently marked failed/missing/md5_mismatch
        (that still have a URL), for `retry-failed`."""
        q = ",".join("?" * len(RETRYABLE_STATUSES))
        return self.db.execute(
            f"SELECT p.id, fe.reason, p.file_status FROM posts p LEFT JOIN file_errors fe ON fe.post_id=p.id "
            f"WHERE p.file_status IN ({q}) AND p.file_url IS NOT NULL ORDER BY p.id DESC", RETRYABLE_STATUSES).fetchall()

    def restore_failed(self, ids, prior: dict) -> int:
        """1.8: after an interrupted retry-failed, put back the failed status of files still pending."""
        n = 0
        for pid in ids:
            st = prior.get(pid)
            if st in RETRYABLE_STATUSES:
                n += self.db.execute("UPDATE posts SET file_status=? WHERE id=? AND file_status='pending'",
                                     (st, pid)).rowcount
        self.commit()
        return n

    def set_pending(self, ids) -> int:
        """1.8: mark these posts pending (retry-failed). Returns how many changed."""
        ids, n = list(ids), 0
        for k in range(0, len(ids), 500):
            part = ids[k:k + 500]
            n += self.db.execute(f"UPDATE posts SET file_status='pending' WHERE id IN ({','.join('?' * len(part))})",
                                 part).rowcount
        self.commit()
        return n

    def reset_retryable(self) -> int:
        cur = self.db.execute(
            f"UPDATE posts SET file_status='pending' WHERE file_status IN ({','.join('?' * len(RETRYABLE_STATUSES))})"
            " AND file_url IS NOT NULL", RETRYABLE_STATUSES)
        self.commit()
        return cur.rowcount

    def set_file_status(self, post_id: int, status: str):
        self.db.execute("UPDATE posts SET file_status=? WHERE id=?", (status, post_id))
        if status != "ok":
            self.db.execute("DELETE FROM verified WHERE post_id=?", (post_id,))

    def set_file_error(self, post_id: int, reason: str, detail: str | None = None) -> int:
        """1.7: remember why the last attempt at this file failed (http_403, http_404, md5_mismatch ...).
        1.8: also counts the failures (until the file is saved); returns that count."""
        self.db.execute("INSERT INTO file_errors(post_id, reason, detail, at, attempts) VALUES(?,?,?,?,1) "
                        "ON CONFLICT(post_id) DO UPDATE SET reason=excluded.reason, detail=excluded.detail, "
                        "at=excluded.at, attempts=file_errors.attempts+1", (post_id, reason, detail, time.time()))
        return self.db.execute("SELECT attempts FROM file_errors WHERE post_id=?", (post_id,)).fetchone()[0]

    def file_error(self, post_id: int) -> tuple | None:
        """1.8: (reason, attempts) of the last failure of this file, or None."""
        return self.db.execute("SELECT reason, attempts FROM file_errors WHERE post_id=?", (post_id,)).fetchone()

    @property
    def failures(self):
        """1.8: the failed-downloads.csv / .log writer for this run (opened on the first failure).
        1.9: trimmed to failure_log_max_mb when it is closed (current failures are always kept)."""
        if getattr(self, "_failures", None) is None:
            from .failures import FailureLog
            self._failures = FailureLog(self.out, store=self,
                                        max_mb=getattr(self, "failure_log_max_mb", None))
        return self._failures

    def archive_counts(self) -> dict:
        """1.9: whole-archive totals for the dashboard (from the export-seeded posts table)."""
        return archive_counts(self.db)

    def mark_verified(self, post_id: int, md5: str) -> None:
        """1.6: this file's md5 was checked (inline while downloading, or by `verify`)."""
        import time as _t
        self.db.execute("INSERT OR REPLACE INTO verified VALUES(?,?,?)", (post_id, md5, _t.time()))

    def file_path(self, post_id: int) -> str | None:
        r = self.db.execute("SELECT file_path FROM posts WHERE id=?", (post_id,)).fetchone()
        return r[0] if r else None

    def find_local_copy(self, md5: str, exclude_id: int) -> str | None:
        """Relative path of an already verified file with this md5 (another post, same content)."""
        row = self.db.execute("SELECT file_path FROM posts WHERE md5=? AND file_status='ok' AND id<>? "
                              "AND file_path IS NOT NULL LIMIT 1", (md5, exclude_id)).fetchone()
        return row[0] if row else None

    def extra_paths(self, post_id: int) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT path FROM extra_files WHERE post_id=? ORDER BY path", (post_id,))]

    def record_file(self, post_id: int, md5: str, relpath: str, extra: list[str] = ()) -> None:
        """Mark a file verified at relpath (+ copies/hardlinks for other directors) and journal it."""
        self.db.execute("UPDATE posts SET file_status='ok', file_path=? WHERE id=?", (relpath, post_id))
        self.db.execute("DELETE FROM verified WHERE post_id=?", (post_id,))  # 1.6: new bytes = unverified
        self.db.execute("DELETE FROM file_errors WHERE post_id=?", (post_id,))
        for x in extra:
            self.db.execute("INSERT OR IGNORE INTO extra_files VALUES(?,?)", (post_id, x))
        self.files_jsonl.write(json.dumps({"id": post_id, "md5": md5, "path": relpath, "extra": list(extra)},
                                          ensure_ascii=False, separators=(",", ":")) + "\n")
        self._jsonl_dirty = True

    def move_file_record(self, post_id: int, md5: str, new_relpath: str, status: str = "ok") -> None:
        self.db.execute("UPDATE posts SET file_path=?, file_status=? WHERE id=?", (new_relpath, status, post_id))
        if status == "ok":
            self.files_jsonl.write(json.dumps({"id": post_id, "md5": md5, "path": new_relpath, "extra":
                                               self.extra_paths(post_id)}, ensure_ascii=False,
                                              separators=(",", ":")) + "\n")
            self._jsonl_dirty = True

    # --- other metadata (pools, tags, wiki pages) ----------------------
    def upsert_meta(self, kind: str, item: dict, journal: bool = True):
        s = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
        if journal:
            if kind not in self.meta_jsonl:
                self.meta_jsonl[kind] = open(self.out / f"{kind}.jsonl", "a", encoding="utf-8", newline="\n")
            self.meta_jsonl[kind].write(s + "\n")
        self.db.execute(
            "INSERT INTO meta_items(kind,id,json,fetched_at) VALUES(?,?,?,?) "
            "ON CONFLICT(kind,id) DO UPDATE SET json=excluded.json, fetched_at=excluded.fetched_at",
            (kind, item["id"], s, time.time()),
        )


# --- 1.9: dashboard totals and the last-run record (control panel resume prompt) -----------------
def archive_counts(db) -> dict:
    """Expected = every post that is not deleted (export or API), done = saved files, remaining = files
    still to download (pending + failed + missing + md5 mismatch), with sizes from the export."""
    q = ("SELECT file_status, COUNT(*), COALESCE(SUM(size), 0) FROM posts "
         "WHERE file_status <> 'deleted' GROUP BY file_status")
    by = {st: (n, b) for st, n, b in db.execute(q)}
    def n(*sts):
        return sum(by.get(s, (0, 0))[0] for s in sts)
    def b(*sts):
        return sum(by.get(s, (0, 0))[1] for s in sts)
    return {"expected_files": n(*by), "expected_bytes": b(*by),
            "done_files": n("ok"), "done_bytes": b("ok"),
            "pending_files": n("pending"), "pending_bytes": b("pending"),
            "failed_files": n(*RETRYABLE_STATUSES), "failed_bytes": b(*RETRYABLE_STATUSES),
            "no_url_files": n("no_url")}


def _now_local() -> str:
    from datetime import datetime
    return datetime.now().astimezone().isoformat(timespec="seconds")


def start_run_record(store: "Store", cmd: str) -> None:
    store.set_state("last_run", {"cmd": cmd, "started": _now_local(), "pid": os.getpid(),
                                 "ended": None, "code": None})
    store.commit()


def finish_run_record(out: Path, code: int) -> None:
    """Called after the store is closed (short separate connection). Never raises."""
    try:
        path, _site = siteprofile.find_db(out)
        if path is None:
            return
        db = sqlite3.connect(str(path), timeout=5)
        try:
            row = db.execute("SELECT value FROM state WHERE key='last_run'").fetchone()
            rec = json.loads(row[0]) if row else None
            if isinstance(rec, dict) and rec.get("pid") == os.getpid() and rec.get("ended") is None:
                rec.update(ended=_now_local(), code=int(code))
                db.execute("UPDATE state SET value=? WHERE key='last_run'", (json.dumps(rec),))
                db.commit()
        finally:
            db.close()
    except (sqlite3.Error, OSError, ValueError, TypeError):
        pass
