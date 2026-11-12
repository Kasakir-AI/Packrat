"""Use the login of the person's own web browser: if they are logged in to a site in Chrome, Firefox, Edge ...,
Packrat takes that site's cookies (and only that site's) when they press the button, never in the background.

What works where (browsers protect their cookies differently):
  Firefox, LibreWolf      every system; no question asked (the cookies are a plain file)
  Chrome, Edge, Brave,    Mac: yes, after macOS asks once whether Packrat may use the browser's key
  Arc, Vivaldi, Opera     ("Always Allow"); Linux: yes
                          Windows: no. Since 2024 these browsers lock their cookies so that only the browser
                          itself can read them ("app-bound encryption"); the built-in login browser is the way there
  Safari                  Mac, only with Full Disk Access for Packrat (System Settings > Privacy & Security)
Sites behind a "checking your browser" page tie that check to the exact browser, so an imported login can stop
working there; the built-in browser handles those.
"""
from __future__ import annotations

import logging
import os
import plistlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("packrat.grab")


@dataclass
class Browser:
    key: str              # browser_cookie3 function name
    title: str
    supported: bool = True
    why: str = ""         # why not, when not supported


TITLES = {"chrome": "Chrome", "edge": "Edge", "brave": "Brave", "arc": "Arc", "vivaldi": "Vivaldi", "opera": "Opera",
          "opera_gx": "Opera GX", "chromium": "Chromium", "firefox": "Firefox", "librewolf": "LibreWolf",
          "safari": "Safari"}
CHROMIUM = {"chrome", "edge", "brave", "arc", "vivaldi", "opera", "opera_gx", "chromium"}
MAC_IDS = {"com.google.chrome": "chrome", "com.microsoft.edgemac": "edge", "com.brave.browser": "brave",
           "company.thebrowser.browser": "arc", "com.vivaldi.vivaldi": "vivaldi", "com.operasoftware.opera": "opera",
           "com.operasoftware.operagx": "opera_gx", "org.chromium.chromium": "chromium", "org.mozilla.firefox": "firefox",
           "io.gitlab.librewolf-community": "librewolf", "com.apple.safari": "safari"}
WIN_PROGIDS = (("chromehtml", "chrome"), ("msedgehtm", "edge"), ("bravehtml", "brave"), ("firefoxurl", "firefox"),
               ("librewolf", "librewolf"), ("operastable", "opera"), ("operagx", "opera_gx"), ("vivaldihtm", "vivaldi"),
               ("chromiumhtm", "chromium"))
LINUX_DESKTOP = (("google-chrome", "chrome"), ("microsoft-edge", "edge"), ("brave", "brave"), ("vivaldi", "vivaldi"),
                 ("opera", "opera"), ("chromium", "chromium"), ("librewolf", "librewolf"), ("firefox", "firefox"))


def browser(key: str, platform: str = "") -> Browser:
    platform = platform or sys.platform
    b = Browser(key, TITLES.get(key, key.title()))
    if platform == "win32" and key in CHROMIUM:
        b.supported, b.why = False, (f"{b.title} on Windows locks its saved logins so that no other program can use "
                                     "them. Use 'Log in with the built-in browser' instead (or Firefox).")
    return b


def default_browser() -> Browser | None:
    """The browser that opens web links, or None when it can't be told (or is one Packrat can't read)."""
    try:
        key = {"darwin": _mac_default, "win32": _windows_default}.get(sys.platform, _linux_default)()
    except Exception:  # noqa: BLE001 - no default browser to find is not an error
        log.debug("default browser not found", exc_info=True)
        key = ""
    return browser(key) if key in TITLES else None


def _mac_default() -> str:
    p = Path.home() / "Library/Preferences/com.apple.LaunchServices/com.apple.launchservices.secure.plist"
    for h in plistlib.loads(p.read_bytes()).get("LSHandlers", []):
        if h.get("LSHandlerURLScheme") in ("https", "http"):
            return MAC_IDS.get(str(h.get("LSHandlerRoleAll", "")).lower(), "")
    return "safari"                  # nothing chosen: Safari is the Mac's default


def _windows_default() -> str:
    import winreg  # type: ignore
    path = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
        prog = str(winreg.QueryValueEx(k, "ProgId")[0]).lower()
    return next((key for start, key in WIN_PROGIDS if prog.startswith(start)), "")


def _linux_default() -> str:
    out = subprocess.run(["xdg-settings", "get", "default-web-browser"], capture_output=True, text=True,
                         timeout=5).stdout.lower()
    return next((key for name, key in LINUX_DESKTOP if name in out), "")


def site_domain(host: str) -> str:
    """The domain whose cookies belong to the site: danbooru.donmai.us -> donmai.us (cdn.donmai.us shares the
    login), chan.sankakucomplex.com -> sankakucomplex.com."""
    host = host.split("/")[0].split(":")[0].lower()
    labels = host.split(".")
    keep = 3 if len(labels) >= 3 and len(labels[-1]) == 2 and len(labels[-2]) <= 3 else 2
    return ".".join(labels[-keep:])


