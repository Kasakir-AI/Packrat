"""Built-in help: the guide window (F1), the guide text widget used in other windows, and the first-start
assistant that sets up the basics in plain language."""
from __future__ import annotations

import re
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, ttk

from . import guides as G
from .uikit import PRESETS, format_label, sample_example  # noqa: F401
from . import theme as th
from . import ui_common as uc
from .ui_common import COLORS

_INLINE = re.compile(r"(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))")


class HelpView(tk.Text):
    """Shows guide text (see guides.py for the format). Links call on_link(target)."""

    def __init__(self, master, on_link, **kw):
        super().__init__(master, wrap="word", relief="flat", borderwidth=0, highlightthickness=0, padx=14,
                         pady=10, cursor="arrow", font="TkDefaultFont", **kw)
        self.on_link = on_link
        base = tkfont.nametofont("TkDefaultFont")
        size = abs(base.cget("size")) or 10
        fam = base.cget("family")
        self.f = {"h1": tkfont.Font(family=fam, size=size + 6, weight="bold"),
                  "h2": tkfont.Font(family=fam, size=size + 2, weight="bold"),
                  "b": tkfont.Font(family=fam, size=size, weight="bold"),
                  "code": tkfont.Font(family=tkfont.nametofont("TkFixedFont").cget("family"), size=size)}
        self._links: dict[str, str] = {}
        self.themed(COLORS)
        self.configure(state="disabled")
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)

    def themed(self, p):
        th.style_text(self, p, readonly_bg=False)
        self.tag_configure("h1", font=self.f["h1"], spacing1=4, spacing3=8)
        self.tag_configure("h2", font=self.f["h2"], spacing1=12, spacing3=4)
        self.tag_configure("b", font=self.f["b"])
        self.tag_configure("code", font=self.f["code"], background=p["help_bg"], foreground=p["help_fg"])
        self.tag_configure("link", foreground=p["accent"], underline=True)
        self.tag_configure("li", lmargin1=12, lmargin2=30, spacing1=2)
        self.tag_configure("p", spacing1=2, spacing3=2)
        self.tag_configure("hl", background=p["select_bg"], foreground=p["select_fg"])
        self.tag_configure("muted", foreground=p["muted"], spacing3=6)

    def show(self, text: str):
        self.configure(state="normal")
        self.delete("1.0", "end")
        self._links = {}
        for raw in text.split("\n"):
            line = raw.rstrip()
            if line.startswith("# "):
                self.insert("end", line[2:] + "\n", ("h1",))
                continue
            if line.startswith("## "):
                self.insert("end", line[3:] + "\n", ("h2",))
                continue
            block = ("p",)
            m = re.match(r"^(\s*)(- |\d+\. )(.*)$", line)
            if m:
                bullet = "•  " if m.group(2) == "- " else m.group(2) + " "
                self.insert("end", bullet, ("li",))
                line, block = m.group(3), ("li",)
            elif raw.startswith("   ~ "):
                line, block = line.strip()[2:], ("li", "muted")
            elif raw.startswith("   ") and line.strip():
                line, block = line.strip(), ("li",)
            self._inline(line, block)
            self.insert("end", "\n", block)
        self.configure(state="disabled")
        self.yview_moveto(0)

    def jump(self, heading: str):
        """Scroll to a heading (as written in the guide, formatting marks are ignored)."""
        plain = heading.replace("**", "").replace("`", "").strip()
        idx = self.search(plain, "1.0", stopindex="end")
        if idx:
            self.yview(idx)
            self.tag_remove("hl", "1.0", "end")
            self.tag_add("hl", idx, f"{idx} lineend")
            self.after(2500, lambda: self.tag_remove("hl", "1.0", "end"))

    def _inline(self, line, block):
        for part in _INLINE.split(line):
            if not part:
                continue
            if part.startswith("**") and part.endswith("**"):
                self.insert("end", part[2:-2], block + ("b",))
            elif part.startswith("`") and part.endswith("`"):
                self.insert("end", " " + part[1:-1] + " ", block + ("code",))
            elif part.startswith("[") and "](" in part:
                label, target = part[1:-1].split("](", 1)
                tag = f"link{len(self._links)}"
                self._links[tag] = target
                self.insert("end", label, block + ("link", tag))
            else:
                self.insert("end", part, block)

    def _tags_at(self, e):
        return self.tag_names(self.index(f"@{e.x},{e.y}"))

    def _click(self, e):
        for t in self._tags_at(e):
            if t in self._links:
                target = self._links[t]
                self.on_link(target[3:] if target.startswith("go:") else target)
                return "break"
            if t == "code":
                rng = self.tag_prevrange("code", self.index(f"@{e.x},{e.y}+1c"))
                if rng:
                    self.clipboard_clear()
                    self.clipboard_append(self.get(*rng).strip())
                return "break"
        return None

    def _motion(self, e):
        tags = self._tags_at(e)
        self.configure(cursor="hand2" if any(t in self._links or t == "code" for t in tags) else "arrow")


