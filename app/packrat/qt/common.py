"""Shared pieces of Packrat's Qt window: the look (light / dark), icons, background work, thumbnails and
small widget helpers. Nothing here knows about a particular page."""
from __future__ import annotations

import hashlib
import io
import logging
import queue
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6 import QtCore, QtGui, QtSvg, QtWidgets

from .. import paths

Qt = QtCore.Qt
log = logging.getLogger("packrat")
ASSETS = Path(__file__).resolve().parent.parent / "assets"

# ---- colours -------------------------------------------------------------------------------------
PALETTES = {
    "dark": dict(bg="#15161c", panel="#1c1e26", raised="#262935", hover="#30344a", border="#33374a",
                 text="#e7e9f1", muted="#9aa1b6", accent="#6f8cff", accent_text="#ffffff", accent_hover="#86a0ff",
                 brand="#f0b663", ok="#56c98a", warn="#f0b663", error="#ff7a7a", fav="#ff8ad1", sel="#2c3a66",
                 field="#12131a", shadow="#000000"),
    "light": dict(bg="#f3f4f8", panel="#ffffff", raised="#e9ebf2", hover="#dfe3ee", border="#d3d7e2",
                  text="#1d1f28", muted="#61687a", accent="#3d63e0", accent_text="#ffffff", accent_hover="#5277ee",
                  brand="#c9821c", ok="#1f9d5c", warn="#b56b00", error="#cc3333", fav="#d1409a", sel="#d5e0ff",
                  field="#ffffff", shadow="#9aa0b0"),
}
C: dict = dict(PALETTES["dark"])     # the current palette; updated in place by apply_theme
DARK = [True]


def system_dark() -> bool:
    try:
        return QtGui.QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:  # noqa: BLE001 - Qt older than 6.5
        from .. import theme as _t
        return _t.system_prefers_dark()


def resolve_theme(choice: str) -> str:
    c = (choice or "system").lower()
    if c in ("dark", "light"):
        return c
    return "dark" if system_dark() else "light"


