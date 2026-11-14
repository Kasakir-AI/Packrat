"""The Tools page: import from Grabber, rename files you have, fix lists, load tag lists, site add-ons,
scheduled monitor checks, backups. Each tool says what it will do before it changes anything."""
from __future__ import annotations

import os
import threading
from datetime import datetime

from PySide6 import QtCore, QtWidgets

from .. import control_panel as cp
from .. import paths, uikit
from . import common as K
from .common import Qt


class ToolsPage(QtWidgets.QWidget):
    TOOLS = (
        ("Import from Grabber", "Your Grabber settings, favorites, monitors, logins, downloaded-file list and download "
         "queue. Grabber itself is not changed.", "import_grabber"),
        ("Rename existing files", "Give files you already have new names with a file name format. Shows the list before "
         "moving anything.", "rename"),
        ("MD5 list fixer", "Teach Packrat the pictures in a folder, so they are never downloaded again.", "md5_fixer"),
        ("Blacklist fixer", "Find saved pictures that match your blacklist. Shows them first.", "blacklist_fixer"),
        ("Empty folders", "Find and remove empty folders.", "empty_dirs"),
        ("Tag loader", "Download a site's tag list, so artist / character names are known and suggested faster.", "tag_loader"),
        ("Site add-ons", "Add or update sites from Grabber's online list (checked by checksum).", "registries"),
        ("Schedule monitors", "Check monitors every hour while Packrat is closed (Windows Task Scheduler).", "schedule"),
        ("Back up the library", "A copy of your favorites, monitors and lists.", "backup"),
        ("Clear the caches", "Delete previews and pictures kept to show them faster. Nothing you saved is touched.", "clear_cache"),
        ("Desktop shortcut", "Put a Packrat icon on the desktop (and the Start menu).", "shortcut"),
    )

    ICONS = {"import_grabber": "import", "rename": "pencil", "md5_fixer": "hash", "blacklist_fixer": "shield",
             "empty_dirs": "folder", "tag_loader": "tag", "registries": "addon", "schedule": "clock", "backup": "save",
             "clear_cache": "sparkle", "shortcut": "screen"}

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.cancel = threading.Event()
        self.busy = False
        self.setProperty("hue", K.PAGE_HUE["tools"])
        grid = QtWidgets.QGridLayout()
        grid.setSpacing(12)
        for i, (title, text, fn) in enumerate(self.TOOLS):
            tint = K.HUES[i % len(K.HUES)]       # the tools are a row of differently coloured drawers
            b = K.button("Open", getattr(self, fn), accent=True)
            b.setProperty("hue", tint)
            card = K.widget(K.vbox(K.hbox(K.tile_label(self.ICONS.get(fn, "wrench"), tint, 36),
                                          (K.label(title, role="heading", wrap=True), 1), spacing=11),
                                   K.label(text, muted=True, wrap=True), None,
                                   K.hbox(b, None), margins=(16, 14, 16, 14), spacing=8), "card")
            card.setMinimumHeight(158)
            grid.addWidget(card, i // 3, i % 3)
        holder = QtWidgets.QWidget()
        holder.setLayout(grid)
        sc = QtWidgets.QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(holder)
        self.out = QtWidgets.QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setMaximumBlockCount(4000)
        self.out.setPlaceholderText("What a tool finds and does is listed here.")
        self.prog = K.label("", muted=True)
        self.cancel_btn = K.button("Cancel", self.cancel.set)
        self.cancel_btn.setEnabled(False)
        split = QtWidgets.QSplitter(Qt.Orientation.Vertical)
        split.addWidget(sc)
        split.addWidget(K.widget(K.vbox(K.hbox(K.label("Results", role="heading"), self.prog, None, self.cancel_btn,
                                               K.button("Clear", self.out.clear)), (self.out, 1), spacing=6)))
        split.setSizes([540, 200])
        self.setLayout(K.vbox(K.page_head("tools", "wrench", "Tools", "Jobs you run now and then: bring things over from "
                                          "Grabber, tidy what you have saved, keep a backup."),
                              (split, 1), margins=(22, 18, 22, 14), spacing=12))

    # ---- plumbing --------------------------------------------------------------------------------
    def say(self, text: str):
        self.out.appendPlainText(str(text))

    def _progress(self, n, what=""):
        self.app.worker.call(self.prog.setText, f"{what} {n:,}" if isinstance(n, int) else f"{n} {what}".strip())

    def _run(self, title, fn, done):
        if self.busy:
            self.say("Another tool is still working. Wait for it, or press Cancel.")
            return
        self.busy = True
        self.cancel.clear()
        self.cancel_btn.setEnabled(True)
        self.say(f"--- {title} ---")
        self.prog.setText("working...")

        def finish():
            self.busy = False
            self.cancel_btn.setEnabled(False)
            self.prog.setText("")

        def ok(r):
            finish()
            try:
                done(r)
            except Exception as e:  # noqa: BLE001
                self.say(f"Problem: {e}")

        def bad(e):
            finish()
            self.say("Stopped: " + uikit.friendly(str(e)))
        self.app.worker.submit(fn, on_done=ok, on_error=bad)

    def _folder(self, title) -> str:
        d = QtWidgets.QFileDialog.getExistingDirectory(self, title, self.app.st.save_path())
        return os.path.normpath(d) if d else ""

    def _site(self, title):
        names = {f"{s.name}  ({s.host})": s for s in self.app.store.all_sites()}
        cur = next((k for k, s in names.items() if s.host in (self.app.st.get("sites") or [])), None)
        keys = sorted(names)
        val, ok = QtWidgets.QInputDialog.getItem(self, title, "Which site do the files come from?", keys,
                                                 keys.index(cur) if cur in keys else 0, False)
        return names[val] if ok and val else None

    # ---- tools -----------------------------------------------------------------------------------
    def import_grabber(self, folder=None):
        from ..grab import grabber_import as G
        if not folder:
            found = G.find_profiles()
            folder = QtWidgets.QFileDialog.getExistingDirectory(
                self, "Grabber's settings folder (the one with settings.ini)", str(found[0]) if found else "")
        if not folder:
            return
        if not G.is_profile(folder):
            K.warn(self, "Import", "This folder has no Grabber settings (settings.ini, favorites.json...).")
            return
        summ = G.summary(folder)
        labels = {"settings": "Settings" + ("" if summ["settings"] else " (none)"),
                  "favorites": f"Favorites ({summ['favorites']})", "monitors": f"Monitors ({summ['monitors']})",
                  "later": f"Kept for later ({summ['later']})", "blacklist": "Blacklist and ignored tags",
                  "words": "Auto-complete words", "md5": f"MD5 list ({summ['md5']:,})",
                  "downloads": f"Download list ({summ['downloads']})", "sites": f"Sites and logins ({summ['sites']})"}
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Import from Grabber")
        boxes = {}
        lay = K.vbox(K.label(f"From {folder}", role="heading"), margins=(18, 16, 18, 14), spacing=8)
        for k in G.PARTS:
            boxes[k] = QtWidgets.QCheckBox(labels[k])
            boxes[k].setChecked(True)
            lay.addWidget(boxes[k])
        lay.addWidget(K.label("Importing twice is safe: what already exists is kept.", muted=True))
        lay.addLayout(K.hbox(None, K.button("Cancel", dlg.reject), K.button("Import", dlg.accept, accent=True)))
        dlg.setLayout(lay)
        if not dlg.exec():
            return
        parts = tuple(k for k, b in boxes.items() if b.isChecked())
        a = self.app

        def done(rep):
            self.say("Imported: " + ", ".join(f"{k}: {v}" for k, v in rep.items() if k != "notes"))
            for n in rep.get("notes") or []:
                self.say("  note: " + n)
            a.store.reload()
            a.favorites.refresh()
            a.monitors.refresh()
            a.downloads.refresh()
            a.settings_page.reload()
            a.browse.settings_changed()
        self._run("Import from Grabber", lambda: G.import_profile(folder, a.st, a.lib, a.store, parts,
                                                                  progress=lambda s: self._progress(str(s))), done)

    def md5_fixer(self):
        folder = self._folder("Folder to add to the MD5 list")
        if not folder:
            return
        from ..grab import tools as T
        self._run("MD5 list fixer", lambda: T.md5_fixer(self.app.lib, folder, progress=self._progress, cancel=self.cancel),
                  lambda r: self.say(f"{r['files']} files looked at, {r['added']} added to the MD5 list."))

    def empty_dirs(self):
        folder = self._folder("Folder to clean")
        if not folder:
            return
        from ..grab import tools as T

        def done(dirs):
            for d in dirs[:500]:
                self.say(d)
            if not dirs:
                self.say("No empty folders.")
            elif K.ask(self, "Empty folders", f"Remove these {len(dirs)} empty folder(s)?", "Remove", "Cancel", default_yes=False):
                self.say(f"{T.remove_dirs(dirs)} folder(s) removed.")
        self._run("Empty folders", lambda: T.empty_dirs(folder), done)

    def rename(self):
        folder = self._folder("Folder with the files to rename")
        if not folder:
            return
        site = self._site("Rename existing files")
        if site is None:
            return
        fmt = K.ask_text(self, "Rename existing files", "New file name format (Help > File names):", self.app.st.filename())
        if not fmt or not fmt.strip():
            return
        ex, problem = uikit.sample_example(fmt.strip(), self.app.st)
        if not ex:
            K.warn(self, "Rename existing files", problem)
            return
        from ..grab import tools as T
        a = self.app

        def done(plan):
            moving = [r for r in plan if r["status"] == "rename"]
            for r in plan[:1000]:
                self.say(f"{r['from']}  ->  {r['to']}" if r["status"] == "rename" else f"{r['from']}  ({r['status']})")
            if not moving:
                self.say("Nothing to rename.")
            elif K.ask(self, "Rename", f"Move {len(moving)} file(s) as listed?", "Move them", "Cancel", default_yes=False):
                res = T.apply_renames(a.lib, plan)
                self.say(f"{res['moved']} moved, {res['skipped']} unchanged.")
        self._run("Rename existing files", lambda: T.rename_existing_plan(
            a.store, a.st, a.lib, folder, site.host, fmt.strip(), progress=self._progress, cancel=self.cancel), done)

    def blacklist_fixer(self):
        folder = self._folder("Folder to check against the blacklist")
        if not folder:
            return
        site = self._site("Blacklist fixer")
        if site is None:
            return
        from ..grab import tools as T
        a = self.app

        def done(hits):
            for h in hits[:1000]:
                self.say(f"{h['path']}   ({h['rule']})")
            if not hits:
                self.say("No saved file matches the blacklist.")
                return
            if K.ask(self, "Blacklist fixer", f"Delete these {len(hits)} file(s)? This cannot be undone.", "Delete",
                     "Keep them", default_yes=False):
                n = 0
                for h in hits:
                    try:
                        os.remove(h["path"])
                        a.lib.md5_remove(h["md5"])
                        n += 1
                    except OSError as e:
                        self.say(f"could not delete {h['path']}: {e}")
                self.say(f"{n} file(s) deleted.")
        self._run("Blacklist fixer", lambda: T.blacklist_fixer(a.store, a.st, a.lib, folder, site.host,
                                                               progress=self._progress, cancel=self.cancel), done)

    def tag_loader(self):
        site = self._site("Tag loader")
        if site is None:
            return
        from ..grab import tools as T
        self._run(f"Tag loader ({site.name})",
                  lambda: T.tag_loader(site, progress=lambda p, n: self._progress(n, f"page {p}, tags"), cancel=self.cancel),
                  lambda r: self.say(f"{r['tags']:,} tags loaded from the {r['source']}."))

    def registries(self):
        RegistryDialog(self, self.app).exec()

    def schedule(self):
        exe = cp.child_python()
        quiet = exe[:-len("python.exe")] + "pythonw.exe" if exe.lower().endswith("python.exe") else exe
        if getattr(__import__("sys"), "frozen", False):
            quiet = __import__("sys").executable        # Packrat.exe itself: no console window flashes up
        cmd = f'"{quiet if os.path.exists(quiet) else exe}" -m packrat monitors --run-due'
        self.say("--- Schedule monitors ---")
        self.say("To check monitors while Packrat is closed, this runs every hour (Windows Task Scheduler):")
        self.say("  " + cmd)
        if os.name != "nt":
            self.say("(Scheduling by itself only works on Windows. Elsewhere, add that command to cron.)")
            return
        if K.ask(self, "Schedule monitors", "Create an hourly Windows scheduled task for the monitors now?\n\nTask name: "
                 "Packrat monitors\n\nTip: with 'Closing the window keeps Packrat running' (Settings > Monitoring) you "
                 "don't need this.", "Create it", "Cancel"):
            import subprocess
            r = subprocess.run(["schtasks", "/Create", "/F", "/SC", "HOURLY", "/TN", "Packrat monitors", "/TR", cmd],
                               capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.say((r.stdout or r.stderr).strip() or "done")
            self.say('To remove it later: Task Scheduler > "Packrat monitors" > Delete.')

    def backup(self):
        dest = paths.data_dir() / "backups" / f"library-{datetime.now():%Y%m%d-%H%M%S}.sqlite3"
        p = self.app.lib.backup_to(dest)
        self.say(f"Library backed up to {p}")

    def clear_cache(self):
        def work():
            n = size = 0
            for kind in ("thumbs", "viewer"):
                d = paths.cache_dir(kind)
                for f in d.iterdir():
                    if f.is_file():
                        try:
                            size += f.stat().st_size
                            f.unlink()
                            n += 1
                        except OSError:
                            pass
            return n, size
        self._run("Clear the caches", work, lambda r: self.say(f"{r[0]:,} files removed, {K.fmt_bytes(r[1])} freed."))

    def shortcut(self):
        from ..winshell import create_shortcuts
        self.say("--- Desktop shortcut ---")
        try:
            made = create_shortcuts(desktop=True, start_menu=True)
        except Exception as e:  # noqa: BLE001
            self.say(f"Could not create the shortcut: {e}")
            return
        for p in made:
            self.say(f"Created {p}")
        if not made:
            self.say("Shortcuts are only created on Windows.")


class RegistryDialog(QtWidgets.QDialog):
    def __init__(self, parent, app):
        super().__init__(parent)
        from ..grab import registry as R
        self.R, self.app, self.entries = R, app, []
        self.setWindowTitle("Site add-ons")
        K.fit(self, 920, 560)
        st = R.load_state()
        self.url = QtWidgets.QComboBox()
        self.url.setEditable(True)
        self.url.addItems([R.OFFICIAL] + [u for u in st["registries"] if u != R.OFFICIAL])
        self.tree = K.tree(("Add-on", "Status", "Sites", "Last change"), (190, 140, 280), multi=False)
        self.msg = K.label("", muted=True, wrap=True)
        self.setLayout(K.vbox(
            K.label("Sites come from 'sources'. Grabber publishes updated ones online; installing one adds or updates "
                    "its sites. Each download is checked against the list's checksum.", muted=True, wrap=True),
            K.hbox(K.label("List"), (self.url, 1), K.button("Load", self.load, accent=True), K.button("Forget this list", self.forget)),
            (self.tree, 1), self.msg,
            K.hbox(K.button("Install / update", self.install, ico="download"), K.button("Remove", self.uninstall), None,
                   K.button("Close", self.accept)), margins=(16, 14, 16, 12), spacing=10))
        QtCore.QTimer.singleShot(50, self.load)

    def forget(self):
        url = self.url.currentText().strip()
        if url == self.R.OFFICIAL:
            self.msg.setText("The official list cannot be removed.")
            return
        self.R.remove_registry(url)
        self.url.removeItem(self.url.currentIndex())
        self.url.setCurrentText(self.R.OFFICIAL)

    def load(self):
        url = self.url.currentText().strip()
        self.msg.setText("Loading the list...")

        def done(reg):
            st = self.R.load_state()
            if url not in st["registries"] and url != self.R.OFFICIAL:
                st["registries"].append(url)
                self.R.save_state(st)
            self.entries = self.R.available([reg])
            self.tree.clear()
            for i, e in enumerate(self.entries):
                row = QtWidgets.QTreeWidgetItem([e["name"], e["status"], ", ".join(e["default_sites"][:4]),
                                                 f"{e['date'][:10]} {e['commit']}"])
                row.setData(0, Qt.ItemDataRole.UserRole, i)
                self.tree.addTopLevelItem(row)
            self.msg.setText(f"{len(self.entries)} add-ons in this list.")
        self.app.worker.submit(self.R.fetch, url, on_done=done,
                               on_error=lambda e: self.msg.setText("Could not load the list: " + uikit.friendly(str(e))))

    def _sel(self):
        it = self.tree.currentItem()
        return self.entries[it.data(0, Qt.ItemDataRole.UserRole)] if it else None

    def install(self, accept=False):
        e = self._sel()
        if not e:
            return
        self.msg.setText(f"Installing {e['name']}...")

        def done(_p):
            self.app.store.reload()
            self.msg.setText(f"{e['name']} installed. Its sites are in 'Sites and logins'.")
            self.load()

        def err(ex):
            if isinstance(ex, self.R.ChecksumMismatch) and K.ask(self, "Install", f"{ex}\n\nInstall anyway?", "Install anyway",
                                                                 "Cancel", default_yes=False):
                self.app.worker.submit(self.R.install, e, True, on_done=done, on_error=lambda x: self.msg.setText(str(x)))
            else:
                self.msg.setText(str(ex))
        self.app.worker.submit(self.R.install, e, bool(accept), on_done=done, on_error=err)

    def uninstall(self):
        e = self._sel()
        if e and self.R.uninstall(e["name"]):
            self.app.store.reload()
            self.msg.setText(f"{e['name']} removed.")
            self.load()
