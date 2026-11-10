"""Saving images from any site (Grabber's ImageDownloader + BatchDownloader, with Packrat's safety habits).

For each image:
  1. load its details first when the file name needs typed tags / tokens the list didn't give
     (or there is no file URL yet); galleries are opened and every image inside is saved
  2. compute the path(s) from the file name format (+ conditional file names); "one file per tag" can
     give several paths
  3. skip it if the file is already there, or act on the MD5 list (same file saved before, from any site):
     save again / copy / move / link / hard link / don't save - separately for "same folder" and "elsewhere"
  4. download to <name>.part (or the temporary folder), checking size and MD5 as it arrives; on 404 try the
     other extensions (extension rotation) and then the sample; retries with back-off; 429/503 wait
  5. fix the extension from the file header, move into place atomically, extra copies / links
  6. after saving: original date, resize / conversions, metadata (ExifTool), .json sidecar, commands,
     SQL, separate log files, MD5 list + library history

A Batch runs download lists (searches = groups, and single images) with several downloads at once,
each site keeping its own interval and limit; it can be stopped and resumed, and reports progress events
(the window's Downloads tab shows them).
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from ..events import StopRequested, control, emit
from ..platform_utils import safe_replace, safe_unlink
from . import filename as F
from .filters import Blacklist, PostFilter
from .image import Image, extension_of, set_extension
from .sources import Forbidden, SiteError

log = logging.getLogger("packrat.grab")
CHUNK = 1 << 18
PAGES_AHEAD = 1          # result pages fetched while earlier ones are still downloading
MAX_RESUMES = 4          # times one transfer may be continued after the connection broke
MAGIC = ((b"\xff\xd8\xff", "jpg"), (b"\x89PNG\r\n\x1a\n", "png"), (b"GIF87a", "gif"), (b"GIF89a", "gif"),
         (b"BM", "bmp"), (b"\x1aE\xdf\xa3", "webm"), (b"PK\x03\x04", "zip"), (b"%PDF", "pdf"),
         (b"FWS", "swf"), (b"CWS", "swf"), (b"ZWS", "swf"))


def sniff_ext(head: bytes) -> str | None:
    for sig, ext in MAGIC:
        if head.startswith(sig):
            return ext
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"avif", b"avis"):
            return "avif"
        if brand in (b"heic", b"heix", b"mif1", b"msf1"):
            return "heic"
        return "mp4"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] == b"\x00\x00\x00\x0cJXL " or head[:2] == b"\xff\x0a":
        return "jxl"
    return None


@dataclass
class SaveResult:
    status: str                 # saved | exists | duplicate | copied | moved | linked | skipped | blacklisted |
                                # filtered | missing | failed | forbidden
    paths: list = field(default_factory=list)
    message: str = ""
    bytes: int = 0
    md5: str = ""


class ImageSaver:
    def __init__(self, settings, library, store, cancel: threading.Event | None = None):
        self.st, self.lib, self.store = settings, library, store
        self.cancel = cancel or threading.Event()
        self._path_locks: dict[str, threading.Lock] = {}
        self._pl = threading.Lock()
        from . import postsave
        self.post = postsave.PostSave(settings)

    def _check(self):
        if self.cancel.is_set():
            raise StopRequested("cancelled")
        control.checkpoint()   # waits here while paused; raises StopRequested on stop
        while control.paused and not control.stopping and not self.cancel.is_set():
            time.sleep(0.2)
        control.checkpoint()   # waits here while paused; raises StopRequested on stop

    def _plock(self, p: str) -> threading.Lock:
        with self._pl:
            return self._path_locks.setdefault(p.lower(), threading.Lock())

    # ---- tokens / paths -----------------------------------------------------------------------
    def tokens(self, img: Image, site, count: int = 0, extra=None) -> dict:
        return F.build_tokens(img, self.st, site=site, extra=extra)

    def paths_for(self, img: Image, site, fmt: str, folder: str, count: int = 0) -> list[str]:
        toks = self.tokens(img, site, count)
        fn = F.Filename(fmt)
        return fn.path(toks, self.st, folder, count, F.DEFAULT_FLAGS | F.FLAG_FOLDER)

    def needs_details(self, img: Image, site, fmt: str, blacklist=None) -> bool:
        if img.details_loaded:
            return False
        if not img.file_url and not img.is_gallery:
            return True
        forced = []
        for a in site.source.meta.get("apis", {}).values():
            forced += a.get("forcedTokens") or []
        forced += site.source.meta.get("forcedTokens") or []
        customs = [c["name"] for c in (self.st.get("Customs") or []) if isinstance(c, dict) and c.get("name")]
        # like Grabber: the file name, the separate log files and the commands all count
        formats = [fmt] + [lf.get("content", "") for lf in (self.st.get("LogFiles") or []) if isinstance(lf, dict)]
        formats += [self.st.get(k) or "" for k in ("Exec/tag_before", "Exec/image", "Exec/tag_after", "Exec/SQL/before",
                                                   "Exec/SQL/tag_before", "Exec/SQL/image", "Exec/SQL/tag_after",
                                                   "Exec/SQL/after")]
        formats += [str(c.get("fn") or "") + " " + str(c.get("cond") or "") for c in (self.st.get("Filenames") or [])
                    if isinstance(c, dict)]
        need = max((F.Filename(f).need_exact_tags(forced, customs) for f in formats if f), default=0)
        if need == 2:
            return True
        if need == 1 and any(not t.type for t in img.tags):
            return bool(self.st.get("useregexfortags", True))
        return False

    # ---- main entry ---------------------------------------------------------------------------
    def save(self, img: Image, site, fmt: str | None = None, folder: str | None = None, count: int = 0,
             blacklist: Blacklist | None = None, post_filter: PostFilter | None = None,
             download_blacklisted: bool | None = None, force_size: str | None = None) -> list[SaveResult]:
        fmt = fmt or self.st.filename()
        folder = folder or self.st.save_path()
        self._check()
        if self.needs_details(img, site, fmt):
            try:
                site.details(img, cancel=self.cancel)
            except SiteError as e:
                log.warning("%s: could not load details of %s: %s", site.name, img.id, e)
        if img.is_gallery:
            return self._save_gallery(img, site, fmt, folder, count, blacklist, post_filter, download_blacklisted)
        toks = self.tokens(img, site, count, extra={"grabber": self._grabber_flags(img, site, fmt, folder)})
        if blacklist and not (download_blacklisted if download_blacklisted is not None
                              else self.st.get("downloadblacklist")):
            hit = blacklist.match(toks)
            if hit:
                return [SaveResult("blacklisted", message="blacklisted: " + ", ".join(hit))]
        if post_filter:
            why = post_filter.match(toks)
            if why:
                return [SaveResult("filtered", message="; ".join(why))]
        fn = F.Filename(fmt)
        tmp_first = fn.need_temporary_file(toks)
        if not tmp_first:
            paths = fn.path(toks, self.st, folder, count, F.DEFAULT_FLAGS | F.FLAG_FOLDER)
            if not paths:
                return [SaveResult("failed", message="the file name format gave no name (check it in Settings)")]
            done = self._existing(img, paths)
            if done is not None:
                return [done]
        else:
            paths = []
        return [self._download_and_place(img, site, fmt, folder, count, paths, toks, force_size)]

    def _grabber_flags(self, img, site, fmt, folder) -> F.Lazy:
        def calc():
            out = []
            try:
                paths = self.paths_for(img, site, fmt, folder)
            except Exception:  # noqa: BLE001
                paths = []
            exists = any(Path(p).exists() for p in paths)
            in_md5 = bool(img.md5 and self.lib.md5_path(img.md5, bool(self.st.get("Save/keepDeletedMd5"))) is not None)
            if exists:
                out.append("alreadyExists")
            if in_md5:
                out.append("inMd5List")
            if exists or in_md5:
                out.append("downloaded")
            names = {t.name.lower() for t in img.tags}
            if any(f["name"].lower() in names for f in self.lib.favorites()):
                out.append("favorited")
            if any(m["search"].lower() in names for m in self.lib.monitors()):
                out.append("monitored")
            return out
        return F.Lazy(calc)

    def _existing(self, img: Image, paths: list[str]) -> SaveResult | None:
        """Already saved? Then record it (so the MD5 list knows) and skip."""
        if all(Path(p).exists() for p in paths):
            if img.md5:
                self.lib.md5_add(img.md5, paths[0])
            return SaveResult("exists", paths, "already exists")
        if not img.md5:
            return None
        known = self.lib.md5_path(img.md5, bool(self.st.get("Save/keepDeletedMd5")))
        if known is None:
            return None
        if known == "":
            return SaveResult("duplicate", [], "downloaded before and deleted (Remember files you deleted is on)")
        target = paths[0]
        same_dir = Path(known).parent.resolve() == Path(target).parent.resolve()
        action = self.st.get("Save/md5DuplicatesSameDir" if same_dir else "Save/md5Duplicates") or "ignore"
        if action == "save":
            return None
        if action == "ignore":
            return SaveResult("duplicate", [known], f"same file already saved: {known}")
        placed = []
        for p in paths:
            if Path(p).exists():
                placed.append(p)
                continue
            Path(p).parent.mkdir(parents=True, exist_ok=True)
            try:
                if action == "copy":
                    shutil.copy2(known, p)
                elif action == "move":
                    safe_replace(Path(known), Path(p))
                    self.lib.md5_add(img.md5, p)
                    known = p
                elif action == "hardlink":
                    try:
                        os.link(known, p)
                    except OSError:
                        shutil.copy2(known, p)
                elif action == "link":
                    try:
                        os.symlink(os.path.abspath(known), p)
                    except OSError:
                        _windows_shortcut(known, p) or shutil.copy2(known, p)
                placed.append(p)
            except OSError as e:
                return SaveResult("failed", placed, f"could not {action} {known}: {e}")
        self.lib.record_saved(img.site_key, img.id, img.md5, placed[0] if placed else known)
        return SaveResult({"copy": "copied", "move": "moved"}.get(action, "linked"), placed,
                          f"{action} of {known}")

    # ---- download -----------------------------------------------------------------------------
    def _urls(self, img: Image, force_size: str | None) -> list[str]:
        full_first = force_size != "sample" and (force_size == "full" or bool(self.st.get("Save/downloadoriginals", True)))
        urls = []
        if full_first:
            urls += [img.file_url]
            if self.st.get("Save/samplefallback", True):
                urls += [img.sample_url]
        else:
            urls += [img.sample_url, img.file_url]
        seen, out = set(), []
        for u in urls:
            if u and u not in seen:
                seen.add(u)
                out.append(u)
        return out

    def _rotation(self, url: str, img: Image) -> list[str]:
        """Extension rotation (only for URLs whose extension is a guess: list APIs without 'ext')."""
        if img.raw.get("ext") or img.details_loaded:
            return [url]
        ext = extension_of(url)
        key = "extensionRotationAnimated" if img.is_animated else "extensionRotationStatic"
        exts = str(self.st.get(key) or "").replace(",", " ").split()
        if not ext or ext not in exts:
            return [url]
        i = exts.index(ext)
        return [url] + [set_extension(url, e) for e in exts[i + 1:] + exts[:i]]

    def _download_and_place(self, img, site, fmt, folder, count, paths, toks, force_size) -> SaveResult:
        urls = self._urls(img, force_size)
        if not urls:
            return SaveResult("failed", message="no file URL (the site hides it: log in, or it was deleted)")
        retries = int(self.st.get("Save/automaticretries") or 0)
        temp_dir = self.st.get("Save/tempPathOverride") or ""
        last_err = "failed"
        for attempt in range(retries + 1):
            for url in urls:
                for u in self._rotation(url, img):
                    self._check()
                    try:
                        res = self._fetch_to(img, site, u, folder, fmt, count, paths, toks, temp_dir)
                    except Forbidden as e:
                        return SaveResult("forbidden", message=str(e))
                    if res.status in ("saved", "exists", "duplicate", "copied", "moved", "linked"):
                        return res
                    last_err = res.message or res.status
                    if res.status == "missing":
                        continue   # next extension / the sample
                    if res.status == "forbidden":
                        return res
                    break          # network / size problem: next URL or next attempt
            if attempt < retries:
                emit("grab_retry", key=img.key(), attempt=attempt + 1, reason=last_err)
                _sleep_check(min(30, 2 ** (attempt + 1)), self)
        return SaveResult("missing" if "404" in last_err else "failed", message=last_err)

    def _fetch_to(self, img, site, url, folder, fmt, count, paths, toks, temp_dir) -> SaveResult:
        kind = "image"
        ref = site.referer(kind, img.page_url, img)
        with site.slot():
            try:
                r = site.request(url, kind, stream=True, referer=ref, cancel=self.cancel, retries=2)
            except SiteError as e:
                return SaveResult("failed", message=str(e))
            try:
                if r.status_code == 404:
                    return SaveResult("missing", message=f"HTTP 404 {url}")
                if r.status_code == 403:
                    if site.consecutive_403 >= 20:
                        raise Forbidden(f"{site.name}: 20 file downloads in a row were refused (HTTP 403); "
                                        "stopping this site. Check its login / options, wait, then try again.")
                    return SaveResult("forbidden", message=f"HTTP 403 {url}")
                if r.status_code >= 400:
                    return SaveResult("failed", message=f"HTTP {r.status_code} {url}")
                ctype = (r.headers.get("Content-Type") or "").lower()
                if ctype.startswith("text/html") and img.ext not in ("html", "htm"):
                    return SaveResult("failed", message=f"the site answered with a web page instead of the file ({url})")
                expected = int(r.headers.get("Content-Length") or 0)
                base_dir = Path(temp_dir) if temp_dir else Path(folder)
                base_dir.mkdir(parents=True, exist_ok=True)
                tmp = base_dir / f".packrat-{os.getpid()}-{threading.get_ident()}-{abs(hash(url)) % 10**8}.part"
                h = hashlib.md5()
                n = 0
                head = b""
                resumes = 0
                can_resume = expected > 0 and "bytes" in (r.headers.get("Accept-Ranges") or "").lower()
                try:
                    with open(tmp, "wb") as f:
                        while True:
                            try:
                                for chunk in r.iter_content(CHUNK):
                                    if not chunk:
                                        continue
                                    if len(head) < 32:
                                        head += chunk[:32 - len(head)]
                                    f.write(chunk)
                                    h.update(chunk)
                                    n += len(chunk)
                                    emit("grab_bytes", key=img.key(), n=n, total=expected)
                                    self._check()
                                break
                            except StopRequested:
                                raise
                            except Exception as e:  # noqa: BLE001 - the connection broke mid-transfer
                                # Big files (videos) on a shaky connection: ask for the rest instead of starting over.
                                if not (can_resume and 0 < n < expected and resumes < MAX_RESUMES):
                                    raise
                                resumes += 1
                                log.info("%s: transfer of %s broke at %d of %d bytes (%s); resuming", site.name, img.id,
                                         n, expected, e.__class__.__name__)
                                r.close()
                                _sleep_check(min(8, 2 ** resumes), self)
                                r = site.request(url, kind, stream=True, referer=ref, cancel=self.cancel, retries=2,
                                                 headers={"Range": f"bytes={n}-"})
                                crange = r.headers.get("Content-Range") or ""
                                if r.status_code != 206 or not crange.replace(" ", "").startswith(f"bytes{n}-"):
                                    raise OSError("the site would not resume the transfer") from None
                except StopRequested:
                    safe_unlink(tmp)
                    raise
                except Exception as e:  # noqa: BLE001 - network breaks mid-transfer
                    safe_unlink(tmp)
                    return SaveResult("failed", message=f"transfer broke: {e.__class__.__name__}")
            finally:
                r.close()
        if expected and n != expected:
            safe_unlink(tmp)
            return SaveResult("failed", message=f"incomplete file ({n} of {expected} bytes)")
        got = h.hexdigest()
        if img.md5 and len(img.md5) == 32 and got != img.md5 and url == img.file_url and extension_of(url) not in (
                "zip",) and n > 0:
            # some sites re-encode (or give a sample under the original name); keep it but note it
            log.debug("%s: md5 differs from the site's (%s != %s) for %s", site.name, got, img.md5, url)
        if not img.md5:
            img.md5 = got
        # extension from the file header
        ext_now = extension_of(url) or img.ext
        if self.st.get("Save/headerDetection", True):
            real = sniff_ext(head)
            if real and real != ext_now and not (real == "jpg" and ext_now in ("jpeg", "jpe", "jfif")) and real != "zip":
                ext_now = real
        if ext_now and ext_now != img.ext:
            img.ext = ext_now
        if url == img.sample_url and url != img.file_url:
            img.data["is_sample"] = True
        # final paths (now that md5 / ext are known)
        toks = self.tokens(img, site, count, extra={"grabber": []})
        final = F.Filename(fmt).path(toks, self.st, folder, count, F.DEFAULT_FLAGS | F.FLAG_FOLDER)
        if not final:
            safe_unlink(tmp)
            return SaveResult("failed", message="the file name format gave no name")
        dup = self._existing(img, final)
        if dup is not None:
            safe_unlink(tmp)
            return dup
        placed = []
        multi = self.st.get("Save/multiple_files") or "copy"
        first = Path(final[0])
        with self._plock(str(first)):
            first.parent.mkdir(parents=True, exist_ok=True)
            if first.exists():
                safe_unlink(tmp)
                return SaveResult("exists", [str(first)], "already exists")
            try:
                safe_replace(tmp, first)
            except OSError:
                shutil.move(str(tmp), str(first))
        placed.append(str(first))
        for p in final[1:]:
            if multi == "move":
                break
            q = Path(p)
            q.parent.mkdir(parents=True, exist_ok=True)
            if q.exists():
                placed.append(p)
                continue
            try:
                if multi == "hardlink":
                    os.link(first, q)
                elif multi == "link":
                    os.symlink(os.path.abspath(first), q)
                else:
                    shutil.copy2(first, q)
                placed.append(p)
            except OSError:
                shutil.copy2(first, q)
                placed.append(p)
        img.saved_path = str(first)
        for p in placed:
            self.lib.record_saved(img.site_key, img.id, got, p)
        self.lib.md5_add(got, str(first))
        if img.md5 and img.md5 != got:
            self.lib.md5_add(img.md5, str(first))
        try:
            new = self.post.after_save(img, site, placed, toks, count)
            if new:
                placed = new
        except Exception as e:  # noqa: BLE001 - post-processing never loses the download
            log.warning("after-save step failed for %s: %s", placed[0], e)
        return SaveResult("saved", placed, "", n, got)

    def _save_gallery(self, gal, site, fmt, folder, count, blacklist, post_filter, dl_bl) -> list[SaveResult]:
        out = []
        page = 1
        n = 0
        while True:
            self._check()
            pr = site.gallery(gal, page, 100, cancel=self.cancel)
            if pr.error:
                if not out:
                    out.append(SaveResult("failed", message=f"gallery: {pr.error}"))
                break
            if not pr.images:
                break
            for im in pr.images:
                n += 1
                im.search = gal.search
                out += self.save(im, site, fmt, folder, count + n, blacklist, post_filter, dl_bl)
            if gal.gallery_count > 0 and n >= gal.gallery_count:
                break
            if pr.page_count > 0 and page >= pr.page_count:
                break
            if len(pr.images) < 2 and page > 1:
                break
            page += 1
            if page > 500:
                break
        return out


def _sleep_check(seconds, saver):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        saver._check()
        time.sleep(0.2)


def _windows_shortcut(target: str, link: str) -> bool:
    """A .lnk shortcut (Grabber's 'Shortcut' on Windows) via PowerShell; False elsewhere / on failure."""
    if os.name != "nt":
        return False
    import subprocess
    lnk = link + ".lnk"
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:PR_LNK);"
          "$s.TargetPath=$env:PR_TARGET;$s.Save()")
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, timeout=30,
                       env=dict(os.environ, PR_LNK=lnk, PR_TARGET=os.path.abspath(target)),
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True
    except Exception:  # noqa: BLE001
        return False