QSS = """
* { font-size: 10pt; }
QWidget { background: %(bg)s; color: %(text)s; selection-background-color: %(accent)s; selection-color: %(accent_text)s; }
QMainWindow, QDialog { background: %(bg)s; }
QToolTip { background: %(raised)s; color: %(text)s; border: 1px solid %(border)s; padding: 5px 7px; border-radius: 6px; }
QLabel { background: transparent; }
QLabel[muted="true"] { color: %(muted)s; }
QLabel[role="title"] { font-size: 15pt; font-weight: 600; }
QLabel[role="heading"] { font-size: 11pt; font-weight: 600; }
QLabel[role="error"] { color: %(error)s; }
QLabel[role="ok"] { color: %(ok)s; }
QFrame#sidebar { background: %(panel)s; border: none; border-right: 1px solid %(border)s; }
QFrame#card, QFrame[card="true"] { background: %(panel)s; border: 1px solid %(border)s; border-radius: 10px; }
QToolButton#nav { background: transparent; border: none; border-radius: 8px; padding: 9px 12px; text-align: left;
    color: %(muted)s; font-weight: 500; }
QToolButton#nav:hover { background: %(raised)s; color: %(text)s; }
QToolButton#nav:checked { background: %(sel)s; color: %(text)s; font-weight: 600; }
QPushButton, QToolButton { background: %(raised)s; border: 1px solid %(border)s; border-radius: 7px; padding: 6px 13px; }
QPushButton:hover, QToolButton:hover { background: %(hover)s; }
QPushButton:pressed, QToolButton:pressed { background: %(border)s; }
QPushButton:disabled, QToolButton:disabled { color: %(muted)s; background: %(bg)s; border-color: %(raised)s; }
QPushButton[accent="true"] { background: %(accent)s; color: %(accent_text)s; border-color: %(accent)s; font-weight: 600; }
QPushButton[accent="true"]:hover { background: %(accent_hover)s; border-color: %(accent_hover)s; }
QPushButton[accent="true"]:disabled { background: %(raised)s; color: %(muted)s; border-color: %(raised)s; }
QPushButton[flat="true"], QToolButton[flat="true"] { background: transparent; border-color: transparent; padding: 5px 8px; }
QPushButton[flat="true"]:hover, QToolButton[flat="true"]:hover { background: %(raised)s; }
QToolButton::menu-indicator, QPushButton::menu-indicator { image: none; width: 0; }
QTabBar::close-button { image: none; background: transparent; border-radius: 4px; width: 14px; height: 14px; margin: 2px; }
QTabBar::close-button:hover { background: %(hover)s; }
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextBrowser {
    background: %(field)s; border: 1px solid %(border)s; border-radius: 7px; padding: 5px 8px; }
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: %(accent)s; }
QLineEdit#search { padding: 8px 12px; font-size: 11pt; border-radius: 9px; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox::down-arrow { image: url(%(img_down)s); width: 10px; height: 10px; margin-right: 8px; }
QComboBox QAbstractItemView { background: %(panel)s; border: 1px solid %(border)s; selection-background-color: %(sel)s;
    selection-color: %(text)s; outline: 0; }
QSpinBox, QDoubleSpinBox { padding-right: 2px; }
QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 18px;
    border: none; background: transparent; margin-top: 2px; }
QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 18px;
    border: none; background: transparent; margin-bottom: 2px; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url(%(img_up)s); width: 8px; height: 8px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url(%(img_down)s); width: 8px; height: 8px; }
QMenu { background: %(panel)s; border: 1px solid %(border)s; border-radius: 8px; padding: 5px; }
QMenu::item { padding: 6px 26px 6px 14px; border-radius: 5px; }
QMenu::item:selected { background: %(sel)s; color: %(text)s; }
QMenu::item:disabled { color: %(muted)s; }
QMenu::separator { height: 1px; background: %(border)s; margin: 5px 8px; }
QTabWidget::pane { border: none; }
QTabBar { background: transparent; qproperty-drawBase: 0; }
QTabBar::tab { background: transparent; color: %(muted)s; padding: 7px 14px; border: none;
    border-bottom: 2px solid transparent; margin-right: 2px; }
QTabBar::tab:hover { color: %(text)s; }
QTabBar::tab:selected { color: %(text)s; border-bottom: 2px solid %(accent)s; font-weight: 600; }
QTreeView, QListView, QTableView, QListWidget, QTreeWidget, QTableWidget { background: %(panel)s;
    border: 1px solid %(border)s; border-radius: 9px; outline: 0; alternate-background-color: %(bg)s; }
QTreeView::item, QListWidget::item, QTableView::item { padding: 4px 6px; border: none; }
QTreeView::item:hover, QListWidget::item:hover { background: %(raised)s; }
QTreeView::item:selected, QListWidget::item:selected, QTableView::item:selected { background: %(sel)s; color: %(text)s; }
QListView#grid { background: %(bg)s; border: none; }
QHeaderView::section { background: %(panel)s; color: %(muted)s; border: none; border-bottom: 1px solid %(border)s;
    padding: 7px 8px; font-weight: 600; }
QScrollBar:vertical { background: transparent; width: 12px; margin: 2px; }
QScrollBar::handle:vertical { background: %(border)s; border-radius: 4px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: %(muted)s; }
QScrollBar:horizontal { background: transparent; height: 12px; margin: 2px; }
QScrollBar::handle:horizontal { background: %(border)s; border-radius: 4px; min-width: 30px; }
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page { background: none; border: none;
    width: 0; height: 0; }
QProgressBar { background: %(raised)s; border: none; border-radius: 5px; height: 10px; text-align: center; color: transparent; }
QProgressBar::chunk { background: %(accent)s; border-radius: 5px; }
QCheckBox, QRadioButton { background: transparent; spacing: 7px; }
QCheckBox::indicator, QRadioButton::indicator { width: 16px; height: 16px; border: 1px solid %(muted)s;
    border-radius: 4px; background: %(field)s; }
QRadioButton::indicator { border-radius: 8px; }
QCheckBox::indicator:checked, QListView::indicator:checked { background: %(accent)s; border-color: %(accent)s; image: url(%(img_check)s); }
QRadioButton::indicator:checked { background: %(accent)s; border: 4px solid %(field)s; }
QListView::indicator { width: 16px; height: 16px; border: 1px solid %(muted)s; border-radius: 4px; background: %(field)s; }
QCheckBox:disabled, QLabel:disabled { color: %(muted)s; }
QGroupBox { border: 1px solid %(border)s; border-radius: 9px; margin-top: 14px; padding: 12px 10px 8px 10px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; color: %(muted)s; }
QSplitter::handle { background: transparent; }
QStatusBar { background: %(panel)s; border-top: 1px solid %(border)s; color: %(muted)s; }
QStatusBar::item { border: none; }
QSlider::groove:horizontal { height: 4px; background: %(border)s; border-radius: 2px; }
QSlider::sub-page:horizontal { background: %(accent)s; border-radius: 2px; }
QSlider::handle:horizontal { background: %(text)s; width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
"""


