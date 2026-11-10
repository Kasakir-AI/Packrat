"""Daily database exports (e621ng /db_exports.json): download, verify, keep, and merge into the DB.

The exports are the source of truth for ranking (score, favs, size, rating, deleted) and for
tag categories / pools / relationships. `sync_exports` refreshes them at most once a day,
verifies each one (sha256 from the listing + a full gzip/header check), keeps the previous good
copy, and merges posts.csv.gz into the DB incrementally (no file is re-downloaded unless the
post's file itself changed)."""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import logging
import sqlite3
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path

import requests

from .client import Client, ForbiddenError, NotFoundError
from .platform_utils import (DiskFullError, StorageError, csv_field_limit, ensure_free, long_path, safe_ext, safe_filename,
                             safe_replace, safe_unlink)
from .events import control, emit
from .naming import categorize_flat
from . import siteprofile
from .store import _is_md5

log = logging.getLogger("packrat")
SEED_BATCH = 5000


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def list_exports(client: Client) -> list[dict] | None:
    """Return the export list, or None if the endpoint doesn't exist (404)."""
    try:
        data = client.get_json("/db_exports.json")
    except NotFoundError:
        return None
    return data if isinstance(data, list) else data.get("db_exports", data)


def _fetch_to(client: Client, url: str, tmp: Path, name: str, total: int, retries: int) -> str:
    """Stream url into tmp (retrying broken transfers); returns the sha256 hex digest."""
    for attempt in range(1, retries + 2):
        h = hashlib.sha256()
        try:
            r = client.stream(url)
            try:
                with open(tmp, "wb") as f:
                    got = 0
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
                        h.update(chunk)
                        got += len(chunk)
                        emit("file_progress", done=got, size=total, name=name)
                        control.check_stop()
            finally:
                r.close()
        except requests.RequestException as e:  # broken transfer: retry a few times
            safe_unlink(tmp)
            if attempt > retries:
                raise RuntimeError(f"export download of {name} kept failing: {e}") from e
            log.warning("export transfer broke (%s); retry %d/%d", e.__class__.__name__, attempt, retries)
            control.sleep(2 ** attempt)
            continue
        except BaseException:
            safe_unlink(tmp)
            raise
        return h.hexdigest()
    raise AssertionError("unreachable")


# 2.0: exports the site lists without a checksum by design (seen on the live listing: artists.csv.gz).
# Missing a checksum there is normal and only logged at INFO, so it is not shown as the panel's "Last
# problem". Only the posts export (the one the archive is built from) still warns when it has none.
NEVER_CHECKSUMMED = frozenset({"artists"})
CHECKSUM_IMPORTANT = frozenset({"posts"})


def _export_kind(entry: dict, fname: str) -> str:
    name = str(entry.get("name") or "").strip().lower()
    if not name:
        name = fname.lower().split(".", 1)[0]
        name = name.split("-", 1)[0] if name[:1].isalpha() else name
    return name


def _log_no_checksum(entry: dict, fname: str) -> None:
    kind = _export_kind(entry, fname)
    if kind in CHECKSUM_IMPORTANT and kind not in NEVER_CHECKSUMMED:
        log.warning("export %s has no checksum in the listing; it cannot be verified", fname)
    else:
        log.info("export %s has no checksum in the listing (normal for this export); saved without "
                 "a checksum check", fname)


def download_export(client: Client, entry: dict, out_dir: Path, retries: int = 3) -> Path:
    url = entry.get("url")
    if not url:
        raise RuntimeError(f"export entry has no url: {entry}")
    out_dir = long_path(Path(out_dir))
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / safe_filename(entry.get("file_name") or url.rsplit("/", 1)[-1], "export.csv.gz")
    want = (entry.get("checksum") or "").lower() or None
    if dest.exists() and want and sha256_of(dest) == want:
        log.info("export %s already present and checksum ok", dest.name)
        return dest
    if not want:
        _log_no_checksum(entry, dest.name)
    ensure_free(out_dir, int(entry.get("file_size") or 0) * 8, 1 * 2**30)  # room to decompress too
    tmp = dest.with_name(dest.name + ".part")
    # 1.7: the CDN in front of static1 can keep serving yesterday's file for the plain URL (seen live
    # on 2026-11-04: Cloudflare cache-control max-age=2678400, last-modified a day behind the listing).
    # On a checksum mismatch, fetch once more with a cache-busting query (?<checksum prefix>, a new
    # cache key; the file itself is unchanged) before giving up and keeping the last good copy.
    urls = [url] + ([f"{url}{'&' if '?' in url else '?'}{want[:12]}"] if want else [])
    for n, u in enumerate(urls):
        digest = _fetch_to(client, u, tmp, dest.name, int(entry.get("file_size") or 0), retries)
        if not want or digest == want:
            break
        safe_unlink(tmp)
        if n + 1 < len(urls):
            log.warning("export %s: sha256 %s does not match the listing (%s...); probably a stale CDN copy - "
                        "retrying once with a cache-busting URL", dest.name, digest[:12], want[:12])
            continue
        raise RuntimeError(f"sha256 mismatch for {dest.name}: got {digest}, expected {want} "
                           "(the export may have been regenerated mid-download, or the CDN serves a stale "
                           "copy; rerun later)")
    safe_replace(tmp, dest)
    log.info("saved export %s (%d bytes, sha256 %s)", dest, dest.stat().st_size, "ok" if want else "unchecked")
    return dest


