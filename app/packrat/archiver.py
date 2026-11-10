"""Core archive logic: pagination, checkpointing, file download + md5 verification."""
from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path

from .client import Client
from .events import control, emit
from .priority import PriorityQueue, Spec
from .naming import Naming
from .platform_utils import StorageError, free_bytes, safe_replace, safe_unlink, storage_problem
from .store import Store, abspath, legacy_relpath
from .fast import disk_tick

log = logging.getLogger("packrat")
PAGE_LIMIT = 320          # API maximum per page
CHUNK = 1 << 20           # 1 MiB read/hash/write chunks
STREAM_RETRIES = 3        # retries for a download that breaks mid-stream


def md5_of(path: Path, chunk: int = CHUNK) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def set_aside(path: Path, md5: str) -> Path:
    """Keep (never delete) a file that is in the way: <stem>.replaced-<md5 prefix>.<ext>."""
    stem, dot, ext = path.name.rpartition(".")
    if not dot:
        stem, ext = path.name, ""
    n = 0
    while True:
        tag = f".replaced-{md5[:8]}" + (f"-{n}" if n else "")
        cand = path.with_name(f"{stem}{tag}.{ext}" if ext else stem + tag)
        if not cand.exists():
            safe_replace(path, cand)
            return cand
        n += 1


def sweep_part_files(root: Path) -> int:
    """Remove leftover *.part files (interrupted transfers; never complete data)."""
    n = 0
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if f.endswith(".part"):
                safe_unlink(Path(dirpath) / f)
                n += 1
    if n:
        log.info("removed %d leftover .part files", n)
    return n


