"""Shared pieces of Packrat's Qt window: the look (pastels, one colour per page, light / dark), the typefaces,
icons, background work, thumbnails and small widget helpers. Nothing here knows what is on a particular page."""
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
FORM_MAX_WIDTH = 1100    # settings-style forms stop growing here; wider fields only spread the eye

# ---- colours -------------------------------------------------------------------------------------
# Every page has its own pastel (its "hue"): Browse is lilac, Downloads mint, Favorites rose ... The colour says
# where you are: the page's row in the sidebar, its main button, its progress bar and its ticks all wear it.
# A hue is five tones:
#   fill    a soft wash        the sidebar pill, icon tiles, selected rows
#   strong  the pastel itself  main buttons, progress bars, ticked boxes
#   hover   strong, one step on
#   line    outlines           the field you type in, the selected picture
#   ink     the hue as text    readable on the window's background
HUES = ("lilac", "mint", "rose", "butter", "sky", "orchid", "peach", "aqua")
TONES = ("fill", "strong", "hover", "line", "ink")
PAGE_HUE = {"browse": "lilac", "downloads": "mint", "favorites": "rose", "monitors": "butter", "archive": "sky",
            "settings": "orchid", "tools": "peach", "log": "aqua"}
_HUE_TONES = {
    "light": {"lilac": ("#e6e3ff", "#bcb5ff", "#aaa2fa", "#8f85ee", "#4a3fa6"),
              "mint": ("#d5f4e5", "#a3e4c4", "#8dd8b3", "#4fbf8e", "#1f7653"),
              "rose": ("#ffdfea", "#ffb6d0", "#fba1c1", "#ee79a6", "#a8356a"),
              "butter": ("#fff0c2", "#ffde80", "#f8d166", "#ddb02e", "#856000"),
              "sky": ("#d8edff", "#a9d6fa", "#92c8f3", "#5fa9e3", "#1e659e"),
              "orchid": ("#f2dffb", "#dfb8f5", "#d4a4f0", "#bc7fe0", "#7f3fa8"),
              "peach": ("#ffe3d2", "#ffc2a1", "#fbb089", "#ee8f5e", "#a34d27"),
              "aqua": ("#d6f1f1", "#a6dfe0", "#90d3d5", "#55b7bb", "#26727a")},
    "dark": {"lilac": ("#322e57", "#b9b2ff", "#ccc7ff", "#9c93f5", "#c2bcff"),
             "mint": ("#1e4133", "#9fe3c1", "#b7edd2", "#5cc79a", "#a6e6c6"),
             "rose": ("#522539", "#ffb3ce", "#ffc8db", "#ee7faa", "#ffbad2"),
             "butter": ("#473a15", "#ffdd7a", "#ffe699", "#d9b03a", "#ffe08a"),
             "sky": ("#1e3a52", "#a5d4fa", "#bddffc", "#62abe4", "#add8fa"),
             "orchid": ("#412951", "#ddb4f5", "#e7c8f8", "#bd82e0", "#e0bbf6"),
             "peach": ("#522e1d", "#ffc09e", "#ffd0b6", "#ee925f", "#ffc6a6"),
             "aqua": ("#1c3f42", "#a3dedf", "#bae8e9", "#58b9bd", "#aae1e2")},
}
_BASE = {
    # text on a pastel button is the same dark ink in both themes: the buttons stay pastel at night
    "light": dict(bg="#faf9fd", side="#f1eef8", panel="#ffffff", raised="#f1eef7", hover="#e8e4f1", border="#e0dbeb",
                  text="#2b2838", muted="#6c6682", accent_text="#27233a", error="#c13b55", field="#ffffff",
                  shadow="#b9b2cc", edge="#b9b2cc", stage="#eceaf3"),
    "dark": dict(bg="#1b1924", side="#15131c", panel="#23212f", raised="#2d2a3b", hover="#3a3649", border="#3b374c",
                 text="#ece9f5", muted="#a6a0bc", accent_text="#1b1924", error="#ff9aa8", field="#16141e",
                 shadow="#000000", edge="#5d5873", stage="#100f16"),
}


