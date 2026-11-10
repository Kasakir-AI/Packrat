"""The Browse page: search tabs with a thumbnail grid, the tag list, and the dialogs to download a search."""
from __future__ import annotations

import json
import os
import threading

from PySide6 import QtCore, QtGui, QtWidgets

from .. import uikit
from . import common as K
from .common import C, Qt

ITEM, PIX = Qt.ItemDataRole.UserRole + 1, Qt.ItemDataRole.UserRole + 2
_ROOT = QtCore.QModelIndex()


def site_names(app, hosts, limit: int = 2) -> str:
    names = [(app.store.get(h).name if app.store.get(h) else h) for h in hosts]
    if not names:
        return "none"
    return ", ".join(names[:limit]) + (f" +{len(names) - limit}" if len(names) > limit else "")


def details_html(sh) -> str:
    im = sh.image
    rows = [("Site", sh.site.name), ("Number", im.id), ("Rating", im.rating or ""), ("Score", im.score if im.score not in (None, "") else ""),
            ("Size", f"{im.width} x {im.height}" if im.width else ""), ("File", " ".join(
                x for x in ((im.ext or "").upper(), K.fmt_bytes(im.file_size) if im.file_size else "") if x)),
            ("Posted", im.date.strftime("%Y-%m-%d %H:%M") if im.date else "")]
    out = "".join(f"<tr><td style='color:{C['muted']}; padding-right:10px;'>{k}</td><td>{K.esc(v)}</td></tr>"
                  for k, v in rows if v not in ("", None))
    extra = []
    if sh.downloaded:
        extra.append(f"<span style='color:{C['ok']}'>You already have this picture</span>")
    if sh.blacklisted:
        extra.append(f"<span style='color:{C['error']}'>Blacklisted: {K.esc(', '.join(sh.blacklisted))}</span>")
    tags = " ".join(t.name for t in im.tags[:24]) + (" ..." if len(im.tags) > 24 else "")
    return (f"<table>{out}</table>" + "".join(f"<div>{x}</div>" for x in extra)
            + (f"<div style='color:{C['muted']}; margin-top:6px;'>{K.esc(tags)}</div>" if tags else ""))


# ---- the grid -------------------------------------------------------------------------------------------
class GridModel(QtCore.QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list = []
        self.pix: dict = {}
        self.failed: set = set()

    def rowCount(self, parent=_ROOT):
        return 0 if parent.isValid() else len(self.items)

    def data(self, idx, role=Qt.ItemDataRole.DisplayRole):
        if not idx.isValid() or not 0 <= idx.row() < len(self.items):
            return None
        sh = self.items[idx.row()]
        if role == ITEM:
            return sh
        if role == PIX:
            return self.pix.get(id(sh))
        if role == Qt.ItemDataRole.ToolTipRole:
            return details_html(sh)
        return None

    def set_items(self, items, append=False):
        if append:
            self.beginInsertRows(_ROOT, len(self.items), len(self.items) + len(items) - 1)
            self.items += items
            self.endInsertRows()
        else:
            self.beginResetModel()
            self.items, self.pix, self.failed = list(items), {}, set()
            self.endResetModel()

    def set_pix(self, sh, pm):
        if pm is None:
            self.failed.add(id(sh))
        else:
            self.pix[id(sh)] = pm
        try:
            i = self.items.index(sh)
        except ValueError:
            return
        ix = self.index(i)
        self.dataChanged.emit(ix, ix)

    def touch(self, i: int | None = None):
        if i is None:
            if self.items:
                self.dataChanged.emit(self.index(0), self.index(len(self.items) - 1))
        else:
            self.dataChanged.emit(self.index(i), self.index(i))


class GridDelegate(QtWidgets.QStyledItemDelegate):
    def __init__(self, view, size: int):
        super().__init__(view)
        self.size = size

    def sizeHint(self, opt, idx):
        return QtCore.QSize(self.size + 16, self.size + 16)

    def paint(self, p: QtGui.QPainter, opt, idx):
        sh, pm = idx.data(ITEM), idx.data(PIX)
        if sh is None:
            return
        p.save()
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        cell = QtCore.QRectF(opt.rect).adjusted(5, 5, -5, -5)
        selected = bool(opt.state & QtWidgets.QStyle.StateFlag.State_Selected)
        hover = bool(opt.state & QtWidgets.QStyle.StateFlag.State_MouseOver)
        path = QtGui.QPainterPath()
        path.addRoundedRect(cell, 10, 10)
        p.fillPath(path, QtGui.QColor(C["raised"] if hover else C["panel"]))
        if pm is not None and not pm.isNull():
            w, h = pm.width() / pm.devicePixelRatio(), pm.height() / pm.devicePixelRatio()
            k = min((cell.width() - 6) / w, (cell.height() - 6) / h, 1.0)
            tw, th = w * k, h * k
            target = QtCore.QRectF(cell.center().x() - tw / 2, cell.center().y() - th / 2, tw, th)
            clip = QtGui.QPainterPath()
            clip.addRoundedRect(target, 7, 7)
            p.setClipPath(clip)
            p.drawPixmap(target, pm, QtCore.QRectF(pm.rect()))
            p.setClipping(False)
        else:
            p.setPen(QtGui.QColor(C["muted"]))
            failed = id(sh) in idx.model().failed
            p.drawText(cell, Qt.AlignmentFlag.AlignCenter, "no preview" if failed else "loading...")
        if sh.blacklisted:
            p.fillPath(path, QtGui.QColor(0, 0, 0, 150) if K.DARK[0] else QtGui.QColor(255, 255, 255, 170))
        im = sh.image
        badge = ""
        if im.is_gallery:
            badge = f"Gallery {im.gallery_count}" if im.gallery_count and im.gallery_count > 0 else "Gallery"
        elif im.is_video:
            badge = "VIDEO"
        elif im.is_animated or (im.ext or "").lower() == "gif":
            badge = "GIF"
        if badge:
            self._badge(p, cell.left() + 8, cell.top() + 8, badge, QtGui.QColor(0, 0, 0, 170), QtGui.QColor("#ffffff"))
        if sh.blacklisted:
            self._badge(p, cell.left() + 8, cell.bottom() - 28, "blacklisted", QtGui.QColor(C["error"]), QtGui.QColor("#ffffff"))
        if sh.downloaded:
            self._badge(p, cell.right() - 58, cell.top() + 8, "saved", QtGui.QColor(C["ok"]), QtGui.QColor("#0b1a10"))
        col, wid = (C["accent"], 3) if selected else (C["ok"], 2) if sh.downloaded else (C["fav"], 2) if sh.favorited \
            else (C["border"], 1)
        pen = QtGui.QPen(QtGui.QColor(col), wid)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(cell.adjusted(wid / 2, wid / 2, -wid / 2, -wid / 2), 10, 10)
        if selected:
            p.fillPath(path, QtGui.QColor(QtGui.QColor(C["accent"]).red(), QtGui.QColor(C["accent"]).green(),
                                          QtGui.QColor(C["accent"]).blue(), 46))
        p.restore()

    @staticmethod
    def _badge(p, x, y, text, bg, fg):
        f = p.font()
        f.setPointSizeF(7.5)
        f.setBold(True)
        p.setFont(f)
        w = p.fontMetrics().horizontalAdvance(text) + 12
        r = QtCore.QRectF(x, y, w, 19)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, 6, 6)
        p.setPen(fg)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)