def fmt_bytes(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TiB"


def fmt_eta(seconds: float) -> str:
    seconds = int(max(0, seconds))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    return (f"{d}d " if d else "") + f"{h:02d}h{m:02d}m"


class Progress:
    """Periodic progress line: files, bytes, throughput, ETA (bounded by request rate and bandwidth)."""

    def __init__(self, total_files: int, total_bytes: int, rate: float, every_s: float = 30.0):
        self.total_files, self.total_bytes, self.rate = total_files, total_bytes, rate
        self.files = 0
        self.bytes = 0
        self.t0 = time.monotonic()
        self.every_s = every_s
        self._last = self.t0

    def eta_seconds(self) -> float:
        left_files = max(0, self.total_files - self.files)
        left_bytes = max(0, self.total_bytes - self.bytes)
        elapsed = max(1e-6, time.monotonic() - self.t0)
        bps = self.bytes / elapsed if self.bytes else 0
        by_rate = left_files / self.rate if self.rate else 0
        by_bw = left_bytes / bps if bps else 0
        return max(by_rate, by_bw)

    def update(self, nbytes: int, force: bool = False) -> None:
        self.files += 1
        self.bytes += nbytes
        now = time.monotonic()
        if force or now - self._last >= self.every_s:
            self._last = now
            elapsed = max(1e-6, now - self.t0)
            log.info("progress: %d/%d files, %s / %s, %.2f MiB/s, ETA %s",
                     self.files, self.total_files, fmt_bytes(self.bytes), fmt_bytes(self.total_bytes),
                     self.bytes / elapsed / 2**20, fmt_eta(self.eta_seconds()))


class Archiver:
    def __init__(self, client: Client | None, store: Store, *, tags: str | None = None,
                 metadata_only: bool = False, max_posts: int | None = None,
                 since_id: int | None = None, include_deleted: bool = False,
                 min_free_bytes: int = 5 * 2**30, naming: Naming | None = None,
                 spec: Spec | None = None, api_json: bool = False, workers: int = 16,
                 check_md5: bool = False, max_consecutive_403: int = 20, on_low_disk: str = "pause"):
        from .fast import clamp_workers
        self.c = client
        self.workers = clamp_workers(workers)      # 1.6: parallel file downloads
        self.check_md5 = check_md5                 # 1.6: md5 while downloading (verify checks later)
        self.max_consecutive_403 = max_consecutive_403
        self.fetcher = None
        # 1.3: download in priority order (see priority.py); None = 1.2 behaviour (newest first)
        self.spec = spec
        self.pq = PriorityQueue(store, spec) if spec is not None else None
        self.api_json = api_json
        self.s = store
        self.naming = naming or Naming()
        query = " ".join((tags or "").split())
        if "order:" in query:
            log.warning("order: tags are ignored by cursor pagination (results are always by id)")
        if include_deleted and "status:" not in query:
            query = (query + " status:any").strip()
        self.tags = query or None
        self.metadata_only = metadata_only
        self.max_posts = max_posts
        self.since_id = since_id or 0
        self.min_free_bytes = min_free_bytes
        self.on_low_disk = on_low_disk if on_low_disk in ("pause", "stop") else "pause"  # 1.9
        self.p = f"q[{self.tags or ''}]:"  # checkpoint namespace per query
        self.stats = {"posts": 0, "no_url": 0, "downloaded": 0, "already_present": 0, "reused": 0,
                      "missing": 0, "md5_mismatch": 0, "failed": 0, "bytes": 0, "api_json": 0}
        self._warned_uncategorized = False
        self._last_disk = 0.0

    # ------------------------------------------------------------------
    def _budget(self) -> int | None:
        if self.max_posts is None:
            return None
        return max(0, self.max_posts - self.stats["posts"])

    def _page_limit(self, budget: int | None) -> int:
        return PAGE_LIMIT if budget is None else max(1, min(PAGE_LIMIT, budget))

    def _save_page(self, posts: list[dict]) -> None:
        for post in posts:
            if self.s.upsert_post(post) == "no_url":
                self.stats["no_url"] += 1
            self.stats["posts"] += 1
        emit("crawl", posts=self.stats["posts"], last_id=posts[-1]["id"] if posts else None)
        emit("stats", **self.stats)

    def check_layout(self) -> None:
        """Warn once if the naming/folder settings changed since files were saved."""
        cur, prev = self.naming.describe(), self.s.get_state("layout")
        if prev is not None and prev != cur:
            n = self.s.db.execute("SELECT COUNT(*) FROM posts WHERE file_status='ok'").fetchone()[0]
            if n:
                log.warning("naming/folder settings changed since the last run. New files use the new "
                            "layout; %d existing files keep their paths. Run `reorganize` (dry run) then "
                            "`reorganize --apply` to move them.", n)
        self.s.set_state("layout", cur)
        self.s.commit()

    # ------------------------------------------------------------------
    def plan(self) -> dict:
        """--dry-run: what would happen, from local state only (no network)."""
        n, b = self.s.pending_summary()
        rate = self.c.limiter.rate if self.c else 1.0
        free = free_bytes(self.s.out)
        bw = {f"{m} Mbit/s": fmt_eta(b * 8 / (m * 1e6)) for m in (50, 100, 500)}
        return {
            "query": self.tags or "(all posts)",
            "pending_files": n, "pending_bytes": b, "pending_human": fmt_bytes(b),
            "free_disk": fmt_bytes(free), "fits_on_disk": b + self.min_free_bytes <= free,
            "est_time_bandwidth_bound": bw, "parallel_downloads": self.workers, "api_rate_req_per_s": rate,
            "next_incremental_page": (f"a{self.s.get_state(self.p + 'newest')}"
                                      if self.s.get_state(self.p + "newest") is not None else None),
            "backfill_cursor": self.s.get_state(self.p + "cursor"),
            "backfill_done": self.s.get_state(self.p + "backfill_done_floor") is not None
                             and self.s.get_state(self.p + "backfill_done_floor") <= self.since_id,
            "note": "dry run: no requests made; new posts found by the API are not included",
            **(self._plan_priority(rate) if self.pq else {}),
        }

    def _plan_priority(self, rate: float) -> dict:
        self.pq.ensure()
        tn, tb = self.pq.pending_in_tier()
        return {"order": self.spec.describe(), "tier_end_rank": self.pq.tier_end(),
                "tier_pending_files": tn, "tier_pending_human": fmt_bytes(tb),
                "tier_time_at_50_mbit": fmt_eta(tb * 8 / 50e6), "tiers": self.pq.tiers(rate=None)}

    def run(self) -> dict:
        self.check_layout()
        if not self.metadata_only:
            self.download_pending()  # finish anything left from a previous run
        self.incremental()
        self.backfill()
        if not self.metadata_only:
            self.download_pending()
        return self.stats

    def incremental(self) -> None:
        """Fetch posts newer than the newest one already seen.

        page=a<id> returns the posts immediately after <id> (server: id > X ORDER BY id ASC LIMIT n),
        so repeatedly advancing to the max id seen never skips posts.
        """
        newest = self.s.get_state(self.p + "newest")
        if newest is None:
            return
        emit("phase", name="checking for new posts")
        while True:
            budget = self._budget()
            if budget == 0:
                return
            posts = self.c.posts(f"a{newest}", self._page_limit(budget), self.tags)
            posts = sorted((p for p in posts if p["id"] > newest), key=lambda p: p["id"])
            if not posts:
                log.info("incremental: up to date (newest id %s)", newest)
                return
            if budget is not None:
                posts = posts[:budget]
            self._save_page(posts)
            newest = posts[-1]["id"]
            self.s.set_state(self.p + "newest", newest)
            self.s.commit()  # metadata + checkpoint are durable before any file download
            log.info("incremental: saved %d posts, newest now %d", len(posts), newest)
            if not self.metadata_only:
                self.download_pending()

    def backfill(self) -> None:
        """Walk from newest to oldest with page=b<id>, checkpointing the lowest id saved."""
        floor = self.since_id
        done_floor = self.s.get_state(self.p + "backfill_done_floor")
        if done_floor is not None and done_floor <= floor:
            log.info("backfill: already complete down to id %s", done_floor)
            return
        cursor = self.s.get_state(self.p + "cursor")
        emit("phase", name="crawling post metadata")
        while True:
            budget = self._budget()
            if budget == 0:
                log.info("backfill: --max-posts reached; resume later from b%s", cursor)
                return
            page = f"b{cursor}" if cursor is not None else None
            posts = self.c.posts(page, self._page_limit(budget), self.tags)
            posts.sort(key=lambda p: p["id"], reverse=True)
            if cursor is not None:
                posts = [p for p in posts if p["id"] < cursor]
            if cursor is None and posts and self.s.get_state(self.p + "newest") is None:
                self.s.set_state(self.p + "newest", posts[0]["id"])
            reached_floor = False
            if floor:
                kept = [p for p in posts if p["id"] > floor]
                reached_floor = len(kept) < len(posts)
                posts = kept
            if budget is not None:
                posts = posts[:budget]
            if posts:
                self._save_page(posts)
                cursor = posts[-1]["id"]
                self.s.set_state(self.p + "cursor", cursor)
            if not posts or reached_floor:
                self.s.set_state(self.p + "backfill_done_floor", floor)
                self.s.commit()
                log.info("backfill: complete (floor id %s)", floor)
                if posts and not self.metadata_only:
                    self.download_pending()
                return
            self.s.commit()  # metadata + cursor are durable before any file download
            log.info("backfill: saved %d posts, cursor now b%d", len(posts), cursor)
            if not self.metadata_only:
                self.download_pending()

    # ------------------------------------------------------------------
    def _engine(self, on_result):
        from .fast import ParallelDownloader
        if self.fetcher is None:
            from .client import StaticFetcher
            self.fetcher = StaticFetcher(self.c.s.headers.get("User-Agent", ""), api_client=self.c,
                                         timeout=self.c.timeout)
        return ParallelDownloader(self, on_result, workers=self.workers, check_md5=self.check_md5,
                                  fetcher=self.fetcher, max_consecutive_403=self.max_consecutive_403)

    def _emit_archive(self) -> None:
        """2.0: fresh whole-archive totals right before each download queue, i.e. after the posts export
        has been loaded/merged (the dashboard used to show only the snapshot taken at the very start)."""
        try:
            emit("archive", **self.s.archive_counts())
        except Exception as e:  # noqa: BLE001 - the dashboard must never stop a download
            log.debug("archive totals unavailable: %s", e)

    def download_pending(self) -> None:
        """1.6: pending files are downloaded by `workers` parallel workers (see fast.py); this thread
        stays the only one that writes the database."""
        if self.pq is not None:
            return self._download_by_priority()
        n, b = self.s.pending_summary()
        if not n:
            return
        if n > 1:
            log.info("downloading %d files (%s) with %d parallel downloads", n, fmt_bytes(b), self.workers)
        emit("phase", name="downloading files")
        self._emit_archive()
        emit("queue", files=n, bytes=b)
        prog = Progress(n, b, None)

        def done(job, status, nbytes):
            prog.update(nbytes)
            emit("file_done", post_id=job.post_id, status=status, nbytes=nbytes, size=job.size)
            emit("stats", **self.stats)
            disk_tick(self)
        try:
            with self._engine(done) as pd:
                for post_id, md5, ext, size, url, relpath in self.s.iter_pending():
                    if not pd.submit(post_id, md5, ext, size, url, relpath):
                        break
        finally:
            self.s.commit()
        if n > 1:
            log.info("downloaded batch: %d files, %s", prog.files, fmt_bytes(prog.bytes))

    def download_ids(self, ids) -> dict:
        """1.8 (`retry-failed`): download only these posts (those of them that are pending), newest first.
        Other pending files are left alone. Returns {status: count} for the selected posts afterwards."""
        ids = sorted({int(i) for i in ids}, reverse=True)
        n, b = self.s.pending_summary(ids)
        if n:
            log.info("retrying %d failed file(s) (%s) with %d parallel downloads", n, fmt_bytes(b), self.workers)
            emit("phase", name="retrying failed files")
            self._emit_archive()
            emit("queue", files=n, bytes=b)
            prog = Progress(n, b, None)

            def done(job, status, nbytes):
                prog.update(nbytes)
                emit("file_done", post_id=job.post_id, status=status, nbytes=nbytes, size=job.size)
                emit("stats", **self.stats)
                disk_tick(self)
            try:
                with self._engine(done) as pd:
                    for post_id, md5, ext, size, url, relpath in self.s.iter_pending(ids=ids):
                        if not pd.submit(post_id, md5, ext, size, url, relpath):
                            break
            finally:
                self.s.commit()
        return self.s.status_counts(ids)

    def _download_by_priority(self) -> None:
        """Download pending files in queue-rank order, within the selected tier (in parallel)."""
        pq = self.pq
        rebuilt = pq.ensure()
        end = pq.tier_end()
        n, b = pq.pending_in_tier(end)
        tier = pq.tier_progress(end)
        emit("priority", order=self.spec.describe(), tier_end=end,
             ranked=self.s.db.execute("SELECT COUNT(*) FROM queue").fetchone()[0], rebuilt=rebuilt)
        emit("tier", **tier)
        if not n:
            left = self.s.pending_summary()[0]
            if left and (self.spec.top or self.spec.limit_gb or self.spec.filtered()):
                log.info("tier complete (%s); %d other pending files are outside the selected order/filters/limit",
                         self.spec.describe(), left)
            return
        if n > 1:
            log.info("downloading %d files (%s) in %s order, tier = ranks 1-%d, %d parallel downloads", n,
                     fmt_bytes(b), self.spec.order, end, self.workers)
        emit("phase", name=f"downloading files ({self.spec.order} order)")
        self._emit_archive()
        emit("queue", files=n, bytes=b)
        prog = Progress(n, b, None)

        def done(job, status, nbytes):
            prog.update(nbytes)
            if status == "ok":
                tier["done_files"] += 1
                tier["done_bytes"] += job.size or nbytes
            emit("rank", rank=job.meta.get("rank"), tier_end=end)
            emit("file_done", post_id=job.post_id, status=status, nbytes=nbytes, size=job.size)
            emit("tier", **tier)
            emit("stats", **self.stats)
            disk_tick(self)
        try:
            with self._engine(done) as pd:
                for chunk in _chunks(pq.iter_pending(end, batch=100), 100):
                    if self.api_json:
                        pd.pump(0)
                        self._refresh_api_json([r[1] for r in chunk])
                    stop = False
                    for rank, post_id, *_ in chunk:
                        row = self.s.db.execute("SELECT md5, ext, size, file_url, file_path FROM posts WHERE id=? "
                                                "AND file_status='pending'", (post_id,)).fetchone()
                        if row is None:  # e.g. the API says it was deleted meanwhile
                            continue
                        if not pd.submit(post_id, *row, rank=rank):
                            stop = True
                            break
                    if stop:
                        break
        finally:
            self.s.commit()
        if n > 1:
            log.info("downloaded batch: %d files, %s", prog.files, fmt_bytes(prog.bytes))

    def _refresh_api_json(self, ids: list[int]) -> None:
        """One API request per 100 files: fresh per-post JSON for export-seeded posts (the export
        has no per-post sources/relationships objects and may be up to a day old)."""
        need = [i for (i,) in self.s.db.execute(
            f"SELECT id FROM posts WHERE id IN ({','.join('?' * len(ids))}) "
            "AND instr(json, '\"_source\":\"db_export\"') > 0", ids)]
        if not need:
            return
        posts = self.c.posts(None, len(need), "id:" + ",".join(map(str, need)))
        for post in posts:
            if post["id"] in need:
                self.s.merge_api_json(post)
                self.stats["api_json"] += 1
        self.s.commit()

    def paths_for(self, info, relpath: str | None) -> list[str]:
        """[primary, *extra copies]. A saved file's path never changes on its own."""
        post_id = info.id
        if not info.categorized and self.naming.uses_categories and not self._warned_uncategorized:
            self._warned_uncategorized = True
            log.warning("tag categories unknown for export-seeded posts (tags.csv.gz not loaded): "
                        "they are saved under _no_director etc. Load the tags export, then run reorganize.")
        planned = self.naming.plan(info, len(str(self.s.out)))
        if relpath:
            return [relpath] + (self.s.extra_paths(post_id) or [p for p in planned[1:] if p != relpath])
        return planned

    def _local_error(self, e: OSError):
        """Disk full / drive gone / read-only: stop the run (resumable) instead of failing files."""
        from .platform_utils import is_disk_full
        if is_disk_full(e):
            raise e
        why = storage_problem(e, self.s.out)
        if why:
            raise StorageError(why) from e

    # ------------------------------------------------------------------
    def verify_all(self, *, action: str = "delete", hash_workers: int = 0, only_unverified: bool = False,
                   restart: bool = False, batch: int = 1000) -> dict:
        """1.6: check every saved file against the md5 in the database (from posts.csv / the API).

        Hashing runs on `hash_workers` threads (default: one per CPU core; hashlib releases the GIL, so
        this uses several cores). Zero-byte, truncated and mismatched files are deleted (action="delete",
        default) or moved to <out>/quarantine/ (action="quarantine"), and go back to 'pending' together
        with files that are absent, so the next download fetches them again. Resumable: the position is
        checkpointed after every batch; an interrupted verify continues where it stopped (restart=True
        starts over). only_unverified: skip files whose md5 was already checked (inline or by an
        earlier verify) - used by `full` when "verify when finished" is on. Leftover .part files are removed."""
        import os as _os
        from concurrent.futures import ThreadPoolExecutor
        if action not in ("delete", "quarantine"):
            raise ValueError("verify action must be delete or quarantine")
        hash_workers = max(1, min(64, int(hash_workers or (_os.cpu_count() or 2))))
        key = "verify:cursor:" + ("new" if only_unverified else "all")
        res = {"ok": 0, "bad": 0, "absent": 0, "zero_byte": 0, "truncated": 0, "md5_mismatch": 0,
               "deleted": 0, "quarantined": 0, "requeued": 0, "bytes_hashed": 0,
               "part_files_removed": sweep_part_files(self.s.out), "hash_workers": hash_workers}
        cursor = None if restart else self.s.get_state(key)
        if cursor is not None:
            log.info("verify: resuming below post id %s (pass --restart to start over)", cursor)
        q = ("SELECT p.id, p.md5, p.ext, p.file_path, p.size FROM posts p "
             + ("LEFT JOIN verified v ON v.post_id = p.id " if only_unverified else "")
             + "WHERE p.file_status='ok' "
             + ("AND (v.post_id IS NULL OR v.md5 IS NOT p.md5) " if only_unverified else ""))
        total = self.s.db.execute("SELECT COUNT(*) FROM (" + q + (" AND p.id < ?)" if cursor is not None else ")"),
                                  (cursor,) if cursor is not None else ()).fetchone()[0]
        emit("phase", name="verifying checksums")
        log.info("verify: %d files to check with %d hashing threads (%s bad files)", total, hash_workers,
                 "delete" if action == "delete" else "quarantine")
        out = Path(self.s.out)
        t0 = time.monotonic()

        def check(row):
            post_id, md5, ext, rel, size = row
            control.checkpoint()
            p = abspath(out, rel or legacy_relpath(md5, ext))
            try:
                st = p.stat().st_size
            except FileNotFoundError:
                return row, p, "absent", 0
            if st == 0:
                return row, p, "zero_byte", 0
            got = md5_of(p)
            if got == md5:
                return row, p, "ok", st
            return row, p, ("truncated" if size and st < int(size) else "md5_mismatch"), st

        checked = 0
        with ThreadPoolExecutor(hash_workers, thread_name_prefix="verify") as pool:
            while True:
                rows = (self.s.db.execute(q + "ORDER BY p.id DESC LIMIT ?", (batch,)).fetchall() if cursor is None
                        else self.s.db.execute(q + "AND p.id < ? ORDER BY p.id DESC LIMIT ?",
                                               (cursor, batch)).fetchall())
                if not rows:
                    break
                for row, p, verdict, nbytes in pool.map(check, rows):  # results in order; DB writes here only
                    post_id, md5 = row[0], row[1]
                    res["bytes_hashed"] += nbytes
                    if verdict == "ok":
                        res["ok"] += 1
                        self.s.mark_verified(post_id, md5)
                        continue
                    if verdict == "absent":
                        res["absent"] += 1
                    else:
                        res["bad"] += 1
                        res[verdict] += 1
                        try:
                            if action == "quarantine":
                                dst = out / "quarantine" / p.relative_to(out)
                                dst.parent.mkdir(parents=True, exist_ok=True)
                                if dst.exists():
                                    dst = dst.with_name(f"{dst.name}.{int(time.time())}")
                                safe_replace(p, dst)
                                res["quarantined"] += 1
                            else:
                                safe_unlink(p)
                                res["deleted"] += 1
                        except OSError as e:
                            self._local_error(e)
                            raise
                        log.warning("verify: %s %s (post %d) -> %s", verdict.replace("_", " "), p.name, post_id,
                                    "deleted" if action == "delete" else "quarantined")
                    self.s.set_file_status(post_id, "pending")  # not on disk any more: download again
                    self.s.set_file_error(post_id, "verify_" + verdict, str(p))  # 1.7: shown in the report
                    res["requeued"] += 1
                checked += len(rows)
                cursor = rows[-1][0]
                self.s.set_state(key, cursor)
                self.s.commit()
                el = max(1e-6, time.monotonic() - t0)
                log.info("verify: %d / %d checked (%s/s)", checked, total, fmt_bytes(res["bytes_hashed"] / el))
                emit("verify", checked=checked, total=total, bps=round(res["bytes_hashed"] / el),
                     **{k: res[k] for k in ("ok", "bad", "absent")})
        self.s.del_state(key)
        self.s.commit()
        res["seconds"] = round(time.monotonic() - t0, 1)
        return res


def _chunks(it, n: int):
    buf = []
    for x in it:
        buf.append(x)
        if len(buf) >= n:
            yield buf
            buf = []
    if buf:
        yield buf


def _size(size) -> int:
    try:
        return max(0, int(size or 0))
    except (TypeError, ValueError):
        return 0


# ----------------------------------------------------------------------
META_ENDPOINTS = {
    "pools": ("/pools.json", {}),
    "tags": ("/tags.json", {"search[hide_empty]": "yes"}),
    "wiki_pages": ("/wiki_pages.json", {}),
    "artists": ("/artists.json", {}),
}


def fetch_meta(client: Client, store: Store, kind: str, max_items: int | None = None) -> int:
    """Walk pools/tags/wiki_pages newest->oldest with page=b<id>, resumable."""
    path, extra = META_ENDPOINTS[kind]
    key = f"meta[{kind}]:"
    if store.get_state(key + "done"):
        log.info("%s: already complete (delete state key %sdone to refetch)", kind, key)
        return 0
    cursor = store.get_state(key + "cursor")
    n = 0
    while max_items is None or n < max_items:
        limit = PAGE_LIMIT if max_items is None else max(1, min(PAGE_LIMIT, max_items - n))
        params = dict(extra, limit=limit)
        if cursor is not None:
            params["page"] = f"b{cursor}"
        items = client.get_json(path, params)
        if isinstance(items, dict):  # some endpoints wrap empty results, e.g. {"tags": []}
            items = next((v for v in items.values() if isinstance(v, list)), [])
        items = sorted((i for i in items if isinstance(i, dict) and isinstance(i.get("id"), int)),
                       key=lambda x: x["id"], reverse=True)
        if cursor is not None:
            items = [i for i in items if i["id"] < cursor]
        if max_items is not None:
            items = items[: max_items - n]
        if not items:
            store.set_state(key + "done", True)
            store.commit()
            break
        for it in items:
            store.upsert_meta(kind, it)
        n += len(items)
        cursor = items[-1]["id"]
        store.set_state(key + "cursor", cursor)
        store.commit()
        log.info("%s: saved %d (cursor b%d)", kind, n, cursor)
    return n