def _nul_free(f):
    """csv can't parse NUL bytes ("line contains NUL"); drop them (they're never meaningful here)."""
    for line in f:
        yield line.replace("\0", "") if "\0" in line else line


def _open_csv_gz(path: Path):
    return gzip.open(path, "rt", newline="", encoding="utf-8", errors="replace")


_DAMAGED = (EOFError, gzip.BadGzipFile, zlib.error)


def _damaged(path: Path, e: BaseException) -> RuntimeError:
    return RuntimeError(f"{Path(path).name} is truncated or damaged ({e.__class__.__name__}: {e}). Delete it "
                        "and run again so it is re-downloaded (progress so far is kept).")


def load_tags_export(store, csv_gz: Path, batch: int = 20000) -> int:
    """Load tags.csv.gz (id,name,category,...) into tag_categories, then categorize seeded posts."""
    csv.field_size_limit(csv_field_limit())
    db, buf, n = store.db, [], 0
    emit("phase", name="loading tag categories")
    try:
        with _open_csv_gz(csv_gz) as f:
            reader, hm = _reader(f, "tags", TAGS_COLUMNS)
            for row in reader:
                row = hm.row(row)
                name, cat = row.get("name"), _int(row.get("category"))
                if not name or cat is None:
                    continue
                buf.append((name, cat))
                if len(buf) >= batch:
                    db.executemany("INSERT OR REPLACE INTO tag_categories VALUES(?,?)", buf)
                    n += len(buf)
                    buf.clear()
                    control.check_stop()
    except _DAMAGED as e:
        db.rollback()
        raise _damaged(csv_gz, e) from e
    db.executemany("INSERT OR REPLACE INTO tag_categories VALUES(?,?)", buf)
    n += len(buf)
    store.commit()
    store._has_cats = None
    log.info("loaded %d tag categories", n)
    fixed = categorize_seeded(store)
    if fixed:
        log.info("categorized tags of %d seeded posts", fixed)
    return n


def categorize_seeded(store, batch: int = 5000) -> int:
    """Fill tag_cats for export-seeded posts (they only have a flat tag string)."""
    if not store.has_tag_categories():
        return 0
    lookup = dict(store.db.execute("SELECT name, category FROM tag_categories"))
    n, last = 0, -1
    while True:
        rows = store.db.execute("SELECT id, tags FROM posts WHERE tag_cats IS NULL AND id > ? ORDER BY id LIMIT ?",
                                (last, batch)).fetchall()
        if not rows:
            break
        upd = [(json.dumps(categorize_flat(t, lookup)[0], ensure_ascii=False, separators=(",", ":")), i)
               for i, t in rows]
        store.db.executemany("UPDATE posts SET tag_cats=? WHERE id=?", upd)
        store.db.commit()
        n += len(rows)
        last = rows[-1][0]
        control.check_stop()
    return n


def _int(v):
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None



# ---------------------------------------------------------------------------
# header mapping: columns are read by header name, never by position
POSTS_COLUMNS = ("id", "uploader_id", "created_at", "md5", "source", "rating", "image_width", "image_height",
                 "tag_string", "locked_tags", "fav_count", "file_ext", "parent_id", "change_seq", "approver_id",
                 "file_size", "comment_count", "description", "duration", "updated_at", "is_deleted",
                 "is_pending", "is_flagged", "score", "up_score", "down_score", "is_rating_locked",
                 "is_status_locked", "is_note_locked", "bg_color", "last_noted_at", "last_commented_at")
POSTS_USED = ("md5", "file_ext", "file_size", "tag_string", "rating", "score", "fav_count", "is_deleted",
              "change_seq", "updated_at", "parent_id", "image_width", "image_height", "created_at")
TAGS_COLUMNS = ("id", "name", "category", "post_count", "created_at", "updated_at", "is_locked")
POOLS_COLUMNS = ("id", "name", "created_at", "updated_at", "creator_id", "description", "is_active",
                 "category", "post_ids")