class Grid(QtWidgets.QListView):
    opened = QtCore.Signal(int)
    middle = QtCore.Signal(int)

    def __init__(self, size: int, invert: bool = False):
        super().__init__()
        self.setObjectName("grid")
        self.invert = invert
        self.setViewMode(QtWidgets.QListView.ViewMode.IconMode)
        self.setResizeMode(QtWidgets.QListView.ResizeMode.Adjust)
        self.setMovement(QtWidgets.QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setSpacing(2)
        self.setMouseTracking(True)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSelectionRectVisible(True)
        self.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(40)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.delegate = GridDelegate(self, size)
        self.setItemDelegate(self.delegate)
        self._press = None

    def _plain(self, e) -> bool:
        mods = e.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        ctrl_only = mods == Qt.KeyboardModifier.ControlModifier
        return (ctrl_only if self.invert else not mods) and e.button() == Qt.MouseButton.LeftButton

    def mousePressEvent(self, e):
        idx = self.indexAt(e.position().toPoint())
        if idx.isValid() and e.button() == Qt.MouseButton.MiddleButton:
            self.middle.emit(idx.row())
            return
        if idx.isValid() and self._plain(e):
            self._press = idx.row()     # a plain click opens the picture and leaves the selection alone
            return
        self._press = None
        if self.invert and idx.isValid() and e.button() == Qt.MouseButton.LeftButton and not e.modifiers():
            self.selectionModel().select(idx, QtCore.QItemSelectionModel.SelectionFlag.Toggle)
            return
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        if self._press is not None:
            idx = self.indexAt(e.position().toPoint())
            row, self._press = self._press, None
            if idx.isValid() and idx.row() == row:
                self.opened.emit(row)
            return
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.currentIndex().isValid():
            self.opened.emit(self.currentIndex().row())
            return
        super().keyPressEvent(e)

    def rows(self) -> list[int]:
        return sorted(ix.row() for ix in self.selectionModel().selectedIndexes())


# ---- search box with tag completion ----------------------------------------------------------------------
class TagCompleter(QtWidgets.QCompleter):
    """Completes the word under the cursor (not the whole line), keeping a leading - or ~."""

    def __init__(self, edit: QtWidgets.QLineEdit, source):
        super().__init__(edit)
        self.edit, self.source = edit, source
        self.model_ = QtCore.QStringListModel(self)
        self.setModel(self.model_)
        self.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setCompletionMode(QtWidgets.QCompleter.CompletionMode.PopupCompletion)
        self.setWidget(edit)
        self.activated[str].connect(self._insert)
        edit.textEdited.connect(self._edited)

    def _word(self):
        text, cur = self.edit.text(), self.edit.cursorPosition()
        start = text.rfind(" ", 0, cur) + 1
        return text, start, cur, text[start:cur]

    def _edited(self, _=None):
        _text, _start, _cur, word = self._word()
        bare = word.lstrip("-~")
        if len(bare) < 2 or ":" in bare:
            self.popup().hide()
            return
        try:
            words = self.source(bare)
        except Exception:  # noqa: BLE001
            words = []
        if not words or (len(words) == 1 and words[0] == bare):
            self.popup().hide()
            return
        self.model_.setStringList(words)
        self.setCompletionPrefix("")
        self.complete()

    def _insert(self, word: str):
        text, start, cur, old = self._word()
        prefix = old[:len(old) - len(old.lstrip("-~"))]
        new = text[:start] + prefix + word + " " + text[cur:].lstrip()
        self.edit.setText(new)
        self.edit.setCursorPosition(start + len(prefix) + len(word) + 1)


class SearchColors(QtCore.QObject):
    """Colours the words in a search box by what they are, like Grabber: artist, character, series and species
    tags in their tag colours, favorites, kept-for-later and blacklisted tags in theirs, and special words
    (rating:safe, order:score) in the meta colour.

    A QLineEdit has no rich text, but it does honour the text formats of an input-method event, so an empty one
    carrying only formats colours the words without touching the text, the cursor or the selection. While an
    input method is composing (Korean, Japanese, Chinese typing), nothing is sent, so the composition is not
    disturbed; the colours follow as soon as the word is committed."""

    def __init__(self, edit: QtWidgets.QLineEdit, app, sites):
        super().__init__(edit)
        self.edit, self.app, self.sites = edit, app, sites       # sites: function returning the tab's hosts
        self.composing = False
        self.sending = False
        self.asked: set = set()
        self.timer = QtCore.QTimer(self, singleShot=True, interval=60, timeout=self.apply)
        edit.textChanged.connect(lambda *_: self.timer.start())
        edit.installEventFilter(self)

    def eventFilter(self, obj, ev):
        if ev.type() == QtCore.QEvent.Type.InputMethod and not self.sending:
            try:
                self.composing = bool(ev.preeditString())
            except Exception:  # noqa: BLE001
                self.composing = False
            if not self.composing:
                self.timer.start()
        return False

    def color_of(self, word: str, favs: set, later: set, black: set) -> str | None:
        bare = word.lstrip("-~").lower()
        if not bare:
            return None
        if bare in black:
            return C["error"]
        if bare in favs:
            return C["fav"]
        if bare in later:
            return C["warn"]
        st = self.app.st
        typ = self.app.tag_types.get(bare)
        if not typ and ":" in bare and not bare.startswith(("http:", "https:")):
            typ = "meta"
        if not typ or typ == "general":
            return None
        col = uikit.tag_color(typ, st, K.DARK[0])
        return None if col.lower() in ("#000000", "#000") else col

    def apply(self):
        edit = self.edit
        if self.composing or not self.app.st.get("colorSearchWords", True):
            return
        text, cur = edit.text(), edit.cursorPosition()
        lib = self.app.lib
        try:
            favs = {f["name"].lower() for f in lib.favorites()}
            later = {n.lower() for n in lib.later()}
            black = {ln.lower() for ln in self.app.st.lines("blacklistedtags") if " " not in ln}
        except Exception:  # noqa: BLE001
            favs, later, black = set(), set(), set()
        attrs, unknown, pos = [], [], 0
        for word in text.split(" "):
            if word:
                col = self.color_of(word, favs, later, black)
                bare = word.lstrip("-~").lower()
                if col:
                    fmt = QtGui.QTextCharFormat()
                    fmt.setForeground(QtGui.QColor(col))
                    attrs.append(QtGui.QInputMethodEvent.Attribute(QtGui.QInputMethodEvent.AttributeType.TextFormat,
                                                                   pos - cur, len(word), fmt))
                elif bare and ":" not in bare and bare not in self.app.tag_types and bare not in self.asked:
                    unknown.append(bare)
            pos += len(word) + 1
        self.sending = True
        try:
            QtCore.QCoreApplication.sendEvent(edit, QtGui.QInputMethodEvent("", attrs))
        finally:
            self.sending = False
        if unknown:
            self._look_up(unknown)

    def _look_up(self, words):
        """Ask the first site's downloaded tag list (Tools > Tag loader) what kind of tag each new word is."""
        self.asked.update(words)
        site = next((s for s in (self.app.store.get(h) for h in self.sites()) if s is not None), None)
        if site is None:
            return

        def work():
            if not site.tag_db().exists():
                return {}
            return site.tag_db_types(words) or {}

        def done(found):
            new = {k.lower(): v for k, v in found.items() if v}
            if new:
                self.app.tag_types.update(new)
                self.timer.start()
        self.app.worker.submit(work, on_done=done, on_error=lambda e: None)


# ---- one search tab ---------------------------------------------------------------------------------------
class SearchTab(QtWidgets.QWidget):
    def __init__(self, page, app, query="", sites=None, page_no=1, gallery=None):
        super().__init__()
        self.page_, self.app, self.gallery = page, app, gallery
        self.sites = list(sites or app.st.get("sites") or [])
        self.items: list = []
        self.result = None
        self.cancel = threading.Event()
        self.is_busy = False
        self.loaded = False
        self.page_infos: dict = {}
        self.back, self.fwd = [], []
        self._last, self._last_page, self._from_hist = None, 1, False
        self._wiki = ""
        st = app.st

        self.sites_btn = K.button("", self.choose_sites, ico="globe", tip="Choose which sites this tab searches")
        self.query = QtWidgets.QLineEdit(query)
        self.query.setObjectName("search")
        self.query.setPlaceholderText("Search tags, e.g.  landscape   or   cat_ears blue_eyes   - then press Enter")
        self.query.setClearButtonEnabled(True)
        self.query.returnPressed.connect(lambda: self.search())
        self.completer = TagCompleter(self.query, self._complete)
        self.colors = SearchColors(self.query, app, lambda: self.sites)
        self.search_btn = K.button("Search", lambda: self.search(), accent=True, ico="search")
        self.filter = QtWidgets.QLineEdit()
        self.filter.setPlaceholderText("Filter results, e.g. score:>10")
        self.filter.setToolTip("Removes pictures from what the site sent: score:>10, -rating:explicit, width:>=1920, "
                               "-grabber:downloaded ... (Help > Filters)")
        self.filter.setFixedWidth(210)
        self.filter.returnPressed.connect(lambda: self.search())
        self.prev_btn = K.button("", lambda: self.go(-1), ico="left", tip="Previous page")
        self.next_btn = K.button("", lambda: self.go(1), ico="right", tip="Next page")
        self.page_no = QtWidgets.QSpinBox()
        self.page_no.setRange(1, 100000)
        self.page_no.setValue(page_no)
        self.page_no.setPrefix("Page ")
        self.page_no.setFixedWidth(84)
        self.page_no.editingFinished.connect(lambda: self.search(keep_page=True) if self.page_no.value() != self._last_page
                                             and self.loaded else None)
        self.per_page = QtWidgets.QSpinBox()
        self.per_page.setRange(1, 1000)
        self.per_page.setValue(int(st.get("limit") or 20))
        self.per_page.setSuffix(" per page")
        self.per_page.setFixedWidth(120)
        self.per_page.setToolTip("How many pictures each site sends per page (most allow up to 100)")
        self.merge = QtGui.QAction("Merge sites (show a picture found on several sites once)", self, checkable=True)
        self.merge.setChecked(bool(st.get("mergeResults", False)))
        m = QtWidgets.QMenu(self)
        m.addAction(self.merge)
        m.addAction("Back to the previous search\tAlt+Left", lambda: self.hist(-1))
        m.addAction("Forward\tAlt+Right", lambda: self.hist(1))
        m.addSeparator()
        m.addAction("Select all pictures\tCtrl+A", lambda: self.grid.selectAll())
        m.addAction("Select none", lambda: self.grid.clearSelection())
        m.addSeparator()
        m.addAction("How to search...", lambda: app.open_help("search"))
        self.more_btn = K.button("", ico="more", tip="More", menu=m)
        top = K.hbox(self.sites_btn, (self.query, 1), self.search_btn, self.filter, self.prev_btn, self.page_no,
                     self.next_btn, self.per_page, self.more_btn, spacing=6)
        if gallery is not None:
            for w in (self.sites_btn, self.query, self.search_btn, self.filter):
                w.setVisible(False)
            top.insertWidget(0, K.label(f"Gallery: {gallery.name or gallery.id}", role="heading"), 1)

        # left: tags
        self.tags = QtWidgets.QListWidget()
        self.tags.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tags.customContextMenuRequested.connect(self._tag_menu)
        self.tags.itemClicked.connect(self._tag_click)
        self.tags.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tags.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.tags.setToolTip("Click: search this tag.  Ctrl+click: add it to the search.  Right-click: more")
        self.wiki_btn = K.button("About this tag...", self.show_wiki, flat=True)
        self.wiki_btn.setVisible(False)
        left = K.widget(K.vbox(K.label("Tags", role="heading"), (self.tags, 1), self.wiki_btn, spacing=6))
        left.setMinimumWidth(150)

        # right: grid / message
        size = max(90, int(st.get("thumbnailSize") or 180))
        self.model = GridModel(self)
        self.grid = Grid(size, bool(st.get("invertToggle")))
        self.grid.setModel(self.model)
        self.grid.opened.connect(self._open)
        self.grid.middle.connect(lambda i: self._open(i, background=True))
        self.grid.customContextMenuRequested.connect(self._menu)
        self.grid.selectionModel().selectionChanged.connect(lambda *_: self._update_sel())
        self.grid.verticalScrollBar().valueChanged.connect(self._scrolled)
        self.empty = K.empty_state("", "")
        self.empty.linkActivated.connect(self._empty_link)
        self.more_results = K.button("Load more", self.load_more)
        self.more_results.setVisible(False)
        self.view = QtWidgets.QStackedWidget()
        self.view.addWidget(self.grid)
        self.view.addWidget(self.empty)
        right = K.widget(K.vbox((self.view, 1), self.more_results, spacing=4))
        split = QtWidgets.QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([220, 1000])
        split.setChildrenCollapsible(False)
        self.split = split

        # bottom
        self.status = K.label("", muted=True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        self.dest = K.button("", self.change_dest, flat=True, ico="folder", tip="Where pictures are saved and how files "
                             "are named. Click to change.")
        self.sel_btn = K.button("Download selected", self.add_selected, tip="Ctrl+click or drag over pictures to select them")
        self.sel_btn.setEnabled(False)
        self.page_btn = K.button("Download this page", self.add_page)
        self.all_btn = K.button("Download all results...", self.add_all, accent=True, ico="download",
                                tip="Everything this search finds, on every chosen site. You can say how many.")
        self.fav_btn = K.button("Favorite", self.toggle_favorite, ico="star", tip="Keep this search in Favorites")
        self.mon_btn = K.button("Monitor...", self.add_monitor, ico="bell",
                                tip="Check this search every few hours and download what is new")
        bottom = K.hbox(self.dest, None, self.sel_btn, self.page_btn, self.all_btn, self.fav_btn, self.mon_btn, spacing=6)
        self.setLayout(K.vbox(top, (split, 1), self.status, bottom, margins=(12, 10, 12, 10), spacing=6))
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+A"), self.grid, activated=self.grid.selectAll,
                        context=Qt.ShortcutContext.WidgetShortcut)
        self.query.setMinimumWidth(150)
        self.dest.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed)
        self.dest.setStyleSheet("text-align: left;")
        bottom.setStretch(0, 1)
        bottom.setStretch(1, 0)
        self._narrow = None
        self.refresh_labels()
        self.draw_empty()

    def resizeEvent(self, e):
        """On a narrow window: hide the two least-used boxes and shorten the button texts, so nothing is cut off."""
        super().resizeEvent(e)
        w = self.width()
        narrow = w < 1020
        if self.gallery is None:
            self.filter.setVisible(w >= 1020)
        self.per_page.setVisible(w >= 860)
        if narrow != self._narrow:
            self._narrow = narrow
            self.page_btn.setText("This page" if narrow else "Download this page")
            self.all_btn.setText("All results..." if narrow else "Download all results...")
            self.mon_btn.setText("" if narrow else "Monitor...")
            self._update_sel()
            self.refresh_labels()

    # ---- labels ----------------------------------------------------------------------------------
    def title(self) -> str:
        if self.gallery is not None:
            return "Gallery " + str(self.gallery.name or self.gallery.id)[:24]
        q = self.query.text().strip()
        return (q if len(q) <= 28 else q[:26] + "...") or "(everything)"

    def refresh_labels(self):
        self.sites_btn.setText(site_names(self.app, self.sites))
        st = self.app.st
        folder = st.save_path()
        short = os.path.basename(folder.rstrip("/\\")) or folder
        self.dest.setText(f"Saving to {short}  ·  {uikit.format_label(st.filename())}")
        fav = self.app.lib.is_favorite(self.query.text().strip()) if self.query.text().strip() else False
        self.fav_btn.setText("" if getattr(self, "_narrow", False) else ("Favorited" if fav else "Favorite"))
        self.fav_btn.setToolTip("This search is in Favorites - click to remove it" if fav else "Keep this search in Favorites")
        self.fav_btn.setIcon(K.icon("star", C["fav"] if fav else None))

    def themed(self):
        self._color_tags()
        self.colors.apply()
        self.model.touch()
        if self.view.currentWidget() is self.empty and not self.items:
            self.draw_empty(searched=self.loaded)

    def set_sites(self, sites):
        self.sites = list(sites)
        self.refresh_labels()

    def choose_sites(self):
        hosts = self.app.open_sites(pick_for=self.sites)
        if hosts is not None:
            self.set_sites(hosts)
            self.app.st.set("sites", list(hosts))
            if self.loaded or self.query.text().strip():
                self.search()

    def change_dest(self):
        if DestDialog(self, self.app).exec():
            for t in self.page_.tabs_list():
                t.refresh_labels()

    def _complete(self, prefix: str) -> list[str]:
        out = list(self.app.lib.complete(prefix, limit=12))
        for h in self.sites[:1]:
            site = self.app.store.get(h)
            try:
                if site is not None and site.tag_db().exists():
                    out += [n for n, _t, _c in site.tag_db().complete(prefix, 12) if n not in out]
            except Exception:  # noqa: BLE001
                pass
        return out[:16]

    # ---- searching -------------------------------------------------------------------------------
    def go(self, d: int):
        self.page_no.setValue(max(1, self.page_no.value() + d))
        self.search(keep_page=True, direction=d)

    def search(self, keep_page=False, direction=0, append=False):
        app = self.app
        if not keep_page:
            self.page_no.setValue(1)
            self.page_infos = {}
        q = self.query.text().strip()
        cur = (q, list(self.sites))
        if not self._from_hist and self._last and self._last != cur and not append:
            self.back.append((*self._last, int(self._last_page)))
            self.fwd.clear()
        self._from_hist = False
        if q.startswith(("http://", "https://")) and " " not in q and self.gallery is None:
            from ..grab.search import parse_post_address
            try:
                site, pid = parse_post_address(app.store, q)
            except ValueError as e:
                self._say(str(e), error=True)
                return
            self.set_sites([site.host])
            self.query.setText(f"id:{pid}")
            q = self.query.text()
        self.page_.retitle(self)
        self.cancel.set()
        self.cancel = threading.Event()
        cancel = self.cancel
        page = self.page_no.value()
        sites = [s for s in (app.store.get(h) for h in self.sites) if s is not None]
        if self.gallery is not None:
            sites = [s for s in (app.store.get(self.gallery.site_key),) if s is not None]
        if not sites:
            self._say("Choose at least one site first (the button left of the search box).", error=True)
            return
        prev = self.page_infos.get(page - 1, {}) if direction >= 0 else {}
        self._say(f"Searching '{q or '(everything)'}' on {', '.join(s.name for s in sites)}...")
        if not append:
            K.set_empty(self.empty, "Searching...", "")
            if not self.items:
                self.view.setCurrentWidget(self.empty)
        self.is_busy, self.loaded = True, True
        self._last, self._last_page = (q, list(self.sites)), page
        if q and self.gallery is None:
            app.lib.add_history(q, self.sites)
        from ..grab import search as S
        per, pf, merge, gal = self.per_page.value(), self.filter.text().strip(), self.merge.isChecked(), self.gallery

        def work():
            return S.run(q, sites, page, per, app.st, app.lib, postfilter=pf, merge=merge, previous=prev, cancel=cancel,
                         gallery=gal)

        def done(res):
            if cancel is not self.cancel:
                return   # an older search
            self.is_busy = False
            self.result = res
            self.page_infos[page] = {pr.site.host: pr.info() for pr in res.pages if not pr.error}
            self.show_result(res, append=append)

        def failed(e):
            if cancel is not self.cancel:
                return
            self.is_busy = False
            msg = uikit.friendly(str(e))
            self._say("The search failed: " + msg, error=True)
            self.draw_empty(error=msg)
        app.worker.submit(work, on_done=done, on_error=failed)

    def _say(self, text: str, error: bool = False):
        self.status.setText(text)
        self.status.setToolTip(text)
        K.restyle(self.status, role="error" if error else "", muted=not error)

    def show_result(self, res, append=False):
        app = self.app
        if not append:
            self.grid.clearSelection()
            self.grid.verticalScrollBar().setValue(0)
        start = len(self.items) if append else 0
        self.model.set_items(res.items, append=append)
        self.items = self.model.items
        if not append:
            from ..textutil import html_to_text
            self._wiki = html_to_text(getattr(res, "wiki", "") or "")
            self.wiki_btn.setVisible(bool(self._wiki))
            self.tags.clear()
            known = app.tag_types
            for name, typ, _count in res.tags:
                if typ and typ != "general":
                    known[name.lower()] = typ
            for sh in res.items:
                for tg in sh.image.tags:
                    if tg.type and tg.type != "general":
                        known[tg.name.lower()] = tg.type
            self.colors.timer.start()
            for name, typ, count in res.tags[:500]:
                it = QtWidgets.QListWidgetItem(f"{name}   {count:,}" if count else name)
                it.setData(Qt.ItemDataRole.UserRole, (name, typ or "general"))
                self.tags.addItem(it)
            self._color_tags()
        n = len(res.items)
        bits = [f"Page {res.page}: {n} picture{'s' if n != 1 else ''}"]
        if res.filtered and app.st.get("showFilteredImagesCount", True):
            bits.append(f"{res.filtered} hidden by filters / blacklist")
        if res.duplicates:
            bits.append(f"{res.duplicates} found on two sites (shown once)")
        counts = [f"{pr.site.name}: {pr.image_count:,}" for pr in res.pages if pr.image_count and pr.image_count > 0]
        if counts:
            bits.append("in total " + ", ".join(counts))
        errs = [f"{e.split(':', 1)[0]}: {uikit.friendly(e.split(':', 1)[-1], e.split(':', 1)[0])}" for e in res.errors]
        self._say("   |   ".join(bits) + (("      Problem - " + "  ".join(errs)) if errs else ""), error=bool(errs))
        self._update_sel()
        if self.items:
            self.view.setCurrentWidget(self.grid)
        else:
            self.draw_empty(error=" ".join(errs) if errs and not res.filtered else "", searched=True)
        for sh in self.items[start:]:
            app.thumbs.get(sh.site, sh.image, lambda pm, sh=sh: self.model.set_pix(sh, pm), size=self.grid.delegate.size)
        if (app.st.get("whitelist_download") or "never") == "page":
            wl = [(sh.site, sh.image) for sh in self.items[start:] if not sh.downloaded and app.whitelisted(sh.image)]
            if wl:
                app.save_images(wl)
        self.more_results.setVisible((app.st.get("infiniteScroll") or "disabled") == "button" and n > 0)
        self.refresh_labels()

    def load_more(self):
        if self.is_busy or not self.loaded:
            return
        self.page_no.setValue(self.page_no.value() + 1)
        self.search(keep_page=True, direction=1, append=True)

    def _scrolled(self, value):
        sb = self.grid.verticalScrollBar()
        if ((self.app.st.get("infiniteScroll") or "disabled") == "scroll" and self.items and not self.is_busy
                and sb.maximum() > 0 and value >= sb.maximum() - 40 and self.result is not None and self.result.items):
            self.load_more()

    def draw_empty(self, error: str = "", searched: bool = False):
        if error:
            K.set_empty(self.empty, "The search did not work", K.esc(error))
        elif searched:
            tips = ["Check the spelling: tags must match the site exactly (cat_ears, not cat ears).",
                    "Try fewer tags. Danbooru allows only 2 without a paid account.",
                    "Some sites hide pictures unless you log in (Sites and logins, bottom left).",
                    "Clear the 'Filter results' box if you typed something there."]
            K.set_empty(self.empty, "No pictures found", f"Searched: {K.esc(site_names(self.app, self.sites, 6))}<br><br>"
                        + "<br>".join(K.esc(t) for t in tips))
        elif self.gallery is not None:
            K.set_empty(self.empty, "Loading the gallery...", "")
        else:
            recent = self.recent_searches()
            text = "Type what you are looking for above and press Enter."
            if recent:
                text += "<br><br><b>Recent searches</b><br>" + "<br>".join(
                    f'<a style="color:{C["accent"]}; text-decoration:none;" href="recent:{i}">{K.esc(q or "(everything)")}</a>'
                    for i, (q, _s) in enumerate(recent))
            K.set_empty(self.empty, "Search for pictures", text)
        self.view.setCurrentWidget(self.empty)

    def recent_searches(self, n: int = 8):
        seen, out = set(), []
        for h in self.app.lib.history(60):
            q = (h.get("search") or "").strip()
            if q and q not in seen:
                seen.add(q)
                out.append((q, h.get("sites") or []))
            if len(out) >= n:
                break
        self._recent = out
        return out

    def _empty_link(self, href: str):
        if href.startswith("recent:"):
            q, sites = self._recent[int(href[7:])]
            self.run_query(q, [s for s in sites if self.app.store.get(s)] or None)

    def run_query(self, q, sites=None):
        self.query.setText(q)
        if sites:
            self.set_sites(sites)
        self.search()

    def hist(self, d: int):
        src, dst = (self.back, self.fwd) if d < 0 else (self.fwd, self.back)
        if not src:
            self.app.flash("No earlier search in this tab." if d < 0 else "No later search in this tab.")
            return
        q, sites, page = src.pop()
        if self._last:
            dst.append((*self._last, int(self._last_page)))
        self._from_hist = True
        self.query.setText(q)
        self.set_sites(sites)
        self.page_no.setValue(page)
        self.search(keep_page=True)

    # ---- pictures --------------------------------------------------------------------------------
    def _update_sel(self):
        n = len(self.grid.rows())
        self.sel_btn.setEnabled(n > 0)
        word = "Selected" if getattr(self, "_narrow", False) else "Download selected"
        self.sel_btn.setText(f"{word} ({n})" if n else word)

    def _open(self, i: int, background: bool = False):
        if not 0 <= i < len(self.items):
            return
        sh = self.items[i]
        if sh.image.is_gallery:
            self.page_.new_tab("", [sh.site.host], focus=not background, gallery=sh.image)
            return
        self.app.open_viewer(self, i)

    def mark_saved(self, img):
        for i, sh in enumerate(self.items):
            if sh.image is img:
                sh.downloaded = True
                self.model.touch(i)

    def _menu(self, pos):
        idx = self.grid.indexAt(pos)
        app, m = self.app, QtWidgets.QMenu(self)
        rows = self.grid.rows()
        if idx.isValid():
            i = idx.row()
            sh = self.items[i]
            im = sh.image
            targets = [self.items[r] for r in rows] if i in rows and len(rows) > 1 else [sh]
            n = len(targets)
            many = f" ({n} pictures)" if n > 1 else ""
            m.addAction("Open", lambda: self._open(i))
            m.addAction("Save now" + many, lambda: app.save_images([(t.site, t.image) for t in targets],
                                                                    lambda img, res: self.mark_saved(img)))
            m.addAction("Add to Downloads" + many, lambda: app.add_to_downloads_images([(t.site, t.image) for t in targets]))
            m.addSeparator()
            m.addAction("Open its page in the browser", lambda: uikit.open_url(sh.site.page_url_for(im)))
            m.addAction("Copy the page address", lambda: self._copy(sh.site.page_url_for(im)))
            if im.file_url:
                m.addAction("Copy the picture address", lambda: self._copy(im.file_url))
            m.addAction("Copy all tags", lambda: self._copy(" ".join(t.name for t in im.tags)))
            m.addAction("Copy all tags with their kind", lambda: self._copy(" ".join(
                f"{t.type}:{t.name}" if t.type and t.type != "general" else t.name for t in im.tags)))
            if im.md5:
                m.addAction("Find this picture (MD5)", lambda: self.page_.new_tab(f"md5:{im.md5}", self.sites, run=True))
            q = self.query.text().strip()
            if q and app.lib.is_favorite(q):
                url = im.preview_url or im.sample_url
                m.addAction(f"Use as the picture of favorite '{q}'", lambda: (app.lib.update_favorite(
                    q, image=json.dumps({"site": sh.site.host, "url": url})), app.favorites.refresh()))
            m.addSeparator()
        m.addAction("Select all", self.grid.selectAll)
        m.addAction("Select none", self.grid.clearSelection)
        m.exec(self.grid.viewport().mapToGlobal(pos))

    def _copy(self, text: str):
        QtWidgets.QApplication.clipboard().setText(text or "")
        self.app.flash("Copied.")

    def add_selected(self):
        sel = [self.items[r] for r in self.grid.rows()]
        if sel:
            self.app.add_to_downloads_images([(s.site, s.image) for s in sel])

    def add_page(self):
        page = [(s.site, s.image) for s in self.items if not s.blacklisted and not s.image.is_gallery]
        if page:
            self.app.add_to_downloads_images(page)
        else:
            self.app.flash("There are no pictures on this page yet - search first.")

    def add_all(self):
        if self.gallery is not None:
            self.app.flash("Use 'Download this page' for a gallery (raise 'per page' to get it all).")
            return
        d = GroupDialog(self, self.app, self.query.text().strip(), self.sites, self.filter.text().strip())
        if d.exec():
            self.app.add_group(**d.values())
            self.app.show_page("downloads")

    def toggle_favorite(self):
        q = self.query.text().strip()
        if not q:
            self.app.flash("Type a search first; a favorite is a search you keep.")
            return
        lib = self.app.lib
        if lib.is_favorite(q):
            lib.remove_favorite(q)
            self.app.flash(f"'{q}' removed from Favorites")
        else:
            lib.add_favorite(q, self.sites)
            self.app.flash(f"'{q}' added to Favorites")
        self.app.favorites.refresh()
        self.refresh_labels()
        self.colors.apply()

    def add_monitor(self):
        d = MonitorDialog(self, self.app, {"search": self.query.text().strip(), "sites": self.sites,
                                           "postfilter": self.filter.text().strip()})
        if d.exec():
            self.app.lib.add_monitor(**d.values())
            self.app.monitors.refresh()
            self.app.scheduler.start()
            self.app.flash("Monitor added. It reports pictures posted from now on.")

    # ---- tags ------------------------------------------------------------------------------------
    def _color_tags(self):
        st = self.app.st
        for i in range(self.tags.count()):
            it = self.tags.item(i)
            _name, typ = it.data(Qt.ItemDataRole.UserRole)
            col = uikit.tag_color(typ, st, K.DARK[0])
            if not K.DARK[0] and col.lower() in ("#000000", "#000"):
                col = C["text"]
            it.setForeground(QtGui.QColor(col))

    def _tag_click(self, it):
        name, _typ = it.data(Qt.ItemDataRole.UserRole)
        if QtWidgets.QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier:
            words = self.query.text().split()
            if name not in words:
                self.query.setText(" ".join(words + [name]))
            return
        self.run_query(name)

    def _tag_menu(self, pos):
        it = self.tags.itemAt(pos)
        if it is not None:
            tag_menu(self.app, it.data(Qt.ItemDataRole.UserRole)[0], self.tags.viewport().mapToGlobal(pos), self)

    def show_wiki(self):
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("About " + (self.query.text().strip() or "this tag"))
        box.setText(self._wiki[:6000])
        box.exec()


