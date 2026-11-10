"""Import a Grabber profile: settings, favorites, monitors, kept-for-later, blacklist, ignored tags,
autocomplete words, the MD5 list (md5s.txt or md5s.sqlite), the download list (restore.igl), sites added
by hand and their logins.

Nothing in the Grabber folder is changed. Importing twice is safe: favorites / kept-for-later / MD5s
are merged, monitors and download-list items that already exist (same search and sites) are skipped.
Passwords and API keys go to the Windows Credential Manager like the ones typed in Packrat.
Monitors start counting from the moment of import (their first check only records where "now" is).
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from . import qtini
from .image import Image, parse_date
from .settings import BY_KEY, SETTINGS
from .tags import Tag

log = logging.getLogger("packrat.grab")

PARTS = ("settings", "favorites", "monitors", "later", "blacklist", "words", "md5", "downloads", "sites")
SECRET_SITE_FIELDS = {"password", "password_hash", "passwordHash", "apiKey", "api_key", "accessToken",
                      "accessTokenSecret", "refreshToken", "consumerSecret", "token", "cookie"}


def candidate_folders() -> list[Path]:
    """Where Grabber keeps its profile (same order Grabber looks in)."""
    home = Path.home()
    c = []
    for env, sub in (("LOCALAPPDATA", "Bionus/Grabber"), ("APPDATA", "Bionus/Grabber"),
                     ("ProgramFiles", "Grabber"), ("ProgramFiles(x86)", "Grabber")):
        v = os.environ.get(env)
        if v:
            c.append(Path(v) / sub)
    c += [home / "Grabber", home / ".Grabber", home / ".config/Bionus/Grabber", home / ".local/share/Grabber",
          Path("/usr/share/Grabber"), home / "Library/Preferences/Bionus/Grabber"]
    return c


def find_profiles() -> list[Path]:
    out = []
    for d in candidate_folders():
        try:
            if (d / "settings.ini").is_file() or (d / "favorites.json").is_file():
                out.append(d)
        except OSError:
            pass
    return out


def is_profile(folder) -> bool:
    p = Path(folder)
    return any((p / f).exists() for f in ("settings.ini", "favorites.json", "favorites.txt", "monitors.json",
                                           "md5s.txt", "md5s.sqlite", "restore.igl"))


def summary(folder) -> dict:
    """What a profile contains (shown before importing)."""
    p = Path(folder)
    out = {"settings": (p / "settings.ini").is_file()}
    fav = _read_favorites(p)
    out["favorites"] = len(fav)
    out["monitors"] = len(_read_json(p / "monitors.json").get("monitors") or []) + sum(
        len(f.get("monitors") or []) for f in fav)
    out["later"] = len(_lines(p / "viewitlater.txt"))
    out["md5"] = _count_md5(p)
    dl = _read_json(p / "restore.igl")
    out["downloads"] = len(dl.get("batchs") or []) + len(dl.get("uniques") or [])
    out["sites"] = len(list(_site_dirs(p)))
    return out


# ---- readers ------------------------------------------------------------------------------------
def _read_json(p: Path) -> dict:
    try:
        v = json.loads(p.read_text("utf-8-sig"))
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def _lines(p: Path) -> list[str]:
    try:
        return [ln.strip() for ln in p.read_text("utf-8-sig", errors="replace").splitlines() if ln.strip()]
    except OSError:
        return []


def _read_favorites(p: Path) -> list[dict]:
    d = _read_json(p / "favorites.json")
    if d:
        return [f for f in d.get("favorites") or [] if isinstance(f, dict) and f.get("tag")]
    out = []
    for ln in _lines(p / "favorites.txt"):   # very old format: tag|note|lastviewed
        xs = ln.split("|")
        out.append({"tag": xs[0], "note": qtini.to_int(xs[1] if len(xs) > 1 else 50, 50),
                    "lastViewed": xs[2] if len(xs) > 2 else ""})
    return out


def _count_md5(p: Path) -> int:
    if (p / "md5s.sqlite").is_file():
        try:
            con = sqlite3.connect(f"file:{p / 'md5s.sqlite'}?mode=ro", uri=True)
            try:
                return con.execute("SELECT COUNT(*) FROM md5s").fetchone()[0]
            finally:
                con.close()
        except sqlite3.Error:
            return 0
    return len(_lines(p / "md5s.txt"))


def _site_dirs(p: Path):
    base = p / "sites"
    if not base.is_dir():
        return
    for tdir in base.iterdir():
        if not tdir.is_dir():
            continue
        for sdir in tdir.iterdir():
            if sdir.is_dir() and (sdir / "settings.ini").is_file():
                yield tdir.name, sdir.name, sdir
    # sites added by hand are listed in sites/<Type>/sites.txt


def _added_sites(p: Path):
    base = p / "sites"
    if not base.is_dir():
        return
    for tdir in base.iterdir():
        if tdir.is_dir():
            for host in _lines(tdir / "sites.txt"):
                yield tdir.name, host


def _query_tags(q) -> str:
    """Grabber's SearchQuery json -> a search string."""
    if isinstance(q, dict):
        tags = q.get("tags")
        if isinstance(tags, list):
            return " ".join(str(t) for t in tags)
        if isinstance(tags, str):
            return tags
        return ""
    if isinstance(q, list):
        return " ".join(str(t) for t in q)
    return str(q or "")


