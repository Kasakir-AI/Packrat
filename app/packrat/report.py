"""1.7: completeness report - the archive (database + files on disk) compared with the site's
latest posts export (db_export/posts.csv.gz).

Every post the export lists as not deleted, with a file (md5), is "expected". Each one lands in
exactly one bucket:

  verified      saved, on disk, md5 checked (inline while downloading, or by `verify`)
  unverified    saved and on disk, md5 not checked yet (run `verify`)
  missing       not saved: not in the archive database yet, not downloaded yet, or the saved
                file is gone from disk / the site replaced the file (new md5)
  failed        the last attempt failed: http_404, http_403, http_429/503, network_error,
                md5_mismatch, wrong_size, login_only (no file URL when signed out),
                marked_deleted (the archive has it as deleted - from the API or an older
                export - although this export lists it; the next sync/full re-checks it)
  skipped       left out on purpose by the download filters (--rating, --min-score ...) or
                beyond the --top / --limit-gb tier

Also counted: files under files/ (and the 1.1 data/ layout) that no post points at ("extra"),
leftover .part files, quarantined files, and saved posts the export no longer expects (deleted
on the site since, or newer than the export).

Writes, in the output folder:
  completeness-report-YYYYmmdd-HHMMSS.txt / .html   human-readable summary + per-director table
  missing-YYYYmmdd-HHMMSS.csv (+ a copy as missing.csv)   every expected post not saved, with
                                                           post_id, md5, director, reason, url
  extra-files-YYYYmmdd-HHMMSS.csv (only if there are any)
Read-only towards the archive apart from a temporary table and the "last_report" state key.
No network.
"""
from __future__ import annotations

import csv
import html
import json
import logging
import os
import shutil
import time
from collections import Counter, defaultdict
from pathlib import Path

from .naming import CATEGORY_NAMES, DEFAULT_DIRECTOR_EXCLUDE, FILES_DIR
from .events import emit

log = logging.getLogger("packrat")

CSV_COLUMNS = ("post_id", "md5", "ext", "size", "director", "category", "reason", "last_error", "url")
BUCKETS = ("verified", "unverified", "missing", "failed", "skipped")
NO_DIRECTOR = "_no_director"
UNKNOWN_DIRECTOR = "_unknown_director"   # tags export not loaded and the post isn't in the database
LEGACY_DIR = "data"                      # 1.1 layout data/aa/bb/<md5>.<ext>
FAILED_STATUS_REASON = {"missing": "http_404", "md5_mismatch": "md5_mismatch", "failed": "failed",
                        "no_url": "login_only", "deleted": "marked_deleted"}
_USED = ("md5", "file_ext", "file_size", "tag_string", "is_deleted")


class ReportError(RuntimeError):
    pass


class NoExportError(ReportError):
    """No posts export to compare with (the full archive skips the report instead of failing)."""


def _site_label() -> str:
    from . import siteprofile
    return siteprofile.current().label


def _artist_word() -> str:
    from . import siteprofile
    return siteprofile.current().artist_word


def file_url(md5: str, ext: str) -> str:
    from . import siteprofile
    return siteprofile.current().file_url(md5, ext) or ""


def _int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _norm(rel: str | None) -> str:
    return (rel or "").replace("\\", "/").strip("/")


def _export_date(path: Path, out) -> str:
    """The site's updated_at for this copy (db_export/manifest.json), else the file time."""
    from .exports import export_dir, read_manifest
    try:
        key = path.resolve().relative_to(export_dir(out).resolve()).as_posix()
        info = read_manifest(out).get(key) or {}
        if info.get("updated_at"):
            return str(info["updated_at"])
    except (ValueError, OSError, AttributeError):  # outside db_export/ (--export) or no manifest
        pass
    return time.strftime("%Y-%m-%d %H:%M (file time)", time.localtime(path.stat().st_mtime))