class HelpWindow(tk.Toplevel):
    """Guides: a question box, topics and one page per site."""

    def __init__(self, app, start: str = "start"):
        super().__init__(app.root, background=COLORS["bg"])
        self.app = app
        self.title("Packrat - Help")
        self.geometry(uc.size_for(self, uc.px(1060), uc.px(740)))
        th.dark_title_bar(self, app.dark)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)
        self._index = None
        self._job = None
        # -- ask a question
        top = ttk.Frame(self, padding=(8, 8, 8, 0))
        top.grid(row=0, column=0, columnspan=2, sticky="ew")
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="Ask a question", font=app.f_bold).grid(row=0, column=0, padx=(0, 8))
        self.q = tk.StringVar()
        self.q_entry = uc.PlaceholderEntry(top, "Type in your own words, e.g.  how do I save a whole search   or   "
                                                "e621 login", textvariable=self.q)
        self.q_entry.grid(row=0, column=1, sticky="ew")
        self.q_entry.bind("<Return>", lambda e: self._search_now())
        self.q_entry.bind("<Escape>", lambda e: (self.q.set(""), "break")[1])
        self.q.trace_add("write", lambda *_: self._search_soon())
        ttk.Button(top, text="Search", command=self._search_now).grid(row=0, column=2, padx=(6, 0))
        sb = ttk.Button(top, text="Setup assistant...", command=lambda: self.app.go("setup"))
        sb.grid(row=0, column=3, padx=(18, 0))
        uc.tip(sb, "Change your sites, download folder, file names and simple / advanced view")
        # -- topics
        left = ttk.Frame(self, padding=(8, 8, 0, 8))
        left.grid(row=1, column=0, sticky="ns")
        left.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(left, show="tree", selectmode="browse")
        self.tree.column("#0", width=uc.px(230))
        self.tree.grid(row=0, column=0, sticky="ns")
        tsb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        tsb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=tsb.set)
        for tid, title, _ in G.TOPICS:
            self.tree.insert("", "end", iid="help:" + tid, text=title, open=(tid == "sites"))
        for host, src in self._sites():
            d = G.site_info(host, src)
            self.tree.insert("help:sites", "end", iid="site:" + host, text=d["name"])
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._selected())
        # -- page
        right = ttk.Frame(self, padding=8)
        right.grid(row=1, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        self.view = HelpView(right, self.go)
        self.view.grid(row=0, column=0, sticky="nsew")
        vs = ttk.Scrollbar(right, orient="vertical", command=self.view.yview)
        vs.grid(row=0, column=1, sticky="ns")
        self.view.configure(yscrollcommand=vs.set)
        bottom = ttk.Frame(right)
        bottom.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Label(bottom, text="Blue words are links. Click a highlighted word to copy it.",
                  style="Muted.TLabel").pack(side="left")
        self.back_btn = ttk.Button(bottom, text="\u25c0 Back", command=self.back)
        self.back_btn.pack(side="right")
        self.history: list[str] = []
        self.current = ""
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Control-f>", lambda e: self.focus_search())
        self.bind("<Alt-Left>", lambda e: self.back())
        self.show(start)

    def _sites(self):
        return [(s.host, s.source.name) for s in self.app.store.all_sites()]

    def _selected(self):
        sel = self.tree.selection()
        if sel and sel[0] != self.current.split("#")[0]:
            self._open(sel[0])

    def _render(self, key):
        base, _, anchor = key.partition("#")
        if base == "help:sites":
            self.view.show(G.sites_index(self._sites()))
        elif base.startswith("help:"):
            text = next((t for i, _, t in G.TOPICS if i == base[5:]), None)
            self.view.show(text or "# Not found")
        elif base.startswith("site:"):
            host = base[5:]
            site = self.app.store.get(host)
            self.view.show(G.site_text(host, site.source.name if site else "",
                                       bool(site and site.has_credentials())))
        if anchor:
            self.view.jump(anchor)

    def _open(self, key, remember=True):
        if remember and self.current and self.current != key:
            self.history.append(self.current)
        self.current = key
        self.back_btn.state(["!disabled"] if self.history else ["disabled"])
        if key.startswith("search:"):
            self.view.show(self._results(key[7:]))
            return
        self._render(key)

    def show(self, target: str):
        from .helpsearch import unlink
        target = unlink(target)
        key = target if ":" in target else "help:" + target
        base = key.split("#")[0]
        if self.tree.exists(base):
            self.tree.selection_set(base)
            self.tree.see(base)
        self._open(key)

    def back(self):
        if self.history:
            key = self.history.pop()
            base = key.split("#")[0]
            if self.tree.exists(base):
                self.current = base   # don't let the selection event add history
                self.tree.selection_set(base)
            self._open(key, remember=False)

    def go(self, target: str):
        if target.startswith(("help:", "site:")):
            self.show(target)
        else:
            self.app.go(target)

    # -- questions
    def focus_search(self):
        self.deiconify()
        self.lift()
        self.q_entry.focus_set()
        self.q_entry.select_range(0, "end")

    def index(self):
        if self._index is None:
            from . import helpsearch as H
            from .grab.settings import SETTINGS
            from .ui_pages import SettingsPage
            self._index = H.build(self._sites(), SETTINGS, uc.ADVANCED[0],
                                  {k: lab for k, lab, _ in SettingsPage.BASICS})
        return self._index

    def view_changed(self):
        self._index = None

    def _search_soon(self):
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(350, self._search_now)

    def _search_now(self):
        self._job = None
        q = self.q.get().strip()
        if not q:
            return
        self.tree.selection_remove(self.tree.selection())
        key = "search:" + q
        if self.current.startswith("search:"):
            self.current = key          # typing refines the same search, no history entry per letter
            self.view.show(self._results(q))
        else:
            self._open(key)

    def _results(self, q):
        from . import helpsearch as H
        return H.results_text(q, self.index().search(q))

    def themed(self, p):
        self.configure(background=p["bg"])
        self.view.themed(p)
        th.dark_title_bar(self, p.get("name") == "dark")


# =====================================================================================================


class DestinationDialog(uc.Dialog):
    """Where pictures go and how they're named, from the Browse tab (Grabber's 'Destination' panel)."""

    def __init__(self, master, app):
        super().__init__(master, "Where pictures are saved")
        self.app = app
        st = app.st
        b = self.body
        b.columnconfigure(1, weight=1)
        ttk.Label(b, text="Where pictures are saved", font=app.f_big).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(b, text="Every download goes into this folder. The name format can add folders inside it "
                  "(per artist, per site...).", style="Muted.TLabel", wraplength=uc.px(600), justify="left").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(2, 10))
        ttk.Label(b, text="Folder").grid(row=2, column=0, sticky="w", padx=(0, 10))
        self.folder = tk.StringVar(value=st.save_path())
        ttk.Entry(b, textvariable=self.folder, width=56).grid(row=2, column=1, sticky="ew")
        ttk.Button(b, text="Choose...", command=self._pick).grid(row=2, column=2, padx=(6, 0))
        ttk.Label(b, text="Names").grid(row=3, column=0, sticky="nw", padx=(0, 10), pady=(10, 0))
        box = ttk.Frame(b)
        box.grid(row=3, column=1, columnspan=2, sticky="w", pady=(10, 0))
        cur = st.filename()
        self.fmt = tk.StringVar(value=cur)
        for label, fmt in PRESETS:
            ttk.Radiobutton(box, text=label, value=fmt, variable=self.fmt).pack(anchor="w", pady=1)
        if cur not in [f for _, f in PRESETS]:
            ttk.Radiobutton(box, text=f"Your own format:  {cur}", value=cur, variable=self.fmt).pack(anchor="w",
                                                                                                    pady=1)
        self.example = ttk.Label(b, text="", wraplength=uc.px(600), justify="left")
        self.example.grid(row=4, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Button(b, text="Write your own format...", command=lambda: (self.destroy(),
                                                                        app.go("settings:File names"))).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(10, 0))
        self.fmt.trace_add("write", lambda *_: self._example())
        self.folder.trace_add("write", lambda *_: self._example())
        self._example()
        self.buttons("Save", self.ok)
        self.result = False

    def _pick(self):
        p = filedialog.askdirectory(parent=self, initialdir=self.folder.get() or None)
        if p:
            self.folder.set(p)

    def _example(self):
        path, err = sample_example(self.fmt.get(), self.app.st, self.folder.get())
        self.example.configure(text=("Example (Pikachu by some_artist, from e621):\n" + path) if path
                               else "Problem: " + (err or "no file name"),
                               foreground=COLORS["ok"] if path else COLORS["error"])

    def ok(self):
        vals = {"Save/filename": self.fmt.get()}
        folder = self.folder.get().strip()
        if folder != self.app.st.save_path():
            vals["Save/path"] = folder
        self.app.st.update(vals)
        self.result = True
        self.destroy()


