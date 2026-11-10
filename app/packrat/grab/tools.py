"""Tools for files already on disk (Grabber's Tools menu), each a function the window and the command line
share. They never delete anything without being asked: the window shows the list first.

  rename_existing   move saved files to a new file name format (asks the site for each file's details
                    unless the library already knows them)
  blacklist_fixer   find saved files whose tags match the blacklist
  md5_fixer         add every file in a folder to the MD5 list
  empty_dirs        find empty folders
  tag_loader        fill a site's local tag database (tag types without extra requests later)
  md5_import_txt    Grabber's md5s.txt -> the MD5 list ("MD5 database converter")
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path

from . import filename as F
from .filters import Blacklist

log = logging.getLogger("packrat.grab")
MD5_RX = re.compile(r"(?<![0-9a-f])([0-9a-f]{32})(?![0-9a-f])", re.I)


def iter_files(folder: str, exclude_exts=("txt", "tmp", "xmp", "xmp_original", "part", "json")):
    for dp, _dirs, files in os.walk(folder):
        for f in files:
            ext = f.rsplit(".", 1)[-1].lower() if "." in f else ""
            if ext in exclude_exts:
                continue
            yield Path(dp) / f


def file_md5(p: Path) -> str:
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def md5_of(p: Path, from_name: bool, force: bool) -> str:
    if from_name and not force:
        m = MD5_RX.search(p.name)
        if m:
            return m.group(1).lower()
    return file_md5(p)


def md5_fixer(lib, folder: str, from_name: bool = True, force: bool = False, exclude=("txt", "tmp", "xmp",
                                                                                    "xmp_original"),
              progress=None, cancel=None) -> dict:
    n = added = 0
    for p in iter_files(folder, exclude):
        if cancel is not None and cancel.is_set():
            break
        n += 1
        try:
            md5 = md5_of(p, from_name, force)
        except OSError:
            continue
        if lib.md5_path(md5) is None:
            lib.md5_add(md5, str(p))
            added += 1
        if progress and n % 50 == 0:
            progress(n, str(p))
    return {"files": n, "added": added}


def md5_import_txt(lib, path: str) -> int:
    pairs = []
    for line in Path(path).read_text("utf-8", errors="replace").splitlines():
        line = line.strip()
        if len(line) > 32 and re.fullmatch(r"[0-9a-fA-F]{32}", line[:32]):
            pairs.append((line[:32], line[32:].strip()))
    return lib.md5_import(pairs)


def empty_dirs(folder: str) -> list[str]:
    out = []
    for dp, dirs, files in os.walk(folder, topdown=False):
        if dp == folder:
            continue
        if not files and all(os.path.join(dp, d) in out for d in dirs):
            out.append(dp)
    return out


def remove_dirs(dirs: list[str]) -> int:
    n = 0
    for d in sorted(dirs, key=len, reverse=True):
        try:
            os.rmdir(d)
            n += 1
        except OSError:
            pass
    return n


def _lookup_image(store, site, lib, p: Path, md5: str, id_from_name: bool):
    """Find the post for a saved file: by md5 search on the site (or id in the name)."""
    q = None
    if id_from_name:
        m = re.search(r"(\d{2,})", p.stem)
        if m:
            q = f"id:{m.group(1)}"
    if q is None and md5:
        q = f"md5:{md5}"
    if q is None:
        return None
    pr = site.search(q, 1, 5)
    for im in pr.images:
        if not md5 or im.md5 == md5 or q.startswith("id:"):
            if not im.details_loaded:
                try:
                    site.details(im)
                except Exception:  # noqa: BLE001
                    pass
            return im
    return None


def rename_existing_plan(store, st, lib, folder: str, site_host: str, new_format: str, from_name=True,
                         force=False, id_from_name=False, progress=None, cancel=None) -> list[dict]:
    """[{"from", "to", "status"}] - nothing is moved here."""
    site = store.get(site_host)
    if site is None:
        raise ValueError(f"unknown site {site_host}")
    plan = []
    for i, p in enumerate(iter_files(folder)):
        if cancel is not None and cancel.is_set():
            break
        try:
            md5 = md5_of(p, from_name, force)
        except OSError:
            continue
        im = _lookup_image(store, site, lib, p, md5, id_from_name)
        if im is None:
            plan.append({"from": str(p), "to": "", "status": "not found on the site"})
            continue
        im.ext = p.suffix.lstrip(".").lower() or im.ext
        toks = F.build_tokens(im, st, site=site, extra={"old_filename": p.name, "old_directory": str(p.parent),
                                                        "directory": str(p.parent)})
        dest = F.Filename(new_format).path(toks, st, folder, 0, F.DEFAULT_FLAGS | F.FLAG_FOLDER)
        if not dest:
            plan.append({"from": str(p), "to": "", "status": "the format gave no name"})
            continue
        to = dest[0]
        plan.append({"from": str(p), "to": to, "status": "same" if Path(to) == p else "rename",
                     "md5": md5, "site": site.host, "id": im.id})
        if progress:
            progress(i + 1, str(p))
    return plan


def apply_renames(lib, plan: list[dict]) -> dict:
    moved = skipped = 0
    for r in plan:
        if r.get("status") != "rename" or not r.get("to"):
            skipped += 1
            continue
        src, dst = Path(r["from"]), Path(r["to"])
        if dst.exists():
            stem, ext = dst.stem, dst.suffix
            k = 2
            while dst.exists():
                dst = dst.with_name(f"{stem}__{k}{ext}")
                k += 1
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(src, dst)
        except OSError as e:
            log.warning("could not move %s: %s", src, e)
            skipped += 1
            continue
        if r.get("md5"):
            lib.md5_set_path(r["md5"], str(dst))
        moved += 1
    return {"moved": moved, "skipped": skipped}


def blacklist_fixer(store, st, lib, folder: str, site_host: str, from_name=True, force=False,
                    blacklist_lines=None, progress=None, cancel=None) -> list[dict]:
    site = store.get(site_host)
    if site is None:
        raise ValueError(f"unknown site {site_host}")
    bl = Blacklist(blacklist_lines if blacklist_lines is not None else st.lines("blacklistedtags"))
    out = []
    for i, p in enumerate(iter_files(folder)):
        if cancel is not None and cancel.is_set():
            break
        try:
            md5 = md5_of(p, from_name, force)
        except OSError:
            continue
        im = _lookup_image(store, site, lib, p, md5, False)
        if im is None:
            continue
        hit = bl.match(F.build_tokens(im, st, site=site))
        if hit:
            out.append({"path": str(p), "rule": ", ".join(hit), "md5": md5})
        if progress:
            progress(i + 1, str(p))
    return out


def tag_loader(site, progress=None, cancel=None, max_pages: int = 10_000, per_page: int = 1000,
               min_count: int = 0) -> dict:
    """Grabber's tag loader. For e621 / e926 / e6ai the daily tags export is used instead: one file
    instead of thousands of pages."""
    db = site.tag_db()
    if site.is_e621_family():
        n = _e621_tags_export(site, db, progress, cancel)
        if n:
            return {"tags": n, "pages": 0, "source": "daily tags export"}
    try:      # like Grabber: refresh the site's tag type list first (numeric types -> artist, character ...)
        site.load_tag_types(force=True)
    except Exception:  # noqa: BLE001 - the shipped list is used instead
        log.debug("tag types for %s not loaded", site.host, exc_info=True)
    total, pages = 0, 0
    for page in range(1, max_pages + 1):
        if cancel is not None and cancel.is_set():
            break
        tags, err = site.tags_page(page, per_page, "count")
        if err or not tags:
            break
        tags = [t for t in tags if t.count >= min_count]
        total += db.add(tags, only_typed=False)
        pages += 1
        if progress:
            progress(page, total)
        if min_count and tags and tags[-1].count < min_count:
            break
    return {"tags": total, "pages": pages, "source": "tag pages"}


def _e621_tags_export(site, db, progress, cancel) -> int:
    import csv
    import gzip
    import io
    from .tags import Tag
    try:
        st, text, _ = site.get_text(site.base_url + "/db_exports.json", "page")
        import json
        listing = json.loads(text) if st == 200 else []
        entry = next((e for e in listing if isinstance(e, dict) and e.get("name") == "tags"), None)
        if not entry:
            return 0
        r = site.request(entry["url"], "image", stream=True)
        raw = r.content
        r.close()
    except Exception as e:  # noqa: BLE001
        log.warning("tags export not available (%s); using tag pages", e)
        return 0
    names = {0: "general", 1: "artist", 2: "contributor", 3: "copyright", 4: "character", 5: "species",
             6: "invalid", 7: "meta", 8: "lore"}
    batch, total = [], 0
    with gzip.open(io.BytesIO(raw), "rt", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if cancel is not None and cancel.is_set():
                break
            try:
                batch.append(Tag(row["name"], names.get(int(row.get("category") or 0), ""),
                                 int(row.get("post_count") or 0), int(row.get("id") or 0)))
            except (KeyError, ValueError):
                continue
            if len(batch) >= 5000:
                total += db.add(batch, only_typed=False)
                batch = []
                if progress:
                    progress(0, total)
    total += db.add(batch, only_typed=False)
    return total
