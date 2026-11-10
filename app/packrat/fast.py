"""1.6 parallel file downloads ("fast mode").

Design:
  * N worker threads (default 16, 1-64) download files from the static host. Each worker has its own
    keep-alive requests.Session (StaticFetcher); there is no per-request rate limit for static files.
    API requests (gap filling, per-post JSON refresh) still go through Client and its own limiter.
  * Workers never touch SQLite. The calling (main) thread is the single DB writer: it prepares jobs
    (path planning reads the DB), and applies every result (set_file_status / record_file / commit).
  * Each file is streamed to <dest>.part and moved into place with os.replace (atomic): a file at its
    final path is always complete. Interrupted transfers delete their .part; leftovers are swept.
  * Integrity while downloading: the byte count is checked against the expected size; the md5 is
    checked inline only when check_md5 is on (default off; `verify` checks everything afterwards).
  * 403 / 429 / 503 from the static host: that file is marked failed (429/503 are retried a few times
    first) and that worker backs off. After MAX_CONSECUTIVE_403 consecutive 403s the whole run stops
    cleanly (ForbiddenError -> exit code 3).
  * Stop / Pause (control file or GUI) reach every worker: pause blocks before the next request, stop
    aborts transfers within one chunk and deletes their .part files.
"""
from __future__ import annotations

import contextlib
import hashlib
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

from .client import AuthError, ForbiddenError, NotFoundError, StaticBlocked, StaticFetcher
from .events import StopRequested, control, emit
from . import platform_utils
from .platform_utils import ensure_free, free_bytes, link_or_copy, safe_replace, safe_unlink
from .store import abspath, legacy_relpath

log = logging.getLogger("packrat")
DEFAULT_WORKERS = 16
MIN_WORKERS, MAX_WORKERS = 1, 64
MAX_CONSECUTIVE_403 = 20
CHUNK = 1 << 20
STREAM_RETRIES = 3
THROTTLE_RETRIES = 3          # 429/503: tries of the same file before it is marked failed
MAX_WORKER_BACKOFF = 60.0
EMIT_EVERY_S = 0.5
DISK_RECHECK_S = 2.0  # 1.9: low disk space: how often waiting workers look at the free space again


class _Halt(Exception):
    """Internal: another worker hit a fatal error; abandon this file (it stays pending)."""


@dataclass
class Job:
    post_id: int
    md5: str
    ext: str
    size: int
    url: str
    primary: str
    extras: list
    dest: Path
    dup_rel: str | None = None
    meta: dict = field(default_factory=dict)  # caller data (e.g. rank), returned with the result


@dataclass
class Result:
    job: Job
    status: str                   # ok | missing | failed | md5_mismatch | stopped | halted | fatal
    nbytes: int = 0
    stats: dict = field(default_factory=dict)
    extras_ok: list = field(default_factory=list)
    forbidden: bool = False
    throttled: bool = False
    exc: BaseException | None = None
    reason: str = ""              # 1.7: why it failed (http_403, http_404, md5_mismatch ...), for the report
    tries: int = 1                # 1.8: requests made for this file in this run (for failed-downloads.csv)


def clamp_workers(n) -> int:
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = DEFAULT_WORKERS
    return max(MIN_WORKERS, min(MAX_WORKERS, n))


