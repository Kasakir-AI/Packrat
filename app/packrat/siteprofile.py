"""3.0: the sites the whole-site archive engine (exports + API crawl + reports) works with.

e6ai and e621 run the same e621ng software: the same JSON API (posts.json with page=a<id>/b<id> cursors),
the same daily database exports (/db_exports.json, verified on 2026-11-08 for both) and the same tag
category numbers. They differ in host names, the names of two tag categories (e6ai: director /
franchise, e621: artist / copyright) and the account page for API keys.

Inside the engine the e6ai names are canonical (director, franchise), because archives made by
e6ai-archiver 1.x/2.0 already store them; e621's API keys "artist" and "copyright" are mapped onto them
when a post is read (see naming.categorize_api_tags), and the window shows each site's own word.

One process archives one site. cli.main() calls activate() once, before anything talks to the network;
modules read current() at call time, never at import time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SiteProfile:
    key: str
    label: str
    host: str
    static_host: str
    db_name: str
    artist_word: str            # what the site calls category 1 (director on e6ai, artist on e621)
    franchise_word: str         # category 3 (franchise / copyright)
    api_key_page: str
    note: str = ""
    api_aliases: dict = field(default_factory=dict)   # API tag-dict key -> canonical category name

    @property
    def base_url(self) -> str:
        return f"https://{self.host}"

    @property
    def static_base(self) -> str:
        return f"https://{self.static_host}/data"

    def file_url(self, md5: str | None, ext: str | None) -> str | None:
        if not md5 or not ext:
            return None
        return f"{self.static_base}/{md5[0:2]}/{md5[2:4]}/{md5}.{ext}"

    def word(self, canonical: str) -> str:
        """The site's own word for a canonical category name (director -> artist on e621)."""
        return {"director": self.artist_word, "franchise": self.franchise_word}.get(canonical, canonical)


SITES: dict[str, SiteProfile] = {
    "e6ai": SiteProfile(
        "e6ai", "e6ai", "e6ai.net", "static1.e6ai.net", "e6ai.sqlite3", "director", "franchise",
        "https://e6ai.net/users/home",
        note="e6ai announced on 2026-11-03 that it closes at the end of November 2026 (read-only until then)."),
    "e621": SiteProfile(
        "e621", "e621", "e621.net", "static1.e621.net", "e621.sqlite3", "artist", "copyright",
        "https://e621.net/users/home",
        note="e621 has millions of posts (many TB): use the order, filters and 'first N GiB' tiers.",
        api_aliases={"artist": "director", "copyright": "franchise"}),
}
DEFAULT_SITE = "e6ai"
_active = SITES[DEFAULT_SITE]


def get(key: str | None) -> SiteProfile:
    k = (key or DEFAULT_SITE).strip().lower()
    k = {"e6ai.net": "e6ai", "e621.net": "e621"}.get(k, k)
    if k not in SITES:
        raise ValueError(f"site must be one of: {', '.join(SITES)} (got {key!r})")
    return SITES[k]


def activate(key: str | None) -> SiteProfile:
    global _active
    _active = get(key)
    return _active


def current() -> SiteProfile:
    return _active


def db_names() -> list[str]:
    return [p.db_name for p in SITES.values()]


def find_db(out) -> tuple[Path | None, SiteProfile | None]:
    """The archive database in `out` (whichever site made it), or (None, None)."""
    out = Path(out)
    for p in SITES.values():
        f = out / p.db_name
        if f.is_file():
            return f, p
    return None, None


def all_aliases() -> dict:
    out = {}
    for p in SITES.values():
        out.update(p.api_aliases)
    return out
