"""Command-line interface."""
from __future__ import annotations

import argparse
import contextlib
import json
import logging
import sqlite3
import sys
from pathlib import Path

from . import __version__, redact, siteprofile
from .archiver import META_ENDPOINTS, Archiver, fetch_meta, fmt_bytes
from .auth import auto_retry, login_required_count, retry_login_required, test_login
from .client import API_RATE_GUIDE, DEFAULT_RATE, AuthError, Client, ForbiddenError, build_user_agent
from .config import resolve
from .events import StopRequested, attach_progress_stream, control, emit, watch_control_file
from .exports import (_sync_one, ensure_tags, list_exports, load_tags_export, read_manifest,
                      seed_from_posts_export, sync_exports, wanted_exports, with_tags, write_manifest)
from .failures import FAIL_CSV, FAIL_LOG, select_retry
from .naming import (DEFAULT_DIRECTOR_EXCLUDE, FOLDER_PRESETS, MULTI_DIRECTOR, NAME_PRESETS, Naming)
from .platform_utils import (DiskFullError, InstanceLock, KeepAwake, StorageError, check_writable, free_bytes,
                             install_signal_handlers, is_disk_full, setup_console, storage_problem)
from .priority import ORDERS, PriorityQueue, Spec
from .report import NoExportError, ReportError, build_report
from .store import RETRYABLE_STATUSES, DatabaseTooNewError, Store, finish_run_record, start_run_record

log = logging.getLogger("packrat")
COMMANDS = ("run", "full", "download", "meta", "recheck", "exports", "sync-exports", "seed-from-export", "stats",
            "verify", "preview", "reorganize", "repair-db", "report", "test-login", "retry-failed")
GLOBAL_VALUE_OPTS = {"--site", "--out", "--username", "--user-agent", "--rate", "--min-free-gb", "--log-file", "--config",
                     "--name-template", "--name-preset", "--folder-template", "--folder-preset",
                     "--multi-director", "--max-outage-hours", "--login", "--director-exclude", "--rating-style",
                     "--name-max-items", "--multi-sep", "--name-max", "--max-path", "--control-file",
                     "--workers", "--on-low-disk", "--failure-log-max-mb"}
GLOBAL_FLAG_OPTS = {"-v", "--verbose", "--no-keep-awake", "--gui", "--no-config", "--progress-json",
                    "--check-md5", "--no-check-md5"}
NETWORK_COMMANDS = {"run", "full", "download", "meta", "recheck", "exports", "sync-exports", "retry-failed"}
PRIORITY_COMMANDS = {"run", "full", "download", "preview", "report"}
RUN_RECORD_COMMANDS = {"run", "full", "download", "retry-failed"}  # 1.9: last_run record (resume prompt)
# exit codes (run.ps1 maps them to messages)
EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_403, EXIT_DISK_FULL, EXIT_LOCKED, EXIT_STORAGE, EXIT_DB = 0, 1, 2, 3, 4, 5, 6, 7
EXIT_AUTH = 8  # HTTP 401: login name / API key not accepted
EXIT_DB_NEWER = 11  # 2.0: the archive database was made by a newer version (refused, untouched)
EXIT_STOPPED = 130
GUI_COMMANDS = {"run", "full", "download", "meta", "recheck", "sync-exports", "seed-from-export", "verify",
                "reorganize", "repair-db", "retry-failed"}


def make_naming(a) -> Naming:
    """Validated naming/folder settings (bad templates fail here, before any request)."""
    ex = DEFAULT_DIRECTOR_EXCLUDE if a.director_exclude is None else tuple(
        t.strip() for t in a.director_exclude.split(",") if t.strip())
    return Naming(name_template=a.name_template, folder_template=a.folder_template,
                  multi_director=a.multi_director, max_items=a.max_items, sep=a.multi_sep,
                  rating_style=a.rating_style, name_max=a.name_max, max_path=a.max_path, director_exclude=ex)


def effective_login(a) -> str | None:
    """The login name used with the API key: --login / PACKRAT_LOGIN, else the username."""
    return (a.login or "").strip() or (a.username or "").strip() or None


def make_client(a, **kw) -> Client:
    ua = build_user_agent(a.username, a.user_agent, getattr(a, "site", None))
    key = (a.api_key or "").strip() or None
    login = effective_login(a) if key else None
    if a.login and not key:
        log.warning("login name '%s' is set but there is no API key (PACKRAT_API_KEY): running signed out", a.login)
    kw.setdefault("max_outage_hours", a.max_outage_hours)
    c = Client(ua, rate=a.rate, login=login, api_key=key, **kw)
    # never log the key itself; the log formatter also masks it should it appear anywhere
    log.info("User-Agent: %s | rate: %.2f req/s | auth: %s", ua, c.limiter.rate,
             f"signed in as {login} (API key from PACKRAT_API_KEY, not shown)" if c.authenticated else "no (anonymous)")
    return c


def _login_retry(a, c, s) -> None:
    auto_retry(c, s, enabled=getattr(a, "login_retry", True) is not False)


def _archiver(a, c, s, **over) -> Archiver:
    kw = dict(tags=getattr(a, "tags", None), metadata_only=getattr(a, "metadata_only", False),
              max_posts=getattr(a, "max_posts", None), since_id=getattr(a, "since_id", None),
              include_deleted=getattr(a, "include_deleted", False),
              min_free_bytes=int(a.min_free_gb * 2**30), naming=a.naming,
              spec=getattr(a, "spec", None),
              api_json=bool(getattr(a, "api_json", False)) and not getattr(a, "metadata_only", False),
              workers=a.workers, check_md5=bool(a.check_md5),
              on_low_disk=getattr(a, "on_low_disk", "pause") or "pause")
    kw.update(over)
    return Archiver(c, s, **kw)


class _Borrowed:
    """A client lent to ensure_tags (which closes what it opens): close() is a no-op."""
    def __init__(self, c):
        self._c = c

    def __getattr__(self, name):
        return getattr(self._c, name)

    def close(self):
        pass


def _anon_client(a) -> Client:
    """1.8: anonymous client for the tags export only (never the login / API key; few retries)."""
    anon = argparse.Namespace(**{**vars(a), "api_key": None, "login": None})
    return make_client(anon, max_retries=2, max_outage_hours=0.05)


