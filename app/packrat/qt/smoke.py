"""A self-test of the window, used by the build machine (`packrat gui --smoke FOLDER`): open the window, visit
every page, play a short video, take screenshots, write smoke.json, and exit 0 (1 when something failed)."""
from __future__ import annotations

import json
import os
import time
import traceback

from PySide6 import QtCore, QtWidgets


def run(win, qapp, outdir: str) -> None:
    os.makedirs(outdir, exist_ok=True)
    report: dict = {"ok": False, "steps": [], "problems": []}

    def pump(sec: float):
        end = time.time() + sec
        while time.time() < end:
            qapp.processEvents()
            time.sleep(0.02)

    def shot(name: str, w=None):
        pump(0.4)
        (w or win).grab().save(os.path.join(outdir, name + ".png"))
        report["steps"].append(name)

    def step(name, fn):
        try:
            fn()
        except Exception:  # noqa: BLE001
            report["problems"].append(f"{name}: {traceback.format_exc(limit=4)}")

    def pages():
        for key in win.pages:
            win.show_page(key)
            shot("page-" + key)
        win.set_theme("light")
        win.show_page("browse")
        shot("light-browse")
        win.set_theme("dark")

    def search_colours():
        win.show_page("browse")
        t = win.browse.current()
        win.tag_types.update({"pikachu": "character", "pokemon": "copyright", "some_artist": "artist"})
        text = "some_artist pikachu pokemon rating:safe landscape"
        t.query.setText(text)
        t.query.setCursorPosition(4)
        t.colors.apply()
        pump(0.3)
        if t.query.text() != text or t.query.cursorPosition() != 4:
            report["problems"].append("colouring the search box changed its text or cursor")
        shot("search-colours")
        t.query.clear()

    def dialogs():
        from .browse import GroupDialog
        from .help import SetupWizard
        from .sites import SiteOptions, SitePicker
        d = SitePicker(win, win)
        d.show()
        shot("sites", d)
        site = win.store.all_sites()[0]
        so = SiteOptions(d, win, site)
        so.show()
        shot("site-options", so)
        so.close()
        d.close()
        g = GroupDialog(win, win, "landscape", [site.host])
        g.show()
        shot("download-all", g)
        g.close()
        w = SetupWizard(win, first=True)
        w.show()
        shot("setup", w)
        w.close()
        win.open_help("start")
        shot("help", win.help_win)
        win.help_win.close()

    def video():
        path = os.environ.get("PACKRAT_SMOKE_VIDEO")
        if not path or not os.path.isfile(path):
            report["video"] = "skipped (no sample file)"
            return
        from .viewer import VideoPane
        vp = VideoPane(win.st)
        vp.resize(640, 420)
        vp.show()
        vp.play(path)
        pump(4.0)
        p = vp.player
        report["video"] = {"position_ms": p.position(), "duration_ms": p.duration(), "error": p.errorString(),
                           "has_video": p.hasVideo(), "sound_device": vp.audio is not None}
        shot("video", vp)
        vp.stop()
        vp.close()
        v = report["video"]
        if v["duration_ms"] <= 0 or v["position_ms"] <= 0 or v["error"] or not v["has_video"]:
            report["problems"].append(f"video did not play: {v}")

    def browser():
        try:
            from PySide6 import QtWebEngineCore, QtWebEngineWidgets  # noqa: F401
            report["login_browser"] = "available"
        except Exception as e:  # noqa: BLE001
            report["login_browser"] = f"missing: {e}"
            report["problems"].append("the built-in login browser is not available")

    def identity():
        report["tray_available"] = QtWidgets.QSystemTrayIcon.isSystemTrayAvailable()
        report["tray_shown"] = bool(win.tray is not None and win.tray.isVisible())
        report["window_icon_sizes"] = [f"{s.width()}x{s.height()}" for s in win.windowIcon().availableSizes()]
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            buf = wintypes.LPWSTR()
            hr = ctypes.windll.shell32.GetCurrentProcessExplicitAppUserModelID(ctypes.byref(buf))
            report["app_user_model_id"] = buf.value if hr == 0 else f"error {hr:#x}"
            if hr != 0 or buf.value != "Kasakir.Packrat":
                report["problems"].append("the taskbar identity was not set")
        scr = qapp.primaryScreen()
        if scr is not None:
            win.bring_up()
            pump(1.0)
            scr.grabWindow(0).save(os.path.join(outdir, "whole-screen.png"))

    def finish():
        for name, fn in (("pages", pages), ("search colours", search_colours), ("dialogs", dialogs), ("video", video), ("browser", browser),
                         ("identity", identity)):
            step(name, fn)
        report["ok"] = not report["problems"]
        with open(os.path.join(outdir, "smoke.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, indent=1)
        win.quitting = True
        win._shutdown()
        qapp.exit(0 if report["ok"] else 1)
    QtCore.QTimer.singleShot(1500, finish)
