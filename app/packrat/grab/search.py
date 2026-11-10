"""Searching several sites at once (a Browse tab, a favorite, a monitor, the command line).

Adds the global "added tags" and post-filter, asks every selected site in parallel, then for each result:
marks blacklisted images (hidden or greyed out, per Settings), drops images the post-filter rejects (and
counts them), marks images already downloaded (MD5 list / file exists), and - when "merge results" is on -
removes duplicates found on several sites (same MD5), keeping the first.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import filename as F
from .filters import Blacklist, PostFilter
from .sources import PageResult

log = logging.getLogger("packrat.grab")


@dataclass
class Shown:
    """An image as a search tab shows it."""
    image: object
    site: object
    blacklisted: list = field(default_factory=list)
    downloaded: bool = False
    favorited: bool = False


@dataclass
class SearchResult:
    query: str
    page: int
    pages: list = field(default_factory=list)      # PageResult per site
    items: list = field(default_factory=list)      # Shown (merged or per site, in site order)
    tags: list = field(default_factory=list)       # (name, type, count) aggregated
    filtered: int = 0
    duplicates: int = 0
    errors: list = field(default_factory=list)
    wiki: str = ""

def effective_query(query: str, st) -> str:
    add = str(st.get("add") or "").split()
    words = query.split()
    return " ".join(words + [a for a in add if a not in words])


def run(query: str, sites: list, page: int, per_page: int, st, lib=None, postfilter: str = "",
        merge: bool = False, previous: dict | None = None, cancel: threading.Event | None = None,
        mark_downloaded: bool = True, gallery=None) -> SearchResult:
    """gallery: an Image of type gallery; its pictures are listed instead of a search (Grabber's gallery tab)."""
    q = effective_query(query, st) if gallery is None else query
    res = SearchResult(query, page)
    previous = previous or {}

    def one(site):
        try:
            if gallery is not None:
                return site.gallery(gallery, page, per_page, cancel=cancel)
            return site.search(q, page, per_page, previous.get(site.host), cancel=cancel)
        except Exception as e:  # noqa: BLE001 - one broken site never breaks the tab
            log.exception("search on %s failed", site.host)
            return PageResult(site, q, page, error=f"{e.__class__.__name__}: {e}")
    with ThreadPoolExecutor(max(1, min(8, len(sites))), thread_name_prefix="search") as pool:
        pages = list(pool.map(one, sites))
    res.pages = pages
    gpf = "" if st.get("globalPostFilterExplicit") else str(st.get("globalPostFilter") or "")   # explicit: in the tab
    pf = PostFilter(" ".join(x for x in (postfilter, gpf) if x.strip()))
    bl = Blacklist(st.lines("blacklistedtags") if hasattr(st, "lines") else [])
    hide_bl = bool(st.get("hideblacklisted"))
    favs = {f["name"].lower() for f in lib.favorites()} if lib is not None else set()
    seen_md5: set[str] = set()
    tag_counts: dict[str, list] = {}
    for pr in pages:
        if pr.error:
            res.errors.append(f"{pr.site.name}: {pr.error}")
        if pr.wiki and not res.wiki:
            res.wiki = pr.wiki
        for t in pr.tags:
            cur = tag_counts.setdefault(t.name, [t.name, t.type, 0])
            cur[2] += t.count
            if t.type and not cur[1]:
                cur[1] = t.type
        for img in pr.images:
            toks = F.build_tokens(img, st, site=pr.site)
            if pf:
                why = pf.match(toks)
                if why:
                    res.filtered += 1
                    continue
            hit = bl.match(toks) if bl else []
            if hit and hide_bl:
                res.filtered += 1
                continue
            if merge and img.md5:
                if img.md5 in seen_md5:
                    res.duplicates += 1
                    continue
                seen_md5.add(img.md5)
            sh = Shown(img, pr.site, hit)
            if mark_downloaded and lib is not None and st.get("Save/md5DownloadedCheck", True):
                sh.downloaded = bool((img.md5 and lib.md5_path(img.md5) is not None)
                                     or lib.was_saved(img.site_key, img.id))
            names = {t.name.lower() for t in img.tags}
            sh.favorited = bool(favs & names)
            res.items.append(sh)
    if not tag_counts:   # sites that give no tag list: count the tags of the results
        for sh in res.items:
            for t in sh.image.tags:
                cur = tag_counts.setdefault(t.name, [t.name, t.type, 0])
                cur[2] += 1
    res.tags = sorted((tuple(v) for v in tag_counts.values()), key=lambda x: (-x[2], x[0]))
    if lib is not None:
        try:
            threshold = int(st.get("tagsautoadd") or 10)
            lib.add_words([t[0] for t in res.tags if t[2] >= threshold or t[2] == 0][:500])
        except Exception:  # noqa: BLE001
            pass
    return res


# ---- one post from its page address (Grabber: "add unique", CLI --get-details) -----------------------------------
_POST_ID = (r"/(?:posts?|show|view|artworks|illust|image|images|gallery|g|post/show|index\.php)/(?:show/|view/)?(\d+)",)


def parse_post_address(store, address: str):
    """(site, post id) from an address like https://e621.net/posts/123; ValueError with a plain message."""
    import re
    from urllib.parse import parse_qs, urlparse
    address = address.strip()
    u = urlparse(address if "://" in address else "https://" + address)
    host = u.netloc.lower()
    site = store.get(host) or store.get(host.removeprefix("www.")) or store.get("www." + host)
    if site is None:
        raise ValueError(f"{host or address} is not one of your sites. Add it first (Sites > Add another site...).")
    pid = (parse_qs(u.query).get("id") or [None])[0]
    if not pid:
        m = re.search(_POST_ID[0], u.path) or re.search(r"(\d{2,})", u.path)
        pid = m.group(1) if m else None
    if not pid:
        raise ValueError("There is no picture number in this address. Open the picture on the site and copy the "
                         "address from there.")
    return site, str(pid)


def find_post(store, address: str, details: bool = True):
    """The (site, Image) a picture's page address points to."""
    site, pid = parse_post_address(store, address)
    pr = site.search(f"id:{pid}", 1, 5)
    im = next((i for i in pr.images if str(i.id) == pid), None)
    if im is None:
        raise ValueError(f"Picture {pid} was not found on {site.name}" + (f" ({pr.error})." if pr.error else "."))
    if details:
        try:
            site.details(im)
        except Exception:  # noqa: BLE001 - the picture itself is enough
            log.debug("details for %s failed", pid, exc_info=True)
    return site, im
