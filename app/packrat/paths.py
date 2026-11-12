"""3.0: where Packrat keeps its files.

The install folder is the one with "Start Packrat.bat" (the program itself is in its app\\ subfolder).
Like e6ai-archiver 1.x/2.0, everything personal lives next to the .bat files, so the whole folder can be
moved or copied to another PC ("portable"):

    packrat.ini        settings (the whole-site archive's [packrat] + [gui] sections)
    archive\\          default whole-site archive folder
    downloads\\        default folder for files saved from Browse / Downloads
    data\\             browse settings (settings.json), library.sqlite3 (MD5 list, favorites, monitors,
                       kept for later, history, download lists), user sources, thumbnails cache, logs
    .venv\\            Packrat's private Python packages

PACKRAT_HOME (environment) overrides the install folder (tests, several profiles).
The Mac app (Packrat.app) keeps these in ~/Library/Application Support/Packrat instead, and saves pictures
to ~/Pictures/Packrat.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
APP_DIR = PACKAGE_DIR.parent
INI_NAME = "packrat.ini"
LEGACY_INI_NAME = "e6ai-archiver.ini"


def install_root() -> Path:
    env = os.environ.get("PACKRAT_HOME")
    if env:
        return Path(env).expanduser().resolve()
    if getattr(sys, "frozen", False):
        if sys.platform == "darwin":       # Packrat.app: the app itself is read-only; personal files go to Library
            home = Path.home() / "Library" / "Application Support" / "Packrat"
            home.mkdir(parents=True, exist_ok=True)
            return home
        return Path(sys.executable).resolve().parent   # Packrat.exe: everything personal lives next to the .exe
    # zip layout: <install>/app/packrat/ ; git checkout: <repo>/app/packrat/ as well
    if APP_DIR.name.lower() == "app":
        return APP_DIR.parent
    return APP_DIR


def data_dir() -> Path:
    d = install_root() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def settings_ini() -> Path:
    return install_root() / INI_NAME


def legacy_ini_candidates() -> list[Path]:
    """Settings files of e6ai-archiver 2.0 unzipped into this same folder (read until packrat.ini exists)."""
    root = install_root()
    return [root / LEGACY_INI_NAME, APP_DIR / LEGACY_INI_NAME]


def default_downloads() -> Path:
    if getattr(sys, "frozen", False) and sys.platform == "darwin" and not os.environ.get("PACKRAT_HOME"):
        return Path.home() / "Pictures" / "Packrat"       # where Mac users look for pictures (Finder > Pictures)
    return install_root() / "downloads"


def user_sources_dir() -> Path:
    d = data_dir() / "sources"
    d.mkdir(parents=True, exist_ok=True)
    return d


def bundled_sources_dir() -> Path:
    return PACKAGE_DIR / "sources"


def cache_dir(kind: str = "") -> Path:
    d = data_dir() / "cache" / kind if kind else data_dir() / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def logs_dir() -> Path:
    d = data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d