def tag_menu(app, name: str, global_pos, tab: SearchTab | None = None, parent=None):
    """Right-click menu of a tag (search tabs and the picture window)."""
    lib, st = app.lib, app.st
    m = QtWidgets.QMenu(parent or tab)
    if tab is not None:
        m.addAction(f"Search '{name}' here", lambda: tab.run_query(name))
        m.addAction("Add to this search", lambda: tab.query.setText((tab.query.text().strip() + " " + name).strip()))
        m.addAction("Exclude from this search", lambda: (tab.query.setText((tab.query.text().strip() + " -" + name).strip()),
                                                        tab.search()))
    m.addAction("Search in a new tab", lambda: (app.new_search_tab(name, tab.sites if tab else None, run=True),
                                                app.show_page("browse")))
    m.addSeparator()
    if lib.is_favorite(name):
        m.addAction("Remove from Favorites", lambda: (lib.remove_favorite(name), app.favorites.refresh()))
    else:
        m.addAction("Add to Favorites", lambda: (lib.add_favorite(name, tab.sites if tab else None), app.favorites.refresh(),
                                                 app.flash(f"'{name}' added to Favorites")))
    m.addAction("Keep for later", lambda: (lib.add_later(name), app.favorites.refresh(), app.flash(f"'{name}' kept for later")))

    def blacklist():
        cur = str(st.get("blacklistedtags") or "").rstrip("\n")
        if name not in cur.split():
            st.set("blacklistedtags", (cur + "\n" + name).strip("\n"))
        app.settings_page.reload()
        app.flash(f"'{name}' is now blacklisted (Settings > Blacklist & tags to undo)")
    m.addAction("Blacklist (never show or download)", blacklist)
    m.addSeparator()
    m.addAction("Copy the tag", lambda: QtWidgets.QApplication.clipboard().setText(name))
    m.exec(global_pos)


