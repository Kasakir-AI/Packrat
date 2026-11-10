"""Helpers shared by Packrat's windows that need no window toolkit: plain-words error messages, tag
colours, login field names, opening files and links, file-name presets."""
from __future__ import annotations

import logging
import webbrowser

log = logging.getLogger("packrat")


def friendly(msg: str, site: str = "") -> str:
    """Error messages in plain words, with what to do. site: the site's name, for advice that only fits it."""
    m = str(msg or "")
    low = m.lower()
    e6 = any(x in (site + " " + low).lower() for x in ("e621", "e926", "e6ai"))
    if "403" in low or "forbidden" in low:
        return ("The site refused access (403). Wait a while and try again; some sites need a login or an API "
                "key (see the site's guide)." + (" e621 / e926 / e6ai also need your user name in Settings."
                                                  if e6 or not site else ""))
    if "401" in low or "unauthorized" in low:
        return "The login was refused (401): check the user name and the key / password."
    if "429" in low or "too many" in low:
        return "The site says: too many requests (429). Wait a few minutes; Packrat slows down by itself."
    if "cloudflare" in low or "just a moment" in low or "challenge" in low:
        return "The site is protected by a 'checking your browser' page (Cloudflare) that blocks programs."
    if "timed out" in low or "timeout" in low:
        return "The site didn't answer in time. It may be busy or down; try again later."
    if "network error" in low or "connection" in low or "name resolution" in low or "getaddrinfo" in low:
        return "Couldn't reach the site. Check your internet connection, or the site may be down."
    if "http 5" in low or " 500" in low or " 502" in low or " 503" in low:
        return "The site has a problem right now (server error). Try again later."
    if "logged in" in low or "login" in low and "need" in low:
        return m + " (Use 'Log in / settings' in the site list.)"
    return m


TYPE_TO_COLOR_KEY = {"artist": "artists", "circle": "circles", "copyright": "copyrights", "character": "characters",
                     "species": "species", "meta": "metas", "model": "models", "general": "generals",
                     "lore": "metas", "contributor": "artists", "invalid": "ignoreds"}


DARK_TAG = {"artists": "#ff7b7b", "copyrights": "#e08ae0", "characters": "#6fd28a", "generals": "#d8d8d8",
            "models": "#6cc8ff", "circles": "#8fd3ff", "species": "#ffa64d", "metas": "#ffa64d"}


def tag_color(typ: str, st, dark: bool) -> str:
    key = TYPE_TO_COLOR_KEY.get(typ or "general", "generals")
    colors = st.get("Coloring") or {}
    c = colors.get(key) if isinstance(colors, dict) else None
    if dark:
        if not c or c.lower() in ("#000000", "#000", "black"):
            return DARK_TAG.get(key, "#d8d8d8")
        if key in DARK_TAG and c.lower() in ("#aa0000", "#aa00aa", "#00aa00", "#ee6600", "#00aaff", "#55bbff"):
            return DARK_TAG[key]
    return c or "#000000"


def open_url(url: str) -> None:
    if url:
        webbrowser.open(url)


def open_path(path: str) -> None:
    import os
    import subprocess
    import sys
    try:
        if os.name == "nt":
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except OSError as e:
        log.warning("could not open %s: %s", path, e)


def reveal(path: str) -> None:
    import os
    import subprocess
    if os.name == "nt":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        open_path(os.path.dirname(path))


def _registry_match(host):
    from .grab import registry as R
    try:
        reg = R.fetch(R.OFFICIAL)
    except ValueError:
        return None
    return R.find_for_host([reg], host.split("://")[-1].rstrip("/"))


def _install_and_add(app, entry, host):
    from .grab import registry as R
    R.install(entry, accept_mismatch=True)
    app.store.reload()
    return app.store.add_site(host, entry["name"])


FIELD_LABELS = {"pseudo": "User name", "password": "Password", "apiKey": "API key", "userId": "User ID",
                "consumerKey": "Consumer key (client ID)", "consumerSecret": "Consumer secret",
                "refreshToken": "Refresh token", "accessToken": "Access token", "salt": "Salt"}


