"""The Downloads, Favorites, Monitors and Log pages."""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from .. import control_panel as cp
from .. import uikit
from . import common as K
from .common import C, Qt

ID = Qt.ItemDataRole.UserRole
SORT = Qt.ItemDataRole.UserRole + 1


class Page(QtWidgets.QWidget):
    """A page with its sign and title, a one-line explanation, a toolbar and a body."""
    KEY = ""      # the page's name in the sidebar: it decides the page's colour (common.PAGE_HUE)
    ICO = ""      # ... and its sign

    def __init__(self, app, title: str, intro: str = ""):
        super().__init__()
        self.app = app
        self.setProperty("hue", K.PAGE_HUE.get(self.KEY, "lilac"))
        self.bar = K.Flow(spacing=6)      # wraps in a narrow window
        self.body = K.vbox(spacing=8)
        head = K.page_head(self.KEY, self.ICO, title, intro)
        self.setLayout(K.vbox(head, self.bar, (self.body, 1), margins=(22, 18, 22, 14), spacing=12))

    def ink(self) -> str:
        """This page's colour, for text."""
        return K.hue(self.KEY, "ink")

    def stacked(self, view: QtWidgets.QWidget, title: str, text: str):
        self.empty = K.empty_state(title, text, ico=self.ICO, hue=self.KEY)
        self.stack = QtWidgets.QStackedWidget()
        self.stack.addWidget(view)
        self.stack.addWidget(self.empty)
        return self.stack

    def show_empty(self, on: bool):
        self.stack.setCurrentIndex(1 if on else 0)