def _iso_ts(v) -> float | None:
    if not v:
        return None
    d = parse_date(str(v))
    if d is None:
        try:
            d = datetime.fromisoformat(str(v))
        except ValueError:
            return None
    try:
        return d.timestamp()
    except (OverflowError, OSError, ValueError):
        return None


# ---- settings ----------------------------------------------------------------------------------
def settings_from_ini(ini: dict) -> dict:
    """Grabber settings.ini (already parsed) -> {Packrat key: value}."""
    out: dict = {}
    if ini.get("sites"):
        v = ini["sites"]
        out["sites"] = [x for x in (v if isinstance(v, list) else str(v).split(",")) if str(x).strip()]
    for s in SETTINGS:
        if s.kind in ("list", "secret", "hidden") or s.key not in ini:
            continue
        v = ini[s.key]
        if s.kind == "bool":
            out[s.key] = qtini.to_bool(v, bool(s.default))
        elif s.kind == "int":
            out[s.key] = qtini.to_int(v, int(s.default or 0))
        elif s.kind == "float":
            try:
                out[s.key] = float(v)
            except (TypeError, ValueError):
                pass
        elif s.kind == "lines":
            out[s.key] = "\n".join(v) if isinstance(v, list) else "\n".join(str(v).split())
        else:
            out[s.key] = ", ".join(v) if isinstance(v, list) else str(v)
            if s.kind == "choice" and s.choices and out[s.key] not in [c[0] for c in s.choices]:
                out.pop(s.key)   # a value this version doesn't offer: keep Packrat's default
    # custom tokens: [Save] Customs\name=tags
    customs = [{"name": k.split("/", 2)[2], "tags": " ".join(v) if isinstance(v, list) else str(v)}
               for k, v in ini.items() if k.startswith("Save/Customs/") and k.count("/") == 2]
    if customs:
        out["Customs"] = customs
    # conditional file names: [Filenames] N_cond / N_fn / N_dir
    rules = {}
    for k, v in ini.items():
        if k.startswith("Filenames/") and "_" in k:
            n, _, what = k[10:].partition("_")
            if n.isdigit():
                rules.setdefault(int(n), {})[what] = ", ".join(v) if isinstance(v, list) else str(v)
    if rules:
        out["Filenames"] = [{"cond": r.get("cond", ""), "fn": r.get("fn", ""), "dir": r.get("dir", "")}
                            for _, r in sorted(rules.items()) if r.get("cond")]
    # separate log files: [LogFiles] N\key=value
    logs = {}
    for k, v in ini.items():
        if k.startswith("LogFiles/") and k.count("/") == 2:
            _, n, key = k.split("/")
            if n.isdigit():
                logs.setdefault(int(n), {})[key] = ", ".join(v) if isinstance(v, list) else v
    if logs:
        lst = []
        for _, d in sorted(logs.items()):
            d["locationType"] = qtini.to_int(d.get("locationType"), 0)
            lst.append(d)
        out["LogFiles"] = lst
    # web services: [WebServices] N\name, N\url, N\order
    ws = {}
    for k, v in ini.items():
        if k.startswith("WebServices/") and k.count("/") == 2:
            _, n, key = k.split("/")
            ws.setdefault(n, {})[key] = v
    if ws:
        out["WebServices"] = [{"name": str(d.get("name") or ""), "url": str(d.get("url") or "")}
                              for d in sorted(ws.values(), key=lambda d: qtini.to_int(d.get("order"), 0))
                              if d.get("url")]
    # image conversions: [Save] ImageConversion\PNG\to=jpg
    conv = []
    for k, v in ini.items():
        if k.startswith("Save/ImageConversion/") and k.endswith("/to") and str(v).strip():
            conv.append({"from": k.split("/")[2].lower(), "to": str(v).lower()})
    if conv:
        out["Save/ImageConversions"] = conv
    # metadata arrays: Save/MetadataExiftool\1\key, \1\value, \size
    for arr in ("Save/MetadataExiftool", "Save/MetadataPropsys"):
        items = {}
        for k, v in ini.items():
            if k.startswith(arr + "/") and k.count("/") == 3:
                n, key = k.split("/")[2:]
                if n.isdigit():
                    items.setdefault(int(n), {})[key] = ", ".join(v) if isinstance(v, list) else str(v)
        if items:
            out[arr] = [{"key": d.get("key", ""), "value": d.get("value", "")} for _, d in sorted(items.items())
                        if d.get("key")]
    # tag colors
    colors = {k.split("/", 2)[2]: str(v) for k, v in ini.items() if k.startswith("Coloring/Colors/")}
    if colors and "Coloring" in BY_KEY:
        base = dict(BY_KEY["Coloring"].default)
        base.update(colors)
        out["Coloring"] = base
    return out