def _palette(name: str) -> dict:
    pal = dict(_BASE[name])
    for hue, tones in _HUE_TONES[name].items():
        pal.update({f"{hue}_{tone}": col for tone, col in zip(TONES, tones)})
    # the names the pages use for "the usual accent" (lilac, Browse's hue) and for meanings
    pal.update(accent=pal["lilac_strong"], accent_hover=pal["lilac_hover"], sel=pal["lilac_fill"],
               link=pal["lilac_ink"], ring=pal["lilac_line"], ok=pal["mint_ink"], warn=pal["butter_ink"],
               fav=pal["rose_ink"], brand=pal["peach_strong"])
    return pal


PALETTES = {name: _palette(name) for name in ("dark", "light")}
C: dict = dict(PALETTES["dark"])     # the current palette; updated in place by apply_theme
DARK = [True]
FONTS = {"body": "", "display": ""}   # the family names Qt gave the bundled typefaces ("" = not loaded)


def hue(name: str, tone: str = "strong") -> str:
    """A colour of a hue or of a page: hue("mint", "fill"), hue("downloads", "ink")."""
    return C[f"{PAGE_HUE.get(name, name if name in HUES else 'lilac')}_{tone}"]


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
QToolTip { background: %(panel)s; color: %(text)s; border: 1px solid %(border)s; padding: 6px 9px; border-radius: 8px; }
QLabel { background: transparent; }
QLabel[muted="true"] { color: %(muted)s; }
QLabel[role="title"] { font-size: 18pt; }
QLabel[role="heading"] { font-size: 12pt; }
QLabel[role="error"] { color: %(error)s; }
QLabel[role="ok"] { color: %(ok)s; }
QFrame#sidebar { background: %(side)s; border: none; border-right: 1px solid %(border)s; }
QFrame#sidebar QPushButton[flat="true"] { color: %(muted)s; text-align: left; }
QFrame#sidebar QPushButton[flat="true"]:hover { background: %(hover)s; color: %(text)s; }
QFrame#card, QFrame[card="true"] { background: %(panel)s; border: 1px solid %(border)s; border-radius: 16px; }
QToolButton#nav { background: transparent; border: none; border-radius: 13px; padding: 6px 9px; text-align: left;
    color: %(muted)s; font-size: 10.5pt; font-weight: 700; }
QToolButton#nav[compact="true"] { padding: 3px 9px; border-radius: 11px; }
QToolButton#nav:hover { background: %(hover)s; color: %(text)s; }
QToolButton#nav:checked { background: %(sel)s; color: %(link)s; }
QPushButton, QToolButton { background: %(panel)s; border: 1px solid %(border)s; border-radius: 10px; padding: 6px 13px; }
QPushButton:hover, QToolButton:hover { background: %(raised)s; border-color: %(edge)s; }
QPushButton:pressed, QToolButton:pressed { background: %(hover)s; }
QPushButton:checked { background: %(sel)s; border-color: %(ring)s; color: %(link)s; }
QPushButton:disabled, QToolButton:disabled { color: %(muted)s; background: %(bg)s; border-color: %(raised)s; }
QPushButton[accent="true"] { background: %(accent)s; color: %(accent_text)s; border-color: %(accent)s; font-weight: 700; }
QPushButton[accent="true"]:hover { background: %(accent_hover)s; border-color: %(accent_hover)s; }
QPushButton[accent="true"]:disabled { background: %(raised)s; color: %(muted)s; border-color: %(raised)s; }
QPushButton[flat="true"], QToolButton[flat="true"] { background: transparent; border-color: transparent; padding: 5px 8px; }
QPushButton[flat="true"]:hover, QToolButton[flat="true"]:hover { background: %(raised)s; }
QToolButton::menu-indicator, QPushButton::menu-indicator { image: none; width: 0; }
QTabBar::close-button { image: none; background: transparent; border-radius: 4px; width: 14px; height: 14px; margin: 2px; }
QTabBar::close-button:hover { background: %(hover)s; }
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextBrowser {
    background: %(field)s; border: 1px solid %(border)s; border-radius: 10px; padding: 6px 10px; }
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: %(ring)s; }
QLineEdit#search { padding: 9px 14px; font-size: 11pt; border-radius: 14px; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox::down-arrow { image: url(%(img_down)s); width: 10px; height: 10px; margin-right: 10px; }
QComboBox QAbstractItemView { background: %(panel)s; border: 1px solid %(border)s; border-radius: 8px;
    selection-background-color: %(sel)s; selection-color: %(text)s; outline: 0; }