def apply_theme(app: QtWidgets.QApplication, choice: str) -> str:
    name = resolve_theme(choice)
    C.clear()
    C.update(PALETTES[name])
    DARK[0] = name == "dark"
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Dark if DARK[0] else Qt.ColorScheme.Light)
    except Exception:  # noqa: BLE001 - Qt older than 6.8: the title bar keeps the system colour
        pass
    pal = QtGui.QPalette()
    R = QtGui.QPalette.ColorRole
    for role, key in ((R.Window, "bg"), (R.WindowText, "text"), (R.Base, "field"), (R.AlternateBase, "panel"),
                      (R.Text, "text"), (R.Button, "raised"), (R.ButtonText, "text"), (R.Highlight, "accent"),
                      (R.HighlightedText, "accent_text"), (R.ToolTipBase, "raised"), (R.ToolTipText, "text"),
                      (R.PlaceholderText, "muted"), (R.Link, "accent")):
        pal.setColor(role, QtGui.QColor(C[key]))
    pal.setColor(QtGui.QPalette.ColorGroup.Disabled, R.Text, QtGui.QColor(C["muted"]))
    pal.setColor(QtGui.QPalette.ColorGroup.Disabled, R.ButtonText, QtGui.QColor(C["muted"]))
    app.setPalette(pal)
    _ICONS.clear()
    app.setStyleSheet(QSS % {**C, **_control_images(name)})
    return name


def _control_images(name: str) -> dict:
    """Small arrow / tick pictures for spin boxes, lists and check boxes, drawn in the theme's colours."""
    d = paths.cache_dir("ui")
    out = {}
    for key, ico, col in (("img_down", "down", C["muted"]), ("img_up", "up", C["muted"]), ("img_check", "check", C["accent_text"])):
        p = d / f"{ico}-{name}.png"
        try:
            icon(ico, col, 16).pixmap(32, 32).save(str(p), "PNG")
        except Exception:  # noqa: BLE001
            pass
        out[key] = str(p).replace("\\", "/")
    return out


