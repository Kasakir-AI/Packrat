"""Packrat's main window (Qt).

A sidebar on the left picks the page: Browse, Downloads, Favorites, Monitors, Site archive, Settings, Tools, Log.
Everything slow (searches, thumbnails, logins, saving) runs in background threads and comes back through
common.Worker; the download list and the site archive run in separate processes, so a long download never makes
the window stutter and Stop always works.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time

from PySide6 import QtCore, QtGui, QtNetwork, QtWidgets

from .. import __version__, paths
from . import common as K
from .common import Qt

log = logging.getLogger("packrat")
APP_ID = "Kasakir.Packrat"          # Windows groups taskbar buttons by this, instead of "python.exe"
NAV = (("browse", "Browse", "search"), ("downloads", "Downloads", "download"), ("favorites", "Favorites", "star"),
       ("monitors", "Monitors", "bell"), ("archive", "Site archive", "box"), ("settings", "Settings", "sliders"),
       ("tools", "Tools", "wrench"), ("log", "Log", "list"))


def set_windows_identity() -> None:
    """Tell Windows this process is 'Packrat' (own taskbar button, own icon, own notifications), not Python."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:  # noqa: BLE001 - cosmetic only
        log.debug("could not set the taskbar identity", exc_info=True)


class UiLogHandler(logging.Handler):
    def __init__(self, win):
        super().__init__(logging.INFO)
        self.win = win
        from ..redact import RedactingFormatter
        self.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))

    def emit(self, record):
        try:
            msg = self.format(record)
        except Exception:  # noqa: BLE001
            return
        self.win.worker.call(self.win.log_line, msg, record.levelno)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, qapp: QtWidgets.QApplication):
        super().__init__()
        from ..grab import library, settings, sources
        self.qapp = qapp
        self.st = settings.shared()
        self.lib = library.shared()
        self.store = sources.store()
        self.worker = K.Worker(self)
        self.thumbs = K.Thumbs(self.worker, int(self.st.get("thumbnailSize") or 180),
                               not bool(self.st.get("resizeInsteadOfCropping", True)))
        self.viewers: list = []
        self.tag_types: dict[str, str] = {}      # tag -> kind, learnt from results; colours the search box
        self.help_win = None
        self.tray = None
        self.quitting = False
        self._saver = None
        self._save_lock = threading.Lock()
        self.setWindowTitle("Packrat")
        self.setWindowIcon(K.app_icon())
        self.setMinimumSize(860, 520)

        # ---- sidebar -----------------------------------------------------------------------------
        logo = QtWidgets.QLabel()
        logo.setPixmap(K.app_icon().pixmap(38, 38))
        name = K.label("Packrat", role="wordmark")
        self.nav: dict[str, QtWidgets.QToolButton] = {}
        group = QtWidgets.QButtonGroup(self)
        side = K.vbox(K.hbox(logo, name, None, margins=(6, 4, 6, 14), spacing=10), margins=(10, 14, 10, 10), spacing=4)
        for key, text, ico in NAV:
            b = QtWidgets.QToolButton()
            b.setObjectName("nav")
            b.setText("  " + text)
            b.setProperty("navico", ico)
            b.setProperty("hue", K.PAGE_HUE[key])      # every page has its own pastel (common.PAGE_HUE)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
            b.clicked.connect(lambda _=False, k=key: self.show_page(k))
            group.addButton(b)
            side.addWidget(b)
            self.nav[key] = b
        side.addStretch(1)
        self.badge = K.label("", muted=True, wrap=True)
        side.addWidget(self.badge)
        self.sites_btn = K.button("Sites and logins", lambda: self.open_sites(), flat=True, ico="globe",
                                  tip="Choose the sites to search, log in to them, add a site")
        self.help_btn = K.button("Help", lambda: self.open_help(self._page_topic()), flat=True, ico="help",
                                 tip="How to use Packrat (F1)")
        self.theme_btn = K.button("", self.toggle_theme, flat=True, ico="moon", tip="Light / dark (Ctrl+Shift+D)")
        for b in (self.sites_btn, self.help_btn):
            side.addWidget(b)
        side.addLayout(K.hbox(K.label(f"v{__version__}", muted=True), None, self.theme_btn, margins=(8, 4, 0, 0)))
        sidebar = K.widget(side, "sidebar")
        sidebar.setFixedWidth(200)

        # ---- pages -------------------------------------------------------------------------------
        self.stack = QtWidgets.QStackedWidget()
        central = QtWidgets.QWidget()
        central.setLayout(K.hbox(sidebar, (self.stack, 1), spacing=0))
        self.setCentralWidget(central)
        self.status = self.statusBar()
        self.status.setSizeGripEnabled(False)
        self.busy_lbl = K.label("", muted=True)
        self.status.addPermanentWidget(self.busy_lbl)

        from .pages import DownloadsPage, FavoritesPage, LogPage, MonitorsPage
        self.logpage = LogPage(self)
        self._handler = UiLogHandler(self)
        logging.getLogger("packrat").addHandler(self._handler)
        logging.getLogger("packrat").setLevel(logging.INFO)
        from ..grab import login as _login
        _login.ASK = self._ask_from_thread
        from .archive import ArchivePage
        from .browse import BrowsePage
        from .settings import SettingsPage
        from .tools import ToolsPage
        self.browse = BrowsePage(self)
        self.downloads = DownloadsPage(self)
        self.favorites = FavoritesPage(self)
        self.monitors = MonitorsPage(self)
        self.archive = ArchivePage(self)
        self.settings_page = SettingsPage(self)
        self.tools = ToolsPage(self)
        self.pages = {"browse": self.browse, "downloads": self.downloads, "favorites": self.favorites,
                      "monitors": self.monitors, "archive": self.archive, "settings": self.settings_page,
                      "tools": self.tools, "log": self.logpage}
        for key, _t, _i in NAV:
            self.pages[key].setProperty("hue", K.PAGE_HUE[key])
            self.stack.addWidget(self.pages[key])
        self._restyle_nav()

        for seq, fn in (("Ctrl+T", lambda: self.new_search_tab(focus=True)), ("Ctrl+W", lambda: self.browse.close_current()),
                        ("F5", lambda: self.browse.reload_current()), ("F1", lambda: self.open_help(self._page_topic())),
                        ("Ctrl+Shift+D", self.toggle_theme), ("Alt+Left", lambda: self.browse.hist(-1)),
                        ("Alt+Right", lambda: self.browse.hist(1)), ("Ctrl+L", lambda: self.browse.focus_search()),
                        ("Ctrl+Q", self.quit)):
            QtGui.QShortcut(QtGui.QKeySequence(seq), self, activated=fn)
        for i, (key, _t, _i) in enumerate(NAV, 1):
            QtGui.QShortcut(QtGui.QKeySequence(f"Ctrl+{i}"), self, activated=lambda k=key: self.show_page(k))

        from ..grab.monitor import MonitorScheduler
        self.scheduler = MonitorScheduler(self.store, self.st, self.lib,
                                          lambda m, r: self.worker.call(self.monitors.result, m, r),
                                          lambda m: self.worker.call(self.monitors.checking, m))
        if self.st.get("Monitoring/enabled", True):
            delay = max(1, int(self.st.get("Monitoring/startupDelay") or 30))
            QtCore.QTimer.singleShot(delay * 1000 if self.lib.monitors() else 1000, self.scheduler.start)
        self._restore_geometry()
        self.show_page(str(self.lib.get_state("qt_page") or "browse"))
        self.browse.restore_session()
        QtCore.QTimer.singleShot(600, self._first_run)

    # ---- pages / navigation ----------------------------------------------------------------------
    def show_page(self, key: str) -> None:
        if key not in self.pages:
            key = "browse"
        self.stack.setCurrentWidget(self.pages[key])
        self.nav[key].setChecked(True)
        shown = getattr(self.pages[key], "shown", None)
        if shown:
            shown()

    def current_page(self) -> str:
        w = self.stack.currentWidget()
        return next((k for k, p in self.pages.items() if p is w), "browse")

    def _page_topic(self) -> str:
        from .. import guides
        return guides.TAB_TOPIC.get(self.current_page(), "start")

    def _restyle_nav(self) -> None:
        for b in self.nav.values():
            b.setIcon(K.nav_icon(b.property("navico"), b.property("hue")))
            b.setIconSize(QtCore.QSize(28, 28))
        self.theme_btn.setProperty("ico", "sun" if K.DARK[0] else "moon")
        K.refresh_icons(self)

    def toggle_theme(self) -> None:
        self.set_theme("light" if K.DARK[0] else "dark")

    def set_theme(self, choice: str) -> None:
        K.apply_theme(self.qapp, choice)
        self.lib.set_state("qt_theme", choice)
        self._restyle_nav()
        for p in self.pages.values():
            fn = getattr(p, "themed", None)
            if fn:
                fn()
        for v in list(self.viewers):
            v.themed()

    def go(self, target: str) -> None:
        """Follow a link from the help: tab:NAME, settings:PAGE, sources, site:HOST, help:TOPIC, url:..., setup."""
        kind, _, rest = target.partition(":")
        if kind == "tab":
            self.show_page(rest)
        elif kind == "settings":
            self.show_page("settings")
            self.settings_page.show_named(rest)
        elif kind == "sources":
            self.open_sites()
        elif kind == "site":
            self.open_sites(focus=rest)
        elif kind == "help":
            self.open_help(rest)
        elif kind == "url":
            from ..uikit import open_url
            open_url(rest)
        elif kind == "setup":
            self.run_setup()
        elif kind == "search":
            self.new_search_tab(rest, focus=True, run=True)
            self.show_page("browse")
        elif kind == "advanced":
            self.flash("This version always shows every page and option.")
        self.raise_()
        self.activateWindow()

    # ---- messages --------------------------------------------------------------------------------
    def flash(self, text: str, ms: int = 8000) -> None:
        self.status.showMessage(text, ms)

    set_status = flash

    def busy(self, text: str = "") -> None:
        self.busy_lbl.setText(text)

    def log_line(self, text: str, level: int = logging.INFO) -> None:
        self.logpage.add(text, level)

    def notify(self, title: str, message: str) -> None:
        """A desktop notification with Packrat's own name and icon (falls back to the status line)."""
        if self.tray is not None and self.tray.supportsMessages():
            self.tray.showMessage(title, message, K.app_icon(), 8000)
        else:
            self.flash(f"{title}: {message}", 15000)

    def _ask_from_thread(self, title, message, default=""):
        """login.py asks for a PIN / pasted address from a background thread."""
        box: dict = {}
        ev = threading.Event()

        def ask():
            box["v"] = K.ask_text(self, title, message, default)
            ev.set()
        self.worker.call(ask)
        ev.wait(600)
        return box.get("v")

    # ---- things pages share ------------------------------------------------------------------------
    def new_search_tab(self, query: str = "", sites=None, focus=True, run=None):
        return self.browse.new_tab(query, sites, focus=focus, run=run)

    def saver(self):
        if self._saver is None:
            from ..grab.download import ImageSaver
            self._saver = ImageSaver(self.st, self.lib, self.store, threading.Event())
        return self._saver

    def save_images(self, items, on_each=None, path=None, filename=None):
        """Save now, in the background (the viewer's Save, 'Save now' in the results)."""
        def work():
            out = []
            saver = self.saver()
            for i, (site, img) in enumerate(items):
                with self._save_lock:
                    res = saver.save(img, site, filename, path, i + 1, download_blacklisted=True)
                out.append((img, res))
                if on_each:
                    self.worker.call(on_each, img, res)
            return out

        def done(out):
            saved = sum(1 for _, rs in out for r in rs if r.status == "saved")
            other = len(out) - saved
            self.flash(f"Saved {saved} picture(s)" + (f", {other} already there or skipped" if other else ""))
        self.worker.submit(work, on_done=done, on_error=lambda e: K.warn(self, "Save", str(e)))

    def add_to_downloads_images(self, items, path="", filename=""):
        for _site, img in items:
            self.lib.batch_add("unique", {"image": img.to_json(), "path": path, "filename": filename})
        self.downloads.refresh()
        self.flash(f"{len(items)} picture(s) added to Downloads - press Start there")

    def add_group(self, query, sites, **kw):
        from ..grab.download import Group
        per_page = int(kw.pop("per_page", 0) or self.st.get("limit") or 20)
        g = Group(search=query, sites=list(sites), per_page=max(per_page, 20), **kw)
        self.lib.batch_add("group", g.to_json())
        self.downloads.refresh()
        self.flash(f"'{query or '(everything)'}' added to Downloads - press Start there")

    def whitelisted(self, img) -> bool:
        wl = set(str(self.st.get("whitelistedtags") or "").split())
        return bool(wl) and any(t.name in wl for t in img.tags)

    def open_viewer(self, tab, index: int):
        sh = tab.items[index]
        if sh.blacklisted and self.st.get("warnblacklisted", True):
            if not K.ask(self, "Blacklisted picture", "This picture is blacklisted (" + ", ".join(sh.blacklisted)
                         + ").\n\nOpen it anyway?", default_yes=False):
                return None
        from .viewer import Viewer
        if self.st.get("Viewer/singleWindow") and self.viewers:
            v = self.viewers[-1]
            v.show_list(tab, index)
        else:
            v = Viewer(self, tab, index)
            self.viewers.append(v)
            v.show()
        v.raise_()
        v.activateWindow()
        return v

    def open_site_wizard(self, host: str = ""):
        """'Set up a site': log in to a popular site step by step (Help, Sites and logins, the Browse menu)."""
        from .wizard import SiteWizard
        SiteWizard(self, self, host).exec()

    def open_sites(self, focus: str = "", pick_for=None):
        from .sites import SitePicker
        dlg = SitePicker(self, self, focus=focus, selected=pick_for)
        dlg.exec()
        return dlg.result_hosts

    def reload_archive(self):
        """Rebuild the Site archive page from its settings file (after importing an older copy's settings)."""
        from .archive import ArchivePage
        old = self.archive
        i = self.stack.indexOf(old)
        self.archive = ArchivePage(self)
        self.archive.setProperty("hue", K.PAGE_HUE["archive"])
        self.pages["archive"] = self.archive
        self.stack.insertWidget(i, self.archive)
        self.stack.removeWidget(old)
        old.poll.stop()
        old.deleteLater()
        self.show_page("archive")

    def open_help(self, target: str = "start"):
        from .help import HelpWindow
        if self.help_win is None:
            self.help_win = HelpWindow(self)
        self.help_win.open(target)

    def run_setup(self, first: bool = False):
        from .help import SetupWizard
        SetupWizard(self, first=first).exec()
        self.settings_page.reload()
        self.browse.settings_changed()

    def _first_run(self):
        if not self.lib.get_state("setup_done"):
            self.run_setup(first=True)
            self.lib.set_state("setup_done", True)

    # ---- window state / closing ----------------------------------------------------------------------
    def _restore_geometry(self):
        g = self.lib.get_state("qt_geometry")
        ok = False
        if g:
            try:
                ok = self.restoreGeometry(QtCore.QByteArray.fromHex(str(g).encode()))
            except Exception:  # noqa: BLE001
                ok = False
        if not ok:
            scr = self.qapp.primaryScreen().availableGeometry()
            w, h = min(1380, scr.width() - 60), min(880, scr.height() - 60)
            self.setGeometry(scr.x() + (scr.width() - w) // 2, scr.y() + (scr.height() - h) // 3, w, h)

    def changeEvent(self, e):
        if (e.type() == QtCore.QEvent.Type.WindowStateChange and self.isMinimized() and self.tray is not None
                and self.st.get("Monitoring/minimizeToTray")):
            QtCore.QTimer.singleShot(0, self.hide)
        super().changeEvent(e)

    def bring_up(self):
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()
        if sys.platform == "darwin":
            _activate_mac_app()     # Qt shows the window but leaves the keyboard with the previous app

    def _app_state(self, state):
        """Mac: opening Packrat again (Spotlight, Finder, Dock) while it runs in the menu bar does not start a
        second copy, it re-activates this one. Bring the window back then, as Windows does via the local socket."""
        if (state == Qt.ApplicationState.ApplicationActive and self._reopen_armed
                and (not self.isVisible() or self.isMinimized())):
            self.bring_up()

    def closeEvent(self, e):
        if not self.quitting and self.tray is not None and self.st.get("Monitoring/closeToTray"):
            e.ignore()
            self.hide()
            if not self.lib.get_state("tray_hint_shown"):
                self.lib.set_state("tray_hint_shown", True)
                self.notify("Packrat is still running", "Its icon is next to the clock. Right-click it to quit.")
            return
        if not self._may_quit():
            e.ignore()
            self.quitting = False
            return
        self._shutdown()
        e.accept()
        self.qapp.quit()

    def quit(self):
        self.quitting = True
        self.close()

    def _may_quit(self) -> bool:
        running = [n for n, r in (("the download list", self.downloads.running()), ("the site archive", self.archive.running()))
                   if r]
        if running:
            if self.st.get("confirm_close", True) and not K.ask(
                    self, "Still working", f"Packrat is running {' and '.join(running)}.\n\nStop and close? Nothing is "
                    "lost: Start continues where it stopped.", "Stop and close", "Keep running", default_yes=False):
                return False
            self.downloads.stop()
            self.archive.stop()
            t0 = time.time()
            while (self.downloads.running() or self.archive.running()) and time.time() - t0 < 20:
                self.qapp.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 100)
            self.downloads.kill()
            self.archive.kill()
        return True

    def _shutdown(self):
        try:
            self.browse.save_session()
            self.lib.set_state("qt_geometry", bytes(self.saveGeometry().toHex()).decode())
            self.lib.set_state("qt_page", self.current_page())
        except Exception:  # noqa: BLE001
            log.debug("could not save the window state", exc_info=True)
        for v in list(self.viewers):
            v.close()
        if self.help_win is not None:
            self.help_win.close()
        self.scheduler.stop()
        logging.getLogger("packrat").removeHandler(self._handler)
        self.worker.close()
        self.thumbs.close()
        if self.tray is not None:
            self.tray.hide()


