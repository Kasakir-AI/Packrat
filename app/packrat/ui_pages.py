"""The other tabs of Packrat's main window: Downloads, Favorites, Monitors, Settings, Tools, Log."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk

from . import control_panel as cp
from . import paths
from . import theme as th
from . import ui_common as uc
from .ui_common import COLORS

log = logging.getLogger("packrat")


def _ago(ts) -> str:
    if not ts:
        return ""
    d = time.time() - float(ts)
    if d < 0:
        d = -d
        if d < 3600:
            return f"in {int(d // 60)} min"
        if d < 86400 * 2:
            return f"in {d / 3600:.1f} h"
        return f"in {d / 86400:.0f} days"
    if d < 90:
        return "just now"
    if d < 3600:
        return f"{int(d // 60)} min ago"
    if d < 86400 * 2:
        return f"{d / 3600:.1f} h ago"
    return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d")


def _fmt_bytes(n) -> str:
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return ""


def _sort_key(v):
    t = str(v).strip().replace(",", "").rstrip("%")
    try:
        return (0, float(t), "")
    except ValueError:
        return (1, 0.0, str(v).lower())


def apply_sort(t) -> None:
    """Re-sort a list after it was refilled (keeps the column the user clicked)."""
    col, rev = getattr(t, "_sort", (None, False))
    if col is None:
        return
    rows = [(t.set(i, col) if col != "#0" else t.item(i, "text"), i) for i in t.get_children("")]
    rows.sort(key=lambda r: _sort_key(r[0]), reverse=rev)
    for n, (_, i) in enumerate(rows):
        t.move(i, "", n)


def _tree(parent, cols, heads, widths, height=12, show="headings", selectmode="extended", sortable=True):
    f = ttk.Frame(parent)
    f.columnconfigure(0, weight=1)
    f.rowconfigure(0, weight=1)
    t = ttk.Treeview(f, columns=cols, show=show, height=height, selectmode=selectmode)

    def sort_by(c):
        col, rev = getattr(t, "_sort", (None, False))
        t._sort = (c, not rev if col == c else False)       # noqa: SLF001
        for cc, hh in zip(cols, heads):
            arrow = (" \u25bc" if t._sort[1] else " \u25b2") if cc == c else ""   # noqa: SLF001
            t.heading(cc, text=hh + arrow)
        apply_sort(t)
    for c, h, w in zip(cols, heads, widths):
        t.heading(c, text=h, **({"command": lambda c=c: sort_by(c)} if sortable else {}))
        t.column(c, width=uc.px(w), stretch=True, anchor="w", minwidth=uc.px(40))
    if sortable and show != "headings":
        t.heading("#0", command=lambda: sort_by("#0"))
    sb = ttk.Scrollbar(f, orient="vertical", command=t.yview)
    t.configure(yscrollcommand=sb.set)
    t.grid(row=0, column=0, sticky="nsew")
    sb.grid(row=0, column=1, sticky="ns")
    base = dict(zip(cols, widths))
    if show != "headings":
        base = {"#0": int(t.column("#0", "width") / uc.SCALE[0]) or 200, **base}
    uc.fit_columns(t, base)
    return f, t


def empty_note(tree, text: str):
    """A message shown inside an empty list (call .update() after filling the list)."""
    lab = tk.Label(tree, text=text, justify="center", wraplength=uc.px(560), borderwidth=0)

    def update():
        if tree.get_children():
            lab.place_forget()
        else:
            lab.configure(background=COLORS["text_bg"], foreground=COLORS["muted"])
            lab.place(relx=0.5, rely=0.4, anchor="center")
    lab.update_note = update
    return lab


def help_button(parent, app, topic, side="right"):
    b = ttk.Button(parent, text="? Help", command=lambda: app.open_help(topic))
    b.pack(side=side, padx=(8, 0))
    uc.tip(b, "How this tab works")
    return b


# =====================================================================================================
class DownloadsPage:
    """The download list: searches ('groups') and single images. Start runs it in a separate process; Stop
    and Pause work like in the site archive; everything resumes where it stopped."""

    def __init__(self, parent, app):
        self.app = app
        self.runner = cp.CliRunner()
        self.then_close = False
        self.stats = {}
        self.cur = {}
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        bar = ttk.Frame(parent, padding=(8, 8, 8, 4))
        bar.grid(row=0, column=0, sticky="ew")
        help_button(bar, app, "download")
        adv = []
        for text, cmd, help_, pad in (
                ("Add a search...", self.add_search, "Add a search to download (like 'Download all results' in "
                 "Browse)", 0),
                ("Add a picture...", self.add_one, "Add one picture by its page address, e.g. "
                 "https://e621.net/posts/12345 (Grabber's 'Add unique')", 4),
                ("Edit...", self.edit, "Change the selected search (double-click does the same)", 4),
                ("Remove", self.remove, "Remove the selected rows from the list (files already saved stay)", 0),
                ("\u25b2", lambda: self.move(-1), "Move up: rows are downloaded from top to bottom", 4),
                ("\u25bc", lambda: self.move(1), "Move down", 2),
                ("Clear finished", self.clear_done, "Remove the rows that are done", 12),
                ("Start again", self.reset, "Reset the selected rows (or all) so they start again from the first "
                 "page; pictures you have are still skipped", 4),
                ("Save list...", self.save_list, "Save the list to a file", 12),
                ("Load list...", self.load_list, "Load a list saved by Packrat (.prl) or Grabber (.igl)", 4)):
            bt = ttk.Button(bar, text=text, command=cmd, width=3 if len(text) == 1 else 0)
            bt.pack(side="left", padx=(pad, 0))
            uc.tip(bt, help_)
            if text in ("\u25b2", "\u25bc", "Save list...", "Load list..."):
                adv.append(bt)
        uc.advanced_only(*adv)
        f, self.tree = _tree(parent, ("what", "sites", "where", "status", "progress"),
                             ("What", "Sites", "Saved in / as", "Status", "Progress"), (300, 150, 260, 90, 190),
                             sortable=False)     # the order is the download order
        f.grid(row=1, column=0, sticky="nsew", padx=8)
        self.empty = empty_note(self.tree, "The download list is empty.\n\nIn the Browse tab, search for something, "
                                "then use 'Download all results', 'Download this page' or 'Download selected'.\n"
                                "The searches appear here; press Start to download them.")
        self.tree.bind("<Double-1>", self.edit)
        self.tree.bind("<Delete>", lambda e: self.remove())
        self.tree.bind("<Button-3>", self._menu)
        pf = ttk.Frame(parent, padding=(8, 6, 8, 8))
        pf.grid(row=2, column=0, sticky="ew")
        pf.columnconfigure(0, weight=1)
        self.phase = tk.StringVar(value="Press Start to download everything in the list.")
        ttk.Label(pf, textvariable=self.phase, font=app.f_bold).grid(row=0, column=0, sticky="w")
        self.statline = tk.StringVar()
        ttk.Label(pf, textvariable=self.statline).grid(row=1, column=0, sticky="w")
        self.fbar = ttk.Progressbar(pf, maximum=1000)
        self.fbar.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        self.curfile = tk.StringVar()
        ttk.Label(pf, textvariable=self.curfile, style="Muted.TLabel").grid(row=3, column=0, sticky="w")
        # what to do when everything is done (Grabber shows this in its download window too)
        wf = ttk.Frame(pf)
        wf.grid(row=4, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Label(wf, text="When everything is downloaded:").pack(side="left")
        from .grab.settings import BY_KEY
        cur = str(app.st.get("Batch/end") or "nothing")
        self._end_choices = [c for c in BY_KEY["Batch/end"].choices if c[0] != "cdtray" or cur == "cdtray"]
        self.when_done = tk.StringVar(value=next((c[1] for c in self._end_choices if c[0] == cur), "Do nothing"))
        wcb = ttk.Combobox(wf, textvariable=self.when_done, values=[c[1] for c in self._end_choices],
                           state="readonly", width=34)
        wcb.pack(side="left", padx=6)
        ofb = ttk.Button(wf, text="Open the download folder", command=lambda: uc.open_path(self.app.st.save_path()))
        ofb.pack(side="left", padx=(18, 0))
        uc.tip(ofb, "Open the folder pictures are saved in")
        wcb.bind("<<ComboboxSelected>>", lambda e: app.st.set(
            "Batch/end", next((c[0] for c in self._end_choices if c[1] == self.when_done.get()), "nothing")))
        ctl = ttk.Frame(pf)
        ctl.grid(row=0, column=1, rowspan=4, sticky="e", padx=(12, 0))
        self.start_btn = ttk.Button(ctl, text="Start", style="Big.Accent.TButton", command=self.start)
        self.start_sel_btn = ttk.Button(ctl, text="Start selected", command=lambda: self.start(selected=True))
        self.pause_btn = ttk.Button(ctl, text="Pause", command=self.pause)
        self.stop_btn = ttk.Button(ctl, text="Stop", command=self.stop)
        self.start_sel_btn.pack(side="left", padx=2)
        self.pause_btn.pack(side="left", padx=2)
        self.stop_btn.pack(side="left", padx=2)
        self.start_btn.pack(side="left", padx=(8, 0))
        uc.advanced_only(self.start_sel_btn)
        uc.tip(self.start_btn, "Download everything in the list that isn't finished yet (Ctrl+Enter)")
        uc.tip(self.start_sel_btn, "Download only the rows you selected")
        uc.tip(self.pause_btn, "Pause the downloads; press again to continue")
        uc.tip(self.stop_btn, "Stop. Nothing is lost: Start continues where it stopped.")
        self._buttons()
        self.refresh()
        app.root.after(150, self._poll)

    # ---- list ----------------------------------------------------------------------------------
    def refresh(self):
        sel = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        for it in self.app.lib.batch_items():
            d = it["data"]
            if it["kind"] == "group":
                what = f"\U0001F50D {d.get('search') or '(everything)'}"
                lim = d.get("limit") or 0
                what += f"   ({'all' if not lim else lim} images" + (f", from page {d['page']}" if (d.get("page") or 1) > 1
                                                                        else "") + ")"
                sites = " ".join(d.get("sites") or [])
            else:
                im = d.get("image") or {}
                what = f"\U0001F5BC {im.get('site_key', '')} #{im.get('id', '')}"
                if d.get("from_monitor"):
                    what += "  (monitor)"
                sites = im.get("site_key", "")
            where = " / ".join(x for x in (d.get("path") or "", d.get("filename") or "") if x) or "(Settings)"
            pr = it.get("progress") or {}
            if it["kind"] == "group":
                prog = f"{pr.get('done', 0)}" + (f" / {pr['limit']}" if pr.get("limit") else "") + " images"
                if pr.get("page"):
                    prog += f", page {pr['page']}" + (f"/{pr['page_count']}" if (pr.get("page_count") or 0) > 0 else "")
            else:
                prog = pr.get("status", "")
            status = {"pending": "waiting", "running": "running", "done": "done", "paused": "stopped"}.get(
                it.get("status") or "pending", it.get("status") or "")
            self.tree.insert("", "end", iid=str(it["id"]), values=(what, sites, where, status, prog),
                             tags=(it.get("status") or "pending",))
        for i in sel:
            if self.tree.exists(i):
                self.tree.selection_add(i)
        self.themed(COLORS)
        self.empty.update_note()
        waiting = sum(1 for i in self.tree.get_children() if "done" not in self.tree.item(i, "tags"))
        try:
            self.app.nb.tab(self.app.frames["downloads"], text=f"Downloads ({waiting})" if waiting else "Downloads")
        except (tk.TclError, AttributeError, KeyError):
            pass

    def themed(self, p):
        self.tree.tag_configure("done", foreground=p["ok"])
        self.tree.tag_configure("running", foreground=p["accent"])
        self.tree.tag_configure("paused", foreground=p["warn"])

    def _ids(self):
        return [int(i) for i in self.tree.selection()]

    def add_search(self):
        from .app_ui import GroupDialog
        tab = self.app.browse.current()
        d = GroupDialog(self.tree, self.app, tab.query.get() if tab else "", tab.sites if tab else
                        self.app.st.get("sites"), "").modal()
        if d:
            self.app.add_group(**d)

    def add_one(self):
        try:
            clip = self.tree.clipboard_get().strip()
        except tk.TclError:
            clip = ""
        addr = simpledialog.askstring(
            "Add a picture", "Page address of the picture (open it on the site and copy the address):",
            initialvalue=clip if clip.startswith(("http://", "https://")) else "", parent=self.tree)
        if not addr or not addr.strip():
            return
        from .grab.search import find_post
        self.app.set_status("Looking up the picture...")
        self.app.worker.submit(find_post, self.app.store, addr,
                               on_done=lambda r: self.app.add_to_downloads_images([r]),
                               on_error=lambda e: messagebox.showerror("Add a picture", uc.friendly(str(e)),
                                                                       parent=self.tree))

    def edit(self, e=None):
        ids = self._ids()
        if len(ids) != 1 or self.running():
            return
        it = next((x for x in self.app.lib.batch_items([ids[0]])), None)
        if not it or it["kind"] != "group":
            return
        from .app_ui import GroupDialog
        d0 = it["data"]
        dlg = GroupDialog(self.tree, self.app, d0.get("search", ""), d0.get("sites") or [], d0.get("postfilter", ""))
        for k in ("page", "per_page", "limit", "path", "filename"):
            if k in dlg.v and d0.get(k) not in (None, ""):
                dlg.v[k].set(d0[k])
        dlg.v["get_blacklisted"].set(bool(d0.get("get_blacklisted")))
        d = dlg.modal()
        if d:
            d0.update({"search": d["query"], "sites": d["sites"], "page": d["page"], "per_page": d["per_page"],
                       "limit": d["limit"], "path": d["path"], "filename": d["filename"],
                       "postfilter": d["postfilter"], "get_blacklisted": d["get_blacklisted"],
                       "previous": {}, "done": {}, "finished_sites": []})
            self.app.lib.batch_update(it["id"], status="pending", data=d0, progress={})
            self.refresh()

    def _menu(self, e):
        iid = self.tree.identify_row(e.y)
        if iid and iid not in self.tree.selection():
            self.tree.selection_set(iid)
        m = tk.Menu(self.app.root, tearoff=False)
        m.add_command(label="Edit...", command=self.edit)
        m.add_command(label="Start selected", command=lambda: self.start(selected=True))
        m.add_command(label="Reset (start again from the first page)", command=self.reset)
        m.add_command(label="Remove", command=self.remove)
        it = next((x for x in self.app.lib.batch_items([int(iid)])), None) if iid else None
        if it and it["kind"] == "group":
            m.add_command(label="Open the search in Browse", command=lambda: self.app.new_search_tab(
                it["data"].get("search", ""), it["data"].get("sites"), run=True))
        folder = (it or {}).get("data", {}).get("path") or self.app.st.save_path()
        m.add_command(label="Open the folder", command=lambda: uc.open_path(folder))
        m.tk_popup(e.x_root, e.y_root)

    def remove(self):
        ids = self._ids()
        if ids and not self.running():
            self.app.lib.batch_remove(ids)
            self.refresh()

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
        for i in self._ids() or [int(x) for x in self.tree.get_children()]:
            it = next((x for x in self.app.lib.batch_items([i])), None)
            if it is None:
                continue
            d = it["data"]
            if it["kind"] == "group":
                d.update(previous={}, done={}, finished_sites=[])
            self.app.lib.batch_update(i, status="pending", data=d, progress={})
        self.refresh()

    def save_list(self):
        p = filedialog.asksaveasfilename(parent=self.tree, title="Save the download list", defaultextension=".prl",
                                         filetypes=(("Packrat download list", "*.prl"), ("All files", "*.*")))
        if not p:
            return
        items = [{"kind": it["kind"], "data": it["data"]} for it in self.app.lib.batch_items()]
        Path(p).write_text(json.dumps({"packrat_list": 1, "items": items}, indent=1, ensure_ascii=False), "utf-8")
        self.app.set_status(f"Download list saved ({len(items)} items)")

    def load_list(self):
        p = filedialog.askopenfilename(parent=self.tree, title="Load a download list",
                                       filetypes=(("Download lists", "*.prl *.igl"), ("All files", "*.*")))
        if not p:
            return
        try:
            d = json.loads(Path(p).read_text("utf-8-sig"))
        except (OSError, ValueError) as e:
            messagebox.showerror("Load list", f"Could not read {p}: {e}", parent=self.tree)
            return
        n = 0
        if isinstance(d, dict) and d.get("packrat_list"):
            for it in d.get("items") or []:
                if it.get("kind") in ("group", "unique") and isinstance(it.get("data"), dict):
                    self.app.lib.batch_add(it["kind"], it["data"])
                    n += 1
            notes = []
        else:
            from .grab import grabber_import as G
            n, notes = G.load_igl(p, self.app.lib, self.app.store)
        self.refresh()
        messagebox.showinfo("Load list", f"{n} item(s) added." + ("\n\n" + "\n".join(notes[:10]) if notes else ""),
                            parent=self.tree)

    # ---- running -------------------------------------------------------------------------------
    def running(self) -> bool:
        return self.runner.running

    def _buttons(self):
        run = self.running()
        for b in (self.start_btn, self.start_sel_btn):
            b.state(["disabled"] if run else ["!disabled"])
        for b in (self.stop_btn, self.pause_btn):
            b.state(["!disabled"] if run else ["disabled"])
        self.pause_btn.configure(text="Resume" if getattr(self, "paused", False) else "Pause")

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
        self.stats, self.cur, self.paused = {}, {}, False
        self.phase.set("Starting...")
        self.statline.set("")
        try:
            self.runner.start(argv, env=env)
        except (OSError, RuntimeError) as e:
            messagebox.showerror("Start", str(e), parent=self.tree)
            return
        self.app.log_line("Download list started", logging.INFO)
        self.app.head_msg("Downloading...")
        self._buttons()

    def stop(self, then_close=False):
        self.then_close = then_close or self.then_close
        if self.running():
            self.runner.stop()
            self.phase.set("Stopping (finishing the files being written)...")

    def pause(self):
        if not self.running():
            return
        self.paused = not getattr(self, "paused", False)
        (self.runner.pause if self.paused else self.runner.resume)()
        self.phase.set("Paused" if self.paused else "Resumed")
        self._buttons()

    def _poll(self):
        if self.app.closing and not self.running() and not self.then_close:
            return
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
        if changed or (self.running() and int(time.time() * 2) % 4 == 0):
            self.refresh()
        self.app.root.after(250, self._poll)

    def _event(self, kind, d) -> bool:
        if kind == "phase":
            self.phase.set(d.get("name", ""))
        elif kind == "grab_stats":
            self.stats = d
            s = d
            dup = s.get("duplicate", 0) + s.get("copied", 0) + s.get("moved", 0) + s.get("linked", 0)
            self.statline.set(f"{s.get('saved', 0)} saved ({_fmt_bytes(s.get('bytes'))}),  {s.get('exists', 0)} "
                              f"already there,  {dup} duplicates,  {s.get('blacklisted', 0) + s.get('filtered', 0)} "
                              f"filtered,  {s.get('failed', 0) + s.get('missing', 0)} failed"
                              + (f",  {s['forbidden']} refused (403)" if s.get("forbidden") else ""))
        elif kind == "grab_bytes":
            tot = d.get("total") or 0
            self.fbar["value"] = (d.get("n", 0) / tot * 1000) if tot else 0
        elif kind == "grab_image":
            self.curfile.set(f"{d.get('status')}: {d.get('path') or d.get('message') or d.get('key')}")
            if d.get("status") in ("failed", "forbidden", "missing"):
                self.app.log_line(f"{d.get('key')}: {d.get('status')} {d.get('message', '')}", logging.WARNING)
        elif kind in ("grab_item", "grab_group", "grab_page"):
            if kind == "grab_page" and d.get("error"):
                self.app.log_line(f"{d.get('site')} page {d.get('page')}: {d.get('error')}", logging.WARNING)
            return kind == "grab_item"
        elif kind == "grab_done":
            self.phase.set("Finished")
        return False

    def _exited(self, code, crashed):
        msgs = {0: "Finished", 130: "Stopped - Start continues where it stopped", 3: "Stopped: a site refused "
                "access (403). See the Log tab.", 2: "Not started: check the settings (see the Log tab)"}
        self.phase.set(msgs.get(code, f"Ended with an error (code {code}) - see the Log tab"))
        self.app.head_msg("")
        self.fbar["value"] = 0
        self._buttons()
        self.refresh()
        s = self.stats
        if code == 0 and s:
            try:
                from .notify import notify
                if not self.app.root.focus_displayof():
                    notify("Packrat", f"Downloads finished: {s.get('saved', 0)} saved, "
                                      f"{s.get('failed', 0) + s.get('missing', 0)} failed")
            except Exception:  # noqa: BLE001
                pass
        if self.then_close:
            self.then_close = False
            self.app.closing = False
            self.app.on_close()


# =====================================================================================================
class FavoritesPage:
    """Favorite searches (with how many new images since you last looked) and searches kept for later."""

    def __init__(self, parent, app):
        self.app = app
        self.thumbs = uc.Thumbs(app.worker, 56, True)
        self.photos = {}
        self.new_counts: dict[str, int] = app.lib.get_state("favorite_new") or {}
        parent.columnconfigure(0, weight=3)
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(1, weight=1)
        bar = ttk.Frame(parent, padding=(8, 8, 8, 4))
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Button(bar, text="Add...", command=self.add).pack(side="left")
        ttk.Button(bar, text="Edit...", command=self.edit).pack(side="left", padx=4)
        ttk.Button(bar, text="Remove", command=self.remove).pack(side="left")
        mb = ttk.Button(bar, text="Monitor...", command=self.monitor)
        mb.pack(side="left", padx=4)
        uc.advanced_only(mb)
        ttk.Button(bar, text="Check for new pictures", command=self.check_new).pack(side="left", padx=(12, 0))
        ttk.Button(bar, text="Download new pictures", command=self.download_new).pack(side="left", padx=4)
        self.msg = tk.StringVar(value="Double-click to open. 'New' = images posted since you last opened it.")
        ttk.Label(bar, textvariable=self.msg, style="Muted.TLabel").pack(side="left", padx=12)
        help_button(bar, app, "favorites")
        style = ttk.Style(app.root)
        style.configure("Fav.Treeview", rowheight=uc.px(62))
        f, self.tree = _tree(parent, ("note", "new", "viewed", "sites", "monitor"),
                             ("Priority", "New", "Last opened", "Sites", "Monitored"), (60, 60, 110, 220, 90),
                             show="tree headings")
        self.tree.heading("#0", text="Favorite")
        self.tree.column("#0", width=uc.px(300))
        f.grid(row=1, column=0, sticky="nsew", padx=(8, 4))
        self.empty = empty_note(self.tree, "No favorites yet.\n\nIn a Browse tab, search for something and press "
                                "'\u2606 Favorite' to keep the search here. Then 'Check for new pictures' tells you "
                                "what was posted since you last looked.")
        self.tree.bind("<Double-1>", lambda e: self.open())
        self.tree.bind("<Button-3>", self._menu)
        lf = ttk.Frame(parent)
        lf.grid(row=1, column=1, sticky="nsew", padx=(4, 8))
        lf.rowconfigure(1, weight=1)
        lf.columnconfigure(0, weight=1)
        ttk.Label(lf, text="Kept for later", font=app.f_bold).grid(row=0, column=0, sticky="w")
        self.later = tk.Listbox(lf, activestyle="dotbox", borderwidth=0, highlightthickness=1)
        self.later.grid(row=1, column=0, sticky="nsew")
        self.later.bind("<Double-1>", lambda e: self.open_later())
        lb = ttk.Frame(lf)
        lb.grid(row=2, column=0, sticky="ew", pady=4)
        ttk.Button(lb, text="Open", command=self.open_later).pack(side="left")
        ttk.Button(lb, text="Remove", command=self.remove_later).pack(side="left", padx=4)
        ttk.Button(lb, text="To favorites", command=self.later_to_fav).pack(side="left")
        self.refresh()

    def themed(self, p):

        try:
            self.later.configure(background=p["field"], foreground=p["fg"], selectbackground=p["select_bg"],
                                 selectforeground=p["select_fg"])
        except tk.TclError:
            pass

    def refresh(self):
        mode = self.app.st.get("favorites_display") or "ind"
        self.tree.configure(style="Fav.Treeview" if "i" in mode else "Treeview")
        favs = self.app.lib.favorites()
        sort = self.app.st.get("favoritesSort") or "name"
        if sort == "note":
            favs.sort(key=lambda f: (-int(f["note"] or 0), f["name"].lower()))
        elif sort == "lastviewed":
            favs.sort(key=lambda f: -(f["last_viewed"] or 0))
        else:
            favs.sort(key=lambda f: f["name"].lower())
        mons = {m["search"]: m for m in self.app.lib.monitors()}
        self.tree.delete(*self.tree.get_children())
        for f in favs:
            name = f["name"] if "n" in mode or "i" not in mode else ""
            vals = (f["note"], self.new_counts.get(f["name"], ""), _ago(f["last_viewed"]),
                    " ".join(f["sites"]) or "(default)", "every " + _every(mons[f["name"]]["interval_min"])
                    if f["name"] in mons else "") if ("d" in mode or "i" not in mode) else ("", "", "", "", "")
            self.tree.insert("", "end", iid=f["name"], text=name or f["name"], values=vals,
                             image=self.photos.get(f["name"], ""))
            if "i" in mode and f.get("image"):
                self._thumb(f)
        self.later.delete(0, "end")
        for t in self.app.lib.later():
            self.later.insert("end", t)
        apply_sort(self.tree)
        self.empty.update_note()

    def _thumb(self, f):
        try:
            d = json.loads(f["image"])
        except (ValueError, TypeError):
            return
        site = self.app.store.get(d.get("site", ""))
        if site is None or not d.get("url"):
            return
        from .grab.image import Image
        im = Image(site.host)
        im.preview_url = d["url"]

        def cb(ph, name=f["name"]):
            if ph is not None and self.tree.exists(name):
                self.photos[name] = ph
                self.tree.item(name, image=ph)
        self.thumbs.get(site, im, cb)

    def _sel(self):
        s = self.tree.selection()
        return s[0] if s else None

    def open(self, name=None):
        name = name or self._sel()
        if not name:
            return
        f = self.app.lib.favorite(name)
        self.app.lib.update_favorite(name, last_viewed=time.time())
        self.new_counts.pop(name, None)
        self.app.lib.set_state("favorite_new", self.new_counts)
        tab = self.app.new_search_tab(name, (f or {}).get("sites") or None, run=False)
        if f and f.get("postfilter"):
            tab.pf.set(f["postfilter"])
        tab.search()
        self.app.nb.select(self.app.frames["browse"])
        self.refresh()

    def add(self):
        name = simpledialog.askstring("Add a favorite", "Search to keep as a favorite (tags):", parent=self.tree)
        if name and name.strip():
            self.app.lib.add_favorite(name.strip(), self.app.st.get("sites") or [])
            self.refresh()

    def edit(self):
        name = self._sel()
        f = self.app.lib.favorite(name) if name else None
        if not f:
            return
        dlg = uc.Dialog(self.tree, f"Favorite: {name}")
        v = {"name": tk.StringVar(value=f["name"]), "note": tk.StringVar(value=str(f["note"])),
             "sites": tk.StringVar(value=" ".join(f["sites"])), "postfilter": tk.StringVar(value=f["postfilter"])}
        for r, (k, lab) in enumerate((("name", "Search"), ("note", "Priority (0-100)"),
                                      ("sites", "Sites (blank = selected)"), ("postfilter", "Post-filter"))):
            ttk.Label(dlg.body, text=lab).grid(row=r, column=0, sticky="w", padx=(0, 10), pady=3)
            ttk.Entry(dlg.body, textvariable=v[k], width=44).grid(row=r, column=1, pady=3)

        def ok():
            try:
                note = max(0, min(100, int(v["note"].get() or 50)))
            except ValueError:
                note = 50
            self.app.lib.update_favorite(name, note=note, sites=v["sites"].get().split(),
                                         postfilter=v["postfilter"].get().strip(),
                                         new_name=" ".join(v["name"].get().split()))
            dlg.destroy()
            self.refresh()
        dlg.buttons("Save", ok)
        dlg.modal()

    def remove(self):
        names = self.tree.selection()
        if names and messagebox.askyesno("Favorites", f"Remove {len(names)} favorite(s)?", parent=self.tree):
            for n in names:
                self.app.lib.remove_favorite(n)
            self.refresh()

    def monitor(self):
        name = self._sel()
        if not name:
            return
        f = self.app.lib.favorite(name)
        from .app_ui import MonitorDialog
        mon = next((m for m in self.app.lib.monitors() if m["search"] == name), None)
        MonitorDialog(self.tree, self.app, mon or {"search": name, "sites": f["sites"] or self.app.st.get("sites"),
                                                   "postfilter": f["postfilter"]}).modal()
        self.app.monitors.refresh()
        self.refresh()

    def _menu(self, e):
        iid = self.tree.identify_row(e.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        m = tk.Menu(self.app.root, tearoff=False)
        m.add_command(label="Open", command=self.open)
        m.add_command(label="Open in the background", command=lambda: self.app.new_search_tab(iid, None, focus=False,
                                                                                              run=True))
        m.add_command(label="Edit...", command=self.edit)
        m.add_command(label="Monitor...", command=self.monitor)
        m.add_command(label="Mark as viewed", command=lambda: (self.app.lib.update_favorite(iid, last_viewed=time.time()),
                                                               self.new_counts.pop(iid, None), self.refresh()))
        m.add_command(label="Download everything...", command=lambda: self._get_all(iid))
        m.add_separator()
        m.add_command(label="Remove", command=self.remove)
        m.tk_popup(e.x_root, e.y_root)

    def _get_all(self, name):
        f = self.app.lib.favorite(name) or {}
        from .app_ui import GroupDialog
        d = GroupDialog(self.tree, self.app, name, f.get("sites") or self.app.st.get("sites"),
                        f.get("postfilter", "")).modal()
        if d:
            self.app.add_group(**d)

    def check_new(self):
        favs = self.app.lib.favorites()
        if not favs:
            return
        self.msg.set(f"Checking {len(favs)} favorite(s)...")
        from .grab import search as S
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
            self.msg.set("Checked " + datetime.now().strftime("%H:%M") + ".")
            self.refresh()
        app.worker.submit(work, on_done=done, on_error=lambda e: self.msg.set(f"Check failed: {e}"))

    def download_new(self):
        names = self.tree.selection() or [n for n, v in self.new_counts.items() if v]
        if not names:
            self.msg.set("Nothing new (press 'Check for new pictures' first, or select favorites).")
            return
        from datetime import timezone
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

    def _later_sel(self):
        s = self.later.curselection()
        return self.later.get(s[0]) if s else None

    def open_later(self):
        t = self._later_sel()
        if t:
            self.app.new_search_tab(t, None, run=True)
            self.app.nb.select(self.app.frames["browse"])

    def remove_later(self):
        t = self._later_sel()
        if t:
            self.app.lib.remove_later(t)
            self.refresh()

    def later_to_fav(self):
        t = self._later_sel()
        if t:
            self.app.lib.add_favorite(t, self.app.st.get("sites") or [])
            self.app.lib.remove_later(t)
            self.refresh()


def _every(minutes) -> str:
    m = int(minutes or 0)
    if m % 1440 == 0:
        return f"{m // 1440} day" + ("s" if m // 1440 > 1 else "")
    if m % 60 == 0:
        return f"{m // 60} h"
    return f"{m} min"


# =====================================================================================================
class MonitorsPage:
    def __init__(self, parent, app):
        self.app = app
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        bar = ttk.Frame(parent, padding=(8, 8, 8, 4))
        bar.grid(row=0, column=0, sticky="ew")
        ttk.Button(bar, text="Add...", command=self.add).pack(side="left")
        ttk.Button(bar, text="Edit...", command=self.edit).pack(side="left", padx=4)
        ttk.Button(bar, text="Remove", command=self.remove).pack(side="left")
        ttk.Button(bar, text="Check now", command=self.check_now).pack(side="left", padx=(12, 4))
        ttk.Button(bar, text="Check all", command=lambda: self.app.scheduler.check_now()).pack(side="left")
        ttk.Button(bar, text="Mark as seen", command=self.mark_seen).pack(side="left", padx=4)
        ttk.Button(bar, text="On / off", command=self.toggle).pack(side="left")
        self.msg = tk.StringVar()
        ttk.Label(bar, textvariable=self.msg, style="Muted.TLabel").pack(side="left", padx=12)
        help_button(bar, app, "favorites")
        f, self.tree = _tree(parent, ("search", "sites", "every", "last", "next", "new", "notify", "download", "error"),
                             ("Search", "Sites", "Every", "Last check", "Next check", "New", "Notify", "Download",
                              "Problem"), (220, 180, 70, 100, 100, 50, 60, 80, 220))
        f.grid(row=1, column=0, sticky="nsew", padx=8)
        self.empty = empty_note(self.tree, "No monitors yet.\n\nA monitor checks a search by itself every few hours "
                                "and tells you (or downloads) what's new. Add one with 'Add...', or with "
                                "'Monitor...' in a Browse tab.")
        self.tree.bind("<Double-1>", lambda e: self.open())
        self.tree.bind("<Button-3>", self._menu)
        ttk.Label(parent, text="Monitors are checked while Packrat is open (Settings > Monitoring). To check them "
                  "while it's closed, schedule:  packrat monitors --run-due   (Tools tab: 'Schedule monitors').",
                  style="Muted.TLabel", padding=(8, 4)).grid(row=2, column=0, sticky="w")
        self.refresh()
        app.root.after(30000, self._tick)

    def _tick(self):
        if not self.app.closing:
            self.refresh()
            self.app.root.after(30000, self._tick)

    def themed(self, p):
        self.tree.tag_configure("new", foreground=p["ok"])
        self.tree.tag_configure("off", foreground=p["muted"])
        self.tree.tag_configure("err", foreground=p["error"])

    def refresh(self):
        sel = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for m in self.app.lib.monitors():
            tags = ("off",) if not m["active"] else ("err",) if m["last_error"] else ("new",) if m["new_count"] else ()
            self.tree.insert("", "end", iid=str(m["id"]), tags=tags, values=(
                m["search"] or "(everything)", " ".join(m["sites"]), _every(m["interval_min"]), _ago(m["last_check"]),
                _ago(m["next_check"]) if m["active"] else "off", m["new_count"] or "", "yes" if m["notify"] else "",
                "yes" if m["download"] else "", uc.friendly(m["last_error"]) if m["last_error"] else ""))
        for i in sel:
            if self.tree.exists(i):
                self.tree.selection_add(i)
        self.themed(COLORS)
        apply_sort(self.tree)
        self.empty.update_note()

    def _ids(self):
        return [int(i) for i in self.tree.selection()]

    def add(self):
        from .app_ui import MonitorDialog
        tab = self.app.browse.current()
        MonitorDialog(self.tree, self.app, {"search": tab.query.get() if tab else "",
                                            "sites": tab.sites if tab else self.app.st.get("sites")}).modal()
        self.refresh()

    def edit(self):
        ids = self._ids()
        if len(ids) == 1:
            from .app_ui import MonitorDialog
            MonitorDialog(self.tree, self.app, self.app.lib.monitor(ids[0])).modal()
            self.refresh()

    def remove(self):
        ids = self._ids()
        if ids and messagebox.askyesno("Monitors", f"Remove {len(ids)} monitor(s)?", parent=self.tree):
            for i in ids:
                self.app.lib.remove_monitor(i)
            self.refresh()

    def check_now(self):
        ids = self._ids()
        if ids:
            self.app.scheduler.check_now(ids)
            self.msg.set("Checking...")

    def mark_seen(self):
        for i in self._ids() or [int(x) for x in self.tree.get_children()]:
            self.app.lib.update_monitor(i, new_count=0)
        self.refresh()

    def toggle(self):
        for i in self._ids():
            m = self.app.lib.monitor(i)
            self.app.lib.update_monitor(i, active=not m["active"])
        self.refresh()

    def open(self):
        ids = self._ids()
        if ids:
            m = self.app.lib.monitor(ids[0])
            self.app.lib.update_monitor(ids[0], new_count=0)
            self.app.new_search_tab(m["search"], m["sites"], run=True)
            self.app.nb.select(self.app.frames["browse"])
            self.refresh()

    def _menu(self, e):
        iid = self.tree.identify_row(e.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        m = tk.Menu(self.app.root, tearoff=False)
        m.add_command(label="Open the search", command=self.open)
        m.add_command(label="Check now", command=self.check_now)
        m.add_command(label="Edit...", command=self.edit)
        m.add_command(label="Mark as seen", command=self.mark_seen)
        m.add_command(label="On / off", command=self.toggle)
        m.add_separator()
        m.add_command(label="Remove", command=self.remove)
        m.tk_popup(e.x_root, e.y_root)

    def checking(self, mon):
        self.msg.set(f"Checking '{mon['search'] or '(everything)'}'...")

    def result(self, mon, res):
        n = len(res["new"])
        if res["errors"]:
            self.msg.set(f"'{mon['search']}': {res['errors'][0]}")
        elif res["first"]:
            self.msg.set(f"'{mon['search']}': first check done - new images will be reported from now on")
        else:
            self.msg.set(f"'{mon['search']}': {n} new image(s)" if n else f"'{mon['search']}': nothing new")
        self.refresh()
        if n and mon.get("notify"):
            try:
                from .notify import notify
                notify("Packrat", f"{n} new image(s) for '{mon['search'] or 'a monitor'}'")
            except Exception:  # noqa: BLE001
                pass
        if n and mon.get("download"):
            dl = self.app.downloads
            dl.refresh()
            if not dl.running():
                ids = [it["id"] for it in self.app.lib.batch_items() if it["kind"] == "unique"
                       and it.get("status") != "done" and it["data"].get("from_monitor") == mon["id"]]
                if ids:
                    dl.start(ids=ids)


# =====================================================================================================
class SettingsPage:
    """Every setting of grab/settings.py, generated: a page list on the left, the fields on the right.
    Changes are saved as you type (a second after the last change)."""

    LIST_EDITORS = {
        "Filenames": (("cond", "Condition (tags or a token test)", 30), ("fn", "File name", 30), ("dir", "Folder", 30)),
        "Customs": (("name", "Token name", 16), ("tags", "Tags (space-separated)", 50)),
        "WebServices": (("name", "Name", 16), ("url", "Address ({url} {md5} {id} {page})", 60)),
        "Save/ImageConversions": (("from", "From extension", 10), ("to", "To extension", 10)),
        "Save/MetadataExiftool": (("key", "ExifTool tag (e.g. XMP:Subject)", 28), ("value", "Value (tokens)", 40)),
        "Save/MetadataPropsys": (("key", "Property (e.g. System.Keywords)", 28), ("value", "Value (tokens)", 40)),
        "LogFiles": (("name", "Name", 14), ("locationType", "Where: 0 folder+name, 1 one file, 2 next to the image "
                                                            "(+suffix), 3 next to it (no extension)", 4),
                     ("path", "Folder (0)", 26), ("filename", "File name (0)", 20), ("uniquePath", "File (1)", 30),
                     ("suffix", "Suffix (2)", 10), ("suffixWithoutExtension", "Suffix (3)", 10),
                     ("content", "Line written (tokens)", 50)),
    }

    # The everyday settings, in plain words: the whole Settings tab in the simple view, the first page in the
    # advanced view. (key, label, help)
    BASICS = (
        ("username", "Your user name on e621 / e926 / e6ai",
         "Optional. These sites ask every program to say who is using it. Leave it empty if you have no account."),
        ("Save/path", "Save pictures in",
         "Empty = the 'downloads' folder inside the Packrat folder. The names below can add folders inside it."),
        ("Save/filename", "Name files like this", "Pick one with Presets, or write your own (Help > File names)."),
        ("Save/md5Duplicates", "If you already have a picture",
         "Packrat remembers every picture it saved, by its fingerprint, even when it comes from another site."),
        ("Save/downloadoriginals", "Download full-size pictures",
         "Off: download the smaller version some sites offer. Faster, and takes less space."),
        ("blacklistedtags", "Never show or download pictures with these tags",
         "One per line, e.g.  gore  or  rating:explicit . Several tags on one line: only pictures that have all of "
         "them. Quick way: right-click a tag in Browse > Blacklist."),
        ("hideblacklisted", "Hide those pictures completely",
         "Off: they are shown greyed out, so you can see that something was left out."),
        ("thumbnailSize", "Preview size (pixels)", "How big pictures are in the search results."),
        ("limit", "Pictures per page", "How many pictures each site sends per page (most allow up to 100)."),
        ("start", "When Packrat opens", ""),
        ("confirm_close", "Ask before closing while downloading",
         "Off: downloads are stopped cleanly and the window closes. Start continues next time."),
    )
    BASIC_GROUP = {"username": "You", "Save/path": "Saving pictures", "blacklistedtags": "Pictures you never want",
                   "thumbnailSize": "Looks"}
    BASIC_CHOICES = {
        "Save/md5Duplicates": (("ignore", "Skip it (recommended)"), ("save", "Download it again"),
                               ("copy", "Copy the file you already have")),
    }
    SIMPLE_PAGES = ("Basics", "General", "Saving", "File names", "Blacklist & tags")

    def basics_rows(self):
        from dataclasses import replace

        from .grab.settings import BY_KEY
        rows, group = [], "You"
        for key, label, help_ in self.BASICS:
            s = BY_KEY[key]
            group = self.BASIC_GROUP.get(key, group)
            kw = {"label": label, "help": help_ or s.help, "group": group, "page": "Basics"}
            ch = self.BASIC_CHOICES.get(key)
            if ch and str(self.app.st.get(key)) in [str(c[0]) for c in ch]:
                kw["choices"] = ch        # the full list stays when you picked something only it has
            rows.append(replace(s, **kw))
        return rows

    def page_for(self, name: str):
        """The page that shows a guide's 'Settings > X' in the current view (None: only the advanced view)."""
        if uc.ADVANCED[0]:
            return name
        return "Basics" if name in self.SIMPLE_PAGES else None

    def view_changed(self):
        self._flush()
        cur = self.names[self.current] if 0 <= self.current < len(self.names) else "Basics"
        self.names = ["Basics"] + list(self.pages) if uc.ADVANCED[0] else ["Basics"]
        self.lb.delete(0, "end")
        for n in self.names:
            self.lb.insert("end", "  " + n)
        if uc.ADVANCED[0]:
            self.lb.grid()
            self.simple_note.grid_remove()
        else:
            self.lb.grid_remove()
            self.simple_note.grid()
        self.show_page(cur if cur in self.names else "Basics")

    def __init__(self, parent, app):
        self.app = app
        from .grab.settings import PAGES, SETTINGS
        self.pages = PAGES
        self.names = ["Basics"] + list(PAGES)
        self.shown: dict = {}
        self.settings = SETTINGS
        self.vars: dict[str, tk.Variable] = {}
        self.texts: dict[str, tk.Text] = {}
        self._job = None
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(0, weight=1)
        left = ttk.Frame(parent, padding=(8, 8, 0, 8))
        left.grid(row=0, column=0, sticky="ns")
        left.rowconfigure(1, weight=1)
        self.find = tk.StringVar()
        fe = ttk.Entry(left, textvariable=self.find, width=20)
        fe.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        uc.add_placeholder(fe, "Find a setting...", self.find)
        self.find.trace_add("write", lambda *_: self._find_changed())
        self.lb = tk.Listbox(left, activestyle="none", exportselection=False, borderwidth=0, highlightthickness=0,
                             width=18, font=app.f_bold)
        self.lb.grid(row=1, column=0, sticky="ns")
        for p in self.names:
            self.lb.insert("end", "  " + p)
        self.simple_note = ttk.Label(left, text="Simple view:\nthe everyday\nsettings.\n\nAll 200+ settings:\n"
                                     "tick 'Advanced\noptions' at the\ntop right.", style="Muted.TLabel",
                                     justify="left")
        self.simple_note.grid(row=1, column=0, sticky="nw", pady=(8, 0))
        self.simple_note.grid_remove()
        self.lb.bind("<<ListboxSelect>>", lambda e: self.show(self.lb.curselection()[0]
                                                              if self.lb.curselection() else 0))
        hb = ttk.Button(left, text="? Help", command=lambda: app.open_help("names"))
        hb.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        from .panel_ui import ScrollFrame
        self.sf = ScrollFrame(parent)
        self.sf.grid(row=0, column=1, sticky="nsew", padx=8, pady=8)
        self._wrap = []
        self.sf.canvas.bind("<Configure>", self._rewrap, add="+")
        bottom = ttk.Frame(parent, padding=(8, 0, 8, 8))
        bottom.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.saved = tk.StringVar(value=f"Saved in {self.app.st.path}")
        ttk.Label(bottom, textvariable=self.saved, style="Muted.TLabel").pack(side="left")
        ttk.Button(bottom, text="Reset this page to defaults", command=self.reset_page).pack(side="right")
        ttk.Button(bottom, text="Open the data folder", command=lambda: uc.open_path(str(paths.data_dir()))).pack(
            side="right", padx=6)
        self.current = 0
        self.lb.selection_set(0)
        self.show(0)
        self.themed(COLORS)

    def themed(self, p):
        self.lb.configure(background=p["panel"], foreground=p["fg"], selectbackground=p["select_bg"],
                          selectforeground=p["select_fg"])
        self.sf.canvas.configure(background=p["bg"])
        for t in self.texts.values():
            th.style_text(t, p)

    PAGE_INTRO = {
        "General": "Starting up, your name (some sites require it), and things added to every search.",
        "Sources": "How searches are made. Which sites to search is chosen with the Sites button in Browse.",
        "Saving": "Where pictures are saved and what happens with pictures you already have.",
        "File names": "How saved files and folders are named. Pick a preset or write your own; the example "
                      "shows the result for a sample picture.",
        "Tag tokens": "Details for %artist%, %character%, %copyright%... in file names: what to write when a "
                      "picture has none, or several. Most people never need to change these.",
        "Conversion": "Convert pictures and videos after downloading (some options need FFmpeg or ImageMagick).",
        "Metadata": "Write tags into the saved files (needs ExifTool), or save a .json file next to each picture.",
        "Interface": "How the window looks: thumbnail size, colours of tags, loading more results.",
        "Viewer": "The picture window.",
        "Blacklist & tags": "Pictures with these tags are hidden and never downloaded. One rule per line.",
        "Monitoring": "Monitors check searches by themselves. These are the defaults for new monitors.",
        "Network": "Proxy, time-outs and the reverse image search sites. Site logins are in the site list "
                   "(Browse > Sites button > Log in / settings).",
        "Commands": "For advanced users: run programs or SQL after each download, or copy pictures into your own "
                    "booru.",
    }

    def _wl(self, label, full=False):
        """Register a label whose wrap width follows the window."""
        self._wrap.append((label, full))
        return label

    def _rewrap(self, _e=None):
        w = self.sf.canvas.winfo_width()
        if w < 100:
            return
        self.sf.inner.update_idletasks()
        for lab, full in getattr(self, "_wrap", []):
            try:
                x = lab.winfo_x() if not full else 0
                left = x if x > 0 else (0 if full else uc.px(300))
                lab.configure(wraplength=max(uc.px(200), w - left - uc.px(30)))
            except tk.TclError:
                pass

    def show_page(self, name: str):
        if name in self.names:
            i = self.names.index(name)
            self.lb.selection_clear(0, "end")
            self.lb.selection_set(i)
            self.show(i)

    def _find_changed(self):
        q = self.find.get().strip().lower()
        if not q:
            self.show(self.current)
            return

        def hit(s):
            return q in s.label.lower() or q in s.help.lower() or q in s.group.lower() or q in s.key.lower()
        basics = [s for s in self.basics_rows() if hit(s)]
        done = {s.key for s in basics}
        others = [s for s in self.settings if s.kind != "hidden" and s.key not in done and hit(s)]
        self._render(basics + others, f"Settings matching '{q}'",
                     "" if uc.ADVANCED[0] or not others else "Some of these are normally only in the advanced view.",
                     with_page=True)

    def show(self, idx):
        idx = idx if 0 <= idx < len(self.names) else 0
        self.current = idx
        page = self.names[idx]
        if page == "Basics":
            self._render(self.basics_rows(), "Settings" if not uc.ADVANCED[0] else "Basics",
                         "The everyday settings. Changes are saved as you make them.", basics=True)
            return
        self._render([s for s in self.settings if s.page == page and s.kind != "hidden"], page,
                     self.PAGE_INTRO.get(page, ""))

    def _flush(self):
        if self._job:
            self.app.root.after_cancel(self._job)
            self._save()

    def _render(self, rows, title, intro, with_page=False, basics=False):
        self._flush()     # a change made less than a second ago still gets saved
        inner = self.sf.inner
        for w in inner.winfo_children():
            w.destroy()
        self.vars, self.texts, self.shown = {}, {}, {}
        self._wrap = []
        inner.columnconfigure(1, weight=1)
        ttk.Label(inner, text=title, font=self.app.f_title).grid(row=0, column=0, columnspan=3, sticky="w")
        r = 1
        if intro:
            self._wl(ttk.Label(inner, text=intro, style="Muted.TLabel", justify="left"), full=True).grid(
                row=1, column=0, columnspan=3, sticky="w", pady=(2, 8))
            r = 2
        if basics and not uc.ADVANCED[0]:
            box = ttk.Frame(inner, padding=(0, 4, 0, 8))
            box.grid(row=r, column=0, columnspan=3, sticky="ew", pady=(0, 6))
            self._wl(ttk.Label(box, text="Packrat has over 200 more settings: file name details for each kind of "
                               "tag, conversions, metadata, proxies, commands... They work the same in the simple "
                               "view, they're just out of the way.", justify="left"), full=True).pack(anchor="w")
            ttk.Button(box, text="Show all settings",
                       command=lambda: self.app.set_advanced(True)).pack(anchor="w", pady=(6, 0))
            r += 1
        if not rows:
            ttk.Label(inner, text="No setting matches.").grid(row=r, column=0, sticky="w")
        group = None
        for s in rows:
            g = f"{s.page} > {s.group}" if with_page else s.group
            if g != group:
                group = g
                ttk.Label(inner, text=group, font=self.app.f_bold).grid(row=r, column=0, columnspan=3, sticky="w",
                                                                        pady=(12, 4))
                r += 1
            r = self._field(inner, r, s)
        self.sf.canvas.yview_moveto(0)
        self.themed(COLORS)
        self.sf.inner.after_idle(self._rewrap)

    def _field(self, f, r, s):
        self.shown[s.key] = s
        st = self.app.st
        val = st.get(s.key)
        lab = ttk.Label(f, text=s.label)
        lab.grid(row=r, column=0, sticky="nw", padx=(0, 12), pady=3)
        if s.help:
            uc_tip(lab, s.help)
        if s.kind == "bool":
            v = tk.BooleanVar(value=bool(val))
            w = ttk.Checkbutton(f, variable=v)
            w.grid(row=r, column=1, sticky="w")
        elif s.kind == "choice":
            labels = [c[1] for c in s.choices]
            cur = next((c[1] for c in s.choices if str(c[0]) == str(val)), str(val))
            v = tk.StringVar(value=cur)
            w = ttk.Combobox(f, textvariable=v, values=labels, state="readonly", width=34)
            w.grid(row=r, column=1, sticky="w")
            v._choices = s.choices  # noqa: SLF001
        elif s.kind in ("int", "float"):
            v = tk.StringVar(value=str(val))
            w = ttk.Spinbox(f, textvariable=v, from_=s.lo if s.lo is not None else -10**9,
                            to=s.hi if s.hi is not None else 10**9, width=10,
                            increment=1 if s.kind == "int" else 0.1)
            w.grid(row=r, column=1, sticky="w")
        elif s.kind == "lines":
            t = tk.Text(f, height=5, width=60, wrap="word", relief="flat", borderwidth=1, highlightthickness=1)
            t.insert("1.0", "\n".join(val) if isinstance(val, list) else str(val or ""))
            t.grid(row=r, column=1, sticky="ew", pady=3)
            t.bind("<KeyRelease>", lambda e, k=s.key: self._changed(k))
            th.style_text(t, COLORS)
            self.texts[s.key] = t
            if s.help:
                self._wl(ttk.Label(f, text=s.help, style="Muted.TLabel", justify="left")).grid(
                    row=r + 1, column=1, sticky="w")
                return r + 2
            return r + 1
        elif s.kind == "list":
            return self._list_field(f, r, s)
        elif s.kind == "secret":
            v = tk.StringVar(value=st.secret(s.key))
            w = ttk.Entry(f, textvariable=v, show="•", width=40)
            w.grid(row=r, column=1, sticky="w")
        else:   # text dir file color filename
            v = tk.StringVar(value=str(val if val is not None else ""))
            row = ttk.Frame(f)
            row.grid(row=r, column=1, sticky="ew")
            row.columnconfigure(0, weight=1)
            w = ttk.Entry(row, textvariable=v, width=60 if s.kind in ("filename", "dir", "file") else 40)
            w.grid(row=0, column=0, sticky="ew" if s.kind in ("filename", "dir", "file") else "w")
            if s.kind == "dir":
                ttk.Button(row, text="Browse...", command=lambda v=v: self._pick_dir(v)).grid(row=0, column=1, padx=4)
            elif s.kind == "file":
                ttk.Button(row, text="Browse...", command=lambda v=v: self._pick_file(v)).grid(row=0, column=1, padx=4)
            elif s.kind == "color":
                ttk.Button(row, text="Choose...", command=lambda v=v: self._pick_color(v)).grid(row=0, column=1, padx=4)
            elif s.kind == "filename":
                from .ui_help import PRESETS
                mb = ttk.Menubutton(row, text="Presets")
                menu = tk.Menu(mb, tearoff=False)
                for label, fmt in PRESETS:
                    menu.add_command(label=f"{label}:   {fmt}", command=lambda v=v, fmt=fmt: v.set(fmt))
                mb["menu"] = menu
                mb.grid(row=0, column=1, padx=4)
                uc.tip(mb, "Ready-made file name formats")
                msg = self._wl(ttk.Label(f, text="", style="Muted.TLabel", justify="left"))
                msg.grid(row=r + 1, column=1, columnspan=2, sticky="w")
                v.trace_add("write", lambda *_, v=v, m=msg: self._check_filename(v, m))
                self._check_filename(v, msg)
                v.trace_add("write", lambda *_, k=s.key: self._changed(k))
                self.vars[s.key] = v
                return r + 2
        v.trace_add("write", lambda *_, k=s.key: self._changed(k))
        self.vars[s.key] = v
        if s.help:
            self._wl(ttk.Label(f, text=s.help, style="Muted.TLabel", justify="left")).grid(
                row=r + 1, column=1, columnspan=2, sticky="w", pady=(0, 4))
            return r + 2
        return r + 1

    def _list_field(self, f, r, s):
        st = self.app.st
        if s.key == "Coloring":
            cf = ttk.Frame(f)
            cf.grid(row=r, column=1, sticky="w")
            colors = st.get("Coloring") or {}
            for i, (k, default) in enumerate(sorted(colors.items())):
                b = tk.Button(cf, text=k, width=12, background=default, relief="flat",
                              command=lambda k=k: self._pick_tag_color(k))
                b.grid(row=i // 4, column=i % 4, padx=2, pady=2)
            return r + 1
        cols = self.LIST_EDITORS.get(s.key)
        if not cols:
            return r + 1
        box = ttk.Frame(f)
        box.grid(row=r, column=1, sticky="ew", pady=3)
        box.columnconfigure(0, weight=1)
        tf, tree = _tree(box, tuple(c[0] for c in cols), tuple(c[1].split(" (")[0] for c in cols),
                         tuple(max(60, c[2] * 6) for c in cols), height=4, sortable=False)
        tf.grid(row=0, column=0, sticky="ew")
        items = [x for x in (st.get(s.key) or []) if isinstance(x, dict)]
        if s.key == "WebServices" and not items:
            from .grab.websvc import DEFAULTS
            items = [dict(d) for d in DEFAULTS]

        def fill():
            tree.delete(*tree.get_children())
            for i, it in enumerate(items):
                tree.insert("", "end", iid=str(i), values=tuple(str(it.get(c[0], "")) for c in cols))

        def save():
            st.set(s.key, list(items))
            self.saved.set("Saved " + datetime.now().strftime("%H:%M:%S"))

        def edit(i=None):
            cur = items[i] if i is not None else {}
            dlg = uc.Dialog(tree, s.label)
            vs = {}
            for rr, (k, lab, w) in enumerate(cols):
                ttk.Label(dlg.body, text=lab, wraplength=uc.px(260), justify="left").grid(row=rr, column=0, sticky="w",
                                                                                   padx=(0, 10), pady=3)
                vs[k] = tk.StringVar(value=str(cur.get(k, "")))
                ttk.Entry(dlg.body, textvariable=vs[k], width=max(20, w)).grid(row=rr, column=1, sticky="w", pady=3)

            def ok():
                d = {k: v.get() for k, v in vs.items()}
                if "locationType" in d:
                    try:
                        d["locationType"] = int(d["locationType"] or 0)
                    except ValueError:
                        d["locationType"] = 0
                if i is None:
                    items.append(d)
                else:
                    items[i] = d
                dlg.destroy()
                fill()
                save()
            dlg.buttons("OK", ok)
            dlg.modal()

        def sel():
            x = tree.selection()
            return int(x[0]) if x else None

        def remove():
            i = sel()
            if i is not None:
                items.pop(i)
                fill()
                save()

        def up(d):
            i = sel()
            if i is not None and 0 <= i + d < len(items):
                items[i], items[i + d] = items[i + d], items[i]
                fill()
                tree.selection_set(str(i + d))
                save()
        bf = ttk.Frame(box)
        bf.grid(row=0, column=1, sticky="n", padx=4)
        ttk.Button(bf, text="Add...", command=edit).pack(fill="x")
        ttk.Button(bf, text="Edit...", command=lambda: edit(sel()) if sel() is not None else None).pack(fill="x",
                                                                                                    pady=2)
        ttk.Button(bf, text="Remove", command=remove).pack(fill="x")
        ttk.Button(bf, text="▲", command=lambda: up(-1)).pack(fill="x", pady=(6, 0))
        ttk.Button(bf, text="▼", command=lambda: up(1)).pack(fill="x")
        tree.bind("<Double-1>", lambda e: edit(sel()) if sel() is not None else None)
        fill()
        if s.help:
            self._wl(ttk.Label(f, text=s.help, style="Muted.TLabel", justify="left")).grid(
                row=r + 1, column=1, sticky="w")
            return r + 2
        return r + 1

    def _check_filename(self, v, lbl):
        from .ui_help import sample_example
        txt = v.get()
        if not txt:
            lbl.configure(text="Empty: the general file name is used.", foreground=COLORS["muted"])
            return
        path, problem = sample_example(txt, self.app.st, self.app.st.save_path())
        if not path:
            lbl.configure(text="Problem: " + (problem or "this format gives no file name"),
                          foreground=COLORS["error"])
            return
        lbl.configure(text=f"Example (Pikachu by some_artist, from e621):  {path}" + (
            f"\nNote: {problem}" if problem else ""), foreground=COLORS["warn"] if problem else COLORS["ok"])

    def _pick_dir(self, v):
        p = filedialog.askdirectory(parent=self.lb, initialdir=v.get() or None)
        if p:
            v.set(p)

    def _pick_file(self, v):
        p = filedialog.askopenfilename(parent=self.lb)
        if p:
            v.set(p)

    def _pick_color(self, v):
        c = colorchooser.askcolor(color=v.get() or None, parent=self.lb)
        if c and c[1]:
            v.set(c[1])

    def _pick_tag_color(self, k):
        colors = self.app.st.get("Coloring") or {}
        c = colorchooser.askcolor(color=colors.get(k), parent=self.lb)
        if c and c[1]:
            colors[k] = c[1]
            self.app.st.set("Coloring", colors)
            self.show(self.current)
            self.app.browse.themed(COLORS)

    def _changed(self, key):
        self._pending = getattr(self, "_pending", set())
        self._pending.add(key)
        if self._job:
            self.app.root.after_cancel(self._job)
        self._job = self.app.root.after(700, self._save)

    def _save(self):
        self._job = None
        from .grab.settings import BY_KEY
        vals = {}
        for key in getattr(self, "_pending", set()):
            s = self.shown.get(key) or BY_KEY.get(key)
            if s is None:
                continue
            if key in self.texts:
                vals[key] = self.texts[key].get("1.0", "end").strip()
                continue
            v = self.vars.get(key)
            if v is None:
                continue
            raw = v.get()
            if s.kind == "bool":
                vals[key] = bool(raw)
            elif s.kind == "choice":
                vals[key] = next((c[0] for c in s.choices if c[1] == raw), raw)
            elif s.kind == "int":
                try:
                    n = int(float(raw))
                except ValueError:
                    continue
                if s.lo is not None:
                    n = max(int(s.lo), n)
                if s.hi is not None:
                    n = min(int(s.hi), n)
                vals[key] = n
            elif s.kind == "float":
                try:
                    vals[key] = float(raw)
                except ValueError:
                    continue
            else:
                vals[key] = raw
        self._pending = set()
        if vals:
            self.app.st.update(vals)
            self.saved.set("Saved " + datetime.now().strftime("%H:%M:%S"))
            if "thumbnailSize" in vals or "resizeInsteadOfCropping" in vals:
                self.app.thumbs.size = int(self.app.st.get("thumbnailSize") or 150)
                self.app.thumbs.crop = not bool(self.app.st.get("resizeInsteadOfCropping", True))
                self.app.thumbs.mem.clear()
                for t in self.app.browse.tabs:
                    t.photos = {}
                    t._relayout()
                    for i in range(len(t.items)):
                        t._load_thumb(i)

    def reset_page(self):
        page = self.names[self.current]
        keys = ([k for k, _, _ in self.BASICS] if page == "Basics" else
                [s.key for s in self.settings if s.page == page])
        if messagebox.askyesno("Settings", f"Put every setting of '{page}' back to its default?", parent=self.lb):
            self.app.st.reset(keys)
            self.show(self.current)


def uc_tip(widget, text):
    from .panel_ui import Tooltip
    Tooltip(widget, lambda: text)


# =====================================================================================================
class ToolsPage:
    def __init__(self, parent, app):
        self.app = app
        self.cancel = threading.Event()
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        top = ttk.Frame(parent, padding=8)
        top.grid(row=0, column=0, sticky="ew")
        tools = (
            ("Import from Grabber...", self.import_grabber, "Settings, favorites, monitors, MD5 list, logins and the "
             "download list of a Grabber installation. Grabber is not changed."),
            ("Rename existing files...", self.rename, "Move files you already have to a new file name format "
             "(looks each one up on the site; shows the list before moving anything)."),
            ("MD5 list fixer...", self.md5_fixer, "Add every file of a folder to the MD5 list, so duplicates are "
             "recognised even if they were saved by another program."),
            ("Blacklist fixer...", self.blacklist_fixer, "Find saved files whose tags match your blacklist."),
            ("Empty folders...", self.empty_dirs, "Find and remove empty folders."),
            ("Tag loader...", self.tag_loader, "Download a site's tag list once, so tag types (artist, character "
             "...) are known without extra requests."),
            ("Source registries...", self.registries, "Install or update extra sources (new sites) from Grabber's "
             "official source registry or another one."),
            ("Schedule monitors...", self.schedule, "Check monitors with Windows Task Scheduler while Packrat is "
             "closed."),
            ("Back up the library", self.backup, "Copy favorites, monitors, MD5 list and the download list "
             "(library.sqlite3) to a dated backup file."),
        )
        top.columnconfigure(1, weight=1)
        descs = []
        for i, (label, fn, help_) in enumerate(tools):
            ttk.Button(top, text=label, command=fn, width=26).grid(row=i, column=0, sticky="w", pady=2)
            d = ttk.Label(top, text=help_, style="Muted.TLabel", wraplength=uc.px(600), justify="left")
            d.grid(row=i, column=1, sticky="w", padx=10)
            descs.append(d)

        def rewrap(e=None):
            w = max(uc.px(200), min(uc.px(700), top.winfo_width() - uc.px(340)))
            for d in descs:
                d.configure(wraplength=w)
        top.bind("<Configure>", rewrap, add="+")
        ttk.Button(top, text="? Help", command=lambda: app.open_help("tools")).grid(row=0, column=2, sticky="ne")
        of = ttk.Frame(parent, padding=(8, 0, 8, 8))
        of.grid(row=1, column=0, sticky="nsew")
        of.columnconfigure(0, weight=1)
        of.rowconfigure(1, weight=1)
        hb = ttk.Frame(of)
        hb.grid(row=0, column=0, sticky="ew")
        self.prog = tk.StringVar()
        ttk.Label(hb, textvariable=self.prog, font=app.f_bold).pack(side="left")
        self.cancel_btn = ttk.Button(hb, text="Cancel", command=self.cancel.set)
        self.cancel_btn.pack(side="right")
        self.cancel_btn.state(["disabled"])
        self.out = tk.Text(of, height=12, wrap="none", relief="flat", font=app.f_mono)
        self.out.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(of, orient="vertical", command=self.out.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self.out.configure(yscrollcommand=sb.set)
        self.themed(COLORS)

    def themed(self, p):
        th.style_text(self.out, p, readonly_bg=True)

    def say(self, text):
        self.out.insert("end", text + "\n")
        self.out.see("end")

    def _run(self, title, fn, done):
        self.cancel = threading.Event()
        self.cancel_btn.configure(command=self.cancel.set)
        self.prog.set(title + "...")
        self.say(f"--- {title} ---")
        self.cancel_btn.state(["!disabled"])

        def ok(r):
            self.cancel_btn.state(["disabled"])
            self.prog.set(title + (": cancelled" if self.cancel.is_set() else ": done"))
            done(r)

        def bad(e):
            self.cancel_btn.state(["disabled"])
            self.prog.set(title + ": failed")
            self.say(f"Error: {e}")
        self.app.worker.submit(fn, on_done=ok, on_error=bad)

    def _progress(self, n, what):
        self.app.worker.call(self.prog.set, f"{n}: {str(what)[-90:]}")

    def _folder(self, title):
        return filedialog.askdirectory(parent=self.out, title=title, initialdir=self.app.st.save_path())

    def _site(self, title):
        hosts = [s.host for s in self.app.store.all_sites()]
        dlg = uc.Dialog(self.out, title)
        ttk.Label(dlg.body, text="Site the files come from").grid(row=0, column=0, sticky="w")
        v = tk.StringVar(value=(self.app.st.get("sites") or [hosts[0]])[0])
        ttk.Combobox(dlg.body, textvariable=v, values=hosts, width=36).grid(row=0, column=1, padx=8)
        res = {}
        dlg.buttons("OK", lambda: (res.setdefault("h", v.get()), dlg.destroy()))
        dlg.modal()
        return res.get("h")

    # ---- tools ----------------------------------------------------------------------------------
    def import_grabber(self, folder=None):
        from .grab import grabber_import as G
        if not folder:
            found = G.find_profiles()
            folder = filedialog.askdirectory(parent=self.out, title="Grabber's settings folder (has settings.ini)",
                                             initialdir=str(found[0]) if found else None)
        if not folder:
            return
        if not G.is_profile(folder):
            messagebox.showerror("Import", "This folder has no Grabber settings (settings.ini, favorites.json...).",
                                 parent=self.out)
            return
        summ = G.summary(folder)
        dlg = uc.Dialog(self.out, "Import from Grabber")
        ttk.Label(dlg.body, text=f"From {folder}", font=self.app.f_bold).grid(row=0, column=0, columnspan=2,
                                                                              sticky="w")
        labels = {"settings": "Settings" + ("" if summ["settings"] else " (none)"),
                  "favorites": f"Favorites ({summ['favorites']})", "monitors": f"Monitors ({summ['monitors']})",
                  "later": f"Kept for later ({summ['later']})", "blacklist": "Blacklist and ignored tags",
                  "words": "Auto-complete words", "md5": f"MD5 list ({summ['md5']:,})",
                  "downloads": f"Download list ({summ['downloads']})", "sites": f"Sites and logins ({summ['sites']})"}
        vs = {}
        for i, k in enumerate(G.PARTS):
            vs[k] = tk.BooleanVar(value=True)
            ttk.Checkbutton(dlg.body, text=labels[k], variable=vs[k]).grid(row=1 + i, column=0, sticky="w")
        ttk.Label(dlg.body, text="Importing twice is safe: what already exists is kept.", style="Muted.TLabel").grid(
            row=20, column=0, sticky="w", pady=(8, 0))
        res = {}
        dlg.buttons("Import", lambda: (res.setdefault("parts", [k for k, v in vs.items() if v.get()]), dlg.destroy()))
        dlg.modal()
        if not res.get("parts"):
            return
        a = self.app

        def done(rep):
            self.say("Imported: " + ", ".join(f"{k}: {v}" for k, v in rep.items() if k != "notes"))
            for n in rep["notes"]:
                self.say("  note: " + n)
            a.favorites.refresh()
            a.monitors.refresh()
            a.downloads.refresh()
            a.settings_page.show(a.settings_page.current)
        self._run("Import from Grabber", lambda: G.import_profile(folder, a.st, a.lib, a.store, tuple(res["parts"]),
                                                                  progress=lambda s: self._progress("importing", s)),
                  done)

    def md5_fixer(self):
        folder = self._folder("Folder to add to the MD5 list")
        if not folder:
            return
        from .grab import tools as T
        self._run("MD5 list fixer", lambda: T.md5_fixer(self.app.lib, folder, progress=self._progress,
                                                        cancel=self.cancel),
                  lambda r: self.say(f"{r['files']} files looked at, {r['added']} added to the MD5 list."))

    def empty_dirs(self):
        folder = self._folder("Folder to clean")
        if not folder:
            return
        from .grab import tools as T

        def done(dirs):
            for d in dirs[:500]:
                self.say(d)
            if not dirs:
                self.say("No empty folders.")
                return
            if messagebox.askyesno("Empty folders", f"Remove these {len(dirs)} empty folder(s)?", parent=self.out):
                self.say(f"{T.remove_dirs(dirs)} folder(s) removed.")
        self._run("Empty folders", lambda: T.empty_dirs(folder), done)

    def rename(self):
        folder = self._folder("Folder with the files to rename")
        if not folder:
            return
        site = self._site("Rename existing files")
        if not site:
            return
        fmt = simpledialog.askstring("Rename existing files", "New file name format:", parent=self.out,
                                     initialvalue=self.app.st.filename())
        if not fmt:
            return
        from .grab import tools as T
        a = self.app

        def done(plan):
            moving = [r for r in plan if r["status"] == "rename"]
            for r in plan[:1000]:
                self.say(f"{r['from']}  ->  {r['to']}" if r["status"] == "rename" else f"{r['from']}  ({r['status']})")
            if moving and messagebox.askyesno("Rename", f"Move {len(moving)} file(s) as listed?", parent=self.out):
                res = T.apply_renames(a.lib, plan)
                self.say(f"{res['moved']} moved, {res['skipped']} unchanged.")
        self._run("Rename existing files", lambda: T.rename_existing_plan(a.store, a.st, a.lib, folder, site, fmt,
                                                                          progress=self._progress, cancel=self.cancel),
                  done)

    def blacklist_fixer(self):
        folder = self._folder("Folder to check against the blacklist")
        if not folder:
            return
        site = self._site("Blacklist fixer")
        if not site:
            return
        from .grab import tools as T
        a = self.app

        def done(hits):
            for h in hits[:1000]:
                self.say(f"{h['path']}   ({h['rule']})")
            if not hits:
                self.say("No saved file matches the blacklist.")
                return
            if messagebox.askyesno("Blacklist fixer", f"Delete these {len(hits)} file(s)?", icon="warning",
                                   parent=self.out):
                n = 0
                for h in hits:
                    try:
                        os.remove(h["path"])
                        a.lib.md5_remove(h["md5"])
                        n += 1
                    except OSError as e:
                        self.say(f"{h['path']}: {e}")
                self.say(f"{n} file(s) deleted.")
        self._run("Blacklist fixer", lambda: T.blacklist_fixer(a.store, a.st, a.lib, folder, site,
                                                               progress=self._progress, cancel=self.cancel), done)

    def tag_loader(self):
        host = self._site("Tag loader")
        site = self.app.store.get(host) if host else None
        if site is None:
            return
        from .grab import tools as T
        self._run(f"Tag loader ({site.name})", lambda: T.tag_loader(site, progress=lambda p, n: self._progress(
            "tags", n), cancel=self.cancel), lambda r: self.say(f"{r['tags']:,} tags loaded from the {r['source']}."))

    def registries(self):
        RegistryDialog(self.out, self.app)

    def schedule(self):
        exe = cp.child_python()
        cmd = f'"{exe}" -m packrat monitors --run-due'
        script = paths.install_root() / "app" / "install_daily_task.ps1"
        self.say("To check monitors while Packrat is closed, run this every hour (Task Scheduler):")
        self.say("  " + cmd)
        self.say(f"  (working folder: {paths.install_root() / 'app'})")
        if os.name == "nt" and messagebox.askyesno(
                "Schedule monitors", "Create an hourly Windows scheduled task for the monitors now?\n\n"
                "Task name: Packrat monitors", parent=self.out):
            import subprocess
            r = subprocess.run(["schtasks", "/Create", "/F", "/SC", "HOURLY", "/TN", "Packrat monitors", "/TR",
                                f'cmd /c cd /d "{paths.install_root() / "app"}" && {cmd}'],
                               capture_output=True, text=True)
            self.say((r.stdout or r.stderr).strip() or "done")
        _ = script

    def backup(self):
        dest = paths.data_dir() / "backups" / f"library-{datetime.now():%Y%m%d-%H%M%S}.sqlite3"
        p = self.app.lib.backup_to(dest)
        self.say(f"Library backed up to {p}")


class RegistryDialog(uc.Dialog):
    def __init__(self, master, app):
        super().__init__(master, "Source registries")
        self.app = app
        from .grab import registry as R
        self.R = R
        top = ttk.Frame(self.body)
        top.pack(fill="x")
        st = R.load_state()
        self.urls = [R.OFFICIAL] + [u for u in st["registries"] if u != R.OFFICIAL]
        self.url = tk.StringVar(value=self.urls[0])
        ttk.Label(top, text="Registry address").pack(side="left", padx=(0, 6))
        self.url_cb = ttk.Combobox(top, textvariable=self.url, values=self.urls, width=62)
        self.url_cb.pack(side="left")
        ttk.Button(top, text="Load", command=self.load).pack(side="left", padx=6)
        fb = ttk.Button(top, text="Forget", command=self.forget)
        fb.pack(side="left")
        uc.tip(fb, "Remove this registry from the list (sources you installed from it stay)")
        f, self.tree = _tree(self.body, ("name", "status", "sites", "change"), ("Source", "Status", "Sites",
                                                                               "Last change"), (170, 120, 230, 260))
        f.pack(fill="both", expand=True, pady=8)
        bf = ttk.Frame(self.body)
        bf.pack(fill="x")
        ttk.Button(bf, text="Install / update", style="Accent.TButton", command=self.install).pack(side="left")
        ttk.Button(bf, text="Uninstall", command=self.uninstall).pack(side="left", padx=6)
        self.msg = ttk.Label(bf, text="", style="Muted.TLabel")
        self.msg.pack(side="left", padx=8)
        self.entries = []
        self.geometry(uc.size_for(self, uc.px(900), uc.px(480)))
        self.load()

    def forget(self):
        url = self.url.get().strip()
        if url == self.R.OFFICIAL:
            self.msg.configure(text="The official registry is always in the list.")
            return
        self.R.remove_registry(url)
        self.urls = [u for u in self.urls if u != url]
        self.url_cb.configure(values=self.urls)
        self.url.set(self.R.OFFICIAL)
        self.load()

    def load(self):
        url = self.url.get().strip()
        self.msg.configure(text="Loading...")

        def done(reg):
            st = self.R.load_state()
            if url not in st["registries"] and url != self.R.OFFICIAL:
                st["registries"].append(url)
                self.R.save_state(st)
            if url not in self.urls:
                self.urls.append(url)
                self.url_cb.configure(values=self.urls)
            self.entries = self.R.available([reg])
            self.tree.delete(*self.tree.get_children())
            for i, e in enumerate(self.entries):
                self.tree.insert("", "end", iid=str(i), values=(e["name"], e["status"], ", ".join(e["default_sites"][:4]),
                                                                f"{e['date'][:10]} {e['commit']}"))
            self.msg.configure(text=f"{reg.get('name')}: {len(self.entries)} sources")
        self.app.worker.submit(self.R.fetch, url, on_done=done, on_error=lambda e: self.msg.configure(text=str(e)))

    def _sel(self):
        s = self.tree.selection()
        return self.entries[int(s[0])] if s else None

    def install(self, accept=False):
        e = self._sel()
        if not e:
            return
        self.msg.configure(text=f"Installing {e['name']}...")

        def done(p):
            self.app.store.reload()
            self.msg.configure(text=f"{e['name']} installed. Its sites are in Browse > Sources.")
            self.load()

        def err(ex):
            if isinstance(ex, self.R.ChecksumMismatch) and messagebox.askyesno("Install", f"{ex}\n\nInstall anyway?",
                                                                                parent=self):
                self.app.worker.submit(self.R.install, e, True, on_done=done,
                                       on_error=lambda x: self.msg.configure(text=str(x)))
                return
            self.msg.configure(text=str(ex))
        self.app.worker.submit(self.R.install, e, accept, on_done=done, on_error=err)

    def uninstall(self):
        e = self._sel()
        if e and self.R.uninstall(e["name"]):
            self.app.store.reload()
            self.msg.configure(text=f"{e['name']} removed.")
            self.load()


# =====================================================================================================
class LogPage:
    def __init__(self, parent, app):
        self.app = app
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        bar = ttk.Frame(parent, padding=(8, 8, 8, 4))
        bar.grid(row=0, column=0, sticky="ew")
        ttk.Button(bar, text="Clear", command=lambda: self.text.delete("1.0", "end")).pack(side="left")
        ttk.Button(bar, text="Copy all", command=self.copy).pack(side="left", padx=4)
        ttk.Button(bar, text="Open the logs folder", command=lambda: uc.open_path(str(paths.logs_dir()))).pack(
            side="left")
        help_button(bar, app, "problems")
        self.text = tk.Text(parent, wrap="word", relief="flat", font=app.f_mono)
        self.text.grid(row=1, column=0, sticky="nsew", padx=(8, 0), pady=(0, 8))
        sb = ttk.Scrollbar(parent, orient="vertical", command=self.text.yview)
        sb.grid(row=1, column=1, sticky="ns", pady=(0, 8), padx=(0, 8))
        self.text.configure(yscrollcommand=sb.set)
        self.themed(COLORS)

    def themed(self, p):
        th.style_text(self.text, p, readonly_bg=True)
        self.text.tag_configure("warn", foreground=p["warn"])
        self.text.tag_configure("err", foreground=p["error"])

    def add(self, line, level=logging.INFO):
        tag = "err" if level >= logging.ERROR else "warn" if level >= logging.WARNING else ""
        at_end = self.text.yview()[1] > 0.98
        self.text.insert("end", line + "\n", (tag,) if tag else ())
        n = int(self.text.index("end-1c").split(".")[0])
        if n > 5000:
            self.text.delete("1.0", f"{n - 4000}.0")
        if at_end:
            self.text.see("end")

    def copy(self):
        self.app.root.clipboard_clear()
        self.app.root.clipboard_append(self.text.get("1.0", "end"))