# ================================================================================================
@dataclass
class Group:
    """A search to download: Grabber's 'group' in the downloads list."""
    search: str
    sites: list
    page: int = 1
    per_page: int = 60
    limit: int = 0                     # 0 = everything
    filename: str = ""
    path: str = ""
    postfilter: str = ""
    get_blacklisted: bool = False
    no_duplicates: bool = True         # same MD5 found on several sites: download once
    previous: dict = field(default_factory=dict)   # per site: last page info (resume / cursor paging)
    done: dict = field(default_factory=dict)       # per site: images handled
    finished_sites: list = field(default_factory=list)

    def to_json(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_json(cls, d: dict) -> "Group":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


class Batch:
    """Run download-list items (rows of library.batch). Resumable: progress is stored after every page."""

    def __init__(self, settings, library, store, workers: int | None = None, cancel=None):
        self.st, self.lib, self.store = settings, library, store
        self.cancel = cancel or threading.Event()
        self.saver = ImageSaver(settings, library, store, self.cancel)
        self.workers = max(1, min(64, int(workers or settings.get("Save/simultaneous") or 4)))
        self.stats = {"saved": 0, "exists": 0, "duplicate": 0, "copied": 0, "moved": 0, "linked": 0, "skipped": 0,
                      "blacklisted": 0, "filtered": 0, "missing": 0, "failed": 0, "forbidden": 0, "bytes": 0,
                      "images": 0}
        self._lock = threading.Lock()
        self._gate = threading.BoundedSemaphore(self.workers)
        self.counter = 0
        self.failures: list[dict] = []
        self.seen_md5: set[str] = set()

    def _bl(self, get_bl: bool) -> Blacklist | None:
        if get_bl:
            return None
        return Blacklist(self.st.lines("blacklistedtags"))

    def _account(self, img: Image, results: list[SaveResult]):
        with self._lock:
            for r in results:
                self.stats[r.status] = self.stats.get(r.status, 0) + 1
                self.stats["bytes"] += r.bytes
                if r.status in ("failed", "missing", "forbidden"):
                    self.failures.append({"site": img.site_key, "id": img.id, "md5": img.md5, "url": img.file_url,
                                          "reason": r.message, "page": img.page_url})
            self.stats["images"] += 1
        for r in results:
            emit("grab_image", key=img.key(), site=img.site_key, id=str(img.id), status=r.status,
                 path=r.paths[0] if r.paths else "", message=r.message, bytes=r.bytes,
                 preview=img.preview_url)
        emit("grab_stats", **self.stats)

    def run_items(self, items: list[dict]) -> dict:
        init_cmd = self.st.get("Exec/Group/init")
        if init_cmd:
            self.saver.post.run_command(init_cmd, {}, "start")
        self.saver.post.sql_start()
        try:
            i = 0
            while i < len(items):
                self.cancel_check()
                if items[i]["kind"] == "group":
                    self._run_group_item(items[i])
                    i += 1
                else:       # single pictures next to each other in the list download side by side
                    j = i
                    while j < len(items) and items[j]["kind"] != "group":
                        j += 1
                    self._run_uniques(items[i:j])
                    i = j
        finally:
            self.saver.post.sql_end()
            end_cmd = self.st.get("Exec/Group/end")
            if end_cmd:
                self.saver.post.run_command(end_cmd, {}, "end")
            self.saver.post.close()
        return self.stats

    def _set_status(self, it: dict, status: str) -> None:
        self.lib.batch_update(it["id"], status=status)
        emit("grab_item", id=it["id"], item_kind=it["kind"], status=status)

    def _run_group_item(self, it: dict) -> None:
        self._set_status(it, "running")
        try:
            problems = self.run_group(it)
        except StopRequested:
            self._set_status(it, "paused")
            raise
        # A site that could not be read is not "finished": the row stays unfinished, so Start tries it again.
        self._set_status(it, "error" if problems else "done")

    def _run_uniques(self, batch: list[dict]) -> None:
        def one(it):
            self.cancel_check()             # stopped before it began: the row simply stays waiting
            self._set_status(it, "running")
            try:
                self.run_unique(it)
            except StopRequested:
                self._set_status(it, "paused")
                raise
            self._set_status(it, "done")
        if len(batch) == 1:
            one(batch[0])
            return
        stop = None
        with ThreadPoolExecutor(self.workers, thread_name_prefix="grab") as pool:
            for fu in [pool.submit(one, it) for it in batch]:
                try:
                    fu.result()
                except StopRequested as e:
                    stop = e
        if stop is not None:
            raise stop

    def _next(self) -> int:
        with self._lock:
            self.counter += 1
            return self.counter

    def cancel_check(self):
        if self.cancel.is_set():
            raise StopRequested("cancelled")
        control.checkpoint()   # waits here while paused; raises StopRequested on stop

    def run_unique(self, it: dict) -> None:
        d = it["data"]
        img = Image.from_json(d["image"])
        site = self.store.get(img.site_key)
        if site is None:
            self._account(img, [SaveResult("failed", message=f"unknown site {img.site_key}")])
            return
        try:
            res = self.saver.save(img, site, d.get("filename") or None, d.get("path") or None, self._next(),
                                  download_blacklisted=True)
        except Forbidden as e:
            res = [SaveResult("forbidden", message=str(e))]
        self._account(img, res)
        self.lib.batch_update(it["id"], progress={"done": 1, "total": 1, "status": res[0].status if res else ""})

    def run_group(self, it: dict) -> list[str]:
        """Download one search. Its sites run side by side (each keeps its own speed limits), and inside a site the
        next result page is fetched while the current one is still downloading. Progress is stored page by page, in
        order, so stopping and starting again never skips a picture. Returns the sites that had a problem."""
        g = Group.from_json(it["data"])
        bl = self._bl(g.get_blacklisted)
        pf = PostFilter(g.postfilter) if g.postfilter else None
        sites = [s for s in (self.store.get(h) for h in g.sites) if s is not None]
        emit("grab_group", id=it["id"], search=g.search, sites=[s.host for s in sites], limit=g.limit)
        todo = [s for s in sites if s.host not in g.finished_sites]
        glock = threading.Lock()
        problems: list[str] = []
        if len(todo) <= 1:
            for site in todo:
                self._run_site(it, g, site, bl, pf, glock, problems)
            return problems
        stop = None
        with ThreadPoolExecutor(len(todo), thread_name_prefix="site") as sp:
            for fu in [sp.submit(self._run_site, it, g, site, bl, pf, glock, problems) for site in todo]:
                try:
                    fu.result()
                except StopRequested as e:
                    stop = e
        if stop is not None:
            raise stop
        return problems

    def _search_page(self, site, g, page: int, prev):
        """One result page, tried twice. None when the site could not be read."""
        for attempt in (1, 2):
            pr = site.search(g.search, page, g.per_page, prev, cancel=self.cancel)
            if not pr.error:
                return pr
            log.warning("%s page %d: %s", site.name, page, pr.error)
            emit("grab_page", site=site.host, page=page, error=pr.error)
            if "403" in pr.error or attempt == 2:
                return None
            _sleep_check(2, self.saver)
        return None

    def _run_site(self, it, g, site, bl, pf, glock, problems) -> None:
        host = site.host
        with glock:
            prev = g.previous.get(host) or None
            submitted = int(g.done.get(host, 0))          # the limit counts per site
        page = int(prev["page"]) + 1 if prev else g.page
        pending: deque = deque()                          # pages handed to the workers, oldest first
        failed = False

        def commit(entry):
            page_no, futures, n, info, page_count, image_count = entry
            for fu in futures:
                fu.result()
            with glock:
                g.done[host] = int(g.done.get(host, 0)) + n
                g.previous[host] = info
                self.lib.batch_update(it["id"], data=g.to_json(),
                                      progress={"done": sum(int(v) for v in g.done.values()),
                                                "limit": g.limit * max(1, len(g.sites)) if g.limit else 0, "site": host,
                                                "page": page_no, "page_count": page_count, "image_count": image_count})

        with ThreadPoolExecutor(self.workers, thread_name_prefix="grab") as pool:
            try:
                while True:
                    self.cancel_check()
                    if g.limit and submitted >= g.limit:
                        break
                    emit("phase", name=f"{site.name}: page {page} of '{g.search or '(everything)'}'")
                    pr = self._search_page(site, g, page, prev)
                    if pr is None:
                        failed = True
                        break
                    imgs = pr.images
                    if not imgs:
                        break
                    emit("grab_page", site=host, page=page, images=len(imgs), page_count=pr.page_count,
                         image_count=pr.image_count)
                    todo = []
                    for im in imgs:
                        if g.limit and submitted + len(todo) >= g.limit:
                            break
                        if g.no_duplicates and im.md5:
                            with self._lock:
                                if im.md5 in self.seen_md5:
                                    continue
                                self.seen_md5.add(im.md5)
                        todo.append(im)
                    futures = [pool.submit(self._one, im, site, g, bl, pf, self._next()) for im in todo]
                    submitted += len(todo)
                    prev = pr.info()
                    pending.append((page, futures, len(todo), prev, pr.page_count, pr.image_count))
                    while len(pending) > PAGES_AHEAD:
                        commit(pending.popleft())
                    if pr.page_count > 0 and page >= pr.page_count:
                        break
                    if len(imgs) < max(1, min(g.per_page, 5)) and pr.page_count <= 0:
                        break
                    page += 1
                while pending:
                    commit(pending.popleft())
            except BaseException:
                for entry in pending:
                    for fu in entry[1]:
                        fu.cancel()
                raise
        with glock:
            if failed:
                problems.append(host)
            else:
                g.finished_sites.append(host)
            self.lib.batch_update(it["id"], data=g.to_json())

    def _one(self, im, site, g, bl, pf, n):
        with self._gate:                 # "downloads at the same time" counts over all sites together
            try:
                res = self.saver.save(im, site, g.filename or None, g.path or None, n, bl, pf, g.get_blacklisted)
            except StopRequested:
                raise
            except Forbidden as e:
                res = [SaveResult("forbidden", message=str(e))]
            except Exception as e:  # noqa: BLE001 - one image never stops the list
                log.exception("unexpected error saving %s", im.key())
                res = [SaveResult("failed", message=f"{e.__class__.__name__}: {e}")]
        self._account(im, res)
        return res