ALIASES = {"ext": "file_ext", "tags": "tag_string", "size": "file_size", "width": "image_width",
           "height": "image_height", "favcount": "fav_count", "favs": "fav_count", "fav_count_total": "fav_count",
           "deleted": "is_deleted", "total_score": "score", "parent": "parent_id"}
REQUIRED = {"posts": ("id",), "tags": ("name", "category"), "pools": ("id", "post_ids")}


class HeaderMap:
    """Maps a CSV header to canonical column names (case/space-insensitive, with aliases).

    Unknown columns are kept in the raw JSON and warned about once; a missing used column
    becomes NULL (with a warning); a missing required column is a clear error."""

    def __init__(self, kind: str, header: list[str] | None, known: tuple, used: tuple = ()):
        if not header:
            raise RuntimeError(f"{kind} export has no header row")
        self.kind, self.rename, unknown = kind, {}, []
        for h in header:
            k = (h or "").strip().lower().replace(" ", "_")
            k = ALIASES.get(k, k) if k not in known else k
            self.rename[h] = k
            if k not in known:
                unknown.append(h)
        have = set(self.rename.values())
        miss_req = [c for c in REQUIRED.get(kind, ()) if c not in have]
        if miss_req:
            raise RuntimeError(f"{kind} export is missing required column(s) {', '.join(miss_req)} "
                               f"(header: {', '.join(header[:12])}...)")
        self.missing = [c for c in used if c not in have]
        self.unknown = unknown
        if unknown:
            log.warning("%s export has new/unknown column(s) %s; kept in the raw JSON, otherwise ignored",
                        kind, ", ".join(unknown))
        if self.missing:
            log.warning("%s export lacks column(s) %s; those values will be empty", kind, ", ".join(self.missing))
        self.identity = all(h == k for h, k in self.rename.items())

    def row(self, row: dict) -> dict:
        if self.identity:
            return row
        return {self.rename.get(k, k): v for k, v in row.items()}


def _bool(v) -> bool:
    return str(v or "").strip().lower() in ("t", "true", "1", "yes", "y")


def _reader(f, kind: str, known: tuple, used: tuple = ()):
    r = csv.DictReader(_nul_free(f))
    hm = HeaderMap(kind, r.fieldnames, known, used)
    return r, hm


def _stage_tuple(row: dict, lookup: dict | None) -> tuple:
    pid = int(row["id"])
    md5 = (row.get("md5") or "").strip().lower()
    md5 = md5 if _is_md5(md5) else None
    ext = safe_ext(row.get("file_ext"))
    deleted = _bool(row.get("is_deleted"))
    url = siteprofile.current().file_url(md5, ext) if md5 and ext and not deleted else None
    status = "deleted" if deleted else ("pending" if url else "no_url")
    row = {k: v for k, v in row.items() if k is not None}  # extra unnamed columns in a damaged row
    row["_source"] = "db_export"
    cats = (json.dumps(categorize_flat(row.get("tag_string"), lookup)[0], ensure_ascii=False,
                       separators=(",", ":")) if lookup else None)
    rating = (row.get("rating") or "").strip().lower() or None
    return (pid, row.get("created_at"), row.get("updated_at"), md5, ext, _int(row.get("file_size")),
            _int(row.get("image_width")), _int(row.get("image_height")), url, rating,
            _int(row.get("score")), _int(row.get("fav_count")), int(deleted), _int(row.get("change_seq")),
            row.get("tag_string"), status, json.dumps(row, ensure_ascii=False, separators=(",", ":")), cats,
            _int(row.get("parent_id")))


_STAGE_COLS = ("id,created_at,updated_at,md5,ext,size,width,height,file_url,rating,score,fav_count,"
               "is_deleted,change_seq,tags,file_status,json,tag_cats,parent_id")
_STAGE_DDL = ("CREATE TABLE IF NOT EXISTS export_stage (id INTEGER PRIMARY KEY, created_at TEXT, updated_at TEXT, "
              "md5 TEXT, ext TEXT, size INTEGER, width INTEGER, height INTEGER, file_url TEXT, rating TEXT, "
              "score INTEGER, fav_count INTEGER, is_deleted INTEGER, change_seq INTEGER, tags TEXT, "
              "file_status TEXT, json TEXT, tag_cats TEXT, parent_id INTEGER)")

