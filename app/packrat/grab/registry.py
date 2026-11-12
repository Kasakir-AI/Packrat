"""Source registries (Grabber's "Sources > Registries"): a JSON list of extra sources, each a zip with a
compiled model.js, installed into data/sources/<Name>/ next to the built-in ones.

Registry JSON:  {"name", "home", "url", "sources": [{"slug", "name", "hash", "lastCommit": {...},
                 "defaultSites": [...], "supportedSites": [...]}]}
A source's zip is <url><slug>.zip. "hash" is the SHA-256 of its model.js: Packrat checks it after the
download (a mismatch is refused) and remembers it, so "Update" only appears when there is something new.

Safety: only zips whose files stay inside the new folder are accepted (no "..\\" paths), the folder must
contain a model.js, and nothing in the built-in sources folder is ever overwritten - an installed source
with the same name as a built-in one takes precedence and can be removed to go back.
"""
from __future__ import annotations

import io
import json
import logging
import shutil
import zipfile
from pathlib import Path, PurePosixPath

import requests

from .. import paths

log = logging.getLogger("packrat.grab")
OFFICIAL = "https://github.com/Bionus/imgbrd-grabber/releases/download/sources-master/metadata.json"
NIGHTLY = "https://github.com/Bionus/imgbrd-grabber/releases/download/sources-develop/metadata.json"
# built-in sources Packrat changed (see sources/PACKRAT-CHANGES.txt): a registry copy would undo the change
PROTECTED = {"E621": "Packrat's E621 source is patched for typed tags and also serves e6ai.net / e926.net; "
                     "the registry version would remove that."}
MAX_ZIP = 20 * 1024 * 1024


class ChecksumMismatch(ValueError):
    """The zip's model.js isn't the one the registry describes (often just a registry published before
    its zips were rebuilt). The window asks before installing anyway."""


def _state_file() -> Path:
    return paths.data_dir() / "registries.json"


def load_state() -> dict:
    try:
        d = json.loads(_state_file().read_text("utf-8"))
        if isinstance(d, dict):
            d.setdefault("registries", [])
            d.setdefault("installed", {})
            return d
    except (OSError, ValueError):
        pass
    return {"registries": [], "installed": {}}


def save_state(d: dict) -> None:
    p = _state_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(d, indent=1), "utf-8")
    tmp.replace(p)


def _session():
    from .sources import proxies
    s = requests.Session()
    s.headers["User-Agent"] = "Packrat/3.0 (source registry)"
    px = proxies()
    if px:
        s.proxies.update(px)
    return s


def fetch(url: str, timeout: float = 30) -> dict:
    """Download and check a registry. Raises ValueError with a readable message."""
    url = url.strip()
    if not url.startswith(("https://", "http://")):
        raise ValueError("A registry address starts with https://")
    try:
        r = _session().get(url, timeout=timeout)
    except requests.RequestException as e:
        raise ValueError(f"could not reach the registry: {e.__class__.__name__}") from e
    if r.status_code != 200:
        raise ValueError(f"the registry answered HTTP {r.status_code}")
    try:
        d = r.json()
    except ValueError as e:
        raise ValueError("this address doesn't return a source registry (not JSON)") from e
    if not isinstance(d, dict) or not isinstance(d.get("sources"), list):
        raise ValueError("this address doesn't return a source registry (no 'sources' list)")
    base = str(d.get("url") or url.rsplit("/", 1)[0] + "/")
    if not base.endswith("/"):
        base += "/"
    d["url"] = base
    d["registry_url"] = url
    return d


def add_registry(url: str) -> dict:
    d = fetch(url)
    st = load_state()
    if url not in st["registries"]:
        st["registries"].append(url)
        save_state(st)
    return d


def remove_registry(url: str) -> None:
    st = load_state()
    st["registries"] = [u for u in st["registries"] if u != url]
    save_state(st)


def available(registries: list[dict], installed: dict | None = None) -> list[dict]:
    """Every source of the given (fetched) registries, with its install status."""
    installed = installed if installed is not None else load_state()["installed"]
    out = []
    for reg in registries:
        for s in reg.get("sources") or []:
            if not isinstance(s, dict) or not s.get("slug"):
                continue
            inst = installed.get(s.get("name") or s["slug"])
            status = "not installed"
            if inst:
                status = "installed" if inst.get("hash") == s.get("hash") else "update available"
            name = s.get("name") or s["slug"]
            if name in PROTECTED:
                status = "built in (kept)"
            out.append({"registry": reg.get("name") or reg.get("registry_url"), "registry_url": reg["registry_url"],
                        "zip": reg["url"] + s["slug"] + ".zip", "slug": s["slug"], "name": s.get("name") or s["slug"],
                        "hash": s.get("hash", ""), "commit": (s.get("lastCommit") or {}).get("subject", ""),
                        "date": (s.get("lastCommit") or {}).get("date", ""),
                        "default_sites": list(s.get("defaultSites") or []),
                        "supported_sites": list(s.get("supportedSites") or []), "status": status})
    return out


