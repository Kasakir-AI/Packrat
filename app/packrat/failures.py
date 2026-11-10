"""1.8: a persistent, human-readable log of every file that failed to download.

Two files in the archive folder, appended to by every run (never rewritten):
  failed-downloads.csv  one row per failed (or later recovered) file: time, run, event, post id, md5, ext,
                        director, status, reason, HTTP status, tries this run, failures so far, URL
  failed-downloads.log  the same as readable lines, with a header line per run

1.9: when a file grows past failure_log_max_mb (default 10 MB, 0 = never) it is trimmed at the end of a
run: rows / lines of posts that are no longer failed move to failed-downloads-archive.csv.gz / .log.gz
(appended, nothing is deleted), rows of posts that are still failed always stay.

The database keeps the latest reason per post (table file_errors, used by the completeness report and
`retry-failed`). Nothing secret is written: URLs lose any user-info / query string and pass through the
same redaction as the log (the API key never appears in a file URL anyway).
"""
from __future__ import annotations

import csv
import itertools
import os
import json
import logging
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from . import __version__
from .events import emit
from .redact import redact

_RUN_SEQ = itertools.count(1)

log = logging.getLogger("packrat")
FAIL_CSV = "failed-downloads.csv"
FAIL_LOG = "failed-downloads.log"
FAIL_CSV_ARCHIVE = "failed-downloads-archive.csv.gz"   # 1.9: trimmed rows (gzip, appended)
FAIL_LOG_ARCHIVE = "failed-downloads-archive.log.gz"
DEFAULT_MAX_MB = 10.0
COLUMNS = ("time", "run", "event", "post_id", "md5", "ext", "director", "status", "reason", "http_status",
           "tries_this_run", "failures_total", "url")
NO_DIRECTOR = "_no_director"
UNKNOWN_DIRECTOR = "_unknown_director"
DIRECTOR_CATEGORY = 1


def http_status_of(reason: str | None) -> str:
    m = re.fullmatch(r"http_(\d{3})", reason or "")
    return m.group(1) if m else ""


def safe_url(url: str | None) -> str:
    """Scheme://host/path only (no user-info, query or fragment), then redacted."""
    if not url:
        return ""
    try:
        p = urlsplit(url)
        url = urlunsplit((p.scheme, p.hostname or "", p.path, "", "")) if p.scheme else url.split("?")[0]
    except ValueError:
        url = url.split("?")[0]
    return redact(url)