# Existing rows: take the export's values when the export is at least as fresh as what we have
# (our copy was fetched before the export was built, or the export's change_seq is newer).
# The DB row is only touched when something actually differs, so `changes()` counts real updates.
_FRESH = "(p.fetched_at IS NULL OR p.fetched_at <= :t OR (s.change_seq IS NOT NULL AND s.change_seq > COALESCE(p.change_seq, -1)))"
_NEW_STATUS = """CASE
      WHEN s.is_deleted = 1 THEN CASE WHEN p.file_status = 'ok' THEN 'ok' ELSE 'deleted' END
      WHEN s.md5 IS NOT NULL AND p.md5 IS NOT NULL AND s.md5 <> p.md5 THEN 'pending'
      WHEN p.file_status IN ('deleted', 'no_url') AND s.file_url IS NOT NULL THEN 'pending'
      ELSE p.file_status END"""
MERGE_SQL = f"""UPDATE posts AS p SET
    score = s.score, fav_count = s.fav_count, rating = COALESCE(s.rating, p.rating),
    tags = COALESCE(s.tags, p.tags), tag_cats = COALESCE(s.tag_cats, p.tag_cats),
    is_deleted = s.is_deleted, change_seq = COALESCE(s.change_seq, p.change_seq),
    updated_at = COALESCE(s.updated_at, p.updated_at), parent_id = s.parent_id,
    size = COALESCE(s.size, p.size), width = COALESCE(s.width, p.width), height = COALESCE(s.height, p.height),
    file_status = {_NEW_STATUS},
    md5 = COALESCE(s.md5, p.md5), ext = COALESCE(s.ext, p.ext),
    file_url = CASE WHEN s.file_url IS NOT NULL AND (p.file_url IS NULL OR s.md5 <> p.md5) THEN s.file_url
                    ELSE p.file_url END,
    json = CASE WHEN instr(p.json, '"_source":"db_export"') > 0 THEN s.json ELSE p.json END,
    fetched_at = :t
  FROM export_stage AS s
  WHERE p.id = s.id AND s.id > :lo AND s.id <= :hi AND {_FRESH} AND (
    p.score IS NOT s.score OR p.fav_count IS NOT s.fav_count OR p.tags IS NOT COALESCE(s.tags, p.tags)
    OR p.rating IS NOT COALESCE(s.rating, p.rating) OR p.is_deleted IS NOT s.is_deleted
    OR p.md5 IS NOT COALESCE(s.md5, p.md5) OR p.parent_id IS NOT s.parent_id
    OR p.change_seq IS NOT COALESCE(s.change_seq, p.change_seq) OR p.file_status IS NOT ({_NEW_STATUS}))"""
INSERT_NEW_SQL = f"""INSERT INTO posts({_STAGE_COLS}, fetched_at)
  SELECT {_STAGE_COLS}, :t FROM export_stage s WHERE s.id > :lo AND s.id <= :hi AND NOT EXISTS (SELECT 1 FROM posts p WHERE p.id = s.id)"""
# Rows that came from an earlier export but are absent now (destroyed on the site).
VANISHED_WHERE = """instr(json, '"_source":"db_export"') > 0 AND id <= :max_id
  AND NOT EXISTS (SELECT 1 FROM export_stage s WHERE s.id = posts.id)"""


def _parse_ts(v) -> float | None:
    """ISO-ish timestamp -> epoch seconds; naive values (the CSV's updated_at) are UTC."""
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()