class ParallelDownloader:
    """Use from the DB-owning thread:  with ParallelDownloader(arch, on_result) as pd: pd.submit(row) ..."""

    def __init__(self, arch, on_result, workers: int = DEFAULT_WORKERS, check_md5: bool = False,
                 fetcher: StaticFetcher | None = None, max_consecutive_403: int = MAX_CONSECUTIVE_403):
        self.a, self.on_result = arch, on_result
        self.workers = clamp_workers(workers)
        self.check_md5 = bool(check_md5)
        self.fetcher = fetcher
        self.max_403 = max(1, int(max_consecutive_403))
        self.jobs: queue.Queue = queue.Queue(maxsize=self.workers * 2)
        self.results: queue.Queue = queue.Queue()
        self.halt = threading.Event()
        self.abort: BaseException | None = None
        self.outstanding = 0
        self.consecutive_403 = 0
        self._threads: list[threading.Thread] = []
        self._inflight: dict[int, list] = {}   # worker index -> [done bytes, size, name]
        self._ilock = threading.Lock()
        self._api_lock = threading.Lock()  # file URLs on the API host itself: one at a time, API limiter
        self._done_bytes = 0
        self._t0 = time.monotonic()
        self._samples: list = []
        self._last_emit = 0.0
        self.peak_active = 0
        self._paused_shown = False
        self._disk_lock = threading.Lock()   # 1.9: low disk space -> wait (see _wait_for_space)
        self._disk_waiting = False
        self.disk_pauses = 0
        self._md5s: dict[str, int] = {}   # md5 -> jobs queued / running (duplicates wait, then hardlink)

    # ---- lifecycle (main thread) ---------------------------------------------------------------
    def __enter__(self):
        for i in range(self.workers):
            t = threading.Thread(target=self._worker, args=(i,), name=f"download-{i + 1}", daemon=True)
            t.start()
            self._threads.append(t)
        return self

    def __exit__(self, et, ev, tb):
        try:
            if et is None:
                self.finish()
        finally:
            if et is not None or self.abort is not None:
                self.halt.set()
            for _ in self._threads:
                self._put_sentinel()
            for t in self._threads:
                t.join()
            while not self.results.empty():  # results of the last jobs (after an abort)
                self._apply(self.results.get_nowait())
        if et is None and self.abort is not None:
            raise self.abort
        return False

    def _put_sentinel(self):
        while True:
            try:
                self.jobs.put(None, timeout=0.2)
                return
            except queue.Full:
                self._drop_queued()

    def _drop_queued(self):
        """After an abort: take not-yet-started jobs back (they simply stay pending in the DB)."""
        try:
            while True:
                j = self.jobs.get_nowait()
                if j is not None:
                    self.outstanding -= 1
                    self._md5s.pop(j.md5, None)
        except queue.Empty:
            pass

    def submit(self, post_id, md5, ext, size, url, relpath, **meta) -> bool:
        """Queue one pending file. Returns False once the run is being stopped (caller stops feeding)."""
        if self._stopping():
            return False
        a = self.a
        while md5 in self._md5s:  # the same file is already being fetched for another post: wait for it,
            self.pump(0.1)        # then this post reuses it (hardlink) instead of a second download
            if self._stopping():
                return False
        info = a.s.post_info(post_id)
        paths = a.paths_for(info, relpath)
        dest = abspath(a.s.out, paths[0])
        job = Job(post_id, md5, ext, _size(size), url, paths[0], paths[1:], dest,
                  a.s.find_local_copy(md5, post_id), meta)
        while True:
            self.pump(0)
            if self._stopping():
                return False
            try:
                self.jobs.put(job, timeout=0.1)
                self.outstanding += 1
                self._md5s[md5] = self._md5s.get(md5, 0) + 1
                return True
            except queue.Full:
                self.pump(0.1)

    def finish(self):
        while self.outstanding > 0:
            if self._stopping():
                self.halt.set()
                self._drop_queued()
            self.pump(0.25)

    def _stopping(self) -> bool:
        if self.abort is None and control.stopping:
            self.abort = StopRequested("stop requested")
        return self.abort is not None

    def pump(self, timeout: float) -> None:
        """Apply finished results (DB writes happen here, in the calling thread) and emit progress."""
        try:
            r = self.results.get(timeout=timeout) if timeout else self.results.get_nowait()
            self._apply(r)
            while True:
                self._apply(self.results.get_nowait())
        except queue.Empty:
            pass
        self._emit_workers()
        if control.paused != self._paused_shown:
            self._paused_shown = control.paused
            if control.paused:
                log.info("paused (transfers already running finish first)")
                emit("phase", name="paused")
            else:
                log.info("resumed")
                emit("phase", name="resumed")

    def _apply(self, r: Result) -> None:
        self.outstanding -= 1
        a, j = self.a, r.job
        if self._md5s.get(j.md5, 0) <= 1:
            self._md5s.pop(j.md5, None)
        else:
            self._md5s[j.md5] -= 1
        for k, v in r.stats.items():
            a.stats[k] = a.stats.get(k, 0) + v
        if r.status in ("stopped", "halted"):
            if r.status == "stopped" and self.abort is None:
                self.abort = StopRequested("stop requested")
            return  # stays pending
        if r.status == "fatal":
            if self.abort is None:
                self.abort = r.exc
            self.halt.set()
            return
        self._done_bytes += r.nbytes
        if r.status == "ok":
            had = a.s.file_error(j.post_id)  # 1.8: a file that failed before and is now saved
            a.s.record_file(j.post_id, j.md5, j.primary, r.extras_ok)
            if self.check_md5 and r.nbytes:
                a.s.mark_verified(j.post_id, j.md5)
            if had:
                self._log_failure(j, r, had[1], event="recovered")
        else:
            a.s.set_file_status(j.post_id, r.status)
            n = a.s.set_file_error(j.post_id, r.reason or r.status, j.url)
            self._log_failure(j, r, n)
        if r.forbidden:
            self.consecutive_403 += 1
            if self.consecutive_403 >= self.max_403 and self.abort is None:
                self.abort = ForbiddenError(
                    f"{self.consecutive_403} file downloads in a row were refused with HTTP 403 by the file "
                    f"server. Stopped cleanly; files already saved are kept and the refused ones are marked "
                    f"'failed' (use 'Retry failed' later). Check that your User-Agent/username is right and "
                    f"wait before running again.")
                log.error("%s", self.abort)
                self.halt.set()
        elif r.status in ("ok", "missing"):
            self.consecutive_403 = 0
        a.s.maybe_commit()
        self.on_result(j, r.status, r.nbytes)

    def _log_failure(self, j: Job, r: Result, total: int, event: str = "failed") -> None:
        """1.8: failed-downloads.csv / .log (+ one line in the run log / panel)."""
        from .failures import directors_of, safe_url
        a = self.a
        try:
            director = directors_of(a.s, j.post_id, getattr(a.naming, "director_exclude", ()))
        except Exception:  # noqa: BLE001 - the log must never stop the run
            director = ""
        fl = getattr(a.s, "failures", None)
        if fl is not None:
            fl.record(post_id=j.post_id, md5=j.md5, ext=j.ext, director=director, status=r.status,
                      reason=r.reason or r.status, tries=r.tries, failures_total=total, url=j.url, event=event)
        if event == "failed":
            log.warning("download failed: post %d (%s, %s) - %s, %d tr%s this run, failure #%d -> %s", j.post_id,
                        director or "?", j.md5, r.reason or r.status, r.tries, "y" if r.tries == 1 else "ies",
                        total, safe_url(j.url))
        emit("file_failed" if event == "failed" else "file_recovered", post_id=j.post_id,
             reason=r.reason or r.status, tries=r.tries, failures_total=total)

    # ---- progress ------------------------------------------------------------------------------
    def inflight(self) -> tuple[int, int]:
        with self._ilock:
            return sum(v[0] for v in self._inflight.values()), len(self._inflight)

    def _emit_workers(self, force=False):
        now = time.monotonic()
        if not force and now - self._last_emit < EMIT_EVERY_S:
            return
        self._last_emit = now
        inflight, active = self.inflight()
        self.peak_active = max(self.peak_active, active)
        total = self._done_bytes + inflight
        self._samples.append((now, total))
        while len(self._samples) > 2 and now - self._samples[0][0] > 5:
            self._samples.pop(0)
        (t1, b1), (t2, b2) = self._samples[0], self._samples[-1]
        bps = (b2 - b1) / (t2 - t1) if t2 > t1 else 0.0
        emit("workers", active=active, workers=self.workers, inflight=inflight, bps=round(bps),
             avg_bps=round(total / max(1e-6, now - self._t0)))

    # ---- worker threads ------------------------------------------------------------------------
    def _worker(self, idx: int) -> None:
        k403 = 0
        while True:
            job = self.jobs.get()
            if job is None:
                return
            try:
                if self.halt.is_set():
                    raise _Halt()
                self._checkpoint()  # Pause blocks here; Stop raises
                with self._ilock:
                    self._inflight[idx] = [0, job.size, job.dest.name]
                r = self._fetch(job, idx)
            except StopRequested:
                r = Result(job, "stopped")
            except _Halt:
                r = Result(job, "halted")
            except BaseException as e:  # disk full, drive gone, 403 from the API host...: stop (resumable)
                self.halt.set()         # the other workers stop before their next request
                r = Result(job, "fatal", exc=e)
            finally:
                with self._ilock:
                    self._inflight.pop(idx, None)
            self.results.put(r)
            if r.forbidden or r.throttled:
                k403 = k403 + 1 if r.forbidden else k403
                wait = min(MAX_WORKER_BACKOFF, 2.0 ** max(1, k403))
                try:
                    self._sleep(wait)
                except (StopRequested, _Halt):
                    pass
            elif r.status == "ok":
                k403 = 0

    def _checkpoint(self):
        while True:
            control.check_stop()
            if self.halt.is_set():
                raise _Halt()
            if not control.paused:
                return
            time.sleep(0.2)

    def _wait_for_space(self, out: Path, need: int) -> None:
        """1.9: before each file, make sure it fits above the --min-free-gb reserve. on_low_disk=stop: stop
        cleanly (DiskFullError, exit 4, as in 1.8). pause (default): every worker waits here, the run
        reports 'paused: low disk space' (panel notification) and continues by itself as soon as space is
        freed; the panel's Resume button asks for an immediate re-check (control word 'recheck')."""
        a = self.a
        reserve = int(getattr(a, "min_free_bytes", 0) or 0)
        if getattr(a, "on_low_disk", "pause") == "stop":
            ensure_free(out, need, reserve)
            return
        while True:
            free = platform_utils.free_bytes(out)
            if free >= need + reserve:
                with self._disk_lock:
                    if self._disk_waiting:
                        self._disk_waiting = False
                        log.info("disk space OK again (%.1f GiB free): downloads continue", free / 2**30)
                        emit("disk_ok", free=free)
                        emit("phase", name="resumed")
                return
            with self._disk_lock:
                first = not self._disk_waiting
                self._disk_waiting = True
                self.disk_pauses += first
            if first:
                log.warning("PAUSED - low disk space at %s: %.1f GiB free, the next file needs %.1f MiB plus "
                            "the %.1f GiB reserve (--min-free-gb). Free some space (or lower the reserve and "
                            "restart); downloads continue by themselves.", out, free / 2**30, need / 2**20,
                            reserve / 2**30)
                emit("disk_low", free=free, need=need, reserve=reserve)
                emit("phase", name="paused: low disk space")
            self._disk_sleep(DISK_RECHECK_S)

    def _disk_sleep(self, seconds: float) -> None:
        left = seconds
        while left > 0:
            control.check_stop()
            if self.halt.is_set():
                raise _Halt()
            if control.take_recheck():
                return
            step = min(left, 0.2)
            time.sleep(step)
            left -= step

    def _sleep(self, seconds: float):
        left = seconds
        while left > 0:
            control.check_stop()
            if self.halt.is_set():
                raise _Halt()
            step = min(left, 0.2)
            time.sleep(step)
            left -= step

    def _matches(self, p: Path, md5: str, size: int) -> tuple[bool, str | None]:
        """Is the file at p the expected one? md5 when check_md5 is on or the size is unknown, else size."""
        st = p.stat().st_size
        if self.check_md5 or not size:
            got = _md5_of(p)
            return got == md5, got
        return st == size, None

    def _fetch(self, j: Job, idx: int) -> Result:
        a = self.a
        out = a.s.out
        try:
            if j.dest.exists():
                if j.dest.stat().st_size == 0:
                    safe_unlink(j.dest)
                else:
                    ok, got = self._matches(j.dest, j.md5, j.size)
                    if ok:
                        return self._place_extras(Result(j, "ok", stats={"already_present": 1}))
                    from .archiver import set_aside
                    kept = set_aside(j.dest, got or _md5_of(j.dest))
                    log.warning("%s has different content; kept it as %s and downloading the expected file",
                                j.dest.name, kept.name)
            legacy = abspath(out, legacy_relpath(j.md5, j.ext))
            dup = abspath(out, j.dup_rel) if j.dup_rel else None
            for src, how in ((legacy, "move"), (dup, "link")):
                if not src or src == j.dest or not src.exists() or not self._matches(src, j.md5, j.size)[0]:
                    continue
                j.dest.parent.mkdir(parents=True, exist_ok=True)
                if how == "move":
                    safe_replace(src, j.dest)
                    st = {"already_present": 1}
                else:
                    link_or_copy(src, j.dest, "hardlink")
                    st = {"reused": 1}
                return self._place_extras(Result(j, "ok", stats=st))
            self._wait_for_space(out, j.size or 0)
            j.dest.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            a._local_error(e)
            raise
        tmp = j.dest.with_name(j.dest.name + ".part")
        stream_errors = mismatches = throttled = vanished = tries = 0
        while True:
            tries += 1
            h = hashlib.md5() if self.check_md5 else None
            n = 0
            serial = self._api_lock if self.fetcher._is_api_host(j.url) else contextlib.nullcontext()
            try:
                with serial:
                    if self.halt.is_set():
                        raise _Halt()
                    try:
                        r = self.fetcher.get(j.url)
                    except (ForbiddenError, AuthError):
                        self.halt.set()  # before the lock is released: no request may follow
                        raise
                    try:
                        with open(tmp, "wb") as f:
                            for chunk in r.iter_content(CHUNK):
                                f.write(chunk)
                                if h is not None:
                                    h.update(chunk)
                                n += len(chunk)
                                with self._ilock:
                                    if idx in self._inflight:
                                        self._inflight[idx][0] = n
                                control.check_stop()
                                if self.halt.is_set():
                                    raise _Halt()
                    finally:
                        r.close()
            except NotFoundError:
                safe_unlink(tmp)
                log.warning("file 404: %s", j.url)
                return Result(j, "missing", stats={"missing": 1}, reason="http_404", tries=tries)
            except ForbiddenError:
                safe_unlink(tmp)  # 403 from the API host itself (Client.stream): stop at once, as before
                raise
            except StaticBlocked as e:
                safe_unlink(tmp)
                status = e.status
                if status == 403:
                    log.warning("file refused (HTTP 403): %s", j.url)
                    return Result(j, "failed", stats={"failed": 1}, forbidden=True, reason="http_403", tries=tries)
                throttled += 1
                if throttled > THROTTLE_RETRIES:
                    log.warning("giving up on %s for now: HTTP %s %d times", j.url, status, throttled)
                    return Result(j, "failed", stats={"failed": 1}, throttled=True, reason=f"http_{status}", tries=tries)
                wait = e.retry_after if e.retry_after is not None else min(MAX_WORKER_BACKOFF, 5.0 * 2 ** throttled)
                log.warning("HTTP %s for %s; this worker waits %.0fs (retry %d/%d)", status, j.dest.name, wait,
                            throttled, THROTTLE_RETRIES)
                self._sleep(wait)
                continue
            except FileNotFoundError as e:
                safe_unlink(tmp)
                a._local_error(e)
                vanished += 1
                if vanished > 1:
                    raise
                j.dest.parent.mkdir(parents=True, exist_ok=True)
                continue
            except (requests.RequestException, OSError) as e:
                safe_unlink(tmp)
                if isinstance(e, OSError) and not isinstance(e, requests.RequestException):
                    a._local_error(e)
                    raise
                stream_errors += 1
                if stream_errors > STREAM_RETRIES:
                    log.error("giving up on %s after %d broken transfers: %s", j.url, stream_errors, e)
                    return Result(j, "failed", stats={"failed": 1}, reason="network_error", tries=tries)
                self._sleep(2 ** stream_errors)
                continue
            except BaseException:
                safe_unlink(tmp)
                raise
            # complete body received: size check always, md5 when enabled
            if h is not None:
                good = h.hexdigest() == j.md5
            elif j.size and n != j.size:
                good = _md5_of(tmp) == j.md5   # size in the metadata may be stale: md5 decides
            else:
                good = True
            if good:
                try:
                    safe_replace(tmp, j.dest)  # atomic: the final path is absent or complete
                except FileNotFoundError as e:
                    # folder deleted/renamed between the last write and the rename: re-create it and refetch once
                    safe_unlink(tmp)
                    a._local_error(e)
                    vanished += 1
                    if vanished > 1:
                        raise
                    j.dest.parent.mkdir(parents=True, exist_ok=True)
                    continue
                except OSError as e:
                    safe_unlink(tmp)
                    a._local_error(e)
                    raise
                return self._place_extras(Result(j, "ok", n, stats={"downloaded": 1, "bytes": n}))
            safe_unlink(tmp)
            mismatches += 1
            log.warning("%s for %s (attempt %d, got %d bytes, expected %s)",
                        "md5 mismatch" if h is not None else "wrong size", j.url, mismatches, n, j.size)
            if mismatches >= 2:
                return Result(j, "md5_mismatch", stats={"md5_mismatch": 1},
                              reason="md5_mismatch" if h is not None else "wrong_size", tries=tries)

    def _place_extras(self, r: Result) -> Result:
        j, a = r.job, self.a
        for rel in j.extras:  # multi_director = copy/hardlink
            x = abspath(a.s.out, rel)
            try:
                if not (x.exists() and x.stat().st_size == j.dest.stat().st_size):
                    link_or_copy(j.dest, x, "hardlink" if a.naming.multi_director == "hardlink" else "copy")
                r.extras_ok.append(rel)
            except OSError as e:
                a._local_error(e)
                log.warning("could not place extra copy %s: %s", rel, e)
        return r


def _md5_of(p: Path) -> str:
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(CHUNK), b""):
            h.update(b)
    return h.hexdigest()


def _size(size) -> int:
    try:
        return max(0, int(size or 0))
    except (TypeError, ValueError):
        return 0


def disk_tick(arch) -> None:
    if time.monotonic() - arch._last_disk > 5:
        arch._last_disk = time.monotonic()
        emit("disk", free=free_bytes(arch.s.out))
