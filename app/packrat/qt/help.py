"""The built-in help (guides, search in your own words), the first-start assistant and About."""
from __future__ import annotations

import html
import os
import re

from PySide6 import QtCore, QtGui, QtWidgets

from .. import __version__, helpsearch, uikit
from .. import guides as G
from . import common as K
from .common import C, Qt

_INLINE = re.compile(r"(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\)|\*[^*\s][^*]*\*)")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _inline(s: str) -> str:
    out = []
    for part in _INLINE.split(s):
        if not part:
            continue
        if part.startswith("**"):
            out.append(f"<b>{html.escape(part[2:-2])}</b>")
        elif part.startswith("`"):
            out.append(f'<span style="font-family:Consolas,monospace; background:{C["raised"]};">&nbsp;{html.escape(part[1:-1])}&nbsp;</span>')
        elif part.startswith("["):
            m = re.match(r"\[([^\]]+)\]\(([^)]+)\)", part)
            label, target = m.group(1), m.group(2)
            out.append(f'<a style="color:{C["link"]}; text-decoration:none;" href="{html.escape(target)}">{html.escape(label)}</a>')
        elif part.startswith("*"):
            out.append(f"<i>{html.escape(part[1:-1])}</i>")
        else:
            out.append(html.escape(part))
    return "".join(out)


def guide_html(text: str) -> str:
    """The guides' small markup (# title, ## heading, - bullet, 1. step, **bold**, `example`, [label](go:x)) as HTML."""
    out, para, items, kind = [], [], [], None

    def flush():
        nonlocal kind
        if para:
            out.append(f'<p style="line-height:150%;">{_inline(" ".join(para))}</p>')
            para.clear()
        if items:
            tag = "ol" if kind == "ol" else "ul"
            out.append(f"<{tag}>" + "".join(f'<li style="line-height:150%; margin-bottom:4px;">{_inline(i)}</li>' for i in items)
                       + f"</{tag}>")
            items.clear()
        kind = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush()
        elif line.startswith("# "):
            flush()
            out.append(f'<h1 style="font-size:19pt; {K.display_css()}">{_inline(line[2:])}</h1>')
        elif line.startswith("## "):
            flush()
            out.append(f'<a name="{slug(line[3:])}"></a><h2 style="font-size:13pt; {K.display_css()} margin-top:18px;">'
                       f"{_inline(line[3:])}</h2>")
        elif line.startswith("- "):
            if kind != "ul":
                flush()
            kind = "ul"
            items.append(line[2:])
        elif re.match(r"^\d+\. ", line):
            if kind != "ol":
                flush()
            kind = "ol"
            items.append(line.split(". ", 1)[1])
        elif raw.startswith(" ") and items:
            items[-1] += " " + line.strip()
        else:
            if items:
                flush()
            para.append(line.strip())
    flush()
    return f'<div style="color:{C["text"]};">' + "\n".join(out) + "</div>"


class GuideView(QtWidgets.QTextBrowser):
    def __init__(self, app, on_go=None):
        super().__init__()
        self.app, self.on_go, self._text = app, on_go, ""
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.anchorClicked.connect(self._clicked)
        self.document().setDocumentMargin(14)

    def set_text(self, text: str, anchor: str = ""):
        self._text = text
        self.setHtml(guide_html(text))
        if anchor:
            self.scrollToAnchor(slug(anchor))

    def retheme(self):
        self.set_text(self._text)

    def _clicked(self, url: QtCore.QUrl):
        target = url.toString()
        if target.startswith("go:"):
            target = helpsearch.unlink(QtCore.QUrl.fromPercentEncoding(target[3:].encode()))
            (self.on_go or self.app.go)(target)
        elif target.startswith(("http://", "https://")):
            uikit.open_url(target)


