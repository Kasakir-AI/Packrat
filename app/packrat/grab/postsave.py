"""What happens after a file is saved: original date, resize, conversions, metadata, sidecars, commands,
SQL, separate log files. Every step is optional (Settings), logged, and never deletes the download when
it fails. External programs (FFmpeg, ImageMagick, ExifTool) are used only when found.
"""
from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path

from . import filename as F

log = logging.getLogger("packrat.grab")
PILLOW_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "bmp", "tif", "tiff"}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def find_program(name: str, configured: str = "") -> str | None:
    if configured and Path(configured).is_file():
        return configured
    from .. import paths
    for base in (paths.install_root(), paths.install_root() / "tools", paths.APP_DIR):
        for cand in (base / name, base / (name + ".exe")):
            if cand.is_file():
                return str(cand)
    return shutil.which(name)


def path_tokens(text: str, path: str) -> str:
    p = str(Path(path))
    return (text.replace("%path:nobackslash%", p.replace("\\", "/")).replace("%path%", p)
            .replace("%dir%", str(Path(path).parent)))


def sql_escape(v) -> str:
    return "'" + str(v).replace("\\", "\\\\").replace("'", "''") + "'"


class PostSave:
    def __init__(self, st):
        self.st = st
        self._sql = None
        self._sql_failed = False
        self._booru = None
        self._booru_off = False
        self.booru_stats = {"uploaded": 0, "exists": 0, "failed": 0}

    # ---- entry ------------------------------------------------------------------------------
    def after_save(self, img, site, paths: list[str], toks: dict, count: int) -> list[str] | None:
        paths = list(paths)
        first = paths[0]
        self.log_files(img, site, first, toks, count)
        date = img.date
        if self.st.get("Save/keepDate", True) and date is not None:
            for p in paths:
                set_file_date(p, date)
        new_first = self.convert(img, site, first)
        if new_first and new_first != first:
            paths[0] = first = new_first
        self.resize(first)
        self.metadata(img, site, first, toks, count)
        if self.st.get("Save/WriteSidecarJson"):
            self.sidecar_json(img, site, first, toks)
        self.commands(img, site, first, toks, count)
        self.local_booru(img, first)
        if self.st.get("Save/keepDate", True) and date is not None:
            set_file_date(first, date)
        return paths

    # ---- local booru ------------------------------------------------------------------------
    def local_booru(self, img, path: str) -> None:
        if self._booru_off:
            return
        if self._booru is None:
            from .localbooru import LocalBooru
            self._booru = LocalBooru.from_settings(self.st)
            if self._booru is None:
                self._booru_off = True
                return
        from .localbooru import UploadError
        src = ""
        if img.sources:
            src = img.sources[0] if isinstance(img.sources, list) else str(img.sources)
        src = src or img.page_url or ""
        try:
            res = self._booru.upload(path, img, str(img.data.get("rating") or img.rating or ""), src)
            self.booru_stats[res] = self.booru_stats.get(res, 0) + 1
            log.info("local booru: %s %s", res, Path(path).name)
        except UploadError as e:
            self.booru_stats["failed"] += 1
            log.warning("local booru upload failed for %s: %s", Path(path).name, e)
            if self.booru_stats["failed"] >= 5 and not (self.booru_stats["uploaded"] or self.booru_stats["exists"]):
                log.warning("local booru: 5 failures in a row, no more uploads this run")
                self._booru_off = True

    # ---- separate log files -----------------------------------------------------------------
    def log_files(self, img, site, path: str, toks: dict, count: int) -> None:
        for lf in self.st.get("LogFiles") or []:
            if not isinstance(lf, dict) or not lf.get("content"):
                continue
            out = F.Filename(lf["content"]).path(toks, self.st, "", count, F.FLAG_CONDITIONALS)
            if not out:
                continue
            contents = path_tokens(out[0], path)
            loc = int(lf.get("locationType", 0) or 0)
            if loc == 0:
                ps = F.Filename(lf.get("filename") or "log.txt").path(toks, self.st, lf.get("path") or self.st.save_path(),
                                                                     count, F.DEFAULT_FLAGS | F.FLAG_FOLDER)
                target = ps[0] if ps else ""
            elif loc == 1:
                target = lf.get("uniquePath") or ""
            elif loc == 2:
                target = path + (lf.get("suffix") or ".txt")
            else:
                target = str(Path(path).with_suffix("")) + (lf.get("suffixWithoutExtension") or ".txt")
            if not target:
                continue
            target = path_tokens(target, path)
            try:
                Path(target).parent.mkdir(parents=True, exist_ok=True)
                exists = Path(target).exists()
                with open(target, "a", encoding="utf-8", newline="") as f:
                    f.write(("\n" if exists else "") + contents)
            except OSError as e:
                log.warning("could not write the log file %s: %s", target, e)

    # ---- conversions ------------------------------------------------------------------------
    def convert(self, img, site, path: str) -> str | None:
        p = Path(path)
        ext = p.suffix.lower().lstrip(".")
        overwrite = bool(self.st.get("Save/ConversionOverwrite", True))
        if ext == "zip" and self.st.get("Save/ConvertUgoira"):
            return self._ugoira(img, site, p, overwrite)
        if ext == "webm" and (self.st.get("Save/FFmpegRemuxWebmToMp4") or self.st.get("Save/FFmpegConvertWebmToMp4")):
            return self._webm_to_mp4(p, overwrite)
        for rule in self.st.get("Save/ImageConversions") or []:
            if not isinstance(rule, dict):
                continue
            src = str(rule.get("from") or "").lower().lstrip(".")
            dst = str(rule.get("to") or "").lower().lstrip(".")
            if src == ext and dst and dst != ext:
                return self._image_convert(p, dst, overwrite) or path
        return None

    def _image_convert(self, p: Path, dst: str, overwrite: bool) -> str | None:
        out = p.with_suffix("." + dst)
        if out.exists() and not overwrite:
            return None
        try:
            from PIL import Image as PI
            with PI.open(p) as im:
                if dst in ("jpg", "jpeg") and im.mode in ("RGBA", "LA", "P"):
                    im = im.convert("RGB")
                im.save(out, quality=95) if dst in ("jpg", "jpeg", "webp") else im.save(out)
        except Exception as e:  # noqa: BLE001 - Pillow can't: try ImageMagick
            magick = find_program("magick", self.st.get("magickPath") or "")
            if not magick:
                log.warning("could not convert %s to %s: %s", p.name, dst, e)
                return None
            r = _run([magick, str(p), str(out)], 300)
            if r != 0 or not out.exists():
                log.warning("ImageMagick could not convert %s", p.name)
                return None
        p.unlink(missing_ok=True)
        log.info("converted %s -> %s", p.name, out.name)
        return str(out)

    def _webm_to_mp4(self, p: Path, overwrite: bool) -> str | None:
        ff = find_program("ffmpeg", self.st.get("ffmpegPath") or "")
        if not ff:
            log.warning("FFmpeg not found: WEBM -> MP4 skipped (install it, or set its path in Settings > Conversion)")
            return None
        out = p.with_suffix(".mp4")
        if out.exists() and not overwrite:
            return None
        to = int(self.st.get("Save/FFmpegConvertTimeout") or 300)
        y = ["-y"] if overwrite else ["-n"]
        ok = False
        if self.st.get("Save/FFmpegRemuxWebmToMp4"):
            ok = _run([ff, "-hide_banner", "-loglevel", "error", *y, "-i", str(p), "-c", "copy",
                       "-movflags", "+faststart", str(out)], to) == 0 and out.exists()
        if not ok and self.st.get("Save/FFmpegConvertWebmToMp4"):
            ok = _run([ff, "-hide_banner", "-loglevel", "error", *y, "-i", str(p), "-c:v", "libx264", "-crf", "20",
                       "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart",
                       str(out)], to) == 0 and out.exists()
        if not ok:
            out.unlink(missing_ok=True)
            return None
        p.unlink(missing_ok=True)
        return str(out)

    def _ugoira(self, img, site, p: Path, overwrite: bool) -> str | None:
        import zipfile
        fmt = (self.st.get("Save/ConvertUgoiraFormat") or "gif").lower()
        delays = _ugoira_delays(img, site)
        try:
            with zipfile.ZipFile(p) as z:
                names = sorted(n for n in z.namelist() if not n.endswith("/"))
                from io import BytesIO
                from PIL import Image as PI
                frames = [PI.open(BytesIO(z.read(n))).convert("RGBA") for n in names]
        except Exception as e:  # noqa: BLE001
            log.warning("ugoira %s: %s", p.name, e)
            return None
        if not frames:
            return None
        durs = [delays.get(n, 100) for n in names]
        if fmt == "mp4":
            ff = find_program("ffmpeg", self.st.get("ffmpegPath") or "")
            if not ff:
                log.warning("FFmpeg not found: ugoira -> MP4 skipped")
                return None
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                lst = Path(td) / "list.txt"
                lines = []
                for i, (fr, d) in enumerate(zip(frames, durs)):
                    fp = Path(td) / f"{i:05d}.png"
                    fr.save(fp)
                    lines += [f"file '{fp.as_posix()}'", f"duration {d / 1000:.3f}"]
                lines.append(f"file '{(Path(td) / f'{len(frames) - 1:05d}.png').as_posix()}'")
                lst.write_text("\n".join(lines), "utf-8")
                out = p.with_suffix(".mp4")
                rc = _run([ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                           "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
                          int(self.st.get("Save/FFmpegConvertTimeout") or 300))
                if rc != 0:
                    return None
        else:
            ext = {"gif": "gif", "webp": "webp", "apng": "png"}.get(fmt, "gif")
            out = p.with_suffix("." + ext)
            if out.exists() and not overwrite:
                return None
            kw = {"save_all": True, "append_images": frames[1:], "duration": durs, "loop": 0}
            if ext == "gif":
                frames = [f.convert("RGB").convert("P", palette=1, colors=256) for f in frames]
                kw["append_images"] = frames[1:]
                kw["disposal"] = 2
            frames[0].save(out, **kw)
        if self.st.get("Save/ConvertUgoiraDeleteOriginal"):
            p.unlink(missing_ok=True)
            return str(out)
        return None

    def resize(self, path: str) -> None:
        mw, mh = int(self.st.get("Save/resizeMaxWidth") or 0), int(self.st.get("Save/resizeMaxHeight") or 0)
        if not (mw or mh):
            return
        ext = Path(path).suffix.lower().lstrip(".")
        if ext not in PILLOW_EXTS or ext == "gif":
            return
        try:
            from PIL import Image as PI
            with PI.open(path) as im:
                w, h = im.size
                if (not mw or w <= mw) and (not mh or h <= mh):
                    return
                im.thumbnail((mw or w, mh or h), PI.LANCZOS)
                tmp = path + ".resize.tmp"
                im.save(tmp, format=im.format or None, quality=95)
            os.replace(tmp, path)
        except Exception as e:  # noqa: BLE001
            log.warning("could not resize %s: %s", path, e)

    # ---- metadata ---------------------------------------------------------------------------
    def metadata(self, img, site, path: str, toks: dict, count: int) -> None:
        props = list(self.st.get("Save/MetadataExiftool") or [])
        props += [dict(p, key=_PROPSYS.get(p.get("key", ""), p.get("key", ""))) for p in
                  (self.st.get("Save/MetadataPropsys") or []) if isinstance(p, dict)]
        props = [p for p in props if isinstance(p, dict) and p.get("key")]
        if not props:
            return
        ext = Path(path).suffix.lower().lstrip(".")
        allowed = str(self.st.get("Save/MetadataExiftoolExtensions") or "").split()
        sidecar = self.st.get("Save/MetadataExiftoolSidecar") or "on_error"
        exif = find_program("exiftool", self.st.get("exiftoolPath") or "")
        if not exif:
            log.warning("ExifTool not found: metadata not written (install it, or set its path in Settings > Metadata)")
            return
        args = []
        if self.st.get("Save/MetadataExiftoolClear"):
            args += ["-all="] + (["--icc_profile:all"] if self.st.get("Save/MetadataExiftoolKeepColorProfile", True) else [])
        for p in props:
            vals = F.Filename(str(p.get("value") or "")).path(toks, self.st, "", count, F.FLAG_CONDITIONALS)
            v = path_tokens(vals[0], path) if vals else ""
            key = p["key"]
            if "," in v and key.lower().endswith(("subject", "keywords", "xpkeywords")):
                args += [f"-{key}={x.strip()}" for x in v.split(",") if x.strip()]
            else:
                args.append(f"-{key}={v}")
        args += ["-overwrite_original", "-charset", "filename=utf8", "-codedcharacterset=utf8"]

        def write(target):
            return _run([exif, *args, target], 120) == 0
        wrote = False
        if sidecar != "only" and (not allowed or ext in allowed):
            wrote = write(path)
        if sidecar in ("both", "only") or (sidecar == "on_error" and not wrote):
            side = (str(Path(path).with_suffix("")) if self.st.get("Save/MetadataExiftoolSidecarNoExtension")
                    else path) + ".xmp"
            if not Path(side).exists():
                _run([exif, "-o", side, path], 120)
            write(side)

    def sidecar_json(self, img, site, path: str, toks: dict) -> None:
        data = {"site": img.site_key, "site_name": getattr(site, "name", img.site_key), "id": img.id, "md5": img.md5,
                "ext": img.ext, "width": img.width, "height": img.height, "file_size": img.file_size,
                "rating": img.rating, "score": img.score, "date": img.date.isoformat() if img.date else None,
                "sources": img.sources, "page_url": img.page_url, "file_url": img.file_url,
                "tags": {}, "search": " ".join(img.search), "saved": datetime.now().astimezone().isoformat(),
                "data": {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in img.data.items()
                         if isinstance(v, (str, int, float, bool, list, dict, datetime)) and k != "endpoint_images"}}
        for t in img.tags:
            data["tags"].setdefault(t.type or "general", []).append(t.name)
        try:
            Path(path + ".json").write_text(json.dumps(data, indent=1, ensure_ascii=False, default=str), "utf-8")
        except OSError as e:
            log.warning("could not write %s.json: %s", path, e)

    # ---- commands / SQL ---------------------------------------------------------------------
    def commands(self, img, site, path: str, toks: dict, count: int) -> None:
        tb, ta, im = (self.st.get("Exec/tag_before"), self.st.get("Exec/tag_after"), self.st.get("Exec/image"))
        stb, sta, sim = (self.st.get("Exec/SQL/tag_before"), self.st.get("Exec/SQL/tag_after"),
                         self.st.get("Exec/SQL/image"))
        if not any((tb, ta, im, stb, sta, sim)):
            return
        for t in img.tags if (tb or stb) else []:
            self._tag_cmd(tb, stb, t, path)
        if im:
            self.run_command(im, toks, "image", path, count)
        if sim:
            self.sql(sim, toks, path, count)
        for t in img.tags if (ta or sta) else []:
            self._tag_cmd(ta, sta, t, path)

    def _tag_cmd(self, cmd, sql, tag, path):
        from .tags import KNOWN_TYPES
        num = KNOWN_TYPES.index(tag.type) if tag.type in KNOWN_TYPES else 0
        repl = lambda s, esc=False: (s.replace("%tag%", sql_escape(tag.name) if esc else tag.name)  # noqa: E731
                                      .replace("%original%", tag.name).replace("%type%", tag.type or "general")
                                      .replace("%number%", str(num)))
        if cmd:
            self._exec(path_tokens(repl(cmd), path), "tag")
        if sql:
            self._sql_exec(path_tokens(repl(sql, True), path))

    def run_command(self, fmt: str, toks: dict, what: str, path: str = "", count: int = 0) -> None:
        out = F.Filename(fmt).path(toks, self.st, "", count, 0) if toks else [fmt]
        if not out:
            return
        self._exec(path_tokens(out[0], path) if path else out[0], what)

    def _exec(self, cmd: str, what: str) -> None:
        if self.st.get("Exec/dry_run"):
            log.info("[dry run] %s command: %s", what, cmd)
            return
        log.info("%s command: %s", what, cmd)
        try:
            if os.name == "nt":
                p = subprocess.run(cmd, shell=True, timeout=600, capture_output=True, creationflags=NO_WINDOW)
            else:
                p = subprocess.run(shlex.split(cmd), timeout=600, capture_output=True)
            if p.returncode != 0:
                log.warning("%s command exited with %s: %s", what, p.returncode,
                            (p.stderr or b"")[:300].decode("utf-8", "replace"))
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            log.warning("%s command failed: %s", what, e)

    def _db(self):
        if self._sql is not None or self._sql_failed:
            return self._sql
        drv = self.st.get("Exec/SQL/driver") or "QSQLITE"
        host = self.st.get("Exec/SQL/host") or ""
        try:
            if drv == "QSQLITE":
                if not host:
                    raise ValueError("no database file set (Settings > Commands > Database > Host)")
                self._sql = sqlite3.connect(host, timeout=30)
            elif drv == "QMYSQL":
                import pymysql  # type: ignore
                self._sql = pymysql.connect(host=host, user=self.st.get("Exec/SQL/user") or "",
                                            password=self.st.secret("Exec/SQL/password"),
                                            database=self.st.get("Exec/SQL/database") or "", autocommit=True)
            elif drv == "QPSQL":
                import psycopg  # type: ignore
                self._sql = psycopg.connect(host=host, user=self.st.get("Exec/SQL/user") or "",
                                            password=self.st.secret("Exec/SQL/password"),
                                            dbname=self.st.get("Exec/SQL/database") or "", autocommit=True)
        except Exception as e:  # noqa: BLE001
            log.warning("database commands disabled: %s", e)
            self._sql_failed = True
            self._sql = None
        return self._sql

    def sql(self, fmt: str, toks: dict, path: str = "", count: int = 0) -> None:
        fn = F.Filename(fmt)
        fn.escape = sql_escape
        out = fn.path(toks, self.st, "", count, 0)
        if out:
            self._sql_exec(path_tokens(out[0], path))

    def _sql_exec(self, q: str) -> None:
        if self.st.get("Exec/dry_run"):
            log.info("[dry run] SQL: %s", q)
            return
        db = self._db()
        if db is None:
            return
        try:
            cur = db.cursor()
            cur.execute(q)
            if hasattr(db, "commit"):
                db.commit()
        except Exception as e:  # noqa: BLE001
            log.warning("SQL command failed: %s (%s)", e, q[:200])

    def sql_start(self):
        q = self.st.get("Exec/SQL/before")
        if q:
            self._sql_exec(q)

    def sql_end(self):
        q = self.st.get("Exec/SQL/after")
        if q:
            self._sql_exec(q)

    def close(self):
        if self._sql is not None:
            try:
                self._sql.close()
            except Exception:  # noqa: BLE001
                pass
            self._sql = None


_PROPSYS = {"System.Author": "XPAuthor", "System.Keywords": "XPKeywords", "System.Comment": "XPComment",
            "System.Title": "XPTitle", "System.Subject": "XPSubject", "System.Rating": "Rating"}


def _ugoira_delays(img, site) -> dict:
    meta = img.data.get("ugoira_metadata") or {}
    if not meta and site is not None:
        for k in site.api_keys():
            if "ugoira_details" in site.source.meta["apis"][k].get("endpoints", {}):
                res = site.endpoint(k, "ugoira_details", {"id": str(img.id)})
                if not res.get("error"):
                    meta = res
                    break
    out = {}
    for fr in meta.get("frames") or []:
        if isinstance(fr, dict) and fr.get("file"):
            try:
                out[fr["file"]] = int(fr.get("delay") or 100)
            except (TypeError, ValueError):
                pass
    return out


def set_file_date(path: str, dt) -> None:
    try:
        ts = dt.timestamp()
        os.utime(path, (ts, ts))
    except (OSError, ValueError, OverflowError):
        pass
    if os.name == "nt":
        _set_creation_time_windows(path, dt)


def _set_creation_time_windows(path: str, dt) -> None:
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        h = k32.CreateFileW(str(path), 0x100, 0x7, None, 3, 0x80 | 0x02000000, None)
        if h == -1 or h == 0xFFFFFFFF:
            return
        try:
            ft = int((dt.timestamp() + 11644473600) * 10_000_000)
            c = wintypes.FILETIME(ft & 0xFFFFFFFF, ft >> 32)
            k32.SetFileTime(h, ctypes.byref(c), None, None)
        finally:
            k32.CloseHandle(h)
    except Exception:  # noqa: BLE001
        pass


def _run(argv, timeout: int) -> int:
    try:
        return subprocess.run(argv, timeout=timeout, capture_output=True, creationflags=NO_WINDOW).returncode
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("%s failed: %s", Path(argv[0]).name, e)
        return 1


def batch_end_action(action: str) -> None:
    """Grabber's 'When the download is finished': notify / play a sound / open CD tray / shut down."""
    if action == "sound":
        try:
            if os.name == "nt":
                import winsound
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            else:
                print("\a", end="", flush=True)
        except Exception:  # noqa: BLE001
            pass
    elif action == "cdtray" and os.name == "nt":
        try:
            import ctypes
            ctypes.windll.WINMM.mciSendStringW("set cdaudio door open", None, 0, None)
        except Exception:  # noqa: BLE001
            pass
    elif action == "shutdown":
        if os.name == "nt":
            subprocess.run(["shutdown", "/s", "/t", "60", "/c", "Packrat finished downloading. Run 'shutdown /a' "
                            "to cancel."], creationflags=NO_WINDOW)
        else:
            subprocess.run(["shutdown", "-h", "+1"])
    time.sleep(0)