# ---- the import ------------------------------------------------------------------------------
def import_profile(folder, st, lib, store, parts=PARTS, progress=None) -> dict:
    """Import the chosen parts. Returns counts per part, plus "notes" (things that were skipped)."""
    p = Path(folder)
    if not is_profile(p):
        raise ValueError(f"{p} doesn't look like a Grabber profile folder (no settings.ini, favorites.json ...)")
    rep: dict = {"notes": []}
    ini = qtini.load(p / "settings.ini")

    def step(name):
        if progress:
            progress(name)

    if "settings" in parts and ini:
        step("settings")
        vals = settings_from_ini(ini)
        st.update(vals)
        for key in ("Proxy/password", "Exec/SQL/password"):
            if ini.get(key):
                st.set(key, str(ini[key]))
        rep["settings"] = len(vals)

    if "blacklist" in parts:
        step("blacklist")
        bl = []
        old = ini.get("blacklistedtags")
        if old:
            bl += (old if isinstance(old, list) else str(old).split())
        bl += [ln for ln in _lines(p / "blacklist.txt") if not ln.startswith("#")]
        if bl:
            cur = st.lines("blacklistedtags")
            st.set("blacklistedtags", "\n".join(cur + [b for b in bl if b not in cur]))
        ign = _lines(p / "ignore.txt")
        if ign:
            cur = st.lines("ignoredtags")
            st.set("ignoredtags", "\n".join(cur + [i for i in ign if i not in cur]))
        rep["blacklist"] = len(bl)
        rep["ignored"] = len(ign)

    if "sites" in parts:
        step("sites")
        n_added = n_set = 0
        for type_name, host in _added_sites(p):
            if store.get(host) is None:
                if type_name in store.types:
                    try:
                        store.add_site(host, type_name)
                        n_added += 1
                    except ValueError as e:
                        rep["notes"].append(f"site {host}: {e}")
                else:
                    rep["notes"].append(f"site {host}: source type {type_name} is not available in Packrat")
        for type_name, host, sdir in _site_dirs(p):
            site = store.get(host)
            if site is None and type_name in store.types:
                try:
                    site = store.add_site(host, type_name)
                    n_added += 1
                except ValueError:
                    site = None
            if site is None:
                rep["notes"].append(f"site {host}: not available in Packrat (source {type_name})")
                continue
            for k, v in qtini.load(sdir / "settings.ini").items():
                if k.startswith("auth/"):
                    field = k[5:]
                    if str(v):
                        site.set_auth_value(field, ", ".join(v) if isinstance(v, list) else str(v))
                        n_set += 1
                elif k in ("cookies",) or k.startswith(("login/", "headers/")) or k in (
                        "name", "ssl", "referer", "referer_preview", "referer_image", "useragent"
                ) or k.startswith(("download/", "sources/", "Login/")):
                    site.set_setting(k, v)
                    n_set += 1
        rep["sites"] = n_added
        rep["site_settings"] = n_set

    favs = _read_favorites(p) if ("favorites" in parts or "monitors" in parts) else []
    if "favorites" in parts:
        step("favorites")
        n = 0
        for f in favs:
            name = str(f["tag"]).strip()
            if not name:
                continue
            lib.add_favorite(name, f.get("sites") or [], qtini.to_int(f.get("note"), 50))
            kw = {}
            lv = _iso_ts(f.get("lastViewed"))
            if lv:
                kw["last_viewed"] = lv
            pf = f.get("postFiltering")
            if pf:
                kw["postfilter"] = " ".join(pf) if isinstance(pf, list) else str(pf)
            thumb = p / "thumbs" / (_safe(name) + ".png")
            if thumb.is_file():
                kw["image"] = str(thumb)
            if kw:
                lib.update_favorite(name, **kw)
            n += 1
        rep["favorites"] = n

    if "monitors" in parts:
        step("monitors")
        existing = {(m["search"], tuple(sorted(m["sites"]))) for m in lib.monitors()}
        mons = list(_read_json(p / "monitors.json").get("monitors") or [])
        for f in favs:   # older Grabber: monitors stored inside each favorite
            for m in f.get("monitors") or []:
                if isinstance(m, dict):
                    m = dict(m)
                    m.setdefault("query", {"tags": str(f["tag"]).split()})
                    mons.append(m)
        n = 0
        for m in mons:
            if not isinstance(m, dict):
                continue
            search = _query_tags(m.get("query") or m.get("tags") or "")
            sites = [str(s) for s in (m.get("sites") or [])] + ([str(m["site"])] if m.get("site") else [])
            sites = [s for s in sites if store.get(s) is not None]
            if not sites:
                rep["notes"].append(f"monitor '{search}': none of its sites exist in Packrat")
                continue
            key = (search, tuple(sorted(sites)))
            if key in existing:
                continue
            existing.add(key)
            pf = m.get("postFilters") or []
            lib.add_monitor(search, sites,
                            interval_min=max(1, qtini.to_int(m.get("interval"), 86400) // 60),
                            delay_min=qtini.to_int(m.get("delay"), 0) // 60,
                            notify=qtini.to_bool(m.get("notify"), True),
                            download=qtini.to_bool(m.get("download"), False),
                            path=str(m.get("pathOverride") or ""), filename=str(m.get("filenameOverride") or ""),
                            postfilter=" ".join(pf) if isinstance(pf, list) else str(pf),
                            blacklisted=qtini.to_bool(m.get("getBlacklisted"), False))
            n += 1
        rep["monitors"] = n

    if "later" in parts:
        step("kept for later")
        later = _lines(p / "viewitlater.txt")
        for t in later:
            lib.add_later(t)
        rep["later"] = len(later)

    if "words" in parts:
        step("autocomplete words")
        words = []
        for ln in _lines(p / "wordsc.txt"):
            words += ln.split()
        if words:
            lib.add_words(words)
        rep["words"] = len(words)

    if "md5" in parts:
        step("MD5 list")
        pairs = []
        if (p / "md5s.sqlite").is_file():
            try:
                con = sqlite3.connect(f"file:{p / 'md5s.sqlite'}?mode=ro", uri=True)
                try:
                    pairs = [(m, pa) for m, pa in con.execute("SELECT md5, path FROM md5s") if m and pa]
                finally:
                    con.close()
            except sqlite3.Error as e:
                rep["notes"].append(f"md5s.sqlite could not be read: {e}")
        if not pairs:
            for ln in _lines(p / "md5s.txt"):
                if len(ln) > 32:
                    pairs.append((ln[:32], ln[32:].strip()))
        rep["md5"] = lib.md5_import(pairs) if pairs else 0

    if "downloads" in parts:
        step("download list")
        n, notes = load_igl(p / "restore.igl", lib, store)
        rep["downloads"] = n
        rep["notes"] += notes
    return rep



def load_igl(path, lib, store) -> tuple[int, list[str]]:
    """Add the items of a Grabber download list file (.igl, restore.igl) to Packrat's download list.
    Items already in the list are skipped. Returns (added, notes)."""
    dl = _read_json(Path(path))
    notes: list[str] = []
    existing = {(it["data"].get("search"), tuple(it["data"].get("sites") or ())) for it in lib.batch_items()
                if it["kind"] == "group"}
    n = 0
    for g in dl.get("batchs") or []:
        if not isinstance(g, dict):
            continue
        q = g.get("query") if isinstance(g.get("query"), dict) else g
        search = _query_tags(q)
        site = str(g.get("site") or "")
        if store.get(site) is None:
            notes.append(f"download '{search}': site {site} not available")
            continue
        if (search, (site,)) in existing:
            continue
        pf = g.get("postFiltering") or []
        data = {"search": search, "sites": [site], "page": max(1, qtini.to_int(g.get("page"), 1)),
                "per_page": max(1, qtini.to_int(g.get("perpage"), 60)),
                "limit": max(0, qtini.to_int(g.get("total"), 0)),
                "filename": str(g.get("filename") or "").replace("\\n", "\n"), "path": str(g.get("path") or ""),
                "postfilter": " ".join(pf) if isinstance(pf, list) else str(pf),
                "get_blacklisted": qtini.to_bool(g.get("getBlacklisted"), False)}
        lib.batch_add("group", data)
        n += 1
    have = {(it["data"]["image"].get("site_key"), str(it["data"]["image"].get("id")), it["data"]["image"].get("md5"))
            for it in lib.batch_items() if it["kind"] == "unique" and isinstance(it["data"].get("image"), dict)}
    for u in dl.get("uniques") or []:
        img = _image_from_grabber(u.get("image") or u, store) if isinstance(u, dict) else None
        if img is None or (img.site_key, str(img.id), img.md5) in have:
            continue
        have.add((img.site_key, str(img.id), img.md5))
        lib.batch_add("unique", {"image": img.to_json(), "path": str(u.get("path") or ""),
                                 "filename": str(u.get("filename") or "").replace("\\n", "\n")})
        n += 1
    return n, notes

def _image_from_grabber(d: dict, store) -> Image | None:
    host = str(d.get("website") or d.get("site") or "")
    site = store.get(host)
    if site is None:
        return None
    img = Image(site.host)
    img.id = qtini.to_int(d.get("id"), 0)
    img.md5 = str(d.get("md5") or "")
    sizes = d.get("sizes") or {}
    img.file_url = str(d.get("url") or d.get("file_url") or (sizes.get("full") or {}).get("url") or "")
    img.sample_url = str((sizes.get("sample") or {}).get("url") or "")
    img.preview_url = str((sizes.get("thumbnail") or {}).get("url") or "")
    img.name = str(d.get("name") or "")
    tags = []
    for t in d.get("tags") or []:
        if isinstance(t, str):
            tags.append(Tag(t))
        elif isinstance(t, dict) and (t.get("text") or t.get("name")):
            tags.append(Tag(str(t.get("text") or t.get("name")), str(t.get("type") or ""),
                            qtini.to_int(t.get("count"), 0), qtini.to_int(t.get("id"), 0)))
    img.tags = tags
    data = d.get("data") or {}
    if isinstance(data, dict):
        img.data.update({k: v for k, v in data.items() if isinstance(v, (str, int, float, bool))})
        if data.get("ext"):
            img.ext = str(data["ext"])
    if not img.ext and img.file_url:
        from .image import extension_of
        img.ext = extension_of(img.file_url)
    img.page_url = site.page_url_for(img) if hasattr(site, "page_url_for") else ""
    return img if img.file_url or img.id else None


def _safe(name: str) -> str:
    for c in '\\/:*?"<>|':
        name = name.replace(c, "")
    return name