# ---- icons (drawn here, so no image files are needed) -------------------------------------------
_PATHS = {
    "search": '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>',
    "download": '<path d="M12 3v12"/><path d="M7 11l5 5 5-5"/><path d="M4 20h16"/>',
    "star": '<path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.500l6.1-.9z"/>',
    "bell": '<path d="M6 9a6 6 0 0112 0c0 6 2 7 2 7H4s2-1 2-7"/><path d="M10 20a2 2 0 004 0"/>',
    "box": '<path d="M3 7l9-4 9 4v10l-9 4-9-4z"/><path d="M3 7l9 4 9-4"/><path d="M12 11v10"/>',
    "sliders": '<path d="M4 6h10"/><path d="M18 6h2"/><circle cx="16" cy="6" r="2"/><path d="M4 12h2"/>'
               '<path d="M10 12h10"/><circle cx="8" cy="12" r="2"/><path d="M4 18h10"/><path d="M18 18h2"/>'
               '<circle cx="16" cy="18" r="2"/>',
    "wrench": '<path d="M14.5 6.500a4 4 0 005 5L10 21l-4-4 9.500-9.500z" transform="rotate(8 12 12)"/>',
    "list": '<path d="M8 6h12"/><path d="M8 12h12"/><path d="M8 18h12"/><circle cx="4" cy="6" r="1"/>'
            '<circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9.500 9.500a2.500 2.500 0 115 0c0 1.500-2.500 2-2.500 4"/>'
            '<circle cx="12" cy="17.500" r=".6"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3a14 14 0 010 18a14 14 0 010-18"/>',
    "down": '<path d="M5 9l7 7 7-7" stroke-width="3"/>', "up": '<path d="M5 15l7-7 7 7" stroke-width="3"/>',
    "check": '<path d="M5 12.500l4.500 4.500L19 7.500" stroke-width="3.2"/>',
    "left": '<path d="M15 5l-7 7 7 7"/>', "right": '<path d="M9 5l7 7-7 7"/>',
    "plus": '<path d="M12 5v14"/><path d="M5 12h14"/>', "close": '<path d="M6 6l12 12"/><path d="M18 6L6 18"/>',
    "play": '<path d="M7 4l13 8-13 8z" fill="currentColor"/>', "pause": '<path d="M8 5v14"/><path d="M16 5v14"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor"/>',
    "save": '<path d="M5 3h11l4 4v14H5z"/><path d="M8 3v6h8"/><path d="M8 21v-7h8v7"/>',
    "folder": '<path d="M3 6h7l2 3h9v10H3z"/>', "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/>'
              '<path d="M12 20v2"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="M5 5l1.500 1.500"/>'
              '<path d="M17.500 17.500L19 19"/><path d="M5 19l1.500-1.500"/><path d="M17.500 6.500L19 5"/>',
    "moon": '<path d="M20 14.500A8.500 8.500 0 019.500 4a8.500 8.500 0 1010.500 10.500z"/>',
    "more": '<circle cx="5" cy="12" r="1.500" fill="currentColor"/><circle cx="12" cy="12" r="1.500" fill="currentColor"/>'
            '<circle cx="19" cy="12" r="1.500" fill="currentColor"/>',
    "refresh": '<path d="M20 11a8 8 0 10-2.300 6"/><path d="M20 4v7h-7"/>',
    "expand": '<path d="M4 9V4h5"/><path d="M20 9V4h-5"/><path d="M4 15v5h5"/><path d="M20 15v5h-5"/>',
    "volume": '<path d="M4 9v6h4l5 4V5L8 9z"/><path d="M16 9a4 4 0 010 6"/>',
    "mute": '<path d="M4 9v6h4l5 4V5L8 9z"/><path d="M16 9l5 6"/><path d="M21 9l-5 6"/>',
    "key": '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9"/><path d="M16 7l3 3"/>',
    "image": '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="1.500"/><path d="M4 18l5-5 4 4 3-3 4 4"/>',
}
_ICONS: dict = {}


def icon(name: str, color: str | None = None, size: int = 20) -> QtGui.QIcon:
    col = color or C["text"]
    key = (name, col, size)
    if key not in _ICONS:
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{col}" color="{col}" '
               f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{_PATHS.get(name, "")}</svg>')
        r = QtSvg.QSvgRenderer(QtCore.QByteArray(svg.encode()))
        pm = QtGui.QPixmap(size * 2, size * 2)
        pm.fill(Qt.GlobalColor.transparent)
        p = QtGui.QPainter(pm)
        r.render(p)
        p.end()
        pm.setDevicePixelRatio(2.0)
        _ICONS[key] = QtGui.QIcon(pm)
    return _ICONS[key]


