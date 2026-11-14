"""The Settings page. Every setting in grab/settings.py is shown from its description there: a page list on the
left, the fields on the right, a box to find a setting by name. Changes are saved as you make them."""
from __future__ import annotations

import os
from dataclasses import replace

from PySide6 import QtCore, QtGui, QtWidgets

from .. import uikit
from ..grab import settings as S
from . import common as K
from .common import C, Qt

LIST_EDITORS = {
    "Filenames": (("cond", "Condition (tags or a token test)"), ("fn", "File name"), ("dir", "Folder")),
    "Customs": (("name", "Token name"), ("tags", "Tags (space-separated)")),
    "WebServices": (("name", "Name"), ("url", "Address ({url} {md5} {id} {page})")),
    "Save/ImageConversions": (("from", "From extension"), ("to", "To extension")),
    "Save/MetadataExiftool": (("key", "ExifTool tag (e.g. XMP:Subject)"), ("value", "Value (tokens)")),
    "Save/MetadataPropsys": (("key", "Property (e.g. System.Keywords)"), ("value", "Value (tokens)")),
    "LogFiles": (("name", "Name"), ("locationType", "Where: 0 folder+name, 1 one file, 2 next to the picture (+suffix), "
                                                    "3 next to it (no extension)"), ("path", "Folder (0)"),
                 ("filename", "File name (0)"), ("uniquePath", "File (1)"), ("suffix", "Suffix (2)"),
                 ("suffixWithoutExtension", "Suffix (3)"), ("content", "Line written (tokens)")),
}
BASICS = (
    ("username", "Your user name on e621 / e926 / e6ai",
     "Optional. These sites ask every program to say who is using it. Leave it empty if you have no account.", "You"),
    ("Save/path", "Save pictures in",
     "Empty = the 'downloads' folder inside the Packrat folder. The names below can add folders inside it.", "Saving pictures"),
    ("Save/filename", "Name files like this", "Pick one with Presets, or write your own (Help > File names).", "Saving pictures"),
    ("Save/md5Duplicates", "If you already have a picture",
     "Packrat remembers every picture it saved, by its fingerprint, even when it comes from another site.", "Saving pictures"),
    ("Save/downloadoriginals", "Download full-size pictures",
     "Off: download the smaller version some sites offer. Faster, and takes less space.", "Saving pictures"),
    ("blacklistedtags", "Never show or download pictures with these tags",
     "One per line, e.g.  gore  or  rating:explicit . Several tags on one line: only pictures that have all of them. "
     "Quick way: right-click a tag in Browse > Blacklist.", "Pictures you never want"),
    ("hideblacklisted", "Hide those pictures completely",
     "Off: they are shown greyed out, so you can see that something was left out.", "Pictures you never want"),
    ("thumbnailSize", "Preview size (pixels)", "How big pictures are in the search results. New tabs use the new size.", "Looks"),
    ("limit", "Pictures per page", "How many pictures each site sends per page (most allow up to 100).", "Looks"),
    ("start", "When Packrat opens", "", "Looks"),
    ("Monitoring/closeToTray", "Closing the window keeps Packrat running", "", "Looks"),
    ("confirm_close", "Ask before closing while downloading",
     "Off: downloads are stopped cleanly and the window closes. Start continues next time.", "Looks"),
)
BASIC_CHOICES = {"Save/md5Duplicates": (("ignore", "Skip it (recommended)"), ("save", "Download it again"),
                                        ("copy", "Copy the file you already have"))}