# ==================================================================================================
class DownloadsPage(Page):
    KEY, ICO = "downloads", "download"
    STATUS = {"pending": "Waiting", "running": "Downloading", "done": "Finished", "paused": "Stopped", "error": "Problem"}
    END = (("nothing", "Do nothing"), ("notify", "Tell me"), ("sound", "Play a sound"),
           ("shutdown", "Shut down the PC (asks first, 60 s to cancel)"))

    def __init__(self, app):
        super().__init__(app, "Downloads", "Searches and pictures waiting to be downloaded. Press Start; you can stop at any "
                         "time and Start continues where it stopped.")
        self.runner = cp.CliRunner()
        self.stats: dict = {}
        self.paused = False
        b = self.bar
        for text, fn, ico, tip in (
                ("Add a search...", self.add_search, "plus", "Download everything a search finds"),
                ("Add a picture...", self.add_one, "image", "One picture, by the address of its page"),
                ("Edit...", self.edit, "", "Change the selected search"),
                ("Remove", self.remove, "", "Remove the selected rows from the list (downloaded files stay)"),
                ("Clear finished", self.clear_done, "", "Remove the rows that are finished")):
            b.addWidget(K.button(text, fn, ico=ico, tip=tip))
        b.addStretch(1)
        more = QtWidgets.QMenu(self)
        more.addAction("Start only the selected rows", lambda: self.start(selected=True))
        more.addAction("Start the selected rows again from the beginning", self.reset)
        more.addAction("Move the selected row up", lambda: self.move(-1))
        more.addAction("Move the selected row down", lambda: self.move(1))
        more.addSeparator()
        more.addAction("Save the list to a file...", self.save_list)
        more.addAction("Load a list from a file...", self.load_list)
        b.addWidget(K.button("Open folder", self.open_folder, ico="folder"))
        b.addWidget(K.button("", ico="more", menu=more, tip="More"))
        self.tree = K.tree(("What", "Sites", "Saved in / as", "Status", "Progress"), (300, 170, 300, 120), sortable=False)
        self.tree.itemDoubleClicked.connect(lambda *_: self.edit())
        self.tree.customContextMenuRequested.connect(self._menu)
        self.body.addWidget(self.stacked(self.tree, "The download list is empty",
                                         "In Browse, search for something, then use <b>Download all results</b>, "
                                         "<b>Download this page</b> or <b>Download selected</b>.<br>They appear here; "
                                         "press Start to download them."), 1)
        self.phase = K.label("Press Start to download everything in the list.", role="heading", wrap=True)
        self.statline = K.label("", muted=True)
        self.curfile = K.label("", muted=True)
        self.curfile.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        self.fbar = QtWidgets.QProgressBar()
        self.fbar.setRange(0, 1000)
        self.fbar.setTextVisible(False)
        self.start_btn = K.button("Start", lambda: self.start(), accent=True, ico="play")
        self.start_btn.setMinimumWidth(130)
        self.pause_btn = K.button("Pause", self.pause, ico="pause")
        self.stop_btn = K.button("Stop", self.stop, ico="stop")
        self.end = QtWidgets.QComboBox()
        self.end.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.end.setMinimumContentsLength(14)     # its longest choice must not push Start out of a narrow window
        for v, lab in self.END:
            self.end.addItem(lab, v)
        self.end.view().setMinimumWidth(330)
        cur = str(app.st.get("Batch/end") or "nothing")
        self.end.setCurrentIndex(max(0, [e[0] for e in self.END].index(cur) if cur in [e[0] for e in self.END] else 0))
        self.end.currentIndexChanged.connect(lambda *_: app.st.set("Batch/end", self.end.currentData()))
        info = K.vbox(self.phase, self.statline, self.curfile, self.fbar, spacing=4)
        card = K.widget(K.hbox((info, 1), K.vbox(K.hbox(self.pause_btn, self.stop_btn, self.start_btn),
                                                   K.hbox(K.label("When finished:", muted=True), (self.end, 1)), spacing=8),
                               margins=(14, 12, 14, 12), spacing=18), "card")
        self.body.addWidget(card)
        self.timer = QtCore.QTimer(self, interval=250, timeout=self._poll)
        self.timer.start()
        self._tick = 0
        self.refresh()
        self._buttons()

    # ---- list ------------------------------------------------------------------------------------
    def refresh(self):
        sel = set(self._ids())
        self.tree.clear()
        items = self.app.lib.batch_items()
        for it in items:
            d, pr = it["data"], it["progress"] or {}
            if it["kind"] == "group":
                lim = d.get("limit") or 0
                what = (d.get("search") or "(everything)") + (f"   (up to {lim:,})" if lim else "")
                if (d.get("page") or 1) > 1:
                    what += f"   from page {d['page']}"
                sites = ", ".join(d.get("sites") or [])
                prog = f"{pr.get('done', 0):,}" + (f" of {pr['limit']:,}" if pr.get("limit") else "") + " pictures"
                if pr.get("page"):
                    prog += f", page {pr['page']}" + (f" of {pr['page_count']}" if (pr.get("page_count") or 0) > 0 else "")
            else:
                im = d.get("image") or {}
                what = f"Picture {im.get('id', '?')}" + ("   (from a monitor)" if d.get("from_monitor") else "")
                sites = im.get("site_key", "")
                prog = str(pr.get("status", ""))
            where = " / ".join(x for x in (d.get("path") or "", d.get("filename") or "") if x) or "the usual place"
            row = QtWidgets.QTreeWidgetItem([what, sites, where, self.STATUS.get(it["status"], it["status"] or ""), prog])
            row.setData(0, ID, it["id"])
            col = {"done": C["ok"], "error": C["error"], "running": self.ink()}.get(it["status"])
            if col:
                row.setForeground(3, QtGui.QColor(col))
            self.tree.addTopLevelItem(row)
            if it["id"] in sel:
                row.setSelected(True)
        self.show_empty(not items)
        n = sum(1 for it in items if it["status"] != "done")
        self.app.nav["downloads"].setText(f"  Downloads ({n})" if n else "  Downloads")

    def themed(self):
        self.refresh()

    def _ids(self) -> list[int]:
        return [it.data(0, ID) for it in self.tree.selectedItems()]

    def add_search(self):
        from .browse import GroupDialog
        tab = self.app.browse.current()
        d = GroupDialog(self, self.app, tab.query.text().strip() if tab else "", tab.sites if tab else self.app.st.get("sites"))
        if d.exec():
            self.app.add_group(**d.values())

    def add_one(self):
        clip = QtWidgets.QApplication.clipboard().text().strip()
        addr = K.ask_text(self, "Add a picture", "Page address of the picture (open it on the site and copy the address):",
                          clip if clip.startswith(("http://", "https://")) else "")
        if not addr or not addr.strip():
            return
        from ..grab.search import find_post
        self.app.flash("Looking up the picture...")
        self.app.worker.submit(find_post, self.app.store, addr.strip(),
                               on_done=lambda r: self.app.add_to_downloads_images([r]),
                               on_error=lambda e: K.warn(self, "Add a picture", uikit.friendly(str(e))))

    def edit(self):
        ids = self._ids()
        if len(ids) != 1 or self.running():
            return
        it = next(iter(self.app.lib.batch_items([ids[0]])), None)
        if not it or it["kind"] != "group":
            return
        from .browse import GroupDialog
        d0 = it["data"]
        dlg = GroupDialog(self, self.app, d0.get("search", ""), d0.get("sites") or [], data=d0)
        if dlg.exec():
            d = dlg.values()
            d0.update({"search": d["query"], "sites": d["sites"], "page": d["page"], "per_page": d["per_page"],
                       "limit": d["limit"], "path": d["path"], "filename": d["filename"], "postfilter": d["postfilter"],
                       "get_blacklisted": d["get_blacklisted"], "previous": {}, "done": {}, "finished_sites": []})
            self.app.lib.batch_update(it["id"], status="pending", data=d0, progress={})
            self.refresh()

    def _menu(self, pos):
        m = QtWidgets.QMenu(self)
        ids = self._ids()
        if ids:
            m.addAction("Start only these", lambda: self.start(selected=True)).setEnabled(not self.running())
            m.addAction("Edit...", self.edit).setEnabled(len(ids) == 1 and not self.running())
            m.addAction("Start again from the beginning", self.reset)
            m.addAction("Open its folder", self.open_folder)
            m.addSeparator()
            m.addAction("Remove", self.remove)
        m.addAction("Add a search...", self.add_search)
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def remove(self):
        ids = self._ids()
        if ids and not self.running():
            self.app.lib.batch_remove(ids)
            self.refresh()
        elif ids:
            self.app.flash("Stop the downloads first, then remove rows.")

    def move(self, d):
        ids = self._ids()
        if len(ids) == 1 and not self.running():
            self.app.lib.batch_move(ids[0], d)
            self.refresh()

    def clear_done(self):
        self.app.lib.batch_clear(only_done=True)
        self.refresh()

    def reset(self):
        if self.running():
            return
        for i in self._ids():
            it = next(iter(self.app.lib.batch_items([i])), None)
            if not it:
                continue
            d = it["data"]
            d.update({"previous": {}, "done": {}, "finished_sites": []})
            self.app.lib.batch_update(i, status="pending", data=d, progress={})
        self.refresh()

    def open_folder(self):
        ids = self._ids()
        path = ""
        if ids:
            it = next(iter(self.app.lib.batch_items([ids[0]])), None)
            path = (it or {}).get("data", {}).get("path") or ""
        path = path or self.app.st.save_path()
        os.makedirs(path, exist_ok=True)
        uikit.open_path(path)

    def save_list(self):
        p, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save the download list", "packrat-list.json", "Lists (*.json)")
        if p:
            items = [{"kind": it["kind"], "data": it["data"]} for it in self.app.lib.batch_items()]
            Path(p).write_text(json.dumps({"packrat_list": 1, "items": items}, indent=1, ensure_ascii=False), "utf-8")
            self.app.flash(f"{len(items)} row(s) saved")

    def load_list(self):
        p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Load a download list", "", "Lists (*.json *.igl);;All files (*)")
        if not p:
            return
        try:
            if p.lower().endswith(".igl"):
                from ..grab.grabber_import import load_igl
                n, problems = load_igl(p, self.app.lib, self.app.store)
                if problems:
                    K.info(self, "Load a list", "\n".join(problems[:12]))
            else:
                d = json.loads(Path(p).read_text("utf-8-sig"))
                n = 0
                for it in d.get("items") or []:
                    if it.get("kind") in ("group", "unique") and isinstance(it.get("data"), dict):
                        self.app.lib.batch_add(it["kind"], it["data"])
                        n += 1
            self.app.flash(f"{n} row(s) added")
        except (OSError, ValueError) as e:
            K.warn(self, "Load a list", f"This file could not be read: {e}")
        self.refresh()

    # ---- running ---------------------------------------------------------------------------------
    def running(self) -> bool:
        return self.runner.running

    def _buttons(self):
        run = self.running()
        self.start_btn.setEnabled(not run)
        self.stop_btn.setEnabled(run)
        self.pause_btn.setEnabled(run)
        self.pause_btn.setText("Resume" if self.paused else "Pause")

    def start(self, selected=False, ids=None):
        if self.running():
            return
        if ids is None and selected:
            ids = self._ids()
            if not ids:
                return
        argv = [cp.child_python(), "-m", "packrat", "batch"]
        if ids:
            argv += ["--ids", *map(str, ids)]
        env = cp.child_env({})
        if os.environ.get("PACKRAT_HOME"):
            env["PACKRAT_HOME"] = os.environ["PACKRAT_HOME"]
        self.stats, self.paused = {}, False
        self.phase.setText("Starting...")
        self.statline.setText("")
        self.curfile.setText("")
        try:
            self.runner.start(argv, env=env)
        except (OSError, RuntimeError) as e:
            K.warn(self, "Start", str(e))
            return
        self.app.log_line("Download list started", logging.INFO)
        self.app.busy("Downloading...")
        self._buttons()

    def stop(self):
        if self.running():
            self.runner.stop()
            self.phase.setText("Stopping (finishing the files being written)...")

    def kill(self):
        if self.running():
            self.runner.kill()

    def pause(self):
        if not self.running():
            return
        self.paused = not self.paused
        (self.runner.pause if self.paused else self.runner.resume)()
        self.phase.setText("Paused" if self.paused else "Downloading...")
        self._buttons()

    def _poll(self):
        changed = False
        try:
            for _ in range(400):
                msg = self.runner.q.get_nowait()
                if msg[0] == "event":
                    changed |= self._event(msg[1], msg[2])
                elif msg[0] == "line":
                    self.app.log_line(msg[1], msg[2])
                elif msg[0] == "exit":
                    self._exited(msg[1], msg[2])
                    changed = True
        except Exception:  # noqa: BLE001 - queue.Empty
            pass
        self._tick += 1
        if changed or (self.running() and self._tick % 8 == 0):
            self.refresh()

    def _event(self, kind, d) -> bool:
        if kind == "phase":
            self.phase.setText(str(d.get("name", "")).capitalize())
        elif kind == "grab_stats":
            self.stats = s = d
            dup = s.get("duplicate", 0) + s.get("copied", 0) + s.get("moved", 0) + s.get("linked", 0)
            self.statline.setText(
                f"{s.get('saved', 0):,} saved ({K.fmt_bytes(s.get('bytes'))})   ·   {s.get('exists', 0):,} already there   ·   "
                f"{dup:,} duplicates   ·   {s.get('blacklisted', 0) + s.get('filtered', 0):,} filtered   ·   "
                f"{s.get('failed', 0) + s.get('missing', 0):,} failed"
                + (f"   ·   {s['forbidden']} refused (403)" if s.get("forbidden") else ""))
        elif kind == "grab_bytes":
            tot = d.get("total") or 0
            self.fbar.setValue(int(d.get("n", 0) / tot * 1000) if tot else 0)
        elif kind == "grab_image":
            self.curfile.setText(f"{d.get('status')}: {d.get('path') or d.get('message') or d.get('key')}")
            if d.get("status") in ("failed", "forbidden", "missing"):
                self.app.log_line(f"{d.get('key')}: {d.get('status')} {d.get('message', '')}", logging.WARNING)
        elif kind in ("grab_item", "grab_group", "grab_page"):
            if kind == "grab_page" and d.get("error"):
                self.app.log_line(f"{d.get('site')} page {d.get('page')}: {d.get('error')}", logging.WARNING)
            return kind == "grab_item"
        elif kind == "grab_done":
            self.phase.setText("Finished")
        return False

    def _exited(self, code, crashed):
        msgs = {0: "Finished", 130: "Stopped - Start continues where it stopped",
                3: "Stopped: a site refused access (403). See the Log page.",
                2: "Not started: check the settings (see the Log page)"}
        self.phase.setText(msgs.get(code, f"Ended with an error (code {code}) - see the Log page"))
        self.app.busy("")
        self.fbar.setValue(0)
        self.paused = False
        self._buttons()
        try:
            self.app.browse.refresh_saved()
        except Exception:  # noqa: BLE001 - only a display nicety
            pass
        s = self.stats
        if code == 0 and s:
            end = str(self.app.st.get("Batch/end") or "nothing")
            text = f"Downloads finished: {s.get('saved', 0)} saved, {s.get('failed', 0) + s.get('missing', 0)} failed"
            if end in ("notify", "shutdown") or not self.app.isActiveWindow():
                self.app.notify("Packrat", text)
            if end == "sound":
                QtWidgets.QApplication.beep()
            if end == "shutdown" and os.name == "nt":
                import subprocess
                if K.ask(self.app, "Shut down", "Downloads are finished. Shut down the PC in 60 seconds?", "Shut down",
                         "Cancel", default_yes=False):
                    subprocess.Popen(["shutdown", "/s", "/t", "60"])