def _tags_for_report(a, s, c=None) -> dict:
    """1.8: make sure the tags export is present, current and loaded before a report (director counts)."""
    fetch = getattr(a, "fetch_tags", True) is not False
    factory = None
    if fetch:
        factory = (lambda: _Borrowed(c)) if c is not None else (lambda: _anon_client(a))
    try:
        r = ensure_tags(s, factory, fetch=fetch)
    except (ValueError, OSError, EOFError) as e:  # e.g. a damaged local tags file
        r = {"fetched": False, "loaded": False, "categories": 0, "note": f"tags export unusable: {e}"}
        log.warning("%s", r["note"])
    return r


class _OfflineRate:  # lets --dry-run report timings without building a networked client
    def __init__(self, rate):
        self.limiter = type("L", (), {"rate": float(rate)})()


def cmd_run(a, c, s):
    arch = _archiver(a, c, s)
    if a.dry_run:
        print(json.dumps({"dry_run": arch.plan()}, indent=2))
        return
    _login_retry(a, c, s)
    stats = arch.run()
    print(json.dumps({"run": stats, "requests": c.request_count}, indent=2))


def cmd_full(a, c, s):
    """Everything in one go: export bundle (daily, verified) -> merge -> priority downloads -> API
    catch-up. Re-running skips what is already done, so it is also the "resume" command."""
    res = sync_exports(c, s, a.exports, force=a.refresh_export)
    if not s.get_state("seeded_export"):
        log.warning("no posts export available; crawling everything through the API (slower)")
    log.info("exports: %s", {k: v for k, v in res.items() if k != "posts_merge"})
    _login_retry(a, c, s)
    arch = _archiver(a, c, s)
    stats = arch.run()
    out = {"full": stats, "exports": res}
    if a.verify_after:
        # 1.6: files saved without an inline md5 check are verified now; bad ones are re-downloaded
        out["verify"] = arch.verify_all(action=a.verify_action, hash_workers=a.hash_workers, only_unverified=True)
        if out["verify"]["requeued"]:
            arch.download_pending()
            out["after_verify"] = {k: arch.stats[k] for k in ("downloaded", "failed", "missing", "md5_mismatch")}
    out["requests"] = c.request_count
    rc = _report_after(a, c, s, out)
    out["requests"] = c.request_count  # (key order kept: the report stays last)
    print(json.dumps(out, indent=2))
    return rc


def _report_after(a, c, s, out: dict):
    """1.7/1.8: completeness report at the end of full / retry-failed. Its failure is the only thing
    that changes the exit code; the downloads themselves already finished."""
    if not a.report_after:
        return None
    t = _tags_for_report(a, s, c)
    out["tags_export"] = t
    try:
        out["report"] = _report_brief(run_report(a, s))
    except NoExportError as e:  # e.g. the export was unavailable and the API crawl was used
        log.warning("completeness report skipped: %s", e)
        out["report"] = {"skipped": str(e)}
    except (ReportError, OSError, sqlite3.Error, RuntimeError) as e:
        log.error("completeness report failed: %s", e)
        out["report"] = {"error": str(e)}
        return EXIT_ERROR
    return None


def run_report(a, s) -> dict:
    return build_report(s, spec=a.spec, naming=a.naming, export_path=getattr(a, "export", None),
                        check_disk=not getattr(a, "no_disk_check", False))


def _report_brief(r: dict) -> dict:
    keys = ("expected", "saved", "verified", "unverified", "missing", "failed", "skipped", "extra_files",
            "percent_saved", "reasons", "files", "summary")
    return {k: r[k] for k in keys if k in r}


def cmd_report(a, s):
    """1.7: compare the archive with the latest posts export. 1.8: first makes sure the tags export is
    there and current (anonymous download of that one file if needed; --no-fetch-tags = fully offline)."""
    t = _tags_for_report(a, s)
    r = run_report(a, s)
    r["tags_export"] = t
    if a.json:
        print(json.dumps(r, indent=2, default=str))
    else:
        if t.get("note"):
            print(f"note: {t['note']}\n")
        print(Path(r["files"]["txt"]).read_text("utf-8"))


def cmd_retry_failed(a, c, s):
    """1.8: re-attempt only files currently marked failed / missing / md5_mismatch (optionally only some
    failure reasons, or the posts listed in a CSV such as missing.csv). Nothing else is downloaded."""
    sel = select_retry(s, a.reason, a.from_csv)
    ids = sel["selected"]
    summary = {"selected": len(ids), "by_reason": dict(sel["by_reason"].most_common()),
               "skipped": dict(sel["skipped"]), "reason_filter": a.reason or "all",
               "from_csv": a.from_csv or None}
    if a.dry_run or not ids:
        if not ids:
            log.info("retry-failed: no failed files match (reason filter: %s)", a.reason or "all")
        summary["dry_run"] = bool(a.dry_run)
        print(json.dumps({"retry_failed": summary}, indent=2))
        return None
    prior = dict(_status_map(s, ids))
    log.info("retry-failed: %d file(s) selected (%s)", len(ids),
             ", ".join(f"{k} {v}" for k, v in summary["by_reason"].items()))
    s.set_pending(ids)
    try:
        counts = _archiver(a, c, s, spec=None).download_ids(ids)
    finally:
        # stopped part-way (Stop / 403 streak / disk): files not attempted keep their old failed status
        n = s.restore_failed(ids, prior)
        if n:
            log.info("retry-failed: %d file(s) not attempted this time keep their failed status", n)
    summary["recovered"] = counts.get("ok", 0)
    summary["still_failed"] = sum(counts.get(k, 0) for k in RETRYABLE_STATUSES)
    summary["status_after"] = counts
    summary["failure_log"] = {"csv": str(s.out / FAIL_CSV), "log": str(s.out / FAIL_LOG)}
    out = {"retry_failed": summary}
    out["requests"] = c.request_count
    rc = _report_after(a, c, s, out)
    out["requests"] = c.request_count  # (key order kept: the report stays last)
    print(json.dumps(out, indent=2, default=str))
    return rc


def _status_map(s, ids):
    for k in range(0, len(ids), 500):
        part = ids[k:k + 500]
        yield from s.db.execute(f"SELECT id, file_status FROM posts WHERE id IN ({','.join('?' * len(part))})", part)


