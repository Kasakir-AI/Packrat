"""Optional progress window (stdlib tkinter). Start with --gui, gui = yes in the ini, or run_gui.bat.

Threading model (the important part for reviewers):
  * The archiver runs in ONE worker thread, exactly the same code as the console mode.
  * The worker only calls events.emit(...) and logging; the GUI's sink and log handler just do
    queue.put_nowait(). The worker never touches a Tk widget (Tk is not thread-safe).
  * The Tk main thread drains the queue every 200 ms (root.after), folds events into GuiModel
    (plain Python, unit-tested without a display) and redraws from model.snapshot().
  * Pause / Stop only set threading.Events in events.control; the worker honours them before the
    next request (pause) or between 1 MiB chunks / during waits (stop). Stop is a normal,
    checkpointed exit: the partial .part file is removed, the DB is committed, the lock released.
  * Closing the window = Stop. Closing it again while still stopping hides the window; the
    process keeps finishing in the console. If Tk itself fails, the worker carries on and the run
    continues in the console: a display problem can never break or corrupt a download.
"""
from __future__ import annotations

import collections
import logging
import os
import queue
import signal
import sys
import threading
import time

from . import __version__
from .events import add_sink, control, remove_sink

log = logging.getLogger("packrat")
POLL_MS = 200
MAX_EVENTS_PER_POLL = 5000
LOG_LINES = 400


def fmt_bytes(n) -> str:
    n = float(n or 0)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or unit == "TiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TiB"


def fmt_duration(s) -> str:
    if s is None or s != s or s < 0 or s > 400 * 86400:
        return "--"
    s = int(s)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{d}d {h:02d}:{m:02d}:{s:02d}" if d else f"{h:02d}:{m:02d}:{s:02d}"