class ListEditor(QtWidgets.QDialog):
    """Rows of named columns (conditional file names, custom tokens, web services...)."""

    def __init__(self, parent, title: str, cols, rows: list):
        super().__init__(parent)
        self.cols = cols
        self.setWindowTitle(title)
        K.fit(self, 900, 460)
        self.table = QtWidgets.QTableWidget(0, len(cols))
        self.table.setHorizontalHeaderLabels([c[1] for c in cols])
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        for r in rows or []:
            self._add(r if isinstance(r, dict) else {})
        ok = K.button("Save", self.accept, accent=True)
        self.setLayout(K.vbox((self.table, 1), K.hbox(
            K.button("Add a row", lambda: self._add({}), ico="plus"), K.button("Remove the selected row", self._remove),
            K.button("Up", lambda: self._move(-1)), K.button("Down", lambda: self._move(1)), None,
            K.button("Cancel", self.reject), ok), margins=(14, 12, 14, 12)))

    def _add(self, d: dict):
        r = self.table.rowCount()
        self.table.insertRow(r)
        for c, (key, _lab) in enumerate(self.cols):
            self.table.setItem(r, c, QtWidgets.QTableWidgetItem(str(d.get(key, "") if d.get(key) is not None else "")))

    def _remove(self):
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)

    def _move(self, d: int):
        r = self.table.currentRow()
        j = r + d
        if r < 0 or not 0 <= j < self.table.rowCount():
            return
        for c in range(self.table.columnCount()):
            a, b = self.table.takeItem(r, c), self.table.takeItem(j, c)
            self.table.setItem(r, c, b)
            self.table.setItem(j, c, a)
        self.table.setCurrentCell(j, 0)

    def rows(self) -> list[dict]:
        out = []
        for r in range(self.table.rowCount()):
            d = {key: (self.table.item(r, c).text().strip() if self.table.item(r, c) else "")
                 for c, (key, _lab) in enumerate(self.cols)}
            if any(d.values()):
                out.append(d)
        return out