def _load_export(db, path: Path) -> dict:
    """posts.csv.gz -> TEMP table rep_export. Returns row counts."""
    from .exports import POSTS_COLUMNS, _bool, _open_csv_gz, _reader
    db.execute("DROP TABLE IF EXISTS temp.rep_export")
    db.execute("CREATE TEMP TABLE rep_export (id INTEGER PRIMARY KEY, md5 TEXT, ext TEXT, size INTEGER, "
               "tags TEXT, deleted INTEGER)")
    n = 0
    with _open_csv_gz(path) as f:
        r, hm = _reader(f, "posts", POSTS_COLUMNS, _USED)
        batch = []
        for raw in r:
            row = hm.row(raw)
            pid = _int(row.get("id"))
            if pid <= 0:
                continue
            batch.append((pid, (row.get("md5") or "").strip().lower(), (row.get("file_ext") or "").strip().lower(),
                          _int(row.get("file_size")), row.get("tag_string") or "", int(_bool(row.get("is_deleted")))))
            n += 1
            if len(batch) >= 5000:
                db.executemany("INSERT OR REPLACE INTO rep_export VALUES(?,?,?,?,?,?)", batch)
                batch.clear()
        if batch:
            db.executemany("INSERT OR REPLACE INTO rep_export VALUES(?,?,?,?,?,?)", batch)
    if not n:
        raise ReportError(f"the posts export {path} has no rows")
    return {"rows": n}


def _director_names(db) -> set | None:
    nums = [k for k, v in CATEGORY_NAMES.items() if v == "director"]
    if db.execute("SELECT 1 FROM tag_categories LIMIT 1").fetchone() is None:
        return None
    return {r[0] for r in db.execute(f"SELECT name FROM tag_categories WHERE category IN "
                                     f"({','.join('?' * len(nums))})", nums)}


def _scan_files(out: Path, known: set) -> tuple[list, int, int]:
    """Files under files/ and data/ that no post points at; .part leftovers; quarantined files."""
    extra, parts = [], 0
    for top in (FILES_DIR, LEGACY_DIR):
        base = out / top
        if not base.is_dir():
            continue
        for root, _dirs, names in os.walk(base):
            for name in names:
                p = Path(root) / name
                rel = _norm(os.path.relpath(p, out))
                if name.endswith(".part"):
                    parts += 1
                    continue
                if rel not in known:
                    try:
                        size = p.stat().st_size
                    except OSError:
                        size = 0
                    extra.append((rel, size))
    quarantined = sum(len(n) for _r, _d, n in os.walk(out / "quarantine")) if (out / "quarantine").is_dir() else 0
    return sorted(extra), parts, quarantined