class SearchHelper(uc.Dialog):
    """'More options' for a search (Grabber's 'Plus' window): sort, rating, size, leave out... in plain words.
    The words come from each site's own list of what it understands, so only working options are offered."""

    SORTS = [("Newest first (normal)", None, None), ("Oldest first", "order:id", ("order:id", "order:id_desc")),
             ("Best score first", "order:score", ("order:score",)),
             ("Most favorited first", "order:favcount", ("order:favcount",)),
             ("Random order", "order:random", ("order:random",))]
    SIZES = [("Any size", None, None), ("Big (at least 1920 pixels wide)", "width:>=1920", ("width:",))]

    def __init__(self, master, app, query: str, sites):
        super().__init__(master, "More search options")
        self.app = app
        self.sites = sites
        self.result = None
        mods = [set(s.source.meta.get("modifiers") or []) for s in sites] or [set()]
        self.mods = mods
        names = {s.host: G.site_info(s.host, s.source.name)["name"] for s in sites}
        self.names = names
        b = self.body
        b.columnconfigure(1, weight=1)
        ttk.Label(b, text="More search options", font=app.f_big).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(b, text="Pick what you want; Packrat adds the right words to your search. Options the ticked "
                  "sites don't support are greyed out.", style="Muted.TLabel", wraplength=uc.px(620),
                  justify="left").grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 10))
        words, picked = self._parse(query)
        self.base = tk.StringVar(value=" ".join(w for w in words if not w.startswith("-")))
        self.exclude = tk.StringVar(value=" ".join(w[1:] for w in words if w.startswith("-") and len(w) > 1))
        r = 2
        ttk.Label(b, text="Look for").grid(row=r, column=0, sticky="w", padx=(0, 12), pady=3)
        ttk.Entry(b, textvariable=self.base, width=50).grid(row=r, column=1, sticky="ew", pady=3)
        r += 1
        ttk.Label(b, text="Leave out").grid(row=r, column=0, sticky="w", padx=(0, 12), pady=3)
        ex = uc.PlaceholderEntry(b, "tags you don't want, e.g.  text  comic", textvariable=self.exclude, width=50)
        ex.grid(row=r, column=1, sticky="ew", pady=3)
        r += 1
        self.sort = self._combo(b, r, "Sort", self.SORTS, picked.get("sort"))
        r += 2
        self.rating_opts = self._rating_options()
        self.rating = self._combo(b, r, "Content", self.rating_opts, picked.get("rating"))
        r += 2
        self.size = self._combo(b, r, "Size", self.SIZES, picked.get("size"))
        r += 2
        self.preview = ttk.Label(b, text="", font=app.f_bold, wraplength=uc.px(620), justify="left")
        self.preview.grid(row=r, column=0, columnspan=2, sticky="w", pady=(12, 0))
        r += 1
        if any("danbooru" in s.source.name.lower() for s in sites):
            ttk.Label(b, text="Danbooru counts these as tags: without a paid account a search can have only 2.",
                      style="Muted.TLabel", wraplength=uc.px(620)).grid(row=r, column=0, columnspan=2, sticky="w",
                                                                         pady=(4, 0))
        for v in (self.base, self.exclude, self.sort[0], self.rating[0], self.size[0]):
            v.trace_add("write", lambda *_: self._update())
        self._update()
        self.buttons("Search", self.ok)

    def _supported(self, needs) -> list[str]:
        """Names of the ticked sites that don't understand an option."""
        if not needs:
            return []
        bad = []
        for site, m in zip(self.sites, self.mods):
            if not all(any(x == n or (n.endswith(":") and x.startswith(n)) for x in m) for n in needs):
                bad.append(self.names.get(site.host, site.host))
        return bad

    def _rating_options(self):
        allgen = all("rating:general" in m for m in self.mods)
        safe = "rating:general" if allgen else "rating:safe"
        return [("Any (what the site shows normally)", None, None),
                ("Safe only", safe, (safe,)),
                ("No explicit pictures", "-rating:explicit", ("rating:explicit",))]

    def _combo(self, b, r, label, opts, current):
        ttk.Label(b, text=label).grid(row=r, column=0, sticky="w", padx=(0, 12), pady=(8, 0))
        labels, values = [], []
        for text, word, needs in opts:
            bad = self._supported(needs)
            if bad and len(bad) == len(self.sites):
                text += "  (not on these sites)"
            elif bad:
                text += "  (not on " + ", ".join(bad[:3]) + ("..." if len(bad) > 3 else "") + ")"
            labels.append(text)
            values.append(word if not bad else "")
        v = tk.StringVar(value=labels[0])
        for lab, (_, word, _) in zip(labels, opts):
            if word and word == current:
                v.set(lab)
        cb = ttk.Combobox(b, textvariable=v, values=labels, state="readonly", width=48)
        cb.grid(row=r, column=1, sticky="w", pady=(8, 0))
        note = ttk.Label(b, text="", style="Muted.TLabel")
        note.grid(row=r + 1, column=1, sticky="w")
        v._map = dict(zip(labels, values))   # noqa: SLF001
        v._note = note                        # noqa: SLF001
        return v, cb

    @staticmethod
    def _parse(query):
        words, picked = [], {}
        for w in query.split():
            low = w.lower()
            if low.startswith(("order:", "sort:")):
                picked["sort"] = "order:" + low.split(":", 1)[1].split(":")[0]
            elif low in ("rating:general", "rating:safe", "rating:g", "rating:s", "-rating:explicit", "-rating:e"):
                picked["rating"] = {"rating:g": "rating:general", "rating:s": "rating:safe",
                                    "-rating:e": "-rating:explicit"}.get(low, low)
            elif low == "width:>=1920":
                picked["size"] = low
            else:
                words.append(w)
        return words, picked

    def _query(self):
        parts = self.base.get().split() + ["-" + w.lstrip("-") for w in self.exclude.get().split()]
        for v, _ in (self.sort, self.rating, self.size):
            word = v._map.get(v.get())   # noqa: SLF001
            if word:
                parts.append(word)
        return " ".join(parts)

    def _update(self):
        for v, _ in (self.sort, self.rating, self.size):
            word = v._map.get(v.get())   # noqa: SLF001
            v._note.configure(text="Not every ticked site understands this, so it is left out of the search."  # noqa: SLF001
                              if word == "" else "")
        q = self._query()
        self.preview.configure(text="Your search will be:   " + (q or "(everything)"))

    def ok(self):
        self.result = self._query()
        self.destroy()