def read_cookies(b: Browser, domain: str, cookie_file: str | None = None) -> list:
    """The browser's cookies for `domain` and its subdomains (http.cookiejar.Cookie objects)."""
    if not b.supported:
        raise RuntimeError(b.why)
    import browser_cookie3  # type: ignore
    loader = getattr(browser_cookie3, b.key)
    try:
        jar = loader(cookie_file=cookie_file, domain_name=domain) if cookie_file else loader(domain_name=domain)
    except PermissionError as e:
        raise RuntimeError(_permission_text(b)) from e
    except Exception as e:  # noqa: BLE001 - browser_cookie3 raises its own error types
        text = str(e)
        if "Operation not permitted" in text or "Permission" in text:
            raise RuntimeError(_permission_text(b)) from e
        if "could not find" in text.lower() or "no such file" in text.lower():
            raise RuntimeError(f"No {b.title} profile was found on this computer.") from e
        raise RuntimeError(f"Packrat couldn't read {b.title}'s login: {text[:200]}") from e
    host_ok = lambda d: d.lstrip(".") == domain or d.lstrip(".").endswith("." + domain)  # noqa: E731
    return [c for c in jar if host_ok(c.domain)]


def _permission_text(b: Browser) -> str:
    if b.key == "safari":
        return ("macOS keeps Safari's logins private. To allow it: System Settings > Privacy & Security > Full Disk "
                "Access > turn on Packrat, then try again. Or use the built-in browser.")
    if sys.platform == "darwin":
        return (f"macOS didn't let Packrat use {b.title}'s key. Try again and choose 'Always Allow' when asked "
                "(enter your Mac password).")
    return f"{b.title} didn't let Packrat read its logins."


def import_login(site, b: Browser, cookie_file: str | None = None) -> int:
    """Copy the browser's cookies for the site into the site's saved login. Returns how many."""
    cookies = read_cookies(b, site_domain(site.host), cookie_file)
    for c in cookies:
        site.cookies.set(c.name, c.value, domain=c.domain, path=c.path or "/")
    if cookies:
        site.save_session_cookies()
        from . import secrets
        for c in cookies:
            if len(c.value or "") >= 16:     # session tokens: never in a log
                secrets.register_redaction(c.value)
        log.info("%s: using %d cookies from %s", site.name, len(cookies), b.title)
    return len(cookies)


def env_supported() -> bool:
    """browser_cookie3 is installed (it comes with Packrat; a very old .venv may not have it yet)."""
    try:
        import browser_cookie3  # type: ignore  # noqa: F401
        return True
    except ImportError:
        return os.environ.get("PACKRAT_FAKE_BROWSER") == "1"


def _profile_dirs() -> dict:
    """Where each browser keeps its profile, per system (it is installed and has been used when it exists)."""
    home = Path.home()
    if sys.platform == "darwin":
        sup = home / "Library/Application Support"
        return {"chrome": sup / "Google/Chrome", "edge": sup / "Microsoft Edge", "brave": sup / "BraveSoftware/Brave-Browser",
                "arc": sup / "Arc/User Data", "vivaldi": sup / "Vivaldi", "opera": sup / "com.operasoftware.Opera",
                "chromium": sup / "Chromium", "firefox": sup / "Firefox/Profiles", "librewolf": sup / "librewolf/Profiles",
                "safari": home / "Library/Safari"}
    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local"))
        roaming = Path(os.environ.get("APPDATA", home / "AppData/Roaming"))
        return {"chrome": local / "Google/Chrome/User Data", "edge": local / "Microsoft/Edge/User Data",
                "brave": local / "BraveSoftware/Brave-Browser/User Data", "vivaldi": local / "Vivaldi/User Data",
                "opera": roaming / "Opera Software/Opera Stable", "chromium": local / "Chromium/User Data",
                "firefox": roaming / "Mozilla/Firefox/Profiles", "librewolf": roaming / "librewolf/Profiles"}
    cfg = home / ".config"
    return {"chrome": cfg / "google-chrome", "edge": cfg / "microsoft-edge", "brave": cfg / "BraveSoftware/Brave-Browser",
            "vivaldi": cfg / "vivaldi", "opera": cfg / "opera", "chromium": cfg / "chromium",
            "firefox": home / ".mozilla/firefox", "librewolf": home / ".librewolf"}


def _has_profile(key: str, p: Path) -> bool:
    """A real profile, not a folder an extension helper left behind (NativeMessagingHosts only)."""
    if key == "safari":
        return p.exists()
    if key in CHROMIUM:
        return (p / "Local State").exists()
    return p.is_dir() and any(p.glob("*/cookies.sqlite"))


def installed_browsers() -> list:
    """The browsers used on this computer, the default one first."""
    default = default_browser()
    keys = [k for k, p in _profile_dirs().items() if _has_profile(k, p)]
    if default and default.key in keys:
        keys.remove(default.key)
        keys.insert(0, default.key)
    return [browser(k) for k in keys]