def seed_from_posts_export(store, csv_gz: Path, mark_backfill_done: bool = True,
                           batch: int = SEED_BATCH, export_time: float | None = None) -> dict:
    """Load or re-merge posts.csv.gz into the local DB (no network), in bounded memory.

    1. Stream the CSV (columns by header name) into an on-disk staging table, in batches.
    2. UPDATE existing posts from it where the export is at least as fresh: score, favs, tags,
       rating, deleted, md5 ... A downloaded file is never re-fetched unless its md5 changed;
       a post deleted on the site keeps its archived file.
    3. INSERT posts that are new.
    4. Posts from an earlier export that are gone from this one are marked deleted (only if the
       new export is not suspiciously small, i.e. >= 90% of the previous row count).
    Interrupting is safe: the staging table is rebuilt from scratch on the next run, the merge is
    one transaction, and the "seeded" marker is written last.
    """
    if sqlite3.sqlite_version_info < (3, 33, 0):
        raise RuntimeError(f"SQLite {sqlite3.sqlite_version} is too old for the export merge (need 3.33+)")
    csv.field_size_limit(csv_field_limit())
    stats = {"rows": 0, "inserted": 0, "updated": 0, "deleted": 0, "skipped_existing": 0, "bad_rows": 0,
             "vanished": 0, "max_id": 0, "unknown_columns": [], "missing_columns": []}
    db = store.db
    lookup = dict(db.execute("SELECT name, category FROM tag_categories")) if store.has_tag_categories() else None
    if lookup is None:
        log.warning("tag categories are not loaded (tags.csv.gz): seeded posts have no director/species "
                    "fields yet, so their files would go to _no_director. `full` loads them automatically; "
                    "or run seed-from-export --tags-csv <tags.csv.gz>.")
    db.execute("DROP TABLE IF EXISTS export_stage")
    db.execute(_STAGE_DDL)
    db.commit()
    emit("phase", name="reading posts export")
    buf: list[tuple] = []
    max_upd = ""
    ins = f"INSERT OR REPLACE INTO export_stage({_STAGE_COLS}) VALUES({','.join('?' * 19)})"

    def flush():
        db.executemany(ins, buf)
        db.commit()
        buf.clear()
        emit("seed", rows=stats["rows"], inserted=0)
        control.checkpoint()

    try:
        with _open_csv_gz(csv_gz) as f:
            reader, hm = _reader(f, "posts", POSTS_COLUMNS, POSTS_USED)
            stats["unknown_columns"], stats["missing_columns"] = hm.unknown, hm.missing
            for raw in reader:
                stats["rows"] += 1
                try:
                    tup = _stage_tuple(hm.row(raw), lookup)
                except (KeyError, ValueError, TypeError, AttributeError):
                    stats["bad_rows"] += 1
                    continue
                stats["max_id"] = max(stats["max_id"], tup[0])
                stats["deleted"] += tup[12]
                if tup[2] and tup[2] > max_upd:
                    max_upd = tup[2]
                buf.append(tup)
                if len(buf) >= batch:
                    flush()
                    if stats["rows"] % (batch * 10) == 0:
                        log.info("read %d export rows", stats["rows"])
    except _DAMAGED as e:
        raise _damaged(csv_gz, e) from e
    if buf:
        flush()
    staged = db.execute("SELECT COUNT(*) FROM export_stage").fetchone()[0]
    # how fresh the export is: listing time if known, else its newest updated_at, else file time
    t = export_time or _parse_ts(max_upd) or Path(csv_gz).stat().st_mtime
    emit("phase", name="merging posts export")
    prev = store.get_state("seeded_export") or {}
    with db:  # one transaction: all or nothing
        # in id ranges: SQLite buffers an INSERT ... SELECT that reads its own target table, so
        # one statement over 200k rows would hold ~700 MB; chunks keep it to a few MB
        for lo in range(0, stats["max_id"] + 1, batch):
            p = {"t": t, "lo": lo, "hi": lo + batch}
            db.execute(MERGE_SQL, p)
            stats["updated"] += db.execute("SELECT changes()").fetchone()[0]
            db.execute(INSERT_NEW_SQL, p)
            stats["inserted"] += db.execute("SELECT changes()").fetchone()[0]
        if prev.get("rows") and staged >= 0.9 * prev.get("staged", prev["rows"]):
            db.execute(f"UPDATE posts SET is_deleted = 1, file_status = CASE WHEN file_status = 'ok' THEN 'ok' "
                       f"ELSE 'deleted' END WHERE file_status <> 'deleted' AND {VANISHED_WHERE}",
                       {"max_id": stats["max_id"]})
            stats["vanished"] = db.execute("SELECT changes()").fetchone()[0]
        elif prev.get("rows"):
            log.warning("posts export has %d rows, far fewer than last time (%s); not treating missing posts "
                        "as deleted", staged, prev.get("staged", prev["rows"]))
    stats["skipped_existing"] = staged - stats["inserted"]
    db.execute("DROP TABLE IF EXISTS export_stage")
    db.commit()
    if stats["max_id"]:
        key = "q[]:"
        newest = store.get_state(key + "newest")
        store.set_state(key + "newest", max(newest or 0, stats["max_id"]))
        if mark_backfill_done:
            store.set_state(key + "backfill_done_floor", 0)
        store.set_state("seeded_export", {"file": str(csv_gz), "rows": stats["rows"], "staged": staged,
                                          "at": time.time(), "export_time": t})
        store.set_state("export_generation", int(store.get_state("export_generation", 0) or 0) + 1)
    store.commit()
    log.info("posts export merged: %d rows, %d new, %d updated, %d vanished, %d bad", stats["rows"],
             stats["inserted"], stats["updated"], stats["vanished"], stats["bad_rows"])
    return stats