def find_for_host(registries: list[dict], host: str) -> dict | None:
    """'Add a site' guessing: a registry source that supports this host."""
    for s in available(registries):
        if host in s["supported_sites"] or host in s["default_sites"]:
            return s
    return None


def install(entry: dict, accept_mismatch: bool = False) -> Path:
    """Download entry["zip"] and install it. Returns the installed folder."""
    if (entry.get("name") or entry.get("slug")) in PROTECTED:
        raise ValueError(PROTECTED[entry.get("name") or entry.get("slug")])
    try:
        r = _session().get(entry["zip"], timeout=60, stream=True)
        if r.status_code != 200:
            raise ValueError(f"download failed: HTTP {r.status_code}")
        buf = io.BytesIO()
        for chunk in r.iter_content(65536):
            buf.write(chunk)
            if buf.tell() > MAX_ZIP:
                raise ValueError("the source zip is unexpectedly large; not installed")
    except requests.RequestException as e:
        raise ValueError(f"download failed: {e.__class__.__name__}") from e
    return install_zip(buf.getvalue(), entry.get("name") or entry["slug"], entry.get("hash", ""),
                       entry.get("registry_url", ""), accept_mismatch)


def install_zip(data: bytes, name: str = "", hash_: str = "", registry_url: str = "",
                accept_mismatch: bool = False) -> Path:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ValueError("the file is not a valid zip") from e
    names = [n for n in zf.namelist() if not n.endswith("/")]
    for n in names:
        pp = PurePosixPath(n.replace("\\", "/"))
        if pp.is_absolute() or ".." in pp.parts or (pp.parts and ":" in pp.parts[0]):
            raise ValueError(f"unsafe path in the zip: {n}")
    # the source folder is either the zip root (model.js at top) or its single top folder
    tops = {PurePosixPath(n.replace("\\", "/")).parts[0] for n in names}
    if any(PurePosixPath(n.replace("\\", "/")).name == "model.js" and len(PurePosixPath(n).parts) == 1 for n in names):
        prefix, folder = "", (name or "Imported source")
    elif len(tops) == 1:
        prefix = next(iter(tops)) + "/"
        folder = name or next(iter(tops))
        if prefix + "model.js" not in [n.replace("\\", "/") for n in names]:
            raise ValueError("no model.js in the zip: not a Grabber source")
    else:
        raise ValueError("no model.js in the zip: not a Grabber source")
    folder = "".join(c for c in folder if c not in '\\/:*?"<>|').strip().strip(".") or "Imported source"
    dest = paths.user_sources_dir() / folder
    tmp = dest.with_name(dest.name + ".installing")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        for n in names:
            rel = n.replace("\\", "/")[len(prefix):]
            if not rel:
                continue
            target = tmp / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(n) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
        if not (tmp / "model.js").is_file():
            raise ValueError("no model.js in the zip: not a Grabber source")
        if hash_:
            import hashlib
            got = hashlib.sha256((tmp / "model.js").read_bytes()).hexdigest()
            if got.lower() != hash_.lower() and not accept_mismatch:
                raise ChecksumMismatch("The downloaded source doesn't match the registry's checksum. This usually "
                                       "means the registry is slightly out of date, but it can also mean the file "
                                       "was changed. Not installed.")
            hash_ = hash_ if got.lower() == hash_.lower() else got
        # check that it loads before replacing anything
        from .jsrt import SourceRuntime
        helper = paths.bundled_sources_dir() / "helper.js"
        rt = SourceRuntime(tmp / "model.js", helper, folder)
        if not rt.meta.get("apis"):
            raise ValueError("the source loads but declares no API")
        if dest.exists():
            shutil.rmtree(dest)
        tmp.replace(dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    st = load_state()
    st["installed"][folder] = {"hash": hash_, "registry": registry_url}
    save_state(st)
    return dest


def uninstall(folder_name: str) -> bool:
    if not folder_name.strip(". ") or any(c in folder_name for c in "/\\"):
        return False   # never the sources folder itself or one outside it
    d = paths.user_sources_dir() / folder_name
    ok = False
    if d.is_dir():
        shutil.rmtree(d)
        ok = True
    st = load_state()
    st["installed"].pop(folder_name, None)
    save_state(st)
    return ok