def app_icon() -> QtGui.QIcon:
    ic = QtGui.QIcon()
    for f in ("packrat.ico", "packrat.png", "packrat-64.png"):
        p = ASSETS / f
        if p.is_file():
            ic.addFile(str(p))
    return ic


# ---- background work ------------------------------------------------------------------------------
class Worker(QtCore.QObject):
    """Run functions in threads; results come back on the window's thread (never touch widgets from a thread)."""
    _wake = QtCore.Signal()

    def __init__(self, parent=None, threads: int = 6):
        super().__init__(parent)
        self.pool = ThreadPoolExecutor(threads, thread_name_prefix="ui")
        self.q: queue.Queue = queue.Queue()
        self.closed = False
        self._wake.connect(self._drain, Qt.ConnectionType.QueuedConnection)

    def submit(self, fn, *args, on_done=None, on_error=None, **kw):
        def run():
            try:
                res = fn(*args, **kw)
            except Exception as e:  # noqa: BLE001
                log.debug("background task failed", exc_info=True)
                self.call(on_error or (lambda err: log.warning("%s", err)), e)
                return
            if on_done:
                self.call(on_done, res)
        return self.pool.submit(run)

    def call(self, fn, *args):
        """From any thread: run fn(*args) on the window's thread soon."""
        if self.closed:
            return
        self.q.put((fn, args))
        self._wake.emit()

    def _drain(self):
        for _ in range(300):
            try:
                fn, args = self.q.get_nowait()
            except queue.Empty:
                return
            try:
                fn(*args)
            except RuntimeError:
                pass   # a widget was closed in the meantime
            except Exception:  # noqa: BLE001
                log.exception("window update failed")
        self._wake.emit()

    def close(self):
        self.closed = True
        self.pool.shutdown(wait=False, cancel_futures=True)


def pil_to_qimage(im) -> QtGui.QImage:
    im = im.convert("RGBA")
    data = im.tobytes("raw", "RGBA")
    return QtGui.QImage(data, im.width, im.height, im.width * 4, QtGui.QImage.Format.Format_RGBA8888).copy()