def load_pools_export(store, csv_gz: Path) -> dict:
    """pools.csv.gz -> meta_items(kind='pools') + pool_posts(post_id, pool_id, position)."""
    csv.field_size_limit(csv_field_limit())
    db, pools, links = store.db, [], []
    try:
        with _open_csv_gz(csv_gz) as f:
            reader, hm = _reader(f, "pools", POOLS_COLUMNS, ("name", "is_active", "category"))
            for raw in reader:
                row = hm.row(raw)
                pid = _int(row.get("id"))
                if pid is None:
                    continue
                ids = [_int(x) for x in str(row.get("post_ids") or "").strip("{}[] ").replace(",", " ").split()]
                ids = [i for i in ids if i is not None]
                row = {k: v for k, v in row.items() if k is not None}
                row.update(post_ids=ids, is_active=_bool(row.get("is_active")), _source="db_export")
                pools.append((pid, json.dumps(row, ensure_ascii=False, separators=(",", ":"))))
                links.extend((post, pid, n) for n, post in enumerate(ids, 1))
    except _DAMAGED as e:
        raise _damaged(csv_gz, e) from e
    now = time.time()
    with db:
        # API-fetched pool objects (`meta pools`) are fresher and richer: keep those
        db.executemany("INSERT INTO meta_items(kind, id, json, fetched_at) VALUES('pools', ?, ?, ?) "
                       "ON CONFLICT(kind, id) DO UPDATE SET json = excluded.json, fetched_at = excluded.fetched_at "
                       "WHERE instr(meta_items.json, '\"_source\":\"db_export\"') > 0",
                       [(i, j, now) for i, j in pools])
        db.execute("DELETE FROM pool_posts")
        db.executemany("INSERT OR IGNORE INTO pool_posts VALUES(?,?,?)", links)
    log.info("loaded %d pools (%d post memberships)", len(pools), len(links))
    return {"pools": len(pools), "memberships": len(links)}


# ---------------------------------------------------------------------------
# the export bundle: download, verify, keep the last good copy, refresh once a day
CORE_EXPORTS = ("posts", "tags", "pools")
APPLY_ORDER = ("tags", "pools", "posts")          # tag categories first, then the posts merge
SYNC_MIN_HOURS = 20                                # exports are rebuilt daily (~11 PM US Eastern)
STALE_HOURS = 36


def wanted_exports(spec: str | None, available: list[str]) -> list[str]:
    spec = (spec or "all").strip().lower()
    if spec == "all":
        return list(available)
    if spec == "core":
        return [n for n in available if n in CORE_EXPORTS]
    names = [x.strip() for x in spec.replace(" ", ",").split(",") if x.strip()]
    return [n for n in names if n in available] + [n for n in names if n not in available]


TAGS_MISSING_NOTE = ("tag categories are not loaded (no usable tags export): director folders and the report's "
                     "per-director counts fall back to _no_director / _unknown_director until the tags export "
                     "can be fetched (it is retried automatically on the next full / sync-exports / report)")


def with_tags(names: str | None) -> str | None:
    """1.8: the tags export always comes with the posts export (tag categories = director folders and
    per-director report counts). 'all' and 'core' already include it."""
    spec = (names or "all").strip().lower()
    if spec in ("all", "core"):
        return names
    parts = [x.strip() for x in spec.replace(" ", ",").split(",") if x.strip()]
    if "posts" in parts and "tags" not in parts:
        log.info("adding the tags export to %s (director folders and per-director report counts need it)",
                 ",".join(parts))
        parts.append("tags")
    return ",".join(parts)


def export_dir(out) -> Path:
    return long_path(Path(out) / "db_export")


def read_manifest(out) -> dict:
    p = export_dir(out) / "manifest.json"
    try:
        return json.loads(p.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def write_manifest(out, man: dict) -> None:
    p = export_dir(out) / "manifest.json"
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(man, indent=2, sort_keys=True), encoding="utf-8")
    safe_replace(tmp, p)


def validate_export(path: Path, name: str) -> int:
    """Decompress the whole file and check its header; returns the data row count (approx: lines).
    Raises RuntimeError if it is truncated, not gzip, or lacks the required columns."""
    known = {"posts": POSTS_COLUMNS, "tags": TAGS_COLUMNS, "pools": POOLS_COLUMNS}.get(name, ())
    try:
        with gzip.open(path, "rt", newline="", encoding="utf-8", errors="replace") as f:
            header = next(csv.reader(_nul_free([f.readline()])), None)
            if not header:
                raise RuntimeError(f"{path.name}: empty export")
            if known:
                HeaderMap(name, header, known)  # raises if a required column is missing
            n = 0
            while True:
                block = f.read(1 << 22)
                if not block:
                    break
                n += block.count("\n")
    except _DAMAGED as e:
        raise _damaged(path, e) from e
    return n