# ==================================================================================================
class FavoritesPage(Page):
    KEY, ICO = "favorites", "star"
    def __init__(self, app):
        super().__init__(app, "Favorites", "Searches you keep. 'Check for new pictures' counts what was posted since you last "
                         "opened each one.")
        self.new_counts: dict = dict(app.lib.get_state("favorite_new") or {})
        b = self.bar
        b.addWidget(K.button("Open", lambda: self.open(), ico="search"))
        b.addWidget(K.button("Add...", self.add, ico="plus"))
        b.addWidget(K.button("Edit...", self.edit))
        b.addWidget(K.button("Remove", self.remove))
        b.addWidget(K.button("Monitor...", self.monitor, ico="bell", tip="Check the selected favorite by itself"))
        b.addStretch(1)
        self.msg = K.label("", muted=True)
        b.addWidget(self.msg)
        b.addWidget(K.button("Download new pictures", self.download_new, ico="download"))
        b.addWidget(K.button("Check for new pictures", self.check_new, accent=True, ico="refresh"))
        self.tree = K.tree(("", "Favorite", "New", "Liked", "Last opened", "Sites", "Filter"), (64, 280, 70, 70, 130, 200))
        self.tree.setIconSize(QtCore.QSize(52, 52))
        self.tree.setUniformRowHeights(False)
        self.tree.itemDoubleClicked.connect(lambda it, _c: self.open(it.data(1, ID)))
        self.tree.customContextMenuRequested.connect(self._menu)
        self.later = QtWidgets.QListWidget()
        self.later.itemDoubleClicked.connect(lambda it: self.open(it.text(), mark=False))
        self.later.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.later.customContextMenuRequested.connect(self._later_menu)
        right = K.widget(K.vbox(K.label("Kept for later", role="heading"),
                                K.label("A reminder list: right-click a tag > Keep for later.", muted=True, wrap=True),
                                (self.later, 1), spacing=6))
        split = QtWidgets.QSplitter()
        split.addWidget(self.stacked(self.tree, "No favorites yet", "A favorite is a search you keep. In Browse, search for "
                                     "something and click <b>Favorite</b>, or right-click a tag."))
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setSizes([900, 240])
        self.body.addWidget(split, 1)
        self.refresh()

    def shown(self):
        self.refresh()

    def refresh(self):
        sel = {it.data(1, ID) for it in self.tree.selectedItems()}
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        favs = self.app.lib.favorites()
        for f in favs:
            new = self.new_counts.get(f["name"], "")
            row = K.SortItem(["", f["name"], str(new), str(f["note"]), K.ago(f["last_viewed"]) if f["last_viewed"] else "never",
                              ", ".join(f["sites"]) or "the usual sites", f["postfilter"]])
            row.setData(1, ID, f["name"])
            row.setData(2, SORT, int(str(new).rstrip("+") or 0))
            row.setData(3, SORT, int(f["note"] or 0))
            row.setData(4, SORT, float(f["last_viewed"] or 0))
            row.setSizeHint(0, QtCore.QSize(64, 58))
            if new:
                row.setForeground(2, QtGui.QColor(self.ink()))
            self.tree.addTopLevelItem(row)
            if f["name"] in sel:
                row.setSelected(True)
            self._thumb(f, row)
        self.tree.setSortingEnabled(True)
        self.show_empty(not favs)
        self.later.clear()
        self.later.addItems(self.app.lib.later())
        total = sum(1 for v in self.new_counts.values() if v)
        self.app.nav["favorites"].setText(f"  Favorites ({total} new)" if total else "  Favorites")

    def _thumb(self, f, row):
        try:
            d = json.loads(f["image"]) if f["image"] else None
        except ValueError:
            d = None
        if not d or not d.get("url"):
            return
        site = self.app.store.get(d.get("site") or "")
        if site is None:
            return
        from ..grab.image import Image
        im = Image(site.host)
        im.preview_url = d["url"]

        def cb(pm, row=row):
            try:
                if pm is not None:
                    row.setIcon(0, QtGui.QIcon(pm))
            except RuntimeError:
                pass   # the list was refreshed in the meantime
        self.app.thumbs.get(site, im, cb, size=104)

    def themed(self):
        self.refresh()

    def _sel(self) -> list[str]:
        return [it.data(1, ID) for it in self.tree.selectedItems()]

    def open(self, name=None, mark=True):
        name = name or next(iter(self._sel()), None)
        if not name:
            return
        f = self.app.lib.favorite(name) or {}
        sites = [h for h in (f.get("sites") or []) if self.app.store.get(h)] or None
        t = self.app.new_search_tab(name, sites, focus=True, run=False)
        t.filter.setText(f.get("postfilter") or "")
        t.search()
        if mark and f:
            self.app.lib.update_favorite(name, last_viewed=time.time())
            self.new_counts.pop(name, None)
            self.app.lib.set_state("favorite_new", self.new_counts)
        self.app.show_page("browse")

    def add(self):
        name = K.ask_text(self, "Add a favorite", "The search to keep (tags):")
        if name and name.strip():
            self.app.lib.add_favorite(name.strip(), self.app.st.get("sites"))
            self.refresh()

    def edit(self):
        name = next(iter(self._sel()), None)
        f = self.app.lib.favorite(name) if name else None
        if not f:
            return
        from .browse import FormDialog, SitesField
        dlg = FormDialog(self, f"Favorite: {name}", ok="Save")
        note = QtWidgets.QSpinBox()
        note.setRange(0, 100)
        note.setValue(int(f["note"] or 0))
        note.setToolTip("How much you like it, 0-100. Only used to sort the list.")
        sites = SitesField(self.app, f["sites"] or self.app.st.get("sites"))
        pf = QtWidgets.QLineEdit(f["postfilter"])
        pf.setPlaceholderText("optional, e.g. score:>10")
        dlg.form.addRow("Liked (0-100)", note)
        dlg.form.addRow("Sites", sites)
        dlg.form.addRow("Filter", pf)
        if dlg.exec():
            self.app.lib.update_favorite(name, note=note.value(), sites=list(sites.hosts), postfilter=pf.text().strip())
            self.refresh()

    def remove(self):
        names = self._sel()
        if names and K.ask(self, "Remove", f"Remove {len(names)} favorite(s)? Downloaded pictures stay.", "Remove", "Cancel"):
            for n in names:
                self.app.lib.remove_favorite(n)
                self.new_counts.pop(n, None)
            self.refresh()

    def monitor(self):
        name = next(iter(self._sel()), None)
        f = self.app.lib.favorite(name) if name else None
        if not f:
            return
        from .browse import MonitorDialog
        d = MonitorDialog(self, self.app, {"search": name, "sites": f["sites"], "postfilter": f["postfilter"]})
        if d.exec():
            self.app.lib.add_monitor(**d.values())
            self.app.monitors.refresh()
            self.app.scheduler.start()
            self.app.flash("Monitor added.")

    def _menu(self, pos):
        m = QtWidgets.QMenu(self)
        if self._sel():
            m.addAction("Open", lambda: self.open())
            m.addAction("Download all its pictures...", self._get_all)
            m.addAction("Edit...", self.edit)
            m.addAction("Monitor...", self.monitor)
            m.addSeparator()
            m.addAction("Remove", self.remove)
        m.addAction("Add...", self.add)
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def _later_menu(self, pos):
        it = self.later.itemAt(pos)
        if it is None:
            return
        name = it.text()
        m = QtWidgets.QMenu(self)
        m.addAction("Search it", lambda: self.open(name, mark=False))
        m.addAction("Make it a favorite", lambda: (self.app.lib.add_favorite(name, self.app.st.get("sites")),
                                                   self.app.lib.remove_later(name), self.refresh()))
        m.addAction("Remove", lambda: (self.app.lib.remove_later(name), self.refresh()))
        m.exec(self.later.viewport().mapToGlobal(pos))

    def _get_all(self):
        name = next(iter(self._sel()), None)
        f = self.app.lib.favorite(name) if name else None
        if not f:
            return
        from .browse import GroupDialog
        d = GroupDialog(self, self.app, name, f["sites"] or self.app.st.get("sites"), f["postfilter"])
        if d.exec():
            self.app.add_group(**d.values())

    def check_new(self):
        favs = self.app.lib.favorites()
        if not favs:
            return
        self.msg.setText(f"Checking {len(favs)} favorite(s)...")
        from ..grab import search as S
        app = self.app

        def work():
            out = {}
            for f in favs:
                sites = [s for s in (app.store.get(h) for h in (f["sites"] or app.st.get("sites") or [])) if s]
                if not sites:
                    continue
                res = S.run(f["name"], sites, 1, 40, app.st, None, postfilter=f["postfilter"], mark_downloaded=False)
                lv = f["last_viewed"] or 0
                new = [sh for sh in res.items if sh.image.date and sh.image.date.timestamp() > lv]
                out[f["name"]] = (len(new), len(res.items), res.items[0] if res.items else None)
            return out

        def done(out):
            for name, (n, total, first) in out.items():
                self.new_counts[name] = (f"{n}+" if n == total and total >= 40 else str(n)) if n else ""
                if first is not None:
                    url = first.image.preview_url or first.image.sample_url
                    if url:
                        app.lib.update_favorite(name, image=json.dumps({"site": first.site.host, "url": url}))
            app.lib.set_state("favorite_new", self.new_counts)
            self.msg.setText("Checked " + datetime.now().strftime("%H:%M") + ".")
            self.refresh()
        app.worker.submit(work, on_done=done, on_error=lambda e: self.msg.setText(f"Check failed: {uikit.friendly(str(e))}"))

    def download_new(self):
        names = self._sel() or [n for n, v in self.new_counts.items() if v]
        if not names:
            self.msg.setText("Nothing new (press 'Check for new pictures' first, or select favorites).")
            return
        for n in names:
            f = self.app.lib.favorite(n)
            if not f:
                continue
            since = datetime.fromtimestamp(f["last_viewed"] or 0, timezone.utc).strftime("%Y-%m-%d")
            pf = " ".join(x for x in (f["postfilter"], f"date:>={since}") if x)
            self.app.add_group(n, f["sites"] or self.app.st.get("sites"), postfilter=pf)
            self.app.lib.update_favorite(n, last_viewed=time.time())
            self.new_counts.pop(n, None)
        self.app.lib.set_state("favorite_new", self.new_counts)
        self.refresh()
        self.app.show_page("downloads")