def cmd_sync_exports(a, c, s):
    """Refresh the export bundle (at most once a day unless --force) and merge it, no file downloads."""
    res = sync_exports(c, s, a.exports, force=a.force)
    man = read_manifest(s.out)
    res["local_bundle"] = {k: {"bytes": v.get("size"), "updated_at": v.get("updated_at")}
                           for k, v in sorted(man.items()) if not k.startswith("previous/")}
    res["local_bundle_total_MB"] = round(sum(v.get("size") or 0 for k, v in man.items()
                                             if not k.startswith("previous/")) / 1e6, 1)
    if a.spec is not None:
        pq = PriorityQueue(s, a.spec)
        pq.ensure()
        res["queue"] = {"order": a.spec.describe(), "ranked": s.db.execute("SELECT COUNT(*) FROM queue").fetchone()[0],
                        "pending_in_tier": pq.pending_in_tier()[0]}
    print(json.dumps({"sync_exports": res, "requests": c.request_count}, indent=2, default=str))


def cmd_preview(a, s):
    """Offline: the next files in download order, where they'd be saved, and tier sizes/times."""
    pq = PriorityQueue(s, a.spec)
    pq.ensure()
    where = {"any": "", "pending": "AND p.file_status='pending'", "ok": "AND p.file_status='ok'"}[a.status]
    rows = s.db.execute(f"SELECT q.rank, p.id, p.file_status, p.file_path, p.score, p.fav_count, p.rating, p.size, "
                        f"q.cum_bytes FROM queue q JOIN posts p ON p.id = q.post_id WHERE q.rank > 0 {where} "
                        f"ORDER BY q.rank LIMIT ?", (a.limit,)).fetchall()
    items = []
    for rank, pid, st, cur, score, favs, rating, size, cum in rows:
        info = s.post_info(pid)
        planned = a.naming.plan(info, len(str(s.out)))
        items.append({"rank": rank, "id": pid, "status": st, "score": score, "favs": favs, "rating": rating,
                      "size": size, "cum_gib": round((cum or 0) / 2**30, 3), "current": cur, "path": planned[0],
                      "extra": planned[1:], "categorized": info.categorized,
                      "directors": ", ".join(a.naming.directors(info)) or "-"})
    tiers = pq.tiers(rate=None, mbps=a.assume_mbps)
    if a.json:
        print(json.dumps({"order": a.spec.describe(), "tier_end_rank": pq.tier_end(), "rows": items,
                          "tiers": tiers}, indent=2, ensure_ascii=False))
        return
    print(f"order:  {a.spec.describe()}\nname:   {a.naming.name_template}\nfolder: "
          f"{a.naming.folder_template or '(flat)'}  multi-director: {a.naming.multi_director}\n")
    print(f"{'rank':>7} {'id':>7} {'score':>6} {'favs':>6} r {'MiB':>7} {'cum GiB':>8} {'status':<8} path")
    for r in items:
        moved = f"   (now at {r['current']}; `reorganize` would move it)" if r["current"] and r["current"] != r["path"] else ""
        print(f"{r['rank']:>7} {r['id']:>7} {_n(r['score']):>6} {_n(r['favs']):>6} {r['rating'] or '-'} "
              f"{(r['size'] or 0) / 2**20:>7.1f} {r['cum_gib']:>8.2f} {r['status']:<8} {r['path']}{moved}")
        for x in r["extra"]:
            print(f"{'':>7} {'':>7} {'':>6} {'':>6}   {'':>7} {'':>8} {'+copy':<8} {x}")
    if not items:
        print("(nothing in the queue: no downloadable posts match this order/filters yet)")
    print(f"\ntiers (time = bytes / {a.assume_mbps:g} Mbit/s: parallel downloads are bandwidth-bound):")
    print(f"  {'tier':<12} {'files':>8} {'GiB':>9} {'hours':>7}   {'left':>8} {'left GiB':>9} {'left h':>7}")
    for t in tiers:
        mark = "  <- selected (--top/--limit-gb)" if t["selected_tier"] else ""
        print(f"  {t['tier']:<12} {t['files']:>8,} {t['gib']:>9,.2f} {t['hours']:>7,.1f}   {t['pending_files']:>8,} "
              f"{t['pending_gib']:>9,.2f} {t['remaining_hours']:>7,.1f}{mark}")
    if any(not r["categorized"] for r in items):
        print("\nnote: some posts have no tag categories yet (tags export not loaded) -> _no_director.")


def _n(v):
    return "-" if v is None else v


def cmd_reorganize(a, s):
    from .layout import reorganize
    res = reorganize(s, a.naming, apply=a.apply, limit=a.limit)
    print(json.dumps({"reorganize": res}, indent=2, ensure_ascii=False))
    if not a.apply:
        print("\nDry run: nothing was moved. Run `reorganize --apply` to do it (never deletes files).")


def cmd_repair_db(a, s):
    s.recover("repair-db requested")
    print(json.dumps({"repair_db": s.recovered}, indent=2))


def cmd_download(a, c, s):
    if a.retry_failed and not a.dry_run:
        log.info("re-queued %d files with status %s", s.reset_retryable(), "/".join(RETRYABLE_STATUSES))
    arch = _archiver(a, c, s)
    if a.dry_run:
        plan = arch.plan()
        if a.retry_failed:
            plan["would_requeue"] = s.db.execute(
                f"SELECT COUNT(*) FROM posts WHERE file_status IN ({','.join('?' * len(RETRYABLE_STATUSES))})",
                RETRYABLE_STATUSES).fetchone()[0]
        print(json.dumps({"dry_run": plan}, indent=2))
        return
    _login_retry(a, c, s)
    n, pend = s.pending_summary()
    free = free_bytes(s.out)
    log.info("pending downloads: %d files, %s; free disk: %s", n, fmt_bytes(pend), fmt_bytes(free))
    if pend > free:
        log.warning("Pending files exceed free space; the run will stop cleanly when the "
                    "--min-free-gb reserve is reached.")
    arch.download_pending()
    print(json.dumps({"download": arch.stats, "requests": c.request_count}, indent=2))


def cmd_meta(a, c, s):
    out = {kind: fetch_meta(c, s, kind, a.meta_max_items) for kind in a.kinds}
    print(json.dumps({"meta": out, "requests": c.request_count}, indent=2))


def cmd_recheck(a, c, s):
    """Re-query posts recorded with no file url (e.g. after signing in), deleted ones included."""
    if not c.authenticated:
        log.warning("not signed in: posts hidden from anonymous visitors will stay hidden")
    res = retry_login_required(c, s, max_posts=a.max_posts, include_deleted=True)
    print(json.dumps({"rechecked": res["checked"], "now_downloadable": res["now_downloadable"],
                      "still_hidden": res["still_hidden"], "not_returned": res["not_returned"],
                      "requests": c.request_count}))