class GuiModel:
    """All progress state, updated from events. No Tk here, so it is testable headless."""
    SPEED_WINDOW_S = 8.0

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.t0 = clock()
        self.phase = "starting"
        self._phase_before_pause = None
        self.cmd = ""
        self.out = ""
        self.total_files = self.total_bytes = 0
        self.files_done = self.bytes_done = 0
        self.cur = None  # dict: post_id, name, path, size, done, director
        self.stats = {}
        self.requests = 0
        self.free = None
        self.net = "ok"
        self.last_error = ""
        self.log = collections.deque(maxlen=LOG_LINES)
        self.new_log = []
        self.crawl_posts = 0
        self.crawl_last_id = None
        self.seed_rows = 0
        self.verify = None
        self.reorg = None
        self.done = False
        self.exit_code = None
        self.order = ""          # priority mode (1.3): description of order + filters
        self.ranked = 0          # files in the ranked queue (all that match the filters)
        self.tier = None         # dict: tier_end, files, bytes, done_files, done_bytes
        self.report = None       # 1.7: dict from the "report" event (summary, txt, html, missing_csv ...)
        self.failed_files = 0    # 1.8: files that failed this run (file_failed events)
        self.recovered_files = 0  # 1.8: earlier failures saved this run
        self.failures = None     # 1.8: end-of-run summary (failed-downloads.csv / .log paths)
        self.rank = None         # rank of the file being downloaded
        self.workers = None      # 1.6 parallel downloads: dict active, workers, inflight
        self._samples = collections.deque()  # (time, cumulative bytes)
        self._first_byte_t = None
        self.archive = None      # 1.9: whole-archive totals at the start of the run ("archive" event)
        self.ok_files = self.ok_bytes = 0   # 1.9: files saved this run (for the dashboard)
        self._fsamples = collections.deque()  # 1.9: (time, files finished) for files/s
        self.disk_low = None     # 1.9: dict free/need/reserve while downloads wait for disk space

    # ---------------------------------------------------------------
    def apply(self, kind: str, d: dict) -> None:
        h = getattr(self, "_on_" + kind, None)
        if h is not None:
            h(d)

    def _on_config(self, d):
        self.cmd, self.out = d.get("cmd", ""), d.get("out", "")

    def _on_phase(self, d):
        name = str(d.get("name", ""))
        if name.startswith("paus"):
            if self._phase_before_pause is None:
                self._phase_before_pause = self.phase
            self.phase = name
        elif name == "resumed":
            self.phase = self._phase_before_pause or self.phase
            self._phase_before_pause = None
        else:
            self.phase, self._phase_before_pause = name, None

    def _on_queue(self, d):  # download_pending may run several times per run (one batch per page)
        self.total_files = self.files_done + int(d.get("files") or 0)
        self.total_bytes = self.bytes_done + int(d.get("bytes") or 0)

    def _on_file_start(self, d):
        self.cur = {"post_id": d.get("post_id"), "name": d.get("name", ""), "path": d.get("path", ""),
                    "size": int(d.get("size") or 0), "done": 0, "director": d.get("director", "")}

    def _on_file_progress(self, d):
        if self.cur is None:  # export downloads report progress without a file_start
            self.cur = {"post_id": None, "name": d.get("name", ""), "path": "", "size": 0, "done": 0,
                        "director": ""}
        self.cur["done"] = int(d.get("done") or 0)
        self.cur["size"] = max(int(d.get("size") or 0), self.cur["done"])
        self._sample()

    def _on_file_done(self, d):
        self.files_done += 1
        # count the expected size so the bar reaches 100% even for skipped/failed files
        self.bytes_done += int(d.get("size") or d.get("nbytes") or 0)
        self.total_files = max(self.total_files, self.files_done)
        self.total_bytes = max(self.total_bytes, self.bytes_done)
        self._transferred_done = getattr(self, "_transferred_done", 0) + int(d.get("nbytes") or 0)
        self.cur = None
        if d.get("status") == "ok":
            self.ok_files += 1
            self.ok_bytes += int(d.get("size") or d.get("nbytes") or 0)
        now = self.clock()
        self._fsamples.append((now, self.files_done))
        while len(self._fsamples) > 2 and now - self._fsamples[0][0] > self.FILES_WINDOW_S:
            self._fsamples.popleft()
        self._sample()

    # ---- 1.9 dashboard ----------------------------------------------------------------------
    FILES_WINDOW_S = 60.0

    def _on_archive(self, d):
        self.archive = {k: int(d.get(k) or 0) for k in (
            "expected_files", "expected_bytes", "done_files", "done_bytes", "pending_files", "pending_bytes",
            "failed_files", "failed_bytes", "no_url_files")}
        # 2.0: the snapshot already contains every file saved so far; only files saved after it are added on
        # top (the 1.9 code reset the run counters instead, and the CLI sent the snapshot only once, before
        # the posts export was loaded - so the totals stayed at the handful of rows of the old database).
        self._arch_base = (self.ok_files, self.ok_bytes)

    def _on_disk_low(self, d):
        self.disk_low = {k: d.get(k) for k in ("free", "need", "reserve")}
        if d.get("free") is not None:
            self.free = d.get("free")

    def _on_disk_ok(self, d):
        self.disk_low = None
        if d.get("free") is not None:
            self.free = d.get("free")

    def files_rate(self) -> float:
        """Files finished per second over the last minute (0 when stalled)."""
        if len(self._fsamples) >= 2:
            (t1, f1), (t2, f2) = self._fsamples[0], self._fsamples[-1]
            if self.clock() - t2 > max(self.SPEED_WINDOW_S, 3 * (t2 - t1) / max(1, f2 - f1)):
                return 0.0
            if t2 > t1:
                return (f2 - f1) / (t2 - t1)
        el = self.clock() - self.t0
        return self.files_done / el if self.files_done and el > 1 else 0.0

    def dashboard(self) -> dict:
        """Whole-archive progress: files done / expected, GiB saved, MB/s + files/s now, failures, ETA
        for everything still to download at the current pace. Empty dict before the 'archive' event."""
        a = self.archive
        if not a:
            return {}
        base_f, base_b = getattr(self, "_arch_base", (0, 0))
        done_f = a["done_files"] + max(0, self.ok_files - base_f)
        done_b = a["done_bytes"] + max(0, self.ok_bytes - base_b)
        # never show more saved than expected: if the snapshot is older than the download queue (the files
        # still queued in this run are not in it), the expected totals are widened to saved + still queued
        q_f = max(0, self.total_files - self.files_done) if not self.done else 0
        q_b = max(0, self.total_bytes - self.bytes_done) if not self.done else 0
        exp_f = max(a["expected_files"], done_f + q_f + a["no_url_files"])
        exp_b = max(a["expected_bytes"], done_b + q_b)
        frac = done_f / exp_f if exp_f else 0.0
        left_f = max(0, exp_f - done_f - a["no_url_files"])
        left_b = max(0, exp_b - done_b)
        fr, bps = self.files_rate(), self.speed_avg()
        known = [x for x in ((left_f / fr) if fr > 0 else None, (left_b / bps) if bps > 0 else None)
                 if x is not None]
        eta = max(known) if known and left_f else None
        failed_total = a["failed_files"] + self.failed_files - self.recovered_files
        return {
            "dash_frac": min(1.0, frac),
            "dash_files": f"{done_f:,} / {exp_f:,} files ({frac * 100:.1f}%)",
            "dash_saved": f"{fmt_bytes(done_b)} of {fmt_bytes(exp_b)} saved",
            "dash_run": f"{self.ok_files:,} files, {fmt_bytes(self.ok_bytes)}",
            "dash_rate": f"{fmt_bytes(self.speed_now())}/s   {fr:.2f} files/s",
            "dash_failed": (f"{self.failed_files:,} now, {max(0, failed_total):,} to retry"),
            "dash_eta": ("--" if eta is None else fmt_duration(eta)) + (f"  ({left_f:,} left)" if left_f else ""),
            "dash_failed_n": self.failed_files,
            "dash_disk_low": bool(self.disk_low),
        }

    def _on_workers(self, d):  # 1.6: periodic summary from the parallel downloader
        self.workers = {"active": int(d.get("active") or 0), "workers": int(d.get("workers") or 0),
                        "inflight": int(d.get("inflight") or 0)}
        self._sample()

    def _on_stats(self, d):
        self.stats = dict(d)

    def _on_request(self, d):
        self.requests = int(d.get("count") or self.requests + 1)

    def _on_disk(self, d):
        self.free = d.get("free")

    def _on_net(self, d):
        if d.get("state") == "ok":
            self.net = "ok"
        else:
            self.net = (f"waiting {fmt_duration(d.get('retry_in'))} ({d.get('why', '')}); "
                        f"down for {fmt_duration(d.get('waited'))}")
            self.last_error = str(d.get("why", ""))

    def _on_priority(self, d):
        self.order, self.ranked = str(d.get("order") or ""), int(d.get("ranked") or 0)

    def _on_tier(self, d):
        self.tier = {k: int(d.get(k) or 0) for k in ("tier_end", "files", "bytes", "done_files", "done_bytes")}

    def _on_rank(self, d):
        self.rank = d.get("rank")

    def _on_crawl(self, d):
        self.crawl_posts, self.crawl_last_id = d.get("posts", 0), d.get("last_id")

    def _on_seed(self, d):
        self.seed_rows = d.get("rows", 0)

    def _on_verify(self, d):
        self.verify = dict(d)

    def _on_report(self, d):
        self.report = dict(d)

    def _on_file_failed(self, d):
        self.failed_files += 1

    def _on_file_recovered(self, d):
        self.recovered_files += 1

    def _on_failures(self, d):
        self.failures = dict(d)

    def _on_reorganize(self, d):
        self.reorg = dict(d)

    def _on_log(self, d):
        line = d.get("msg", "")
        self.log.append(line)
        self.new_log.append(line)
        if d.get("level", 0) >= logging.WARNING:
            self.last_error = line.split(" ", 1)[-1][:300]  # drop the time stamp

    def _on_done(self, d):
        self.done, self.exit_code = True, d.get("code")
        self.phase = {0: "finished", 130: "stopped (resume any time)", 3: "STOPPED on 403 (see log)"}.get(
            self.exit_code, f"ended with errors (exit code {self.exit_code}; see log)")
        self.cur = None

    # ---------------------------------------------------------------
    def transferred(self) -> int:
        return (getattr(self, "_transferred_done", 0) + (self.cur or {}).get("done", 0)
                + (self.workers or {}).get("inflight", 0))

    def _sample(self):
        now, b = self.clock(), self.transferred()
        if b and self._first_byte_t is None:
            self._first_byte_t, self._first_bytes = now, 0
        self._samples.append((now, b))
        while len(self._samples) > 2 and now - self._samples[0][0] > self.SPEED_WINDOW_S:
            self._samples.popleft()

    def speed_now(self) -> float:
        if len(self._samples) < 2:
            return 0.0
        (t1, b1), (t2, b2) = self._samples[0], self._samples[-1]
        if self.clock() - t2 > self.SPEED_WINDOW_S:  # stalled (waiting / paused)
            return 0.0
        return max(0.0, (b2 - b1) / (t2 - t1)) if t2 > t1 else 0.0

    def speed_avg(self) -> float:
        if self._first_byte_t is None:
            return 0.0
        dt = self.clock() - self._first_byte_t
        return self.transferred() / dt if dt > 1 else 0.0

    def eta(self):
        if not self.total_files or self.files_done >= self.total_files:
            return None
        # the larger of "bytes left / speed" and "files left / file rate" (the 1 request/s limit
        # makes the file count the bottleneck for small files), like the console progress line
        avg = self.speed_avg()
        left_b = (self.total_bytes - self.bytes_done - (self.cur or {}).get("done", 0)
                  - (self.workers or {}).get("inflight", 0))
        by_bytes = left_b / avg if avg > 0 and self.total_bytes and left_b > 0 else None
        el = self.clock() - self.t0
        by_files = el / self.files_done * (self.total_files - self.files_done) if self.files_done else None
        known = [x for x in (by_bytes, by_files) if x is not None]
        return max(known) if known else None

    def snapshot(self) -> dict:
        st = self.stats
        cur_b = (self.cur or {}).get("done", 0) + (self.workers or {}).get("inflight", 0)
        frac_b = (self.bytes_done + cur_b) / self.total_bytes if self.total_bytes else 0.0
        frac_f = self.files_done / self.total_files if self.total_files else 0.0
        overall = min(1.0, frac_b if self.total_bytes else frac_f)
        s = {
            "title": f"Packrat {__version__}" + (f" - {self.cmd}" if self.cmd else ""),
            "phase": self.phase,
            "overall": overall,
            "overall_text": (f"{self.files_done:,} / {self.total_files:,} files   |   "
                             f"{fmt_bytes(self.bytes_done + cur_b)} / {fmt_bytes(self.total_bytes)}   "
                             f"({overall * 100:.1f}%)") if self.total_files else self._idle_text(),
            "speed": fmt_bytes(self.speed_now()) + "/s",
            "avg": fmt_bytes(self.speed_avg()) + "/s",
            "eta": fmt_duration(self.eta()),
            "elapsed": fmt_duration(self.clock() - self.t0),
            "requests": f"{self.requests:,}",
            "free": fmt_bytes(self.free) if self.free is not None else "--",
            "net": self.net,
            "downloaded": f"{st.get('downloaded', 0):,}",
            "skipped": f"{st.get('already_present', 0) + st.get('reused', 0):,}",
            "failed": f"{st.get('failed', 0) + st.get('md5_mismatch', 0):,}",
            "missing": f"{st.get('missing', 0):,}",
            "no_url": f"{st.get('no_url', 0):,}",
            "posts": f"{st.get('posts', self.crawl_posts):,}",
            "last_error": self.last_error or "none",
            "out": self.out,
            "done": self.done,
            "order": self.order or "--",
        }
        s.update(self.dashboard())
        t = self.tier
        if t and t["files"]:
            frac = t["done_bytes"] / t["bytes"] if t["bytes"] else t["done_files"] / t["files"]
            label = "all matching" if t["files"] >= self.ranked else f"top {t['files']:,}"
            s["tier_frac"] = min(1.0, frac)
            s["tier_text"] = (f"tier {label} of {self.ranked:,} ranked:  {t['done_files']:,} / {t['files']:,} files   "
                              f"{fmt_bytes(t['done_bytes'])} / {fmt_bytes(t['bytes'])}   ({min(1.0, frac) * 100:.1f}%)")
        else:
            s["tier_frac"], s["tier_text"] = 0.0, ("(default order: newest first)" if not self.order else "")
        s["rank_text"] = (f"rank {self.rank:,} of {t['files']:,} in tier" if self.cur and self.rank and t else "")
        if self.cur:
            c = self.cur
            s["file_head"] = (f"post #{c['post_id']}   director: {c['director'] or '-'}"
                              + (f"   |   {s['rank_text']}" if s["rank_text"] else "")
                              if c["post_id"] is not None else "export download")
            s["file_name"] = c["name"]
            s["file_path"] = c["path"]
            s["file_frac"] = min(1.0, c["done"] / c["size"]) if c["size"] else 0.0
            s["file_text"] = f"{fmt_bytes(c['done'])} / {fmt_bytes(c['size']) if c['size'] else '?'}"
        elif self.workers and self.workers["active"] and not self.done:
            w = self.workers
            s.update(file_head=f"{w['active']} of {w['workers']} parallel downloads active",
                     file_name=f"{fmt_bytes(w['inflight'])} in flight", file_path="", file_frac=0.0,
                     file_text=f"{s['speed']} total")
        else:
            s.update(file_head="(no file in progress)", file_name="", file_path="", file_frac=0.0, file_text="")
        return s

    def _idle_text(self) -> str:
        if self.seed_rows:
            return f"seeding: {self.seed_rows:,} export rows read"
        if self.verify:
            v = self.verify
            tot = f" / {v['total']:,}" if v.get("total") else ""
            spd = f", {fmt_bytes(v['bps'])}/s" if v.get("bps") else ""
            return (f"verified {v.get('checked', 0):,}{tot} files ({v.get('bad', 0)} bad, "
                    f"{v.get('absent', 0)} absent{spd})")
        if self.reorg:
            return "reorganize: " + ", ".join(f"{k} {v:,}" for k, v in self.reorg.items())
        if self.crawl_posts:
            return f"metadata: {self.crawl_posts:,} posts saved (last id {self.crawl_last_id})"
        return "waiting for work..."