class FirstRun(uc.Dialog):
    """Five short questions on the first start."""

    def __init__(self, app, grabber_folder=None, rerun: bool = False, current_sites=None):
        super().__init__(app.root, "Setup assistant" if rerun else "Welcome to Packrat")
        self.app, self.grabber, self.rerun = app, grabber_folder, rerun
        self.step = 0
        st = app.st
        self.name = tk.StringVar(value=st.get("username") or "")
        ticked = set(current_sites or []) if rerun else {"safebooru.org", "e926.net"}
        hosts = [h for h in G.POPULAR if app.store.get(h) is not None]
        hosts += [h for h in (current_sites or []) if h not in hosts and app.store.get(h) is not None]
        self.site_vars = {h: tk.BooleanVar(value=h in ticked) for h in hosts}
        self.folder = tk.StringVar(value=st.save_path())
        self.preset = tk.StringVar(value=st.filename())
        self.adv = tk.BooleanVar(value=uc.ADVANCED[0])
        self.do_import = tk.BooleanVar(value=bool(grabber_folder) and not rerun)
        self.geometry(uc.size_for(self, uc.px(720), uc.px(540)))
        self.page = ttk.Frame(self.body)
        self.page.pack(fill="both", expand=True)
        nav = ttk.Frame(self, padding=(12, 0, 12, 12))
        nav.pack(fill="x")
        self.dots = ttk.Label(nav, style="Muted.TLabel")
        self.dots.pack(side="left")
        self.next_btn = ttk.Button(nav, text="Next", style="Accent.TButton", command=self.next)
        self.next_btn.pack(side="right")
        self.back_btn = ttk.Button(nav, text="Back", command=lambda: self.goto(self.step - 1))
        self.back_btn.pack(side="right", padx=6)
        ttk.Button(nav, text="Skip", command=self.destroy).pack(side="right", padx=(0, 18))
        self.steps = [self._welcome, self._sites, self._folder, self._names, self._view] + (
            [self._grabber] if grabber_folder else [])
        self.goto(0)

    def _clear(self, title, text):
        for w in self.page.winfo_children():
            w.destroy()
        ttk.Label(self.page, text=title, font=self.app.f_title).pack(anchor="w")
        lab = ttk.Label(self.page, text=text, wraplength=uc.px(660), justify="left")
        lab.pack(anchor="w", pady=(6, 12))

    def goto(self, i):
        self.step = max(0, min(i, len(self.steps) - 1))
        self.steps[self.step]()
        self.back_btn.state(["disabled"] if self.step == 0 else ["!disabled"])
        self.next_btn.configure(text="Finish" if self.step == len(self.steps) - 1 else "Next")
        self.dots.configure(text=f"Step {self.step + 1} of {len(self.steps)}")

    def _welcome(self):
        self._clear("Setup assistant" if self.rerun else "Welcome",
                    ("These questions change the basics. Your current choices are already filled in; press Next "
                     "to keep them, or Skip to leave without changing anything.\n\n") if self.rerun else
                    "Packrat finds pictures on image sites, shows them, and saves the ones you want. "
                    "These few questions set up the basics; everything can be changed later in Settings, "
                    "or here again from the Help menu.\n\n"
                    "Some sites (e621, e926, e6ai) ask every program to say who is using it. If you have an "
                    "account there, type your user name. Otherwise leave it empty.")
        f = ttk.Frame(self.page)
        f.pack(anchor="w")
        ttk.Label(f, text="Your user name (optional)").pack(side="left")
        ttk.Entry(f, textvariable=self.name, width=30).pack(side="left", padx=8)

    def _sites(self):
        self._clear("Which sites?", "Tick the sites you want to search. You can add or remove sites any time with "
                    "the Sites button. (Many more are available there: Pixiv, Reddit, DeviantArt, ...)")
        box = ttk.Frame(self.page)
        box.pack(fill="both", expand=True)
        for r, (h, v) in enumerate(self.site_vars.items()):
            d = G.site_info(h)
            ttk.Checkbutton(box, text=d["name"], variable=v).grid(row=r, column=0, sticky="w", pady=1)
            ttk.Label(box, text=d["what"], style="Muted.TLabel").grid(row=r, column=1, sticky="w", padx=10)
            ttk.Label(box, text=G.CONTENT_LABEL[d["content"]], style="Muted.TLabel").grid(row=r, column=2,
                                                                                          sticky="w")

    def _folder(self):
        self._clear("Where should pictures go?", "Downloaded pictures are saved in this folder (and folders "
                    "inside it, if you choose so on the next page).")
        f = ttk.Frame(self.page)
        f.pack(fill="x")
        ttk.Entry(f, textvariable=self.folder).pack(side="left", fill="x", expand=True)
        ttk.Button(f, text="Choose...", command=self._pick).pack(side="left", padx=6)

    def _pick(self):
        p = filedialog.askdirectory(parent=self, initialdir=self.folder.get() or None)
        if p:
            self.folder.set(p)

    def _names(self):
        self._clear("How should files be named?", "Pick one. The example shows a picture of Pikachu by "
                    "'some_artist' from e621. More choices: Settings > File names.")
        self.example = ttk.Label(self.page, style="Muted.TLabel", wraplength=uc.px(660))
        for label, fmt in PRESETS:
            ttk.Radiobutton(self.page, text=label, value=fmt, variable=self.preset,
                            command=self._example).pack(anchor="w", pady=1)
        cur = self.app.st.filename()
        if cur not in [f for _, f in PRESETS]:
            ttk.Radiobutton(self.page, text=f"Keep your own format:  {cur}", value=cur, variable=self.preset,
                            command=self._example).pack(anchor="w", pady=1)
        self.example.pack(anchor="w", pady=(12, 0))
        self._example()

    def _example(self):
        path, err = sample_example(self.preset.get(), self.app.st, self.folder.get())
        self.example.configure(text=("Example:  " + path) if path else err)

    def _view(self):
        self._clear("How much do you want to see?", "Packrat can do a lot. The simple view shows what you need to "
                    "search, look and download. The advanced view adds the rest. You can switch any time with "
                    "'Advanced options' at the top right; nothing you set is lost.")
        ttk.Radiobutton(self.page, text="Simple view (recommended)", value=False, variable=self.adv).pack(
            anchor="w", pady=(0, 2))
        ttk.Label(self.page, text="Browse, Downloads, Favorites, Site archive and the everyday settings.",
                  style="Muted.TLabel").pack(anchor="w", padx=(24, 0), pady=(0, 10))
        ttk.Radiobutton(self.page, text="Advanced view", value=True, variable=self.adv).pack(anchor="w",
                                                                                             pady=(0, 2))
        ttk.Label(self.page, text="Also the Monitors, Tools and Log tabs, filters, every setting (over 200) and "
                  "speed limits for each site. Good if you used Grabber before.", style="Muted.TLabel",
                  wraplength=uc.px(620), justify="left").pack(anchor="w", padx=(24, 0))

    def _grabber(self):
        self._clear("Import from Grabber?", f"Grabber's settings were found in {self.grabber}. Packrat can copy "
                    "its favorites, monitors, logins, file name settings and the list of pictures you already "
                    "have. Grabber itself is not changed.")
        ttk.Checkbutton(self.page, text="Yes, import them", variable=self.do_import).pack(anchor="w")

    def next(self):
        if self.step == 1 and not any(v.get() for v in self.site_vars.values()):
            self.site_vars.get("safebooru.org", next(iter(self.site_vars.values()))).set(True)
        if self.step < len(self.steps) - 1:
            self.goto(self.step + 1)
            return
        st = self.app.st
        sites = [h for h, v in self.site_vars.items() if v.get()]
        vals = {"sites": sites, "Save/filename": self.preset.get()}
        if self.name.get().strip():
            vals["username"] = self.name.get().strip()
        if self.folder.get().strip() and self.folder.get().strip() != st.save_path():
            vals["Save/path"] = self.folder.get().strip()
        st.update(vals)
        self.result = {"sites": sites, "import": self.do_import.get() and self.grabber,
                       "advanced": bool(self.adv.get())}
        self.destroy()