# ---- tray icon ----------------------------------------------------------------------------------------
def make_tray(win: MainWindow) -> QtWidgets.QSystemTrayIcon | None:
    if not win.st.get("Monitoring/enableTray", True) or not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
        return None
    tray = QtWidgets.QSystemTrayIcon(K.app_icon(), win)
    tray.setToolTip("Packrat")
    m = QtWidgets.QMenu()
    m.addAction("Open Packrat", win.bring_up)
    m.addSeparator()
    m.addAction("Check monitors now", lambda: (win.scheduler.start(), win.scheduler.check_now(),
                                              win.flash("Checking monitors...")))
    start = m.addAction("Start the download list", lambda: win.downloads.start())
    stop = m.addAction("Stop the download list", lambda: win.downloads.stop())
    m.addSeparator()
    m.addAction("Open the downloads folder", lambda: _open_downloads(win))
    m.addSeparator()
    m.addAction("Quit Packrat", win.quit)
    m.aboutToShow.connect(lambda: (start.setEnabled(not win.downloads.running()), stop.setEnabled(win.downloads.running())))
    tray.setContextMenu(m)
    tray._menu = m   # keep it alive
    tray.activated.connect(lambda why: win.bring_up() if why in (
        QtWidgets.QSystemTrayIcon.ActivationReason.Trigger, QtWidgets.QSystemTrayIcon.ActivationReason.DoubleClick) else None)
    tray.messageClicked.connect(win.bring_up)
    tray.show()
    return tray


