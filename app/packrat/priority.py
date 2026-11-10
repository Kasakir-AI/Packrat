"""Priority download order (1.3): which files to fetch first, computed in SQLite.

The queue is a table `queue(rank INTEGER PRIMARY KEY, post_id UNIQUE, cum_bytes)`:
rank 1 is fetched first; cum_bytes is the running total of file sizes up to that rank, so
"top N" and "first N GiB" tiers are simple range lookups. It holds every candidate file,
downloaded or not, so ranks and tier progress stay meaningful and stable across resumes.

Changing --order or a filter changes the queue signature and rebuilds the queue (one
INSERT ... SELECT with window functions, about a second for 200k posts). Progress is never
lost: whether a file is done lives in posts.file_status, not in the queue.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import asdict, dataclass, field

log = logging.getLogger("packrat")

ORDERS = ("default", "popular", "favorites", "newest", "oldest", "smallest-first", "balanced")

# Every ORDER BY ends in a unique key (id), so ties always break the same way (stable ranks).
# NULL score/favs count as 0; a NULL size sorts last for smallest-first.
ORDER_SQL = {
    "default": "id DESC",  # 1.2 behaviour: newest first
    "newest": "id DESC",
    "oldest": "id ASC",
    "popular": "COALESCE(score, 0) DESC, COALESCE(fav_count, 0) DESC, id DESC",
    "favorites": "COALESCE(fav_count, 0) DESC, COALESCE(score, 0) DESC, id DESC",
    "smallest-first": "size IS NULL, size ASC, id DESC",
    "balanced": "bal DESC, id DESC",
}

# balanced = 0.5 * score percentile + 0.3 * favorites percentile + 0.2 * small-file percentile
# (PERCENT_RANK over the candidate set: 0 = lowest, 1 = highest). Percentiles instead of raw
# values keep one viral post from flattening everything else, and need no math functions.
BALANCED_SQL = ("0.5 * PERCENT_RANK() OVER (ORDER BY COALESCE(score, 0))"
                " + 0.3 * PERCENT_RANK() OVER (ORDER BY COALESCE(fav_count, 0))"
                " + 0.2 * (1.0 - PERCENT_RANK() OVER (ORDER BY COALESCE(size, 9e18)))")

# Files that can still be downloaded, or already were. Deleted / no-URL posts are not ranked.
CANDIDATE_STATUSES = ("pending", "ok", "missing", "failed", "md5_mismatch")
RATINGS = {"s", "q", "e"}


def _list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        items = v
    else:
        items = str(v).replace(",", " ").split()
    return [x.strip().lower() for x in items if x.strip()]


@dataclass
class Spec:
    """Order + filters + tier limits (everything that decides what is fetched, and when)."""
    order: str = "default"
    min_score: int | None = None
    min_favs: int | None = None
    rating: list = field(default_factory=list)        # subset of s,q,e
    director: list = field(default_factory=list)      # any of these director tags
    tags: list = field(default_factory=list)          # all of these; "-tag" excludes
    max_size_mb: float | None = None
    exclude_ext: list = field(default_factory=list)
    limit_gb: float | None = None                     # tier: first N GiB of the queue
    top: int | None = None                            # tier: first N files of the queue

    @classmethod
    def from_args(cls, a) -> "Spec":
        g = lambda k: getattr(a, k, None)  # noqa: E731
        order = (g("order") or "default").strip().lower()
        if order not in ORDERS:
            raise ValueError(f"unknown order {order!r}; choose one of: {', '.join(ORDERS)}")
        rating = _list(g("rating"))
        bad = [r for r in rating if r not in RATINGS]
        if bad:
            raise ValueError(f"unknown rating(s) {', '.join(bad)}; use s, q, e (e.g. --rating s,q)")
        director = [d.replace(" ", "_") for d in _list(g("director"))]
        for name, v in (("min_score", g("min_score")), ("min_favs", g("min_favs")), ("top", g("top"))):
            if v is not None and not isinstance(v, int):
                raise ValueError(f"{name} must be a whole number")
        for name, v in (("max_size_mb", g("max_size_mb")), ("limit_gb", g("limit_gb"))):
            if v is not None and v <= 0:
                raise ValueError(f"{name} must be greater than 0")
        if g("top") is not None and g("top") <= 0:
            raise ValueError("top must be greater than 0")
        return cls(order=order, min_score=g("min_score"), min_favs=g("min_favs"), rating=sorted(set(rating)),
                   director=director, tags=_list(g("filter_tags")), max_size_mb=g("max_size_mb"),
                   exclude_ext=[e.lstrip(".") for e in _list(g("exclude_ext"))],
                   limit_gb=g("limit_gb"), top=g("top"))

    # -- what decides the queue (the tier limits only cut it, so they are not part of it) --
    def ranking_key(self) -> dict:
        d = asdict(self)
        d.pop("limit_gb"), d.pop("top")
        return d

    def filtered(self) -> bool:
        return any(v not in (None, [], "default") for k, v in self.ranking_key().items() if k != "order")

    def describe(self) -> str:
        parts = [f"order={self.order}"]
        for k, v in asdict(self).items():
            if k != "order" and v not in (None, []):
                parts.append(f"{k}={','.join(map(str, v)) if isinstance(v, list) else v}")
        return " ".join(parts)

    def where(self, candidates: bool = True) -> tuple[str, list]:
        """SQL WHERE clause (on posts) for the filters, with its parameters. candidates=False: the
        filters only (1.7 report: was a post left out by a filter?)."""
        sql = [f"file_url IS NOT NULL AND file_status IN ({','.join('?' * len(CANDIDATE_STATUSES))})"
               if candidates else "1"]
        args: list = list(CANDIDATE_STATUSES) if candidates else []
        if self.min_score is not None:
            sql.append("COALESCE(score, 0) >= ?"); args.append(self.min_score)
        if self.min_favs is not None:
            sql.append("COALESCE(fav_count, 0) >= ?"); args.append(self.min_favs)
        if self.rating:
            sql.append(f"rating IN ({','.join('?' * len(self.rating))})"); args += self.rating
        if self.director:  # any of them; tags is the space-separated list of all tags
            sql.append("(" + " OR ".join(["instr(' ' || COALESCE(tags, '') || ' ', ?) > 0"] * len(self.director)) + ")")
            args += [f" {d} " for d in self.director]
        for t in self.tags:
            neg = t.startswith("-")
            sql.append(f"instr(' ' || COALESCE(tags, '') || ' ', ?) {'=' if neg else '>'} 0")
            args.append(f" {t.lstrip('-')} ")
        if self.max_size_mb is not None:
            sql.append("(size IS NULL OR size <= ?)"); args.append(int(self.max_size_mb * 2**20))
        if self.exclude_ext:
            sql.append(f"COALESCE(ext, '') NOT IN ({','.join('?' * len(self.exclude_ext))})"); args += self.exclude_ext
        return " AND ".join(sql), args


def _ranked_select(spec: Spec, extra_where: str = "") -> tuple[str, list]:
    """SELECT (rn, id, size) for the candidates in priority order."""
    where, args = spec.where()
    if extra_where:
        where += " AND " + extra_where
    cols = "id, score, fav_count, size" + (f", {BALANCED_SQL} AS bal" if spec.order == "balanced" else "")
    sql = (f"SELECT ROW_NUMBER() OVER (ORDER BY {ORDER_SQL[spec.order]}) AS rn, id, COALESCE(size, 0) AS sz "
           f"FROM (SELECT {cols} FROM posts WHERE {where})")
    return sql, args


class PriorityQueue:
    def __init__(self, store, spec: Spec):
        self.s, self.spec = store, spec
        need = (3, 33, 0)  # window functions (3.25) + UPDATE ... FROM used by the export merge (3.33)
        if sqlite3.sqlite_version_info < need:
            raise RuntimeError(f"SQLite {sqlite3.sqlite_version} is too old (need 3.33+); update Python")

    def signature(self) -> str:
        key = dict(self.spec.ranking_key(), export_gen=self.s.get_state("export_generation", 0))
        return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]

    # ------------------------------------------------------------------
    def ensure(self) -> bool:
        """Build the queue if the order/filters/export changed; else append newly found posts.
        Returns True if it was rebuilt."""
        sig = self.signature()
        if self.s.get_state("queue_sig") != sig:
            self.rebuild(sig)
            return True
        self.append_new()
        return False

    def rebuild(self, sig: str | None = None) -> int:
        db = self.s.db
        prev = self.s.get_state("queue_spec")
        db.execute("DELETE FROM queue")
        sel, args = _ranked_select(self.spec)
        db.execute(f"INSERT INTO queue(rank, post_id, cum_bytes) SELECT rn, id, "
                   f"SUM(sz) OVER (ORDER BY rn ROWS UNBOUNDED PRECEDING) FROM ({sel})", args)
        n = db.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
        self.s.set_state("queue_sig", sig or self.signature())
        self.s.set_state("queue_spec", self.spec.describe())
        self.s.commit()
        if prev and prev != self.spec.describe():
            log.info("download order changed (%s -> %s); queue rebuilt, finished files are kept", prev,
                     self.spec.describe())
        log.info("priority queue: %d files ranked (%s)", n, self.spec.describe())
        return n

    def append_new(self) -> int:
        """Posts found after the queue was built (API crawl): ranked among themselves, appended."""
        db = self.s.db
        last = db.execute("SELECT COALESCE(MAX(rank), 0), COALESCE(MAX(cum_bytes), 0) FROM queue").fetchone()
        sel, args = _ranked_select(self.spec, "file_status = 'pending' AND id NOT IN (SELECT post_id FROM queue)")
        db.execute(f"INSERT INTO queue(rank, post_id, cum_bytes) SELECT ? + rn, id, ? + "
                   f"SUM(sz) OVER (ORDER BY rn ROWS UNBOUNDED PRECEDING) FROM ({sel})", [last[0], last[1], *args])
        n = db.execute("SELECT changes()").fetchone()[0]
        if n:
            log.debug("priority queue: appended %d new files", n)
        return n

    # ------------------------------------------------------------------
    def tier_end(self) -> int:
        """Highest rank inside the selected tier (--top / --limit-gb), or the whole queue."""
        db = self.s.db
        end = db.execute("SELECT COALESCE(MAX(rank), 0) FROM queue").fetchone()[0]
        if self.spec.top is not None:
            end = min(end, self.spec.top)
        if self.spec.limit_gb is not None:
            r = db.execute("SELECT COALESCE(MAX(rank), 0) FROM queue WHERE cum_bytes <= ?",
                           (int(self.spec.limit_gb * 2**30),)).fetchone()[0]
            end = min(end, r)
        return end

    def tier_progress(self, end: int | None = None) -> dict:
        end = self.tier_end() if end is None else end
        r = self.s.db.execute(
            "SELECT COUNT(*), COALESCE(SUM(p.size), 0), COALESCE(SUM(p.file_status = 'ok'), 0), "
            "COALESCE(SUM(CASE WHEN p.file_status = 'ok' THEN p.size END), 0) "
            "FROM queue q JOIN posts p ON p.id = q.post_id WHERE q.rank <= ?", (end,)).fetchone()
        return {"tier_end": end, "files": r[0], "bytes": r[1], "done_files": r[2], "done_bytes": r[3]}

    def iter_pending(self, end: int | None = None, batch: int = 500):
        """Yield (rank, id, md5, ext, size, file_url, file_path) for pending files in rank order,
        keyset-paginated on rank (constant memory; files finished meanwhile are skipped)."""
        end = self.tier_end() if end is None else end
        last = 0
        while True:
            rows = self.s.db.execute(
                "SELECT q.rank, p.id, p.md5, p.ext, p.size, p.file_url, p.file_path FROM queue q "
                "JOIN posts p ON p.id = q.post_id WHERE q.rank > ? AND q.rank <= ? AND p.file_status = 'pending' "
                "ORDER BY q.rank LIMIT ?", (last, end, batch)).fetchall()
            if not rows:
                return
            yield from rows
            last = rows[-1][0]

    def pending_in_tier(self, end: int | None = None) -> tuple[int, int]:
        end = self.tier_end() if end is None else end
        return self.s.db.execute(
            "SELECT COUNT(*), COALESCE(SUM(p.size), 0) FROM queue q JOIN posts p ON p.id = q.post_id "
            "WHERE q.rank <= ? AND p.file_status = 'pending'", (end,)).fetchone()

    def top(self, count: int) -> list[dict]:
        cols = ("rank", "id", "score", "fav_count", "rating", "ext", "size", "cum_bytes", "file_status", "tags")
        rows = self.s.db.execute(
            "SELECT q.rank, p.id, p.score, p.fav_count, p.rating, p.ext, p.size, q.cum_bytes, p.file_status, "
            "p.tag_cats FROM queue q JOIN posts p ON p.id = q.post_id ORDER BY q.rank LIMIT ?", (count,)).fetchall()
        out = []
        for r in rows:
            d = dict(zip(cols, r))
            try:
                d["director"] = ",".join((json.loads(d.pop("tags") or "{}") or {}).get("director", [])) or "-"
            except ValueError:
                d["director"] = "-"
            out.append(d)
        return out

    def tiers(self, sizes=(1000, 10000, 50000), rate: float | None = None, mbps: float = 50.0) -> list[dict]:
        """Count, size and estimated time for the top-N tiers (and the whole queue).

        1.6: file downloads run in parallel without a per-request limit, so time = bytes / bandwidth
        (rate=None, the default). Passing a rate gives the old max(files / rate, bytes / bandwidth)
        estimate for strictly one-file-at-a-time pacing. Remaining = only files not downloaded yet."""
        db = self.s.db
        total = db.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
        out = []
        marks = sorted({n for n in sizes if n < total} | {total})
        if self.spec.top or self.spec.limit_gb:
            marks = sorted(set(marks) | {self.tier_end()})
        bw = mbps * 1e6 / 8
        for n in marks:
            if not n:
                continue
            cum = db.execute("SELECT cum_bytes FROM queue WHERE rank = ?", (n,)).fetchone()[0] or 0
            left_n, left_b = self.pending_in_tier(n)
            est = lambda f, b: max(f / rate if rate else 0.0, b / bw) / 3600  # noqa: E731
            out.append({"tier": "all" if n == total else f"top {n:,}", "files": n, "bytes": cum,
                        "gib": round(cum / 2**30, 2), "hours": round(est(n, cum), 1),
                        "pending_files": left_n, "pending_gib": round(left_b / 2**30, 2),
                        "remaining_hours": round(est(left_n, left_b), 1),
                        "selected_tier": n == self.tier_end() and bool(self.spec.top or self.spec.limit_gb)})
        return out