def cmd_test_login(a) -> int:
    """One signed-in request to check the login name + API key. No archive folder needed."""
    if not (a.api_key or "").strip():
        print("error: no API key. Set the PACKRAT_API_KEY environment variable (never put it on the command "
              "line); the control panel does this for you.", file=sys.stderr)
        return EXIT_USAGE
    if not a.username:
        print("error: --username is required (it goes into the User-Agent)", file=sys.stderr)
        return EXIT_USAGE
    try:
        c = make_client(a, max_retries=2, max_outage_hours=0.05)
    except ValueError as e:
        log.error("%s", e)
        return EXIT_USAGE
    try:
        res = test_login(c, c.login)
    except AuthError:
        print(json.dumps({"test_login": {"ok": False, "login": c.login, "reason": "rejected (HTTP 401)",
                                         "message": f"{siteprofile.current().label} did not accept this login name + API key."}}))
        log.error("sign-in rejected (HTTP 401): check the login name and API key (not your password)")
        return EXIT_AUTH
    except ForbiddenError as e:
        log.error("%s", e)
        log.error("STOPPED on 403. Do not retry with a different/browser User-Agent.")
        return EXIT_403
    except StopRequested:
        return EXIT_STOPPED
    except (RuntimeError, OSError) as e:
        log.error("sign-in test failed: %s", e)
        return EXIT_ERROR
    finally:
        c.close()
    print(json.dumps({"test_login": res}, indent=2))
    if res.get("warning"):
        log.warning("%s", res["warning"])
    return EXIT_OK if res["ok"] else EXIT_AUTH


def cmd_exports(a, c, s):
    ex = list_exports(c)
    if ex is None:
        print("This site does not expose /db_exports.json (404). Use the API crawl instead.")
        return
    print(json.dumps(ex, indent=2))
    if a.download:
        want = with_tags(",".join(a.download))  # 1.8: the posts export always comes with the tags export
        by = {e.get("name"): e for e in ex if isinstance(e, dict)}
        names = wanted_exports(want if "all" not in a.download else "all", list(by))
        unknown = [n for n in names if n not in by]
        if unknown:
            log.warning("no export named: %s", ", ".join(sorted(unknown)))
        man = read_manifest(s.out)
        res = {"downloaded": [], "unchanged": [], "failed": {}}
        for n in names:
            if n in by:  # verified, validated, previous copy kept (same as sync-exports, without merging)
                _sync_one(c, s.out, by[n], man, res)
                write_manifest(s.out, man)
        print(json.dumps({"exports_download": res}, indent=2))


def cmd_seed(a, s):
    path = Path(a.csv) if a.csv else Path(s.out) / "db_export" / "posts.csv.gz"
    if not path.is_file():
        raise ValueError(f"{path} not found; run `exports --download posts` first or pass --csv")
    tags = Path(a.tags_csv) if a.tags_csv else Path(s.out) / "db_export" / "tags.csv.gz"
    if a.tags_csv and not tags.is_file():
        raise ValueError(f"{tags} not found")
    if tags.is_file():
        load_tags_export(s, tags)
    st = seed_from_posts_export(s, path, mark_backfill_done=not a.keep_backfill)
    n, b = s.pending_summary()
    st["pending_files_now"], st["pending_bytes_now"] = n, b
    print(json.dumps({"seed": st}, indent=2))


def cmd_stats(a, s):
    db = s.db
    total, = db.execute("SELECT COUNT(*) FROM posts").fetchone()
    by_status = dict(db.execute("SELECT file_status, COUNT(*) FROM posts GROUP BY 1").fetchall())
    known_bytes, n_sized = db.execute(
        "SELECT COALESCE(SUM(size),0), COUNT(size) FROM posts WHERE size IS NOT NULL").fetchone()
    ok_bytes, = db.execute("SELECT COALESCE(SUM(size),0) FROM posts WHERE file_status='ok'").fetchone()
    pend_n, pend_bytes = s.pending_summary()
    max_id, min_id = db.execute("SELECT MAX(id), MIN(id) FROM posts").fetchone()
    by_ext = dict(db.execute("SELECT ext, COUNT(*) FROM posts GROUP BY 1").fetchall())
    out = {"posts": total, "id_range": [min_id, max_id], "file_status": by_status,
           "tag_categories_loaded": db.execute("SELECT COUNT(*) FROM tag_categories").fetchone()[0],
           "by_ext": by_ext, "bytes_on_disk_verified": ok_bytes, "bytes_known_all": known_bytes,
           "pending_files": pend_n, "bytes_pending_exact": pend_bytes,
           "GiB_pending_exact": round(pend_bytes / 2**30, 1),
           "login_required": {"posts_without_file_url": login_required_count(s),
                              "note": "not deleted, saved without a file URL (hidden from the account that "
                                      "fetched them); a signed-in run re-checks them automatically"}}
    if n_sized:
        avg = known_bytes / n_sized
        out["avg_file_bytes"] = round(avg)
        if max_id:
            out["ESTIMATE_total_bytes_if_every_id_up_to_max_had_a_file"] = round(avg * max_id)
            out["ESTIMATE_total_GiB"] = round(avg * max_id / 2**30, 1)
            out["estimate_note"] = ("Upper-bound style estimate: avg size of posts seen x highest id. "
                                    "Seed from the posts export for an exact figure (bytes_pending_exact).")
    print(json.dumps(out, indent=2))