SECRET_IDS = {"password", "apiKey", "consumerSecret", "refreshToken", "accessToken", "accessTokenSecret"}


def auth_label(k, a) -> str:
    t = a.get("type", k)
    ids = {f.get("id") for f in a.get("fields") or []}
    if t == "url" and "apiKey" in ids:
        return "API key"
    if t == "url":
        return "User name and password"
    if t == "http_basic":
        return "API key (sent as a password)" if a.get("passwordType") == "apiKey" else "User name and password"
    if t in ("post", "get"):
        return "User name and password (like on the website)"
    if t == "oauth2":
        return {"pkce": "Sign in with your browser", "authorization_code": "Sign in with your browser",
                "refresh_token": "Refresh token", "client_credentials": "App keys",
                "password": "User name and password", "password_json": "User name and password"}.get(
            a.get("authType"), "OAuth 2")
    if t == "oauth1":
        return "Sign in with your browser (PIN)"
    return t


def auth_fields(a) -> list[str]:
    t = a.get("type")
    if t in ("url", "post", "get"):
        out = [f["id"] for f in a.get("fields") or [] if f.get("id") and f.get("type") not in ("const", "hash")]
        return out
    if t == "http_basic":
        return ["pseudo", a.get("passwordType") or "password"]
    if t == "oauth2":
        at = a.get("authType")
        if at == "refresh_token":
            return ["refreshToken"]
        if at in ("password", "password_json"):
            return ["pseudo", "password"]
        if at == "client_credentials":
            return ["consumerKey", "consumerSecret"]
        return ["consumerKey"]
    if t == "oauth1":
        return ["consumerKey", "consumerSecret"]
    return []


PRESETS = [
    ("By fingerprint (no duplicates, the default)", "%md5%.%ext%"),
    ("A folder per site, post number", "%website%/%id%.%ext%"),
    ("A folder per artist", "%artist%/%id%.%ext%"),
    ("Series, then character folders", "%copyright%/%character%/%id%.%ext%"),
    ("A folder per search", "%search%/%id%.%ext%"),
    ("Site, artist, post number", "%website%/%artist%/%id%.%ext%"),
]


def sample_example(fmt: str, st, folder: str = "") -> tuple[str, str]:
    """(example path, problem) for a file name format, using a made-up picture."""
    from datetime import datetime, timezone

    from .grab import filename as F
    from .grab.image import Image
    from .grab.tags import Tag
    img = Image("e621.net")
    img.id, img.md5, img.ext = 1234567, "0cc175b9c0f1b6a831c399e269772661", "jpg"
    img.width, img.height, img.file_size = 1920, 1080, 845123
    img.file_url = "https://static1.e621.net/data/0c/c1/0cc175b9c0f1b6a831c399e269772661.jpg"
    img.tags = [Tag("some_artist", "artist"), Tag("pokemon", "copyright"), Tag("pikachu", "character"),
                Tag("mouse", "species"), Tag("smile", "general"), Tag("outside", "general")]
    img.search = ["pikachu"]
    img.data["rating"] = "safe"
    img.data["score"] = 42
    img.data["date"] = datetime(2026, 11, 8, 14, 30, tzinfo=timezone.utc)
    try:
        level, msg = F.Filename(fmt).check()
        if level == "error":
            return "", msg
        toks = F.build_tokens(img, st)
        out = F.Filename(fmt).path(toks, st, folder, 1, F.DEFAULT_FLAGS | F.FLAG_FOLDER)
        import os
        return (os.path.normpath(out[0]) if out else ""), ("" if level != "warning" else msg)
    except Exception as e:  # noqa: BLE001
        return "", str(e)


def format_label(fmt: str) -> str:
    """A file name format in words when it's one of the presets."""
    for label, f in PRESETS:
        if f == fmt:
            return label.split(" (")[0]
    return fmt if len(fmt) <= 32 else fmt[:30] + "\u2026"
