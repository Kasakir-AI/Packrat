"""1.9: light / dark theme for the control panel.

Pure parts (palettes, choice parsing, the Windows "apps use light theme" setting) are testable without a
display; apply() styles a Tk root + ttk.Style. The panel uses the portable 'clam' ttk theme in both
modes so every widget (entries, comboboxes, spinboxes, check boxes, scrollbars, tabs) can be recoloured
the same way on Windows, macOS and Linux - the native Windows theme ignores most colour options.
"""
from __future__ import annotations

import os
import sys

CHOICES = ("system", "light", "dark")
LABELS = {"light": "Light", "dark": "Dark", "system": "Follow Windows" if os.name == "nt" else "Follow system"}
DEFAULT = "system"

LIGHT = {
    "name": "light",
    "bg": "#f3f3f3", "panel": "#fbfbfb", "field": "#ffffff", "fg": "#1b1b1b", "muted": "#666666",
    "border": "#c4c4c4", "focus": "#2f6fd0", "select_bg": "#cfe0fb", "select_fg": "#111111",
    "button": "#e7e7e7", "button_hover": "#dcdcdc", "button_pressed": "#cdcdcd",
    "accent": "#1f63c6", "accent_fg": "#ffffff", "accent_hover": "#2a72db",
    "disabled_fg": "#9a9a9a", "disabled_bg": "#ececec",
    "trough": "#e2e2e2", "scroll": "#c2c2c2", "scroll_hover": "#a8a8a8", "bar": "#2f8f46",
    "tab": "#e4e4e4", "tab_sel": "#fbfbfb",
    "help_bg": "#eef3f8", "help_fg": "#1b1b1b", "tip_bg": "#ffffe0", "tip_fg": "#1b1b1b",
    "banner_bg": "#fff4d6", "banner_fg": "#3d2c00",
    "text_bg": "#ffffff", "text_fg": "#1b1b1b", "insert": "#1b1b1b",
    # log / status colours (contrast >= 4.5:1 on text_bg / bg)
    "ok": "#1b7f2a", "error": "#b3261e", "forbidden": "#b3261e", "stopped": "#8a5a00", "warn": "#8a5a00",
    "info": "#333333", "cmd": "#1f4e8c", "debug": "#666666",
}
DARK = {
    "name": "dark",
    "bg": "#1e1f22", "panel": "#26282c", "field": "#2b2d31", "fg": "#e6e6e6", "muted": "#a3a7ad",
    "border": "#45484e", "focus": "#5b9cf2", "select_bg": "#2f4f7f", "select_fg": "#ffffff",
    "button": "#35373c", "button_hover": "#404349", "button_pressed": "#4a4d54",
    "accent": "#2f6fcc", "accent_fg": "#ffffff", "accent_hover": "#3a78d4",
    "disabled_fg": "#73777e", "disabled_bg": "#2a2b2f",
    "trough": "#2a2b2f", "scroll": "#4a4d54", "scroll_hover": "#5c6068", "bar": "#3fae5a",
    "tab": "#2a2c30", "tab_sel": "#26282c",
    "help_bg": "#24303d", "help_fg": "#dce6f2", "tip_bg": "#3a3d43", "tip_fg": "#f0f0f0",
    "banner_bg": "#3b3220", "banner_fg": "#f5e3b8",
    "text_bg": "#191a1d", "text_fg": "#dcdcdc", "insert": "#e6e6e6",
    "ok": "#6fd28a", "error": "#ff8a80", "forbidden": "#ff8a80", "stopped": "#f0c060", "warn": "#f0c060",
    "info": "#d0d0d0", "cmd": "#8ab4f8", "debug": "#9aa0a6",
}
PALETTES = {"light": LIGHT, "dark": DARK}


def normalize(choice) -> str:
    """'Dark', 'dark', 'auto', 'follow windows' ... -> one of CHOICES (unknown -> system)."""
    s = str(choice or "").strip().lower().replace("_", " ").replace("-", " ")
    if s in ("light", "l", "day"):
        return "light"
    if s in ("dark", "d", "night"):
        return "dark"
    return "system"


def _winreg_reader():
    import winreg  # noqa: PLC0415 - Windows only
    k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                       r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
    try:
        return winreg.QueryValueEx(k, "AppsUseLightTheme")[0]
    finally:
        winreg.CloseKey(k)


def system_prefers_dark(reader=None, platform: str | None = None) -> bool:
    """Windows: HKCU\\...\\Themes\\Personalize AppsUseLightTheme == 0 means dark. Elsewhere (or when the
    value cannot be read): light. `reader` returns the DWORD (tests pass a fake)."""
    platform = platform or sys.platform
    if reader is None:
        if not platform.startswith("win"):
            return False
        reader = _winreg_reader
    try:
        return int(reader()) == 0
    except Exception:  # noqa: BLE001 - missing key / old Windows / no winreg: light
        return False