def build_report(store, spec=None, naming=None, export_path=None, check_disk: bool = True,
                 write: bool = True, stamp: str | None = None) -> dict:
    from .exports import good_export_path
    from .store import abspath, legacy_relpath
    out = Path(store.out)
    db = store.db
    path = Path(export_path) if export_path else good_export_path(out, "posts")
    if path is None or not path.is_file():
        raise NoExportError("no posts export to compare with: run `sync-exports` (or `exports`) first, or pass "
                          "--export PATH to a posts.csv.gz from the site's /db_exports.json listing")
    t0 = time.monotonic()
    emit("phase", name="completeness report")
    log.info("report: comparing the archive with %s", path)
    exp = _load_export(db, path)
    exp["path"], exp["date"] = str(path), _export_date(path, out)
    exp["deleted"] = db.execute("SELECT COUNT(*) FROM rep_export WHERE deleted=1").fetchone()[0]

    # filters / tier (what the user chose not to download)
    match_sql, match_args = (spec.where(candidates=False) if spec is not None else ("1", []))
    filtered = spec is not None and spec.filtered()
    in_tier = None
    if spec is not None and (spec.top is not None or spec.limit_gb is not None):
        from .priority import PriorityQueue
        pq = PriorityQueue(store, spec)
        pq.ensure()
        end = pq.tier_end()
        in_tier = {r[0] for r in db.execute("SELECT post_id FROM queue WHERE rank <= ?", (end,))}
    matching = ({r[0] for r in db.execute(f"SELECT id FROM posts WHERE {match_sql}", match_args)}
                if filtered else None)

    exclude = set(getattr(naming, "director_exclude", DEFAULT_DIRECTOR_EXCLUDE))
    dir_names = _director_names(db)

    def directors_of(tag_cats, tags) -> list:
        if tag_cats:
            try:
                d = json.loads(tag_cats).get("director")
                if isinstance(d, list):
                    return [x for x in d if x not in exclude]
            except (ValueError, AttributeError):
                pass
        if dir_names is None:
            return [UNKNOWN_DIRECTOR]
        return [t for t in (tags or "").split() if t in dir_names and t not in exclude]

    counts = Counter()
    bytes_ = Counter()
    reasons = {b: Counter() for b in ("missing", "failed", "skipped")}
    by_dir = defaultdict(Counter)
    rows_out = []
    known = set()
    q = ("SELECT e.id, e.md5, e.ext, e.size, e.tags, p.file_status, p.file_path, p.md5, p.file_url, p.tag_cats, "
         "v.md5, fe.reason, fe.detail FROM rep_export e LEFT JOIN posts p ON p.id = e.id "
         "LEFT JOIN verified v ON v.post_id = e.id LEFT JOIN file_errors fe ON fe.post_id = e.id "
         "WHERE e.deleted = 0 AND e.md5 <> '' ORDER BY e.id")
    for (pid, md5, ext, size, tags, status, rel, pmd5, purl, tag_cats, vmd5, err, _detail) in db.execute(q):
        reason = ""
        if status is None:
            bucket, reason = "missing", "not_in_archive"
        elif status == "ok":
            rel = rel or legacy_relpath(pmd5 or md5, ext)
            known.add(_norm(rel))
            on_disk = True
            if check_disk:
                try:
                    on_disk = abspath(out, rel).stat().st_size > 0
                except OSError:
                    on_disk = False
            if pmd5 and pmd5 != md5:
                bucket, reason = "missing", "replaced_on_site"
            elif not on_disk:
                bucket, reason = "missing", "file_missing_on_disk"
            else:
                bucket = "verified" if vmd5 and vmd5 == md5 else "unverified"
        elif status == "pending":
            if matching is not None and pid not in matching:
                bucket, reason = "skipped", "filter"
            elif in_tier is not None and pid not in in_tier:
                bucket, reason = "skipped", "tier_limit"
            else:
                bucket, reason = "missing", "not_downloaded_yet"
        else:
            bucket = "failed"
            reason = err if status in ("failed", "md5_mismatch") and err else FAILED_STATUS_REASON.get(status, status)
        counts[bucket] += 1
        bytes_[bucket] += size or 0
        if reason:
            reasons[bucket][reason] += 1
        ds = directors_of(tag_cats, tags)
        primary = ds[0] if ds else NO_DIRECTOR
        by_dir[primary]["expected"] += 1
        by_dir[primary][bucket] += 1
        if bucket in ("missing", "failed", "skipped"):
            rows_out.append((pid, md5, ext, size or "", " ".join(ds) or NO_DIRECTOR, bucket, reason, err or "",
                             purl or file_url(md5, ext)))
    expected = sum(counts.values())
    expected_bytes = sum(bytes_.values())

    # saved posts the export does not expect; files no post points at
    kept_deleted = db.execute("SELECT COUNT(*) FROM posts p JOIN rep_export e ON e.id = p.id "
                              "WHERE p.file_status='ok' AND (e.deleted = 1 OR e.md5 = '')").fetchone()[0]
    not_in_export = db.execute("SELECT COUNT(*) FROM posts p WHERE p.file_status='ok' AND "
                               "p.id NOT IN (SELECT id FROM rep_export)").fetchone()[0]
    for (rel, md5, ext) in db.execute("SELECT file_path, md5, ext FROM posts WHERE file_status='ok' AND "
                                      "id NOT IN (SELECT id FROM rep_export WHERE deleted=0 AND md5<>'')"):
        known.add(_norm(rel or legacy_relpath(md5, ext)))
    known.update(_norm(r[0]) for r in db.execute("SELECT path FROM extra_files"))
    extra, parts, quarantined = _scan_files(out, known) if check_disk else ([], 0, 0)
    db.execute("DROP TABLE IF EXISTS temp.rep_export")

    saved = counts["verified"] + counts["unverified"]
    res = {
        "export": exp, "expected": expected, "expected_bytes": expected_bytes,
        "saved": saved, "saved_bytes": bytes_["verified"] + bytes_["unverified"],
        **{b: counts[b] for b in BUCKETS},
        "missing_bytes": bytes_["missing"], "failed_bytes": bytes_["failed"], "skipped_bytes": bytes_["skipped"],
        "reasons": {b: dict(reasons[b].most_common()) for b in reasons},
        "percent_saved": round(100.0 * saved / expected, 2) if expected else 0.0,
        "extra_files": len(extra), "extra_bytes": sum(s for _r, s in extra), "part_files": parts,
        "quarantined_files": quarantined, "saved_deleted_on_site": kept_deleted,
        "saved_not_in_export": not_in_export, "filters": spec.describe() if spec is not None else "",
        "disk_checked": check_disk,
        "directors": len([d for d in by_dir if d != NO_DIRECTOR]),
        "seconds": round(time.monotonic() - t0, 1),
    }
    res["summary"] = summary_line(res)
    by_dir_rows = sorted(((d, c) for d, c in by_dir.items()), key=lambda x: (-x[1]["expected"], x[0]))
    if write:
        res["files"] = _write(out, res, by_dir_rows, rows_out, extra, stamp)
        res["summary"] = summary_line(res)
        store.set_state("last_report", {"at": time.time(), **res["files"], "summary": res["summary"]})
        store.commit()
    res["top_directors"] = [{"director": d, **{k: c[k] for k in ("expected", *BUCKETS)}}
                            for d, c in by_dir_rows[:20]]
    log.info("%s", res["summary"])
    emit("report", summary=res["summary"], percent=res["percent_saved"], **(res.get("files") or {}))
    return res