class HelpWindow(QtWidgets.QWidget):
    def __init__(self, app):
        super().__init__(None, Qt.WindowType.Window)
        self.app = app
        self.index = None
        self.setWindowTitle("Packrat - Help")
        self.setWindowIcon(K.app_icon())
        K.fit(self, 1000, 720)
        self.ask = QtWidgets.QLineEdit()
        self.ask.setObjectName("search")
        self.ask.setPlaceholderText("Ask in your own words, e.g.  how do I save a whole search   - then press Enter")
        self.ask.setClearButtonEnabled(True)
        self.ask.returnPressed.connect(self.search)
        self.topics = QtWidgets.QListWidget()
        for tid, title, _ in G.TOPICS:
            it = QtWidgets.QListWidgetItem(title)
            it.setData(Qt.ItemDataRole.UserRole, tid)
            self.topics.addItem(it)
        self.topics.currentItemChanged.connect(lambda it, _p: self._show_topic(it.data(Qt.ItemDataRole.UserRole)) if it
                                               and not self._quiet else None)
        self.topics.setFixedWidth(270)
        self.topics.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.topics.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._quiet = False
        self.view = GuideView(app, on_go=self._go)
        side = K.vbox((self.topics, 1), K.button("Run the setup assistant again", lambda: app.run_setup()),
                      K.button("About Packrat", lambda: AboutDialog(self, app).exec()), spacing=6)
        self.setLayout(K.vbox(self.ask, K.hbox(side, (self.view, 1), spacing=10), margins=(14, 12, 14, 12), spacing=10))
        QtGui.QShortcut(QtGui.QKeySequence("Escape"), self, activated=self.close)

    def _go(self, target: str):
        if target.startswith(("help:", "site:")):
            self.open(target[5:] if target.startswith("help:") else target)
        else:
            self.app.go(target)

    def _show_topic(self, tid: str, anchor: str = ""):
        text = next((t for i, _title, t in G.TOPICS if i == tid), None)
        if text is None:
            text = f"# Not found\n\nThere is no guide called '{tid}'."
        if tid == "sites":
            text += "\n\n" + G.sites_index([(s.host, s.source.name) for s in self.app.store.all_sites()])
        self.view.set_text(text, anchor)

    def open(self, target: str = "start"):
        target = target or "start"
        if target.startswith("site:"):
            s = self.app.store.get(target[5:])
            self.view.set_text(G.site_text(target[5:], s.source.name if s else "", login_saved=bool(s and s.has_credentials())))
        else:
            tid, _, anchor = target.partition("#")
            self._quiet = True
            for i in range(self.topics.count()):
                if self.topics.item(i).data(Qt.ItemDataRole.UserRole) == tid:
                    self.topics.setCurrentRow(i)
            self._quiet = False
            self._show_topic(tid, anchor)
        self.show()
        self.raise_()
        self.activateWindow()

    def search(self):
        q = self.ask.text().strip()
        if not q:
            return
        if self.index is None:
            from ..grab.settings import SETTINGS
            self.index = helpsearch.build([(s.host, s.source.name) for s in self.app.store.all_sites()], SETTINGS)
        self.view.set_text(helpsearch.results_text(q, self.index.search(q)))

    def themed(self):
        self.view.retheme()


class AboutDialog(QtWidgets.QDialog):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.setWindowTitle("About Packrat")
        logo = QtWidgets.QLabel()
        logo.setPixmap(K.app_icon().pixmap(96, 96))
        from .. import paths
        text = K.label(
            f"<div style=\"font-size:20pt; {K.display_css()}\">Packrat {__version__}</div>"
            "<p>Finds, downloads and organizes pictures from many sites, and keeps a complete copy of e6ai or e621.</p>"
            "<p>The site sources come from Grabber by Bionus "
            "(Apache License 2.0); see app\\packrat\\sources\\GRABBER-NOTICE.txt.<br>The window uses Qt for Python "
            "(PySide6, LGPL).</p>"
            f"<p style='color:{C['muted']}'>Packrat folder: {html.escape(str(paths.install_root()))}</p>", wrap=True, rich=True,
            selectable=True)
        text.setOpenExternalLinks(True)
        text.setMinimumWidth(460)
        self.setLayout(K.vbox(K.hbox(logo, (text, 1), spacing=18), K.hbox(None, K.button("Close", self.accept, accent=True)),
                              margins=(22, 20, 22, 16), spacing=14))