def resolve(choice, system_dark=None) -> str:
    """The palette name to use for a saved choice ('system' asks system_dark(), default the registry)."""
    c = normalize(choice)
    if c != "system":
        return c
    return "dark" if (system_dark or system_prefers_dark)() else "light"


def palette(choice, system_dark=None) -> dict:
    return PALETTES[resolve(choice, system_dark)]


def _hex(c: str) -> tuple:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio of two #rrggbb colours (1 .. 21)."""
    def lum(c):
        r, g, b_ = (x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in _hex(c))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b_
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def apply(root, style, p: dict) -> None:
    """Style every ttk widget class the panel uses, plus Tk defaults for plain tk widgets and the
    combobox drop-down list. Safe to call again at any time (instant switching)."""
    try:
        style.theme_use("clam")
    except Exception:  # noqa: BLE001
        pass
    bg, fg, field, border = p["bg"], p["fg"], p["field"], p["border"]
    root.configure(background=bg)
    # plain tk widgets created later (Toplevel, Text, Listbox of the combobox drop-down, Menu)
    for pat, val in (("*Background", bg), ("*Foreground", fg), ("*selectBackground", p["select_bg"]),
                     ("*selectForeground", p["select_fg"]), ("*insertBackground", p["insert"]),
                     ("*TCombobox*Listbox.background", field), ("*TCombobox*Listbox.foreground", fg),
                     ("*TCombobox*Listbox.selectBackground", p["select_bg"]),
                     ("*TCombobox*Listbox.selectForeground", p["select_fg"]),
                     ("*Menu.background", p["panel"]), ("*Menu.foreground", fg),
                     ("*Menu.activeBackground", p["select_bg"]), ("*Menu.activeForeground", p["select_fg"])):
        root.option_add(pat, val, 80)
    style.configure(".", background=bg, foreground=fg, fieldbackground=field, bordercolor=border,
                    lightcolor=bg, darkcolor=bg, troughcolor=p["trough"], focuscolor=p["focus"],
                    selectbackground=p["select_bg"], selectforeground=p["select_fg"],
                    insertcolor=p["insert"], arrowcolor=fg, indicatorcolor=field)
    style.map(".", foreground=[("disabled", p["disabled_fg"])], background=[("disabled", bg)])
    for s in ("TFrame", "TLabel", "TLabelframe", "TPanedwindow", "TCheckbutton", "TRadiobutton"):
        style.configure(s, background=bg, foreground=fg)
    style.configure("Card.TFrame", background=p["panel"])
    style.configure("TLabelframe", bordercolor=border, lightcolor=bg, darkcolor=bg)
    style.configure("TLabelframe.Label", background=bg, foreground=fg)
    style.configure("Group.TLabelframe.Label", background=bg, foreground=fg)
    style.configure("Sash", sashthickness=6, gripcount=0, background=p["border"])
    style.configure("TButton", background=p["button"], foreground=fg, bordercolor=border,
                    lightcolor=p["button"], darkcolor=p["button"], padding=(10, 4), focuscolor=p["focus"])
    style.map("TButton",
              background=[("disabled", p["disabled_bg"]), ("pressed", p["button_pressed"]),
                          ("active", p["button_hover"])],
              foreground=[("disabled", p["disabled_fg"])],
              lightcolor=[("pressed", p["button_pressed"]), ("active", p["button_hover"])],
              darkcolor=[("pressed", p["button_pressed"]), ("active", p["button_hover"])])
    style.configure("Accent.TButton", background=p["accent"], foreground=p["accent_fg"],
                    lightcolor=p["accent"], darkcolor=p["accent"])
    style.map("Accent.TButton",
              background=[("disabled", p["disabled_bg"]), ("pressed", p["accent"]),
                          ("active", p["accent_hover"])],
              foreground=[("disabled", p["disabled_fg"])],
              lightcolor=[("active", p["accent_hover"])], darkcolor=[("active", p["accent_hover"])])
    style.configure("Toolbutton", background=p["button"], foreground=fg, padding=(8, 2), bordercolor=border)
    style.map("Toolbutton",
              background=[("selected", p["accent"]), ("active", p["button_hover"])],
              foreground=[("selected", p["accent_fg"])])
    for s in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(s, fieldbackground=field, foreground=fg, background=p["button"], bordercolor=border,
                        lightcolor=field, darkcolor=field, arrowcolor=fg, insertcolor=p["insert"],
                        selectbackground=p["select_bg"], selectforeground=p["select_fg"])
        style.map(s, fieldbackground=[("readonly", field), ("disabled", p["disabled_bg"])],
                  foreground=[("disabled", p["disabled_fg"])],
                  bordercolor=[("focus", p["focus"])], lightcolor=[("focus", p["focus"])],
                  selectbackground=[("readonly", field)] if s == "TCombobox" else [],
                  selectforeground=[("readonly", fg)] if s == "TCombobox" else [],
                  background=[("active", p["button_hover"]), ("pressed", p["button_pressed"])])
    try:   # 3.0: check boxes as big as the text (clam draws them at a fixed 10 px, tiny at 150-200 %)
        import tkinter.font as _tkf
        _line = _tkf.nametofont("TkDefaultFont").metrics("linespace")
    except Exception:  # noqa: BLE001
        _line = 16
    for s in ("TCheckbutton", "TRadiobutton"):
        style.configure(s, indicatorbackground=field, indicatorforeground=fg, upperbordercolor=border,
                        lowerbordercolor=border, focuscolor=p["focus"], indicatorsize=max(10, int(_line * 0.72)),
                        indicatormargin=(0, 0, max(4, _line // 4), 0))
        style.map(s, background=[("active", bg)], foreground=[("disabled", p["disabled_fg"])],
                  indicatorbackground=[("selected", p["accent"]), ("pressed", p["button_pressed"]),
                                       ("disabled", p["disabled_bg"])],
                  indicatorforeground=[("selected", p["accent_fg"])])
    style.configure("TNotebook", background=bg, bordercolor=border, lightcolor=bg, darkcolor=bg,
                    tabmargins=(2, 4, 2, 0))
    style.configure("TNotebook.Tab", background=p["tab"], foreground=p["muted"], bordercolor=border,
                    lightcolor=p["tab"], darkcolor=p["tab"], padding=(10, 4))
    style.map("TNotebook.Tab", background=[("selected", p["tab_sel"]), ("active", p["button_hover"])],
              foreground=[("selected", fg)], lightcolor=[("selected", p["tab_sel"])])
    for s in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
        style.configure(s, background=p["scroll"], troughcolor=p["trough"], bordercolor=p["trough"],
                        lightcolor=p["scroll"], darkcolor=p["scroll"], arrowcolor=fg, gripcount=0)
        style.map(s, background=[("active", p["scroll_hover"]), ("pressed", p["scroll_hover"])])
    for s in ("Horizontal.TProgressbar", "Thin.Horizontal.TProgressbar"):
        style.configure(s, background=p["bar"], troughcolor=p["trough"], bordercolor=border,
                        lightcolor=p["bar"], darkcolor=p["bar"])
    style.configure("Err.TLabel", foreground=p["error"], background=bg)
    style.configure("Warn.TLabel", foreground=p["warn"], background=bg)
    style.configure("Ok.TLabel", foreground=p["ok"], background=bg)
    style.configure("Muted.TLabel", foreground=p["muted"], background=bg)
    style.configure("Help.TLabel", background=p["help_bg"], foreground=p["help_fg"], padding=(8, 6))
    style.configure("Banner.TFrame", background=p["banner_bg"])
    style.configure("Banner.TLabel", background=p["banner_bg"], foreground=p["banner_fg"])
    # 3.0: lists (download list, favorites, monitors, tags, sites). The row height follows the font: the clam
    # theme's fixed 20 px rows cut the text in half at 150 % / 200 % Windows display scaling.
    try:
        import tkinter.font as tkfont
        line = tkfont.nametofont("TkDefaultFont").metrics("linespace")
    except Exception:  # noqa: BLE001
        line = 16
    style.configure("Treeview", background=p["text_bg"], fieldbackground=p["text_bg"], foreground=p["text_fg"],
                    bordercolor=border, lightcolor=p["text_bg"], darkcolor=p["text_bg"],
                    rowheight=int(line * 1.5) + 2)
    style.map("Treeview", background=[("selected", p["select_bg"])], foreground=[("selected", p["select_fg"])])
    style.configure("Treeview.Heading", background=p["button"], foreground=fg, bordercolor=border,
                    lightcolor=p["button"], darkcolor=p["button"], relief="flat")
    style.map("Treeview.Heading", background=[("active", p["button_hover"])])


def style_text(widget, p: dict, *, readonly_bg: bool = False) -> None:
    """tk.Text / tk.Listbox / tk.Canvas colours (these ignore ttk styles)."""
    kw = {"background": p["bg"] if readonly_bg else p["text_bg"]}
    try:
        cls = widget.winfo_class()
    except Exception:  # noqa: BLE001
        cls = ""
    if cls == "Canvas":
        widget.configure(background=p["bg"], highlightthickness=0)
        return
    kw.update(foreground=p["text_fg"], insertbackground=p["insert"], selectbackground=p["select_bg"],
              selectforeground=p["select_fg"], highlightbackground=p["border"], highlightcolor=p["focus"])
    widget.configure(**kw)


def dark_title_bar(root, dark: bool) -> bool:
    """Windows 10 1809+/11: ask DWM for a dark (or light) window title bar. Best effort, never raises."""
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
        val = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (20; 19 on pre-20H1 builds)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                return True
    except Exception:  # noqa: BLE001
        pass
    return False