# ==================================================================================================
def every(minutes) -> str:
    m = int(minutes or 0)
    if m % 1440 == 0 and m:
        return "every day" if m == 1440 else f"every {m // 1440} days"
    if m % 60 == 0 and m:
        return "every hour" if m == 60 else f"every {m // 60} hours"
    return f"every {m} min"


class MonitorsPage(Page):
    KEY, ICO = "monitors", "bell"
    def __init__(self, app):
        super().__init__(app, "Monitors", "Searches that check themselves. They run while Packrat is open (it can sit next "
                         "to the clock: Settings > Monitoring), and report pictures posted after they were created.")
        b = self.bar
        b.addWidget(K.button("Add...", self.add, ico="plus"))
        b.addWidget(K.button("Edit...", self.edit))
        b.addWidget(K.button("Remove", self.remove))
        b.addWidget(K.button("Pause / resume", self.toggle, tip="Paused monitors are not checked. More in the right-click menu."))
        b.addWidget(K.button("Open the search", self.open, ico="search"))
        b.addStretch(1)
        self.msg = K.label("", muted=True)
        b.addWidget(self.msg)
        b.addWidget(K.button("Check now", self.check_now, accent=True, ico="refresh"))
        self.tree = K.tree(("Search", "Sites", "Checks", "New", "Last check", "Next check", "Does", "Problem"),
                           (260, 190, 110, 60, 110, 110, 150))
        self.tree.itemDoubleClicked.connect(lambda *_: self.edit())
        self.tree.customContextMenuRequested.connect(self._menu)
        self.body.addWidget(self.stacked(self.tree, "No monitors yet", "A monitor checks a search every few hours, tells you "
                                         "when there are new pictures and can download them.<br>Add one here, or with "
                                         "<b>Monitor...</b> in a search tab."), 1)
        self.timer = QtCore.QTimer(self, interval=30_000, timeout=self.refresh)
        self.timer.start()
        self.refresh()

    def shown(self):
        self.refresh()

    def themed(self):
        self.refresh()

    def refresh(self):
        sel = set(self._ids())
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        mons = self.app.lib.monitors()
        new_total = 0
        for m in mons:
            does = " + ".join(x for x in ("notifies" if m["notify"] else "", "downloads" if m["download"] else "") if x) or "counts"
            row = K.SortItem([m["search"] or "(everything)", ", ".join(m["sites"]), every(m["interval_min"]),
                              str(m.get("new_count") or ""), K.ago(m["last_check"]),
                              "paused" if not m["active"] else K.ago(m["next_check"]), does, m.get("last_error") or ""])
            row.setData(0, ID, m["id"])
            row.setData(3, SORT, int(m.get("new_count") or 0))
            row.setData(4, SORT, float(m["last_check"] or 0))
            row.setData(5, SORT, float(m["next_check"] or 0))
            if not m["active"]:
                for c in range(8):
                    row.setForeground(c, QtGui.QColor(C["muted"]))
            if m.get("last_error"):
                row.setForeground(7, QtGui.QColor(C["error"]))
            if m.get("new_count"):
                row.setForeground(3, QtGui.QColor(self.ink()))
                new_total += int(m["new_count"])
            self.tree.addTopLevelItem(row)
            if m["id"] in sel:
                row.setSelected(True)
        self.tree.setSortingEnabled(True)
        self.show_empty(not mons)
        self.app.nav["monitors"].setText(f"  Monitors ({new_total} new)" if new_total else "  Monitors")

    def _ids(self) -> list[int]:
        return [it.data(0, ID) for it in self.tree.selectedItems()]

    def add(self):
        from .browse import MonitorDialog
        tab = self.app.browse.current()
        d = MonitorDialog(self, self.app, {"search": tab.query.text().strip() if tab else "",
                                           "sites": tab.sites if tab else self.app.st.get("sites")})
        if d.exec():
            self.app.lib.add_monitor(**d.values())
            self.app.scheduler.start()
            self.refresh()

    def edit(self):
        ids = self._ids()
        m = self.app.lib.monitor(ids[0]) if len(ids) == 1 else None
        if not m:
            return
        from .browse import MonitorDialog
        d = MonitorDialog(self, self.app, m)
        if d.exec():
            v = d.values()
            v["next_check"] = (m["last_check"] or time.time()) + v["interval_min"] * 60
            self.app.lib.update_monitor(m["id"], **v)
            self.refresh()

    def remove(self):
        ids = self._ids()
        if ids and K.ask(self, "Remove", f"Remove {len(ids)} monitor(s)?", "Remove", "Cancel"):
            for i in ids:
                self.app.lib.remove_monitor(i)
            self.refresh()

    def check_now(self):
        ids = self._ids() or None
        if not self.app.lib.monitors():
            return
        self.app.scheduler.start()
        self.app.scheduler.check_now(ids)
        self.msg.setText("Checking...")

    def mark_seen(self):
        for i in self._ids():
            self.app.lib.update_monitor(i, new_count=0)
        self.refresh()

    def toggle(self):
        for i in self._ids():
            m = self.app.lib.monitor(i)
            if m:
                self.app.lib.update_monitor(i, active=int(not m["active"]))
        self.refresh()

    def open(self):
        ids = self._ids()
        m = self.app.lib.monitor(ids[0]) if ids else None
        if m:
            t = self.app.new_search_tab(m["search"], [h for h in m["sites"] if self.app.store.get(h)] or None, run=False)
            t.filter.setText(m.get("postfilter") or "")
            t.search()
            self.app.lib.update_monitor(m["id"], new_count=0)
            self.app.show_page("browse")

    def _menu(self, pos):
        menu = QtWidgets.QMenu(self)
        if self._ids():
            for text, fn in (("Check now", self.check_now), ("Open the search", self.open), ("Edit...", self.edit),
                             ("Mark as seen", self.mark_seen), ("Pause / resume", self.toggle)):
                menu.addAction(text, fn)
            menu.addSeparator()
            menu.addAction("Remove", self.remove)
        menu.addAction("Add...", self.add)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def checking(self, mon):
        self.msg.setText(f"Checking '{mon.get('search') or '(everything)'}'...")

    def result(self, mon, res):
        n = len(res["new"])
        name = mon["search"] or "(everything)"
        if res["errors"]:
            self.msg.setText(f"'{name}': {uikit.friendly(res['errors'][0])}")
        elif res["first"]:
            self.msg.setText(f"'{name}': first check done - new pictures are reported from now on")
        else:
            self.msg.setText(f"'{name}': {n} new picture(s)" if n else f"'{name}': nothing new")
        self.refresh()
        if n and mon.get("notify"):
            self.app.notify("Packrat", f"{n} new picture(s) for '{name}'")
        if n and mon.get("download"):
            dl = self.app.downloads
            dl.refresh()
            if not dl.running():
                ids = [it["id"] for it in self.app.lib.batch_items() if it["kind"] == "unique"
                       and it.get("status") != "done" and it["data"].get("from_monitor") == mon["id"]]
                if ids:
                    dl.start(ids=ids)