def cmd_verify(a, s, c=None):
    """Check every saved file's md5 (parallel, resumable); bad files are deleted or quarantined and
    re-queued. With --redownload (needs the network) the re-queued files are downloaded right away."""
    arch = Archiver(c, s, naming=a.naming, workers=a.workers, check_md5=bool(a.check_md5),
                    min_free_bytes=int(a.min_free_gb * 2**30))
    res = arch.verify_all(action=a.verify_action, hash_workers=a.hash_workers, only_unverified=a.only_new,
                          restart=a.restart)
    out = {"verify": res}
    if c is not None:
        n, b = s.pending_summary()
        log.info("re-downloading %d missing / damaged files (%s)", n, fmt_bytes(b))
        arch.download_pending()
        out["redownload"] = arch.stats
        out["requests"] = c.request_count
    print(json.dumps(out, indent=2))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="packrat",
                                description="Packrat. These commands are the SITE ARCHIVE (a complete copy of "
                                            "e6ai or e621: daily exports + JSON API). Browse / Downloads side: "
                                            "packrat grab | batch | monitors | sources | import-grabber | "
                                            "tools --help.  The window: packrat gui  (only the site archive "
                                            "window: packrat --gui).")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    g = p.add_argument_group("common (precedence: command line > environment > config file > default)")
    g.add_argument("--config", help="INI config file (or PACKRAT_CONFIG; default ./packrat.ini if present)")
    g.add_argument("--no-config", action="store_true",
                   help="ignore config files and PACKRAT_CONFIG (the control panel passes every setting explicitly)")
    g.add_argument("--site", type=str.lower, choices=tuple(siteprofile.SITES),
                   help="which site this archive is for: e6ai (default) or e621 (PACKRAT_SITE). One archive "
                        "folder holds one site")
    g.add_argument("--out", help="output directory (PACKRAT_OUT; default ./archive)")
    g.add_argument("--username", help="your username on the site, used in the User-Agent (PACKRAT_USERNAME)")
    g.add_argument("--user-agent", help="full custom User-Agent (PACKRAT_USER_AGENT). Never a browser UA.")
    g.add_argument("--rate", type=float,
                   help=f"API requests/second (PACKRAT_RATE; default {DEFAULT_RATE:g}; the e621-family API docs ask "
                        f"for at most ~{API_RATE_GUIDE:g}/s). File downloads use --workers instead")
    g.add_argument("--workers", type=int,
                   help="parallel file downloads from the static file host, 1-64 (PACKRAT_WORKERS; default 16)")
    g.add_argument("--check-md5", action=argparse.BooleanOptionalAction, default=None,
                   help="check each file's md5 while downloading (PACKRAT_CHECK_MD5; default off: sizes are "
                        "checked, `verify` checks md5s afterwards)")
    g.add_argument("--min-free-gb", type=float,
                   help="stop cleanly when free disk space would drop below this (PACKRAT_MIN_FREE_GB; default 5)")
    g.add_argument("--on-low-disk", type=str.lower, choices=("pause", "stop"),
                   help="when free space would drop below --min-free-gb: pause and wait for space (default; "
                        "resumes by itself) or stop cleanly with exit code 4 (PACKRAT_ON_LOW_DISK)")
    g.add_argument("--failure-log-max-mb", type=float,
                   help="trim failed-downloads.csv / .log above this size; entries of files that are not "
                        "failed any more move to failed-downloads-archive.*.gz, current failures are always "
                        "kept (PACKRAT_FAILURE_LOG_MAX_MB; default 10, 0 = never trim)")
    g.add_argument("--log-file", help="also write the log to this file, UTF-8 (PACKRAT_LOG_FILE)")
    g.add_argument("--no-keep-awake", action="store_true", default=None,
                   help="on Windows, allow the PC to sleep while running (default: keep awake)")
    g.add_argument("-v", "--verbose", action="store_true")
    g.add_argument("--gui", action="store_true", default=None,
                   help="with no command: open the control panel; with a command: show the progress window "
                        "(PACKRAT_GUI / gui=yes; falls back to the console if unavailable)")
    g.add_argument("--login", help="login name for sign-in (PACKRAT_LOGIN; default: --username). The API key "
                                   "is read only from the PACKRAT_API_KEY environment variable, never the "
                                   "command line")
    g.add_argument("--max-outage-hours", type=float,
                   help="keep waiting this long for the network/site to come back (default 12)")
    g.add_argument("--progress-json", action="store_true", help=argparse.SUPPRESS)  # control panel channel
    g.add_argument("--control-file", help=argparse.SUPPRESS)                         # control panel Stop/Pause
    n = p.add_argument_group("file names and folders (see README: Naming)")
    n.add_argument("--name-template", help="e.g. \"{source_name}_{id}.{ext}\" (PACKRAT_NAME_TEMPLATE)")
    n.add_argument("--name-preset", choices=sorted(NAME_PRESETS), dest="name_template_preset",
                   help="default = {source_name}_{id}.{ext}; detailed = id+director+character+species+rating; "
                        "minimal = {id}.{ext}")
    n.add_argument("--folder-template", help="e.g. \"{director}/{rating}\", '' or 'flat' (PACKRAT_FOLDER_TEMPLATE)")
    n.add_argument("--folder-preset", choices=sorted(FOLDER_PRESETS), dest="folder_template_preset")
    n.add_argument("--multi-director", choices=MULTI_DIRECTOR,
                   help="posts with several directors: first (default) | multi (joined folder name) | "
                        "copy / hardlink (one copy per director folder)")
    n.add_argument("--director-exclude", help="tags never used as {director}, comma-separated "
                                              "(PACKRAT_DIRECTOR_EXCLUDE; default: built-in list)")
    n.add_argument("--rating-style", choices=("word", "letter"), help="{rating} as safe/questionable/explicit "
                                                                       "(word, default) or s/q/e (letter)")
    n.add_argument("--name-max-items", dest="max_items", type=int,
                   help="max tags per multi-value field such as {character} (1-50, default 3)")
    n.add_argument("--multi-sep", help="separator between several values in one field (default ',')")
    n.add_argument("--name-max", type=int, help="max file name length (0 = automatic, else 30-200)")
    n.add_argument("--max-path", type=int, help="max full path length (default 240, minimum 100)")
    sub = p.add_subparsers(dest="cmd")

    prio = argparse.ArgumentParser(add_help=False)
    o = prio.add_argument_group("download order and filters (see README: Download order)")
    o.add_argument("--order", type=str.lower, choices=ORDERS,
                   help="default/newest = newest first; popular = score, then favs; favorites = favs, then score; "
                        "oldest; smallest-first; balanced = 0.5 score pct + 0.3 favs pct + 0.2 small-file pct "
                        "(PACKRAT_ORDER)")
    o.add_argument("--min-score", type=int, help="only posts with score >= N (missing score counts as 0)")
    o.add_argument("--min-favs", type=int, help="only posts with at least N favorites")
    o.add_argument("--rating", help="only these ratings, e.g. s,q (s=safe q=questionable e=explicit)")
    o.add_argument("--director", help="only posts by any of these directors, comma-separated")
    o.add_argument("--max-size-mb", type=float, help="skip files larger than N MiB")
    o.add_argument("--exclude-ext", help="skip these file types, e.g. webm,mp4")
    o.add_argument("--limit-gb", type=float, help="tier: only the first N GiB of the queue (in order); stops there")
    o.add_argument("--top", type=int, help="tier: only the first N files of the queue (in order)")
    o.add_argument("--api-json", action=argparse.BooleanOptionalAction, default=None,
                   help="before downloading export-seeded files, fetch their fresh API JSON in batches of 100 "
                        "(1 request per 100 files; default on, PACKRAT_API_JSON)")
    o.add_argument("--autostart", dest="gui_autostart", action="store_true", default=None,
                   help="GUI: start right away instead of waiting on the order/filter panel (PACKRAT_GUI_AUTOSTART)")
    lr = argparse.ArgumentParser(add_help=False)
    lr.add_argument("--login-retry", action=argparse.BooleanOptionalAction, default=None,
                    help="when signed in, first re-fetch posts that were saved without a file URL and queue "
                         "the ones now visible (default on, PACKRAT_LOGIN_RETRY)")
    bundle = argparse.ArgumentParser(add_help=False)
    bundle.add_argument("--exports", help="export bundle: all (default) | core (posts,tags,pools) | comma list "
                                          "(PACKRAT_EXPORTS)")

    f = sub.add_parser("full", parents=[prio, bundle, lr],
                       help="exports + merge + downloads (in --order) + API catch-up, in one resumable command")
    f.add_argument("--verify-after", action=argparse.BooleanOptionalAction, default=None,
                   help="when the downloads are done, check the md5 of every file not checked yet and "
                        "re-download bad ones (PACKRAT_VERIFY_AFTER; default on)")
    f.add_argument("--verify-action", choices=("delete", "quarantine"),
                   help="what to do with bad files (PACKRAT_VERIFY_ACTION; default delete)")
    f.add_argument("--hash-workers", type=int, help="hashing threads for verify (default: one per CPU core)")
    f.add_argument("--report-after", action=argparse.BooleanOptionalAction, default=None,
                   help="at the end (after verify), write the completeness report: archive vs the posts export "
                        "(PACKRAT_REPORT_AFTER; default on)")
    f.add_argument("--refresh-export", action="store_true",
                   help="check/download the exports now even if checked in the last 20 hours")
    f.add_argument("--max-posts", type=int, help="stop after saving N posts from the API this run")
    f.add_argument("--tags", "--filter-tags", dest="filter_tags",
                   help="only posts with all these tags (space/comma separated; -tag excludes), from local metadata")

    se = sub.add_parser("sync-exports", parents=[prio, bundle],
                        help="refresh the export bundle (max once a day), verify it and merge it into the DB")
    se.add_argument("--force", "--refresh-export", action="store_true", dest="force",
                    help="check now even if checked in the last 20 hours")

    r = sub.add_parser("run", parents=[prio, lr], help="incremental update + resumable backfill (default)")
    r.add_argument("--filter-tags", help="download filter on local metadata (all these tags; -tag excludes)")
    r.add_argument("--metadata-only", action="store_true", help="save metadata, skip file downloads")
    r.add_argument("--max-posts", type=int, help="stop after saving N posts this run")
    r.add_argument("--tags", help="tag search query to restrict the archive (separate checkpoint per query)")
    r.add_argument("--since-id", type=int, help="only archive posts with id > N")
    r.add_argument("--include-deleted", action="store_true",
                   help="add status:any so deleted posts' metadata is recorded too")
    r.add_argument("--dry-run", action="store_true",
                   help="no network: show pending files/bytes, free space and minimum time, then exit")

    d = sub.add_parser("download", parents=[prio, lr],
                       help="download files still pending, in --order (e.g. after --metadata-only or seeding)")
    d.add_argument("--tags", "--filter-tags", dest="filter_tags",
                   help="only posts with all these tags (space/comma separated; -tag excludes)")
    d.add_argument("--retry-failed", action="store_true",
                   help="first re-queue files that ended as missing / md5_mismatch / failed")
    d.add_argument("--dry-run", action="store_true", help="no network: report what would be downloaded")

    m = sub.add_parser("meta", help="fetch pools / tags / wiki_pages / artists metadata (resumable)")
    m.add_argument("kinds", nargs="+", choices=sorted(META_ENDPOINTS))
    m.add_argument("--max-items", dest="meta_max_items", type=int, help="stop after N items per kind")

    sub.add_parser("test-login", help="check the login name + API key with ONE signed-in request (key from "
                                      "PACKRAT_API_KEY)")
    rc = sub.add_parser("recheck", help="re-query posts that had no file url (e.g. after signing in)")
    rc.add_argument("--max-posts", type=int)

    e = sub.add_parser("exports", help="list (and optionally download) daily DB exports")
    e.add_argument("--download", nargs="*", metavar="NAME", help="names to download (posts, pools, tags, ... or all)")

    sd = sub.add_parser("seed-from-export", help="load a downloaded posts.csv.gz into the DB (no network)")
    sd.add_argument("--csv", help="path to posts.csv.gz (default: <out>/db_export/posts.csv.gz)")
    sd.add_argument("--tags-csv", help="tags.csv.gz for tag categories (default: <out>/db_export/tags.csv.gz if present)")
    sd.add_argument("--keep-backfill", action="store_true",
                    help="don't mark the API backfill as done (use if you also want full API JSON for old posts)")

    sub.add_parser("stats", help="local statistics and a size estimate (no network)")
    v = sub.add_parser("verify", help="check the md5 of every saved file (parallel, resumable); delete or "
                                      "quarantine bad ones and re-queue them (no network unless --redownload)")
    v.add_argument("--verify-action", "--action", dest="verify_action", choices=("delete", "quarantine"),
                   help="bad (zero-byte / truncated / wrong md5) files: delete (default) or move to "
                        "<out>/quarantine/ (PACKRAT_VERIFY_ACTION)")
    v.add_argument("--redownload", action=argparse.BooleanOptionalAction, default=False,
                   help="afterwards, download the files that are now missing (uses the network; default off "
                        "here, on in the control panel)")
    v.add_argument("--hash-workers", type=int, help="hashing threads (default: one per CPU core)")
    v.add_argument("--only-new", action="store_true",
                   help="skip files whose md5 was already checked (inline or by an earlier verify)")
    v.add_argument("--restart", action="store_true", help="start over instead of resuming an interrupted verify")
    pv = sub.add_parser("preview", parents=[prio],
                        help="next files in download order, their paths, and tier sizes/times (no network)")
    pv.add_argument("--count", "--limit", dest="limit", type=int, default=20, help="rows to show (default 20)")
    pv.add_argument("--status", choices=("any", "pending", "ok"), default="any")
    pv.add_argument("--tags", "--filter-tags", dest="filter_tags", help="only posts with all these tags")
    pv.add_argument("--assume-mbps", type=float, default=50.0,
                    help="download bandwidth for the time estimate (default 50 Mbit/s)")
    pv.add_argument("--dry-run", action="store_true", help="accepted for symmetry; preview never uses the network")
    pv.add_argument("--json", action="store_true")
    rp = sub.add_parser("report", parents=[prio],
                        help="completeness report: archive vs the latest posts export - expected / verified / "
                             "missing / failed (with reason) / skipped / extra files, per director; writes "
                             "completeness-report-*.txt/.html and missing.csv (no network)")
    rp.add_argument("--tags", "--filter-tags", dest="filter_tags",
                    help="the tag filter your downloads use (posts it excludes count as skipped)")
    rp.add_argument("--export", help="compare with this posts export (.csv.gz) instead of db_export/posts.csv.gz")
    rp.add_argument("--no-disk-check", action="store_true",
                    help="trust the database: don't stat saved files or look for extra files (faster)")
    rp.add_argument("--json", action="store_true", help="print the full result as JSON instead of the text report")
    rp.add_argument("--fetch-tags", action=argparse.BooleanOptionalAction, default=None,
                    help="if the tags export is missing or older than the posts export, download it first "
                         "(anonymous, sha256-verified; one listing + one file) so per-director counts work "
                         "(PACKRAT_FETCH_TAGS; default on; --no-fetch-tags = no network at all)")
    rf = sub.add_parser("retry-failed", parents=[prio],
                        help="re-download ONLY files currently marked failed / missing / md5_mismatch "
                             "(optionally by --reason or from a CSV); updates failed-downloads.csv and the report")
    rf.add_argument("--tags", "--filter-tags", dest="filter_tags",
                    help="the tag filter your downloads use (only shapes the report written afterwards)")
    rf.add_argument("--reason", help="only these failures, comma-separated: 403, 404, 429, 503, http (any HTTP "
                                     "error), network, md5 (md5 or size mismatch), size, verify, unknown, or an "
                                     "exact reason such as http_403 (default: all)")
    rf.add_argument("--from-csv", help="retry the posts listed in this CSV (post_id column): missing.csv, "
                                       "failed-downloads.csv or your own list")
    rf.add_argument("--report-after", action=argparse.BooleanOptionalAction, default=None,
                    help="afterwards, rewrite the completeness report (PACKRAT_REPORT_AFTER; default on)")
    rf.add_argument("--fetch-tags", action=argparse.BooleanOptionalAction, default=None,
                    help="refresh the tags export for that report if needed (PACKRAT_FETCH_TAGS; default on)")
    rf.add_argument("--dry-run", action="store_true", help="no network: list how many would be retried, by reason")
    rf.epilog = ("Files are retried newest first. The order/filter options only shape the completeness report "
                 "written afterwards (posts your filters exclude count as skipped there).")
    ro = sub.add_parser("reorganize", help="move saved files to the current naming/folder layout (dry run first)")
    ro.add_argument("--apply", action="store_true", help="actually move files (default: dry run)")
    ro.add_argument("--limit", type=int, help="only check the first N files")
    sub.add_parser("repair-db", help="back up the database and rebuild it from the JSONL journals (no network)")
    return p


