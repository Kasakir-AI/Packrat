"""1.9: desktop notification when a run ends (or pauses for disk space). No extra packages.

Windows: a toast via PowerShell + the WinRT notification API (shown as from "Windows PowerShell"), falling
back inside the same script to a classic tray balloon (System.Windows.Forms.NotifyIcon). Linux:
notify-send if installed; macOS: osascript. Returns the method used, or None when nothing is available -
the control panel then shows its own small pop-up instead. Never raises, never blocks the window
(the helper process runs detached; PowerShell gets the script on stdin, so no text is ever parsed as code:
title and message are passed as XML-escaped / single-quote-escaped literals).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from xml.sax.saxutils import escape

APP_ID = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe"


def _ps_quote(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def windows_script(title: str, message: str) -> str:
    """PowerShell: try a toast, else a tray balloon (6 s). Pure function so tests can inspect it."""
    xml = (f"<toast><visual><binding template='ToastGeneric'><text>{escape(title)}</text>"
           f"<text>{escape(message)}</text></binding></visual></toast>")
    return "\n".join([
        "$ErrorActionPreference = 'Stop'",
        f"$title = {_ps_quote(title)}",
        f"$msg = {_ps_quote(message)}",
        "try {",
        "  [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null",
        "  [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null",
        "  $doc = New-Object Windows.Data.Xml.Dom.XmlDocument",
        f"  $doc.LoadXml({_ps_quote(xml)})",
        "  $toast = New-Object Windows.UI.Notifications.ToastNotification $doc",
        f"  [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier({_ps_quote(APP_ID)}).Show($toast)",
        "} catch {",
        "  Add-Type -AssemblyName System.Windows.Forms",
        "  Add-Type -AssemblyName System.Drawing",
        "  $n = New-Object System.Windows.Forms.NotifyIcon",
        "  $n.Icon = [System.Drawing.SystemIcons]::Information",
        "  $n.BalloonTipTitle = $title",
        "  $n.BalloonTipText = $msg",
        "  $n.Visible = $true",
        "  $n.ShowBalloonTip(6000)",
        "  Start-Sleep -Seconds 7",
        "  $n.Dispose()",
        "}",
    ])


def _clean(s: str, n: int) -> str:
    s = " ".join(str(s or "").replace("\r", " ").split("\n")[0:6]).strip()
    return s[:n]


def notify(title: str, message: str, *, urgent: bool = False, platform: str | None = None,
           which=shutil.which, popen=subprocess.Popen) -> str | None:
    platform = platform or sys.platform
    title, message = _clean(title, 120), _clean(message, 380)
    try:
        if platform.startswith("win"):
            exe = which("powershell") or which("powershell.exe")
            if not exe:
                return None
            kw = {}
            if os.name == "nt":
                kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
            p = popen([exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", "-"],
                      stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                      text=True, encoding="utf-8", **kw)
            try:
                p.stdin.write(windows_script(title, message))
                p.stdin.close()
            except (OSError, ValueError, AttributeError):
                pass
            return "windows-toast"
        if platform == "darwin":
            exe = which("osascript")
            if not exe:
                return None
            esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')  # noqa: E731
            popen([exe, "-e", f'display notification "{esc(message)}" with title "{esc(title)}"'],
                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return "osascript"
        exe = which("notify-send")
        if not exe or not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            return None
        popen([exe, "-a", "Packrat", "-u", "critical" if urgent else "normal", "--", title, message],
              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "notify-send"
    except (OSError, ValueError):
        return None