# ==================================================================================================
class LogPage(Page):
    KEY, ICO = "log", "list"
    MAX = 5000

    def __init__(self, app):
        super().__init__(app, "Log", "What happened, and any problems. The same text is kept in data\\logs in the Packrat folder.")
        self.view = QtWidgets.QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(self.MAX)
        self.view.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        f = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)
        f.setPointSize(9)
        self.view.setFont(f)
        self.only = QtWidgets.QCheckBox("Problems only")
        self.only.toggled.connect(self._refill)
        self.lines: list[tuple[str, int]] = []
        self.bar.addWidget(self.only)
        self.bar.addStretch(1)
        self.bar.addWidget(K.button("Copy all", lambda: QtWidgets.QApplication.clipboard().setText(self.view.toPlainText())))
        self.bar.addWidget(K.button("Clear", self.clear))
        self.bar.addWidget(K.button("Open the log folder", self._open_folder, ico="folder"))
        self.body.addWidget(self.view, 1)
        self.problems = 0

    def _open_folder(self):
        from .. import paths
        uikit.open_path(str(paths.logs_dir()))

    def _fmt(self, level: int) -> QtGui.QTextCharFormat:
        f = QtGui.QTextCharFormat()
        f.setForeground(QtGui.QColor(C["error"] if level >= logging.ERROR else C["warn"] if level >= logging.WARNING
                                     else C["text"]))
        return f

    def add(self, text: str, level: int = logging.INFO):
        self.lines.append((text, level))
        if len(self.lines) > self.MAX:
            del self.lines[:500]
        if level >= logging.WARNING:
            self.problems += 1
            if self.app.current_page() != "log":
                self.app.nav["log"].setText(f"  Log ({self.problems})")
        if self.only.isChecked() and level < logging.WARNING:
            return
        self._append(text, level)

    def _append(self, text, level):
        sb = self.view.verticalScrollBar()
        at_end = sb.value() >= sb.maximum() - 4
        cur = self.view.textCursor()
        cur.movePosition(QtGui.QTextCursor.MoveOperation.End)
        cur.insertText(text + "\n", self._fmt(level))
        if at_end:
            sb.setValue(sb.maximum())

    def _refill(self, *_):
        self.view.clear()
        for text, level in self.lines:
            if not self.only.isChecked() or level >= logging.WARNING:
                self._append(text, level)

    def shown(self):
        self.problems = 0
        self.app.nav["log"].setText("  Log")

    def clear(self):
        self.lines.clear()
        self.view.clear()

    def themed(self):
        self._refill()