def insert_default_command(argv: list[str]) -> list[str]:
    """`packrat --username x --max-posts 5` means `... run --max-posts 5`.

    Walks global options (skipping their values) to the first other token; if that token is
    not a subcommand (or help/version), "run" is inserted there.
    """
    i = 0
    while i < len(argv):
        tok = argv[i]
        key = tok.split("=", 1)[0]
        if key in GLOBAL_VALUE_OPTS:
            i += 1 if "=" in tok else 2
            continue
        if key in GLOBAL_FLAG_OPTS:
            i += 1
            continue
        if tok in COMMANDS or tok in ("-h", "--help", "--version"):
            return argv
        return argv[:i] + ["run"] + argv[i:]
    return argv + ["run"]


def setup_logging(a) -> None:
    redact.register(getattr(a, "api_key", None))
    redact.install_excepthook()
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if a.log_file:
        Path(a.log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(a.log_file, encoding="utf-8"))
    fmt = redact.RedactingFormatter("%(asctime)s %(levelname)s %(message)s")  # API key -> ***
    for h in handlers:
        h.setFormatter(fmt)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, handlers=handlers, force=True)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def wants_control_panel(argv: list[str]) -> bool:
    """`packrat --gui` with no command (only global options) opens the window."""
    if "--gui" not in argv:
        return False
    i = 0
    while i < len(argv):
        key = argv[i].split("=", 1)[0]
        if key in GLOBAL_VALUE_OPTS:
            i += 1 if "=" in argv[i] else 2
        elif key in GLOBAL_FLAG_OPTS:
            i += 1
        else:
            return False
    return True


