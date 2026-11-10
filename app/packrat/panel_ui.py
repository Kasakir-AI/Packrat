"""Control panel window (1.4, sign-in in 1.5). Tk only; the logic lives in control_panel.py.

Layout (works from 980x640 up; default about 1220x780 scaled by the screen DPI):
  header:  title, status
  left:    notebook  Basics | Download order & filters | Naming & folders | Advanced  (each scrolls)
           help bar (what the option under the mouse does)
  right:   status message, progress (overall, tier, current file, details), log pane
  bottom:  command preview (copyable), buttons  Save settings | Reset | Retry failed files ... Pause | Stop |
           Start, and below them Open archive folder | Open log | Open failure log | Open latest report
1.9: Light / Dark / Follow Windows theme (header switch, saved in [gui]), whole-archive dashboard, resume
banner, desktop notifications, low-disk pause with Resume, Open failure log.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

from . import __version__
from . import control_panel as cp
from . import notify as notifier
from . import theme as th
from .client import build_user_agent
from .gui import GuiModel, _bmp_safe, fmt_bytes
from .redact import redact
from .report import latest_report

POLL_MS = 100
DEFAULT_SIZE = (1280, 860)  # 2.0: at 96 dpi; scaled by the display DPI and capped at the screen size
MIN_SIZE = (980, 640)
LOG_MIN_LINES = 8          # 2.0: the log pane never gets smaller than this (the status boxes scroll instead)
DASH_TWO_COLUMNS_PX = 470  # 2.0: narrower 'Whole archive' boxes show the figures in one column
MONO_FAMILIES = ("Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Menlo", "Liberation Mono")
REFRESH_MS = 250
LOG_MAX_LINES = 3000
COLORS = dict(th.LIGHT)   # 1.9: the active palette (updated in place when the theme changes)
THEME_POLL_MS = 3000      # 'Follow Windows': re-read the setting this often
NOTIFY_MIN_S = 60         # no desktop notification for short local runs (preview, stats ...)
RATING_BOXES = (("s", "Safe"), ("q", "Questionable"), ("e", "Explicit"))


def mono_family(root, fallback: str) -> str:
    """2.0: a readable monospace family that exists on this system (Consolas on Windows)."""
    try:
        have = {f.lower(): f for f in tkfont.families(root)}
    except tk.TclError:
        return fallback
    for fam in MONO_FAMILIES:
        if fam.lower() in have:
            return have[fam.lower()]
    return fallback


class Tooltip:
    def __init__(self, widget, text_fn, delay=600):
        self.w, self.text_fn, self.delay, self.tip, self.job = widget, text_fn, delay, None, None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        self.job = self.w.after(self.delay, self._show)

    def _cancel(self):
        if self.job:
            self.w.after_cancel(self.job)
            self.job = None

    def _show(self):
        text = self.text_fn()
        if not text or self.tip:
            return
        x, y = self.w.winfo_rootx() + 16, self.w.winfo_rooty() + self.w.winfo_height() + 4
        self.tip = tk.Toplevel(self.w)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        self.tip.configure(background=COLORS["border"])
        tk.Label(self.tip, text=text, justify="left", background=COLORS["tip_bg"], foreground=COLORS["tip_fg"],
                 relief="flat", borderwidth=0, wraplength=420, padx=6, pady=4).pack(padx=1, pady=1)

    def _hide(self, _e=None):
        self._cancel()
        if self.tip:
            self.tip.destroy()
            self.tip = None


class ScrollFrame(ttk.Frame):
    """A frame whose content scrolls vertically (mouse wheel while the pointer is over it)."""

    def __init__(self, master, padding=(8, 6, 8, 8)):
        super().__init__(master)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, background=COLORS["bg"])
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas, padding=padding)
        self.win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vsb.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.win, width=e.width))
        self.bind("<Enter>", self._bind_wheel)
        self.bind("<Leave>", self._unbind_wheel)

    def _bind_wheel(self, _e=None):
        self.bind_all("<MouseWheel>", self._wheel)
        self.bind_all("<Button-4>", self._wheel)
        self.bind_all("<Button-5>", self._wheel)

    def _unbind_wheel(self, _e=None):
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.unbind_all(seq)

    def _wheel(self, e):
        if self.inner.winfo_height() <= self.canvas.winfo_height():
            return
        n = -1 if (getattr(e, "num", 0) == 4 or getattr(e, "delta", 0) > 0) else 1
        self.canvas.yview_scroll(n * (1 if sys.platform == "darwin" else 3), "units")


class ControlPanel:
    def __init__(self, root: tk.Tk, settings_path: Path, prefill: dict | None = None, runner=None,
                 container=None, on_closed=None):
        self.root = root
        # 3.0: Packrat's main window shows this panel as its "Site archive" tab: widgets go into `container`,
        # the main window owns the title, the theme buttons, closing and the window size.
        self.host = container if container is not None else root
        self.embedded = container is not None
        self.on_closed = on_closed
        self.on_theme = None   # called after a theme change (the main window recolours its own parts)
        self.settings_path = Path(settings_path)
        self.runner = runner or cp.CliRunner()
        self.model = GuiModel()
        self.tk_version = float(root.tk.call("info", "tclversion"))
        self.vars: dict[str, tk.Variable] = {}
        self.rating_vars: dict[str, tk.BooleanVar] = {}
        self.kind_vars: dict[str, tk.BooleanVar] = {}
        self.rows: dict[str, list] = {}        # key -> widgets to enable/disable
        self.labels: dict[str, ttk.Label] = {}
        self.errlabels: dict[str, ttk.Label] = {}
        self.tab_frames: dict[str, ttk.Frame] = {}
        self.errors: dict[str, str] = {}
        self.warnings: list[str] = []
        self._refresh_job = None
        self.last_exit = None
        self.close_pending = False
        self.paused = False
        self.started_mode = None
        self.log_path = None
        self.destroyed = False
        self._texts: list[tuple] = []          # 1.9: tk widgets the theme recolours: (widget, readonly_bg)
        self._msg_kind, self._status_kind, self._signin_kind = "info", None, "info"
        self._disk_notified = False
        self._dash_cache = (None, None)        # (out, status) of the idle dashboard
        self.resume_info = None
        self.run_started = None

        state, self.notes = cp.load_settings(self.settings_path)
        state.update(prefill or {})
        self.theme_choice = th.normalize(state.get("theme", th.DEFAULT))
        self._theme_name = th.resolve(self.theme_choice)
        self.notify_var = tk.BooleanVar(value=bool(state.get("notify", True)))
        self._setup_style()
        self._build()
        self._apply_theme()
        self.set_state(state)
        self._load_saved_key()
        if "site" in self.vars:   # 3.0: each site has its own saved key
            self.vars["site"].trace_add("write", lambda *_: self._site_changed())
        self.saved_state = self.get_state()
        for n in self.notes:
            self.log_line(n, logging.WARNING)
        self.log_line(f"Settings file: {self.settings_path}" + ("" if self.settings_path.is_file() else
                                                                " (not created yet: use Save settings)"))
        self.refresh_now()
        if not self.embedded:
            root.protocol("WM_DELETE_WINDOW", self.on_close)
            root.report_callback_exception = self._callback_error
        root.after(POLL_MS, self.poll)
        root.after(REFRESH_MS, self.refresh_progress)
        root.after(THEME_POLL_MS, self._poll_system_theme)
        self.check_resume()
        self.check_previous_install()

    # ------------------------------------------------------------------ style / fonts / theme
    def _setup_style(self):
        st = ttk.Style(self.root)
        self.style = st
        th.apply(self.root, st, th.PALETTES[self._theme_name])  # clam in both themes (recolourable)
        base = tkfont.nametofont("TkDefaultFont")
        size = base.cget("size")
        self.f_bold = base.copy()
        self.f_bold.configure(weight="bold")
        self.f_title = base.copy()
        self.f_title.configure(size=(abs(size) + 4) * (1 if size > 0 else -1), weight="bold")
        self.f_small = base.copy()
        self.f_small.configure(size=(abs(size) - 1) * (1 if size > 0 else -1))
        self.f_big = base.copy()
        self.f_big.configure(size=(abs(size) + 3) * (1 if size > 0 else -1), weight="bold")
        # 2.0: the log / command / preview font. TkFixedFont is Courier New 10 on Windows, which looked
        # oversized next to the 9 pt UI font (and got cut off in the small log pane): use a proper
        # monospace family at the same size as the rest of the window.
        self.f_mono = tkfont.nametofont("TkFixedFont").copy()
        self.f_mono.configure(family=mono_family(self.root, self.f_mono.cget("family")), size=size)
        self._fonts()

    def _fonts(self):
        st = self.style
        st.configure("Accent.TButton", font=self.f_bold, padding=(14, 4))
        st.configure("Big.Accent.TButton", font=self.f_big, padding=(30, 8))   # 2.0: the Start button
        st.configure("Side.TButton", padding=(12, 8))
        st.configure("Err.TLabel", font=self.f_small)
        st.configure("Muted.TLabel", font=self.f_small)
        st.configure("Group.TLabelframe.Label", font=self.f_bold)
        st.configure("Banner.TLabel", font=self.f_bold)
        st.configure("Toolbutton", font=self.f_small)

    def _text(self, w, readonly_bg=False):
        self._texts.append((w, readonly_bg))
        return w

    def _apply_theme(self):
        """Apply the current choice (light / dark / follow the system) to every widget, instantly."""
        name = th.resolve(self.theme_choice)
        p = th.PALETTES[name]
        self._theme_name = name
        COLORS.clear()
        COLORS.update(p)
        th.apply(self.root, self.style, p)
        self._fonts()
        for w, ro in self._texts:
            try:
                th.style_text(w, p, readonly_bg=ro)
            except tk.TclError:
                pass
        for sf in list(self.tab_frames.values()) + [self.status_sf]:
            sf.canvas.configure(background=p["bg"])
        self.log.tag_configure("warn", foreground=p["warn"])
        self.log.tag_configure("err", foreground=p["error"])
        self.log.tag_configure("cmd", foreground=p["cmd"])
        self.preview_text.tag_configure("desc", foreground=p["muted"])
        self.preview_text.tag_configure("err", foreground=p["error"])
        self.msg_lbl.configure(foreground=p.get(self._msg_kind, p["info"]))
        self.status_lbl.configure(foreground=p.get(self._status_kind, p["fg"]) if self._status_kind else p["fg"])
        self.signin_lbl.configure(foreground=p.get(self._signin_kind, p["info"]))
        th.dark_title_bar(self.root, name == "dark")
        if hasattr(self, "saved_state"):
            self.refresh_now()
        if self.on_theme:
            self.on_theme(p)

    def set_theme(self, choice: str | None = None):
        self.theme_choice = th.normalize(choice if choice is not None else self.theme_var.get())
        self.theme_var.set(self.theme_choice)
        self._apply_theme()
        try:
            cp.save_gui_values({"theme": self.theme_choice}, self.settings_path)
        except OSError as e:
            self.log_line(f"Could not save the theme to {self.settings_path}: {e}", logging.WARNING)

    def toggle_theme(self, _e=None):
        self.set_theme("light" if self._theme_name == "dark" else "dark")
        return "break"

    def _poll_system_theme(self):
        if self.destroyed:
            return
        if self.theme_choice == "system" and th.resolve("system") != self._theme_name:
            self._apply_theme()
        self.root.after(THEME_POLL_MS, self._poll_system_theme)

    def save_notify(self):
        try:
            cp.save_gui_values({"notify": bool(self.notify_var.get())}, self.settings_path)
        except OSError as e:
            self.log_line(f"Could not save the notification setting: {e}", logging.WARNING)

    def _set_status(self, text: str, kind: str | None):
        self.status_var.set(text)
        self._status_kind = kind
        self.status_lbl.configure(foreground=COLORS.get(kind, COLORS["fg"]) if kind else COLORS["fg"])

    # ------------------------------------------------------------------ layout
    def _build(self):
        r = self.host
        if not self.embedded:
            self.root.title(f"Packrat {__version__} - site archive")
        r.columnconfigure(0, weight=1)
        r.rowconfigure(1, weight=1)

        head = ttk.Frame(r, padding=(10, 8, 10, 0))
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(1, weight=1)
        ttk.Label(head, text="Site archive" if self.embedded else "Packrat",
                  font=self.f_big if self.embedded else self.f_title).grid(row=0, column=0, sticky="w")
        ver = ttk.Label(head, text="  every post and file of e6ai or e621, kept complete" if self.embedded
                        else f"  version {__version__} - site archive", style="Muted.TLabel")
        ver.grid(row=0, column=1, sticky="w")
        self._bind_help([ver], lambda: (
            "Archives every post and file of a whole site (e6ai or e621): parallel file downloads, the API at "
            "your chosen rate, md5 verification and a completeness report. Point at anything in this window "
            "to see what it does."))
        self.status_var = tk.StringVar(value="Ready")
        self.status_lbl = ttk.Label(head, textvariable=self.status_var, font=self.f_bold)
        self.status_lbl.grid(row=0, column=2, sticky="e", padx=(0, 16))
        self._bind_help([self.status_lbl], lambda: (
            "What the archiver is doing: Ready (nothing running), Running, Paused, Finished, Stopped, "
            "or Stopped: access denied (403) / Stopped: error (see the message and the log on the right)."))
        tf = ttk.Frame(head)
        if not self.embedded:   # in the main window the theme buttons are in its header
            tf.grid(row=0, column=3, sticky="e")
        ttk.Label(tf, text="Theme", style="Muted.TLabel").pack(side="left", padx=(0, 4))
        self.theme_var = tk.StringVar(value=self.theme_choice)
        self.theme_btns = {}
        for c in ("light", "dark", "system"):
            b = ttk.Radiobutton(tf, text=th.LABELS[c], value=c, variable=self.theme_var, style="Toolbutton",
                                command=self.set_theme)
            b.pack(side="left", padx=(0, 2))
            self.theme_btns[c] = b
        self._bind_help(list(self.theme_btns.values()), lambda: (
            "Light or dark window, or follow the Windows setting (Settings > Personalization > Colors > 'Choose "
            "your app mode'). Applies at once and is remembered (in the [gui] section of the settings file). "
            "Shortcut: Ctrl+Shift+D switches between light and dark."))
        if not self.embedded:
            self.root.bind("<Control-D>", self.toggle_theme)   # Ctrl+Shift+D
        # 1.9: resume banner (shown at launch when the last download run did not finish)
        self.banner = ttk.Frame(head, style="Banner.TFrame", padding=(10, 6))
        self.banner.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        self.banner.columnconfigure(0, weight=1)
        self.banner_var = tk.StringVar()
        bl = ttk.Label(self.banner, textvariable=self.banner_var, style="Banner.TLabel", justify="left")
        bl.grid(row=0, column=0, sticky="w")
        self.resume_btn = ttk.Button(self.banner, text="Resume", style="Accent.TButton", command=self.resume_last)
        self.resume_btn.grid(row=0, column=1, padx=(10, 0))
        ttk.Button(self.banner, text="Not now", command=self.hide_banner).grid(row=0, column=2, padx=(6, 0))
        ttk.Button(self.banner, text="Don't ask for this run",
                   command=lambda: self.hide_banner(forget=True)).grid(row=0, column=3, padx=(6, 0))
        self.banner.grid_remove()
        self._wrap_banner(self.banner, bl)
        # 2.0: "earlier install found" banner (first start of a new copy that has no settings yet)
        self.imp_banner = ttk.Frame(head, style="Banner.TFrame", padding=(10, 6))
        self.imp_banner.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        self.imp_banner.columnconfigure(0, weight=1)
        self.imp_var = tk.StringVar()
        il = ttk.Label(self.imp_banner, textvariable=self.imp_var, style="Banner.TLabel", justify="left")
        il.grid(row=0, column=0, sticky="w")
        self.imp_btn = ttk.Button(self.imp_banner, text="Import these settings", style="Accent.TButton",
                                  command=lambda: self.import_settings(self.import_found))
        self.imp_btn.grid(row=0, column=1, padx=(10, 0))
        ttk.Button(self.imp_banner, text="Other folder...", command=self.import_settings).grid(
            row=0, column=2, padx=(6, 0))
        ttk.Button(self.imp_banner, text="Not now", command=self.hide_import_banner).grid(row=0, column=3,
                                                                                         padx=(6, 0))
        self.imp_banner.grid_remove()
        self._wrap_banner(self.imp_banner, il)
        self.import_found = None

        pw = ttk.PanedWindow(r, orient="horizontal")
        pw.grid(row=1, column=0, sticky="nsew", padx=10, pady=8)
        left = ttk.Frame(pw)
        right = ttk.Frame(pw)
        pw.add(left, weight=3)
        pw.add(right, weight=2)
        self.paned = pw

        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        self.nb = ttk.Notebook(left)
        self.nb.grid(row=0, column=0, sticky="nsew")
        for i, name in enumerate(cp.TABS):
            sf = ScrollFrame(self.nb)
            self.nb.add(sf, text=f" {name} ")   # 2.0: no "1 2 3 4" prefixes: all four tabs fit at 980 px
            self.tab_frames[name] = sf
        self.help_var = tk.StringVar(value="Point at any option to see what it does.")
        self.nb.bind("<<NotebookTabChanged>>",
                     lambda e: self.help_var.set("Point at any option to see what it does."), add="+")
        hl = ttk.Label(left, textvariable=self.help_var, style="Help.TLabel", wraplength=600, justify="left")
        hl.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        left.bind("<Configure>", lambda e: hl.configure(wraplength=max(200, e.width - 20)))

        self.label_col = int(190 * max(1.0, self.root.winfo_fpixels("1i") / 96.0))
        self.mode_hints: dict[str, ttk.Label] = {}
        self._build_basics(self.tab_frames[cp.TABS[0]].inner)
        groups = {}
        for o in cp.OPTIONS:
            if o.tab == cp.TABS[0] or o.key in ("out", "username", "rate", "min_free_gb", "keep_awake",
                                                 "on_low_disk"):
                continue
            parent = self.tab_frames[o.tab].inner
            if o.tab not in self.mode_hints:
                h = ttk.Label(parent, text="", style="Muted.TLabel", justify="left")
                h.pack(fill="x", pady=(0, 6))
                self._autowrap(h, parent, 20)
                self.mode_hints[o.tab] = h
            if (o.tab, o.group) not in groups:
                groups[(o.tab, o.group)] = [self._group(parent, o.group), 0]
                if o.tab == cp.TABS[2] and o.group == "File names":
                    self._build_name_preview(parent, groups[(o.tab, o.group)][0])
            g = groups[(o.tab, o.group)]
            g[1] = self._add_option(g[0], g[1], o)
        self._build_identity_extras(groups)
        self._build_progress(right)
        self._build_bottom(r)

    def _group(self, parent, title, before=None):
        lf = ttk.LabelFrame(parent, text=title, padding=(10, 4, 10, 8), style="Group.TLabelframe")
        lf.pack(fill="x", pady=(0, 8), **({"before": before} if before else {}))
        lf.columnconfigure(1, weight=1)
        lf.columnconfigure(0, minsize=self.label_col)
        return lf

    @staticmethod
    def _autowrap(label, container, minus=40):
        container.bind("<Configure>", lambda e: label.configure(wraplength=max(200, e.width - minus)), add="+")

    def _build_basics(self, f):
        g = self._group(f, "What to do")
        self.mode_labels = {m.label: m.key for m in cp.MODES}
        self.mode_var = tk.StringVar()
        ttk.Label(g, text="Mode").grid(row=0, column=0, sticky="w", padx=(0, 10))
        cb = ttk.Combobox(g, textvariable=self.mode_var, state="readonly", values=[m.label for m in cp.MODES],
                          height=len(cp.MODES))
        cb.grid(row=0, column=1, columnspan=2, sticky="ew", pady=2)
        self.mode_cb = cb
        self.mode_help = ttk.Label(g, text="", wraplength=520, justify="left", style="Muted.TLabel")
        self.mode_help.grid(row=1, column=1, columnspan=2, sticky="w")
        self._bind_help([cb], lambda: cp.MODE[self.mode_key()].help)
        self._autowrap(self.mode_help, g, self.label_col + 30)
        self.mode_var.trace_add("write", lambda *_: self.on_change())
        self._add_option(g, 2, cp.OPT["site"])   # 3.0: e6ai or e621

        g2 = self._group(f, "Who you are")
        self._add_option(g2, 0, cp.OPT["username"])
        self._build_signin(f)
        g3 = self._group(f, "Where to save")
        r = self._add_option(g3, 0, cp.OPT["out"])
        self.free_var = tk.StringVar()
        fl = ttk.Label(g3, textvariable=self.free_var, style="Muted.TLabel", wraplength=520, justify="left")
        fl.grid(row=r, column=1, columnspan=2, sticky="w")
        self._autowrap(fl, g3, self.label_col + 30)
        self.example_var = tk.StringVar()
        ttk.Label(g3, text="Example file", style="Muted.TLabel").grid(row=r + 1, column=0, sticky="nw", padx=(0, 10))
        ex = ttk.Label(g3, textvariable=self.example_var, style="Muted.TLabel", wraplength=520, justify="left")
        ex.grid(row=r + 1, column=1, columnspan=2, sticky="w")
        self._autowrap(ex, g3, self.label_col + 30)
        self.path_warn = ttk.Label(g3, text="", style="Warn.TLabel", wraplength=520, justify="left")
        self.path_warn.grid(row=r + 2, column=1, columnspan=2, sticky="w")
        self._autowrap(self.path_warn, g3, self.label_col + 30)
        self.path_warn.grid_remove()
        g4 = self._group(f, "Speed")
        r = 0
        for k in ("workers", "rate"):
            r = self._add_option(g4, r, cp.OPT[k])
        al = ttk.Label(g4, text="Files download in parallel from the static file server; the API (post lists, "
                                "metadata) uses its own rate. Note: the e621-family API docs ask for at most "
                                "~2 requests/s on the API. Always on: descriptive User-Agent with your name, "
                                "resumable .part downloads, stop after 20 file-server 403s in a row.",
                       style="Muted.TLabel", justify="left")
        al.grid(row=r, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self._autowrap(al, g4, 30)
        g5 = self._group(f, "Checksums")
        r = 0
        for k in ("check_md5", "verify_after", "verify_action", "redownload", "report_after"):
            r = self._add_option(g5, r, cp.OPT[k])
        g6 = self._group(f, "Disk and power")
        r = 0
        for k in ("min_free_gb", "on_low_disk", "keep_awake"):
            r = self._add_option(g6, r, cp.OPT[k])
        nc = ttk.Checkbutton(g6, text="Desktop notification when a run ends or pauses (finished, 403 stop, disk "
                                      "full, error, key rejected)", variable=self.notify_var,
                             command=self.save_notify)
        nc.grid(row=r, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self._bind_help([nc], lambda: (
            "Windows: a notification in the corner of the screen (via PowerShell; falls back to a tray balloon), "
            "Linux: notify-send, macOS: Notification Center; otherwise a small window from the panel. Only for "
            "downloads and runs longer than a minute; not when you press Stop yourself. Saved at once ([gui])."))

    def _build_signin(self, f):
        """Login name + API key (masked), key page link, one-request test, optional saved key."""
        g = self._group(f, cp.SIGNIN)
        intro = ttk.Label(g, text="Some posts are only shown to signed-in users. e6ai and e621 don't let tools "
                                  "sign in with your password; use your login name and an API key from your "
                                  "account page instead. Leave the key blank to archive signed out.",
                          style="Muted.TLabel", justify="left")
        intro.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))
        self._autowrap(intro, g, 30)
        r = self._add_option(g, 1, cp.OPT["login"])
        self.login_hint = ttk.Label(g, text="", style="Muted.TLabel")
        self.login_hint.grid(row=1, column=2, sticky="w", padx=(6, 0))
        r = self._add_option(g, r, cp.OPT["api_key"])
        self.key_entry = self.rows["api_key"][1]
        self.show_key = tk.BooleanVar(value=False)
        sk = ttk.Checkbutton(g, text="Show", variable=self.show_key,
                             command=lambda: self.key_entry.configure(show="" if self.show_key.get() else "*"))
        sk.grid(row=r - 2, column=2, sticky="w", padx=(6, 0))
        bf = ttk.Frame(g)
        bf.grid(row=r, column=1, columnspan=2, sticky="w", pady=(2, 0))
        kb = ttk.Button(bf, text="Where do I get this?", command=self.open_key_page)
        kb.pack(side="left")
        self.test_btn = ttk.Button(bf, text="Test sign-in", command=self.test_signin)
        self.test_btn.pack(side="left", padx=(8, 0))
        self._bind_help([kb], lambda: "Opens your account page (" + cp.api_key_page(self._site()) + "). Log in there in "
                        "your browser, open 'Manage API Keys' (older site versions: 'Manage API Access'), create or "
                        "view a key and paste it here.")
        self._bind_help([self.test_btn], lambda: "Makes ONE signed-in request to the site (your User-Agent, the rate "
                        "limit and the stop-on-403 rule apply) and tells you whether the login name + API key "
                        "work. Nothing is downloaded.")
        r += 1
        self.signin_var = tk.StringVar(value="")
        self.signin_lbl = ttk.Label(g, textvariable=self.signin_var, justify="left")
        self.signin_lbl.grid(row=r, column=1, columnspan=2, sticky="w")
        self._autowrap(self.signin_lbl, g, self.label_col + 30)
        r += 1
        self.remember_var = None
        if cp.keyring_available():
            store = ("Windows Credential Manager" if os.name == "nt" else
                     "macOS Keychain" if sys.platform == "darwin" else "the system keyring")
            self.remember_var = tk.BooleanVar(value=False)
            rf = ttk.Frame(g)
            rf.grid(row=r, column=0, columnspan=3, sticky="w", pady=2)
            rc = ttk.Checkbutton(rf, text="Remember API key on this PC", variable=self.remember_var)
            rc.pack(side="left")
            self.forget_btn = ttk.Button(rf, text="Forget saved key", command=self.forget_saved_key)
            self.forget_btn.pack(side="left", padx=(12, 0))
            self._bind_help([rc], lambda: f"Keeps the key in {store}, protected by your OS account, "
                            "never in the settings file. It is saved when you press Test sign-in, Start or "
                            "Save settings, and filled in when the panel opens.")
            self._bind_help([self.forget_btn], lambda: f"Deletes the saved key from {store}. The key stays in the "
                            "field for this session only.")
        else:
            nk = ttk.Label(g, text="The key is kept for this session only (to remember it on this PC, install the "
                                   "optional 'keyring' package).", style="Muted.TLabel", justify="left")
            nk.grid(row=r, column=0, columnspan=3, sticky="w", pady=2)
            self._autowrap(nk, g, 30)
        r += 1
        r = self._add_option(g, r, cp.OPT["login_retry"])
        self.lr_var = tk.StringVar(value="")
        lr = ttk.Label(g, textvariable=self.lr_var, style="Muted.TLabel", justify="left")
        lr.grid(row=r, column=0, columnspan=3, sticky="w")
        self._autowrap(lr, g, 30)
        self._signin_busy = False
        self._signin_forbidden = False
        self._lr_out = None

    # ---- sign in actions
    def open_key_page(self):
        try:
            webbrowser.open(cp.api_key_page(self._site()))
        except Exception as e:  # noqa: BLE001
            self.set_message(f"Could not open the browser: {e}. Go to {cp.api_key_page(self._site())}", "error")

    def _site(self) -> str:
        v = self.vars.get("site")
        return (v.get() if v is not None else "") or "e6ai"

    def _signin_result(self, level: str, text: str):
        self.signin_var.set(text)
        self._signin_kind = level
        self.signin_lbl.configure(foreground=COLORS.get(level, COLORS["info"]))

    def _remember_key(self):
        """Save the key in the OS credential store if 'Remember' is ticked (never in the ini)."""
        if self.remember_var is None or not self.remember_var.get():
            return
        st = self.get_state()
        key, login = st.get("api_key", "").strip(), cp.effective_login(st)
        site = st.get("site") or "e6ai"
        if key and login and cp.saved_key(login, site=site) != key:
            if cp.save_key(login, key, site=site):
                self.log_line(f"API key for {login} on {site} saved in the system credential store (not in the settings file).")
            else:
                self.log_line("Could not save the API key in the system credential store.", logging.WARNING)

    def forget_saved_key(self):
        st = self.get_state()
        login = cp.effective_login(st)
        ok = cp.forget_key(login, site=st.get("site") or "e6ai")
        if self.remember_var is not None:
            self.remember_var.set(False)
        self.set_message(f"Saved API key for {login} removed from this PC." if ok else
                         "No saved API key to remove.", "ok" if ok else "info")

    def _site_changed(self):
        if self.remember_var is not None and self.remember_var.get():
            self.vars["api_key"].set("")      # that key belongs to the other site
            self.remember_var.set(False)
        self._load_saved_key()

    def _load_saved_key(self):
        if self.remember_var is None or self.vars["api_key"].get().strip():
            return
        st = self.get_state()
        login = cp.effective_login(st)
        k = cp.saved_key(login, site=st.get("site") or "e6ai")
        if k:
            self.vars["api_key"].set(k)
            self.remember_var.set(True)
            self.log_line(f"API key for {login} loaded from the system credential store.")

    def test_signin(self):
        if self._signin_busy or self.runner.running:
            return
        st = self.get_state()
        if self._signin_forbidden or self.last_exit == 3:
            if not messagebox.askyesno("Last answer was 403", "The site answered 403 Forbidden last time. Testing "
                                       "again right away is not recommended.\n\nTest anyway?", icon="warning",
                                       default="no", parent=self.root):
                return
        self._remember_key()
        self._signin_busy = True
        self._signin_result("info", f"Testing (one request to {self._site()})...")
        self._update_buttons()
        box = {}

        def work():
            try:
                box["res"] = cp.run_test_login(st)
            except Exception as e:  # noqa: BLE001
                box["res"] = ("error", f"Could not test the sign-in: {e}")

        threading.Thread(target=work, name="test-login", daemon=True).start()

        def check():
            if self.destroyed:
                return
            if "res" not in box:
                self.root.after(150, check)
                return
            level, text = box["res"]
            text = redact(text, st.get("api_key"))
            self._signin_result(level, text)
            self.log_line(f"Test sign-in: {text}", logging.INFO if level == "ok" else logging.WARNING)
            if level == "forbidden":
                self._signin_forbidden = True
                self.last_exit = 3
                messagebox.showwarning("Stopped on 403", text, parent=self.root)
            # short cool-down so the button can't be used to hammer the site
            self.root.after(3000, self._signin_done)

        self.root.after(150, check)

    def _signin_done(self):
        self._signin_busy = False
        self._update_buttons()

    def _refresh_login_required(self, out: str, force: bool = False):
        if not force and out == self._lr_out:
            return
        self._lr_out = out
        n = cp.login_required_in_archive(out)
        if not n:
            self.lr_var.set("" if n is None else "No posts in this archive are waiting for sign-in.")
        else:
            self.lr_var.set(f"{n:,} posts in this archive were saved without a file URL (hidden from the account "
                            "that fetched them). A signed-in Full / Update / Download re-checks them first.")

    def _build_name_preview(self, parent, before):
        lf = self._group(parent, "Live preview (example posts)", before=before)
        self.preview_text = self._text(tk.Text(lf, height=7, wrap="none", font=self.f_mono, relief="flat",
                                               background=COLORS["bg"], borderwidth=0), True)
        self.preview_text.grid(row=0, column=0, columnspan=3, sticky="ew")
        self.preview_text.tag_configure("desc", foreground=COLORS["muted"])
        self.preview_text.tag_configure("err", foreground=COLORS["error"])
        self.preview_text.configure(state="disabled")

    def _build_identity_extras(self, groups):
        g = groups.get((cp.TABS[3], "Identity"))
        if not g:
            return
        lf, r = g
        self.ua_var = tk.StringVar()
        ttk.Label(lf, text="User-Agent sent", style="Muted.TLabel").grid(row=r, column=0, sticky="w", padx=(0, 10))
        ttk.Label(lf, textvariable=self.ua_var, style="Muted.TLabel").grid(row=r, column=1, columnspan=2, sticky="w")

    def _bind_help(self, widgets, text_fn):
        for w in widgets:
            w.bind("<Enter>", lambda e, f=text_fn: self.help_var.set(f()), add="+")
            Tooltip(w, text_fn)

    def _add_option(self, parent, row, o: cp.Opt) -> int:
        """Label | input (+Browse) on one row, inline error under it. Returns the next free row."""
        help_fn = (lambda o=o: f"{o.label}: {o.help}" + ("" if not o.flag or isinstance(o.flag, dict)
                                                          else f"   [{o.flag}]") + self._na_text(o))
        widgets = []
        if o.kind in ("flag", "neg"):
            v = tk.BooleanVar(value=bool(o.default))
            w = ttk.Checkbutton(parent, text=o.label, variable=v)
            w.grid(row=row, column=0, columnspan=3, sticky="w", pady=2)
            widgets.append(w)
            self.vars[o.key] = v
        else:
            lbl = ttk.Label(parent, text=o.label)
            lbl.grid(row=row, column=0, sticky="w", padx=(0, 10), pady=2)
            self.labels[o.key] = lbl
            widgets.append(lbl)
            v = tk.StringVar(value=str(o.default))
            self.vars[o.key] = v
            if o.kind == "ratings":
                box = ttk.Frame(parent)
                box.grid(row=row, column=1, columnspan=2, sticky="w")
                for code, text in RATING_BOXES:
                    bv = tk.BooleanVar(value=False)
                    self.rating_vars[code] = bv
                    c = ttk.Checkbutton(box, text=text, variable=bv, command=self._sync_ratings)
                    c.pack(side="left", padx=(0, 12))
                    widgets.append(c)
            elif o.kind == "kinds":
                box = ttk.Frame(parent)
                box.grid(row=row, column=1, columnspan=2, sticky="w")
                for k in cp.META_KINDS:
                    bv = tk.BooleanVar(value=k in str(o.default).split(","))
                    self.kind_vars[k] = bv
                    c = ttk.Checkbutton(box, text=k.replace("_", " "), variable=bv, command=self._sync_kinds)
                    c.pack(side="left", padx=(0, 12))
                    widgets.append(c)
            elif o.kind == "choice":
                w = ttk.Combobox(parent, textvariable=v, values=list(o.choices), state="readonly", width=18)
                w.grid(row=row, column=1, sticky="w", pady=2)
                widgets.append(w)
            elif o.kind == "combo":
                w = ttk.Combobox(parent, textvariable=v, values=list(o.choices), width=40)
                w.grid(row=row, column=1, columnspan=2, sticky="ew", pady=2)
                widgets.append(w)
            elif o.kind in ("int", "float") and o.step:
                w = ttk.Spinbox(parent, textvariable=v, from_=o.lo, to=o.hi, increment=o.step, width=10,
                                format="%.1f" if o.kind == "float" and o.step < 1 else "")
                w.grid(row=row, column=1, sticky="w", pady=2)
                widgets.append(w)
            elif o.kind in ("int", "float"):
                w = ttk.Entry(parent, textvariable=v, width=12)
                w.grid(row=row, column=1, sticky="w", pady=2)
                widgets.append(w)
            else:
                w = ttk.Entry(parent, textvariable=v, show="*" if o.kind == "secret" else "")
                w.grid(row=row, column=1, sticky="ew", pady=2)
                widgets.append(w)
                if o.kind in ("dir", "file", "savefile"):
                    b = ttk.Button(parent, text="Browse...", command=lambda o=o: self.browse(o))
                    b.grid(row=row, column=2, sticky="w", padx=(6, 0))
                    widgets.append(b)
                    if o.key == "out":
                        self.browse_out_btn = b
        err = ttk.Label(parent, text="", style="Err.TLabel")
        err.grid(row=row + 1, column=1, columnspan=2, sticky="w")
        err.grid_remove()
        self.errlabels[o.key] = err
        self.rows[o.key] = widgets
        self._bind_help(widgets, help_fn)
        v.trace_add("write", lambda *_: self.on_change())
        return row + 2

    def _na_text(self, o):
        m = cp.MODE[self.mode_key()]
        return "" if cp.applies(o, m) else f"\n(Not used by '{m.label}'.)"

    def _sync_ratings(self):
        self.vars["rating"].set(",".join(c for c, _ in RATING_BOXES if self.rating_vars[c].get()))

    def _sync_kinds(self):
        self.vars["meta_kinds"].set(",".join(k for k in cp.META_KINDS if self.kind_vars[k].get()))

    def _build_progress(self, outer):
        """2.0: message on top, then a vertical split: the status boxes (scroll if the window is short)
        and the log, which always keeps at least LOG_MIN_LINES lines (drag the divider to change it)."""
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)
        self.msg_var = tk.StringVar(value="Ready. Choose what to do on the Basics tab, then press Start.")
        self.msg_lbl = ttk.Label(outer, textvariable=self.msg_var, wraplength=420, justify="left",
                                 font=self.f_bold)
        self.msg_lbl.grid(row=0, column=0, sticky="ew", padx=(8, 0))
        outer.bind("<Configure>", lambda e: self.msg_lbl.configure(wraplength=max(200, e.width - 20)))
        vp = self.vpaned = ttk.PanedWindow(outer, orient="vertical")
        vp.grid(row=1, column=0, sticky="nsew", padx=(8, 0), pady=(6, 0))
        self.status_sf = ScrollFrame(vp, padding=(0, 0, 6, 4))
        f = self.status_sf.inner
        f.columnconfigure(0, weight=1)
        logpane = ttk.Frame(vp)
        vp.add(self.status_sf, weight=1)
        vp.add(logpane, weight=1)
        vp.bind("<Configure>", self._fit_status_sash, add="+")
        f.bind("<Configure>", self._fit_status_sash, add="+")
        self._user_sash = False
        self.pv = {}

        def var(k):
            self.pv[k] = tk.StringVar(value="")
            return self.pv[k]
        da = self.dash_frame = ttk.LabelFrame(f, text="Whole archive", padding=(10, 4, 10, 8),
                                              style="Group.TLabelframe")
        da.grid(row=1, column=0, sticky="ew", pady=(0, 0))
        # 2.0: files and GiB on two lines (they were side by side and got cut off in a narrow window); the
        # four figures below are a 2 x 2 grid when there is room and one column when the window is narrow
        ttk.Label(da, textvariable=var("dash_files"), font=self.f_bold).grid(row=0, column=0, columnspan=4,
                                                                            sticky="w")
        ttk.Label(da, textvariable=var("dash_saved"), style="Muted.TLabel").grid(row=1, column=0, columnspan=4,
                                                                                sticky="w")
        self.dbar = ttk.Progressbar(da, maximum=1000)
        self.dbar.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(4, 6))
        self._dash_cells = []
        for lab, k in (("Speed", "dash_rate"), ("This run", "dash_run"), ("Failures", "dash_failed"),
                       ("ETA (all)", "dash_eta")):
            self._dash_cells.append((ttk.Label(da, text=lab + ":", style="Muted.TLabel"),
                                     ttk.Label(da, textvariable=var(k))))
        self._dash_cols = None
        self._layout_dash(2)
        da.bind("<Configure>", lambda e: self._layout_dash(2 if e.width >= DASH_TWO_COLUMNS_PX else 1), add="+")
        self.disk_lbl = ttk.Label(da, textvariable=var("dash_disk"), style="Warn.TLabel", justify="left")
        self.disk_lbl.grid(row=9, column=0, columnspan=4, sticky="w", pady=(4, 0))
        f.bind("<Configure>", lambda e: self.disk_lbl.configure(wraplength=max(200, e.width - 30)), add="+")
        self._bind_help([da], lambda: (
            "Whole archive: everything the posts export lists (all non-deleted posts with a file) against what "
            "is saved in this archive folder. Updated live during a run; ETA (all) is the time left for all "
            "remaining files at the current speed."))
        self.dash_frame = da
        pr = ttk.LabelFrame(f, text="Progress", padding=(10, 4, 10, 8), style="Group.TLabelframe")
        pr.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        pr.columnconfigure(0, weight=1)
        ttk.Label(pr, textvariable=var("phase"), font=self.f_bold).grid(row=0, column=0, sticky="w")
        self.bar = ttk.Progressbar(pr, maximum=1000)
        self.bar.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        ttk.Label(pr, textvariable=var("overall_text"), style="Muted.TLabel").grid(row=2, column=0, sticky="w")
        self.tbar = ttk.Progressbar(pr, maximum=1000)
        self.tbar.grid(row=3, column=0, sticky="ew", pady=(4, 0))
        ttk.Label(pr, textvariable=var("tier_text"), style="Muted.TLabel").grid(row=4, column=0, sticky="w")
        ttk.Label(pr, textvariable=var("file_head")).grid(row=5, column=0, sticky="w", pady=(6, 0))
        ttk.Label(pr, textvariable=var("file_name"), style="Muted.TLabel").grid(row=6, column=0, sticky="w")
        self.fbar = ttk.Progressbar(pr, maximum=1000)
        self.fbar.grid(row=7, column=0, sticky="ew", pady=(2, 0))
        ttk.Label(pr, textvariable=var("file_text"), style="Muted.TLabel").grid(row=8, column=0, sticky="w")
        det = ttk.Frame(pr)
        det.grid(row=9, column=0, sticky="ew", pady=(6, 0))
        cells = [("Speed", "speed"), ("Average", "avg"), ("ETA", "eta"), ("Elapsed", "elapsed"),
                 ("Requests", "requests"), ("Free disk", "free"), ("Downloaded", "downloaded"),
                 ("Skipped", "skipped"), ("Failed", "failed"), ("Missing", "missing"), ("Posts", "posts"),
                 ("Network", "net")]
        for i, (t, k) in enumerate(cells):
            r, c = divmod(i, 3)
            ttk.Label(det, text=t + ":", style="Muted.TLabel").grid(row=r, column=c * 2, sticky="w")
            ttk.Label(det, textvariable=var(k)).grid(row=r, column=c * 2 + 1, sticky="w", padx=(4, 14))
        le = ttk.Label(pr, textvariable=var("last_error"), style="Warn.TLabel", wraplength=420, justify="left")
        le.grid(row=10, column=0, sticky="w", pady=(4, 0))
        pr.bind("<Configure>", lambda e: le.configure(wraplength=max(200, e.width - 30)), add="+")
        logpane.rowconfigure(1, weight=1)
        logpane.columnconfigure(0, weight=1)
        lh = ttk.Frame(logpane)
        lh.grid(row=0, column=0, sticky="ew", pady=(6, 0))
        ttk.Label(lh, text="Log", font=self.f_bold).pack(side="left")
        clr = ttk.Button(lh, text="Clear", command=self.clear_log)
        clr.pack(side="right")
        cpy = ttk.Button(lh, text="Copy", command=self.copy_log)
        cpy.pack(side="right", padx=(0, 6))
        self._bind_help([cpy], lambda: "Copy the whole log to the clipboard (e.g. to paste into a bug report).")
        self._bind_help([clr], lambda: "Clear the log in this window (the log file on disk is kept).")
        lf = ttk.Frame(logpane)
        lf.grid(row=1, column=0, sticky="nsew", pady=(4, 0))
        lf.rowconfigure(0, weight=1)
        lf.columnconfigure(0, weight=1)
        self.log = self._text(tk.Text(lf, height=LOG_MIN_LINES, wrap="word", font=self.f_mono, state="disabled",
                                      relief="flat", borderwidth=1, highlightthickness=1, padx=6, pady=4,
                                      spacing1=1))
        sb = ttk.Scrollbar(lf, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        self.log.tag_configure("warn", foreground=COLORS["warn"])
        self.log.tag_configure("err", foreground=COLORS["error"])
        self.log.tag_configure("cmd", foreground=COLORS["cmd"])
        self._show_snapshot()

    def _layout_dash(self, cols: int):
        if cols == self._dash_cols:
            return
        self._dash_cols = cols
        for i, (lab, val) in enumerate(self._dash_cells):
            r, c = divmod(i, cols)
            lab.grid(row=3 + r, column=c * 2, sticky="w", padx=(0, 6))
            val.grid(row=3 + r, column=c * 2 + 1, sticky="w", padx=(0, 16))
        da = self.dash_frame
        for c in range(4):
            da.columnconfigure(c, weight=1 if (c % 2 and c < cols * 2) else 0)

    def _log_min_height(self) -> int:
        return self.f_mono.metrics("linespace") * (LOG_MIN_LINES + 1) + self.f_bold.metrics("linespace") + 30

    def _fit_status_sash(self, _e=None):
        """Give the status boxes their natural height, but never squeeze the log below LOG_MIN_LINES lines;
        the status boxes scroll when the window is too short for both (min window size, 150% scaling)."""
        try:
            total = self.vpaned.winfo_height()
            if total < 100:
                return
            natural = self.status_sf.inner.winfo_reqheight() + 4
            want = max(120, min(natural, total - self._log_min_height()))
            cur = self.vpaned.sashpos(0)
            if abs(cur - want) > 2:
                self.vpaned.sashpos(0, want)
        except tk.TclError:
            pass

    def _build_bottom(self, r):
        b = ttk.Frame(r, padding=(10, 0, 10, 10))
        b.grid(row=2, column=0, sticky="ew")
        b.columnconfigure(1, weight=1)
        ttk.Label(b, text="Command", font=self.f_bold).grid(row=0, column=0, sticky="nw", padx=(0, 8))
        self.cmd_text = self._text(tk.Text(b, height=2, wrap="char", font=self.f_mono, relief="flat",
                                           borderwidth=1, highlightthickness=1))
        self.cmd_text.grid(row=0, column=1, sticky="ew")
        self.cmd_text.bind("<Key>", lambda e: None if (e.state & 4 and e.keysym.lower() in ("c", "a")) else "break")
        cb = ttk.Button(b, text="Copy", command=self.copy_command)
        cb.grid(row=0, column=2, sticky="n", padx=(6, 0))
        self._bind_help([self.cmd_text, cb], lambda: (
            "The exact command the panel runs in the background (copy it to run the same thing in a terminal). "
            "Not shown: --progress-json and --control-file, which connect the run to this window; the API key "
            "goes in the PACKRAT_API_KEY environment variable, never on the command line."))
        # 2.0: secondary buttons in two rows on the left, Pause / Stop and a big Start on the right
        outer_bar = ttk.Frame(b)
        outer_bar.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        outer_bar.columnconfigure(0, weight=1)
        sec = ttk.Frame(outer_bar)
        sec.grid(row=0, column=0, sticky="w")
        act = ttk.Frame(outer_bar)
        act.grid(row=0, column=1, sticky="e")
        bar = ttk.Frame(sec)
        bar.grid(row=0, column=0, sticky="w")
        self.save_btn = ttk.Button(bar, text="Save settings", command=self.save)
        self.save_btn.pack(side="left")
        self.reset_btn = ttk.Button(bar, text="Reset to defaults", command=self.reset)
        self.reset_btn.pack(side="left", padx=(6, 0))
        self.import_btn = ttk.Button(bar, text="Import settings...", command=self.import_settings)  # 2.0
        self.import_btn.pack(side="left", padx=(6, 18))
        self._bind_help([self.import_btn], lambda: (
            "Upgrading? Take the settings of an earlier e6ai-archiver folder (for example C:\\e6ai-archiver) "
            "and keep archiving into ITS archive folder: nothing is copied, moved or downloaded again. "
            "Stop the old version first."))
        # 1.9: the four Open buttons get their own row so Pause / Stop / Start never get clipped at 980 px
        obar = ttk.Frame(sec)
        obar.grid(row=1, column=0, sticky="w", pady=(6, 0))
        ob = ttk.Button(obar, text="Open archive folder", command=self.open_out)
        ob.pack(side="left")
        ol = ttk.Button(obar, text="Open log", command=self.open_log)
        ol.pack(side="left", padx=(6, 0))
        self._bind_help([ob], lambda: "Open the archive folder (where the files, the database and the reports "
                                      "are) in Explorer.")
        self._bind_help([ol], lambda: "Open the full log file of the current / last run in a text editor.")
        self.faillog_btn = ttk.Button(obar, text="Open failure log", command=self.open_failure_log)  # 1.9
        self.faillog_btn.pack(side="left", padx=(6, 0))
        self._bind_help([self.faillog_btn], lambda: (
            "Open failed-downloads.log in the archive folder: one line per file that failed (post id, director, "
            "reason, tries). failed-downloads.csv next to it has the same as a table. Old entries of files that "
            "were saved later move to failed-downloads-archive.*.gz (see Advanced > Logging)."))
        self.report_btn = ttk.Button(obar, text="Open latest report", command=self.open_report, state="disabled")
        self.report_btn.pack(side="left", padx=(6, 0))
        self.retry_btn = ttk.Button(bar, text="Retry failed files", command=self.retry_failed)  # 1.8
        self.retry_btn.pack(side="left", padx=(6, 0))
        self._bind_help([self.retry_btn], lambda: (
            "Re-download ONLY the files that failed (403 / 404 / network / md5 ...), nothing else: selects "
            "'Retry failed files only' and starts. Limit it to some reasons or to the posts in a CSV (e.g. "
            "missing.csv) on the Advanced tab. Every failure is logged in failed-downloads.csv / .log in the "
            "archive folder."))
        self.problem_var = tk.StringVar()
        ttk.Label(act, textvariable=self.problem_var, style="Err.TLabel", wraplength=260,
                  justify="right").grid(row=0, column=0, sticky="e", padx=(0, 12))
        self.pause_btn = ttk.Button(act, text="Pause", style="Side.TButton", command=self.toggle_pause,
                                    state="disabled")
        self.pause_btn.grid(row=0, column=1, sticky="ns", padx=(0, 6))
        self.stop_btn = ttk.Button(act, text="Stop", style="Side.TButton", command=self.stop, state="disabled")
        self.stop_btn.grid(row=0, column=2, sticky="ns", padx=(0, 8))
        self.start_btn = ttk.Button(act, text="Start", style="Big.Accent.TButton", command=self.start)
        self.start_btn.grid(row=0, column=3, sticky="nsew")
        for w, t in ((self.save_btn, f"Save these settings to {self.settings_path} (the command line uses them "
                                     "too). The API key is never saved."),
                     (self.reset_btn, "Put every option back to its default (the settings file changes only when "
                                      "you Save)."),
                     (self.start_btn, "Start what is chosen under 'What to do' (Basics tab). Progress and the "
                                      "log appear on the right. Stop any time: pressing Start again later "
                                      "continues where it left off. "
                                      "Shortcut: Ctrl+Enter."),
                     (self.stop_btn, "Stop cleanly: the current file is abandoned (its .part file removed), "
                                     "everything else is saved. Start again to resume."),
                     (self.pause_btn, "Pause before the next request; Resume continues. While downloads wait "
                                      "for disk space, Resume checks the free space again right away."),
                     (self.report_btn, "Open the newest completeness report (archive vs the posts export) in "
                                       "your browser. Written by 'Completeness report' and at the end of the "
                                       "full archive.")):
            self._bind_help([w], lambda t=t: t)
        if not self.embedded:   # the main window routes Ctrl+Enter to the visible tab
            self.root.bind("<Control-Return>", lambda e: self.start())

    # ------------------------------------------------------------------ state
    def mode_key(self) -> str:
        return self.mode_labels.get(self.mode_var.get(), cp.DEFAULT_MODE)

    def get_state(self) -> dict:
        st = {k: v.get() for k, v in self.vars.items()}
        st["mode"] = self.mode_key()
        return st

    def set_state(self, state: dict) -> None:
        for k, v in state.items():
            if k == "mode" and v in cp.MODE:
                self.mode_var.set(cp.MODE[v].label)
            elif k in self.vars:
                var = self.vars[k]
                var.set(bool(v) if isinstance(var, tk.BooleanVar) else ("" if v is None else str(v)))
        sel = set(cp._ratings(self.vars["rating"].get()).split(","))
        for c, bv in self.rating_vars.items():
            bv.set(c in sel)
        kinds = set(str(self.vars["meta_kinds"].get()).split(","))
        for k, bv in self.kind_vars.items():
            bv.set(k in kinds)

    def on_change(self):
        if self._refresh_job:
            self.root.after_cancel(self._refresh_job)
        self._refresh_job = self.root.after(150, self.refresh_now)

    def refresh_now(self):
        """Validation, enabled/disabled options, previews, free space, Start button."""
        self._refresh_job = None
        st = self.get_state()
        mode = cp.MODE[st["mode"]]
        self.mode_help.configure(text=mode.help + ("" if mode.network or mode.cmd == "report"
                                                   else "  (No network requests.)"))
        for h in self.mode_hints.values():
            h.configure(text=f"Mode: {mode.label}. Greyed-out options are not used by this mode "
                             "(change the mode on the Basics tab).")
        self.errors, self.warnings = cp.validate(st)
        for o in cp.OPTIONS:
            on = cp.applies(o, mode)
            for w in self.rows[o.key]:
                try:
                    if isinstance(w, ttk.Label):
                        w.configure(foreground="" if on else COLORS["muted"])
                    elif isinstance(w, ttk.Combobox):
                        w.configure(state=("readonly" if o.kind == "choice" else "normal") if on else "disabled")
                    else:
                        w.state(["!disabled"] if on else ["disabled"])
                except tk.TclError:
                    pass
            e = self.errlabels[o.key]
            if o.key in self.errors:
                e.configure(text=self.errors[o.key])
                e.grid()
            else:
                e.grid_remove()
        if self.runner.running:
            self.mode_cb.state(["disabled"])
            self.browse_out_btn.state(["disabled"])
        else:
            self.mode_cb.state(["!disabled"])
        # command preview
        try:
            line = cp.quote_command(cp.build_command(st))
        except Exception as e:  # never let a preview problem break the window
            line = f"(cannot build the command: {e})"
        line = redact(line, st.get("api_key"))  # the key is never in argv; belt and braces
        self.cmd_text.delete("1.0", "end")
        self.cmd_text.insert("1.0", _bmp_safe(line, self.tk_version))
        # name preview + example
        try:
            rows = cp.preview_paths(st)
            ptxt = []
            for d, p in rows:
                ptxt += [(f"{d}:\n", "desc"), (f"  {p}\n", "")]
            self.example_var.set(rows[0][1])
        except ValueError as e:
            ptxt = [(f"Invalid naming settings: {e}\n", "err")]
            self.example_var.set("(fix the naming settings on the Naming & folders tab)")
        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", "end")
        for t, tag in ptxt:
            self.preview_text.insert("end", _bmp_safe(t, self.tk_version), tag)
        self.preview_text.configure(state="disabled")
        # free space, warnings, UA
        free = cp.free_space(st["out"]) if st["out"] else None
        need = ""
        if mode.key == "full":
            need = " (a full archive needs several hundred GB)"
        self.free_var.set(f"Free space on this drive: {fmt_bytes(free)}{need}" if free is not None else
                          "Free space: unknown (drive not available?)")
        pw = cp.path_warnings(st["out"]) if "out" not in self.errors else []
        if pw:
            self.path_warn.configure(text="\n".join("! " + w for w in pw))
            self.path_warn.grid()
        else:
            self.path_warn.grid_remove()
        try:
            self.ua_var.set(build_user_agent(st["username"] or None, st["user_agent"] or None))
        except ValueError:
            self.ua_var.set("(enter your username on the Basics tab)")
        self.login_hint.configure(text=f"blank = {st['username']}" if st["username"] and not st["login"] else "")
        self._refresh_login_required(st["out"])
        n = len(self.errors)
        self.problem_var.set("" if not n else f"Fix {n} problem{'s' if n > 1 else ''} first (red text)")
        self._update_buttons()
        dirty = st != self.saved_state if hasattr(self, "saved_state") else False
        self.save_btn.configure(text="Save settings *" if dirty else "Save settings")

    def _update_buttons(self):
        running = self.runner.running
        self.start_btn.state(["disabled"] if running or self.errors else ["!disabled"])
        self.stop_btn.state(["!disabled"] if running else ["disabled"])
        self.pause_btn.state(["!disabled"] if running else ["disabled"])
        has_key = bool(self.vars["api_key"].get().strip())
        self.test_btn.state(["disabled"] if running or self._signin_busy or not has_key else ["!disabled"])
        self.report_btn.state(["!disabled"] if self.report_path() else ["disabled"])
        self.faillog_btn.state(["!disabled"] if os.path.isfile(self._failure_log()) else ["disabled"])
        self.retry_btn.state(["disabled"] if running else ["!disabled"])
        if not running:
            self.stop_btn.configure(text="Stop")
            self.pause_btn.configure(text="Pause")
            self._idle_dashboard()

    # ------------------------------------------------------------------ actions
    def browse(self, o: cp.Opt):
        cur = self.vars[o.key].get()
        if o.kind == "dir":
            init = cur if cur and os.path.isdir(cp._abs_out(cur)) else str(Path.home())
            p = filedialog.askdirectory(parent=self.root, title="Choose the archive folder", initialdir=init,
                                        mustexist=False)
        elif o.kind == "savefile":
            p = filedialog.asksaveasfilename(parent=self.root, title="Log file", defaultextension=".log",
                                             initialfile="packrat-archive.log",
                                             filetypes=[("Log files", "*.log"), ("All files", "*.*")])
        else:
            p = filedialog.askopenfilename(parent=self.root, title=o.label,
                                           filetypes=[("Gzip CSV", "*.csv.gz"), ("All files", "*.*")])
        if p:
            self.vars[o.key].set(os.path.normpath(p))

    def save(self):
        self._remember_key()
        try:
            p = cp.save_settings({**self.get_state(), "theme": self.theme_choice, "notify": self.notify_var.get()},
                                 self.settings_path)
        except OSError as e:
            messagebox.showerror("Save settings", f"Could not save {self.settings_path}:\n{e}", parent=self.root)
            return
        self.saved_state = self.get_state()
        self.set_message(f"Settings saved to {p}", "ok")
        self.refresh_now()

    def reset(self):
        if not messagebox.askyesno("Reset to defaults", "Put every option back to its default?\n\nYour settings "
                                   "file is only changed when you press Save settings.", parent=self.root):
            return
        keep = {k: self.vars[k].get() for k in ("username", "out")}
        self.set_state(dict(cp.DEFAULTS, mode=cp.DEFAULT_MODE, **keep))
        self.set_message("Options reset to defaults (username and archive folder kept). Not saved yet.", "info")

    def open_path(self, p: str):
        try:
            if os.name == "nt":
                os.startfile(p)  # noqa: S606 (opens Explorer / the default viewer)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", p])
            else:
                subprocess.Popen(["xdg-open", p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            messagebox.showerror("Open", f"Could not open {p}:\n{e}", parent=self.root)

    def open_out(self):
        p = cp._abs_out(self.vars["out"].get())
        if not os.path.isdir(p):
            messagebox.showinfo("Open archive folder", f"{p}\n\ndoes not exist yet; it is created by the first run.",
                                parent=self.root)
            return
        self.open_path(p)

    def report_path(self) -> str | None:
        """1.7: the report of the last run (its "report" event), else the newest one in the archive folder."""
        p = (self.model.report or {}).get("html")
        if p and os.path.isfile(p):
            return p
        try:
            found = latest_report(cp._abs_out(self.vars["out"].get()))
        except (OSError, ValueError):
            return None
        return str(found) if found else None

    def retry_failed(self):
        """1.8: the 'Retry failed files' button = choose that mode and Start."""
        if self.runner.running:
            return
        self.mode_var.set(cp.MODE["retry-failed"].label)
        self.refresh_now()
        self.start()

    # ---- 1.9 -------------------------------------------------------------------------------------
    def _failure_log(self) -> str:
        return str(cp.failure_log_path(self.vars["out"].get()))

    def open_failure_log(self):
        p = self._failure_log()
        if not os.path.isfile(p):
            messagebox.showinfo("Open failure log", f"{p}\n\ndoes not exist: no download has failed in this "
                                "archive folder (yet).", parent=self.root)
            return
        self.open_path(p)

    def _archive_status(self, force=False):
        out = cp._abs_out(self.vars["out"].get())
        if force or self._dash_cache[0] != out:
            self._dash_cache = (out, cp.archive_status(out))
        return self._dash_cache[1]

    def _idle_dashboard(self):
        """When no run is going: the whole-archive totals from the database (no rates / ETA)."""
        if not hasattr(self, "dbar") or (self.model.archive and self.model.done):
            return
        st = self._archive_status()
        if not st:
            for k in ("dash_files", "dash_saved", "dash_rate", "dash_run", "dash_failed", "dash_eta", "dash_disk"):
                self.pv[k].set("")
            self.pv["dash_files"].set("No archive in this folder yet")
            self.dbar["value"] = 0
            return
        m = GuiModel()
        m.apply("archive", st)
        d = m.dashboard()
        for k in ("dash_files", "dash_saved", "dash_failed"):
            self.pv[k].set(d[k])
        self.pv["dash_failed"].set(f"{st['failed_files']:,} to retry")
        self.pv["dash_rate"].set("--")
        self.pv["dash_run"].set("--")
        left = max(0, st["expected_files"] - st["done_files"] - st["no_url_files"])
        self.pv["dash_eta"].set(f"{left:,} files ({fmt_bytes(st['pending_bytes'] + st['failed_bytes'])}) left"
                                if left else "nothing left to download")
        self.pv["dash_disk"].set("")
        self.dbar["value"] = d["dash_frac"] * 1000

    def _disk_ok_to_start(self, st: dict, mode) -> bool:
        """Before a download run: warn when the files still to download will not fit above the reserve."""
        status = self._archive_status(force=True)
        need = cp.remaining_bytes(status, mode.cmd, st.get("limit_gb"))
        ok, msg = cp.disk_check(need, cp.free_space(st["out"]) if st["out"] else None, st.get("min_free_gb"))
        if ok:
            return True
        self.log_line("Disk space: " + msg.split("\n")[0], logging.WARNING)
        return messagebox.askyesno("Not enough free disk space", msg + "\n\nStart anyway?", icon="warning",
                                   default="no", parent=self.root)

    def _on_disk_event(self, kind: str):
        if kind == "disk_low":
            self._set_status("Paused: low disk space", "warn")
            self.pause_btn.configure(text="Resume")
            self.set_message("Downloads are paused because the drive is nearly full. Free some space: they "
                             "continue by themselves (or press Resume to check now).", "warn")
            if not self._disk_notified:
                self._disk_notified = True
                d = self.model.disk_low or {}
                self.notify("Packrat: paused - disk almost full",
                            f"{fmt_bytes(d.get('free'))} free on the archive drive. Free some space; downloads "
                            "continue by themselves.", popup=False, urgent=True)
        else:
            self._disk_notified = False
            self.pv["dash_disk"].set("")
            if not self.paused:
                self.pause_btn.configure(text="Pause")
                self._set_status("Running", "ok")
            self.set_message("Enough disk space again: downloads continue.", "ok")

    def notify(self, title: str, body: str, *, popup=True, urgent=False):
        if not self.notify_var.get():
            return None
        how = notifier.notify(title, body, urgent=urgent)
        if how is None and popup:
            self.popup(title, body)
            how = "panel"
        if how:
            self.log_line(f"Notification ({how}): {title}")
        return how

    def notify_end(self, code, crashed, popup=True):
        mode = self.started_mode
        if mode is None:
            return None
        long_run = self.run_started is not None and time.monotonic() - self.run_started >= NOTIFY_MIN_S
        if not (mode.network or long_run):
            return None
        m = self.model
        summary = f"{m.ok_files:,} file(s) saved ({fmt_bytes(m.ok_bytes)})" if m.archive else ""
        if m.failed_files:
            summary += f", {m.failed_files:,} failed" if summary else f"{m.failed_files:,} file(s) failed"
        n = cp.notification_for(code, crashed, mode.label, summary)
        if not n:
            return None
        return self.notify(*n, popup=popup, urgent=code != 0)

    def popup(self, title: str, body: str):
        """Small themed, non-modal window (when the system has no notifications)."""
        w = tk.Toplevel(self.root, background=COLORS["bg"])
        w.title(title)
        w.transient(self.root)
        w.resizable(False, False)
        fr = ttk.Frame(w, padding=(14, 12))
        fr.pack(fill="both", expand=True)
        ttk.Label(fr, text=title, font=self.f_bold, wraplength=380, justify="left").pack(anchor="w")
        ttk.Label(fr, text=body, wraplength=380, justify="left").pack(anchor="w", pady=(6, 10))
        ttk.Button(fr, text="OK", command=w.destroy).pack(anchor="e")
        w.update_idletasks()
        x = self.root.winfo_rootx() + max(0, self.root.winfo_width() - w.winfo_reqwidth() - 30)
        y = self.root.winfo_rooty() + max(0, self.root.winfo_height() - w.winfo_reqheight() - 60)
        w.geometry(f"+{x}+{y}")
        self._popup = w
        return w

    def check_resume(self):
        """At launch: offer one-click Resume when the last download run in this archive did not finish."""
        try:
            status = self._archive_status(force=True)
            ui = cp.load_ui_state()
            offer = cp.resume_offer(status, dismissed=ui.get("resume_dismissed"))
        except Exception as e:  # noqa: BLE001 - never block the window on this
            self.log_line(f"(could not check for an unfinished run: {e})", logging.DEBUG)
            return None
        self.resume_info = offer
        if offer:
            self.banner_var.set(offer["text"])
            self.banner.grid()
            self.log_line("Unfinished run found: " + offer["text"], logging.WARNING)
        return offer

    def check_previous_install(self):
        """2.0: a new copy with no settings file and no archive yet: offer the settings of an earlier install."""
        try:
            if self.settings_path.is_file() or self._has_archive(self.vars["out"].get()):
                return None
            found = cp.find_previous_installs()
        except Exception as e:  # noqa: BLE001 - never block the window on this
            self.log_line(f"(could not look for an earlier install: {e})", logging.DEBUG)
            return None
        if not found:
            return None
        f = found[0]
        self.import_found = f["dir"]
        what = "settings and an archive" if f["ini"] and f["has_db"] else ("an archive" if f["has_db"] else
                                                                           "settings")
        self.imp_var.set(f"Earlier e6ai-archiver found in {f['dir']} ({what}; archive folder: {f['out']}). "
                         "Import its settings to keep archiving into the same folder? Nothing is downloaded "
                         "again. Stop the old version first if it is still running.")
        self.imp_banner.grid()
        self.log_line("Earlier install found: " + f["dir"])
        return f

    @staticmethod
    def _wrap_banner(frame, label):
        """2.0: wrap a banner's text in the room the buttons leave (it was cut off at the right edge)."""
        def fit(_e=None):
            used = sum(w.winfo_reqwidth() + 16 for w in frame.grid_slaves(row=0) if w is not label)
            label.configure(wraplength=max(240, frame.winfo_width() - used - 24))
        frame.bind("<Configure>", fit, add="+")

    def hide_import_banner(self):
        self.imp_banner.grid_remove()

    @staticmethod
    def _has_archive(out) -> bool:
        from .siteprofile import find_db
        return find_db(cp._abs_out(out))[0] is not None

    def import_settings(self, src=None):
        if self.runner.running:
            self.set_message("Stop the run first, then import settings.", "warn")
            return None
        if not src:
            init = self.import_found or ("C:\\" if os.name == "nt" else str(Path.home()))
            src = filedialog.askdirectory(parent=self.root, initialdir=init, mustexist=True,
                                          title="Choose the earlier e6ai-archiver folder (it has "
                                                "e6ai-archiver.ini or an 'archive' folder)")
            if not src:
                return None
        try:
            res = cp.import_previous_install(src, self.settings_path)
        except (OSError, ValueError) as e:
            messagebox.showerror("Import settings", str(e), parent=self.root)
            return None
        state, notes = cp.load_settings(self.settings_path)
        self.set_state(state)
        self.saved_state = self.get_state()
        self._dash_cache = (None, None)
        self.hide_import_banner()
        for n in notes + res["notes"]:
            self.log_line(n, logging.WARNING)
        self.log_line(f"Imported settings from {res['dir']} into {res['dest']}"
                      + (f" (previous file kept as {res['backup']})" if res["backup"] else ""))
        self.set_message(f"Settings imported. Archive folder: {res['out']}. Press Start to continue where the "
                         "earlier version stopped.", "ok")
        self.refresh_now()
        self.check_resume()
        return res

    def hide_banner(self, forget=False):
        if forget and self.resume_info:
            cp.save_ui_state({"resume_dismissed": self.resume_info["started"]})
        self.banner.grid_remove()

    def resume_last(self):
        """Resume = the settings of the interrupted run (saved by Start, never the API key), else the
        current ones with the matching mode; then Start."""
        offer = self.resume_info
        if not offer or self.runner.running:
            return
        last = cp.load_ui_state().get("last_run_panel") or {}
        cur_out = cp._abs_out(self.vars["out"].get())
        if isinstance(last.get("state"), dict) and last.get("out") == cur_out and \
                cp.MODE.get(last.get("mode"), cp.MODE[offer["mode"]]).cmd == offer["cmd"]:
            st = dict(last["state"])
            st["mode"] = "update" if last.get("mode") == "test" else last.get("mode")
            st.pop("api_key", None)
            self.set_state(st)
            self.log_line("Resuming with the settings of the interrupted run.")
        else:
            self.mode_var.set(cp.MODE[offer["mode"]].label)
            self.log_line("Resuming with the current settings (" + cp.MODE[offer["mode"]].label + ").")
        self.hide_banner()
        self.refresh_now()
        self.start()

    def open_report(self):
        p = self.report_path()
        if not p:
            messagebox.showinfo("Open report", "No completeness report yet. Choose 'Completeness report' in "
                                "'What to do' and Start (no network), or run the full archive.", parent=self.root)
            return
        self.open_path(p)

    def current_log_path(self) -> str:
        st = self.get_state()
        return os.path.abspath(st["log_file"]) if st["log_file"].strip() else cp.default_log_file(st["out"])

    def open_log(self):
        p = self.log_path or self.current_log_path()
        if not os.path.isfile(p):
            messagebox.showinfo("Open log", f"{p}\n\ndoes not exist yet; it is written by the next run.",
                                parent=self.root)
            return
        self.open_path(p)

    def copy_command(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.cmd_text.get("1.0", "end").strip())
        self.set_message("Command copied to the clipboard.", "info")

    def copy_log(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.log.get("1.0", "end"))

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def set_message(self, text: str, kind: str = "info"):
        self.msg_var.set(text)
        self._msg_kind = kind
        self.msg_lbl.configure(foreground=COLORS.get(kind, COLORS["info"]))

    def log_line(self, line: str, level: int = logging.INFO, tag: str | None = None):
        tag = tag or ("err" if level >= logging.ERROR else "warn" if level >= logging.WARNING else "")
        key = self.vars["api_key"].get() if "api_key" in self.vars else ""
        line = redact(line, key)  # the child masks it too; never show the key in the log pane
        at_end = self.log.yview()[1] >= 0.999
        self.log.configure(state="normal")
        self.log.insert("end", _bmp_safe(line, self.tk_version) + "\n", tag)
        n = int(self.log.index("end-1c").split(".")[0])
        if n > LOG_MAX_LINES:
            self.log.delete("1.0", f"{n - LOG_MAX_LINES}.0")
        self.log.configure(state="disabled")
        if at_end:
            self.log.see("end")

    # ------------------------------------------------------------------ run control
    def start(self):
        if self.runner.running:
            return  # no double start
        self.refresh_now()
        st = self.get_state()
        mode = cp.MODE[st["mode"]]
        if self.errors:
            key = next(iter(self.errors))
            tab = cp.OPT[key].tab if key in cp.OPT else cp.TABS[0]
            if key in ("username", "out", "rate", "min_free_gb"):
                tab = cp.TABS[0]
            self.nb.select(cp.TABS.index(tab))
            self.set_message(f"Not started: {cp.OPT[key].label if key in cp.OPT else key}: {self.errors[key]}",
                             "error")
            return
        if self.last_exit == 3 and mode.network:
            if not messagebox.askyesno("Last run was stopped by a 403", "The site answered 403 Forbidden last "
                                       "time. Starting again right away is not recommended: check your username / "
                                       "User-Agent / API key first, or wait.\n\nStart anyway?", icon="warning",
                                       default="no", parent=self.root):
                return
        if mode.confirm and not messagebox.askyesno(mode.label, mode.confirm, parent=self.root,
                                                    icon="warning" if mode.key != "full" else "question",
                                                    default="yes" if mode.key == "full" else "no"):
            return
        if mode.network and mode.cmd in cp.RESUME_MODE and mode.key != "test" and not self._disk_ok_to_start(st, mode):
            return
        self._remember_key()
        argv = cp.build_command(st)
        self.log_path = argv[argv.index("--log-file") + 1]
        self.model = GuiModel()
        self.model.apply("phase", {"name": "starting"})
        self.log_line("")
        self.log_line(f"> {cp.quote_command(argv)}", tag="cmd")
        try:
            self.runner.start(argv, env=cp.child_env(st))
        except OSError as e:
            self.set_message(f"Could not start the archiver: {e}", "error")
            self.log_line(f"could not start: {e}", logging.ERROR)
            return
        self.started_mode = mode
        self.paused = False
        self._disk_notified = False
        self.run_started = time.monotonic()
        self.hide_banner()
        if mode.cmd in cp.RESUME_MODE:   # what one-click Resume uses after an interruption
            safe = {k: v for k, v in st.items() if k != "api_key"}
            cp.save_ui_state({"last_run_panel": {"state": safe, "mode": mode.key, "out": cp._abs_out(st["out"]),
                                                 "started": time.strftime("%Y-%m-%dT%H:%M:%S")}})
        self._set_status("Running", "ok")
        self.set_message(f"Running: {mode.label}. You can keep using the window; Stop saves progress.", "info")
        self.refresh_now()

    def stop(self):
        if not self.runner.running:
            return
        r = self.runner
        if r.stop_requested_at is None:
            r.stop()
            self._set_status("Stopping...", "warn")
            self.set_message("Stopping cleanly (finishing the current step; at most a few seconds)...", "warn")
            self.log_line("Stop requested.", logging.WARNING)
        elif time.monotonic() - r.stop_requested_at >= cp.FORCE_STOP_AFTER_S:
            if messagebox.askyesno("Force stop", "The run has not stopped yet. End it immediately?\n\nIt is still "
                                   "resumable (unfinished downloads are kept as .part files and redone).",
                                   icon="warning", default="no", parent=self.root):
                r.kill()
                self.log_line("Force stop.", logging.WARNING)

    def toggle_pause(self):
        if not self.runner.running:
            return
        if self.model.disk_low and not self.paused:    # 1.9: "Resume" = check the free space again now
            self.runner.recheck_disk("run")
            self.log_line("Checking the free disk space again...")
            return
        if self.paused:
            self.runner.resume()
            self.pause_btn.configure(text="Pause")
            self._set_status("Running", "ok")
        else:
            self.runner.pause()
            self.pause_btn.configure(text="Resume")
            self._set_status("Paused", "warn")
        self.paused = not self.paused

    def poll(self):
        try:
            for _ in range(5000):
                try:
                    msg = self.runner.q.get_nowait()
                except Exception:
                    break
                if msg[0] == "event":
                    self.model.apply(msg[1], msg[2] if isinstance(msg[2], dict) else {})
                    if msg[1] in ("disk_low", "disk_ok"):
                        self._on_disk_event(msg[1])
                elif msg[0] == "line":
                    line, level = msg[1], msg[2]
                    self.log_line(line, level)
                    if level >= logging.WARNING:
                        self.model.apply("log", {"msg": line, "level": level})
                        self.model.new_log.clear()
                elif msg[0] == "exit":
                    self.on_exit(msg[1], msg[2])
            r = self.runner
            if r.running and r.stop_requested_at is not None and \
                    time.monotonic() - r.stop_requested_at >= cp.FORCE_STOP_AFTER_S:
                self.stop_btn.configure(text="Force stop")
        finally:
            if not self.destroyed:
                self.root.after(POLL_MS, self.poll)

    def on_exit(self, code, crashed):
        self.last_exit = code
        self.model.apply("done", {"code": code})
        kind, text = cp.exit_message(code, crashed)
        self.set_message(text, kind)
        self._set_status({"ok": "Finished", "stopped": "Stopped", "forbidden": "Stopped: access denied (403)"}
                         .get(kind, "Stopped: error"),
                         kind if kind in COLORS else "error")
        self.log_line(f"[exit code {code}] {text}", logging.INFO if kind in ("ok", "stopped") else logging.ERROR)
        self._show_snapshot()
        self._refresh_login_required(self.get_state()["out"], force=True)
        self.refresh_now()
        self._dash_cache = (None, None)
        if self.close_pending:
            self.destroy()
            return
        self.notify_end(code, crashed, popup=kind not in ("forbidden", "error") or code == 2)
        if kind == "forbidden":
            messagebox.showwarning("Stopped on 403", text, parent=self.root)
        elif kind == "error" and code not in (2,):
            messagebox.showerror("Run ended with an error", text, parent=self.root)

    def refresh_progress(self):
        if self.destroyed:
            return
        if self.runner.running:
            self._show_snapshot()
        self.root.after(REFRESH_MS, self.refresh_progress)

    def _show_snapshot(self):
        s = self.model.snapshot()
        if not self.runner.running and not self.model.done:
            s.update(phase="Not running", overall_text="", tier_text="", file_head="", speed="--", avg="--",
                     eta="--", elapsed="--", last_error="")
        for k, v in self.pv.items():
            val = s.get(k, "")
            if k == "last_error":
                val = "" if val in ("none", "") else "Last problem: " + str(val)
            v.set(_bmp_safe(str(val), self.tk_version))
        if self.runner.running or self.model.archive:
            self.dbar["value"] = s.get("dash_frac", 0.0) * 1000
            if self.model.disk_low:
                d = self.model.disk_low
                self.pv["dash_disk"].set(
                    f"Paused: low disk space - {fmt_bytes(d.get('free'))} free, the next file needs "
                    f"{fmt_bytes(d.get('need'))} + {fmt_bytes(d.get('reserve'))} kept free. Free some space (or "
                    f"lower 'Keep free on the drive'); downloads continue by themselves - or press Resume.")
        self.bar["value"] = s["overall"] * 1000
        self.tbar["value"] = s["tier_frac"] * 1000
        self.fbar["value"] = s["file_frac"] * 1000

    # ------------------------------------------------------------------ closing
    def on_close(self):
        if self.runner.running:
            if not messagebox.askyesno("A run is in progress", "Stop the run cleanly and close the window when it "
                                       "has stopped?\n\nProgress is saved; Start again later to resume.",
                                       icon="warning", parent=self.root):
                return
            self.close_pending = True
            self.stop()
            return
        if self.get_state() != self.saved_state:
            ans = messagebox.askyesnocancel("Save settings?", "Save the changed settings before closing?",
                                            parent=self.root)
            if ans is None:
                return
            if ans:
                self.save()
        self.destroy()

    def destroy(self):
        try:
            ui = {"tab": self.nb.index("current"), "sash": self.paned.sashpos(0)}
            if not self.embedded:
                ui["geometry"] = self.root.geometry()
            cp.save_ui_state(ui)   # merged: keeps last_run_panel / dismissals
        except tk.TclError:
            pass
        if self.embedded:
            self.destroyed = True
            if self.on_closed:
                self.on_closed()
            return
        self.destroyed = True
        try:
            for after_id in self.root.tk.splitlist(self.root.tk.call("after", "info")):
                self.root.after_cancel(after_id)   # no timer may fire into a destroyed window
        except tk.TclError:
            pass
        self.root.destroy()

    def _callback_error(self, exc, val, tb):
        import traceback
        self.log_line("window error: " + "".join(traceback.format_exception_only(exc, val)).strip(), logging.ERROR)