def directors_of(store, post_id: int, exclude=()) -> str:
    """Space-separated director tags of a post (from its categorized tags, else via tag_categories)."""
    row = store.db.execute("SELECT tag_cats, tags FROM posts WHERE id=?", (post_id,)).fetchone()
    if not row:
        return UNKNOWN_DIRECTOR
    tag_cats, tags = row
    ex = set(exclude or ())
    if tag_cats:
        try:
            d = json.loads(tag_cats).get("director")
            if isinstance(d, list):
                d = [x for x in d if x not in ex]
                return " ".join(d) or NO_DIRECTOR
        except (ValueError, AttributeError):
            pass
    names = (tags or "").split()
    if not names or not store.has_tag_categories():
        return UNKNOWN_DIRECTOR if names else NO_DIRECTOR
    q = ",".join("?" * len(names[:500]))
    d = [n for (n,) in store.db.execute(f"SELECT name FROM tag_categories WHERE category=? AND name IN ({q})",
                                        (DIRECTOR_CATEGORY, *names[:500])) if n not in ex]
    return " ".join(sorted(d)) or NO_DIRECTOR


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class FailureLog:
    """Appends to failed-downloads.csv / .log. Opened lazily (a run without failures writes nothing)."""

    def __init__(self, out, command: str | None = None, store=None, max_mb: float | None = None):
        self.out = Path(out)
        self.store = store
        self.max_mb = DEFAULT_MAX_MB if max_mb is None else float(max_mb)
        self.trimmed: dict | None = None
        # run id: local start time + process id (two runs in the same second stay distinct)
        self.run = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{next(_RUN_SEQ)}"
        self.command = command
        self._csv = self._log = self._w = None
        self.failed = Counter()      # reason -> count, this run
        self.recovered = 0

    @property
    def csv_path(self) -> Path:
        return self.out / FAIL_CSV

    @property
    def log_path(self) -> Path:
        return self.out / FAIL_LOG

    def _open(self) -> None:
        if self._w is not None:
            return
        new = not self.csv_path.exists() or self.csv_path.stat().st_size == 0
        self._csv = open(self.csv_path, "a", encoding="utf-8", newline="")
        self._w = csv.writer(self._csv)
        if new:
            self._w.writerow(COLUMNS)
        self._log = open(self.log_path, "a", encoding="utf-8", newline="\n")
        self._log.write(f"=== run {self.run} started {_now()} (Packrat {__version__}"
                        f"{', ' + self.command if self.command else ''})\n")

    def record(self, *, post_id: int, md5: str = "", ext: str = "", director: str = "", status: str = "",
               reason: str = "", tries: int = 1, failures_total: int = 1, url: str = "",
               event: str = "failed") -> None:
        try:
            self._open()
            u = safe_url(url)
            http = http_status_of(reason)
            self._w.writerow((_now(), self.run, event, post_id, md5 or "", ext or "", director or "", status or "",
                              reason or "", http, tries, failures_total, u))
            if event == "failed":
                self.failed[reason or status] += 1
                self._log.write(f"{_now()}  FAILED     post {post_id}  {reason or status}"
                                f"{' (HTTP ' + http + ')' if http else ''}  tries {tries} this run, "
                                f"failure #{failures_total}  director {director or '?'}  {u}\n")
            else:
                self.recovered += 1
                self._log.write(f"{_now()}  RECOVERED  post {post_id}  saved after {failures_total} earlier "
                                f"failure(s)  director {director or '?'}  {u}\n")
            self._csv.flush()
            self._log.flush()
        except OSError as e:  # the log must never stop a download run
            log.warning("could not write %s: %s", FAIL_CSV, e)

    def summary(self) -> str:
        n = sum(self.failed.values())
        if not n and not self.recovered:
            return ""
        parts = []
        if n:
            parts.append(f"{n:,} file(s) failed this run (" +
                         ", ".join(f"{k} {v:,}" for k, v in self.failed.most_common()) + ")")
        if self.recovered:
            parts.append(f"{self.recovered:,} earlier failure(s) recovered")
        return "; ".join(parts) + f" - details in {self.csv_path.name} / {self.log_path.name}"

    def close(self) -> None:
        s = self.summary()
        if self._w is not None:
            try:
                self._log.write(f"=== run {self.run} ended {_now()}: {s or 'no failures'}\n")
            except OSError:
                pass
            for f in (self._csv, self._log):
                try:
                    f.close()
                except OSError:
                    pass
            self._csv = self._log = self._w = None
            if self.store is not None and self.max_mb > 0:
                try:
                    self.trimmed = trim_failure_logs(self.out, int(self.max_mb * 2**20),
                                                     still_failed_ids(self.store))
                except Exception as e:  # noqa: BLE001 - trimming must never break the end of a run
                    log.warning("could not trim %s: %s", FAIL_CSV, e)
        if s:
            (log.warning if self.failed else log.info)("%s", s)
            emit("failures", summary=s, failed=sum(self.failed.values()), recovered=self.recovered,
                 by_reason=dict(self.failed), csv=str(self.csv_path), log=str(self.log_path))


# ---- 1.9: size cap ------------------------------------------------------------------------------
def still_failed_ids(store) -> set:
    from .store import RETRYABLE_STATUSES
    q = ",".join("?" * len(RETRYABLE_STATUSES))
    return {i for (i,) in store.db.execute(f"SELECT id FROM posts WHERE file_status IN ({q})",
                                           RETRYABLE_STATUSES)}


_POST_RE = re.compile(r"\bpost (\d+)\b")


def _replace_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def trim_failure_logs(out, max_bytes: int, keep_ids) -> dict:
    """Trim failed-downloads.csv / .log when larger than max_bytes. Rows / lines of posts in keep_ids
    (still failed) always stay; the rest are appended to the gzip archives first (a crash in between
    leaves duplicates, never a loss). Returns {"csv": rows moved, "log": lines moved} for trimmed files."""
    import gzip
    import io
    out, keep = Path(out), {int(i) for i in keep_ids}
    done = {}
    cp = out / FAIL_CSV
    if max_bytes > 0 and cp.is_file() and cp.stat().st_size > max_bytes:
        with open(cp, encoding="utf-8", newline="") as f:
            rows = list(csv.reader(f))
        header, body = (rows[0], rows[1:]) if rows and rows[0] and rows[0][0] == "time" else (list(COLUMNS), rows)
        pi = header.index("post_id") if "post_id" in header else 3
        def kept(r):
            try:
                return int(r[pi]) in keep
            except (ValueError, IndexError):
                return False
        stay = [r for r in body if kept(r)]
        move = [r for r in body if not kept(r)]
        if move:
            arch = out / FAIL_CSV_ARCHIVE
            new = not arch.exists()
            buf = io.StringIO()
            w = csv.writer(buf)
            if new:
                w.writerow(header)
            w.writerows(move)
            with gzip.open(arch, "at", encoding="utf-8", newline="") as g:
                g.write(buf.getvalue())
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(header)
            w.writerows(stay)
            _replace_text(cp, buf.getvalue())
            done["csv"] = len(move)
            log.info("%s was over %.0f MB: %d row(s) of files that are not failed any more moved to %s; "
                     "%d row(s) of %d still-failed file(s) kept", FAIL_CSV, max_bytes / 2**20, len(move),
                     FAIL_CSV_ARCHIVE, len(stay), len({r[pi] for r in stay}))
    lp = out / FAIL_LOG
    if max_bytes > 0 and lp.is_file() and lp.stat().st_size > max_bytes:
        with open(lp, encoding="utf-8", newline="") as f:
            lines = f.readlines()
        def keep_line(s):
            m = _POST_RE.search(s)
            return bool(m) and " FAILED " in s and int(m.group(1)) in keep
        stay = [s for s in lines if keep_line(s)]
        move = [s for s in lines if not keep_line(s)]
        if move:
            with gzip.open(out / FAIL_LOG_ARCHIVE, "at", encoding="utf-8", newline="") as g:
                g.write("".join(move))
            note = (f"=== trimmed {_now()}: {len(move)} older line(s) moved to {FAIL_LOG_ARCHIVE}; the lines "
                    f"of files that are still failed are kept below\n")
            _replace_text(lp, note + "".join(stay))
            done["log"] = len(move)
    return done


