"""Developer tool: build the Packrat download zip (the one that runs with an installed Python).

    python app/tools/build_dist.py [OUTPUT.zip]

The zip has one top folder, "Packrat": START HERE - README.txt, the .bat files, app/ (the program, its tests and
tools) and docs/. Personal folders (.venv, data, downloads, archive) and caches are never included.
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
ROOT = APP.parent
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", "build", "dist", ".venv", "data", "downloads", "archive",
             ".git", ".github", "node_modules"}
SKIP_SUFFIX = (".pyc", ".pyo", ".tmp")
TOP_FILES = ("START HERE - README.txt", "Start Packrat.bat", "Start Packrat (classic window).bat", "Check Python.bat")


def files(base: Path):
    for p in sorted(base.rglob("*")):
        rel = p.relative_to(base).parts
        if p.is_dir() or any(part in SKIP_DIRS or part.endswith(".egg-info") for part in rel):
            continue
        if p.name.endswith(SKIP_SUFFIX):
            continue
        yield p


def add(z: zipfile.ZipFile, p: Path, name: str) -> None:
    """Windows scripts always go in with Windows line endings, whatever the checkout used."""
    if p.suffix.lower() in (".bat", ".ps1"):
        z.writestr(name, p.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    else:
        z.write(p, name)


def version() -> str:
    text = (APP / "packrat" / "__init__.py").read_text("utf-8")
    return text.split('__version__ = "', 1)[1].split('"', 1)[0]


def main(argv) -> int:
    out = Path(argv[0]) if argv else ROOT / "dist" / f"Packrat-{version()}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name in TOP_FILES:
            if (ROOT / name).is_file():
                add(z, ROOT / name, f"Packrat/{name}")
                n += 1
        for sub in ("app", "docs"):
            for p in files(ROOT / sub):
                add(z, p, f"Packrat/{sub}/{p.relative_to(ROOT / sub).as_posix()}")
                n += 1
    print(f"{out}  ({n} files, {out.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
