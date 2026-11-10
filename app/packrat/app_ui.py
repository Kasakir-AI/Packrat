"""Packrat's main window.

Tabs: Browse (search tabs with thumbnails), Downloads (the download list), Favorites, Monitors, Site archive
(the e6ai / e621 whole-site archiver, as it was in e6ai-archiver 2.0), Settings, Tools, Log.

Everything slow (searches, thumbnails, logins, saving an image) runs in background threads and comes back
through ui_common.Worker; the download list runs in a separate process like the site archive does, so a
long download never makes the window stutter and Stop always works.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__, paths
from . import control_panel as cp
from . import theme as th
from . import ui_common as uc
from .ui_common import COLORS

log = logging.getLogger("packrat")


class UiLogHandler(logging.Handler):
    def __init__(self, app):
        super().__init__(logging.INFO)
        self.app = app
        from .redact import RedactingFormatter
        self.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))

    def emit(self, record):
        try:
            msg = self.format(record)
        except Exception:  # noqa: BLE001
            return
        self.app.worker.call(self.app.log_line, msg, record.levelno)


# =====================================================================================================
class PackratApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        from .grab import library, settings, sources
        self.st = settings.shared()
        self.lib = library.shared()
        self.store = sources.store()
        self.worker = uc.Worker(root)
        uc.set_scale(root)
        uc.ADVANCED[0] = bool(self.lib.get_state("advanced_view"))
        self.help_win = None
        self.closing = False
        self.search_tabs: list = []
        self.viewers: list = []
        self._saver = None
        self._save_lock = threading.Lock()
        root.title(f"Packrat {__version__}")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        self.head = ttk.Frame(root, padding=(10, 6, 10, 0))
        self.head.grid(row=0, column=0, sticky="ew")
        self.nb = ttk.Notebook(root)
        self.nb.grid(row=1, column=0, sticky="nsew", padx=6, pady=(4, 6))
        self.frames = {}
        for key, label in (("browse", "Browse"), ("downloads", "Downloads"), ("favorites", "Favorites"),
                           ("monitors", "Monitors"), ("archive", "Site archive"), ("settings", "Settings"),
                           ("tools", "Tools"), ("log", "Log")):
            f = ttk.Frame(self.nb)
            self.nb.add(f, text=label)
            self.frames[key] = f
        # the site archive panel sets up the ttk style, fonts and theme for the whole window
        from .panel_ui import ControlPanel
        self.panel = ControlPanel(root, cp.settings_path(), container=self.frames["archive"],
                                  on_closed=self._finish_close)
        self.f_bold, self.f_small, self.f_title = self.panel.f_bold, self.panel.f_small, self.panel.f_title
        self.f_big, self.f_mono = self.panel.f_big, self.panel.f_mono
        self.panel.on_theme = self._themed
        COLORS.update(th.PALETTES[self.panel._theme_name])
        self.dark = self.panel._theme_name == "dark"
        self._build_head()

        from .ui_pages import DownloadsPage, FavoritesPage, LogPage, MonitorsPage, SettingsPage, ToolsPage
        self.logpage = LogPage(self.frames["log"], self)
        self._handler = UiLogHandler(self)
        logging.getLogger("packrat").addHandler(self._handler)
        logging.getLogger("packrat").setLevel(logging.INFO)
        self.thumbs = uc.Thumbs(self.worker, uc.px(int(self.st.get("thumbnailSize") or 150)),
                                not bool(self.st.get("resizeInsteadOfCropping", True)))
        from .grab import login as _login
        _login.ASK = self._ask_from_thread
        self.browse = BrowsePage(self.frames["browse"], self)
        self.downloads = DownloadsPage(self.frames["downloads"], self)
        self.favorites = FavoritesPage(self.frames["favorites"], self)
        self.monitors = MonitorsPage(self.frames["monitors"], self)
        self.settings_page = SettingsPage(self.frames["settings"], self)
        self.tools = ToolsPage(self.frames["tools"], self)
        self._themed(th.PALETTES[self.panel._theme_name])
        self.apply_view()

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.report_callback_exception = self._callback_error
        root.bind("<Control-t>", lambda e: self.new_search_tab(focus=True))
        root.bind("<Control-w>", lambda e: self.browse.close_current())
        for key, d in (("<Alt-Left>", -1), ("<Alt-Right>", 1)):
            root.bind(key, lambda e, d=d: (self.browse.current().hist(d) if self.browse.current() is not None
                                           and self.nb.select() == str(self.frames["browse"]) else None))
        root.bind("<Control-D>", lambda e: self.panel.toggle_theme())
        root.bind("<Control-Return>", self._ctrl_enter)
        root.bind("<F5>", lambda e: self.browse.reload_current())
        root.bind("<F1>", lambda e: self.open_help(self._tab_topic()))
        self.browse.restore_session()
        from .grab.monitor import MonitorScheduler
        self.scheduler = MonitorScheduler(self.store, self.st, self.lib, self._monitor_result,
                                          lambda m: self.worker.call(self.monitors.checking, m))
        self.scheduler.start()
        self.root.after(1500, self._first_run_hints)

    # ---- header ---------------------------------------------------------------------------------
    def _build_head(self):
        h = self.head
        h.columnconfigure(1, weight=1)
        ttk.Label(h, text="Packrat", font=self.f_title).grid(row=0, column=0, sticky="w")
        self._subtitle = f"  {__version__}  -  browse, download and organize images from many sites"
        self.head_status = tk.StringVar(value=self._subtitle)
        self.head_lbl = ttk.Label(h, textvariable=self.head_status, style="Muted.TLabel")
        self.head_lbl.grid(row=0, column=1, sticky="w")
        h.columnconfigure(1, weight=1, minsize=0)
        tf = ttk.Frame(h)
        tf.grid(row=0, column=3, sticky="e")
        self.adv_var = tk.BooleanVar(value=uc.ADVANCED[0])
        ac = ttk.Checkbutton(tf, text="Advanced options", variable=self.adv_var,
                             command=lambda: self.set_advanced(self.adv_var.get()))
        ac.pack(side="left", padx=(0, 14))
        uc.tip(ac, "Off (simple view): only what you need to search, look and download.\n"
                   "On: also the Monitors, Tools and Log tabs, every setting, and more buttons.\n"
                   "Switching never loses anything you set.")
        self.help_btn = ttk.Button(tf, text="? Help ▾", style="Accent.TButton", command=self._help_menu)
        self.help_btn.pack(side="left", padx=(0, 14))
        uc.tip(self.help_btn, "Guides for everything in Packrat and every site, a search box for questions, "
                              "and the setup assistant (F1 opens the guide for this tab)")
        ttk.Label(tf, text="Theme", style="Muted.TLabel").pack(side="left", padx=(0, 4))
        self.theme_var = tk.StringVar(value=self.panel.theme_choice)
        for c in ("light", "dark", "system"):
            ttk.Radiobutton(tf, text=th.LABELS[c], value=c, variable=self.theme_var, style="Toolbutton",
                            command=lambda: self.panel.set_theme(self.theme_var.get())).pack(side="left", padx=(0, 2))

    def _themed(self, p):
        COLORS.clear()
        COLORS.update(p)
        self.dark = p.get("name") == "dark"
        try:
            self.theme_var.set(self.panel.theme_choice)
        except AttributeError:
            return   # still building
        for page in ("browse", "downloads", "favorites", "monitors", "settings_page", "tools", "logpage"):
            obj = getattr(self, page, None)
            if obj is not None and hasattr(obj, "themed"):
                obj.themed(p)
        for v in list(self.viewers):
            v.themed(p)
        uc.refresh_placeholders()
        if self.help_win is not None:
            try:
                self.help_win.themed(p)
            except tk.TclError:
                self.help_win = None

    # ---- simple / advanced view ----------------------------------------------------------------
    ADV_TABS = ("monitors", "tools", "log")

    def apply_view(self):
        """Show or hide what belongs to the advanced view. Nothing is stopped or lost: monitors keep running,
        hidden settings keep their values."""
        on = uc.ADVANCED[0]
        cur = self.nb.select()
        for key in self.ADV_TABS:
            f = self.frames[key]
            if on:
                self.nb.add(f)          # a hidden tab comes back at its old place
            else:
                if str(f) == cur:
                    self.nb.select(self.frames["browse"])
                self.nb.hide(f)
        uc.apply_advanced(on)
        for t in getattr(self.browse, "tabs", []):
            t.view_changed()
        self.settings_page.view_changed()
        try:
            self.adv_var.set(on)
        except AttributeError:
            pass

    def set_advanced(self, on: bool, quiet: bool = False):
        on = bool(on)
        if on == uc.ADVANCED[0]:
            return
        uc.ADVANCED[0] = on
        self.lib.set_state("advanced_view", on)
        self.apply_view()
        if self.help_win is not None:
            try:
                self.help_win.view_changed()
            except tk.TclError:
                self.help_win = None
        if not quiet:
            self.flash("Advanced options shown: Monitors, Tools and Log tabs, every setting, more buttons."
                       if on else "Simple view: only the everyday options. Everything you set still works.")

    def need_advanced(self, what: str) -> bool:
        """For a link to something that only the advanced view shows: ask once, then show it."""
        if uc.ADVANCED[0]:
            return True
        if messagebox.askyesno("Advanced options", f"{what} is part of the advanced options, hidden to keep "
                               "Packrat simple.\n\nShow the advanced options now?\n\n(You can hide them again any "
                               "time: untick 'Advanced options' at the top right.)", parent=self.root):
            self.set_advanced(True)
            return True
        return False

    def head_msg(self, text: str = ""):
        """A message in the header, where the subtitle is; empty puts the subtitle back."""
        if text:
            self.head_status.set("  " + text)
            self.head_lbl.configure(style="TLabel", font=self.f_bold)
        else:
            self.head_status.set(self._subtitle)
            self.head_lbl.configure(style="Muted.TLabel", font="")

    def flash(self, text: str, ms: int = 6000):
        self.head_msg(text)
        if getattr(self, "_flash_job", None):
            self.root.after_cancel(self._flash_job)
        self._flash_job = self.root.after(ms, self.head_msg)

    def _help_menu(self):
        m = tk.Menu(self.root, tearoff=False)
        m.add_command(label="Help for this tab", accelerator="F1", command=lambda: self.open_help(self._tab_topic()))
        m.add_command(label="Ask a question...", command=lambda: self.open_help("", search=True))
        m.add_command(label="Getting started", command=lambda: self.open_help("start"))
        m.add_command(label="Guides for each site", command=lambda: self.open_help("sites"))
        m.add_separator()
        m.add_command(label="Run the setup assistant again...", command=self.run_setup)
        m.add_checkbutton(label="Show advanced options", variable=self.adv_var,
                          command=lambda: self.set_advanced(self.adv_var.get()))
        m.add_command(label="What do simple and advanced change?", command=lambda: self.open_help("advanced"))
        m.add_separator()
        m.add_command(label="Open the Packrat folder", command=lambda: uc.open_path(str(paths.install_root())))
        m.add_command(label="About Packrat", command=self.about)
        b = self.help_btn
        try:
            m.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())
        finally:
            m.grab_release()

    def about(self):
        from .ui_help import AboutDialog
        AboutDialog(self.root, self).modal()

    # ---- help and navigation --------------------------------------------------------------------
    def _tab_topic(self) -> str:
        from .guides import TAB_TOPIC
        cur = self.nb.select()
        for key, f in self.frames.items():
            if str(f) == cur:
                return TAB_TOPIC.get(key, "start")
        return "start"

    def open_help(self, target: str = "start", search: bool = False):
        from .ui_help import HelpWindow
        if self.help_win is not None and self.help_win.winfo_exists():
            if target:
                self.help_win.show(target)
            self.help_win.deiconify()
            self.help_win.lift()
        else:
            self.help_win = HelpWindow(self, target or "start")
        if search:
            self.help_win.focus_search()

    TAB_NAMES = {"monitors": "The Monitors tab", "tools": "The Tools tab", "log": "The Log tab"}

    def show_tab(self, key: str, ask: bool = True) -> bool:
        if key in self.ADV_TABS and not uc.ADVANCED[0]:
            if ask:
                if not self.need_advanced(self.TAB_NAMES.get(key, "This tab")):
                    return False
            else:
                self.nb.add(self.frames[key])   # just this tab, for this time (e.g. the Grabber import)
        self.nb.select(self.frames[key])
        self.root.lift()
        return True

    def go(self, target: str):
        """Follow a link from a guide: tab:X, settings:PAGE, sources, help:/site:, url:, setup, advanced:on|off"""
        if target.startswith(("help:", "site:")):
            self.open_help(target)
        elif target.startswith("url:"):
            uc.open_url(target[4:])
        elif target.startswith("tab:") and target[4:] in self.frames:
            self.show_tab(target[4:])
        elif target.startswith("settings:"):
            page = self.settings_page.page_for(target[9:])
            if page is None and not self.need_advanced(f"Settings > {target[9:]}"):
                return
            self.nb.select(self.frames["settings"])
            self.settings_page.show_page(page or target[9:])
            self.root.lift()
        elif target == "setup":
            self.run_setup()
        elif target.startswith("advanced:"):
            self.set_advanced(target.endswith(":on"))
        elif target == "sources":
            self.nb.select(self.frames["browse"])
            self.root.lift()
            t = self.browse.current()
            if t is not None:
                self.root.after(50, t.choose_sites)

    def _ask_from_thread(self, title, message, default=""):
        """For logins that need you to paste something (Pixiv): asks on the window's thread."""
        from tkinter import simpledialog
        box, done = {}, threading.Event()

        def ask(_=None):
            box["v"] = simpledialog.askstring(title, message, initialvalue=default, parent=self.root)
            done.set()
        self.worker.call(ask)
        done.wait(900)
        return box.get("v")

    def _ctrl_enter(self, e):
        cur = self.nb.select()
        if cur == str(self.frames["archive"]):
            self.panel.start()
        elif cur == str(self.frames["downloads"]):
            self.downloads.start()
        return "break"

    def log_line(self, text: str, level: int = logging.INFO):
        self.logpage.add(text, level)

    def _callback_error(self, exc, val, tb):
        import traceback
        log.error("window error: %s", "".join(traceback.format_exception(exc, val, tb))[-1500:])

    def _first_run_hints(self):
        if self.lib.get_state("first_run_done"):
            return
        self.lib.set_state("first_run_done", True)
        self.run_setup(first=True)

    def run_setup(self, first: bool = False):
        """The setup assistant: on the first start, and again from the Help menu (with your current choices)."""
        from .grab import grabber_import as G
        from .ui_help import FirstRun
        try:
            found = G.find_profiles()
        except Exception:  # noqa: BLE001
            found = []
        t = self.browse.current()
        res = FirstRun(self, str(found[0]) if found else None, rerun=not first,
                       current_sites=(t.sites if t is not None else None)).modal()
        if not res:
            return
        if "advanced" in res:
            self.set_advanced(res["advanced"], quiet=True)
        t = self.browse.current()
        if t is not None:
            t.set_sites(res["sites"])
            t.entry.focus_set()
            t.draw_empty()
            t.refresh_dest()
        self.settings_page.show(self.settings_page.current)
        if res.get("import"):
            self.show_tab("tools", ask=False)
            self.tools.import_grabber(str(res["import"]))
        else:
            self.nb.select(self.frames["browse"])

    # ---- shared actions -------------------------------------------------------------------------
    def new_search_tab(self, query: str = "", sites=None, focus=True, run=None):
        return self.browse.new_tab(query, sites, focus=focus, run=run)

    def saver(self):
        if self._saver is None:
            from .grab.download import ImageSaver
            self._saver = ImageSaver(self.st, self.lib, self.store, threading.Event())
        return self._saver

    def save_images(self, items, on_each=None, path=None, filename=None):
        """Save now, in the background (the viewer's Save, a search tab's 'Save selected')."""
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
            self.set_status(f"Saved {saved} picture(s)" + (f", {other} already there or skipped" if other else ""))
        self.worker.submit(work, on_done=done, on_error=lambda e: messagebox.showerror("Save", str(e),
                                                                                       parent=self.root))

    def add_to_downloads_images(self, items, path="", filename=""):
        for site, img in items:
            self.lib.batch_add("unique", {"image": img.to_json(), "path": path, "filename": filename})
        self.downloads.refresh()
        self.set_status(f"{len(items)} picture(s) added to Downloads - press Start there")

    def add_group(self, query, sites, **kw):
        from .grab.download import Group
        per_page = int(kw.pop("per_page", 0) or self.st.get("limit") or 20)
        g = Group(search=query, sites=list(sites), per_page=max(per_page, 20), **kw)
        self.lib.batch_add("group", g.to_json())
        self.downloads.refresh()
        self.set_status(f"'{query or '(everything)'}' added to Downloads - press Start there")

    def set_status(self, text: str):
        self.flash(text, 8000)

    def whitelisted(self, img) -> bool:
        wl = set(str(self.st.get("whitelistedtags") or "").split())
        return bool(wl) and any(t.name in wl for t in img.tags)

    def open_viewer(self, tab, index: int):
        sh = tab.items[index]
        if sh.blacklisted and self.st.get("warnblacklisted", True):
            if not messagebox.askyesno("Blacklisted image", "This image is blacklisted (" + ", ".join(sh.blacklisted)
                                       + ").\n\nOpen it anyway?", parent=self.root):
                return None
        if self.st.get("Viewer/singleWindow") and self.viewers:
            v = self.viewers[-1]
            v.show_list(tab, index)
            v.lift()
            return v
        v = Viewer(self, tab, index)
        self.viewers.append(v)
        return v

    def _monitor_result(self, mon, res):
        self.worker.call(self.monitors.result, mon, res)

    # ---- closing --------------------------------------------------------------------------------
    def on_close(self):
        if self.closing:
            return
        if self.downloads.running():
            if self.st.get("confirm_close", True) and not messagebox.askyesno(
                    "Downloads are running", "Stop the download list and close?\n\nNothing is lost: Start "
                    "continues where it stopped.", icon="warning", parent=self.root):
                return
            self.downloads.stop(then_close=True)
            self.closing = True
            return
        self.browse.save_session()
        self.closing = True
        self.panel.on_close()          # asks about a running archive / unsaved archive settings
        if not self.panel.destroyed:   # the user cancelled there
            self.closing = False

    def _finish_close(self):
        try:
            self.scheduler.stop()
        except AttributeError:
            pass
        try:
            cp.save_ui_state({"main_geometry": self.root.geometry(),
                              "main_tab": self.nb.index("current")})
        except tk.TclError:
            pass
        self.worker.close()
        self.thumbs.close()
        logging.getLogger("packrat").removeHandler(self._handler)
        try:
            for after_id in self.root.tk.splitlist(self.root.tk.call("after", "info")):
                self.root.after_cancel(after_id)
        except tk.TclError:
            pass
        self.root.destroy()


