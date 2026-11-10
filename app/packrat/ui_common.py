"""Shared pieces of Packrat's main window: background work, thumbnails, the tag-completing search field,
the site chooser and the site options dialog."""
from __future__ import annotations

import hashlib
import io
import logging
import queue
import sys
import tkinter as tk
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from tkinter import messagebox, ttk

from . import paths
from . import theme as th
from .uikit import (DARK_TAG, FIELD_LABELS, SECRET_IDS, TYPE_TO_COLOR_KEY, _install_and_add,  # noqa: F401
                    _registry_match, auth_fields, auth_label, friendly, open_path, open_url, reveal, tag_color)

log = logging.getLogger("packrat")
COLORS = th.PALETTES["light"].copy()   # current palette (the window updates it on theme changes)
SCALE = [1.0]                          # display scaling (1.0 = 96 dpi); set by the window at start


def px(n: float) -> int:
    """Pixels at the current display scaling (Windows 150 % / 200 % ...)."""
    return int(round(n * SCALE[0]))


def set_scale(root) -> None:
    try:
        SCALE[0] = max(1.0, float(root.winfo_fpixels("1i")) / 96.0)
    except tk.TclError:
        SCALE[0] = 1.0


def fit_columns(tree, base: dict) -> None:
    """Keep a list's columns inside its width: they grow and shrink together (base = relative widths)."""
    total = float(sum(base.values())) or 1.0

    def fit(e=None):
        w = (e.width if e is not None else tree.winfo_width()) - px(4)
        if w < 50:
            return
        for col, b in base.items():
            tree.column(col, width=max(px(40), int(w * b / total)))
    tree.bind("<Configure>", fit, add="+")