def place_window(root, ui: dict) -> None:
    """Restore the saved size if it fits the screen; else a DPI-scaled default that fits (1280x800 too)."""
    scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.minsize(min(int(MIN_SIZE[0] * scale), sw - 40), min(int(MIN_SIZE[1] * scale), sh - 80))
    g = ui.get("geometry", "")
    try:
        size, _, rest = g.partition("+")
        w, h = (int(x) for x in size.split("x"))
        if w <= sw and h <= sh:
            root.geometry(g if rest and all(0 <= int(x) < m for x, m in zip(rest.split("+"), (sw, sh))) else size)
            return
    except ValueError:
        pass
    w, h = min(int(DEFAULT_SIZE[0] * scale), sw - 40), min(int(DEFAULT_SIZE[1] * scale), sh - 80)
    root.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")


def make_root():
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # sharp text; Tk then scales fonts by the DPI
        except Exception:
            pass
    return tk.Tk()


def run_panel(settings_path: Path, prefill: dict | None = None, on_ready=None) -> int:
    try:
        root = make_root()
    except tk.TclError as e:
        print(f"The control panel needs a display ({e}). Use the command line instead: packrat --help",
              file=sys.stderr)
        return 1
    ui = cp.load_ui_state()
    root.withdraw()                      # 1.9: no white flash: show the window once it is themed
    try:
        root.configure(background=th.palette(cp.load_settings(Path(settings_path))[0].get("theme"))["bg"])
    except Exception:  # noqa: BLE001
        pass
    place_window(root, ui)
    panel = ControlPanel(root, settings_path, prefill)
    root.deiconify()
    try:
        panel.nb.select(int(ui.get("tab", 0)))
        def place_sash():
            root.update_idletasks()
            width = panel.paned.winfo_width()
            if width < 200:                      # not laid out yet
                root.after(50, place_sash)
                return
            want = int(ui.get("sash") or 0) or int(width * 0.55)   # options side gets a little more room
            panel.paned.sashpos(0, min(max(want, int(width * 0.3)), int(width * 0.75)))
        root.after(50, place_sash)
    except (tk.TclError, ValueError):
        pass
    if on_ready:
        on_ready(panel)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        if panel.runner.running:
            panel.runner.stop()
            panel.runner.proc.wait(60)
    return 0
