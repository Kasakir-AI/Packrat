"""The Qt window: pieces that can be checked without showing anything."""
import importlib
import os
import sys

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_every_window_module_imports():
    for name in ("app", "archive", "browse", "common", "help", "pages", "settings", "sites", "smoke", "tools", "viewer"):
        importlib.import_module(f"packrat.qt.{name}")


def test_style_sheet_has_every_colour():
    from packrat.qt import common as K
    for name, pal in K.PALETTES.items():
        text = K.QSS % {**pal, "img_down": "d.png", "img_up": "u.png", "img_check": "c.png"}
        assert "%(" not in text, name
    assert set(K.PALETTES["dark"]) == set(K.PALETTES["light"])


def test_every_page_has_a_colour_and_every_colour_all_its_tones():
    from packrat.qt import common as K
    from packrat.qt.app import NAV
    assert {key for key, _text, _ico in NAV} == set(K.PAGE_HUE) and set(K.PAGE_HUE.values()) <= set(K.HUES)
    for pal in K.PALETTES.values():
        assert all(f"{h}_{t}" in pal for h in K.HUES for t in K.TONES)
    assert all(f'[hue="{h}"]' in K.QSS for h in K.HUES)


def test_bundled_typefaces_are_there_with_their_license():
    from packrat.qt import common as K
    for _role, files in K._FONT_FILES:
        assert all((K.ASSETS / f).is_file() for f in files)
    assert (K.ASSETS / "FONTS-LICENSE.txt").is_file()


def test_tag_colours_follow_the_theme_unless_you_chose_your_own():
    from packrat.qt import common as K

    class St:
        def __init__(self, colors):
            self.colors = colors

        def get(self, _key):
            return self.colors
    K.C.clear()
    K.C.update(K.PALETTES["light"])
    K.DARK[0] = False
    assert K.tag_color("artist", St({"artists": "#aa0000"})) == K.C["rose_ink"]       # the colour Packrat starts with
    assert K.tag_color("artist", St({"artists": "#123456"})) == "#123456"             # one you picked
    assert K.tag_color("general", St({})) == "#000000"                                # "no colour": the text colour


def test_guide_markup_becomes_html():
    from packrat.qt.help import guide_html, slug
    html = guide_html("# Title\n\n## Keys here\n- **S**: save `%md5%`\n- [Browse](go:tab:browse)\n\n1. one\n   more\n2. two")
    assert "<h1" in html and '<a name="keys-here">' in html and "<b>S</b>" in html
    assert 'href="go:tab:browse"' in html and "<ol>" in html and "one more" in html
    assert slug("Keys here!") == "keys-here"


def test_every_guide_renders():
    from packrat import guides
    from packrat.qt.help import guide_html
    for tid, _title, text in guides.TOPICS:
        assert guide_html(text).count("<h1") == 1, tid


def test_shortcut_script_quotes_paths():
    from packrat.winshell import shortcut_script
    s = shortcut_script("C:\\Users\\O'Neil\\Desktop\\Packrat.lnk", "C:\\P\\pythonw.exe", "-m packrat gui", "C:\\P", "C:\\P\\i.ico")
    assert "O''Neil" in s and "$lnk.Arguments = '-m packrat gui'" in s and "i.ico,0" in s


def test_built_program_ignores_dash_m_packrat(monkeypatch):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    import packrat_entry
    seen = {}
    monkeypatch.setattr("packrat.cli.main", lambda a: seen.setdefault("args", a) and 0 or 0)
    monkeypatch.setattr(sys, "argv", ["packrat-cli.exe", "-m", "packrat", "batch", "--ids", "3"])
    assert packrat_entry.main() == 0 and seen["args"] == ["batch", "--ids", "3"]


def test_settings_every_kind_has_a_field():
    from packrat.grab import settings as S
    known = {"text", "int", "float", "bool", "choice", "dir", "file", "color", "lines", "filename", "list", "hidden", "secret"}
    assert {s.key for s in S.SETTINGS if s.kind not in known} == set()
    from packrat.qt.settings import BASICS, LIST_EDITORS
    assert all(k in S.BY_KEY for k, *_ in BASICS)
    lists = {s.key for s in S.SETTINGS if s.kind == "list"}
    assert lists - set(LIST_EDITORS) == {"Coloring"}


def test_search_words_are_coloured_without_changing_the_text(tmp_path):
    from types import SimpleNamespace

    from PySide6 import QtCore, QtWidgets

    from packrat.grab.settings import Settings
    from packrat.qt import common as K
    from packrat.qt.browse import SearchColors
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["test"])
    st = Settings(tmp_path / "settings.json")
    st.set("blacklistedtags", "gore\ncat dog")
    lib = SimpleNamespace(favorites=lambda: [{"name": "Landscape"}], later=lambda: ["someday"])
    app = SimpleNamespace(st=st, lib=lib, tag_types={"pikachu": "character"}, store=SimpleNamespace(get=lambda h: None),
                          worker=None)
    edit = QtWidgets.QLineEdit()
    colors = SearchColors(edit, app, lambda: [])
    favs, later, black = {"landscape"}, {"someday"}, {"gore"}
    assert colors.color_of("-gore", favs, later, black) == K.C["error"]
    assert colors.color_of("landscape", favs, later, black) == K.C["fav"]
    assert colors.color_of("~someday", favs, later, black) == K.C["warn"]
    assert colors.color_of("pikachu", favs, later, black) not in (None, K.C["fav"], K.C["error"])
    assert colors.color_of("rating:safe", favs, later, black) is not None        # special words: the meta colour
    assert colors.color_of("smile", favs, later, black) is None                  # unknown / general: normal colour
    assert colors.color_of("https://e621.net/posts/1", favs, later, black) is None
    edit.setText("pikachu landscape -gore smile")
    edit.setSelection(8, 9)
    colors.apply()
    qapp.processEvents()
    assert edit.text() == "pikachu landscape -gore smile" and edit.selectedText() == "landscape"
    assert edit.isUndoAvailable() is False                                       # colouring is not an edit
    ev = QtCore.QEvent(QtCore.QEvent.Type.InputMethod)                           # not ours: only watched, never eaten
    assert colors.eventFilter(edit, ev) is False
    st.set("colorSearchWords", False)
    colors.apply()
    assert edit.text() == "pikachu landscape -gore smile"


def test_opening_again_brings_the_window_back():
    """Mac: Spotlight / Finder / Dock re-activate the running Packrat; a window hidden in the menu bar comes back,
    a visible one is left alone, and the activation that comes with starting ('start hidden') is ignored."""
    from types import SimpleNamespace

    from packrat.qt.app import MainWindow
    from packrat.qt.common import Qt
    shown = []
    win = SimpleNamespace(_reopen_armed=True, isVisible=lambda: False, isMinimized=lambda: False,
                          bring_up=lambda: shown.append(1))
    active = Qt.ApplicationState.ApplicationActive
    MainWindow._app_state(win, active)
    assert shown == [1]
    MainWindow._app_state(win, Qt.ApplicationState.ApplicationInactive)
    win.isVisible = lambda: True
    MainWindow._app_state(win, active)
    win.isVisible, win._reopen_armed = (lambda: False), False
    MainWindow._app_state(win, active)
    assert shown == [1]