# =====================================================================================================
class BrowsePage:
    def __init__(self, parent, app):
        self.app = app
        self.parent = parent
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        self.nb = ttk.Notebook(parent)
        self.nb.grid(row=0, column=0, sticky="nsew")
        self.plus = ttk.Frame(self.nb)
        self.nb.add(self.plus, text=" + ")
        self.nb.bind("<<NotebookTabChanged>>", self._changed)
        self.nb.bind("<Button-2>", self._middle)
        self.nb.bind("<Button-3>", self._tab_menu)
        self.tabs: list[SearchTab] = []

    def new_tab(self, query="", sites=None, focus=True, run=None, page=1, gallery=None):
        sites = list(sites or self.app.st.get("sites") or ["e6ai.net"])
        t = SearchTab(self.nb, self.app, query, sites, page, gallery=gallery)
        self.nb.insert(self.plus, t.frame, text=t.title())
        self.tabs.append(t)
        if focus:
            self.nb.select(t.frame)
            t.entry.focus_set()
        if run if run is not None else bool(query):
            t.search()
        return t

    def _changed(self, e=None):
        if self.nb.select() == str(self.plus):
            self.new_tab()

    def current(self):
        sel = self.nb.select()
        return next((t for t in self.tabs if str(t.frame) == sel), None)

    def close_current(self):
        t = self.current()
        if t is not None:
            self.close(t)

    def close(self, t):
        if len(self.tabs) == 1:
            t.query.set("")
            t.clear()
            return
        i = self.tabs.index(t)
        self.tabs.remove(t)
        self.nb.forget(t.frame)
        t.frame.destroy()
        if self.tabs:
            self.nb.select(self.tabs[min(i, len(self.tabs) - 1)].frame)

    def reload_current(self):
        t = self.current()
        if t:
            t.search(keep_page=True)

    def _tab_at(self, e):
        try:
            idx = self.nb.index(f"@{e.x},{e.y}")
        except tk.TclError:
            return None
        w = self.nb.tabs()[idx]
        return next((t for t in self.tabs if str(t.frame) == w), None)

    def _middle(self, e):
        t = self._tab_at(e)
        if t:
            self.close(t)

    def _tab_menu(self, e):
        t = self._tab_at(e)
        if not t:
            return
        m = tk.Menu(self.app.root, tearoff=False)
        m.add_command(label="Duplicate tab", command=lambda: self.new_tab(t.query.get(), t.sites, run=True))
        m.add_command(label="Close tab", command=lambda: self.close(t))
        m.add_command(label="Close other tabs", command=lambda: [self.close(x) for x in list(self.tabs) if x is not t])
        m.add_separator()
        m.add_command(label="Back", accelerator="Alt+Left", command=lambda: t.hist(-1),
                      state="normal" if t.back else "disabled")
        m.add_command(label="Forward", accelerator="Alt+Right", command=lambda: t.hist(1),
                      state="normal" if t.fwd else "disabled")
        rm = tk.Menu(m, tearoff=False)
        recent = t.recent_searches(15)
        for q, sites in recent:
            rm.add_command(label=q[:60], command=lambda q=q, si=sites: t.run_query(q, si))
        if recent:
            rm.add_separator()
            rm.add_command(label="Clear the list", command=lambda: (self.app.lib.clear_history(), t.draw_empty()))
        m.add_cascade(label="Recent searches", menu=rm, state="normal" if recent else "disabled")
        m.tk_popup(e.x_root, e.y_root)

    def themed(self, p):
        for t in self.tabs:
            t.themed(p)

    def save_session(self):
        tabs = [{"query": t.query.get(), "sites": t.sites, "page": t.page.get()} for t in self.tabs
                if t.gallery is None]
        self.app.lib.set_state("tabs", tabs)

    def restore_session(self):
        mode = self.app.st.get("start") or "restore"
        tabs = self.app.lib.get_state("tabs") if mode == "restore" else None
        if tabs:
            for i, d in enumerate(tabs):
                run = i == 0 or bool(self.app.st.get("preloadAllTabs"))
                t = self.new_tab(d.get("query", ""), d.get("sites"), focus=(i == 0), run=False,
                                 page=int(d.get("page") or 1))
                if run and (d.get("query") or mode == "restore"):
                    t.search(keep_page=True)
                elif d.get("query"):
                    t.pending = True
            self.nb.select(self.tabs[0].frame)
        else:
            t = self.new_tab(focus=True, run=(mode == "loadfirst"))