QSpinBox, QDoubleSpinBox { padding-right: 2px; }
QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 20px;
    border: none; background: transparent; margin-top: 3px; }
QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 20px;
    border: none; background: transparent; margin-bottom: 3px; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url(%(img_up)s); width: 8px; height: 8px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url(%(img_down)s); width: 8px; height: 8px; }
QMenu { background: %(panel)s; border: 1px solid %(border)s; border-radius: 12px; padding: 6px; }
QMenu::item { padding: 7px 26px 7px 14px; border-radius: 8px; }
QMenu::item:selected { background: %(sel)s; color: %(text)s; }
QMenu::item:disabled { color: %(muted)s; }
QMenu::separator { height: 1px; background: %(border)s; margin: 6px 10px; }
QTabWidget::pane { border: none; }
QTabBar { background: transparent; qproperty-drawBase: 0; }
QTabBar::tab { background: transparent; color: %(muted)s; padding: 6px 14px; border: none; border-radius: 10px;
    margin: 2px 3px 4px 0; }
QTabBar::tab:hover { background: %(raised)s; color: %(text)s; }
QTabBar::tab:selected { background: %(sel)s; color: %(link)s; font-weight: 700; }
QTreeView, QListView, QTableView, QListWidget, QTreeWidget, QTableWidget { background: %(panel)s;
    border: 1px solid %(border)s; border-radius: 12px; padding: 3px; outline: 0; alternate-background-color: %(bg)s; }
QTreeView::item, QListWidget::item, QTableView::item { padding: 5px 7px; border: none; }
QListWidget::item { border-radius: 7px; }
QTreeView::item:hover, QListWidget::item:hover { background: %(raised)s; }
QTreeView::item:selected, QListWidget::item:selected, QTableView::item:selected { background: %(sel)s; color: %(text)s; }
QListView#grid { background: %(bg)s; border: none; padding: 0; }
QHeaderView { background: transparent; }
QHeaderView::section { background: %(panel)s; color: %(muted)s; border: none; border-bottom: 1px solid %(border)s;
    padding: 8px 8px; font-weight: 700; }
QScrollBar:vertical { background: transparent; width: 12px; margin: 2px; }
QScrollBar::handle:vertical { background: %(border)s; border-radius: 4px; min-height: 34px; }
QScrollBar::handle:vertical:hover { background: %(edge)s; }
QScrollBar:horizontal { background: transparent; height: 12px; margin: 2px; }
QScrollBar::handle:horizontal { background: %(border)s; border-radius: 4px; min-width: 34px; }
QScrollBar::handle:horizontal:hover { background: %(edge)s; }
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page { background: none; border: none;
    width: 0; height: 0; }
QProgressBar { background: %(raised)s; border: none; border-radius: 6px; height: 12px; text-align: center; color: transparent; }
QProgressBar::chunk { background: %(accent)s; border-radius: 6px; }
QCheckBox, QRadioButton { background: transparent; spacing: 8px; }
QCheckBox::indicator, QRadioButton::indicator { width: 17px; height: 17px; border: 1px solid %(edge)s;
    border-radius: 6px; background: %(field)s; }