def summary_line(r: dict) -> str:
    s = (f"completeness: {r['saved']:,} of {r['expected']:,} expected posts saved ({r['percent_saved']:.1f}%), "
         f"{r['verified']:,} md5-verified; missing {r['missing']:,}, failed {r['failed']:,}, "
         f"skipped by filters {r['skipped']:,}, extra files {r['extra_files']:,}")
    if r.get("files"):
        s += f" - report: {r['files']['txt']}"
    return s


def _gib(n) -> str:
    return f"{(n or 0) / 2**30:,.2f} GiB"


def _atomic_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _sections(r: dict) -> list:
    e = r["export"]
    pct = (lambda n: f"{100.0 * n / r['expected']:.1f}%" if r["expected"] else "-")
    main = [("Expected (not deleted, with a file)", r["expected"], _gib(r["expected_bytes"]), ""),
            ("Saved and md5-verified", r["verified"], "", pct(r["verified"])),
            ("Saved, not verified yet", r["unverified"], "", pct(r["unverified"])),
            ("Missing", r["missing"], _gib(r["missing_bytes"]), pct(r["missing"])),
            ("Failed", r["failed"], _gib(r["failed_bytes"]), pct(r["failed"])),
            ("Skipped by filters / tier", r["skipped"], _gib(r["skipped_bytes"]), pct(r["skipped"]))]
    other = [("Extra files (no post points at them)", r["extra_files"], _gib(r["extra_bytes"])),
             ("Leftover .part files", r["part_files"], ""),
             ("Files in quarantine/", r["quarantined_files"], ""),
             ("Saved posts now deleted on the site", r["saved_deleted_on_site"], ""),
             ("Saved posts newer than the export", r["saved_not_in_export"], "")]
    head = [("Export", e["path"]), ("Export date", e["date"]),
            ("Export rows", f"{e['rows']:,} ({e['deleted']:,} deleted)"),
            ("Filters", r["filters"] or "none"),
            ("Files on disk checked", "yes" if r["disk_checked"] else "no (--no-disk-check)")]
    return [head, main, other]