# =====================================================================================================
class SearchTab:
    PAD = 8

    def __init__(self, nb, app, query, sites, page=1, gallery=None):
        self.app = app
        self.nb = nb
        self.sites = list(sites)
        self.gallery = gallery       # an Image of type gallery: this tab lists its pictures
        self.back: list = []         # earlier (query, sites, page) of this tab
        self.fwd: list = []
        self._last = None
        self._last_page = 1
        self.frame = ttk.Frame(nb, padding=(6, 6, 6, 4))
        self.items: list = []        # uc Shown
        self.selected: set[int] = set()
        self.last_click = None
        self.previous: dict = {}     # per host: page info, for the next page
        self.result = None
        self.busy = False
        self.pending = False
        self.cancel = threading.Event()
        self.photos: dict[int, object] = {}
        self.page_infos: dict[int, dict] = {}
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(1, weight=1)

        bar = ttk.Frame(self.frame)
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(1, weight=1)
        self.src_btn = ttk.Button(bar, text=self._src_text(), command=self.choose_sites)
        self.src_btn.grid(row=0, column=0, padx=(0, 6))
        uc.tip(self.src_btn, "Choose which sites to search. Each site has a short guide there.")
        self.query = tk.StringVar(value=query)
        self.entry = uc.TagEntry(bar, self._complete, on_enter=self.search, textvariable=self.query)
        self.entry.grid(row=0, column=1, sticky="ew")
        uc.add_placeholder(self.entry, "Type what you're looking for, e.g.  landscape  or  cat_ears blue_eyes  "
                                       "- then press Enter", self.query)
        uc.tip(self.entry, "Tags (words with underscores) separated by spaces. -tag excludes. "
                           "Press F1 for search tips.")
        ttk.Button(bar, text="Search", style="Accent.TButton", command=self.search).grid(row=0, column=2, padx=(6, 0))
        mo = ttk.Button(bar, text="More options...", command=self.search_helper)
        mo.grid(row=0, column=3, padx=6)
        uc.tip(mo, "Sort the results, show only safe pictures, only big ones... without knowing the special words. "
                   "Only what the ticked sites support is offered.")
        nav = ttk.Frame(bar)
        nav.grid(row=0, column=4)
        b = ttk.Button(nav, text="\u25c0", width=3, command=lambda: self.go(-1))
        b.pack(side="left")
        uc.tip(b, "Previous page")
        ttk.Label(nav, text=" Page ").pack(side="left")
        self.page = tk.IntVar(value=page)
        sp = ttk.Spinbox(nav, from_=1, to=100000, textvariable=self.page, width=5)
        sp.pack(side="left", padx=2)
        sp.bind("<Return>", lambda e: self.search(keep_page=True))
        b = ttk.Button(nav, text="\u25b6", width=3, command=lambda: self.go(1))
        b.pack(side="left")
        uc.tip(b, "Next page")
        self.per_page = tk.IntVar(value=int(app.st.get("limit") or 20))
        hb = ttk.Button(bar, text="?", width=3, command=lambda: app.open_help("search"))
        hb.grid(row=0, column=5, padx=(8, 0))
        uc.tip(hb, "How to search")
        row2 = ttk.Frame(self.frame)
        row2.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        row2.columnconfigure(1, weight=1)
        flt = ttk.Frame(row2)
        flt.grid(row=0, column=0, sticky="w")
        # where pictures go (like Grabber's 'Destination' panel), always in view
        dest = ttk.Frame(row2)
        dest.grid(row=0, column=2, sticky="e")
        ttk.Label(dest, text="Saving to", style="Muted.TLabel").pack(side="left")
        self.dest_lbl = ttk.Label(dest, text="", font=app.f_bold)
        self.dest_lbl.pack(side="left", padx=(4, 4))
        uc.tip(self.dest_lbl, lambda: self._dest_tip())
        db = ttk.Button(dest, text="Change...", command=self.change_dest)
        db.pack(side="left")
        uc.tip(db, "Choose the folder and how files are named")
        self._row2, self._flt, self._dest = row2, flt, dest
        row2.bind("<Configure>", lambda e: self._fit_row2(), add="+")
        row2 = flt
        fl = ttk.Label(row2, text="Filter results")
        fl.grid(row=0, column=0, sticky="w")
        self.pf = tk.StringVar(value=str(app.st.get("globalPostFilter") or "")
                               if app.st.get("globalPostFilterExplicit") else "")
        pfe = ttk.Entry(row2, textvariable=self.pf, width=26)
        pfe.grid(row=0, column=1, sticky="w", padx=(6, 0))
        uc.add_placeholder(pfe, "e.g. score:>10", self.pf)
        pfe.bind("<Return>", lambda e: self.search(keep_page=True))
        ftip = ("Hide pictures from the results, e.g.  score:>10   -rating:explicit   width:>=1920   "
                "-grabber:downloaded (hide what you have). Press Enter to apply. F1 for more.")
        uc.tip(fl, ftip)
        uc.tip(pfe, ftip)
        self.merge = tk.BooleanVar(value=bool(app.st.get("mergeresults")))
        mc = ttk.Checkbutton(row2, text="Merge sites", variable=self.merge,
                             command=lambda: self.search(keep_page=True))
        mc.grid(row=0, column=2, padx=8)
        uc.tip(mc, "Show the results of all sites as one list, without pictures found on two sites twice.")
        ttk.Label(row2, text="Pictures per page").grid(row=0, column=3, padx=(8, 4))
        ttk.Spinbox(row2, from_=1, to=1000, textvariable=self.per_page, width=4).grid(row=0, column=4)
        uc.advanced_only(flt)
        self.refresh_dest()
        if gallery is not None:
            self.entry.state(["readonly"])
            mo.state(["disabled"])
            uc.tip(self.entry, "The pictures of this gallery. 'Download all results' saves the whole gallery.")
        btns = ttk.Frame(self.frame)
        btns.grid(row=3, column=0, sticky="ew", pady=(4, 0))
        self.sel_lbl = ttk.Label(btns, text="", style="Muted.TLabel")
        self.sel_lbl.pack(side="left")
        for text, cmd, help_, style in reversed((
                ("Download selected", self.get_selected, "Add the pictures you selected (Ctrl+click) to the "
                 "Downloads tab", None),
                ("Download this page", self.get_page, "Add the pictures on this page to the Downloads tab", None),
                ("Download all results...", self.get_all, "Add the whole search (every page, every ticked site) "
                 "to the Downloads tab. You can choose how many pictures.", "Accent.TButton"),
                ("\u2606 Favorite", self.add_favorite, "Keep this search in the Favorites tab", None),
                ("Monitor...", self.add_monitor, "Check this search regularly for new pictures", None))):
            bt = ttk.Button(btns, text=text, command=cmd, **({"style": style} if style else {}))
            bt.pack(side="right", padx=(18 if text.startswith("\u2606") else 2, 2))
            uc.tip(bt, help_)
            if text == "Monitor...":
                mon_btn = bt
        uc.advanced_only(mon_btn)
        self.status = tk.StringVar(value="")
        self.status_lbl = ttk.Label(self.frame, textvariable=self.status, style="Muted.TLabel")
        self.status_lbl.grid(row=4, column=0, sticky="ew", pady=(2, 0))

        pw = ttk.PanedWindow(self.frame, orient="horizontal")
        pw.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        left = ttk.Frame(pw)
        right = ttk.Frame(pw)
        pw.add(right, weight=0)
        pw.add(left, weight=1)
        self.pw = pw
        # tags
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)
        ttk.Label(right, text="Tags", font=app.f_bold).grid(row=0, column=0, sticky="w")
        self.tags = ttk.Treeview(right, columns=("count",), show="tree", selectmode="browse", height=10)
        self.tags.column("#0", width=uc.px(180))
        self.tags.column("count", width=uc.px(60), anchor="e")
        tsb = ttk.Scrollbar(right, orient="vertical", command=self.tags.yview)
        self.tags.configure(yscrollcommand=tsb.set)
        self.tags.grid(row=1, column=0, sticky="nsew")
        tsb.grid(row=1, column=1, sticky="ns")
        self.tags.bind("<ButtonRelease-1>", self._tag_click)
        self.tags.bind("<Button-2>", self._tag_middle)
        self.tags.bind("<Button-3>", self._tag_menu)
        ttk.Label(right, text="Click a tag: search it\nCtrl+click: add it to the search\nRight-click: more",
                  style="Muted.TLabel", justify="left").grid(row=2, column=0, sticky="w")
        # the site's wiki text for the searched tag (Grabber's Wiki panel)
        self._wiki = ""
        self.wiki_btn = ttk.Button(right, text="About this tag...", command=self.show_wiki)
        self.wiki_btn.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        uc.tip(self.wiki_btn, "What the site's wiki says about the tag you searched")
        self.wiki_btn.grid_remove()
        # results
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(left, highlightthickness=0, background=COLORS["text_bg"])
        vsb = ttk.Scrollbar(left, orient="vertical", command=self._yview)
        self.canvas.configure(yscrollcommand=vsb.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        self.more_btn = ttk.Button(left, text="Load more", command=self.load_more)
        self.canvas.bind("<Configure>", lambda e: self._relayout())
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<Double-Button-1>", lambda e: "break")
        self.canvas.bind("<Button-3>", self._menu)
        self.canvas.bind("<Enter>", lambda e: self._wheel(True))
        self.canvas.bind("<Leave>", lambda e: self._wheel(False))
        self.canvas.bind("<Motion>", self._hover)
        self.tip = None
        self.frame.bind("<Visibility>", lambda e: self._maybe_pending())
        self._update_sel()

    # ---- simple / advanced, destination, search helper ---------------------------------------------
    def _pf(self) -> str:
        """The tab's filter. Hidden in the simple view, so it doesn't apply there either."""
        return self.pf.get() if uc.ADVANCED[0] else ""

    def _fit_row2(self):
        """The filter and 'Saving to' share a row when there's room, otherwise 'Saving to' goes below."""
        try:
            need = self._dest.winfo_reqwidth() + (self._flt.winfo_reqwidth() + uc.px(24)
                                                  if self._flt.winfo_manager() else 0)
            below = need > self._row2.winfo_width() > 1
            info = self._dest.grid_info()
            if below and int(info.get("row", 0)) == 0:
                self._dest.grid(row=1, column=0, columnspan=3, sticky="e", pady=(4, 0))
            elif not below and int(info.get("row", 0)) == 1:
                self._dest.grid(row=0, column=2, columnspan=1, sticky="e", pady=0)
        except tk.TclError:
            pass

    def view_changed(self):
        self.frame.after_idle(self._fit_row2)
        self.per_page.set(int(self.per_page.get() or 0) if uc.ADVANCED[0] else int(self.app.st.get("limit") or 20))

    def refresh_dest(self):
        from .ui_help import format_label
        folder = self.app.st.save_path()
        parts = Path(folder).parts
        short = folder if len(folder) <= 34 else "\u2026" + os.sep + os.sep.join(parts[-2:])
        self.dest_lbl.configure(text=f"{short}  \u00b7  {format_label(self.app.st.filename())}")

    def _dest_tip(self):
        from .ui_help import sample_example
        path, err = sample_example(self.app.st.filename(), self.app.st, self.app.st.save_path())
        return (f"Folder: {self.app.st.save_path()}\nFile name format: {self.app.st.filename()}\n"
                + (f"Example: {path}" if path else f"Problem: {err}"))

    def change_dest(self):
        from .ui_help import DestinationDialog
        if DestinationDialog(self.frame, self.app).modal():
            for t in self.app.browse.tabs:
                t.refresh_dest()
            self.app.settings_page.show(self.app.settings_page.current)

    def search_helper(self):
        from .ui_help import SearchHelper
        sites = [s for s in (self.app.store.get(h) for h in self.sites) if s is not None]
        q = SearchHelper(self.frame, self.app, self.query.get(), sites).modal()
        if q is not None:
            self.query.set(q)
            self.entry.icursor("end")
            self.search()

    # ---- helpers ---------------------------------------------------------------------------------
    def title(self):
        q = self.query.get().strip() or "(everything)"
        if self.gallery is not None:
            q = "\u25a4 " + q
        return (q[:22] + "…") if len(q) > 23 else q

    def _src_text(self):
        from .guides import site_info
        names = [site_info(h)["name"] for h in self.sites]
        if not names:
            return "Sites: none \u25be"
        if len(names) <= 2:
            return "Sites: " + ", ".join(names) + " \u25be"
        return f"Sites: {names[0]} + {len(names) - 1} more \u25be"

    def set_sites(self, sites):
        self.sites = list(sites)
        self.app.st.set("sites", self.sites)
        self.src_btn.configure(text=self._src_text())

    def _complete(self, prefix):
        if not self.app.st.get("autocompletion", True):
            return []
        words = self.app.lib.complete(prefix, limit=15)
        for host in self.sites[:2]:
            s = self.app.store.get(host)
            if s is not None:
                try:
                    for t in s.tag_db().complete(prefix, 15):
                        n = t[0] if isinstance(t, tuple) else getattr(t, "name", str(t))
                        if n not in words:
                            words.append(n)
                except Exception:  # noqa: BLE001
                    pass
        favs = [f["name"] for f in self.app.lib.favorites() if f["name"].startswith(prefix)]
        return list(dict.fromkeys(favs + words))[:30]

    def _maybe_pending(self):
        if self.pending:
            self.pending = False
            self.search(keep_page=True)

    def choose_sites(self):
        if self.gallery is not None:
            self.status.set("This tab shows one gallery. Open a new tab (+) to search other sites.")
            return
        res = uc.SitePicker(self.frame, self.app, self.sites).modal()
        if res:
            self.set_sites(res)
            self.draw_empty()
            if self.query.get().strip() or self.items:
                self.search(keep_page=False)

    def go(self, d):
        self.page.set(max(1, int(self.page.get() or 1) + d))
        self.search(keep_page=True, direction=d)

    # ---- searching -------------------------------------------------------------------------------
    def search(self, keep_page=False, direction=0, append=False):
        if not keep_page:
            self.page.set(1)
            self.previous = {}
        q = self.query.get().strip()
        cur = (q, list(self.sites))
        if not getattr(self, "_from_hist", False) and self._last and self._last != cur and not append:
            self.back.append((*self._last, int(self._last_page)))
            self.fwd.clear()
        self._from_hist = False
        if q.startswith(("http://", "https://")) and " " not in q and self.gallery is None:
            # a picture's page address: search that picture on its site
            from .grab.search import parse_post_address
            try:
                site, pid = parse_post_address(self.app.store, q)
            except ValueError as e:
                self.status.set(str(e))
                self.status_lbl.configure(foreground=COLORS["error"])
                return
            self.set_sites([site.host])
            self.query.set(f"id:{pid}")
            q = self.query.get()
        try:
            self.nb.tab(self.frame, text=self.title())
        except tk.TclError:
            pass
        self.cancel.set()
        self.cancel = threading.Event()
        cancel = self.cancel
        page = int(self.page.get() or 1)
        sites = [s for s in (self.app.store.get(h) for h in self.sites) if s is not None]
        if not sites:
            self.status.set("Choose at least one site (Sources button).")
            return
        prev = self.page_infos.get(page - 1, {}) if direction >= 0 else {}
        self.status.set(f"Searching '{q or '(everything)'}' on {', '.join(s.name for s in sites)}...")
        self.status_lbl.configure(foreground=COLORS["muted"])
        if not append and not self.items:
            self.canvas.delete("all")
            self.canvas.create_text(max(300, self.canvas.winfo_width()) // 2, 60, text="Searching...",
                                    font=self.app.f_title, fill=COLORS["muted"], anchor="n")
        self.busy = True
        self._last, self._last_page = (q, list(self.sites)), page
        if q and self.gallery is None:
            self.app.lib.add_history(q, self.sites)
        from .grab import search as S

        def work():
            return S.run(q, sites, page, int(self.per_page.get() or 20), self.app.st, self.app.lib,
                         postfilter=self._pf(), merge=self.merge.get(), previous=prev, cancel=cancel,
                         gallery=self.gallery)

        def done(res):
            if cancel is not self.cancel:
                return   # an older search
            self.busy = False
            self.result = res
            self.page_infos[page] = {pr.site.host: pr.info() for pr in res.pages if not pr.error}
            self.show(res, append=append)
        self.app.worker.submit(work, on_done=done, on_error=self._error)

    def _error(self, e):
        self.busy = False
        self.status.set("The search failed: " + uc.friendly(str(e)))
        self.status_lbl.configure(foreground=COLORS["error"])
        self.draw_empty(error=uc.friendly(str(e)))

    def draw_empty(self, error: str = "", searched: bool = False):
        """What the result area says when it has no pictures: how to start, or why nothing came back."""
        if self.items:
            return
        self._empty_state = (error, searched)
        c = self.canvas
        c.delete("all")
        w = max(300, c.winfo_width())
        from .guides import site_info
        names = ", ".join(site_info(h)["name"] for h in self.sites) or "no site"
        if error:
            title, lines = "Something went wrong", [error, "",
                                                     "Check the site's guide (Sites button > click the site), or "
                                                     "press F1 > Problems and fixes."]
        elif searched:
            title, lines = "No pictures found", [
                f"Searched: {names}.", "",
                "\u2022 Check the spelling: tags must match the site exactly (cat_ears, not cat ears).",
                "\u2022 Try fewer tags. Danbooru allows only 2 without a paid account.",
                "\u2022 Some sites hide pictures unless you log in (Sites button > the site > Log in)."]
            if self._pf().strip():
                lines.append("\u2022 Clear the 'Filter results' box: it hides pictures that don't match it.")
        else:
            title, lines = "Search for pictures", [
                f"Searching: {names}   (change with the Sites button, top left)", "",
                "1.  Type tags in the box above and press Enter, for example:",
                "        landscape        cat_ears blue_eyes        dragon -human",
                "2.  Click a picture to see it big. Hold Ctrl and click to select several.",
                "3.  Use the Download buttons below, then press Start in the Downloads tab.", "",
                "Press F1 or the ? button for the full guide."]
        y = 40
        c.create_text(w // 2, y, text=title, font=self.app.f_title,
                      fill=COLORS["error"] if error else COLORS["fg"], anchor="n")
        body = c.create_text(w // 2, y + uc.px(44), text="\n".join(lines),
                             font=self.app.f_big if not error else None, fill=COLORS["muted"], anchor="n",
                             width=min(w - 40, uc.px(820)), justify="left")
        bottom = (c.bbox(body) or (0, 0, 0, uc.px(300)))[3]
        recent = [] if error or searched or self.gallery is not None else self.recent_searches(6)
        if recent:
            x0 = (c.bbox(body) or (w // 4, 0, 0, 0))[0]
            yy = bottom + uc.px(24)
            c.create_text(x0, yy, text="Your recent searches (click one):", font=self.app.f_bold,
                          fill=COLORS["fg"], anchor="nw")
            for q, sites in recent:
                yy += uc.px(26)
                where = ", ".join(site_info(h)["name"] for h in sites[:3]) + (" ..." if len(sites) > 3 else "")
                tid = c.create_text(x0 + uc.px(16), yy, text=f"{q}", font=self.app.f_big, fill=COLORS["accent"],
                                    anchor="nw", tags=("recent",))
                c.create_text((c.bbox(tid) or (0, 0, x0 + uc.px(200), 0))[2] + uc.px(12), yy + 2,
                              text=f"on {where}", fill=COLORS["muted"], anchor="nw")
                c.tag_bind(tid, "<Button-1>", lambda e, q=q, si=sites: self.run_query(q, si))
                c.tag_bind(tid, "<Enter>", lambda e: c.configure(cursor="hand2"))
                c.tag_bind(tid, "<Leave>", lambda e: c.configure(cursor=""))
            bottom = yy + uc.px(30)
        c.configure(scrollregion=(0, 0, w, max(uc.px(400), bottom + 20)))

    def recent_searches(self, n: int = 8):
        out, seen = [], set()
        for h in self.app.lib.history(200):
            key = h["search"].strip().lower()
            if key and key not in seen:
                seen.add(key)
                out.append((h["search"].strip(), list(h.get("sites") or self.sites)))
            if len(out) >= n:
                break
        return out

    def run_query(self, q, sites=None):
        if sites:
            self.set_sites([h for h in sites if self.app.store.get(h) is not None] or self.sites)
        self.query.set(q)
        self.search()

    def hist(self, d: int):
        """Back (-1) / forward (+1) through this tab's searches (Alt+Left / Alt+Right)."""
        src, dst = (self.back, self.fwd) if d < 0 else (self.fwd, self.back)
        if not src:
            self.status.set("Nothing to go back to." if d < 0 else "Nothing to go forward to.")
            return
        dst.append((self.query.get(), list(self.sites), int(self.page.get() or 1)))
        q, sites, page = src.pop()
        self._from_hist = True
        self.set_sites(sites)
        self.query.set(q)
        self.page.set(page)
        self.search(keep_page=True)

    def load_more(self):
        if self.busy:
            return
        self.page.set(int(self.page.get() or 1) + 1)
        self.search(keep_page=True, direction=1, append=True)

    def clear(self):
        self.items, self.selected, self.photos = [], set(), {}
        self.canvas.delete("all")
        self.tags.delete(*self.tags.get_children())
        self._update_sel()

    def _update_sel(self):
        n = len(self.selected)
        self.sel_lbl.configure(text=f"{n} selected" if n else "Ctrl+click pictures to select")

    def show(self, res, append=False):
        if not append:
            self.clear()
            self.canvas.yview_moveto(0)
        start = len(self.items)
        self.items += res.items
        # tags
        if not append:
            from .textutil import html_to_text
            self._wiki = html_to_text(getattr(res, "wiki", "") or "")
            if self._wiki:
                self.wiki_btn.grid()
            else:
                self.wiki_btn.grid_remove()
            for name, typ, count in res.tags[:400]:
                self.tags.insert("", "end", text=name, values=(f"{count:,}" if count else "",),
                                 tags=(typ or "general",))
            self._color_tags()
        n = len(res.items)
        bits = [f"Page {res.page}: {n} picture{'s' if n != 1 else ''}"]
        if res.filtered and self.app.st.get("showFilteredImagesCount", True):
            bits.append(f"{res.filtered} hidden by filters / blacklist")
        if res.duplicates:
            bits.append(f"{res.duplicates} found on two sites (shown once)")
        counts = [f"{pr.site.name}: {pr.image_count:,}" for pr in res.pages if pr.image_count and pr.image_count > 0]
        if counts:
            bits.append("in total " + ", ".join(counts))
        errs = [f"{e.split(':', 1)[0]}: {uc.friendly(e.split(':', 1)[-1], e.split(':', 1)[0])}" for e in res.errors]
        self.status.set("  |  ".join(bits) + (("      Problem - " + "  ".join(errs)) if errs else ""))
        self.status_lbl.configure(foreground=COLORS["error"] if errs else COLORS["muted"])
        self._update_sel()
        self._relayout()
        if not self.items:
            self.draw_empty(error=" ".join(errs) if errs and not res.filtered else "", searched=True)
        for i in range(start, len(self.items)):
            self._load_thumb(i)
        if (self.app.st.get("whitelist_download") or "never") == "page":
            wl = [(sh.site, sh.image) for sh in self.items[start:] if not sh.downloaded and self.app.whitelisted(sh.image)]
            if wl:
                self.app.save_images(wl)
        mode = self.app.st.get("infiniteScroll") or "disabled"
        if mode in ("button", "scroll") and n:
            self.more_btn.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        else:
            self.more_btn.grid_remove()

    def _load_thumb(self, i):
        sh = self.items[i]

        def cb(photo, i=i, sh=sh):
            if i >= len(self.items) or self.items[i] is not sh:
                return
            if photo is not None:
                self.photos[i] = photo
                self.canvas.itemconfigure(f"img{i}", image=photo)
                self.canvas.itemconfigure(f"ph{i}", text="")
            else:
                self.canvas.itemconfigure(f"ph{i}", text="no preview")
        self.app.thumbs.get(sh.site, sh.image, cb)

    # ---- grid ------------------------------------------------------------------------------------
    def _cell(self):
        return self.app.thumbs.size + 2 * self.PAD

    def _relayout(self):
        c = self.canvas
        if not self.items:
            self.draw_empty(*getattr(self, "_empty_state", ("", False)))
            return
        c.delete("all")
        size, cell = self.app.thumbs.size, self._cell()
        width = max(cell, c.winfo_width())
        cols = max(1, width // cell)
        x0 = (width - cols * cell) // 2
        p = COLORS
        for i, sh in enumerate(self.items):
            r, k = divmod(i, cols)
            x, y = x0 + k * cell + self.PAD, r * cell + self.PAD
            cx, cy = x + size // 2, y + size // 2
            c.create_rectangle(x - 3, y - 3, x + size + 3, y + size + 3, outline=self._border(i), width=3,
                               tags=(f"box{i}", f"it{i}", "box"))
            c.create_text(cx, cy, text="loading...", fill=p["muted"], tags=(f"ph{i}", f"it{i}"))
            c.create_image(cx, cy, image=self.photos.get(i) or "", tags=(f"img{i}", f"it{i}"))
            badges = []
            im = sh.image
            if im.is_video and self.app.st.get("previewVideoIndicator", True):
                badges.append("▶ video")
            elif im.is_animated:
                badges.append("GIF")
            if getattr(im, "is_gallery", False):
                badges.append(f"▤ {im.gallery_count or ''}")
            if sh.downloaded:
                badges.append("✓ saved")
            if sh.blacklisted:
                badges.append("blacklisted")
            if badges:
                t = c.create_text(x + 4, y + size - 4, text="  ".join(badges), anchor="sw", fill="#ffffff",
                                  font=self.app.f_small, tags=(f"it{i}", f"badge{i}"))
                bb = c.bbox(t)
                c.create_rectangle(bb[0] - 2, bb[1] - 1, bb[2] + 2, bb[3] + 1, fill="#202020", outline="",
                                   tags=(f"it{i}", f"badgebg{i}"))
                c.tag_raise(t)
            if sh.blacklisted:
                c.create_rectangle(x, y, x + size, y + size, fill=p["bg"], stipple="gray50", outline="",
                                   tags=(f"it{i}", f"veil{i}"))
        rows = (len(self.items) + cols - 1) // cols
        c.configure(scrollregion=(0, 0, width, rows * cell + self.PAD))
        self.cols, self.x0 = cols, x0

    def _border(self, i):
        sh = self.items[i]
        if i in self.selected:
            return COLORS["focus"]
        if sh.blacklisted:
            return COLORS["muted"]
        if sh.favorited:
            return "#ff9ecb"
        if sh.downloaded:
            return COLORS["ok"]
        return COLORS["text_bg"]

    def _index_at(self, x, y):
        cell = self._cell()
        x, y = self.canvas.canvasx(x) - self.x0, self.canvas.canvasy(y)
        if x < 0:
            return None
        k, r = int(x // cell), int(y // cell)
        if k >= self.cols:
            return None
        i = r * self.cols + k
        return i if 0 <= i < len(self.items) else None

    def _redraw_border(self, i):
        self.canvas.itemconfigure(f"box{i}", outline=self._border(i))
        self._update_sel()

    def _click(self, e):
        i = self._index_at(e.x, e.y)
        if i is None:
            if not (e.state & 4):
                old = list(self.selected)
                self.selected.clear()
                for j in old:
                    self._redraw_border(j)
            return
        ctrl, shift = bool(e.state & 4), bool(e.state & 1)
        if self.app.st.get("invertToggle"):
            ctrl = not ctrl
        if shift and self.last_click is not None:
            a, b = sorted((self.last_click, i))
            for j in range(a, b + 1):
                self.selected.add(j)
                self._redraw_border(j)
        elif ctrl:
            self.selected.symmetric_difference_update({i})
            self._redraw_border(i)
            self.last_click = i
        else:
            self.last_click = i
            if getattr(self.items[i].image, "is_gallery", False):
                self.open_gallery(i)
            else:
                self.app.open_viewer(self, i)

    def show_wiki(self):
        if not self._wiki:
            return
        first = next((w for w in self.query.get().split() if not w.startswith(("-", "~")) and ":" not in w), "")
        win = tk.Toplevel(self.app.root, background=COLORS["bg"])
        win.title(f"About {first or 'this tag'} - from {self._src_text().replace(chr(0x25be), '').strip()}")
        win.geometry(uc.size_for(win, uc.px(560), uc.px(420)))
        th.dark_title_bar(win, self.app.dark)
        f = ttk.Frame(win, padding=10)
        f.pack(fill="both", expand=True)
        f.rowconfigure(1, weight=1)
        f.columnconfigure(0, weight=1)
        ttk.Label(f, text=first.replace("_", " ") or "This tag", font=self.app.f_big).grid(row=0, column=0,
                                                                                         sticky="w")
        t = tk.Text(f, wrap="word", font="TkDefaultFont", relief="flat", padx=8, pady=6,
                    background=COLORS["text_bg"], foreground=COLORS["fg"], highlightthickness=0)
        t.insert("1.0", self._wiki)
        t.configure(state="disabled")
        t.grid(row=1, column=0, sticky="nsew", pady=(6, 6))
        sb = ttk.Scrollbar(f, orient="vertical", command=t.yview)
        sb.grid(row=1, column=1, sticky="ns", pady=(6, 6))
        t.configure(yscrollcommand=sb.set)
        ttk.Button(f, text="Close", command=win.destroy).grid(row=2, column=0, columnspan=2, sticky="e")
        win.bind("<Escape>", lambda e: win.destroy())

    def open_gallery(self, i):
        """A gallery (album, pool, E-Hentai gallery...) opens in its own tab with the pictures inside."""
        sh = self.items[i]
        img = sh.image
        name = img.name or str((img.data or {}).get("title") or "") or f"gallery {img.id}"
        self.app.browse.new_tab(str(name)[:120], [sh.site.host], focus=True, run=True, gallery=img)

    def _menu(self, e):
        i = self._index_at(e.x, e.y)
        m = tk.Menu(self.app.root, tearoff=False)
        if i is not None:
            sh = self.items[i]
            if i not in self.selected:
                old = list(self.selected)
                self.selected = {i}
                for j in old + [i]:
                    self._redraw_border(j)
            n = len(self.selected)
            m.add_command(label="Open", command=lambda: self.app.open_viewer(self, i))
            m.add_command(label=f"Save now ({n})", command=self.save_selected)
            m.add_command(label=f"Add to the download list ({n})", command=self.get_selected)
            m.add_separator()
            m.add_command(label="Open in browser", command=lambda: uc.open_url(sh.image.page_url))
            m.add_command(label="Copy file address", command=lambda: self._copy(sh.image.file_url))
            m.add_command(label="Copy page address", command=lambda: self._copy(sh.image.page_url))
            from .grab import websvc
            ws = tk.Menu(m, tearoff=False)
            for svc in websvc.services(self.app.st):
                ws.add_command(label=svc["name"], command=lambda s=svc: websvc.open_service(s, sh.image))
            m.add_cascade(label="Search this image on", menu=ws)
            if sh.image.md5:
                m.add_command(label="Find this picture on the ticked sites (MD5)", command=lambda: (
                    self.app.new_search_tab(f"md5:{sh.image.md5}", self.sites, run=True)))
            m.add_separator()
            m.add_command(label="Copy all tags", command=lambda: self._copy(
                " ".join(t.name for t in sh.image.tags)))
            m.add_command(label="Copy all tags (with types)", command=lambda: self._copy(
                " ".join((f"{t.type}:{t.name}" if t.type and t.type != "general" else t.name)
                         for t in sh.image.tags)))
            q = self.query.get().strip()
            if q and self.app.lib.is_favorite(q) and sh.image.preview_url:
                m.add_command(label=f"Use as the picture of favorite '{q[:30]}'", command=lambda: (
                    self.app.lib.update_favorite(q, image=json.dumps({"site": sh.site.host,
                                                                      "url": sh.image.preview_url})),
                    self.app.favorites.refresh(), self.app.set_status("Favorite picture changed")))
            if sh.downloaded:
                p = self.app.lib.md5_path(sh.image.md5) if sh.image.md5 else None
                if p:
                    m.add_command(label="Show the saved file", command=lambda: uc.reveal(p))
            m.add_separator()
        m.add_command(label="Select all", command=self.select_all)
        m.add_command(label="Select none", command=self.select_none)
        m.tk_popup(e.x_root, e.y_root)

    def _hover(self, e):
        i = self._index_at(e.x, e.y)
        if getattr(self, "_hover_i", None) == i:
            return
        self._hover_i = i
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None
        if i is None:
            return
        im = self.items[i].image
        lines = [f"{im.site_key}  #{im.id}", f"{im.width}x{im.height}  {_size(im.file_size)}  score {im.score}  "
                 f"rating {im.rating or '?'}"]
        if im.date:
            lines.append(im.date.strftime("%Y-%m-%d %H:%M"))
        bl = self.items[i].blacklisted
        if bl:
            lines.append("blacklisted: " + ", ".join(bl))
        tags = " ".join(t.name for t in im.tags[:25])
        if tags:
            lines.append(tags + (" …" if len(im.tags) > 25 else ""))
        self.tip = tk.Toplevel(self.canvas)
        self.tip.wm_overrideredirect(True)
        tk.Label(self.tip, text="\n".join(lines), justify="left", background=COLORS["tip_bg"],
                 foreground=COLORS["tip_fg"], wraplength=uc.px(420), borderwidth=1, relief="solid",
                 padx=6, pady=4).pack()
        self.tip.geometry(f"+{e.x_root + 16}+{e.y_root + 16}")
        self.canvas.after(6000, lambda t=self.tip: t.destroy() if t is not None and t.winfo_exists() else None)

    def _copy(self, text):
        self.app.root.clipboard_clear()
        self.app.root.clipboard_append(text or "")

    def select_all(self):
        self.selected = set(range(len(self.items)))
        for i in self.selected:
            self._redraw_border(i)

    def select_none(self):
        old, self.selected = self.selected, set()
        for i in old:
            self._redraw_border(i)

    def _yview(self, *a):
        self.canvas.yview(*a)
        self._check_scroll()

    def _check_scroll(self):
        if (self.app.st.get("infiniteScroll") == "scroll" and not self.busy and self.items
                and self.canvas.yview()[1] >= 0.995):
            self.load_more()

    def _wheel(self, on):
        if on:
            self.canvas.bind_all("<MouseWheel>", self._on_wheel)
            self.canvas.bind_all("<Button-4>", lambda e: self._scroll(-3))
            self.canvas.bind_all("<Button-5>", lambda e: self._scroll(3))
        else:
            self.canvas.unbind_all("<MouseWheel>")
            self.canvas.unbind_all("<Button-4>")
            self.canvas.unbind_all("<Button-5>")
            if self.tip is not None:
                self.tip.destroy()
                self.tip = None
                self._hover_i = None

    def _on_wheel(self, e):
        self._scroll(-int(e.delta / 40) if abs(e.delta) >= 40 else (-1 if e.delta > 0 else 1))

    def _scroll(self, n):
        self.canvas.yview_scroll(n, "units")
        self._check_scroll()

    # ---- tags panel --------------------------------------------------------------------------------
    def _color_tags(self):
        from .grab.tags import KNOWN_TYPES
        for typ in set(KNOWN_TYPES) | {"general", "unknown", ""}:
            self.tags.tag_configure(typ or "general", foreground=uc.tag_color(typ, self.app.st, self.app.dark))

    def _tag_name(self, e):
        iid = self.tags.identify_row(e.y)
        return self.tags.item(iid, "text") if iid else None

    def _tag_click(self, e):
        name = self._tag_name(e)
        if not name:
            return
        if e.state & 4:
            self.query.set((self.query.get().strip() + " " + name).strip())
        else:
            self.query.set(name)
        self.search()

    def _tag_middle(self, e):
        name = self._tag_name(e)
        if name:
            self.app.new_search_tab(name, self.sites, focus=False, run=True)

    def _tag_menu(self, e):
        name = self._tag_name(e)
        if not name:
            return
        tag_menu(self.app, name, e, self)

    def themed(self, p):
        self.canvas.configure(background=p["text_bg"])
        self._color_tags()
        self._relayout()

    # ---- downloads ---------------------------------------------------------------------------------
    def _chosen(self):
        return [(self.items[i].site, self.items[i].image) for i in sorted(self.selected)]

    def get_selected(self):
        items = self._chosen()
        if not items:
            self.status.set("Nothing selected: Ctrl+click pictures to select them.")
            return
        self.app.add_to_downloads_images(items)

    def save_selected(self):
        items = self._chosen()
        if items:
            def each(img, res):
                for i, sh in enumerate(self.items):
                    if sh.image is img and res and res[0].status in ("saved", "exists", "copied", "moved", "linked"):
                        sh.downloaded = True
                        self._relayout()
                        break
            self.app.save_images(items, each)

    def get_page(self):
        items = [(sh.site, sh.image) for sh in self.items if not sh.blacklisted]
        if items:
            self.app.add_to_downloads_images(items)

    def get_all(self):
        if self.gallery is not None:     # the whole gallery: downloads know how to page through it
            self.app.add_to_downloads_images([(self.app.store.get(self.sites[0]), self.gallery)])
            self.app.set_status("The whole gallery was added to Downloads. Press Start there.")
            return
        q = self.query.get().strip()
        d = GroupDialog(self.frame, self.app, q, self.sites, self._pf()).modal()
        if d:
            self.app.add_group(**d)

    def add_favorite(self):
        q = self.query.get().strip()
        if not q:
            self.status.set("Type a search first, then press Favorite to keep it.")
            return
        self.app.lib.add_favorite(q, self.sites)
        self.app.favorites.refresh()
        self.app.set_status(f"'{q}' added to favorites")

    def add_monitor(self):
        q = self.query.get().strip()
        MonitorDialog(self.frame, self.app, {"search": q, "sites": self.sites, "postfilter": self._pf()}).modal()
        self.app.monitors.refresh()


def tag_menu(app, name, e, tab=None):
    lib = app.lib
    m = tk.Menu(app.root, tearoff=False)
    sites = tab.sites if tab else None
    m.add_command(label="Search in a new tab", command=lambda: app.new_search_tab(name, sites, run=True))
    if tab is not None:
        m.add_command(label="Add to this search", command=lambda: (
            tab.query.set((tab.query.get().strip() + " " + name).strip()), tab.search()))
        m.add_command(label="Exclude from this search", command=lambda: (
            tab.query.set((tab.query.get().strip() + " -" + name).strip()), tab.search()))
    m.add_separator()
    if lib.is_favorite(name):
        m.add_command(label="Remove from favorites", command=lambda: (lib.remove_favorite(name),
                                                                      app.favorites.refresh()))
    else:
        m.add_command(label="Add to favorites", command=lambda: (lib.add_favorite(name, sites or []),
                                                                 app.favorites.refresh()))
    if name in lib.later():
        m.add_command(label="Don't keep for later", command=lambda: (lib.remove_later(name), app.favorites.refresh()))
    else:
        m.add_command(label="Keep for later", command=lambda: (lib.add_later(name), app.favorites.refresh()))
    bl = app.st.lines("blacklistedtags")
    if name in bl:
        m.add_command(label="Remove from the blacklist",
                      command=lambda: app.st.set("blacklistedtags", "\n".join(x for x in bl if x != name)))
    else:
        m.add_command(label="Blacklist", command=lambda: app.st.set("blacklistedtags", "\n".join(bl + [name])))
    ig = app.st.lines("ignoredtags")
    if name not in ig:
        m.add_command(label="Ignore (leave out of file names)",
                      command=lambda: app.st.set("ignoredtags", "\n".join(ig + [name])))
    m.add_separator()
    m.add_command(label="Copy", command=lambda: (app.root.clipboard_clear(), app.root.clipboard_append(name)))
    if tab is not None and tab.result is not None and getattr(tab.result, "tags", None):
        tags = tab.result.tags
        m.add_command(label="Copy all tags in the list", command=lambda: (
            app.root.clipboard_clear(), app.root.clipboard_append(" ".join(t[0] for t in tags))))
        m.add_command(label="Copy all tags (with types)", command=lambda: (
            app.root.clipboard_clear(), app.root.clipboard_append(" ".join(
                (f"{t[1]}:{t[0]}" if t[1] and t[1] != "general" else t[0]) for t in tags))))
    m.tk_popup(e.x_root, e.y_root)


def _size(n) -> str:
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return ""
    if not n:
        return ""
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return ""


# =====================================================================================================
class GroupDialog(uc.Dialog):
    """'Get all': a whole search as one item of the download list."""

    def __init__(self, master, app, query, sites, postfilter=""):
        super().__init__(master, "Download all results")
        self.app = app
        b = self.body
        self.v = {}
        ttk.Label(b, text="Download every picture of this search", font=app.f_big).grid(row=0, column=0, columnspan=3,
                                                                                    sticky="w")
        ttk.Label(b, text="This adds the search to the Downloads tab; nothing is downloaded until you press Start "
                  "there. Pictures you already have are skipped.", wraplength=uc.px(620), justify="left",
                  style="Muted.TLabel").grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 10))

        def row(r, label, key, value, width=40, kind="entry", help_=""):
            ttk.Label(b, text=label).grid(row=r, column=0, sticky="w", padx=(0, 10), pady=3)
            if kind == "check":
                v = tk.BooleanVar(value=bool(value))
                ttk.Checkbutton(b, variable=v).grid(row=r, column=1, sticky="w")
            else:
                v = tk.StringVar(value=str(value))
                ttk.Entry(b, textvariable=v, width=width).grid(row=r, column=1, sticky="w", pady=3)
            if help_:
                ttk.Label(b, text=help_, style="Muted.TLabel").grid(row=r, column=2, sticky="w", padx=(6, 0))
            self.v[key] = v
        row(2, "Search", "search", query)
        row(3, "Sites", "sites", " ".join(sites), help_="addresses, separated by spaces")
        row(4, "How many pictures", "limit", 0, 8, help_="0 = all of them")
        row(5, "Save in folder", "path", app.st.save_path(), 50)
        fb = ttk.Button(b, text="Choose...", command=self._pick)
        fb.grid(row=5, column=2, sticky="w", padx=(6, 0))
        row(6, "File names", "filename", app.st.filename(), 50, help_="see Settings > File names")
        row(7, "Filter results", "postfilter", postfilter, 40, help_="optional, e.g. score:>10")
        row(8, "Start at page", "page", 1, 6)
        row(9, "Pictures per page", "per_page", max(20, int(app.st.get("limit") or 20)), 6,
            help_="(more = fewer requests)")
        row(10, "Also blacklisted pictures", "get_blacklisted", False, kind="check")
        self.buttons("Add to Downloads", self.ok)

    def _pick(self):
        p = filedialog.askdirectory(parent=self, initialdir=self.v["path"].get() or None)
        if p:
            self.v["path"].set(p)

    def ok(self):
        try:
            d = {k: (v.get() if not isinstance(v, tk.BooleanVar) else bool(v.get())) for k, v in self.v.items()}
            d["sites"] = d["sites"].split()
            for k in ("page", "per_page", "limit"):
                d[k] = max(0 if k == "limit" else 1, int(d[k] or 0))
        except ValueError:
            messagebox.showerror("Add", "Pages and counts must be numbers.", parent=self)
            return
        if not d["sites"]:
            messagebox.showerror("Add", "Give at least one site.", parent=self)
            return
        if d["path"] == self.app.st.save_path():
            d["path"] = ""
        if d["filename"] == self.app.st.filename():
            d["filename"] = ""
        from .grab.filename import Filename
        if d["filename"]:
            lvl, msg = Filename(d["filename"]).check()
            if lvl == "error":
                messagebox.showerror("File name", msg, parent=self)
                return
        d["query"] = d.pop("search")
        self.result = d
        self.destroy()


class MonitorDialog(uc.Dialog):
    def __init__(self, master, app, mon: dict):
        super().__init__(master, "Monitor" if mon.get("id") else "New monitor")
        self.app, self.mon = app, mon
        st = app.st
        b = self.body
        self.v = {}

        def row(r, label, key, value, kind="entry", width=40, help_=""):
            ttk.Label(b, text=label).grid(row=r, column=0, sticky="w", padx=(0, 10), pady=3)
            if kind == "check":
                v = tk.BooleanVar(value=bool(value))
                ttk.Checkbutton(b, variable=v).grid(row=r, column=1, sticky="w")
            else:
                v = tk.StringVar(value=str(value))
                ttk.Entry(b, textvariable=v, width=width).grid(row=r, column=1, sticky="w", pady=3)
            if help_:
                ttk.Label(b, text=help_, style="Muted.TLabel", wraplength=uc.px(260), justify="left").grid(
                    row=r, column=2, sticky="w", padx=(6, 0))
            self.v[key] = v
        row(0, "Search", "search", mon.get("search", ""))
        row(1, "Sites", "sites", " ".join(mon.get("sites") or []))
        row(2, "Check every (minutes)", "interval_min", mon.get("interval_min") or st.get("Monitoring/defaultInterval"),
            width=8, help_="1440 = once a day")
        row(3, "Ignore images newer than (minutes)", "delay_min", mon.get("delay_min", st.get("Monitoring/defaultDelay")),
            width=8, help_="gives taggers time to finish")
        row(4, "Notify", "notify", mon.get("notify", st.get("Monitoring/defaultNotificationEnabled")), "check")
        row(5, "Download new images", "download", mon.get("download", st.get("Monitoring/defaultDownloadEnabled")),
            "check")
        row(6, "Folder", "path", mon.get("path", ""), width=50, help_="blank = Settings")
        row(7, "File name", "filename", mon.get("filename", ""), width=50, help_="blank = Settings")
        row(8, "Post-filter", "postfilter", mon.get("postfilter", ""))
        row(9, "Also blacklisted images", "blacklisted", mon.get("blacklisted", False), "check")
        ttk.Label(b, text="The first check only notes where 'now' is: a monitor reports images posted after it "
                  "was created. Use 'Download all results' for older ones.", style="Muted.TLabel", wraplength=uc.px(520),
                  justify="left").grid(row=10, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.buttons("Save", self.ok)

    def ok(self):
        d = {k: (bool(v.get()) if isinstance(v, tk.BooleanVar) else v.get().strip()) for k, v in self.v.items()}
        sites = d.pop("sites").split()
        bad = [h for h in sites if self.app.store.get(h) is None]
        if not sites or bad:
            messagebox.showerror("Monitor", f"Unknown site(s): {' '.join(bad)}" if bad else "Give at least one site.",
                                 parent=self)
            return
        try:
            d["interval_min"] = max(1, int(d["interval_min"]))
            d["delay_min"] = max(0, int(d["delay_min"] or 0))
        except ValueError:
            messagebox.showerror("Monitor", "The interval and delay are numbers of minutes.", parent=self)
            return
        if self.mon.get("id"):
            self.app.lib.update_monitor(self.mon["id"], sites=sites, **d)
        else:
            self.app.lib.add_monitor(d.pop("search"), sites, **d)
        self.result = True
        self.destroy()


# =====================================================================================================
class Viewer(tk.Toplevel):
    """One image: fit to the window, animated GIFs play, videos open in your player. Arrow keys / wheel:
    previous / next. Space: slideshow. F11: full screen. S: save. Ctrl+S: save as. Esc: close."""

    def __init__(self, app, tab, index):
        super().__init__(app.root, background=COLORS["bg"])
        self.app = app
        self.tab, self.index = tab, index
        self.pil = None
        self.frames = []
        self.anim_job = None
        self.slide_job = None
        self.req = 0
        self.local_path = None
        geo = app.lib.get_state("viewer_geometry") if app.st.get("Viewer/rememberGeometry", True) else None
        self.geometry(geo or uc.size_for(self, uc.px(1240), uc.px(800)))
        th.dark_title_bar(self, app.dark)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(2, weight=1)
        bar = ttk.Frame(self, padding=(8, 6))
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Button(bar, text="◀", width=3, command=lambda: self.step(-1)).pack(side="left")
        ttk.Button(bar, text="▶", width=3, command=lambda: self.step(1)).pack(side="left", padx=(2, 10))
        ttk.Button(bar, text="Save", style="Accent.TButton", command=self.save).pack(side="left")
        ttk.Button(bar, text="Save as...", command=self.save_as).pack(side="left", padx=4)
        ttk.Button(bar, text="Add to list", command=self.to_list).pack(side="left")
        self.open_btn = ttk.Button(bar, text="Open file", command=self.open_file)
        self.open_btn.pack(side="left", padx=4)
        ttk.Button(bar, text="Browser", command=lambda: uc.open_url(self.img.page_url)).pack(side="left")
        self.ws_btn = ttk.Menubutton(bar, text="Search on")
        self.ws_btn.pack(side="left", padx=4)
        self.slide_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Slideshow", variable=self.slide_var, command=self.slideshow,
                        style="Toolbutton").pack(side="left", padx=4)
        ttk.Button(bar, text="Full screen", command=self.fullscreen).pack(side="left")
        more = ttk.Menubutton(bar, text="More")
        more.pack(side="left", padx=4)
        mm = tk.Menu(more, tearoff=False, postcommand=lambda: self._more_menu(mm))
        more.configure(menu=mm)
        self.info = tk.StringVar()
        ttk.Label(self, textvariable=self.info, style="Muted.TLabel", padding=(10, 0, 10, 4)).grid(
            row=1, column=0, columnspan=2, sticky="w")
        # tags + details
        side = ttk.Frame(self, padding=(8, 0, 4, 8))
        side.grid(row=2, column=0, sticky="ns")
        side.rowconfigure(0, weight=1)
        self.tagtree = ttk.Treeview(side, show="tree", selectmode="browse", height=20)
        self.tagtree.column("#0", width=uc.px(220))
        self.tagtree.grid(row=0, column=0, sticky="ns")
        self.tagtree.bind("<ButtonRelease-1>", self._tag_click)
        self.tagtree.bind("<Button-3>", self._tag_menu)
        self.details = tk.Text(side, width=30, height=12, wrap="word", relief="flat", font=app.f_small)
        th.style_text(self.details, COLORS, readonly_bg=True)
        self.details.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.canvas = tk.Canvas(self, highlightthickness=0, background=self._bg())
        self.canvas.grid(row=2, column=1, sticky="nsew", padx=(0, 8), pady=(0, 8))
        self.canvas.bind("<Configure>", lambda e: self._render())
        self.canvas.bind("<Button-2>", lambda e: self.close() if app.st.get("Viewer/imageCloseMiddleClick", True)
                         else None)
        self.canvas.bind("<Double-Button-1>", lambda e: self.fullscreen())
        self.bind("<Left>", lambda e: self.step(-1))
        self.bind("<Right>", lambda e: self.step(1))
        self.bind("<Escape>", lambda e: self.close() if not self.attributes("-fullscreen") else self.fullscreen())
        self.bind("<F11>", lambda e: self.fullscreen())
        self.bind("<space>", lambda e: (self.slide_var.set(not self.slide_var.get()), self.slideshow()))
        self.bind("s", lambda e: self.save())
        self.bind("<Control-s>", lambda e: self.save_as())
        self.bind("<Control-Alt-s>", lambda e: self.save(favorite=True))
        self.bind("<F5>", lambda e: self.load())
        if app.st.get("Viewer/imageNavigateScroll", True):
            self.canvas.bind("<MouseWheel>", lambda e: self.step(-1 if e.delta > 0 else 1))
            self.canvas.bind("<Button-4>", lambda e: self.step(-1))
            self.canvas.bind("<Button-5>", lambda e: self.step(1))
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.show_list(tab, index)
        self.focus_set()

    def _bg(self):
        return self.app.st.get("Viewer/imageBackgroundColor") or COLORS["text_bg"]

    def themed(self, p):
        self.configure(background=p["bg"])
        self.canvas.configure(background=self._bg())
        th.style_text(self.details, p, readonly_bg=True)
        th.dark_title_bar(self, p.get("name") == "dark")
        self._fill_tags()

    @property
    def img(self):
        return self.tab.items[self.index].image

    @property
    def site(self):
        return self.tab.items[self.index].site

    def show_list(self, tab, index):
        self.tab, self.index = tab, index
        self.load()

    def step(self, d):
        if not self.tab.items:
            return
        self.index = (self.index + d) % len(self.tab.items)
        self.load()

    def load(self):
        self.req += 1
        req = self.req
        img, site = self.img, self.site
        self.title(f"{img.site_key} #{img.id} - Packrat")
        self.info.set(f"{self.index + 1} / {len(self.tab.items)}   loading...")
        self._stop_anim()
        self.canvas.delete("all")
        self.canvas.create_text(self.canvas.winfo_width() // 2, self.canvas.winfo_height() // 2, text="Loading...",
                                fill=COLORS["muted"], tags=("msg",))
        self._fill_tags()
        self._fill_details()
        from .grab import websvc
        m = tk.Menu(self.ws_btn, tearoff=False)
        for svc in websvc.services(self.app.st):
            m.add_command(label=svc["name"], command=lambda s=svc: websvc.open_service(s, img))
        self.ws_btn["menu"] = m
        self.local_path = self.app.lib.md5_path(img.md5) if img.md5 else None
        self.open_btn.state(["!disabled"] if self.local_path else ["disabled"])
        if (not self.local_path and (self.app.st.get("whitelist_download") or "never") == "image"
                and self.app.whitelisted(img)):
            self.save()

        def work():
            if not img.details_loaded and site.source.meta.get("apis"):
                try:
                    site.details(img)
                except Exception:  # noqa: BLE001
                    pass
            return self._fetch(site, img)

        def done(res):
            if req != self.req:
                return
            self._fill_tags()
            self._fill_details()
            self._show(res)
        self.app.worker.submit(work, on_done=done, on_error=lambda e: self._msg(f"Could not load: {e}"))
        self._preload()

    def _fetch(self, site, img):
        """(kind, data): ("image", PIL frames), ("video", path or url)."""
        from PIL import Image as PI
        local = self.local_path
        if img.is_video:
            return ("video", local or "")
        src = None
        if local and os.path.isfile(local):
            src = local
        else:
            use_sample = bool(self.app.st.get("Viewer/viewSamples")) and img.sample_url
            url = (img.sample_url if use_sample else img.file_url) or img.sample_url or img.preview_url
            cache = paths.cache_dir("viewer") / (uc.hashlib.sha1(url.encode()).hexdigest() + "." + (img.ext or "img"))
            if not cache.is_file():
                r = site.request(url, "image", referer=site.referer("image", img.page_url, img), stream=True)
                try:
                    if r.status_code != 200:
                        raise ValueError(f"HTTP {r.status_code}")
                    tmp = cache.with_suffix(cache.suffix + ".part")
                    with open(tmp, "wb") as f:
                        for chunk in r.iter_content(65536):
                            if self.req and not self.winfo_exists():
                                break
                            f.write(chunk)
                    os.replace(tmp, cache)
                finally:
                    r.close()
            src = str(cache)
        im = PI.open(src)
        frames = []
        try:
            n = getattr(im, "n_frames", 1)
            if n > 1:
                for k in range(min(n, 300)):
                    im.seek(k)
                    frames.append((im.convert("RGBA"), int(im.info.get("duration", 80) or 80)))
            else:
                frames.append((im.convert("RGBA") if im.mode in ("P", "LA", "RGBA") else im.convert("RGB"), 0))
        finally:
            im.close()
        return ("image", frames)

    def _preload(self):
        n = int(self.app.st.get("Viewer/preload") or 0)
        for d in range(1, n + 1):
            for j in (self.index + d, self.index - d):
                if 0 <= j < len(self.tab.items):
                    sh = self.tab.items[j]
                    if not sh.image.is_video:
                        self.app.worker.submit(self._fetch_quiet, sh.site, sh.image)

    def _fetch_quiet(self, site, img):
        try:
            self._fetch(site, img)
        except Exception:  # noqa: BLE001
            pass

    def _show(self, res):
        kind, data = res
        if kind == "video":
            self.frames = []
            self.canvas.delete("all")
            w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
            self.canvas.create_text(w // 2, h // 2 - 20, text="▶  Video", font=self.app.f_title,
                                    fill=COLORS["fg"])
            b = ttk.Button(self.canvas, text="Play in your video player", style="Accent.TButton",
                           command=self.play_video)
            self.canvas.create_window(w // 2, h // 2 + 20, window=b)
            self.info.set(f"{self.index + 1} / {len(self.tab.items)}   video {_size(self.img.file_size)}")
            return
        self.frames = data
        self.pil = data[0][0]
        self.info.set(f"{self.index + 1} / {len(self.tab.items)}   {self.pil.width}x{self.pil.height}"
                      + (f"  {len(data)} frames" if len(data) > 1 else ""))
        self._render()
        if len(data) > 1:
            self._animate(0)
        if self.slide_var.get():
            self._next_slide()

    def _render(self):
        if not self.frames:
            return
        self._draw(self.frames[0][0] if not self.anim_job else self.pil)

    def _draw(self, pil):
        from PIL import Image as PI, ImageTk
        cw, ch = max(50, self.canvas.winfo_width()), max(50, self.canvas.winfo_height())
        w, h = pil.size
        scale = min(cw / w, ch / h)
        if scale > 1 and not self.app.st.get("Viewer/scaleUp"):
            scale = 1
        im = pil.resize((max(1, int(w * scale)), max(1, int(h * scale))), PI.LANCZOS) if scale != 1 else pil
        self.photo = ImageTk.PhotoImage(im)
        self.canvas.delete("all")
        self.canvas.create_image(cw // 2, ch // 2, image=self.photo)

    def _animate(self, k):
        if not self.frames or len(self.frames) < 2:
            return
        pil, dur = self.frames[k % len(self.frames)]
        self.pil = pil
        self._draw(pil)
        self.anim_job = self.after(max(20, dur), lambda: self._animate(k + 1))

    def _stop_anim(self):
        if self.anim_job:
            self.after_cancel(self.anim_job)
            self.anim_job = None

    def _msg(self, text):
        self.canvas.delete("all")
        self.canvas.create_text(self.canvas.winfo_width() // 2, self.canvas.winfo_height() // 2, text=text,
                                fill=COLORS["error"], width=500)

    def _fill_tags(self):
        t = self.tagtree
        t.delete(*t.get_children())
        img = self.img
        order = self.app.st.get("Viewer/tagOrder") or "type"
        tags = list(img.tags)
        from .grab.tags import KNOWN_TYPES
        if order == "type":
            tags.sort(key=lambda x: (KNOWN_TYPES.index(x.type) if x.type in KNOWN_TYPES else 99, x.name))
        elif order == "alphabetical":
            tags.sort(key=lambda x: x.name)
        show_count = self.app.st.get("Viewer/showTagCount")
        for tg in tags:
            t.insert("", "end", text=tg.name + (f"  ({tg.count:,})" if show_count and tg.count else ""),
                     tags=(tg.type or "general", "n:" + tg.name))
        for typ in set(KNOWN_TYPES) | {"general", ""}:
            t.tag_configure(typ or "general", foreground=uc.tag_color(typ, self.app.st, self.app.dark))

    def _fill_details(self):
        img = self.img
        d = self.details
        d.configure(state="normal")
        d.delete("1.0", "end")
        rows = [("Site", img.site_key), ("Id", str(img.id)), ("Rating", img.rating or "?"),
                ("Score", "?" if img.score in (None, "") else str(img.score)),
                ("Size", f"{img.width}x{img.height}  {_size(img.file_size)}"), ("Type", img.ext or "?"),
                ("Posted", img.date.strftime("%Y-%m-%d %H:%M") if img.date else "?"), ("MD5", img.md5 or "?")]
        for k, v in rows:
            d.insert("end", f"{k}: ", ("k",))
            d.insert("end", v + "\n")
        for s in (img.sources or [])[:5]:
            d.insert("end", "Source: ", ("k",))
            d.insert("end", str(s) + "\n", ("link",))
        if self.local_path:
            d.insert("end", "Saved: ", ("k",))
            d.insert("end", self.local_path + "\n")
        d.tag_configure("k", font=self.app.f_bold)
        d.tag_configure("link", foreground=COLORS["accent"], underline=True)
        d.tag_bind("link", "<Button-1>", self._open_link)
        d.configure(state="disabled")

    def _open_link(self, e):
        idx = self.details.index(f"@{e.x},{e.y}")
        line = self.details.get(f"{idx} linestart", f"{idx} lineend")
        uc.open_url(line.split(": ", 1)[-1].strip())

    def _tag_name(self, e):
        iid = self.tagtree.identify_row(e.y)
        if not iid:
            return None
        for t in self.tagtree.item(iid, "tags"):
            if t.startswith("n:"):
                return t[2:]
        return None

    def _tag_click(self, e):
        name = self._tag_name(e)
        if name:
            self.app.new_search_tab(name, self.tab.sites, run=True)
            self.app.root.lift()

    def _tag_menu(self, e):
        name = self._tag_name(e)
        if name:
            tag_menu(self.app, name, e, None)

    def play_video(self):
        if self.local_path and os.path.isfile(self.local_path):
            uc.open_path(self.local_path)
            return
        img, site = self.img, self.site
        self.info.set("downloading the video to play it...")

        def work():
            cache = paths.cache_dir("viewer") / (img.md5 or str(img.id)) if img.md5 else None
            target = Path(str(cache) + "." + (img.ext or "mp4")) if cache else paths.cache_dir("viewer") / f"{img.id}.mp4"
            if not target.is_file():
                r = site.request(img.file_url, "image", referer=site.referer("image", img.page_url, img), stream=True)
                try:
                    if r.status_code != 200:
                        raise ValueError(f"HTTP {r.status_code}")
                    tmp = target.with_suffix(target.suffix + ".part")
                    with open(tmp, "wb") as f:
                        for chunk in r.iter_content(1 << 16):
                            f.write(chunk)
                    os.replace(tmp, target)
                finally:
                    r.close()
            return str(target)
        self.app.worker.submit(work, on_done=lambda p: (uc.open_path(p), self.info.set("playing in your video player")),
                               on_error=lambda e: self.info.set(f"could not get the video: {e}"))

    def _more_menu(self, m):
        m.delete(0, "end")
        fav = bool(self.app.st.get("Save/path_favorites"))
        m.add_command(label="Save to the favorites folder" if fav else "Save to the favorites folder (set it in "
                      "Settings > Saving first)", accelerator="Ctrl+Alt+S", command=lambda: self.save(favorite=True),
                      state="normal" if fav else "disabled")
        m.add_separator()
        m.add_command(label="Copy the picture", command=self.copy_picture,
                      state="normal" if self.pil is not None and sys.platform == "win32" else "disabled")
        m.add_command(label="Copy the picture's address", command=lambda: self._copy(self.img.file_url))
        m.add_command(label="Copy the page address", command=lambda: self._copy(self.img.page_url))
        m.add_separator()
        m.add_command(label="Show the saved file", command=lambda: uc.reveal(self.local_path),
                      state="normal" if self.local_path else "disabled")
        m.add_command(label="Reload", accelerator="F5", command=self.load)

    def _copy(self, text):
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.info.set("copied")

    def copy_picture(self):
        if self.pil is not None and uc.copy_image(self.pil):
            self.info.set("picture copied - paste it anywhere")
        else:
            self.info.set("could not copy the picture")

    def save(self, favorite: bool = False):
        def each(img, res):
            if res:
                r = res[0]
                self.info.set(f"{r.status}: {r.paths[0] if r.paths else r.message}")
                if r.paths:
                    self.local_path = r.paths[0]
                    self.open_btn.state(["!disabled"])
                    for sh in self.tab.items:
                        if sh.image is img:
                            sh.downloaded = True
                    self.tab._relayout()
        if favorite and not self.app.st.get("Save/path_favorites"):
            self.info.set("No favorites folder yet: set it in Settings > Saving (advanced view)")
            return
        self.app.save_images([(self.site, self.img)], each,
                             path=self.app.st.save_path(favorite=True) if favorite else None,
                             filename=self.app.st.filename(favorite=True) if favorite else None)

    def save_as(self):
        img = self.img
        p = filedialog.asksaveasfilename(parent=self, initialfile=f"{img.md5 or img.id}.{img.ext or 'jpg'}",
                                         title="Save as")
        if not p:
            return
        folder, name = os.path.dirname(p), os.path.basename(p)
        self.app.save_images([(self.site, img)], lambda i, r: self.info.set(r[0].status if r else ""),
                             path=folder, filename=name.replace("%", "_"))

    def to_list(self):
        self.app.add_to_downloads_images([(self.site, self.img)])

    def open_file(self):
        if self.local_path:
            uc.open_path(self.local_path)

    def slideshow(self):
        if self.slide_job:
            self.after_cancel(self.slide_job)
            self.slide_job = None
        if self.slide_var.get():
            self._next_slide()

    def _next_slide(self):
        if self.slide_job:
            self.after_cancel(self.slide_job)
        sec = int(self.app.st.get("Viewer/slideshow") or 5)
        self.slide_job = self.after(sec * 1000, lambda: self.step(1) if self.slide_var.get() else None)

    def fullscreen(self):
        self.attributes("-fullscreen", not self.attributes("-fullscreen"))

    def close(self):
        self._stop_anim()
        if self.slide_job:
            self.after_cancel(self.slide_job)
        try:
            if not self.attributes("-fullscreen"):
                self.app.lib.set_state("viewer_geometry", self.geometry())
        except tk.TclError:
            pass
        if self in self.app.viewers:
            self.app.viewers.remove(self)
        self.destroy()


# =====================================================================================================
def main(argv=None) -> int:
    from .panel_ui import make_root
    try:
        root = make_root()
    except tk.TclError as e:
        print(f"Packrat's window needs a display ({e}). The command line works without one: packrat --help",
              file=sys.stderr)
        return 1
    root.withdraw()
    try:
        root.configure(background=th.palette(cp.load_settings(cp.settings_path())[0].get("theme"))["bg"])
    except Exception:  # noqa: BLE001
        pass
    ui = cp.load_ui_state()
    scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.minsize(min(int(900 * scale), sw - 40), min(int(600 * scale), sh - 80))
    g = ui.get("main_geometry") or ""
    try:
        size = g.split("+")[0]
        w, h = (int(x) for x in size.split("x"))
        root.geometry(g if w <= sw and h <= sh else f"{min(w, sw - 40)}x{min(h, sh - 80)}")
    except ValueError:
        w, h = min(int(1360 * scale), sw - 40), min(int(860 * scale), sh - 80)
        root.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")
    from logging.handlers import RotatingFileHandler
    fh = RotatingFileHandler(paths.logs_dir() / "packrat-window.log", maxBytes=5_000_000, backupCount=2,
                             encoding="utf-8")
    from .redact import RedactingFormatter
    fh.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(threadName)s %(message)s"))
    logging.getLogger().addHandler(fh)
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    app = PackratApp(root)
    try:
        tab = int(ui.get("main_tab", 0))
        if 0 <= tab < len(app.nb.tabs()):
            app.nb.select(tab)
    except (ValueError, tk.TclError):
        pass
    root.deiconify()
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