class Thumbs:
    """Preview pictures: memory (LRU) -> disk cache -> download. Decoding and scaling happen in threads."""

    def __init__(self, worker: Worker, size: int = 180, crop: bool = False):
        self.w, self.size, self.crop = worker, size, crop
        self.mem: OrderedDict = OrderedDict()
        self.max_mem = 1500
        self.dir = paths.cache_dir("thumbs")
        self.pool = ThreadPoolExecutor(6, thread_name_prefix="thumb")

    @staticmethod
    def key(url: str) -> str:
        return hashlib.sha1(url.encode("utf-8", "replace")).hexdigest()

    def get(self, site, img, callback, size: int | None = None):
        """callback(QPixmap or None) on the window's thread."""
        url = img.preview_url or img.sample_url or ""
        if not url:
            callback(None)
            return
        s = int(size or self.size)
        rect = tuple(getattr(img, "preview_rect", ()) or ())
        k = f"{self.key(url)}-{s}-{int(self.crop)}" + ("-" + "_".join(map(str, rect)) if rect else "")
        if k in self.mem:
            self.mem.move_to_end(k)
            callback(self.mem[k])
            return

        def work():
            from PIL import Image as PI
            raw = None
            p = self.dir / (self.key(url) + ".bin")
            if p.is_file():
                try:
                    raw = p.read_bytes()
                except OSError:
                    raw = None
            if raw is None:
                r = site.request(url, "thumbnail", referer=site.referer("thumbnail", img.page_url, img), retries=1)
                try:
                    if r.status_code != 200:
                        return None
                    raw = r.content
                finally:
                    r.close()
                try:
                    p.write_bytes(raw)
                except OSError:
                    pass
            try:
                im = PI.open(io.BytesIO(raw))
                im.seek(0)
                im = im.convert("RGBA") if im.mode in ("P", "LA", "RGBA") else im.convert("RGB")
                if rect:      # one picture of a sprite sheet (E-Hentai gallery pages)
                    x, y, w, h = rect
                    im = im.crop((max(0, x), max(0, y), min(im.width, x + w), min(im.height, y + h)))
            except Exception:  # noqa: BLE001 - not a picture (a video preview link, an error page)
                return None
            if self.crop:
                w, h = im.size
                m = min(w, h)
                im = im.crop(((w - m) // 2, (h - m) // 2, (w - m) // 2 + m, (h - m) // 2 + m)).resize((s, s), PI.LANCZOS)
            else:
                im.thumbnail((s, s), PI.LANCZOS)
            return pil_to_qimage(im)

        def done(fut):
            try:
                qi = fut.result()
            except Exception:  # noqa: BLE001
                qi = None
            self.w.call(self._make, k, qi, callback)
        self.pool.submit(work).add_done_callback(done)

    def _make(self, k, qimage, callback):
        if qimage is None:
            callback(None)
            return
        pm = QtGui.QPixmap.fromImage(qimage)
        self.mem[k] = pm
        while len(self.mem) > self.max_mem:
            self.mem.popitem(last=False)
        callback(pm)

    def close(self):
        self.pool.shutdown(wait=False, cancel_futures=True)


# ---- small helpers --------------------------------------------------------------------------------
def fmt_bytes(n) -> str:
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return "?"


def ago(ts) -> str:
    if not ts:
        return "never"
    d = time.time() - float(ts)
    if d < 0:
        d = -d
        pre, post = "in ", ""
    else:
        pre, post = "", " ago"
    for lim, div, unit in ((90, 1, "s"), (5400, 60, "min"), (129600, 3600, "h"), (10 ** 12, 86400, "days")):
        if d < lim:
            return f"{pre}{int(d / div)} {unit}{post}"
    return "?"


def label(text: str = "", *, muted: bool = False, role: str = "", wrap: bool = False, selectable: bool = False,
          rich: bool = False) -> QtWidgets.QLabel:
    lb = QtWidgets.QLabel(text)
    if muted:
        lb.setProperty("muted", True)
    if role:
        lb.setProperty("role", role)
    lb.setWordWrap(wrap)
    if rich:
        lb.setTextFormat(Qt.TextFormat.RichText)
    if selectable:
        lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse)
    return lb


def restyle(w: QtWidgets.QWidget, **props) -> None:
    for k, v in props.items():
        w.setProperty(k, v)
    w.style().unpolish(w)
    w.style().polish(w)


def button(text: str = "", slot=None, *, accent: bool = False, flat: bool = False, ico: str = "", tip: str = "",
           menu: QtWidgets.QMenu | None = None) -> QtWidgets.QPushButton:
    b = QtWidgets.QPushButton(text)
    if accent:
        b.setProperty("accent", True)
    if flat:
        b.setProperty("flat", True)
    if ico:
        b.setIcon(icon(ico, C["accent_text"] if accent else None))
        b.setProperty("ico", ico)
    if tip:
        b.setToolTip(tip)
    if slot:
        b.clicked.connect(lambda *_: slot())
    if menu is not None:
        b.setMenu(menu)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def refresh_icons(root: QtWidgets.QWidget) -> None:
    """After a theme change: redraw every button icon in the new text colour."""
    for b in root.findChildren(QtWidgets.QAbstractButton):
        name = b.property("ico")
        if name:
            b.setIcon(icon(name, C["accent_text"] if b.property("accent") else None))


def hbox(*items, margins=(0, 0, 0, 0), spacing: int = 8) -> QtWidgets.QHBoxLayout:
    return _fill(QtWidgets.QHBoxLayout(), items, margins, spacing)


def vbox(*items, margins=(0, 0, 0, 0), spacing: int = 8) -> QtWidgets.QVBoxLayout:
    return _fill(QtWidgets.QVBoxLayout(), items, margins, spacing)


def _fill(lay, items, margins, spacing):
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    for it in items:
        if it is None:
            lay.addStretch(1)
        elif isinstance(it, int):
            lay.addSpacing(it)
        elif isinstance(it, QtWidgets.QLayout):
            lay.addLayout(it)
        elif isinstance(it, tuple):
            (lay.addLayout if isinstance(it[0], QtWidgets.QLayout) else lay.addWidget)(it[0], it[1])
        else:
            lay.addWidget(it)
    return lay


def widget(layout: QtWidgets.QLayout, name: str = "") -> QtWidgets.QWidget:
    w = QtWidgets.QFrame() if name else QtWidgets.QWidget()
    if name:
        w.setObjectName(name)
    w.setLayout(layout)
    return w


def fit(win: QtWidgets.QWidget, w: int, h: int) -> None:
    """Size a window, but never bigger than the screen it is on."""
    scr = (win.screen() or QtWidgets.QApplication.primaryScreen()).availableGeometry()
    win.resize(min(w, scr.width() - 40), min(h, scr.height() - 60))


def ask(parent, title: str, text: str, yes: str = "Yes", no: str = "No", default_yes: bool = True) -> bool:
    mb = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Icon.Question, title, text, parent=parent)
    y = mb.addButton(yes, QtWidgets.QMessageBox.ButtonRole.AcceptRole)
    n = mb.addButton(no, QtWidgets.QMessageBox.ButtonRole.RejectRole)
    mb.setDefaultButton(y if default_yes else n)
    mb.exec()
    return mb.clickedButton() is y