QRadioButton::indicator { border-radius: 9px; }
QCheckBox::indicator:hover, QRadioButton::indicator:hover { border-color: %(ring)s; }
QCheckBox::indicator:checked, QListView::indicator:checked { background: %(accent)s; border-color: %(ring)s; image: url(%(img_check)s); }
QRadioButton::indicator:checked { background: %(ring)s; border: 4px solid %(field)s; }
QListView::indicator { width: 17px; height: 17px; border: 1px solid %(edge)s; border-radius: 6px; background: %(field)s; }
QCheckBox:disabled, QLabel:disabled { color: %(muted)s; }
QGroupBox { border: 1px solid %(border)s; border-radius: 14px; margin-top: 16px; padding: 14px 12px 10px 12px; font-weight: 700; }
QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 6px; color: %(muted)s; }
QSplitter::handle { background: transparent; }
QStatusBar { background: %(side)s; border-top: 1px solid %(border)s; color: %(muted)s; }
QStatusBar::item { border: none; }
QSlider::groove:horizontal { height: 4px; background: %(border)s; border-radius: 2px; }
QSlider::sub-page:horizontal { background: %(ring)s; border-radius: 2px; }
QSlider::handle:horizontal { background: %(text)s; width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
"""

# The same rules once more for each hue. A page (or a dialog, or one card) says hue="mint" and everything inside
# follows: no widget needs to know which page it is on.
_HUE_QSS = """
QToolButton#nav[hue="@"]:checked { background: %(@_fill)s; color: %(@_ink)s; }
QWidget[hue="@"] QPushButton[accent="true"] { background: %(@_strong)s; border-color: %(@_strong)s; }
QWidget[hue="@"] QPushButton[accent="true"]:hover { background: %(@_hover)s; border-color: %(@_hover)s; }
QWidget[hue="@"] QPushButton[accent="true"]:disabled { background: %(raised)s; border-color: %(raised)s; }
QWidget[hue="@"] QLineEdit, QWidget[hue="@"] QPlainTextEdit, QWidget[hue="@"] QTextEdit, QWidget[hue="@"] QSpinBox,
QWidget[hue="@"] QDoubleSpinBox { selection-background-color: %(@_strong)s; }
QWidget[hue="@"] QLineEdit:focus, QWidget[hue="@"] QPlainTextEdit:focus, QWidget[hue="@"] QSpinBox:focus,
QWidget[hue="@"] QDoubleSpinBox:focus, QWidget[hue="@"] QComboBox:focus { border-color: %(@_line)s; }
QWidget[hue="@"] QComboBox QAbstractItemView { selection-background-color: %(@_fill)s; }
QWidget[hue="@"] QMenu::item:selected { background: %(@_fill)s; }
QWidget[hue="@"] QTabBar::tab:selected { background: %(@_fill)s; color: %(@_ink)s; }
QWidget[hue="@"] QTreeView::item:selected, QWidget[hue="@"] QListWidget::item:selected,
QWidget[hue="@"] QTableView::item:selected { background: %(@_fill)s; }
QWidget[hue="@"] QProgressBar::chunk { background: %(@_strong)s; }
QWidget[hue="@"] QCheckBox::indicator:hover, QWidget[hue="@"] QRadioButton::indicator:hover { border-color: %(@_line)s; }
QWidget[hue="@"] QCheckBox::indicator:checked, QWidget[hue="@"] QListView::indicator:checked { background: %(@_strong)s;
    border-color: %(@_line)s; }
QWidget[hue="@"] QRadioButton::indicator:checked { background: %(@_line)s; }
QWidget[hue="@"] QSlider::sub-page:horizontal { background: %(@_line)s; }
"""
# A button can also carry a hue of its own (the Tools cards). These come last, so they win over the page's hue.
_OWN_HUE_QSS = """
QWidget QPushButton[accent="true"][hue="@"] { background: %(@_strong)s; border-color: %(@_strong)s; }
QWidget QPushButton[accent="true"][hue="@"]:hover { background: %(@_hover)s; border-color: %(@_hover)s; }
"""
QSS += "".join(_HUE_QSS.replace("@", h) for h in HUES) + "".join(_OWN_HUE_QSS.replace("@", h) for h in HUES)


# ---- type ----------------------------------------------------------------------------------------
# Nunito for everything you read, Fredoka for names and titles. Both ship in assets/ (SIL Open Font License).
# If a file is missing or the system refuses it, the system's own typeface is used and nothing else changes.
_FONT_FILES = (("body", ("Nunito-Regular.ttf", "Nunito-Bold.ttf")), ("display", ("Fredoka-Medium.ttf",)))
_FONT_QSS = """
QLabel[role="title"], QLabel[role="heading"], QLabel[role="wordmark"] { font-family: "%(display)s"; font-weight: %(weight)s; }
QLabel[role="wordmark"] { font-size: 19pt; }
"""


def load_fonts(app: QtWidgets.QApplication) -> None:
    for role, files in _FONT_FILES:
        for f in files:
            try:
                fid = QtGui.QFontDatabase.addApplicationFont(str(ASSETS / f)) if (ASSETS / f).is_file() else -1
                fams = QtGui.QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
            except Exception:  # noqa: BLE001
                fams = []
            if fams and not FONTS[role]:
                FONTS[role] = fams[0]
    if FONTS["body"]:
        f = QtGui.QFont(FONTS["body"])
        f.setPointSize(10)
        app.setFont(f)


def display_font(points: float) -> QtGui.QFont:
    """The title typeface, for text that is painted by hand or set as rich text."""
    f = QtGui.QFont(FONTS["display"] or QtWidgets.QApplication.font().family())
    f.setPointSizeF(points)
    f.setWeight(QtGui.QFont.Weight.Medium if FONTS["display"] else QtGui.QFont.Weight.Bold)
    return f


def display_css() -> str:
    """The same, as CSS for rich text."""
    if FONTS["display"]:
        return f"font-family:'{FONTS['display']}'; font-weight:500;"
    return "font-weight:700;"


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
                      (R.Text, "text"), (R.Button, "panel"), (R.ButtonText, "text"), (R.Highlight, "accent"),
                      (R.HighlightedText, "accent_text"), (R.ToolTipBase, "panel"), (R.ToolTipText, "text"),
                      (R.PlaceholderText, "muted"), (R.Link, "link")):
        pal.setColor(role, QtGui.QColor(C[key]))
    pal.setColor(QtGui.QPalette.ColorGroup.Disabled, R.Text, QtGui.QColor(C["muted"]))
    pal.setColor(QtGui.QPalette.ColorGroup.Disabled, R.ButtonText, QtGui.QColor(C["muted"]))
    app.setPalette(pal)
    _ICONS.clear()
    _TILES.clear()
    fonts = _FONT_QSS % {"display": FONTS["display"] or app.font().family(), "weight": 500 if FONTS["display"] else 700}
    app.setStyleSheet(QSS % {**C, **_control_images(name)} + fonts)
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
    "tag": '<path d="M3 11V4h7l11 11-7 7z"/><circle cx="7.500" cy="8.500" r="1.200"/>',
    "trash": '<path d="M4 7h16"/><path d="M9 7V4h6v3"/><path d="M6 7l1 13h10l1-13"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.500 2"/>',
    "shield": '<path d="M12 3l8 3v6c0 4.500-3.200 7.800-8 9-4.800-1.200-8-4.500-8-9V6z"/>',
    "screen": '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8"/><path d="M12 16v4"/>',
    "pencil": '<path d="M4 20l1-4L16 5l3 3L8 19z"/><path d="M14 7l3 3"/>',
    "hash": '<path d="M9.500 4L7.500 20"/><path d="M16.500 4l-2 16"/><path d="M4.500 9h16"/><path d="M3.500 15h16"/>',
    "sparkle": '<path d="M11 3l1.900 5.100L18 10l-5.100 1.900L11 17l-1.900-5.100L4 10l5.100-1.900z"/>'
               '<path d="M18.500 15.500l.8 2.200 2.200.8-2.200.8-.8 2.200-.8-2.200-2.200-.8 2.200-.8z"/>',
    "addon": '<rect x="4" y="4" width="16" height="16" rx="4"/><path d="M12 8.500v7"/><path d="M8.500 12h7"/>',
    "import": '<path d="M12 3v11"/><path d="M8 10l4 4 4-4"/><path d="M4 14v6h16v-6"/>',
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


_TILES: dict = {}


def tile(name: str, hue_name: str = "lilac", size: int = 28, on: bool = False, round_: float = 0.36) -> QtGui.QPixmap:
    """An icon on a rounded pastel square: the sign of a page, a tool, an empty list. `on` is the stronger
    version, for the page that is open."""
    key = (name, hue_name, size, on, round_)
    if key not in _TILES:
        pm = QtGui.QPixmap(size * 2, size * 2)
        pm.fill(Qt.GlobalColor.transparent)
        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QColor(hue(hue_name, "strong" if on else "fill")))
        p.drawRoundedRect(QtCore.QRectF(0, 0, size * 2, size * 2), size * 2 * round_, size * 2 * round_)
        inner = int(size * 2 * 0.58)
        off = (size * 2 - inner) // 2
        ink = C["accent_text"] if on else hue(hue_name, "ink")
        p.drawPixmap(QtCore.QRect(off, off, inner, inner), icon(name, ink, inner).pixmap(inner, inner))
        p.end()
        pm.setDevicePixelRatio(2.0)
        _TILES[key] = pm
    return _TILES[key]


def nav_icon(name: str, hue_name: str, size: int = 28) -> QtGui.QIcon:
    """The sidebar's icon for a page: a soft tile, and a stronger one while the page is open."""
    ic = QtGui.QIcon()
    for mode in (QtGui.QIcon.Mode.Normal, QtGui.QIcon.Mode.Active, QtGui.QIcon.Mode.Selected):
        ic.addPixmap(tile(name, hue_name, size), mode, QtGui.QIcon.State.Off)
        ic.addPixmap(tile(name, hue_name, size, on=True), mode, QtGui.QIcon.State.On)
    return ic


def tile_label(name: str, hue_name: str, size: int = 40) -> QtWidgets.QLabel:
    lb = QtWidgets.QLabel()
    lb.setFixedSize(size, size)
    lb.setProperty("tile", f"{name}|{hue_name}|{size}")
    lb.setPixmap(tile(name, hue_name, size))
    return lb


def page_head(page: str, ico: str, title: str, intro: str = "") -> QtWidgets.QHBoxLayout:
    """The top of a page: its sign in its own colour, its name, and one line about what it is for."""
    text = vbox(label(title, role="title"), spacing=1)
    if intro:
        text.addWidget(label(intro, muted=True, wrap=True))
    lay = hbox(tile_label(ico, PAGE_HUE.get(page, page), 44), (text, 1), spacing=14)
    lay.setAlignment(lay.itemAt(0).widget(), Qt.AlignmentFlag.AlignTop)
    return lay


_TAG_HUE = {"artists": "rose", "copyrights": "orchid", "characters": "mint", "species": "peach", "metas": "butter",
            "models": "sky", "circles": "aqua"}


def tag_color(typ: str, st) -> str:
    """The colour of a kind of tag. The colours Packrat starts with become the matching pastel (artists rose,
    series orchid, characters mint ...); a colour you picked yourself in Settings is used exactly as it is."""
    from .. import uikit
    from ..grab.settings import TAG_COLORS
    key = uikit.TYPE_TO_COLOR_KEY.get(typ or "general", "generals")
    colors = st.get("Coloring") or {}
    mine = str(colors.get(key) or "").lower() if isinstance(colors, dict) else ""
    if key in _TAG_HUE and mine in ("", dict(TAG_COLORS).get(key, "")):
        return C[_TAG_HUE[key] + "_ink"]
    return uikit.tag_color(typ, st, DARK[0])


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
    for lb in root.findChildren(QtWidgets.QLabel):
        spec = lb.property("tile")
        if spec:
            name, hue_name, size = str(spec).split("|")
            lb.setPixmap(tile(name, hue_name, int(size)))
        elif isinstance(lb, Empty):
            lb.retheme()


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


class Flow(QtWidgets.QLayout):
    """A row of buttons that goes on to a second line when the window is too narrow for it, instead of cutting the
    buttons off. addStretch() marks a gap: what is added after it sits at the right end of its line."""

    def __init__(self, spacing: int = 6):
        super().__init__()
        self._items: list = []
        self._gap: int | None = None
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item):
        self._items.append(item)

    def addStretch(self, _factor: int = 1):
        if self._gap is None:
            self._gap = len(self._items)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        if not 0 <= i < len(self._items):
            return None
        if self._gap is not None and i < self._gap:
            self._gap -= 1
        return self._items.pop(i)

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._place(QtCore.QRect(0, 0, width, 0), False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._place(rect, True)

    def sizeHint(self):          # everything on one line
        shown = [it.sizeHint() for it in self._items if not it.isEmpty()]
        m = self.contentsMargins()
        return QtCore.QSize(sum(s.width() for s in shown) + self.spacing() * max(0, len(shown) - 1) + m.left() + m.right(),
                            max((s.height() for s in shown), default=0) + m.top() + m.bottom())

    def minimumSize(self):       # the widest single button
        shown = [it.sizeHint() for it in self._items if not it.isEmpty()]
        m = self.contentsMargins()
        return QtCore.QSize(max((s.width() for s in shown), default=0) + m.left() + m.right(),
                            max((s.height() for s in shown), default=0) + m.top() + m.bottom())

    def _place(self, rect, move: bool) -> int:
        m, sp = self.contentsMargins(), self.spacing()
        width = rect.width() - m.left() - m.right()
        lines, cur, used = [], [], 0
        for i, it in enumerate(self._items):
            if it.isEmpty():         # a hidden button takes no room
                continue
            w = min(it.sizeHint().width(), max(width, 1))
            if cur and used + sp + w > width:
                lines.append(cur)
                cur, used = [], 0
            used += (sp if cur else 0) + w
            cur.append((i, it, w))
        if cur:
            lines.append(cur)
        y = rect.y() + m.top()
        for line in lines:
            h = max(it.sizeHint().height() for _i, it, _w in line)
            free = max(0, width - sum(w for _i, _it, w in line) - sp * (len(line) - 1))
            x = rect.x() + m.left()
            for i, it, w in line:
                if free and self._gap is not None and i >= self._gap:
                    x, free = x + free, 0
                if move:
                    ih = it.sizeHint().height()
                    it.setGeometry(QtCore.QRect(x, y + (h - ih) // 2, w, ih))
                x += w + sp
            y += h + sp
        return y - (sp if lines else 0) - rect.y() + m.bottom()


def flow(*items, spacing: int = 6) -> Flow:
    """Like hbox, for a row of buttons that may wrap. None is the gap (see Flow)."""
    lay = Flow(spacing)
    for it in items:
        if it is None:
            lay.addStretch()
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


class Empty(QtWidgets.QLabel):
    """What a page shows while there is nothing in it: its sign, a few words, and what to do next."""

    def __init__(self, ico: str = "", hue_name: str = "lilac"):
        super().__init__()
        self.ico, self.hue_name = ico, hue_name
        self.parts = ("", "")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setOpenExternalLinks(False)

    def retheme(self) -> None:
        set_empty(self, *self.parts)

    def _picture(self) -> str:
        if not self.ico:
            return ""
        p = paths.cache_dir("ui") / f"empty-{self.ico}-{self.hue_name}-{'dark' if DARK[0] else 'light'}.png"
        try:
            tile(self.ico, self.hue_name, 72, round_=0.5).save(str(p), "PNG")
        except Exception:  # noqa: BLE001 - no picture then
            return ""
        src = str(p).replace("\\", "/")
        return f'<div style="margin-bottom:14px;"><img src="{src}" width="72" height="72"></div>'


def empty_state(title: str, text: str = "", ico: str = "", hue: str = "lilac") -> Empty:
    lb = Empty(ico, PAGE_HUE.get(hue, hue))
    set_empty(lb, title, text)
    return lb


def set_empty(lb: QtWidgets.QLabel, title: str, text: str = "") -> None:
    pic = ""
    if isinstance(lb, Empty):
        lb.parts = (title, text)
        pic = lb._picture()
    lb.setText(f'{pic}<div style="font-size:17pt; {display_css()}">{title}</div>'
               f'<div style="color:{C["muted"]}; line-height:150%; margin-top:10px;">{text}</div>')


def esc(s) -> str:
    import html
    return html.escape(str(s if s is not None else ""))
