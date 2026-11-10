"""Developer tool: compile Grabber's source models (TypeScript) into app/packrat/sources/.

Needs Node.js + npm (only to build; Packrat itself runs the compiled JavaScript with QuickJS via dukpy).

    python tools/build_sources.py PATH/TO/imgbrd-grabber/src/sites

Grabber is Apache-2.0 licensed; its LICENSE and NOTICE are
copied next to the compiled models. Packrat additions (e6ai.net / e926.net as sites of the E621 source,
descriptive User-Agents for the e621 family) are written by this script and listed in PACKRAT-CHANGES.txt.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_APP = HERE.parent / "app" if (HERE.parent / "app" / "packrat").is_dir() else HERE.parent   # tree or zip layout
DEST = _APP / "packrat" / "sources"
TSCONFIG = {
    "compilerOptions": {"declaration": False, "module": "commonjs", "target": "es2017", "strict": True,
                        "noImplicitReturns": True, "types": [], "outDir": "__out__", "skipLibCheck": True,
                        "lib": ["es2019", "dom"]},
    "include": ["**/*.ts"],
    "exclude": ["node_modules", "test-*.ts", "**/*.spec.ts", "**/downloader.ts", "__out__"],
}
E621_UA = "Packrat/%version% (by %username% on %host%)"
E621_TYPES = "0,general\n1,artist\n2,contributor\n3,copyright\n4,character\n5,species\n6,invalid\n7,meta\n8,lore\n"
sys.path.insert(0, str(HERE))
from model_patches import PATCHES  # noqa: E402

EXTRA_SITES = {  # Packrat additions to Grabber's defaults
    "E621": {
        "e6ai.net": {"name": "e6ai", "types": E621_TYPES},
        "e926.net": {"name": "e926", "types": E621_TYPES},
    },
}


def main(src: str) -> int:
    src = Path(src).resolve()
    work = HERE / "_build_sites"
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(src, work, ignore=shutil.ignore_patterns("node_modules", "resources"))
    (work / "tsconfig.packrat.json").write_text(json.dumps(TSCONFIG, indent=1), "utf-8")
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    npx = "npx.cmd" if sys.platform == "win32" else "npx"
    subprocess.run([npm, "install", "--no-audit", "--no-fund", "typescript@5.9.3"], cwd=work, check=True)
    subprocess.run([npx, "tsc", "-p", "tsconfig.packrat.json"], cwd=work, check=True)
    out = work / "__out__"
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)
    shutil.copy2(out / "helper.js", DEST / "helper.js")
    count = 0
    for d in sorted(p for p in work.iterdir() if p.is_dir() and (p / "model.ts").is_file()):
        js = out / d.name / "model.js"
        if not js.is_file():
            continue
        t = DEST / d.name
        t.mkdir()
        shutil.copy2(js, t / "model.js")
        for f in ("icon.png", "sites.txt", "supported.txt"):
            if (d / f).is_file():
                shutil.copy2(d / f, t / f)
        for site in (p for p in d.iterdir() if p.is_dir() and "." in p.name):
            shutil.copytree(site, t / site.name)
        count += 1
    # --- Packrat additions -------------------------------------------------------------------
    changes = []
    for source, reps in PATCHES.items():
        js = DEST / source / "model.js"
        text = js.read_text("utf-8")
        for old, new in reps:
            if text.count(old) != 1:
                raise SystemExit(f"patch for {source} no longer matches the Grabber model: {old[:60]!r}")
            text = text.replace(old, new)
        js.write_text(text, "utf-8")
        changes.append(f"{source}: model patched ({len(reps)} changes, see tools/model_patches.py)")
    for source, sites in EXTRA_SITES.items():
        sdir = DEST / source
        listed = (sdir / "sites.txt").read_text("utf-8").split() if (sdir / "sites.txt").is_file() else []
        for host, info in sites.items():
            h = sdir / host
            h.mkdir(exist_ok=True)
            (h / "defaults.ini").write_text(
                f'[General]\nname={info["name"]}\nssl=true\n\n[download]\nimagesperpage=320\n\n'
                f'[Headers]\nUser-Agent="{E621_UA}"\n', "utf-8")
            (h / "tag-types.txt").write_text(info["types"], "utf-8")
            if host not in listed:
                listed.append(host)
            changes.append(f"{source}: added site {host} ({info['name']})")
        (sdir / "sites.txt").write_text("\n".join(listed) + "\n", "utf-8")
    e621 = DEST / "E621" / "e621.net" / "defaults.ini"
    if e621.is_file():
        txt = e621.read_text("utf-8")
        import re
        txt = re.sub(r'User-Agent="[^"]*"', f'User-Agent="{E621_UA}"', txt)
        e621.write_text(txt, "utf-8")
        changes.append("E621/e621.net: descriptive User-Agent (e621's API rules forbid browser-like ones)")
    for f in ("LICENSE", "NOTICE"):
        p = src.parent.parent / f
        if p.is_file():
            shutil.copy2(p, DEST / f"GRABBER-{f}.txt")
    rev = subprocess.run(["git", "-C", str(src), "log", "-1", "--format=%H %cd"], capture_output=True,
                         text=True).stdout.strip()
    (DEST / "PACKRAT-CHANGES.txt").write_text(
        "Compiled from Grabber's src/sites (Apache-2.0) by tools/build_sources.py.\n"
        f"Grabber revision: {rev or 'unknown'}\n\nPackrat changes:\n" + "\n".join("- " + c for c in changes) + "\n",
        "utf-8")
    shutil.rmtree(work)
    print(f"{count} source models -> {DEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