class AboutDialog(uc.Dialog):
    """Version, where things are kept, and credits."""

    def __init__(self, master, app):
        super().__init__(master, "About Packrat")
        import platform

        from . import __version__, paths
        b = self.body
        ttk.Label(b, text="Packrat", font=app.f_title).pack(anchor="w")
        ttk.Label(b, text=f"Version {__version__}", style="Muted.TLabel").pack(anchor="w")
        ttk.Label(b, text="Browse, download and organize pictures from many sites. The e6ai Archiver 2.0, grown up, "
                  "with Grabber's features and site scripts.", wraplength=uc.px(480), justify="left").pack(
            anchor="w", pady=(10, 10))
        rows = (("Packrat folder", str(paths.install_root())), ("Your data", str(paths.data_dir())),
                ("Python", f"{platform.python_version()} ({platform.system()} {platform.release()})"))
        g = ttk.Frame(b)
        g.pack(anchor="w", fill="x")
        for i, (k, v) in enumerate(rows):
            ttk.Label(g, text=k, style="Muted.TLabel").grid(row=i, column=0, sticky="nw", padx=(0, 12), pady=1)
            ttk.Label(g, text=v, wraplength=uc.px(380), justify="left").grid(row=i, column=1, sticky="w", pady=1)
        bf = ttk.Frame(b)
        bf.pack(anchor="w", pady=(8, 0))
        ttk.Button(bf, text="Open your data folder", command=lambda: uc.open_path(str(paths.data_dir()))).pack(
            side="left")
        ttk.Label(b, text="Credits", font=app.f_bold).pack(anchor="w", pady=(14, 2))
        ttk.Label(b, text="The site scripts (sources) and many ideas come from Grabber by Bionus "
                  "(Apache License 2.0, see sources\\GRABBER-LICENSE.txt). Packrat is not made by, or connected "
                  "to, the Grabber project.", wraplength=uc.px(480), justify="left").pack(anchor="w")
        self.buttons("Close", self.destroy, cancel=False)