def copy_image(pil) -> bool:
    """Put a picture on the Windows clipboard (as a bitmap, so any program can paste it)."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes
    try:
        out = io.BytesIO()
        pil.convert("RGB").save(out, "BMP")
        data = out.getvalue()[14:]           # CF_DIB: the bitmap without its file header
        k, u = ctypes.windll.kernel32, ctypes.windll.user32
        k.GlobalAlloc.restype, k.GlobalAlloc.argtypes = wintypes.HGLOBAL, [wintypes.UINT, ctypes.c_size_t]
        k.GlobalLock.restype, k.GlobalLock.argtypes = ctypes.c_void_p, [wintypes.HGLOBAL]
        k.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        k.GlobalFree.argtypes = [wintypes.HGLOBAL]
        u.OpenClipboard.argtypes = [wintypes.HWND]
        u.SetClipboardData.restype, u.SetClipboardData.argtypes = wintypes.HANDLE, [wintypes.UINT, wintypes.HANDLE]
        h = k.GlobalAlloc(0x0002, len(data))          # GMEM_MOVEABLE
        if not h:
            return False
        p = k.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        k.GlobalUnlock(h)
        if not u.OpenClipboard(None):
            k.GlobalFree(h)
            return False
        try:
            u.EmptyClipboard()
            if not u.SetClipboardData(8, h):           # CF_DIB; the clipboard owns the memory from here on
                k.GlobalFree(h)
                return False
        finally:
            u.CloseClipboard()
        return True
    except Exception:  # noqa: BLE001
        return False


def size_for(win, w: int, h: int) -> str:
    """'WxH' for a window: the size asked for (already scaled), never bigger than the screen."""
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    return f"{min(w, sw - 40)}x{min(h, sh - 90)}"


# ---- simple / advanced view ---------------------------------------------------------------------------
ADVANCED = [False]                     # the window sets it from the saved choice
_ADV: list = []                        # (widget, show, hide)


def advanced_only(*widgets) -> None:
    """These widgets only show in the advanced view. Call after placing them (grid or pack); switching back
    puts them where they were."""
    for w in widgets:
        mgr = w.winfo_manager()
        if mgr == "grid":
            entry = (w, w.grid, w.grid_remove)
        elif mgr == "pack":
            info = w.pack_info()
            order = list(w.master.pack_slaves())

            def show(w=w, info=info, order=order):
                if w.winfo_manager():
                    return
                packed = {str(o) for o in w.master.pack_slaves()}
                i = order.index(w) if w in order else len(order)
                nxt = next((o for o in order[i + 1:] if str(o) in packed), None)
                w.pack(**info, **({"before": nxt} if nxt is not None else {}))
            entry = (w, show, w.pack_forget)
        else:
            continue
        _ADV.append(entry)
        if not ADVANCED[0]:
            entry[2]()


def apply_advanced(on: bool) -> None:
    ADVANCED[0] = bool(on)
    keep = []
    for entry in _ADV:
        w, show, hide = entry
        try:
            if not w.winfo_exists():
                continue
            (show if on else hide)()
            keep.append(entry)
        except tk.TclError:
            continue
    _ADV[:] = keep


def tip(widget, text) -> None:
    """A tooltip (text or a function returning text)."""
    from .panel_ui import Tooltip
    Tooltip(widget, text if callable(text) else (lambda: text))


PLACEHOLDERS: list = []   # refresh functions (theme changes)


def refresh_placeholders() -> None:
    for fn in list(PLACEHOLDERS):
        try:
            fn()
        except tk.TclError:
            PLACEHOLDERS.remove(fn)


def add_placeholder(entry, text: str, var=None) -> None:
    """Grey example text inside an empty entry (a label on top: the entry's value stays empty)."""
    var = var or entry.cget("textvariable")
    if isinstance(var, str):
        name = var
        var = tk.StringVar(master=entry, name=name)
    lab = tk.Label(entry, text=text, anchor="w", padx=0, pady=0, borderwidth=0, cursor="xterm")

    def update(*_):
        try:
            if var.get():
                lab.place_forget()
            else:
                lab.configure(background=COLORS["field"], foreground=COLORS["muted"])
                lab.place(x=4, rely=0.5, anchor="w")
        except (tk.TclError, KeyError):
            pass
    lab.bind("<Button-1>", lambda e: entry.focus_set())
    entry.bind("<FocusIn>", update, add="+")
    entry.bind("<FocusOut>", update, add="+")
    var.trace_add("write", update)
    entry.after(50, update)
    PLACEHOLDERS.append(update)


class PlaceholderEntry(ttk.Entry):
    """An entry with grey example text while it is empty."""

    def __init__(self, master, placeholder: str, textvariable=None, **kw):
        self.var = textvariable or tk.StringVar()
        super().__init__(master, textvariable=self.var, **kw)
        add_placeholder(self, placeholder, self.var)

    def value(self) -> str:
        return self.var.get()


# ---- background work -----------------------------------------------------------------------------
class Worker:
    """Run functions in threads; their results come back on the Tk thread (never touch Tk from a thread)."""

    def __init__(self, root, threads: int = 6):
        self.root = root
        self.pool = ThreadPoolExecutor(threads, thread_name_prefix="ui")
        self.q: queue.Queue = queue.Queue()
        self.closed = False
        root.after(40, self._drain)

    def submit(self, fn, *args, on_done=None, on_error=None, **kw):
        def run():
            try:
                res = fn(*args, **kw)
            except Exception as e:  # noqa: BLE001
                log.debug("background task failed", exc_info=True)
                if on_error:
                    self.q.put((on_error, e))
                else:
                    self.q.put((_log_error, e))
                return
            if on_done:
                self.q.put((on_done, res))
        return self.pool.submit(run)

    def call(self, fn, *args):
        """From any thread: run fn(*args) on the Tk thread soon."""
        self.q.put((lambda a: fn(*a), args))

    def _drain(self):
        if self.closed:
            return
        n = 0
        while n < 200:
            try:
                fn, arg = self.q.get_nowait()
            except queue.Empty:
                break
            n += 1
            try:
                fn(arg)
            except tk.TclError:
                pass   # a widget closed in the meantime
            except Exception:  # noqa: BLE001
                log.exception("window update failed")
        self.root.after(30 if n else 60, self._drain)

    def close(self):
        self.closed = True
        self.pool.shutdown(wait=False, cancel_futures=True)


def _log_error(e):
    log.warning("%s", e)


# ---- thumbnails ------------------------------------------------------------------------------
class Thumbs:
    """Preview images: memory (LRU) -> disk cache -> download; Pillow scales them in a thread and the Tk
    image is made on the Tk thread."""

    def __init__(self, worker: Worker, size: int = 150, crop: bool = False):
        self.w = worker
        self.size = size
        self.crop = crop
        self.mem: OrderedDict = OrderedDict()
        self.max_mem = 600
        self.dir = paths.cache_dir("thumbs")
        self.pool = ThreadPoolExecutor(4, thread_name_prefix="thumb")

    def key(self, url: str) -> str:
        return hashlib.sha1(url.encode("utf-8", "replace")).hexdigest()

    def get(self, site, img, callback):
        """callback(photo or None) on the Tk thread."""
        url = img.preview_url or img.sample_url or ""
        if not url:
            callback(None)
            return
        rect = tuple(getattr(img, "preview_rect", ()) or ())
        k = f"{self.key(url)}-{self.size}-{int(self.crop)}" + ("-" + "_".join(map(str, rect)) if rect else "")
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
                if rect:      # one picture of a sprite sheet (Grabber crops the same way)
                    x, y, w, h = rect
                    im = im.crop((max(0, x), max(0, y), min(im.width, x + w), min(im.height, y + h)))
            except Exception:  # noqa: BLE001 - not an image (a video preview link, an error page)
                return None
            s = self.size
            if self.crop:
                w, h = im.size
                m = min(w, h)
                im = im.crop(((w - m) // 2, (h - m) // 2, (w - m) // 2 + m, (h - m) // 2 + m))
                im = im.resize((s, s), PI.LANCZOS)
            else:
                im.thumbnail((s, s), PI.LANCZOS)
            return im

        def done(fut):
            try:
                pil = fut.result()
            except Exception:  # noqa: BLE001
                pil = None
            self.w.call(self._make, k, pil, callback)
        self.pool.submit(work).add_done_callback(done)

    def _make(self, k, pil, callback):
        if pil is None:
            callback(None)
            return
        from PIL import ImageTk
        ph = ImageTk.PhotoImage(pil)
        self.mem[k] = ph
        while len(self.mem) > self.max_mem:
            self.mem.popitem(last=False)
        callback(ph)

    def close(self):
        self.pool.shutdown(wait=False, cancel_futures=True)


# ---- tag colors --------------------------------------------------------------------------------


# ---- search field with tag completion -----------------------------------------------------------
class TagEntry(ttk.Entry):
    """An entry that completes the word under the cursor from: tags seen in searches, favorites, the
    sites' tag databases. Up/Down choose, Tab/Enter accept, Esc closes."""

    def __init__(self, master, completer, on_enter=None, **kw):
        self.var = kw.pop("textvariable", None) or tk.StringVar()
        super().__init__(master, textvariable=self.var, **kw)
        self.completer = completer
        self.on_enter = on_enter
        self.pop = None
        self.lb = None
        self._job = None
        self.bind("<KeyRelease>", self._key)
        self.bind("<Down>", self._down)
        self.bind("<Up>", self._up)
        self.bind("<Tab>", self._accept)
        self.bind("<Return>", self._enter)
        self.bind("<Escape>", lambda e: self._close())
        self.bind("<FocusOut>", lambda e: self.after(150, self._close))

    def _word(self):
        text = self.var.get()
        pos = self.index("insert")
        start = text.rfind(" ", 0, pos) + 1
        end = text.find(" ", pos)
        end = len(text) if end < 0 else end
        return text[start:end], start, end

    def _key(self, e):
        if e.keysym in ("Down", "Up", "Return", "Tab", "Escape", "Left", "Right", "Shift_L", "Shift_R",
                        "Control_L", "Control_R"):
            return
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(120, self._suggest)

    def _suggest(self):
        self._job = None
        w, _, _ = self._word()
        prefix = w.lstrip("-~")
        if len(prefix) < 2:
            self._close()
            return
        words = self.completer(prefix)
        if not words:
            self._close()
            return
        if self.pop is None:
            self.pop = tk.Toplevel(self)
            self.pop.wm_overrideredirect(True)
            self.lb = tk.Listbox(self.pop, height=8, activestyle="dotbox", exportselection=False,
                                 background=COLORS["field"], foreground=COLORS["fg"],
                                 selectbackground=COLORS["select_bg"], selectforeground=COLORS["select_fg"],
                                 highlightthickness=1, borderwidth=0)
            self.lb.pack(fill="both", expand=True)
            self.lb.bind("<ButtonRelease-1>", lambda e: self._accept())
        self.lb.delete(0, "end")
        for wd in words[:30]:
            self.lb.insert("end", wd)
        self.lb.selection_clear(0, "end")
        x, y = self.winfo_rootx(), self.winfo_rooty() + self.winfo_height()
        self.pop.geometry(f"{max(260, self.winfo_width() // 2)}x{min(8, len(words)) * 20 + 4}+{x}+{y}")
        self.pop.lift()

    def _down(self, e):
        if self.lb is None:
            return None
        cur = self.lb.curselection()
        i = (cur[0] + 1) if cur else 0
        if i < self.lb.size():
            self.lb.selection_clear(0, "end")
            self.lb.selection_set(i)
            self.lb.see(i)
        return "break"

    def _up(self, e):
        if self.lb is None:
            return None
        cur = self.lb.curselection()
        i = (cur[0] - 1) if cur else 0
        if i >= 0:
            self.lb.selection_clear(0, "end")
            self.lb.selection_set(i)
            self.lb.see(i)
        return "break"

    def _accept(self, e=None):
        if self.lb is None or not self.lb.curselection():
            self._close()
            return None
        choice = self.lb.get(self.lb.curselection()[0]).split("  ")[0]
        w, s, en = self._word()
        lead = w[: len(w) - len(w.lstrip("-~"))]
        text = self.var.get()
        new = text[:s] + lead + choice + " " + text[en:].lstrip()
        self.var.set(new)
        self.icursor(s + len(lead) + len(choice) + 1)
        self._close()
        return "break"

    def _enter(self, e):
        if self.lb is not None and self.lb.curselection():
            return self._accept()
        self._close()
        if self.on_enter:
            self.on_enter()
        return "break"

    def _close(self):
        if self.pop is not None:
            try:
                self.pop.destroy()
            except tk.TclError:
                pass
        self.pop = self.lb = None


# ---- dialogs ------------------------------------------------------------------------------------
class Dialog(tk.Toplevel):
    def __init__(self, master, title: str):
        super().__init__(master, background=COLORS["bg"])
        self.title(title)
        self.transient(master.winfo_toplevel())
        self.result = None
        self.body = ttk.Frame(self, padding=12)
        self.body.pack(fill="both", expand=True)
        self.bind("<Escape>", lambda e: self.destroy())
        th.dark_title_bar(self, COLORS.get("name") == "dark")

    def buttons(self, ok_text="OK", on_ok=None, cancel=True):
        bf = ttk.Frame(self, padding=(12, 0, 12, 12))
        bf.pack(fill="x")
        if cancel:
            ttk.Button(bf, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bf, text=ok_text, style="Accent.TButton", command=on_ok or self.destroy).pack(side="right",
                                                                                             padx=(0, 6))
        return bf

    def center(self):
        self.update_idletasks()
        m = self.master.winfo_toplevel()
        x = m.winfo_rootx() + max(0, (m.winfo_width() - self.winfo_reqwidth()) // 2)
        y = m.winfo_rooty() + max(0, (m.winfo_height() - self.winfo_reqheight()) // 3)
        self.geometry(f"+{x}+{y}")

    def modal(self):
        self.center()
        self.grab_set()
        self.wait_window()
        return self.result


class SitePicker(Dialog):
    """Choose the sites a search uses. Every site is explained in plain words (what it has, adult content,
    whether it needs a login), with its guide on the right and a one-click test."""

    def __init__(self, master, app, selected: list[str]):
        super().__init__(master, "Choose sites")
        from . import guides as G
        self.G = G
        self.app = app
        self.store = app.store
        self.selected = list(selected)
        self.vars: dict[str, bool] = {h: (h in selected) for h in (s.host for s in self.store.all_sites())}
        sw = self.winfo_screenwidth()
        self.geometry(f"{min(px(1280), sw - 40)}x{min(px(740), self.winfo_screenheight() - 80)}")
        b = self.body
        b.columnconfigure(0, weight=3, uniform="split")
        b.columnconfigure(1, weight=2, uniform="split")
        b.rowconfigure(2, weight=1)
        ttk.Label(b, text="Which sites should this search use?", font=app.f_big).grid(row=0, column=0, columnspan=2,
                                                                                    sticky="w")
        top = ttk.Frame(b)
        top.grid(row=1, column=0, sticky="ew", pady=(6, 6))
        top.columnconfigure(0, weight=1)
        self.flt = tk.StringVar()
        e = PlaceholderEntry(top, "Find a site by name...", textvariable=self.flt)
        e.grid(row=0, column=0, sticky="ew")
        e.focus_set()
        self.flt.trace_add("write", lambda *_: self._fill())
        self.hide_adult = tk.BooleanVar(value=bool(app.lib.get_state("hide_adult_sites")))
        ttk.Checkbutton(top, text="Hide adult sites", variable=self.hide_adult,
                        command=self._adult_toggled).grid(row=0, column=1, padx=(10, 0))
        lf = ttk.Frame(b)
        lf.grid(row=2, column=0, sticky="nsew")
        lf.rowconfigure(0, weight=1)
        lf.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(lf, columns=("what", "content", "login"), show="tree headings",
                                 selectmode="browse")
        self.tree.heading("#0", text="Site")
        self.tree.heading("what", text="What it has")
        self.tree.heading("content", text="Content")
        self.tree.heading("login", text="Login")
        for c, w, st in (("#0", 210, True), ("what", 250, True), ("content", 95, True), ("login", 125, True)):
            self.tree.column(c, width=px(w), stretch=st, minwidth=px(60))
        fit_columns(self.tree, {"#0": 210, "what": 250, "content": 95, "login": 125})
        sb = ttk.Scrollbar(lf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<Button-1>", self._click)
        self.tree.bind("<space>", lambda e: (self._toggle(self.tree.focus()), "break")[1])
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._show_guide())
        self.tree.bind("<Double-1>", lambda e: self.options())
        ttk.Label(b, text="Click the box (or press Space) to tick a site. Click a name to read about it.",
                  style="Muted.TLabel").grid(row=3, column=0, sticky="w", pady=(4, 0))
        # guide + actions
        rf = ttk.Frame(b, padding=(12, 0, 0, 0))
        rf.grid(row=1, column=1, rowspan=3, sticky="nsew")
        rf.rowconfigure(1, weight=1)
        rf.columnconfigure(0, weight=1)
        acts = ttk.Frame(rf)
        acts.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.login_btn = ttk.Button(acts, text="Log in / settings...", command=self.options)
        self.login_btn.pack(side="left")
        self.test_btn = ttk.Button(acts, text="Test this site", command=self.test)
        self.test_btn.pack(side="left", padx=6)
        from .ui_help import HelpView
        self.guide = HelpView(rf, self._link, height=10, width=30)
        self.guide.grid(row=1, column=0, sticky="nsew")
        self.test_msg = ttk.Label(rf, text="", wraplength=px(420), justify="left")
        self.test_msg.grid(row=2, column=0, sticky="w", pady=(6, 0))
        bf = ttk.Frame(self, padding=(12, 0, 12, 12))
        bf.pack(fill="x")
        ttk.Button(bf, text="Add another site...", command=self.add_site).pack(side="left")
        self.remove_btn = ttk.Button(bf, text="Remove site", command=self.remove)
        self.remove_btn.pack(side="left", padx=(6, 0))
        ttk.Button(bf, text="Untick all", command=self.untick).pack(side="left", padx=6)
        self.count = ttk.Label(bf, text="", font=app.f_bold)
        self.count.pack(side="left", padx=8)
        ttk.Button(bf, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bf, text="Use these sites", style="Accent.TButton", command=self.ok).pack(side="right", padx=6)
        self._fill()
        first = next((h for h in selected if self.tree.exists(h)), None)
        if first:
            self.tree.selection_set(first)
            self.tree.see(first)
        else:
            self._show_guide()

    def _info(self, site):
        return self.G.site_info(site.host, site.source.name)

    def _fill(self):
        f = self.flt.get().strip().lower()
        sel = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        groups = (("ticked", "Ticked"), ("popular", "Good places to start"), ("all", "All other sites"))
        for gid, label in groups:
            self.tree.insert("", "end", iid="#" + gid, text=label, open=True, tags=("group",))
        for s in sorted(self.store.all_sites(), key=lambda s: self._info(s)["name"].lower()):
            d = self._info(s)
            if f and f not in s.host.lower() and f not in d["name"].lower() and f not in d["what"].lower():
                continue
            if self.hide_adult.get() and d["content"] == "adult" and not self.vars.get(s.host):
                continue
            gid = "ticked" if self.vars.get(s.host) else ("popular" if s.host in self.G.POPULAR else "all")
            login = self.G.LOGIN_SHORT.get(d["login"], "")
            if s.has_credentials():
                login = "\u2713 saved"
            self.tree.insert("#" + gid, "end", iid=s.host, text=("\u2611  " if self.vars.get(s.host) else "\u2610  ")
                             + d["name"], values=(d["what"], self.G.CONTENT_SHORT.get(d["content"], ""), login),
                             tags=(d["content"],))
        for gid, _ in groups:
            if not self.tree.get_children("#" + gid):
                self.tree.delete("#" + gid)
        for i in sel:
            if self.tree.exists(i):
                self.tree.selection_set(i)
        self.tree.tag_configure("group", font=self.app.f_bold)
        self.tree.tag_configure("adult", foreground=COLORS["warn"])
        n = sum(self.vars.values())
        self.count.configure(text=f"{n} ticked")

    def _adult_toggled(self):
        self.app.lib.set_state("hide_adult_sites", bool(self.hide_adult.get()))
        self._fill()

    def _click(self, e):
        iid = self.tree.identify_row(e.y)
        if not iid or iid.startswith("#"):
            return None
        # the box: the first ~3 characters of the name column
        if self.tree.identify_column(e.x) == "#0":
            bbox = self.tree.bbox(iid, "#0")
            if bbox and e.x - bbox[0] < px(60):
                self._toggle(iid)
                self.tree.selection_set(iid)
                return "break"
        return None

    def _toggle(self, iid):
        if not iid or iid.startswith("#") or iid not in self.vars:
            return
        self.vars[iid] = not self.vars[iid]
        self._fill()
        if self.tree.exists(iid):
            self.tree.selection_set(iid)
            self.tree.see(iid)

    def _current(self):
        sel = self.tree.selection()
        if not sel or sel[0].startswith("#"):
            return None
        return self.store.get(sel[0])

    def _show_guide(self):
        s = self._current()
        for b in (self.login_btn, self.test_btn, self.remove_btn):
            b.state(["!disabled"] if s else ["disabled"])
        self.test_msg.configure(text="")
        if s is None:
            self.guide.show("# Choosing sites\nTick the sites to search in the list on the left. Click a site's "
                            "name to read what it has and how to search it.\n\nNot sure where to start? "
                            "**Safebooru** has safe anime art, **e926** safe furry art, **Wallhaven** "
                            "wallpapers.\n\n[More about sites and logins](go:help:sites)")
            return
        self.guide.show(self.G.site_text(s.host, s.source.name, s.has_credentials()))

    def _link(self, target):
        if target.startswith(("help:", "site:")):
            self.app.open_help(target)
        else:
            self.app.go(target)

    def untick(self):
        for k in self.vars:
            self.vars[k] = False
        self._fill()

    def test(self):
        s = self._current()
        if s is None:
            return
        self.test_msg.configure(text=f"Testing {s.name}...", foreground=COLORS["muted"])

        def work():
            msgs = []
            if s.has_credentials():
                ok, msg = s.login()
                msgs.append(("Login works." if ok else "Login failed: " + friendly(msg)))
            pr = s.search("", 1, 5)
            if pr.error:
                return False, " ".join(msgs + ["Search failed: " + friendly(pr.error)])
            return True, " ".join(msgs + [f"It works: the site answered with {len(pr.images)} pictures."])

        def done(res):
            ok, text = res
            try:
                self.test_msg.configure(text=text, foreground=COLORS["ok" if ok else "error"])
            except tk.TclError:
                pass
        self.app.worker.submit(work, on_done=done,
                               on_error=lambda e: self.test_msg.configure(text=friendly(str(e)),
                                                                          foreground=COLORS["error"]))

    def add_site(self):
        site = AddSiteDialog(self, self.app).modal()
        if site:
            self.vars[site.host] = True
            self._fill()
            if self.tree.exists(site.host):
                self.tree.selection_set(site.host)
                self.tree.see(site.host)

    def remove(self):
        s = self._current()
        if s is None:
            return
        if messagebox.askyesno("Remove site", f"Remove {self._info(s)['name']} ({s.host}) from the list?\n\n"
                               "You can add it again later with 'Add another site...'.", parent=self):
            self.store.remove_site(s.host)
            self.vars.pop(s.host, None)
            self._fill()
            self._show_guide()

    def options(self):
        s = self._current()
        if s is not None:
            SiteOptions(self, self.app, s).modal()
            self._fill()
            self._show_guide()

    def ok(self):
        self.result = [h for h, v in self.vars.items() if v]
        if not self.result:
            messagebox.showinfo("Choose sites", "Tick at least one site (click the box in front of its name).",
                                parent=self)
            return
        self.destroy()


class AddSiteDialog(Dialog):
    def __init__(self, master, app, host: str = ""):
        super().__init__(master, "Add another site")
        self.app = app
        b = self.body
        ttk.Label(b, text="Add a site that isn't in the list", font=app.f_big).pack(anchor="w")
        ttk.Label(b, text="Many image sites run the same software as the sites in the list (Danbooru, Gelbooru, "
                  "Moebooru, Shimmie...). Type the site's address and Packrat finds out which one it uses: it "
                  "tries each kind once, or finds a matching add-on in Grabber's online list.",
                  wraplength=px(560), justify="left").pack(anchor="w", pady=(6, 10))
        f = ttk.Frame(b)
        f.pack(fill="x")
        ttk.Label(f, text="Address").pack(side="left")
        self.host = tk.StringVar(value=host)
        en = PlaceholderEntry(f, "for example konachan.net", textvariable=self.host, width=40)
        en.pack(side="left", padx=8, fill="x", expand=True)
        en.focus_set()
        f2 = ttk.Frame(b)
        f2.pack(fill="x", pady=(10, 0))
        ttk.Label(f2, text="Software").pack(side="left")
        self.type_var = tk.StringVar(value="Find out automatically")
        ttk.Combobox(f2, textvariable=self.type_var, values=["Find out automatically"] + sorted(app.store.types),
                     state="readonly", width=34).pack(side="left", padx=8)
        self.msg = ttk.Label(b, text="", style="Muted.TLabel", wraplength=px(560), justify="left")
        self.msg.pack(anchor="w", pady=(10, 0))
        self.buttons("Add", self.ok)
        self.bind("<Return>", lambda e: self.ok())

    def ok(self):
        host = self.host.get().strip()
        if not host:
            self.msg.configure(text="Type the site's address first.", foreground=COLORS["error"])
            return
        t = self.type_var.get()
        auto = t == "Find out automatically"
        self.msg.configure(text="Checking the site (this can take a minute)..." if auto else "Adding...",
                           foreground=COLORS["muted"])
        bare = host.split("://")[-1].rstrip("/")

        def work():
            if auto:
                guess = self.app.store.guess_type(bare)
                if guess is None:
                    reg = _registry_match(host)
                    if reg is None:
                        raise ValueError("Packrat couldn't recognise this site. Check the address (open it in your "
                                         "browser), or choose its software in the list if you know it.")
                    return ("registry", reg)
                return ("site", self.app.store.add_site(host, guess))
            return ("site", self.app.store.add_site(host, t))

        def done(res):
            kind, val = res
            if kind == "registry":
                if messagebox.askyesno("Add a site", f"The add-on '{val['name']}' from Grabber's online list "
                                       f"supports this site. Install it?", parent=self):
                    self.app.worker.submit(_install_and_add, self.app, val, host,
                                           on_done=lambda s: self._finish(s), on_error=self._err)
                return
            self._finish(val)
        self.app.worker.submit(work, on_done=done, on_error=self._err)

    def _finish(self, site):
        self.result = site
        self.destroy()

    def _err(self, e):
        try:
            self.msg.configure(text=friendly(str(e)), foreground=COLORS["error"])
        except tk.TclError:
            pass


class SiteOptions(Dialog):
    """A site's login and options, in plain words. Only the fields the chosen login method uses are shown."""

    def __init__(self, master, app, site):
        from . import guides as G
        d = G.site_info(site.host, site.source.name)
        super().__init__(master, f"{d['name']} - login and settings")
        self.app, self.site = app, site
        self.geometry(size_for(self, px(760), px(600)))
        nb = ttk.Notebook(self.body)
        nb.pack(fill="both", expand=True)
        self.v: dict[str, tk.Variable] = {}
        # -- login
        lg = ttk.Frame(nb, padding=12)
        nb.add(lg, text="Login")
        lg.columnconfigure(1, weight=1)
        self.auths = site.source.meta.get("auth", {}) or {}
        self.field_rows: dict[str, tuple] = {}
        if not self.auths:
            ttk.Label(lg, text="This site has no login. Nothing to set up here.").grid(row=0, column=0, sticky="w")
        else:
            ttk.Label(lg, text=f"Login: {G.LOGIN_LABEL.get(d['login'], '')}", font=app.f_bold).grid(
                row=0, column=0, columnspan=2, sticky="w")
            steps = d.get("login_steps") or ["Fill in the fields below, then press Test login."]
            ttk.Label(lg, text="\n".join(f"{i + 1}. {t}".replace("**", "").replace("`", "")
                                         for i, t in enumerate(steps)), wraplength=px(680), justify="left",
                      style="Muted.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 10))
            # API-key methods first: they are what the guides explain and what most sites prefer for programs
            def rank(k):
                a = self.auths[k]
                if "apiKey" in auth_fields(a):
                    return 0
                return 1 if a.get("authType") in ("pkce", "authorization_code") else 2
            order = sorted(self.auths, key=rank)
            self.auths = {k: self.auths[k] for k in order}
            self.labels = {k: auth_label(k, a) for k, a in self.auths.items()}
            cur = site.login_type() if site.has_credentials() else ""
            cur = cur or next(iter(self.auths))
            ttk.Label(lg, text="How to log in").grid(row=2, column=0, sticky="w", padx=(0, 12))
            self.method = tk.StringVar(value=self.labels[cur])
            names = list(dict.fromkeys(self.labels.values()))
            cb = ttk.Combobox(lg, textvariable=self.method, values=names, state="readonly", width=40)
            cb.grid(row=2, column=1, sticky="w", pady=4)
            cb.bind("<<ComboboxSelected>>", lambda e: self._show_fields())
            all_ids = []
            for a in self.auths.values():
                for fid in auth_fields(a):
                    if fid not in all_ids:
                        all_ids.append(fid)
            r = 3
            for fid in all_ids:
                lab = ttk.Label(lg, text=FIELD_LABELS.get(fid, fid))
                v = tk.StringVar(value=site.auth_value(fid))
                row = ttk.Frame(lg)
                ent = ttk.Entry(row, textvariable=v, width=44, show="\u2022" if fid in SECRET_IDS else "")
                ent.pack(side="left")
                if fid in SECRET_IDS:
                    sv = tk.BooleanVar(value=False)
                    ttk.Checkbutton(row, text="Show", variable=sv,
                                    command=lambda e=ent, sv=sv: e.configure(show="" if sv.get() else "\u2022")).pack(
                        side="left", padx=6)
                self.v["auth/" + fid] = v
                self.field_rows[fid] = (lab, row, r)
                r += 1
            self.test_msg = ttk.Label(lg, text="Passwords and keys are kept in the Windows Credential Manager, "
                                      "never in a file.", style="Muted.TLabel", wraplength=px(680), justify="left")
            self.test_msg.grid(row=50, column=0, columnspan=2, sticky="w", pady=(10, 0))
            bf = ttk.Frame(lg)
            bf.grid(row=51, column=0, columnspan=2, sticky="w", pady=6)
            ttk.Button(bf, text="Test login", style="Accent.TButton", command=self.test).pack(side="left")
            ttk.Button(bf, text="Open the site", command=lambda: open_url(site.base_url)).pack(side="left", padx=6)
            ttk.Button(bf, text="Log out / clear", command=self.clear).pack(side="left")
            self._show_fields()
        # -- speed limits
        dl = ttk.Frame(nb, padding=12)
        nb.add(dl, text="Speed limits")
        ttk.Label(dl, text="How long Packrat waits between requests to this site. The defaults are polite; "
                  "lower values can get you blocked.", wraplength=px(680), justify="left",
                  style="Muted.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        for i, (k, lab) in enumerate((("page", "Seconds between result pages"),
                                      ("details", "Seconds between picture details"),
                                      ("image", "Seconds between downloads"),
                                      ("thumbnail", "Seconds between previews"),
                                      ("retry", "Seconds to wait after an error"))):
            self._row(dl, i + 1, lab, f"download/throttle_{k}", str(site.throttle(k)))
        self._row(dl, 6, "Downloads at the same time", "download/simultaneous", str(site.max_simultaneous()))
        # -- advanced
        adv = ttk.Frame(nb, padding=12)
        nb.add(adv, text="Advanced")
        self._row(adv, 0, "Name shown in Packrat", "name", site.name)
        self._check(adv, 1, "Use a secure connection (https)", "ssl", site.ssl)
        from .grab import qtini
        self._check(adv, 2, "Use the default order of APIs (Settings > Sources)", "sources/usedefault",
                    qtini.to_bool(site.setting("sources/usedefault"), True))
        apis = sorted({(a.get("name") or k) for k, a in (site.source.meta.get("apis") or {}).items()})
        for i in range(1, 5):
            self._row(adv, 2 + i, f"API {i}", f"sources/source_{i}", site.setting(f"sources/source_{i}", "") or "",
                      [""] + [a.lower() for a in apis])
        self._row(adv, 7, "Referer (pages)", "referer", site.setting("referer", "none"),
                  ["none", "host", "page", "details", "image"])
        ttk.Label(adv, text=f"Software: {site.source.name}.  Address: {site.base_url}", style="Muted.TLabel").grid(
            row=9, column=0, columnspan=2, sticky="w", pady=(12, 0))
        if not ADVANCED[0]:
            # simple view: only the login. The other tabs are still there (and saved), just out of the way.
            nb.hide(dl)
            nb.hide(adv)
            more = ttk.Frame(self.body)
            more.pack(fill="x", pady=(6, 0))
            ttk.Label(more, text="Speed limits and expert options for this site are hidden in the simple view.",
                      style="Muted.TLabel").pack(side="left")

            def show_more():
                nb.add(dl)
                nb.add(adv)
                more.destroy()
            ttk.Button(more, text="Show them", command=show_more).pack(side="left", padx=8)
        self.buttons("Save", self.save)

    def _method_key(self):
        inv = {lab: k for k, lab in self.labels.items()}
        return inv.get(self.method.get(), "")

    def _show_fields(self):
        a = self.auths.get(self._method_key(), {})
        defaults = self.site.source.site_defaults(self.site.host)
        # app keys the source already provides (Pixiv, Twitter) aren't something you need to see
        want = [f for f in auth_fields(a) if not (f.startswith("consumer") and defaults.get("auth/" + f))]
        r = 3
        for fid, (lab, row, _) in self.field_rows.items():
            if fid in want:
                lab.grid(row=r, column=0, sticky="w", padx=(0, 12), pady=3)
                row.grid(row=r, column=1, sticky="w", pady=3)
                r += 1
            else:
                lab.grid_remove()
                row.grid_remove()

    def _row(self, f, r, label, key, value, choices=None):
        ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", padx=(0, 12), pady=3)
        v = tk.StringVar(value="" if value is None else str(value))
        w = (ttk.Combobox(f, textvariable=v, values=choices, width=30) if choices is not None
             else ttk.Entry(f, textvariable=v, width=34))
        w.grid(row=r, column=1, sticky="w", pady=3)
        self.v[key] = v

    def _check(self, f, r, label, key, value):
        v = tk.BooleanVar(value=bool(value))
        ttk.Checkbutton(f, text=label, variable=v).grid(row=r, column=0, columnspan=2, sticky="w", pady=3)
        self.v[key] = v

    def _apply(self):
        s = self.site
        for k, v in self.v.items():
            val = v.get()
            if k.startswith("auth/"):
                val = str(val).strip()
                if val != s.auth_value(k[5:]):
                    s.set_auth_value(k[5:], val)
            elif k.startswith("download/"):
                try:
                    s.set_setting(k, float(val) if "." in str(val) else int(val))
                except ValueError:
                    pass
            else:
                s.set_setting(k, val if val != "" else None)
        if self.auths:
            key = self._method_key()
            # filled in the fields of another method only? use that one
            if not any(str(self.v.get("auth/" + f, tk.StringVar()).get()).strip()
                       for f in auth_fields(self.auths.get(key, {}))):
                for k2, a in self.auths.items():
                    if any(str(self.v.get("auth/" + f, tk.StringVar()).get()).strip() for f in auth_fields(a)):
                        key = k2
                        break
            s.set_setting("login/type", key)
        s.reset_login()

    def test(self):
        self._apply()
        self.test_msg.configure(text="Testing...", foreground=COLORS["muted"])

        def done(res):
            ok, msg = res
            try:
                self.test_msg.configure(text=("Login works. " if ok else "Login failed: ") + friendly(msg or ""),
                                        foreground=COLORS["ok" if ok else "error"])
            except tk.TclError:
                pass
        self.app.worker.submit(self.site.login, on_done=done,
                               on_error=lambda e: self.test_msg.configure(text=friendly(str(e)),
                                                                          foreground=COLORS["error"]))

    def clear(self):
        for k, v in self.v.items():
            if k.startswith("auth/"):
                v.set("")
        self._apply()
        self.test_msg.configure(text="Login details removed.", foreground=COLORS["muted"])

    def save(self):
        self._apply()
        self.destroy()