# ---- the page (tabs) --------------------------------------------------------------------------------------
class BrowsePage(QtWidgets.QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(lambda i: self.close(self.tabs.widget(i)))
        self.tabs.currentChanged.connect(self._changed)
        self.tabs.tabBar().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tabs.tabBar().customContextMenuRequested.connect(self._tab_menu)
        plus = K.button("", lambda: self.new_tab(focus=True), flat=True, ico="plus", tip="New search tab (Ctrl+T)")
        self.tabs.setCornerWidget(plus, Qt.Corner.TopRightCorner)
        self.setLayout(K.vbox(self.tabs, margins=(0, 6, 0, 0)))

    def tabs_list(self) -> list[SearchTab]:
        return [self.tabs.widget(i) for i in range(self.tabs.count())]

    def current(self) -> SearchTab | None:
        return self.tabs.currentWidget()

    def new_tab(self, query="", sites=None, focus=True, run=None, page=1, gallery=None):
        t = SearchTab(self, self.app, query, sites, page, gallery)
        i = self.tabs.addTab(t, t.title())
        x = K.button("", lambda t=t: self.close(t), flat=True, ico="close", tip="Close this tab (Ctrl+W)")
        x.setFixedSize(22, 22)
        x.setIconSize(QtCore.QSize(12, 12))
        x.setStyleSheet("padding: 0;")
        self.tabs.tabBar().setTabButton(i, QtWidgets.QTabBar.ButtonPosition.RightSide, x)
        if focus:
            self.tabs.setCurrentIndex(i)
            t.query.setFocus()
        if run or gallery is not None or (run is None and query):
            t.search(keep_page=True)
        return t

    def retitle(self, tab):
        i = self.tabs.indexOf(tab)
        if i >= 0:
            self.tabs.setTabText(i, tab.title())
            self.tabs.setTabToolTip(i, tab.query.text())

    def close(self, tab):
        if tab is None:
            return
        tab.cancel.set()
        self.tabs.removeTab(self.tabs.indexOf(tab))
        tab.deleteLater()
        if self.tabs.count() == 0:
            self.new_tab(focus=True)

    def close_current(self):
        if self.app.current_page() == "browse":
            self.close(self.current())

    def reload_current(self):
        t = self.current()
        if t is not None and self.app.current_page() == "browse":
            t.search(keep_page=True)

    def hist(self, d):
        t = self.current()
        if t is not None and self.app.current_page() == "browse":
            t.hist(d)

    def focus_search(self):
        self.app.show_page("browse")
        t = self.current()
        if t is not None:
            t.query.setFocus()
            t.query.selectAll()

    def _changed(self, _i):
        t = self.current()
        if t is not None and not t.loaded and t.query.text().strip() and getattr(t, "pending", False):
            t.pending = False
            t.search(keep_page=True)

    def _tab_menu(self, pos):
        i = self.tabs.tabBar().tabAt(pos)
        t = self.tabs.widget(i) if i >= 0 else None
        m = QtWidgets.QMenu(self)
        if t is not None:
            m.addAction("Back", lambda: t.hist(-1)).setEnabled(bool(t.back))
            m.addAction("Forward", lambda: t.hist(1)).setEnabled(bool(t.fwd))
            m.addAction("Search again", lambda: t.search(keep_page=True))
            m.addAction("Duplicate", lambda: self.new_tab(t.query.text(), t.sites, run=t.loaded))
            rec = m.addMenu("Recent searches")
            for q, sites in t.recent_searches(12):
                rec.addAction(q, lambda q=q, s=sites: t.run_query(q, [x for x in s if self.app.store.get(x)] or None))
            rec.addSeparator()
            rec.addAction("Clear the list", lambda: (self.app.lib.clear_history(), t.draw_empty() if not t.items else None))
            m.addSeparator()
            m.addAction("Close", lambda: self.close(t))
            m.addAction("Close the other tabs", lambda: [self.close(x) for x in self.tabs_list() if x is not t])
        m.addAction("New tab", lambda: self.new_tab(focus=True))
        m.exec(self.tabs.tabBar().mapToGlobal(pos))

    def themed(self):
        for t in self.tabs_list():
            t.themed()

    def settings_changed(self):
        for t in self.tabs_list():
            t.refresh_labels()
            t.colors.apply()

    def refresh_saved(self):
        """After downloads finished: mark the pictures in the open tabs that are saved now."""
        lib = self.app.lib
        for t in self.tabs_list():
            for i, sh in enumerate(t.items):
                im = sh.image
                if not sh.downloaded and ((im.md5 and lib.md5_path(im.md5) is not None) or lib.was_saved(im.site_key, im.id)):
                    sh.downloaded = True
                    t.model.touch(i)

    def save_session(self):
        data = [{"query": t.query.text(), "sites": t.sites, "page": t.page_no.value(), "filter": t.filter.text()}
                for t in self.tabs_list() if t.gallery is None]
        self.app.lib.set_state("qt_session", {"tabs": data, "current": self.tabs.currentIndex()})

    def restore_session(self):
        st = self.app.st
        mode = st.get("start") or "restore"
        sess = self.app.lib.get_state("qt_session") or {}
        tabs = sess.get("tabs") or []
        if mode == "restore" and tabs:
            all_now = bool(st.get("preloadAllTabs"))
            cur = min(max(0, int(sess.get("current") or 0)), len(tabs) - 1)
            for i, d in enumerate(tabs[:30]):
                sites = [h for h in d.get("sites") or [] if self.app.store.get(h)] or None
                t = self.new_tab(d.get("query", ""), sites, focus=False, run=False, page=int(d.get("page") or 1))
                t.filter.setText(d.get("filter", ""))
                t.pending = bool(d.get("query")) and not all_now and i != cur
                if d.get("query") and (all_now or i == cur):
                    t.search(keep_page=True)
            self.tabs.setCurrentIndex(cur)
        else:
            self.new_tab(focus=True, run=(mode == "loadfirst"))


# ---- dialogs ----------------------------------------------------------------------------------------------
class FormDialog(QtWidgets.QDialog):
    """A small form: add rows, then OK / Cancel."""

    def __init__(self, parent, title: str, intro: str = "", ok: str = "OK"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(560)
        self.form = QtWidgets.QFormLayout()
        self.form.setVerticalSpacing(10)
        self.form.setHorizontalSpacing(14)
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.msg = K.label("", role="error", wrap=True)
        self.ok_btn = K.button(ok, self._ok, accent=True)
        self.ok_btn.setDefault(True)
        lay = K.vbox(margins=(18, 16, 18, 14), spacing=12)
        if intro:
            lay.addWidget(K.label(intro, muted=True, wrap=True))
        lay.addLayout(self.form)
        lay.addWidget(self.msg)
        lay.addLayout(K.hbox(None, K.button("Cancel", self.reject), self.ok_btn))
        self.setLayout(lay)

    def _ok(self):
        problem = self.check()
        if problem:
            self.msg.setText(problem)
            return
        self.accept()

    def check(self) -> str:
        return ""

    def folder_row(self, label: str, value: str, placeholder: str = "") -> QtWidgets.QLineEdit:
        ed = QtWidgets.QLineEdit(value)
        ed.setPlaceholderText(placeholder)
        b = K.button("Browse...", lambda: self._pick(ed))
        self.form.addRow(label, K.hbox((ed, 1), b))
        return ed

    def _pick(self, ed):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose a folder", ed.text() or "")
        if d:
            ed.setText(os.path.normpath(d))


class SitesField(QtWidgets.QWidget):
    def __init__(self, app, hosts):
        super().__init__()
        self.app, self.hosts = app, list(hosts or [])
        self.lbl = K.label("")
        self.setLayout(K.hbox((self.lbl, 1), K.button("Choose...", self.pick)))
        self._show()

    def _show(self):
        self.lbl.setText(site_names(self.app, self.hosts, 5))

    def pick(self):
        r = self.app.open_sites(pick_for=self.hosts)
        if r is not None:
            self.hosts = list(r)
            self._show()


def _filename_combo(app, value: str) -> QtWidgets.QComboBox:
    cb = QtWidgets.QComboBox()
    cb.setEditable(True)
    for lab, fmt in uikit.PRESETS:
        cb.addItem(fmt, lab)
        cb.setItemData(cb.count() - 1, lab, Qt.ItemDataRole.ToolTipRole)
    cb.setCurrentText(value)
    cb.lineEdit().setPlaceholderText(f"the usual: {app.st.filename()}")
    return cb


class GroupDialog(FormDialog):
    """Download everything a search finds."""

    def __init__(self, parent, app, query, sites, postfilter="", data: dict | None = None):
        super().__init__(parent, "Download all results", "Everything this search finds goes into the download list. "
                         "Pictures you already have are skipped.", ok="Add to Downloads")
        d = data or {}
        self.app = app
        self.query = QtWidgets.QLineEdit(query)
        self.sites = SitesField(app, sites)
        self.limit = QtWidgets.QSpinBox()
        self.limit.setRange(0, 10_000_000)
        self.limit.setSpecialValueText("everything")
        self.limit.setValue(int(d.get("limit") or 0))
        self.limit.setToolTip("Stop after this many pictures per site. 'everything' = no limit.")
        self.page = QtWidgets.QSpinBox()
        self.page.setRange(1, 100000)
        self.page.setValue(int(d.get("page") or 1))
        self.per_page = QtWidgets.QSpinBox()
        self.per_page.setRange(1, 1000)
        self.per_page.setValue(int(d.get("per_page") or max(60, int(app.st.get("limit") or 20))))
        self.pf = QtWidgets.QLineEdit(d.get("postfilter", postfilter))
        self.pf.setPlaceholderText("optional, e.g. score:>10 -rating:explicit")
        self.bl = QtWidgets.QCheckBox("Also download blacklisted pictures")
        self.bl.setChecked(bool(d.get("get_blacklisted")))
        self.form.addRow("Search", self.query)
        self.form.addRow("Sites", self.sites)
        self.form.addRow("How many", self.limit)
        self.path = self.folder_row("Save in", d.get("path", ""), f"the usual: {app.st.save_path()}")
        self.filename = _filename_combo(app, d.get("filename", ""))
        self.form.addRow("Name files", self.filename)
        self.form.addRow("Filter", self.pf)
        self.form.addRow("Start at page", K.hbox(self.page, K.label("with"), self.per_page, K.label("pictures per request"), None))
        self.form.addRow("", self.bl)

    def check(self) -> str:
        if not self.sites.hosts:
            return "Choose at least one site."
        fn = self.filename.currentText().strip()
        if fn:
            _ex, problem = uikit.sample_example(fn, self.app.st)
            if problem and not _ex:
                return "File name: " + problem
        return ""

    def values(self) -> dict:
        return {"query": self.query.text().strip(), "sites": list(self.sites.hosts), "page": self.page.value(),
                "per_page": self.per_page.value(), "limit": self.limit.value(), "path": self.path.text().strip(),
                "filename": self.filename.currentText().strip(), "postfilter": self.pf.text().strip(),
                "get_blacklisted": self.bl.isChecked()}


class MonitorDialog(FormDialog):
    def __init__(self, parent, app, mon: dict):
        super().__init__(parent, "Monitor", "A monitor checks a search by itself, tells you when there are new pictures "
                         "and can download them. It reports pictures posted after it was created.",
                         ok="Save" if mon.get("id") else "Add the monitor")
        st = app.st
        self.app = app
        self.search = QtWidgets.QLineEdit(mon.get("search", ""))
        self.sites = SitesField(app, mon.get("sites") or st.get("sites"))
        self.interval = QtWidgets.QComboBox()
        self._choices = [(60, "every hour"), (180, "every 3 hours"), (360, "every 6 hours"), (720, "every 12 hours"),
                         (1440, "once a day"), (10080, "once a week")]
        cur = int(mon.get("interval_min") or st.get("Monitoring/defaultInterval") or 1440)
        if cur not in [c[0] for c in self._choices]:
            self._choices.append((cur, f"every {cur} minutes"))
        for v, lab in self._choices:
            self.interval.addItem(lab, v)
        self.interval.setCurrentIndex([c[0] for c in self._choices].index(cur))
        self.delay = QtWidgets.QSpinBox()
        self.delay.setRange(0, 100000)
        self.delay.setSuffix(" min")
        self.delay.setValue(int(mon.get("delay_min", st.get("Monitoring/defaultDelay") or 0)))
        self.delay.setToolTip("Only report pictures at least this old (gives the site time to tag and moderate them).")
        self.notify = QtWidgets.QCheckBox("Tell me when there are new pictures")
        self.notify.setChecked(bool(mon.get("notify", st.get("Monitoring/defaultNotificationEnabled", True))))
        self.download = QtWidgets.QCheckBox("Download new pictures by itself")
        self.download.setChecked(bool(mon.get("download", st.get("Monitoring/defaultDownloadEnabled", True))))
        self.bl = QtWidgets.QCheckBox("Include blacklisted pictures")
        self.bl.setChecked(bool(mon.get("blacklisted")))
        self.pf = QtWidgets.QLineEdit(mon.get("postfilter", ""))
        self.pf.setPlaceholderText("optional, e.g. score:>10")
        self.form.addRow("Search", self.search)
        self.form.addRow("Sites", self.sites)
        self.form.addRow("Check", self.interval)
        self.form.addRow("Wait before reporting", self.delay)
        self.form.addRow("", self.notify)
        self.form.addRow("", self.download)
        self.path = self.folder_row("Save in", mon.get("path", ""), f"the usual: {st.save_path()}")
        self.filename = _filename_combo(app, mon.get("filename", ""))
        self.form.addRow("Name files", self.filename)
        self.form.addRow("Filter", self.pf)
        self.form.addRow("", self.bl)

    def check(self) -> str:
        return "" if self.sites.hosts else "Choose at least one site."

    def values(self) -> dict:
        return {"search": self.search.text().strip(), "sites": list(self.sites.hosts),
                "interval_min": int(self.interval.currentData()), "delay_min": self.delay.value(),
                "notify": self.notify.isChecked(), "download": self.download.isChecked(), "path": self.path.text().strip(),
                "filename": self.filename.currentText().strip(), "postfilter": self.pf.text().strip(),
                "blacklisted": self.bl.isChecked()}


class DestDialog(FormDialog):
    """Where pictures are saved and how the files are named (the two settings people change most)."""

    def __init__(self, parent, app):
        super().__init__(parent, "Where pictures are saved", "These are the same two settings as in Settings > Basics.",
                         ok="Save")
        self.app = app
        st = app.st
        self.path = self.folder_row("Save pictures in", st.get("Save/path") or "", f"empty = {st.save_path()}")
        self.filename = _filename_combo(app, st.filename())
        self.form.addRow("Name files like this", self.filename)
        self.example = K.label("", muted=True, wrap=True)
        self.form.addRow("Example", self.example)
        self.filename.currentTextChanged.connect(self._preview)
        self.path.textChanged.connect(self._preview)
        self._preview()

    def _preview(self, *_):
        fmt = self.filename.currentText().strip() or "%md5%.%ext%"
        ex, problem = uikit.sample_example(fmt, self.app.st, self.path.text().strip() or self.app.st.save_path())
        self.example.setText(ex or problem)
        K.restyle(self.example, role="" if ex else "error", muted=bool(ex))

    def check(self) -> str:
        ex, problem = uikit.sample_example(self.filename.currentText().strip() or "%md5%.%ext%", self.app.st)
        return "" if ex else problem

    def accept(self):
        self.app.st.update({"Save/path": self.path.text().strip(), "Save/filename": self.filename.currentText().strip()
                            or "%md5%.%ext%"})
        self.app.settings_page.reload()
        super().accept()
