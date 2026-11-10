"""Command line for the Browse / Downloads side of Packrat (the whole-site archive keeps its own commands).

  packrat grab -t "TAGS" -s "SITE SITE" ...      Grabber's command line, same options and output formats
  packrat batch [--ids 1 2 ...]                   run the download list (what the window's Start button does)
  packrat monitors [--run-due | --list | --check ID]
  packrat sources [--list | --add HOST [--type T] | --remove HOST | --registry URL [--install NAME]
                   | --registries | --add-registry URL | --remove-registry URL]
  packrat import-grabber [FOLDER] [--parts settings favorites ...]
  packrat tools md5-fixer | empty-dirs | rename | blacklist-fixer | tag-loader | md5-import ...
  packrat gui                                     open the window

Exit codes: 0 done, 1 error, 2 bad options, 3 a site refused access (403), 130 stopped.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading

from .events import StopRequested, attach_progress_stream, control, emit, watch_control_file
from .platform_utils import setup_console

log = logging.getLogger("packrat")
COMMANDS = ("grab", "batch", "monitors", "sources", "import-grabber", "tools", "gui")
EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_403, EXIT_STOPPED = 0, 1, 2, 3, 130


_VALUE_OPTS = ("--log-file", "--control-file")


def is_grab_command(argv: list[str]) -> bool:
    """True when the first word that isn't one of this parser's options is one of COMMANDS."""
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in _VALUE_OPTS:
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        return tok in COMMANDS
    return False


# ---- parser -----------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="packrat", description="Packrat: browse, download and organize images "
                                "from many sites.")
    p.add_argument("-d", "--debug", "--verbose", dest="debug", action="store_true", help="show debug messages")
    p.add_argument("--log-file", help="also write the log to this file")
    p.add_argument("--progress-json", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--control-file", help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("grab", help="search sites, print or download the results (Grabber's command line)")
    g.add_argument("-t", "--tags", default="", help="what to search for")
    g.add_argument("-s", "--sources", default="", help="sites, separated by spaces (default: Settings > Sources)")
    g.add_argument("-p", "--page", type=int, default=1, help="first page (default 1)")
    g.add_argument("-i", "--perpage", type=int, default=20, help="images per page (default 20)")
    g.add_argument("-m", "--max", type=int, default=0, help="stop after this many images (default: the page)")
    g.add_argument("-l", "--location", help="folder to save in (default: Settings > Save)")
    g.add_argument("-f", "--filename", help="file name format (default: Settings > Save)")
    g.add_argument("-u", "--user", help="log in with this user name (this run only)")
    g.add_argument("-w", "--password", help="... and this password / API key (this run only)")
    g.add_argument("-b", "--blacklist", action="store_true", help="also download / list blacklisted images")
    g.add_argument("--tb", "--tags-blacklist", dest="tags_blacklist", default="",
                   help="tags to leave out of --return-tags")
    g.add_argument("-r", "--postfilter", default="", help="post-filter, e.g. \"score:>10 -rating:e\"")
    g.add_argument("-n", "--no-duplicates", action="store_true", help="same MD5 on several sites: keep the first")
    g.add_argument("--tm", "--tags-min", dest="tags_min", type=int, default=0, help="--return-tags: minimum count")
    g.add_argument("--tf", "--tags-format", dest="tags_format", default="%tag\t%count\t%type",
                   help="--return-tags format (%%tag %%count %%type %%stype %%typeId)")
    g.add_argument("--ignore-error", action="store_true", help="keep going when a site fails")
    g.add_argument("--proxy", help="[user:password@]host:port (this run only)")
    g.add_argument("--no-login", action="store_true", help="don't log in, even when the site has a login saved")
    g.add_argument("-j", "--json", action="store_true", help="print results as JSON")
    g.add_argument("--load-details", action="store_true", help="also load each image's details (more requests)")
    g.add_argument("--get-details", metavar="URL", help="print the details of one post, from its page address")
    g.add_argument("--load-tag-database", action="store_true", help="fill the tag type database of the sites")
    what = g.add_mutually_exclusive_group()
    what.add_argument("--rc", "--return-count", dest="ret", action="store_const", const="count",
                      help="print the number of results")
    what.add_argument("--rt", "--return-tags", dest="ret", action="store_const", const="tags",
                      help="print the tags of the results")
    what.add_argument("--rp", "--return-pure-tags", dest="ret", action="store_const", const="pure_tags",
                      help="print the site's tag list")
    what.add_argument("--ri", "--return-images", dest="ret", action="store_const", const="images",
                      help="print the file addresses")
    what.add_argument("--download", dest="ret", action="store_const", const="download", help="download")

    b = sub.add_parser("batch", help="run the download list")
    b.add_argument("--ids", type=int, nargs="*", help="only these items (default: everything not finished)")
    b.add_argument("--workers", type=int, help="simultaneous downloads (default: Settings)")
    b.add_argument("--include-done", action="store_true", help="also run items marked done (finds new images)")

    m = sub.add_parser("monitors", help="check monitored searches")
    m.add_argument("--list", action="store_true", help="list the monitors")
    m.add_argument("--run-due", action="store_true", help="check the monitors that are due, then download "
                   "what they added to the download list (for Task Scheduler)")
    m.add_argument("--check", type=int, nargs="*", help="check these monitors now")
    m.add_argument("--no-download", action="store_true", help="only add new images to the download list")

    s = sub.add_parser("sources", help="sites and sources")
    s.add_argument("--list", action="store_true", help="list the sites")
    s.add_argument("--types", action="store_true", help="list the source types")
    s.add_argument("--add", metavar="HOST", help="add a site (its type is guessed unless --type is given)")
    s.add_argument("--type", help="source type for --add")
    s.add_argument("--remove", metavar="HOST", help="remove a site")
    s.add_argument("--test", metavar="HOST", help="test a site: one search request (and its login, if any)")
    s.add_argument("--registry", metavar="URL", nargs="?", const="official",
                   help="show a source registry (default: the official one)")
    s.add_argument("--install", metavar="NAME", help="with --registry: install this source")
    s.add_argument("--accept-mismatch", action="store_true", help=argparse.SUPPRESS)
    s.add_argument("--uninstall", metavar="NAME", help="remove an installed (registry) source")
    s.add_argument("--registries", action="store_true", help="list the source registries you added")
    s.add_argument("--add-registry", metavar="URL", help="remember a source registry (Grabber: source-registry add)")
    s.add_argument("--remove-registry", metavar="URL", help="forget a source registry")

    ig = sub.add_parser("import-grabber", help="import a Grabber profile (settings, favorites, monitors ...)")
    ig.add_argument("folder", nargs="?", help="Grabber's settings folder (default: found automatically)")
    ig.add_argument("--parts", nargs="*", help="only these: settings favorites monitors later blacklist words "
                    "md5 downloads sites")
    ig.add_argument("--dry-run", action="store_true", help="only show what the profile contains")

    t = sub.add_parser("tools", help="tools for files already on disk")
    tsub = t.add_subparsers(dest="tool", required=True)
    t1 = tsub.add_parser("md5-fixer", help="add every file of a folder to the MD5 list")
    t1.add_argument("folder")
    t1.add_argument("--hash-files", action="store_true", help="hash every file (default: MD5 from the name "
                    "when it has one)")
    t2 = tsub.add_parser("empty-dirs", help="list (or delete) empty folders")
    t2.add_argument("folder")
    t2.add_argument("--delete", action="store_true")
    t3 = tsub.add_parser("rename", help="move saved files to a new file name format (dry run unless --apply)")
    t3.add_argument("folder")
    t3.add_argument("--site", required=True)
    t3.add_argument("--format", required=True)
    t3.add_argument("--id-from-name", action="store_true", help="find posts by the id in the file name")
    t3.add_argument("--apply", action="store_true")
    t4 = tsub.add_parser("blacklist-fixer", help="find saved files that match the blacklist")
    t4.add_argument("folder")
    t4.add_argument("--site", required=True)
    t4.add_argument("--delete", action="store_true")
    t5 = tsub.add_parser("tag-loader", help="fill a site's tag type database")
    t5.add_argument("--site", required=True)
    t5.add_argument("--min-count", type=int, default=0)
    t6 = tsub.add_parser("md5-import", help="import a Grabber md5s.txt into the MD5 list")
    t6.add_argument("file")

    g = sub.add_parser("gui", help="open the window")
    g.add_argument("--classic", action="store_true", help="the older Tk window instead of the Qt one")
    g.add_argument("--show", action="store_true", help="open the window even when 'Start hidden' is on")
    g.add_argument("--new-instance", action="store_true", help=argparse.SUPPRESS)
    g.add_argument("--smoke", metavar="FOLDER", help=argparse.SUPPRESS)
    return p


# ---- main -------------------------------------------------------------------------------------
def open_window(classic: bool = False, extra=None) -> int:
    """The Qt window; the classic Tk window when asked for, or when Qt (PySide6) is not installed."""
    if not classic:
        try:
            from .qt import main as qt_main
            import PySide6  # noqa: F401
        except ImportError as e:
            print(f"The Qt window is not available ({e}); opening the classic window instead.", file=sys.stderr)
        else:
            return qt_main(list(extra or []))
    from .app_ui import main as gui_main
    return gui_main([])


def main(argv: list[str]) -> int:
    a = build_parser().parse_args(argv)
    if a.cmd == "gui":
        extra = [f for f, on in (("--show", a.show), ("--new-instance", a.new_instance)) if on]
        return open_window(classic=a.classic, extra=extra + (["--smoke", a.smoke] if a.smoke else []))
    setup_console()
    _setup_logging(a)
    control.reset()
    if a.progress_json:
        attach_progress_stream(sys.stdout)
    if a.control_file:
        watch_control_file(a.control_file)
    try:
        return {"grab": cmd_grab, "batch": cmd_batch, "monitors": cmd_monitors, "sources": cmd_sources,
                "import-grabber": cmd_import, "tools": cmd_tools}[a.cmd](a)
    except StopRequested:
        log.warning("Stopped. Nothing is lost: run it again to continue.")
        return EXIT_STOPPED
    except KeyboardInterrupt:
        log.warning("Interrupted.")
        return EXIT_STOPPED
    except ValueError as e:
        log.error("%s", e)
        return EXIT_USAGE
    except BrokenPipeError:   # output piped into a program that stopped reading (e.g. `| head`)
        try:
            sys.stdout = open(os.devnull, "w")
        except OSError:
            pass
        return EXIT_OK


def _setup_logging(a) -> None:
    from . import redact
    redact.install_excepthook()
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if a.log_file:
        from pathlib import Path
        Path(a.log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(a.log_file, encoding="utf-8"))
    else:
        from . import paths
        from logging.handlers import RotatingFileHandler
        handlers.append(RotatingFileHandler(paths.logs_dir() / "packrat.log", maxBytes=5_000_000, backupCount=3,
                                            encoding="utf-8"))
    fmt = redact.RedactingFormatter("%(asctime)s %(levelname)s %(message)s")
    for h in handlers:
        h.setFormatter(fmt)
    logging.basicConfig(level=logging.DEBUG if a.debug else logging.INFO, handlers=handlers, force=True)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _ctx():
    from .grab import library, settings, sources
    return settings.shared(), library.shared(), sources.store()


def _sites(store, st, spec: str):
    hosts = spec.split() if spec.strip() else list(st.get("sites") or [])
    hosts = [h for h in hosts if h]
    if not hosts:
        raise ValueError("choose at least one site: -s \"danbooru.donmai.us gelbooru.com\"")
    out = []
    for h in hosts:
        s = store.get(h)
        if s is None:
            raise ValueError(f"unknown site {h!r}: see `packrat sources --list`, or add it with "
                             f"`packrat sources --add {h}`")
        out.append(s)
    return out


def _stop_watch(cancel: threading.Event):
    def loop():
        while not cancel.is_set():
            if control.stopping:
                cancel.set()
                return
            cancel.wait(0.3)
    threading.Thread(target=loop, daemon=True, name="stop-watch").start()


def _print(s: str) -> None:
    try:
        print(s, flush=True)
    except UnicodeEncodeError:
        print(s.encode("ascii", "replace").decode(), flush=True)


# ---- grab -------------------------------------------------------------------------------------
def cmd_grab(a) -> int:
    from .grab import filename as F, sources as S
    from .grab.filters import Blacklist, PostFilter
    st, lib, store = _ctx()
    if a.proxy:
        S.PROXY_OVERRIDE = _proxy(a.proxy)
    if a.get_details:
        return _get_details(a, store, st)
    sites = _sites(store, st, a.sources)
    for s in sites:
        if a.user or a.password:
            s.auth_override = {"pseudo": a.user or "", "password": a.password or "", "apiKey": a.password or ""}
        if a.no_login:
            s._login_tried = True
    if a.load_tag_database:
        from .grab.tools import tag_loader
        for s in sites:
            r = tag_loader(s, progress=lambda page, n: log.info("%s: %d tags", s.name, n))
            log.info("%s: %d tags loaded (%s)", s.name, r["tags"], r["source"])
        if not a.ret:
            return EXIT_OK
    ret = a.ret or "images"
    if ret == "pure_tags":
        return _pure_tags(a, sites)
    tags = a.tags
    pf = PostFilter(" ".join(x for x in (a.postfilter, st.get("globalPostFilter") or "") if x.strip()))
    bl = None if a.blacklist else Blacklist(st.lines("blacklistedtags"))
    cancel = threading.Event()
    _stop_watch(cancel)
    results, errors, count_total, tag_counts = [], [], 0, {}
    seen_md5 = set()
    for site in sites:
        if a.max and len(results) >= a.max and ret not in ("count", "tags"):
            break
        page, prev = a.page, None
        while True:
            control.checkpoint()
            pr = site.search(tags, page, a.perpage, prev, cancel=cancel)
            if pr.error:
                errors.append(f"{site.name}: {pr.error}")
                log.error("%s: %s", site.name, pr.error)
                if not a.ignore_error:
                    return EXIT_403 if "403" in pr.error else EXIT_ERROR
                break
            if page == a.page:
                count_total += max(pr.image_count, 0) if pr.image_count >= 0 else 0
                for t in pr.tags:
                    cur = tag_counts.setdefault(t.name, [t.name, t.count, t.type])
                    if cur is not None and cur[1] != t.count:
                        cur[1] += t.count
            if ret == "count":
                if pr.image_count < 0:
                    count_total += len(pr.images)   # site gives no total: count the first page
                break
            for im in pr.images:
                if a.max and len(results) >= a.max:
                    break
                if a.no_duplicates and im.md5:
                    if im.md5 in seen_md5:
                        continue
                    seen_md5.add(im.md5)
                if a.load_details and not im.details_loaded:
                    try:
                        site.details(im, cancel)
                    except Exception as e:  # noqa: BLE001
                        log.warning("details of %s: %s", im.key(), e)
                toks = F.build_tokens(im, st, site=site)
                if pf and pf.match(toks):
                    continue
                if bl and bl.match(toks):
                    continue
                results.append((site, im))
            prev = pr.info()
            if not a.max or len(results) >= a.max or not pr.images:
                break
            if pr.page_count > 0 and page >= pr.page_count:
                break
            page += 1
    if ret == "count":
        _print(json.dumps({"count": count_total}) if a.json else str(count_total))
        return EXIT_OK if not errors or a.ignore_error else EXIT_ERROR
    if ret == "tags":
        if not tag_counts:
            for _, im in results:
                for t in im.tags:
                    cur = tag_counts.setdefault(t.name, [t.name, 0, t.type])
                    cur[1] += 1
        bl_tags = set(a.tags_blacklist.split())
        rows = [r for r in tag_counts.values() if r[1] >= a.tags_min and r[0] not in bl_tags]
        if a.json:
            _print(json.dumps([{"name": n, "count": c, "type": t or "unknown"} for n, c, t in rows],
                              ensure_ascii=False))
        else:
            for n, c, t in rows:
                _print(_tag_line(a.tags_format, n, c, t))
        return EXIT_OK
    if ret == "images":
        if a.json:
            _print(json.dumps([_image_json(im, site) for site, im in results], ensure_ascii=False, default=str))
        else:
            for _, im in results:
                _print(im.file_url or im.sample_url)
        return EXIT_OK
    # download
    from .grab.download import Batch, Group
    batch = Batch(st, lib, store, cancel=cancel)
    g = Group(search=tags, sites=[s.host for s in sites], filename=a.filename or "", path=a.location or "",
              get_blacklisted=a.blacklist)
    n = 0
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(batch.workers) as pool:
        futs = []
        for site, im in results:
            n += 1
            futs.append(pool.submit(batch._one, im, site, g, None, None, n))
        for f in futs:
            f.result()
    stt = batch.stats
    log.info("Done: %d saved, %d already there, %d duplicates, %d failed", stt["saved"], stt["exists"],
             stt["duplicate"] + stt["copied"] + stt["moved"] + stt["linked"], stt["failed"] + stt["missing"])
    if a.json:
        _print(json.dumps(stt))
    return EXIT_OK if not stt["failed"] and not stt["forbidden"] else (EXIT_403 if stt["forbidden"] else EXIT_ERROR)


def _tag_line(fmt: str, name, count, typ) -> str:
    from .grab.tags import KNOWN_TYPES
    typ = typ or "unknown"
    tid = KNOWN_TYPES.index(typ) if typ in KNOWN_TYPES else -1
    return (fmt.replace("\\t", "\t").replace("\\n", "\n").replace("%tag", str(name)).replace("%count", str(count))
            .replace("%stype", typ[:1]).replace("%typeId", str(tid)).replace("%type", typ))


def _image_json(im, site) -> dict:
    return {"id": im.id, "md5": im.md5, "website": site.host, "file_url": im.file_url, "sample_url": im.sample_url,
            "preview_url": im.preview_url, "page_url": im.page_url, "width": im.width, "height": im.height,
            "file_size": im.file_size, "ext": im.ext, "rating": im.rating, "score": im.score,
            "date": im.date.isoformat() if im.date else None, "sources": im.sources,
            "tags": [{"name": t.name, "type": t.type or "unknown", "count": t.count} for t in im.tags]}


def _pure_tags(a, sites) -> int:
    rows = []
    for s in sites:
        tags, err = s.tags_page(a.page, a.perpage, "count")
        if err:
            log.error("%s: %s", s.name, err)
            if not a.ignore_error:
                return EXIT_ERROR
        rows += [(t.name, t.count, t.type) for t in tags if t.count >= a.tags_min]
    if a.json:
        _print(json.dumps([{"name": n, "count": c, "type": t or "unknown"} for n, c, t in rows], ensure_ascii=False))
    else:
        for n, c, t in rows:
            _print(_tag_line(a.tags_format, n, c, t))
    return EXIT_OK


def _get_details(a, store, st) -> int:
    from .grab.search import find_post
    try:
        site, im = find_post(store, a.get_details)
    except ValueError as e:
        log.error("%s", e)
        return EXIT_ERROR
    _print(json.dumps(_image_json(im, site), ensure_ascii=False, default=str, indent=None if a.json else 1))
    return EXIT_OK


def _proxy(spec: str) -> dict:
    spec = spec.strip()
    if "://" not in spec:
        spec = "http://" + spec
    return {"http": spec, "https": spec}


# ---- batch ------------------------------------------------------------------------------------
def cmd_batch(a) -> int:
    from .grab.download import Batch
    from .grab.postsave import batch_end_action
    st, lib, store = _ctx()
    items = lib.batch_items(a.ids or None)
    if not a.ids and not a.include_done:
        items = [it for it in items if it.get("status") != "done"]
    if not items:
        log.info("The download list is empty (or everything in it is done).")
        emit("grab_done", stats={}, items=0)
        return EXIT_OK
    cancel = threading.Event()
    _stop_watch(cancel)
    batch = Batch(st, lib, store, workers=a.workers, cancel=cancel)
    emit("phase", name=f"download list: {len(items)} item(s)")
    try:
        stats = batch.run_items(items)
    finally:
        _write_failures(batch)
    booru = batch.saver.post.booru_stats
    log.info("Done: %d saved, %d already there, %d duplicates handled, %d skipped by filters, %d failed",
             stats["saved"], stats["exists"], stats["duplicate"] + stats["copied"] + stats["moved"] + stats["linked"],
             stats["blacklisted"] + stats["filtered"] + stats["skipped"], stats["failed"] + stats["missing"])
    if any(booru.values()):
        log.info("Local booru: %d uploaded, %d already there, %d failed", booru["uploaded"], booru["exists"],
                 booru["failed"])
    emit("grab_done", stats=stats, items=len(items), failures=len(batch.failures))
    batch_end_action(st.get("Batch/end") or "nothing")
    if stats.get("forbidden"):
        return EXIT_403
    return EXIT_OK


def _write_failures(batch) -> None:
    if not batch.failures:
        return
    from datetime import datetime
    from . import paths
    p = paths.logs_dir() / f"failed-{datetime.now():%Y%m%d-%H%M%S}.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        for r in batch.failures:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    log.info("The %d failed image(s) are listed in %s", len(batch.failures), p)


# ---- monitors ---------------------------------------------------------------------------------
def cmd_monitors(a) -> int:
    from .grab import monitor
    st, lib, store = _ctx()
    mons = lib.monitors()
    if a.list or not (a.run_due or a.check is not None):
        if not mons:
            _print("No monitors yet: use 'Monitor...' in a Browse tab or in the Favorites tab.")
        for m in mons:
            _print(f"{m['id']:>4}  {m['search'] or '(everything)'}  on {', '.join(m['sites'])}  every "
                   f"{m['interval_min']} min  new: {m['new_count']}{'  (off)' if not m['active'] else ''}")
        return EXIT_OK
    todo = lib.due_monitors() if a.run_due else [m for m in mons if not a.check or m["id"] in a.check]
    cancel = threading.Event()
    _stop_watch(cancel)
    total = 0
    added = []
    for m in todo:
        control.checkpoint()
        emit("phase", name=f"monitor: {m['search']}")
        res = monitor.check(m, store, st, lib, cancel)
        total += len(res["new"])
        if res["first"]:
            log.info("monitor '%s': first check, counting from now on", m["search"])
        else:
            log.info("monitor '%s': %d new", m["search"], len(res["new"]))
        for e in res["errors"]:
            log.warning("monitor '%s': %s", m["search"], e)
        if res["new"] and m.get("download"):
            added.append(m["id"])
        if res["new"] and m.get("notify"):
            try:
                from .notify import notify
                notify("Packrat", f"{len(res['new'])} new image(s) for {m['search'] or 'a monitor'}")
            except Exception:  # noqa: BLE001
                pass
    if added and not a.no_download:
        items = [it for it in lib.batch_items() if it["kind"] == "unique" and it.get("status") != "done"
                 and it["data"].get("from_monitor") in added]
        if items:
            from .grab.download import Batch
            Batch(st, lib, store, cancel=cancel).run_items(items)
            for it in items:
                lib.batch_remove([it["id"]])
    log.info("%d monitor(s) checked, %d new image(s)", len(todo), total)
    return EXIT_OK


# ---- sources ----------------------------------------------------------------------------------
def cmd_sources(a) -> int:
    st, lib, store = _ctx()
    if a.add:
        s = store.add_site(a.add, a.type)
        _print(f"Added {s.host} ({s.source.name}).")
        return EXIT_OK
    if a.remove:
        store.remove_site(a.remove)
        _print(f"Removed {a.remove}.")
        return EXIT_OK
    if a.test:
        s = store.get(a.test)
        if s is None:
            raise ValueError(f"unknown site {a.test}")
        if s.has_credentials():
            ok, msg = s.login()
            _print(f"Login: {'OK' if ok else 'FAILED'} {msg}")
        pr = s.search("", 1, 5)
        _print(f"Search: {'FAILED ' + pr.error if pr.error else 'OK, ' + str(len(pr.images)) + ' images'}")
        return EXIT_ERROR if pr.error else EXIT_OK
    if a.registry:
        from .grab import registry as R
        url = R.OFFICIAL if a.registry == "official" else a.registry
        reg = R.fetch(url)
        lst = R.available([reg])
        if a.install:
            e = next((x for x in lst if a.install in (x["name"], x["slug"])), None)
            if e is None:
                raise ValueError(f"no source {a.install!r} in this registry")
            try:
                d = R.install(e, accept_mismatch=a.accept_mismatch)
            except R.ChecksumMismatch as ex:
                log.error("%s Add --accept-mismatch to install it anyway.", ex)
                return EXIT_ERROR
            _print(f"Installed {e['name']} into {d}")
            return EXIT_OK
        _print(f"{reg.get('name')}  ({len(lst)} sources)")
        for x in lst:
            _print(f"  {x['name']:<28} {x['status']:<18} {', '.join(x['default_sites'][:3])}")
        return EXIT_OK
    if a.registries or a.add_registry or a.remove_registry:
        from .grab import registry as R
        if a.add_registry:
            reg = R.add_registry(a.add_registry)
            _print(f"Added {reg.get('name') or a.add_registry} ({len(R.available([reg]))} sources).")
        if a.remove_registry:
            R.remove_registry(a.remove_registry)
            _print(f"Removed {a.remove_registry}.")
        _print(f"{R.OFFICIAL}  (official)")
        for u in R.load_state()["registries"]:
            if u != R.OFFICIAL:
                _print(u)
        return EXIT_OK
    if a.uninstall:
        from .grab import registry as R
        _print("Removed." if R.uninstall(a.uninstall) else "Not installed.")
        return EXIT_OK
    if a.types:
        for t in sorted(store.types.values(), key=lambda t: t.dir_name.lower()):
            _print(f"{t.dir_name:<28} {', '.join(t.default_sites[:4])}{' (installed)' if t.user else ''}")
        return EXIT_OK
    for s in store.all_sites():
        _print(f"{s.host:<32} {s.source.name}{'  [login saved]' if s.has_credentials() else ''}")
    return EXIT_OK


# ---- import -----------------------------------------------------------------------------------
def cmd_import(a) -> int:
    from .grab import grabber_import as G
    st, lib, store = _ctx()
    folder = a.folder
    if not folder:
        found = G.find_profiles()
        if not found:
            raise ValueError("Grabber's settings folder wasn't found. Give it: packrat import-grabber "
                             "\"C:\\Users\\YOU\\AppData\\Local\\Bionus\\Grabber\"")
        folder = str(found[0])
    summ = G.summary(folder)
    _print(f"Grabber profile: {folder}")
    _print("  " + ", ".join(f"{k}: {v}" for k, v in summ.items()))
    if a.dry_run:
        return EXIT_OK
    rep = G.import_profile(folder, st, lib, store, tuple(a.parts or G.PARTS),
                           progress=lambda step: log.info("importing %s...", step))
    _print("Imported: " + ", ".join(f"{k} {v}" for k, v in rep.items() if k != "notes"))
    for n in rep["notes"]:
        _print("  note: " + n)
    return EXIT_OK


# ---- tools ------------------------------------------------------------------------------------
def cmd_tools(a) -> int:
    from .grab import tools as T
    st, lib, store = _ctx()
    cancel = threading.Event()
    _stop_watch(cancel)
    prog = lambda n, what: emit("phase", name=f"{n}: {what}")  # noqa: E731
    if a.tool == "md5-fixer":
        r = T.md5_fixer(lib, a.folder, from_name=not a.hash_files, progress=prog, cancel=cancel)
        _print(f"{r['files']} files, {r['added']} added to the MD5 list")
    elif a.tool == "md5-import":
        _print(f"{T.md5_import_txt(lib, a.file)} MD5s imported")
    elif a.tool == "empty-dirs":
        dirs = T.empty_dirs(a.folder)
        for d in dirs:
            _print(d)
        if a.delete:
            _print(f"{T.remove_dirs(dirs)} folder(s) removed")
        else:
            _print(f"{len(dirs)} empty folder(s). Add --delete to remove them.")
    elif a.tool == "rename":
        plan = T.rename_existing_plan(store, st, lib, a.folder, a.site, a.format, id_from_name=a.id_from_name,
                                      progress=prog, cancel=cancel)
        for r in plan:
            if r["status"] == "rename":
                _print(f"{r['from']}  ->  {r['to']}")
            elif r["status"] != "same":
                _print(f"{r['from']}  ({r['status']})")
        if a.apply:
            res = T.apply_renames(lib, plan)
            _print(f"{res['moved']} moved, {res['skipped']} unchanged")
        else:
            _print(f"{sum(r['status'] == 'rename' for r in plan)} file(s) would move. Add --apply to do it.")
    elif a.tool == "blacklist-fixer":
        hits = T.blacklist_fixer(store, st, lib, a.folder, a.site, progress=prog, cancel=cancel)
        for h in hits:
            _print(f"{h['path']}  ({h['rule']})")
        if a.delete:
            import os
            n = 0
            for h in hits:
                try:
                    os.remove(h["path"])
                    lib.md5_remove(h["md5"])
                    n += 1
                except OSError as e:
                    log.warning("%s: %s", h["path"], e)
            _print(f"{n} file(s) deleted")
        else:
            _print(f"{len(hits)} file(s) match the blacklist. Add --delete to delete them.")
    elif a.tool == "tag-loader":
        site = store.get(a.site)
        if site is None:
            raise ValueError(f"unknown site {a.site}")
        r = T.tag_loader(site, progress=lambda p, n: emit("phase", name=f"{n} tags"), cancel=cancel,
                         min_count=a.min_count)
        _print(f"{r['tags']} tags loaded from {r['source']}")
    return EXIT_OK