def _open_downloads(win):
    from ..uikit import open_path
    p = win.st.save_path()
    os.makedirs(p, exist_ok=True)
    open_path(p)


# ---- start ------------------------------------------------------------------------------------------------
def _instance_name() -> str:
    import hashlib
    return "packrat-" + hashlib.sha1(str(paths.install_root()).lower().encode()).hexdigest()[:16]


def _activate_mac_app() -> None:
    """[NSApp activateIgnoringOtherApps:YES] through the Objective-C runtime (no extra package needed)."""
    try:
        import ctypes
        import ctypes.util
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        objc.objc_getClass.restype = objc.sel_registerName.restype = ctypes.c_void_p
        send = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
        send_bool = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool)(("objc_msgSend", objc))
        nsapp = send(objc.objc_getClass(b"NSApplication"), objc.sel_registerName(b"sharedApplication"))
        send_bool(nsapp, objc.sel_registerName(b"activateIgnoringOtherApps:"), True)
    except Exception:  # noqa: BLE001 - the window is shown anyway
        log.debug("could not activate the app", exc_info=True)


def _already_running(name: str) -> bool:
    """True when another Packrat window for this folder answered (it was asked to come to the front)."""
    if os.name == "nt":
        try:    # this new process may bring windows forward; let the running Packrat do it instead
            import ctypes
            ctypes.windll.user32.AllowSetForegroundWindow(-1)   # ASFW_ANY
        except Exception:  # noqa: BLE001
            pass
    sock = QtNetwork.QLocalSocket()
    sock.connectToServer(name)
    if sock.waitForConnected(400):
        sock.write(b"show\n")
        sock.waitForBytesWritten(400)
        sock.disconnectFromServer()
        return True
    return False