class QueueLogHandler(logging.Handler):
    def __init__(self, q):
        super().__init__(logging.INFO)
        self.q = q
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))

    def emit(self, record):
        try:
            self.q.put_nowait(("log", {"msg": self.format(record), "level": record.levelno}))
        except queue.Full:
            pass
        except Exception:
            self.handleError(record)


def _bmp_safe(s: str, tk_version: float) -> str:
    """Tk < 8.7 cannot display characters outside the BMP (emoji); replace them instead of failing."""
    if tk_version >= 8.7:
        return s
    return "".join(ch if ord(ch) <= 0xFFFF else "\ufffd" for ch in s)


class ArchiverWindow:
    def __init__(self, root, q: queue.Queue, model: GuiModel, close_when_done: float | None = None,
                 start_args=None, on_start=None):
        import tkinter as tk
        from tkinter import ttk
        from tkinter.scrolledtext import ScrolledText
        self.tk, self.root, self.q, self.m = tk, root, q, model
        self.tkv = float(getattr(tk, "TkVersion", 8.6))
        self.closes = 0
        self.close_when_done = close_when_done  # seconds; None = keep the window open to read results
        self._closing = False
        self.start_args, self.on_start = start_args, on_start
        self.started = start_args is None
        root.title(f"Packrat {__version__}")
        root.minsize(720, 560)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.report_callback_exception = self._callback_error
        pad = {"padx": 8, "pady": 3}
        f = ttk.Frame(root, padding=8)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)
        self.v = {}

        def var(key):
            self.v[key] = tk.StringVar(value="")
            return self.v[key]

        def font(base, delta=0, bold=False):  # derive from Tk's named fonts (a family tuple would not)
            try:
                import tkinter.font as tkfont
                fnt = tkfont.nametofont(base).copy()
                fnt.configure(size=max(8, abs(int(fnt.cget("size"))) + delta), weight="bold" if bold else "normal")
                return fnt
            except Exception:
                return None
        self._fonts = [font("TkDefaultFont", 3, True), font("TkDefaultFont", 1, True), font("TkFixedFont")]

        ttk.Label(f, textvariable=var("phase"), font=self._fonts[0]).grid(
            row=0, column=0, columnspan=4, sticky="w", **pad)
        ttk.Label(f, textvariable=var("out"), foreground="#555").grid(row=1, column=0, columnspan=4, sticky="w", padx=8)
        self._build_order_panel(f, pad, var)
        self.bar = ttk.Progressbar(f, maximum=1000)
        self.bar.grid(row=3, column=0, columnspan=4, sticky="ew", **pad)
        ttk.Label(f, textvariable=var("overall_text")).grid(row=4, column=0, columnspan=4, sticky="w", padx=8)
        self.tbar = ttk.Progressbar(f, maximum=1000)
        self.tbar.grid(row=5, column=0, columnspan=4, sticky="ew", padx=8, pady=(6, 0))
        ttk.Label(f, textvariable=var("tier_text")).grid(row=6, column=0, columnspan=4, sticky="w", padx=8)

        cf = ttk.LabelFrame(f, text="Current file", padding=6)
        cf.grid(row=7, column=0, columnspan=4, sticky="ew", **pad)
        cf.columnconfigure(0, weight=1)
        ttk.Label(cf, textvariable=var("file_head")).grid(row=0, column=0, sticky="w")
        ttk.Label(cf, textvariable=var("file_name"), font=self._fonts[1]).grid(row=1, column=0, sticky="w")
        ttk.Label(cf, textvariable=var("file_path"), foreground="#555").grid(row=2, column=0, sticky="w")
        self.fbar = ttk.Progressbar(cf, maximum=1000)
        self.fbar.grid(row=3, column=0, sticky="ew", pady=2)
        ttk.Label(cf, textvariable=var("file_text")).grid(row=4, column=0, sticky="w")

        sf = ttk.LabelFrame(f, text="Details", padding=6)
        sf.grid(row=8, column=0, columnspan=4, sticky="ew", **pad)
        cells = [("Speed now", "speed"), ("Average", "avg"), ("ETA", "eta"), ("Elapsed", "elapsed"),
                 ("Downloaded", "downloaded"), ("Skipped (have)", "skipped"), ("Failed", "failed"),
                 ("Missing (404)", "missing"), ("No URL", "no_url"), ("Posts saved", "posts"),
                 ("Requests", "requests"), ("Free disk", "free")]
        for i, (label, key) in enumerate(cells):
            r, c = divmod(i, 4)
            ttk.Label(sf, text=label + ":").grid(row=r, column=c * 2, sticky="e", padx=(8, 2))
            ttk.Label(sf, textvariable=var(key), width=13).grid(row=r, column=c * 2 + 1, sticky="w")
        ttk.Label(sf, text="Network:").grid(row=3, column=0, sticky="e", padx=(8, 2))
        ttk.Label(sf, textvariable=var("net")).grid(row=3, column=1, columnspan=7, sticky="w")
        ttk.Label(sf, text="Last problem:").grid(row=4, column=0, sticky="e", padx=(8, 2))
        ttk.Label(sf, textvariable=var("last_error"), foreground="#a00", wraplength=600).grid(
            row=4, column=1, columnspan=7, sticky="w")

        f.rowconfigure(9, weight=1)
        self.logw = ScrolledText(f, height=8, wrap="none", font=self._fonts[2])
        self.logw.grid(row=9, column=0, columnspan=4, sticky="nsew", **pad)
        self.logw.configure(state="disabled")

        bf = ttk.Frame(f)
        bf.grid(row=10, column=0, columnspan=4, sticky="e", **pad)
        self.pause_btn = ttk.Button(bf, text="Pause", command=self.on_pause)
        self.pause_btn.pack(side="left", padx=4)
        self.stop_btn = ttk.Button(bf, text="Stop", command=self.on_stop)
        self.stop_btn.pack(side="left", padx=4)
        if os.name == "nt":
            ttk.Button(bf, text="Open folder", command=self.on_open).pack(side="left", padx=4)
        if not self.started:
            self.pause_btn.configure(state="disabled")
            self.stop_btn.configure(text="Cancel")
            self.m.phase = "choose the download order and filters, then press Start"
        self.refresh()

    # --- order / filter panel (1.3) ------------------------------------------
    PANEL_FIELDS = (("min_score", "Min score"), ("min_favs", "Min favs"), ("rating", "Rating (s,q,e)"),
                    ("director", "Director(s)"), ("filter_tags", "Tags"), ("max_size_mb", "Max MiB/file"),
                    ("exclude_ext", "Exclude ext"), ("limit_gb", "Stop after GiB"), ("top", "Stop after N files"))

    def _build_order_panel(self, f, pad, var):
        from tkinter import ttk
        from .priority import ORDERS
        of = ttk.LabelFrame(f, text="Download order and filters", padding=6)
        of.grid(row=2, column=0, columnspan=4, sticky="ew", **pad)
        self.order_frame, self.inputs = of, {}
        a = self.start_args
        if a is None:  # already running (autostart / no panel): read-only summary
            ttk.Label(of, textvariable=var("order")).grid(row=0, column=0, sticky="w")
            return
        ttk.Label(of, text="Order:").grid(row=0, column=0, sticky="e", padx=(2, 2))
        ov = self.tk.StringVar(value=(getattr(a, "order", None) or "default"))
        cb = ttk.Combobox(of, textvariable=ov, values=list(ORDERS), state="readonly", width=15)
        cb.grid(row=0, column=1, sticky="w")
        self.inputs["order"] = (ov, cb)
        for i, (key, label) in enumerate(self.PANEL_FIELDS):
            r, c = divmod(i + 1, 5)
            ttk.Label(of, text=label + ":").grid(row=r, column=c * 2, sticky="e", padx=(8, 2), pady=1)
            val = getattr(a, key, None)
            sv = self.tk.StringVar(value="" if val is None else str(val))
            e = ttk.Entry(of, textvariable=sv, width=16 if key in ("director", "filter_tags") else 9)
            e.grid(row=r, column=c * 2 + 1, sticky="w")
            self.inputs[key] = (sv, e)
        self.start_btn = ttk.Button(of, text="Start", command=self.on_start_clicked)
        self.start_btn.grid(row=0, column=10, rowspan=2, sticky="nse", padx=(12, 2))
        self.panel_msg = self.tk.StringVar(value="Order is fixed while running; Stop, change it and Start again "
                                                 "- finished files are always kept.")
        ttk.Label(of, textvariable=self.panel_msg, foreground="#555").grid(row=2, column=0, columnspan=11, sticky="w")

    def panel_values(self) -> dict:
        return {k: v.get().strip() for k, (v, _) in self.inputs.items()}

    def on_start_clicked(self):
        from .priority import Spec
        import argparse
        vals = self.panel_values()
        ns = argparse.Namespace(**vars(self.start_args))
        conv = {"min_score": int, "min_favs": int, "top": int, "max_size_mb": float, "limit_gb": float}
        try:
            for k, v in vals.items():
                setattr(ns, k, (conv[k](v) if k in conv else v) if v else None)
            ns.order = ns.order or "default"
            spec = Spec.from_args(ns)
        except ValueError as e:
            self.panel_msg.set(f"Invalid value: {e}")
            return
        for k in vals:
            setattr(self.start_args, k, getattr(ns, k))
        self.start_args.spec = spec
        for _, w in self.inputs.values():
            w.configure(state="disabled")
        self.start_btn.configure(state="disabled")
        self.panel_msg.set(f"running with {spec.describe()}")
        self.m.order = spec.describe()
        self.started = True
        self.m.phase = "starting"
        self.pause_btn.configure(state="normal")
        self.stop_btn.configure(text="Stop")
        log.info("starting with %s", spec.describe())
        if self.on_start is not None:
            self.on_start()

    # --- buttons ---------------------------------------------------------
    def on_pause(self):
        if self.m.done:
            return
        if control.paused:
            control.resume()
            self.pause_btn.configure(text="Pause")
        else:
            control.pause()
            self.pause_btn.configure(text="Resume")

    def on_stop(self):
        if self.m.done or not self.started:  # Cancel before Start: nothing is running
            self.root.destroy()
            return
        control.stop()
        self.m.phase = "stopping cleanly (finishing the current step)..."
        self.stop_btn.configure(state="disabled")
        self.pause_btn.configure(state="disabled")

    def on_close(self):
        self.closes += 1
        if self.m.done or self.closes > 1:
            self.root.destroy()  # second close: hide; the worker finishes stopping in the console
        else:
            self.on_stop()

    def on_open(self):
        try:
            os.startfile(self.m.out)  # type: ignore[attr-defined]
        except Exception as e:
            log.warning("cannot open folder: %s", e)

    def _callback_error(self, exc, val, tb):
        if issubclass(exc, KeyboardInterrupt):  # Ctrl-C in the console while the window is open
            self.on_stop()
            return
        log.error("progress window error (ignored): %s: %s", exc.__name__, val)

    # --- polling -----------------------------------------------------------
    def poll(self):
        try:
            for _ in range(MAX_EVENTS_PER_POLL):
                try:
                    kind, data = self.q.get_nowait()
                except queue.Empty:
                    break
                self.m.apply(kind, data)
            self.refresh()
        except Exception as e:  # never stop polling because of one bad event
            log.debug("gui poll error: %s", e)
        finally:
            try:
                self.root.after(POLL_MS, self.poll)
            except Exception:
                pass

    def refresh(self):
        s = self.m.snapshot()
        for k, var in self.v.items():
            var.set(_bmp_safe(str(s.get(k, "")), self.tkv))
        self.root.title(s["title"] + ("" if not s["done"] else " - done"))
        self.bar["value"] = int(s["overall"] * 1000)
        self.fbar["value"] = int(s["file_frac"] * 1000)
        self.tbar["value"] = int(s["tier_frac"] * 1000)
        if self.m.new_log:
            lines, self.m.new_log = self.m.new_log, []
            self.logw.configure(state="normal")
            self.logw.insert("end", _bmp_safe("\n".join(lines), self.tkv) + "\n")
            n = int(self.logw.index("end-1c").split(".")[0])
            if n > LOG_LINES:
                self.logw.delete("1.0", f"{n - LOG_LINES}.0")
            self.logw.see("end")
            self.logw.configure(state="disabled")
        if s["done"]:
            self.stop_btn.configure(text="Close", state="normal")
            self.pause_btn.configure(state="disabled")
            if self.close_when_done is not None and not self._closing:
                self._closing = True
                self.root.after(int(self.close_when_done * 1000), self.root.destroy)

    def run(self):
        self.root.after(POLL_MS, self.poll)
        self.root.mainloop()


