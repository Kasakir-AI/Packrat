"""Windows shell integration without extra packages: Desktop / Start menu shortcuts with Packrat's icon."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import paths

ICON = Path(__file__).resolve().parent / "assets" / "packrat.ico"


def launch_command() -> tuple[str, str]:
    """(program, arguments) that open Packrat's window without a console."""
    if getattr(sys, "frozen", False):
        return sys.executable, ""
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    return str(pyw if pyw.exists() else exe), "-m packrat gui"


def _ps_quote(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def shortcut_script(link: str, target: str, args: str, workdir: str, icon: str) -> str:
    """PowerShell that writes one .lnk (a pure function, so a test can read it)."""
    return "\n".join([
        "$ErrorActionPreference = 'Stop'",
        "$sh = New-Object -ComObject WScript.Shell",
        f"$lnk = $sh.CreateShortcut({_ps_quote(link)})",
        f"$lnk.TargetPath = {_ps_quote(target)}",
        f"$lnk.Arguments = {_ps_quote(args)}",
        f"$lnk.WorkingDirectory = {_ps_quote(workdir)}",
        f"$lnk.IconLocation = {_ps_quote(icon + ',0')}",
        "$lnk.Description = 'Packrat - find, download and organize pictures'",
        "$lnk.Save()",
    ])


def create_shortcuts(desktop: bool = True, start_menu: bool = True) -> list[str]:
    """Create 'Packrat.lnk' on the Desktop and / or in the Start menu. Returns the files written."""
    if os.name != "nt":
        return []
    target, args = launch_command()
    icon = target if getattr(sys, "frozen", False) else str(ICON)
    workdir = str(paths.install_root())
    places = []
    if desktop:
        places.append("[Environment]::GetFolderPath('Desktop')")
    if start_menu:
        places.append("[Environment]::GetFolderPath('Programs')")
    made = []
    for place in places:
        script = (f"$dir = {place}\n$link = Join-Path $dir 'Packrat.lnk'\n"
                  + shortcut_script("PLACEHOLDER", target, args, workdir, icon).replace("'PLACEHOLDER'", "$link")
                  + "\nWrite-Output $link")
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "-"], input=script,
                           capture_output=True, text=True, timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip()[:400] or "PowerShell could not create the shortcut")
        made += [ln.strip() for ln in r.stdout.splitlines() if ln.strip().lower().endswith(".lnk")]
    return made