def _setup_logging() -> None:
    from logging.handlers import RotatingFileHandler

    from ..redact import RedactingFormatter
    fh = RotatingFileHandler(paths.logs_dir() / "packrat-window.log", maxBytes=5_000_000, backupCount=2, encoding="utf-8")
    fh.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(threadName)s %(message)s"))
    logging.getLogger().addHandler(fh)
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def main(argv=None) -> int:
    argv = list(argv or [])
    set_windows_identity()
    QtCore.QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)   # the login browser needs it
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["Packrat", *argv])
    qapp.setApplicationName("Packrat")
    qapp.setApplicationDisplayName("Packrat")
    qapp.setApplicationVersion(__version__)
    qapp.setDesktopFileName("packrat")
    qapp.setWindowIcon(K.app_icon())
    qapp.setStyle("Fusion")
    qapp.setQuitOnLastWindowClosed(False)
    name = _instance_name()
    smoke_run = "--smoke" in argv
    if not smoke_run and "--new-instance" not in argv and _already_running(name):
        return 0
    _setup_logging()
    from ..grab import library
    if smoke_run:
        library.shared().set_state("setup_done", True)
    K.load_fonts(qapp)
    K.apply_theme(qapp, str(library.shared().get_state("qt_theme") or "system"))

    def excepthook(tp, val, tb):
        log.error("unexpected error in the window", exc_info=(tp, val, tb))
    sys.excepthook = excepthook

    win = MainWindow(qapp)
    win._reopen_armed = False      # not for the activation that comes with this start ("start hidden" stays hidden)
    QtCore.QTimer.singleShot(2000, lambda: setattr(win, "_reopen_armed", True))
    qapp.applicationStateChanged.connect(win._app_state)
    QtNetwork.QLocalServer.removeServer(name)
    server = QtNetwork.QLocalServer(win)
    server.newConnection.connect(lambda: (server.nextPendingConnection().close(), win.bring_up()))
    server.listen(name)
    win.tray = make_tray(win)
    if "--smoke" in argv:
        from . import smoke
        win.show()
        smoke.run(win, qapp, argv[argv.index("--smoke") + 1])
    elif win.tray is not None and win.st.get("Monitoring/startMinimized") and "--show" not in argv:
        pass   # stay next to the clock
    else:
        win.show()
    from .viewer import clear_viewer_cache
    win.worker.submit(clear_viewer_cache)
    code = qapp.exec()
    return int(code or 0)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