class SettingsPage(QtWidgets.QWidget):
    def __init__(self, app):
        super().__init__()
        self.app, self.st = app, app.st
        self.setProperty("hue", K.PAGE_HUE["settings"])
        self._narrow = False
        self._forms, self._titles, self._spacers = [], [], []     # of the page that is shown; see _set_narrow
        self.pending: dict = {}
        self.timer = QtCore.QTimer(self, singleShot=True, interval=500, timeout=self._flush)
        self.find = QtWidgets.QLineEdit()
        self.find.setPlaceholderText("Find a setting...")
        self.find.setClearButtonEnabled(True)
        self.find.textChanged.connect(self._find)
        self.names = ["Basics", *S.PAGES]
        self.list = QtWidgets.QListWidget()
        self.list.addItems(self.names)
        self.list.setFixedWidth(200)
        self.list.currentRowChanged.connect(lambda i: self.show_named(self.names[i]) if i >= 0 and not self._quiet else None)
        self._quiet = False
        self.title = K.label("", role="title")
        self.intro = K.label("", muted=True, wrap=True)
        self.saved = K.label("", muted=True)
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.reset_btn = K.button("Reset this page", self.reset_page, tip="Put every setting on this page back to its default")
        head = K.hbox(K.tile_label("sliders", K.PAGE_HUE["settings"], 44),
                      (K.vbox(K.hbox(self.title, None, self.saved, self.reset_btn), self.intro, spacing=1), 1), spacing=14)
        right = K.vbox(head, (self.scroll, 1), spacing=10)
        left = K.vbox(self.find, (self.list, 1), spacing=8)
        self.setLayout(K.hbox(left, (right, 1), margins=(22, 18, 22, 14), spacing=18))
        self.current = "Basics"
        self.list.setCurrentRow(0)
        self.show_named("Basics")

    # ---- which rows ------------------------------------------------------------------------------
    def basics_rows(self):
        rows = []
        for key, label, help_, group in BASICS:
            s = S.BY_KEY.get(key)
            if s is None:
                continue
            kw = {"label": label, "help": help_ or s.help, "group": group, "page": "Basics"}
            ch = BASIC_CHOICES.get(key)
            if ch and str(self.st.get(key)) in [str(c[0]) for c in ch]:
                kw["choices"] = ch
            rows.append(replace(s, **kw))
        return rows

    INTRO = {"Basics": "The everyday settings. The pages on the left have everything else.",
             "File names": "How saved files are named. A / makes a folder. Help > File names explains every token.",
             "Blacklist & tags": "Pictures you never want, and tags to leave out of file names.",
             "Monitoring": "Searches that check themselves, and Packrat's icon next to the clock.",
             "Network": "Proxy, the name Packrat gives to sites, reverse image search services.",
             "Sources": "Which of a site's interfaces Packrat tries first."}

    def show_named(self, name: str):
        self._flush()
        name = name if name in self.names else "Basics"
        self.current = name
        self._quiet = True
        self.list.setCurrentRow(self.names.index(name))
        self._quiet = False
        rows = self.basics_rows() if name == "Basics" else [s for s in S.SETTINGS if s.page == name and s.kind != "hidden"]
        self._render(rows, name, self.INTRO.get(name, ""), basics=name == "Basics")

    def _find(self, text: str):
        t = text.strip().lower()
        if not t:
            self.show_named(self.current)
            return
        self._flush()
        hits = [s for s in S.SETTINGS if s.kind != "hidden" and (t in s.label.lower() or t in s.key.lower()
                                                                  or t in (s.help or "").lower() or t in s.group.lower())]
        self._render(hits[:80], f"Settings matching '{text.strip()}'", f"{len(hits)} found" + (
            " (showing the first 80)" if len(hits) > 80 else ""), with_page=True)

    def reload(self):
        self.pending.clear()
        if self.find.text().strip():
            self._find(self.find.text())
        else:
            self.show_named(self.current)

    def themed(self):
        self.reload()

    # ---- drawing ---------------------------------------------------------------------------------
    def _render(self, rows, title, intro, with_page=False, basics=False):
        self.title.setText(title)
        self.intro.setText(intro)
        self.intro.setVisible(bool(intro))
        self.reset_btn.setVisible(not with_page)
        body = QtWidgets.QVBoxLayout()
        body.setContentsMargins(0, 4, 12, 12)
        body.setSpacing(14)
        self._forms, self._titles, self._spacers = [], [], []
        groups: dict[str, QtWidgets.QFormLayout] = {}
        for s in rows:
            gname = f"{s.page}  >  {s.group}" if with_page else s.group
            form = groups.get(gname)
            if form is None:
                box = QtWidgets.QGroupBox(gname)
                form = QtWidgets.QFormLayout(box)
                form.setVerticalSpacing(10)
                form.setHorizontalSpacing(16)
                form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
                groups[gname] = form
                self._forms.append(form)
                body.addWidget(box)
            w = self._field(s)
            if w is None:
                continue
            if s.help:
                w.setToolTip(s.help)
            if s.kind == "bool":
                sp = QtWidgets.QLabel("")
                sp.setMinimumWidth(190)
                self._spacers.append(sp)
                form.addRow(sp, w)
            else:
                lab = QtWidgets.QLabel(s.label)
                lab.setToolTip(s.help or "")
                lab.setMinimumWidth(190)
                lab.setWordWrap(True)
                lab.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self._titles.append(lab)
                form.addRow(lab, w)
            if basics and s.help:
                hl = K.label(s.help, muted=True, wrap=True)
                hl.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Preferred)
                hl.setMinimumWidth(240)
                sp = QtWidgets.QLabel(" ")
                self._spacers.append(sp)
                form.addRow(sp, hl)
        if not rows:
            body.addWidget(K.label("Nothing found.", muted=True))
        body.addStretch(1)
        holder = QtWidgets.QWidget()
        holder.setLayout(body)
        holder.setMaximumWidth(K.FORM_MAX_WIDTH)   # a 2000 px wide text box is hard to read
        self._set_narrow(self._narrow)
        self.scroll.setWidget(holder)

    def _set_narrow(self, narrow: bool):
        """In a narrow window a setting's name goes above its field instead of beside it, so the field is not cut
        off at the right."""
        self._narrow = narrow
        self.list.setFixedWidth(150 if narrow else 200)
        wrap = QtWidgets.QFormLayout.RowWrapPolicy
        side = Qt.AlignmentFlag.AlignLeft if narrow else Qt.AlignmentFlag.AlignRight
        for form in self._forms:
            form.setRowWrapPolicy(wrap.WrapAllRows if narrow else wrap.DontWrapRows)
        for lab in self._titles:
            lab.setMinimumWidth(0 if narrow else 190)
            lab.setWordWrap(not narrow)     # above its field a name has the whole line to itself
            lab.setAlignment(side | Qt.AlignmentFlag.AlignVCenter)
        for sp in self._spacers:        # empty cells that only line the two columns up
            sp.setVisible(not narrow)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        narrow = self.width() < 800
        if narrow != self._narrow:
            self._set_narrow(narrow)

    def _changed(self, key, value):
        self.pending[key] = value
        self.saved.setText("")
        self.timer.start()

    def _flush(self):
        if not self.pending:
            return
        vals, self.pending = self.pending, {}
        secrets = {k: v for k, v in vals.items() if k in S.SECRET_KEYS}
        plain = {k: v for k, v in vals.items() if k not in secrets}
        if plain:
            self.st.update(plain)
        for k, v in secrets.items():
            self.st.set(k, v)
        self.saved.setText("Saved")
        QtCore.QTimer.singleShot(2500, lambda: self.saved.setText(""))
        if {"Save/path", "Save/filename", "sites"} & set(vals):
            self.app.browse.settings_changed()

    def _field(self, s) -> QtWidgets.QWidget | None:
        st, key, kind = self.st, s.key, s.kind
        val = st.secret(key) if kind == "secret" else st.get(key)
        if kind == "bool":
            w = QtWidgets.QCheckBox(s.label)
            w.setChecked(bool(val))
            w.toggled.connect(lambda on, k=key: self._changed(k, bool(on)))
            return w
        if kind in ("int", "float"):
            w = QtWidgets.QSpinBox() if kind == "int" else QtWidgets.QDoubleSpinBox()
            lo = s.lo if s.lo is not None else (-10 ** 9 if kind == "int" else -1e9)
            hi = s.hi if s.hi is not None else (10 ** 9 if kind == "int" else 1e9)
            w.setRange(int(lo) if kind == "int" else lo, int(min(hi, 2_000_000_000)) if kind == "int" else hi)
            try:
                w.setValue(int(val) if kind == "int" else float(val))
            except (TypeError, ValueError):
                pass
            w.setMaximumWidth(160)
            w.valueChanged.connect(lambda v, k=key: self._changed(k, v))
            return w
        if kind == "choice":
            w = QtWidgets.QComboBox()
            for v, lab in s.choices:
                w.addItem(str(lab), v)
            i = next((i for i, (v, _l) in enumerate(s.choices) if str(v) == str(val)), -1)
            if i < 0:
                w.addItem(str(val), val)
                i = w.count() - 1
            w.setCurrentIndex(i)
            w.setMaximumWidth(420)
            w.currentIndexChanged.connect(lambda _i, k=key, w=w: self._changed(k, w.currentData()))
            return w
        if kind == "lines":
            w = QtWidgets.QPlainTextEdit("\n".join(val) if isinstance(val, list) else str(val or ""))
            w.setMinimumHeight(110)
            w.setMaximumHeight(180)
            w.textChanged.connect(lambda k=key, w=w: self._changed(k, w.toPlainText()))
            return w
        if kind == "list":
            return self._list_field(s, val)
        ed = QtWidgets.QLineEdit("" if val is None else str(val))
        if kind == "secret":
            ed.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
            ed.setPlaceholderText("kept in the Windows Credential Manager")
        ed.textChanged.connect(lambda t, k=key: self._changed(k, t if kind in ("secret", "filename") else t.strip()))
        if kind == "dir":
            if key == "Save/path":
                ed.setPlaceholderText(f"empty = {st.save_path() if not val else ''}".strip())
            return K.widget(K.hbox((ed, 1), K.button("Browse...", lambda: self._pick_dir(ed)),
                                   K.button("Open", lambda: self._open_dir(ed), ico="folder")))
        if kind == "file":
            return K.widget(K.hbox((ed, 1), K.button("Browse...", lambda: self._pick_file(ed))))
        if kind == "color":
            return K.widget(K.hbox((ed, 1), K.button("Pick...", lambda: self._pick_color(ed))))
        if kind == "filename":
            ex = K.label("", muted=True, wrap=True)

            def show(_t=None):
                fmt = ed.text().strip()
                if not fmt and key != "Save/filename":
                    ex.setText("empty = the same as the usual file name")
                    K.restyle(ex, role="", muted=True)
                    return
                path, problem = uikit.sample_example(fmt or "%md5%.%ext%", st, st.save_path())
                ex.setText(("Example: " + path) if path else problem)
                K.restyle(ex, role="" if path else "error", muted=bool(path))
            menu = QtWidgets.QMenu(self)
            for lab, fmt in uikit.PRESETS:
                menu.addAction(f"{lab}      {fmt}", lambda f=fmt: ed.setText(f))
            ed.textChanged.connect(show)
            show()
            return K.widget(K.vbox(K.hbox((ed, 1), K.button("Presets", menu=menu)), ex, spacing=4))
        return ed

    def _list_field(self, s, val):
        key = s.key
        if key == "Coloring":
            colors = dict(val) if isinstance(val, dict) else dict(S.TAG_COLORS)
            grid = QtWidgets.QGridLayout()
            for i, (name, default) in enumerate(S.TAG_COLORS):
                b = QtWidgets.QPushButton(name)

                def paint(b=b, name=name, default=default):
                    col = colors.get(name) or default
                    fg = "#ffffff" if QtGui.QColor(col).lightness() < 140 else "#000000"
                    b.setStyleSheet(f"background:{col}; color:{fg}; border:1px solid {C['border']}; border-radius:6px; padding:5px 10px;")

                def pick(_=False, name=name, default=default, paint=paint):
                    c = QtWidgets.QColorDialog.getColor(QtGui.QColor(colors.get(name) or default), self, f"Colour of {name}")
                    if c.isValid():
                        colors[name] = c.name()
                        paint()
                        self._changed("Coloring", dict(colors))
                        self.app.browse.themed()
                b.clicked.connect(pick)
                paint()
                grid.addWidget(b, i // 4, i % 4)
            return K.widget(grid)
        cols = LIST_EDITORS.get(key)
        if cols is None:
            return K.label("(edited elsewhere)", muted=True)
        rows = list(val) if isinstance(val, list) else []
        lab = K.label("")

        def show():
            lab.setText(f"{len(rows)} row{'s' if len(rows) != 1 else ''}")

        def edit():
            dlg = ListEditor(self, s.label, cols, rows)
            if dlg.exec():
                rows[:] = dlg.rows()
                show()
                self._changed(key, list(rows))
        show()
        return K.widget(K.hbox(lab, K.button("Edit...", edit), None))

    def _pick_dir(self, ed):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose a folder", ed.text() or self.st.save_path())
        if d:
            ed.setText(os.path.normpath(d))

    def _open_dir(self, ed):
        p = ed.text().strip() or self.st.save_path()
        os.makedirs(p, exist_ok=True)
        uikit.open_path(p)

    def _pick_file(self, ed):
        p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Choose a file", ed.text() or "")
        if p:
            ed.setText(os.path.normpath(p))

    def _pick_color(self, ed):
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(ed.text() or "#000000"), self)
        if c.isValid():
            ed.setText(c.name())

    def reset_page(self):
        name = self.current
        keys = [k for k, *_ in BASICS] if name == "Basics" else [s.key for s in S.SETTINGS if s.page == name
                                                                 and s.kind not in ("hidden", "secret")]
        if K.ask(self, "Reset", f"Put the {len(keys)} settings on '{name}' back to their defaults?", "Reset", "Cancel",
                 default_yes=False):
            self.pending.clear()
            self.st.reset(keys)
            self.reload()
            self.app.browse.settings_changed()