class SetupWizard(QtWidgets.QDialog):
    """A few questions on the first start. Everything can be changed later in Settings."""

    def __init__(self, app, first: bool = False):
        super().__init__(app)
        self.app = app
        st = app.st
        self.setWindowTitle("Welcome to Packrat" if first else "Setup assistant")
        self.setMinimumSize(640, 480)
        self.pages = QtWidgets.QStackedWidget()

        # 1 - who
        self.username = QtWidgets.QLineEdit(str(st.get("username") or ""))
        self.username.setPlaceholderText("optional")
        logo = QtWidgets.QLabel()
        logo.setPixmap(K.app_icon().pixmap(72, 72))
        p1 = K.vbox(K.hbox(logo, K.vbox(K.label("Welcome to Packrat", role="title"), K.label(
            "Packrat finds pictures on image sites, shows them, and saves the ones you want. These few questions set up "
            "the basics; everything can be changed later in Settings.", wrap=True, muted=True)), spacing=16), 10,
            K.label("Some sites (e621, e926, e6ai) ask every program to say who is using it. If you have an account there, "
                    "type your user name. Otherwise leave it empty.", wrap=True),
            K.hbox(K.label("Your user name"), (self.username, 1)), None, spacing=12)

        # 2 - sites
        self.site_boxes: dict[str, QtWidgets.QCheckBox] = {}
        chosen = set(st.get("sites") or [])
        grid = QtWidgets.QGridLayout()
        grid.setVerticalSpacing(8)
        hosts = [h for h in G.POPULAR if app.store.get(h)]
        for h in sorted(chosen):
            if h not in hosts and app.store.get(h):
                hosts.append(h)
        for i, h in enumerate(hosts):
            s = app.store.get(h)
            d = G.site_info(h, s.source.name)
            cb = QtWidgets.QCheckBox(f"{d['name']}  -  {d.get('what') or ''}  ({G.CONTENT_SHORT.get(d.get('content'), '')})")
            cb.setChecked(h in chosen)
            self.site_boxes[h] = cb
            grid.addWidget(cb, i, 0)
        p2 = K.vbox(K.label("Which sites do you want to search?", role="title"),
                    K.label("Tick one or more. Not sure? Safebooru (safe anime art) and e926 (safe furry art) need no "
                            "login. There are many more in 'Sites and logins' (bottom left of the window).", wrap=True, muted=True),
                    grid, None, spacing=12)

        # 3 - saving
        self.path = QtWidgets.QLineEdit(str(st.get("Save/path") or ""))
        self.path.setPlaceholderText(f"empty = {st.save_path()}")
        self.names = QtWidgets.QComboBox()
        for lab, fmt in uikit.PRESETS:
            self.names.addItem(f"{lab}      {fmt}", fmt)
        cur = st.filename()
        if cur not in [f for _l, f in uikit.PRESETS]:
            self.names.addItem(cur, cur)
        self.names.setCurrentIndex(self.names.findData(cur))
        self.example = K.label("", muted=True, wrap=True)
        self.names.currentIndexChanged.connect(self._example)
        self.path.textChanged.connect(self._example)
        p3 = K.vbox(K.label("Where should pictures go?", role="title"),
                    K.hbox(K.label("Save pictures in"), (self.path, 1), K.button("Browse...", self._pick)),
                    K.label("Leave it empty to use the 'downloads' folder inside the Packrat folder.", muted=True, wrap=True),
                    10, K.label("How should files be named?", role="heading"), self.names, self.example, None, spacing=10)

        # 4 - extras
        self.tray_close = QtWidgets.QCheckBox("Keep Packrat running next to the clock when I close the window "
                                              "(so monitors keep checking)")
        self.tray_close.setChecked(bool(st.get("Monitoring/closeToTray")))
        self.grabber = None
        from ..grab import grabber_import
        try:
            profiles = grabber_import.find_profiles()
        except Exception:  # noqa: BLE001
            profiles = []
        self.shortcut = QtWidgets.QCheckBox("Put a Packrat icon on my desktop and in the Start menu")
        self.shortcut.setChecked(first and os.name == "nt")
        self.shortcut.setVisible(os.name == "nt")
        p4 = K.vbox(K.label("Almost done", role="title"), self.tray_close, self.shortcut, spacing=12)
        if profiles and first:
            self.grabber = QtWidgets.QCheckBox(f"Import my settings, favorites and logins from Grabber ({profiles[0]})")
            self.grabber.setChecked(True)
            self.grabber_folder = str(profiles[0])
            p4.addWidget(self.grabber)
            p4.addWidget(K.label("Grabber itself is not changed.", muted=True))
        p4.addWidget(K.label("Help is built in: press F1 or click Help (bottom left) at any time, and type a question in "
                             "your own words.", wrap=True))
        p4.addStretch(1)

        for lay in (p1, p2, p3, p4):
            lay.setContentsMargins(26, 22, 26, 10)
            self.pages.addWidget(K.widget(lay))
        self.step = K.label("", muted=True)
        self.back = K.button("Back", lambda: self._move(-1))
        self.next = K.button("Next", lambda: self._move(1), accent=True)
        self.next.setDefault(True)
        self.setLayout(K.vbox((self.pages, 1), K.hbox(self.step, None, K.button("Skip", self.reject), self.back, self.next,
                                                      margins=(26, 8, 26, 18)), spacing=0))
        self._example()
        self._move(0)

    def _pick(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose a folder", self.path.text() or "")
        if d:
            self.path.setText(os.path.normpath(d))

    def _example(self, *_):
        ex, problem = uikit.sample_example(self.names.currentData() or "%md5%.%ext%", self.app.st,
                                           self.path.text().strip() or self.app.st.save_path())
        self.example.setText("Example: " + (ex or problem))

    def _move(self, d: int):
        i = self.pages.currentIndex() + d
        if i >= self.pages.count():
            self._finish()
            return
        if d > 0 and self.pages.currentIndex() == 1 and not any(cb.isChecked() for cb in self.site_boxes.values()):
            self.step.setText("Tick at least one site.")
            return
        i = max(0, i)
        self.pages.setCurrentIndex(i)
        self.back.setEnabled(i > 0)
        self.next.setText("Finish" if i == self.pages.count() - 1 else "Next")
        self.step.setText(f"Step {i + 1} of {self.pages.count()}")

    def _finish(self):
        st = self.app.st
        vals = {"username": self.username.text().strip(), "Save/path": self.path.text().strip(),
                "Save/filename": self.names.currentData() or "%md5%.%ext%",
                "Monitoring/closeToTray": self.tray_close.isChecked()}
        sites = [h for h, cb in self.site_boxes.items() if cb.isChecked()]
        if sites:
            vals["sites"] = sites
        st.update(vals)
        for t in self.app.browse.tabs_list():
            if not t.loaded and sites:
                t.set_sites(sites)
        if self.shortcut.isChecked() and os.name == "nt":
            try:
                from ..winshell import create_shortcuts
                create_shortcuts()
            except Exception as e:  # noqa: BLE001
                self.app.flash(f"The desktop icon could not be created: {e}")
        if self.grabber is not None and self.grabber.isChecked():
            self.app.show_page("tools")
            self.app.tools.import_grabber(self.grabber_folder)
        self.accept()