# ---- 1.8: retry-failed selection ---------------------------------------------------------------
REASON_GROUPS = {
    "403": ("http_403",), "404": ("http_404",), "429": ("http_429",), "503": ("http_503",),
    "http": "http_",               # any HTTP status
    "network": ("network_error",),
    "md5": ("md5_mismatch", "wrong_size"), "size": ("wrong_size",),
    "verify": "verify_",           # files `verify` found bad
    "unknown": None,               # failed before 1.7 (no reason recorded)
}
_EXACT = re.compile(r"(http_\d{3}|network_error|md5_mismatch|wrong_size|verify_[a-z_]+|missing|failed)")


def parse_reasons(spec) -> list | None:
    """'403,md5' -> tokens. None/''/'all' = every failed file. Raises ValueError on an unknown word."""
    if not spec:
        return None
    toks = [x.strip().lower() for x in str(spec).replace(";", ",").split(",") if x.strip()]
    if not toks or "all" in toks:
        return None
    bad = [x for x in toks if x not in REASON_GROUPS and not _EXACT.fullmatch(x)]
    if bad:
        raise ValueError(f"unknown --reason {', '.join(bad)} (use {', '.join(REASON_GROUPS)}, or an exact "
                         "reason such as http_403 / network_error / md5_mismatch)")
    return toks


def reason_matches(reason: str | None, toks: list | None) -> bool:
    if toks is None:
        return True
    for t in toks:
        g = REASON_GROUPS.get(t, (t,))
        if g is None:
            if not reason:
                return True
        elif isinstance(g, str):
            if reason and reason.startswith(g):
                return True
        elif reason in g:
            return True
    return False


def read_ids_csv(path) -> list:
    """Post ids from a CSV with a post_id (or id) column: missing.csv, failed-downloads.csv, or a plain list."""
    ids = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        return ids
    head = [h.strip().lower() for h in rows[0]]
    col = head.index("post_id") if "post_id" in head else head.index("id") if "id" in head else None
    body = rows[1:] if col is not None else rows
    col = col or 0
    for r in body:
        if len(r) > col and r[col].strip().isdigit():
            ids.append(int(r[col].strip()))
    return list(dict.fromkeys(ids))


def select_retry(store, reasons=None, from_csv=None) -> dict:
    """Which posts `retry-failed` re-attempts: {selected: [ids], by_reason: Counter, skipped: Counter}."""
    from .store import RETRYABLE_STATUSES, abspath
    toks = parse_reasons(reasons)
    sel, by, skipped = [], Counter(), Counter()
    if from_csv is None:
        for pid, reason, _st in store.failed_files():
            if reason_matches(reason, toks):
                sel.append(pid)
                by[reason or "unknown"] += 1
            else:
                skipped["other_reason"] += 1
        return {"selected": sel, "by_reason": by, "skipped": skipped}
    for pid in read_ids_csv(from_csv):
        row = store.db.execute("SELECT p.file_status, p.file_url, p.file_path, fe.reason FROM posts p LEFT JOIN "
                               "file_errors fe ON fe.post_id=p.id WHERE p.id=?", (pid,)).fetchone()
        if row is None:
            skipped["not_in_database"] += 1
            continue
        st, url, rel, reason = row
        if not url:
            skipped["no_file_url (deleted or hidden)"] += 1
            continue
        if st == "ok":
            if rel and abspath(store.out, rel).exists():
                skipped["already_saved"] += 1
                continue
            reason = reason or "gone_from_disk"
        elif st not in RETRYABLE_STATUSES and st != "pending":
            skipped[f"status_{st}"] += 1
            continue
        if not reason_matches(reason if reason != "gone_from_disk" else None, toks):
            skipped["other_reason"] += 1
            continue
        sel.append(pid)
        by[reason or ("not_downloaded_yet" if st == "pending" else "unknown")] += 1
    return {"selected": sel, "by_reason": by, "skipped": skipped}