def main(argv=None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    from .grabcli import is_grab_command
    if is_grab_command(raw):   # 3.0: browse / download side (grab, batch, monitors, sources, ...)
        from .grabcli import main as grab_main
        return grab_main(raw)
    if wants_control_panel(raw):
        from .control_panel import main as panel_main
        return panel_main(raw)
    argv = insert_default_command(raw)
    a = build_parser().parse_args(argv)
    setup_console()
    try:
        for k in ("name_template", "folder_template"):  # --*-preset wins over templates from env/ini
            if getattr(a, k + "_preset", None):
                setattr(a, k, getattr(a, k + "_preset"))
        cfg_path = resolve(a)
        siteprofile.activate(a.site)   # 3.0: e6ai or e621 (host names, category words, file host)
        if a.folder_template in ("flat", "none"):
            a.folder_template = ""
        a.naming = make_naming(a)
        if not 1 <= int(a.workers) <= 64:
            raise ValueError(f"--workers must be 1-64 (got {a.workers})")
        a.on_low_disk = str(a.on_low_disk or "pause").strip().lower()
        if a.on_low_disk not in ("pause", "stop"):
            raise ValueError(f"on_low_disk must be pause or stop (got {a.on_low_disk!r})")
        if a.failure_log_max_mb is None or a.failure_log_max_mb < 0:
            raise ValueError(f"failure_log_max_mb must be 0 (never trim) or more (got {a.failure_log_max_mb})")
        if a.verify_action not in ("delete", "quarantine"):
            raise ValueError(f"verify action must be delete or quarantine (got {a.verify_action!r})")
        if a.rate <= 0:
            raise ValueError("--rate must be above 0")
        a.spec = Spec.from_args(a) if a.cmd in PRIORITY_COMMANDS | {"sync-exports", "retry-failed"} else None
        if a.cmd == "retry-failed":
            from .failures import parse_reasons
            parse_reasons(a.reason)  # unknown words fail here, before anything runs
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_USAGE
    setup_logging(a)
    if a.rate > API_RATE_GUIDE:
        log.info("note: API rate %g req/s is above the ~%g req/s the e621-family API docs ask for "
                 "(continuing as set)", a.rate, API_RATE_GUIDE)
    if cfg_path:
        log.info("config file: %s", cfg_path)
    install_signal_handlers()
    control.reset()
    if a.progress_json:
        attach_progress_stream(sys.stdout)
    if a.control_file:
        watch_control_file(a.control_file)
    if a.cmd == "test-login":
        return cmd_test_login(a)
    if a.gui and a.cmd in GUI_COMMANDS and not getattr(a, "dry_run", False):  # (preview has no window)
        from .gui import run_with_gui
        code = run_with_gui(lambda: execute(a), a)
        if code is not None:
            return code
    return execute(a)


def execute(a) -> int:
    """Run the chosen command; returns the exit code. Runs in a worker thread when --gui is used.
    1.9: download runs leave a `last_run` record (command, start, end, exit code) in the database, so the
    control panel can offer to resume an interrupted run."""
    code = None
    try:
        code = _execute(a)
        return code
    finally:
        if a.cmd in RUN_RECORD_COMMANDS and not getattr(a, "dry_run", False):
            finish_run_record(Path(a.out), EXIT_STOPPED if code is None else code)


def _execute(a) -> int:
    dry = getattr(a, "dry_run", False)
    emit("config", out=str(Path(a.out).resolve()), cmd=a.cmd, rate=a.rate, workers=a.workers, site=a.site,
         name_template=a.naming.name_template, folder_template=a.naming.folder_template,
         order=a.spec.describe() if getattr(a, "spec", None) else None)
    with contextlib.ExitStack() as stack:  # everything acquired is released in reverse order
        try:
            check_writable(Path(a.out))  # unplugged drive / read-only folder: clear error up front
            stack.enter_context(InstanceLock(Path(a.out)))
            net = a.cmd in NETWORK_COMMANDS or (a.cmd == "verify" and getattr(a, "redownload", False))
            if net and not dry and not a.no_keep_awake:
                stack.enter_context(KeepAwake())
            s = Store(a.out, site=a.site)
            s.failure_log_max_mb = a.failure_log_max_mb  # 1.9: FailureLog trims at the end of the run
            stack.callback(s.close)
            emit("disk", free=free_bytes(s.out))
            if a.cmd in RUN_RECORD_COMMANDS and not dry:   # 1.9: resume prompt + dashboard totals
                start_run_record(s, a.cmd)
                emit("archive", **s.archive_counts())
            offline = {"stats": cmd_stats, "seed-from-export": cmd_seed, "verify": cmd_verify,
                       "preview": cmd_preview, "reorganize": cmd_reorganize, "repair-db": cmd_repair_db,
                       "report": cmd_report}
            if a.cmd == "verify" and getattr(a, "redownload", False) and not dry:
                c = make_client(a)
                stack.callback(c.close)
                cmd_verify(a, s, c)
            elif a.cmd in offline:
                return offline[a.cmd](a, s) or EXIT_OK
            elif dry:
                {"run": cmd_run, "download": cmd_download,
                 "retry-failed": cmd_retry_failed}[a.cmd](a, _OfflineRate(a.rate), s)
            else:
                c = make_client(a)
                stack.callback(c.close)
                return {"run": cmd_run, "full": cmd_full, "download": cmd_download, "meta": cmd_meta,
                        "recheck": cmd_recheck, "exports": cmd_exports,
                        "sync-exports": cmd_sync_exports, "retry-failed": cmd_retry_failed}[a.cmd](a, c, s) or EXIT_OK
            return EXIT_OK
        except ReportError as e:
            log.error("report: %s", e)
            return EXIT_ERROR
        except AuthError as e:
            log.error("%s", e)
            log.error("STOPPED: sign-in rejected (401). Fix the login name / API key (control panel: "
                      "'Test sign-in'), or clear the key to run signed out.")
            return EXIT_AUTH
        except ForbiddenError as e:
            log.error("%s", e)
            log.error("STOPPED on 403. Do not retry with a different/browser User-Agent; "
                      "fix the cause (UA format, username, API key, or ask site staff).")
            return EXIT_403
        except StopRequested:
            log.warning("Stopped. Progress is checkpointed; run again to resume.")
            return EXIT_STOPPED
        except KeyboardInterrupt:
            log.warning("Interrupted. Progress is checkpointed; rerun the same command to resume.")
            return EXIT_STOPPED
        except DiskFullError as e:
            log.error("%s", e)
            return EXIT_DISK_FULL
        except StorageError as e:
            log.error("Storage problem: %s. Nothing was lost; reconnect the drive / fix permissions and "
                      "rerun to resume.", e)
            return EXIT_STORAGE
        except sqlite3.Error as e:
            return _db_error(e, a)
        except OSError as e:
            if is_disk_full(e):
                log.error("Disk full: %s. Free space and rerun to resume.", e)
                return EXIT_DISK_FULL
            why = storage_problem(e, Path(a.out))
            if why:
                log.error("Storage problem: %s. Rerun to resume once fixed.", why)
                return EXIT_STORAGE
            log.error("OS error: %s", e)
            return EXIT_ERROR
        except ValueError as e:
            log.error("%s", e)
            return EXIT_USAGE
        except DatabaseTooNewError as e:
            log.error("%s", e)
            return EXIT_DB_NEWER
        except RuntimeError as e:
            msg = str(e)
            if msg.startswith("Another Packrat"):
                log.error("%s", msg)
                return EXIT_LOCKED
            log.error("%s (progress is checkpointed; rerun to resume)", msg)
            return EXIT_ERROR


def _db_error(e: sqlite3.Error, a) -> int:
    m = str(e).lower()
    if "locked" in m or "busy" in m:
        log.error("Database is locked by another program (DB browser? backup tool?): %s", e)
        return EXIT_LOCKED
    if any(k in m for k in ("disk i/o", "readonly", "read-only", "unable to open", "disk is full", "full")):
        if "full" in m:
            log.error("Disk full while writing the database: %s", e)
            return EXIT_DISK_FULL
        log.error("Database storage problem: %s (drive unplugged or read-only?). Rerun to resume.", e)
        return EXIT_STORAGE
    log.error("Database error: %s. If this repeats, run `repair-db` (backs up the DB and rebuilds it "
              "from posts.jsonl / files.jsonl; no downloads needed).", e)
    return EXIT_DB