def _write(out: Path, r: dict, by_dir_rows, rows_out, extra, stamp) -> dict:
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    files = {"txt": str(out / f"completeness-report-{stamp}.txt"),
             "html": str(out / f"completeness-report-{stamp}.html"),
             "missing_csv": str(out / f"missing-{stamp}.csv"),
             "missing_latest": str(out / "missing.csv")}
    head, main, other = _sections(r)
    when = time.strftime("%Y-%m-%d %H:%M:%S %Z")

    # --- missing.csv
    tmp = Path(files["missing_csv"] + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLUMNS)
        w.writerows(rows_out)
    os.replace(tmp, files["missing_csv"])
    shutil.copyfile(files["missing_csv"], files["missing_latest"])
    if extra:
        files["extra_csv"] = str(out / f"extra-files-{stamp}.csv")
        tmp = Path(files["extra_csv"] + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(("path", "size"))
            w.writerows(extra)
        os.replace(tmp, files["extra_csv"])

    # --- text
    L = [f"Packrat completeness report ({_site_label()}) - {when}", f"Archive: {out}", ""]
    L += [f"{k + ':':<24}{v}" for k, v in head] + [""]
    L += [f"{k:<40}{n:>10,}  {pc:>6}  {b}" for k, n, b, pc in main] + [""]
    for b in ("missing", "failed", "skipped"):
        if r["reasons"][b]:
            L.append(f"{b.capitalize()} by reason:")
            L += [f"    {k:<36}{n:>10,}" for k, n in r["reasons"][b].items()]
    L.append("")
    L += [f"{k:<40}{n:>10,}  {b}" for k, n, b in other] + [""]
    W = _artist_word()
    L.append(f"Per {W} ({len(by_dir_rows):,}; a post with several {W}s counts under the first):")
    L.append(f"    {W:<40}{'expected':>9}{'verified':>9}{'unverif.':>9}{'missing':>9}{'failed':>9}"
             f"{'skipped':>9}")
    if any(d == UNKNOWN_DIRECTOR for d, _ in by_dir_rows):
        L.append(f"    ({UNKNOWN_DIRECTOR}: the tags export isn't loaded, so tags can't be split into {W}s "
                 f"for posts not fetched through the API - run `sync-exports` / the full archive)")
    for d, c in by_dir_rows:
        L.append(f"    {d[:40]:<40}" + "".join(f"{c[k]:>9,}" for k in ("expected", *BUCKETS)))
    L += ["", f"Not saved, one row per post: {files['missing_csv']} (copy: missing.csv)"]
    if extra:
        L.append(f"Extra files: {files['extra_csv']}")
    L += ["", "Reasons: not_in_archive = not in the database yet (run `full` or `seed-from-export`); "
          "not_downloaded_yet = queued; file_missing_on_disk = saved once, file gone; replaced_on_site = new md5; "
          "http_403/http_404/http_429/http_503/network_error/md5_mismatch/wrong_size = last download attempt; "
          "verify_* = removed by verify; login_only = no file URL when signed out; marked_deleted = deleted per the API or an older export; "
          "filter / tier_limit = left out by your download settings.", ""]
    _atomic_text(Path(files["txt"]), "\n".join(L))

    # --- html
    esc = html.escape
    H = ["<!doctype html><html><head><meta charset='utf-8'><title>Packrat completeness report</title>",
         "<style>body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#222}table{border-collapse:collapse;"
         "margin:8px 0 18px}td,th{border:1px solid #ccc;padding:3px 8px}td.n{text-align:right}"
         "th{background:#eee}.bar{height:14px;background:#ddd;width:420px}.bar div{height:14px;background:#3a7}"
         "a{color:#1f5fbf}"
         # 1.9: follows the system / browser dark mode
         "@media (prefers-color-scheme: dark){body{background:#1e1f22;color:#e3e5e8}td,th{border-color:#4a4d52}"
         "th{background:#2b2d31}.bar{background:#3a3d42}.bar div{background:#3fb37f}a{color:#8ab4f8}"
         "code{color:#f0c674}}"
         "</style><meta name='color-scheme' content='light dark'></head><body>",
         f"<h1>Completeness report</h1><p>{esc(when)} &mdash; archive <code>{esc(str(out))}</code></p>",
         f"<div class='bar'><div style='width:{min(100, r['percent_saved']):.1f}%'></div></div>",
         f"<p><b>{esc(r['summary'].split(' - report:')[0])}</b></p><table>"]
    H += [f"<tr><th>{esc(k)}</th><td>{esc(str(v))}</td></tr>" for k, v in head] + ["</table><table>"]
    H += [f"<tr><td>{esc(k)}</td><td class=n>{n:,}</td><td class=n>{pc}</td><td class=n>{b}</td></tr>"
          for k, n, b, pc in main] + ["</table>"]
    for b in ("missing", "failed", "skipped"):
        if r["reasons"][b]:
            H.append(f"<h3>{b.capitalize()} by reason</h3><table>")
            H += [f"<tr><td>{esc(k)}</td><td class=n>{n:,}</td></tr>" for k, n in r["reasons"][b].items()]
            H.append("</table>")
    H.append("<h3>Other</h3><table>")
    H += [f"<tr><td>{esc(k)}</td><td class=n>{n:,}</td><td class=n>{b}</td></tr>" for k, n, b in other]
    H.append(f"</table><h3>Per {W} ({len(by_dir_rows):,})</h3><p>A post with several {W}s counts under "
             f"the first.</p><table><tr><th>{W}</th>" + "".join(f"<th>{k}</th>" for k in ("expected", *BUCKETS))
             + "</tr>")
    for d, c in by_dir_rows:
        H.append(f"<tr><td>{esc(d)}</td>" + "".join(f"<td class=n>{c[k]:,}</td>" for k in ("expected", *BUCKETS))
                 + "</tr>")
    H.append(f"</table><p>Not saved, one row per post: <a href='{esc(Path(files['missing_csv']).name)}'>"
             f"{esc(Path(files['missing_csv']).name)}</a></p></body></html>")
    _atomic_text(Path(files["html"]), "\n".join(H))
    return files


def latest_report(out) -> Path | None:
    """Newest completeness-report-*.html in the output folder (for the control panel's Open report)."""
    try:
        found = sorted(Path(out).glob("completeness-report-*.html"))
    except OSError:
        return None
    return found[-1] if found else None