def _make_root():
    """Tk root or None (no tkinter installed, no display, broken Tcl)."""
    try:
        import tkinter as tk
    except Exception as e:
        log.warning("GUI unavailable (tkinter missing: %s); using the console", e)
        return None
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # sharp text on scaled displays
        except Exception:
            pass
    try:
        return tk.Tk()
    except Exception as e:
        log.warning("GUI unavailable (%s); using the console", e)
        return None


def wants_start_panel(args) -> bool:
    """Show the order/filter panel (and wait for Start) for download commands unless --autostart."""
    return (args is not None and getattr(args, "spec", None) is not None
            and getattr(args, "cmd", None) in ("run", "full", "download")
            and not getattr(args, "gui_autostart", False) and not getattr(args, "dry_run", False))


def run_with_gui(fn, args=None, close_when_done: float | None = None, on_ready=None):
    """Run fn() (returns an exit code) in a worker thread with the progress window on this thread.
    Returns fn's exit code, or None if no window could be created (caller then runs fn itself).
    With a start panel (see wants_start_panel) the worker starts only when Start is pressed;
    closing the window before that returns 130 (nothing was done)."""
    root = _make_root()
    if root is None:
        return None
    q: queue.Queue = queue.Queue(maxsize=50000)

    def sink(kind, data):
        try:
            q.put_nowait((kind, data))
        except queue.Full:  # GUI not draining (window frozen/closed): drop display updates, never block
            pass

    worker = threading.Thread(target=lambda: work(), name="archiver-worker")
    panel = wants_start_panel(args)
    try:
        win = ArchiverWindow(root, q, GuiModel(), close_when_done=close_when_done,
                             start_args=args if panel else None, on_start=lambda: worker.start())
        if on_ready is not None:  # tests / screenshots: called on the Tk thread with the window
            root.after(0, on_ready, win)
    except Exception as e:
        log.warning("GUI unavailable (%s); using the console", e)
        try:
            root.destroy()
        except Exception:
            pass
        return None
    handler = QueueLogHandler(q)
    add_sink(sink)
    log.addHandler(handler)
    result = {"code": 1}

    def work():
        try:
            result["code"] = fn()
        except BaseException as e:  # execute() handles everything expected; this is the last net
            log.exception("worker crashed: %s", e)
        finally:
            try:
                q.put(("done", {"code": result["code"]}), timeout=5)
            except queue.Full:
                pass

    old_int = None
    if threading.current_thread() is threading.main_thread():
        try:  # Ctrl-C in the console window = Stop button (clean stop), not a crash in Tk
            old_int = signal.signal(signal.SIGINT, lambda *_: control.stop())
        except (ValueError, OSError):
            old_int = None
    if not panel:
        worker.start()
    try:
        win.run()
    except KeyboardInterrupt:
        control.stop()
    except Exception as e:
        log.error("progress window failed (%s); the run continues in the console", e)
    finally:
        remove_sink(sink)
        log.removeHandler(handler)
    if worker.ident is None:  # window closed before Start
        log.info("closed before Start; nothing was downloaded")
        result["code"] = 130
    if worker.is_alive():
        print("window closed; finishing in the console (Ctrl-C again = stop)...", file=sys.stderr)
    while worker.is_alive():
        try:
            worker.join(0.5)
        except KeyboardInterrupt:
            control.stop()
    if old_int is not None:
        try:
            signal.signal(signal.SIGINT, old_int)
        except (ValueError, OSError):
            pass
    return result["code"]