def warn(parent, title: str, text: str) -> None:
    QtWidgets.QMessageBox.warning(parent, title, text)


def info(parent, title: str, text: str) -> None:
    QtWidgets.QMessageBox.information(parent, title, text)


def ask_text(parent, title: str, prompt: str, text: str = "") -> str | None:
    val, ok = QtWidgets.QInputDialog.getText(parent, title, prompt, text=text)
    return val if ok else None


def tree(headers, widths=(), sortable: bool = True, multi: bool = True) -> QtWidgets.QTreeWidget:
    t = QtWidgets.QTreeWidget()
    t.setHeaderLabels(list(headers))
    t.setRootIsDecorated(False)
    t.setAlternatingRowColors(False)
    t.setUniformRowHeights(True)
    t.setSortingEnabled(sortable)
    t.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection if multi
                       else QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
    t.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    for i, w in enumerate(widths):
        t.setColumnWidth(i, w)
    t.header().setStretchLastSection(True)
    return t


class SortItem(QtWidgets.QTreeWidgetItem):
    """A row that sorts numbers as numbers (the value to sort by is kept in UserRole + 1)."""

    def __lt__(self, other):
        col = self.treeWidget().sortColumn() if self.treeWidget() else 0
        a, b = self.data(col, Qt.ItemDataRole.UserRole + 1), other.data(col, Qt.ItemDataRole.UserRole + 1)
        if a is not None and b is not None:
            try:
                return a < b
            except TypeError:
                pass
        return self.text(col).lower() < other.text(col).lower()


def empty_state(title: str, text: str = "") -> QtWidgets.QLabel:
    lb = QtWidgets.QLabel()
    lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lb.setWordWrap(True)
    lb.setTextFormat(Qt.TextFormat.RichText)
    lb.setOpenExternalLinks(False)
    set_empty(lb, title, text)
    return lb


def set_empty(lb: QtWidgets.QLabel, title: str, text: str = "") -> None:
    lb.setText(f'<div style="font-size:15pt; font-weight:600;">{title}</div>'
               f'<div style="color:{C["muted"]}; line-height:150%; margin-top:10px;">{text}</div>')


def esc(s) -> str:
    import html
    return html.escape(str(s if s is not None else ""))