def good_export_path(out, name: str) -> Path | None:
    """The newest copy of an export that passes validation: current, else db_export/previous/."""
    d = export_dir(out)
    for p in (d / f"{name}.csv.gz", d / "previous" / f"{name}.csv.gz"):
        if p.is_file():
            try:
                validate_export(p, name)
                if p.parent.name == "previous":
                    log.warning("using the previous good copy of the %s export (the current one is damaged)", name)
                return p
            except RuntimeError as e:
                log.warning("%s", e)
    return None


# RuntimeError subclasses that must stop the run instead of "keep the last good copy"
_FATAL = (ForbiddenError, DiskFullError, StorageError)


def sync_exports(client, store, names: str | None = "all", force: bool = False, apply: bool = True) -> dict:
    """Refresh the export bundle (at most once per SYNC_MIN_HOURS unless force) and merge it.

    Per export: same sha256 as the listing -> nothing to do. Otherwise download to .part,
    verify sha256, decompress fully + check the header, move the old copy to previous/, and
    swap in the new one. Any failure (other than 403) keeps the last good copy and carries on.
    Then tags -> pools -> posts are merged into the DB if they changed since the last merge."""
    out = store.out
    names = with_tags(names)
    d = export_dir(out)
    d.mkdir(parents=True, exist_ok=True)
    man = read_manifest(out)
    res: dict = {"checked": False, "downloaded": [], "unchanged": [], "failed": {}, "applied": [], "sizes": {}}
    last = store.get_state("exports_checked_at") or 0
    recent = not force and time.time() - last < SYNC_MIN_HOURS * 3600
    # 1.8: a missing tags export is fetched even inside the once-a-day window
    tags_only = (recent and "tags" in wanted_exports(names, list(APPLY_ORDER))
                 and not (export_dir(out) / "tags.csv.gz").is_file() and not store.has_tag_categories())
    if recent and not tags_only:
        log.info("exports were checked %.1f h ago (they are rebuilt once a day); not checking again "
                 "(use --refresh-export to force)", (time.time() - last) / 3600)
    else:
        if tags_only:
            log.info("the tags export is missing (director folders need it): fetching it now")
        emit("phase", name="checking database exports")
        listing = None
        try:
            listing = list_exports(client)
        except _FATAL:  # 403 / disk full / drive gone: stop, never fall back silently
            raise
        except (requests.RequestException, RuntimeError, ValueError) as e:
            log.warning("could not read the export listing (%s); using the local copies", e)
        if listing is not None:
            res["checked"] = True
            if not tags_only:
                store.set_state("exports_checked_at", time.time())
            by = {e.get("name"): e for e in listing if isinstance(e, dict) and e.get("name")}
            for name in (["tags"] if tags_only else wanted_exports(names, list(by))):
                e = by.get(name)
                if e is None:
                    if name == "tags":
                        res["failed"]["tags"] = "the site's export listing has no tags export"
                    log.warning("no export named %r on the site", name)
                    continue
                res["sizes"][name] = int(e.get("file_size") or 0)
                _sync_one(client, out, e, man, res)
                write_manifest(out, man)
        else:
            log.warning("no export listing available; using whatever is already downloaded")
    store.commit()
    if apply:
        applied = store.get_state("applied_exports") or {}
        for name in APPLY_ORDER:
            if names not in (None, "all", "core") and name not in wanted_exports(names, list(APPLY_ORDER)):
                continue
            p = good_export_path(out, name)
            if p is None:
                continue
            sha = sha256_of(p)
            if applied.get(name) == sha and not (name == "posts" and not store.get_state("seeded_export")):
                continue
            entry = man.get(p.name if p.parent == d else f"previous/{p.name}", {})
            t = _parse_ts(entry.get("updated_at"))
            if name == "tags":
                load_tags_export(store, p)
            elif name == "pools":
                load_pools_export(store, p)
            else:
                res["posts_merge"] = seed_from_posts_export(store, p, export_time=t)
            applied[name] = sha
            res["applied"].append(name)
            store.set_state("applied_exports", applied)
            store.commit()
    wanted_tags = "tags" in wanted_exports(names, list(APPLY_ORDER))
    if wanted_tags and not store.has_tag_categories():
        why = res["failed"].get("tags")
        res["tags_note"] = TAGS_MISSING_NOTE + (f" [{why}]" if why else "")
        log.warning("%s", res["tags_note"])
    elif wanted_tags and "tags" in res["failed"]:
        res["tags_note"] = "tags export not updated (%s); using the last good copy" % res["failed"]["tags"]
    return res


def ensure_tags(store, client_factory=None, fetch: bool = True) -> dict:
    """1.8: before a report, make sure tag categories are loaded and not older than the posts export.

    1. If there is no good local tags export, or it is clearly older than the local posts export
       (manifest dates more than 6 h apart), fetch it (one listing request + one file, anonymous,
       sha256-verified with the cache-busting retry) - only when `fetch` and a client factory are given.
    2. Load the local tags export into tag_categories if it differs from what was loaded last.
    3. Fill tag_cats of export-seeded posts that have none yet.
    Never raises for network problems: the result carries a clear note instead."""
    out = store.out
    res = {"fetched": False, "loaded": False, "categories": 0, "note": ""}
    man = read_manifest(out)
    local = good_export_path(out, "tags")
    t_tags = _parse_ts(man.get("tags.csv.gz", {}).get("updated_at"))
    t_posts = _parse_ts(man.get("posts.csv.gz", {}).get("updated_at"))
    stale = local is None or bool(t_posts and t_tags and t_tags < t_posts - 6 * 3600)
    if stale and fetch and client_factory is not None:
        why = "missing" if local is None else "older than the posts export"
        log.info("tags export %s: fetching it (anonymous, one listing + one file)", why)
        emit("phase", name="fetching the tags export")
        c = None
        try:
            c = client_factory()
            listing = list_exports(c)
            e = next((x for x in (listing or []) if isinstance(x, dict) and x.get("name") == "tags"), None)
            if e is None:
                res["note"] = "the site's export listing has no tags export"
            else:
                export_dir(out).mkdir(parents=True, exist_ok=True)
                r = {"downloaded": [], "unchanged": [], "failed": {}}
                _sync_one(c, out, e, man, r)
                write_manifest(out, man)
                if r["failed"]:
                    res["note"] = f"tags export download failed: {r['failed']['tags']}"
                else:
                    res["fetched"] = "tags" in r["downloaded"]
        except _FATAL:
            raise
        except (requests.RequestException, RuntimeError, ValueError, OSError) as ex:
            res["note"] = f"could not fetch the tags export ({ex})"
        finally:
            if c is not None and hasattr(c, "close"):
                c.close()
        if res["note"]:
            log.warning("%s; using what is available", res["note"])
    elif stale and local is None:
        res["note"] = "no tags export in the archive and fetching is off"
    p = good_export_path(out, "tags")
    if p is not None:
        applied = store.get_state("applied_exports") or {}
        sha = sha256_of(p)
        if applied.get("tags") != sha or not store.has_tag_categories():
            load_tags_export(store, p)
            applied["tags"] = sha
            store.set_state("applied_exports", applied)
            store.commit()
            res["loaded"] = True
        else:
            categorize_seeded(store)
    res["categories"] = store.db.execute("SELECT COUNT(*) FROM tag_categories").fetchone()[0]
    if not res["categories"]:
        res["note"] = TAGS_MISSING_NOTE + (f" [{res['note']}]" if res["note"] else "")
        log.warning("%s", res["note"])
    return res


def _sync_one(client, out, e: dict, man: dict, res: dict) -> None:
    d = export_dir(out)
    name = e["name"]
    fname = safe_filename(e.get("file_name") or f"{name}.csv.gz", f"{name}.csv.gz")
    cur = d / fname
    want = (e.get("checksum") or "").lower() or None
    upd = _parse_ts(e.get("updated_at"))
    if upd and time.time() - upd > STALE_HOURS * 3600:
        log.warning("the %s export on the site is stale (last rebuilt %s)", name, e.get("updated_at"))
    have = man.get(fname, {})
    if cur.is_file() and want and have.get("sha256") == want and cur.stat().st_size == have.get("size"):
        res["unchanged"].append(name)
        return
    if cur.is_file() and want and sha256_of(cur) == want:  # e.g. downloaded by 1.2 (no manifest)
        man[fname] = {"sha256": want, "size": cur.stat().st_size, "updated_at": e.get("updated_at"),
                      "downloaded_at": have.get("downloaded_at") or cur.stat().st_mtime}
        res["unchanged"].append(name)
        return
    inc = d / "incoming"
    try:
        new = download_export(client, e, inc)
        validate_export(new, name)
    except _FATAL:
        safe_unlink(inc / fname)
        raise
    except (RuntimeError, OSError, requests.RequestException) as ex:
        log.warning("%s export not updated (%s); keeping the last good copy", name, ex)
        res["failed"][name] = str(ex)
        safe_unlink(inc / fname)
        return
    if cur.is_file():
        (d / "previous").mkdir(exist_ok=True)
        safe_replace(cur, d / "previous" / fname)
        if fname in man:
            man["previous/" + fname] = man[fname]
    safe_replace(new, cur)
    man[fname] = {"sha256": sha256_of(cur) if not want else want, "size": cur.stat().st_size,
                  "updated_at": e.get("updated_at"), "downloaded_at": time.time()}
    res["downloaded"].append(name)
