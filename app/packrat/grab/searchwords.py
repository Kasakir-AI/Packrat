"""Packrat's search words: one way to ask for a sort order, a rating or a score, whatever the site.

Every site writes these differently. Danbooru and Gelbooru have four ratings (general, sensitive, questionable,
explicit) and on Danbooru rating:s means *sensitive*; e621, Moebooru and Sankaku have three and rating:s means
*safe*; Derpibooru uses the tags safe / suggestive / questionable / explicit; Zerochan has no ratings at all.
"Best first" is order:score on most boorus (Gelbooru's source turns order: into its sort:), order:toplist on
Wallhaven and order:fav on Zerochan; Derpibooru writes a minimum score as score.gte:10.

People write the words once, in Packrat's form, and translate() rewrites them for each site just before that
site is searched:

    order:popular     best first             (also order:score, order:top, order:best, sort:score)
    order:favorites   most favorited first   (also order:favcount, order:fav, order:faves)
    order:newest      newest first           (the normal order)
    order:oldest      oldest first           (also order:id, sort:id:asc)
    order:random      random order
    rating:sfw        safe for work only     (also rating:general, rating:safe)
    rating:sensitive  suggestive, not nude   (Danbooru / Gelbooru's own level; elsewhere their safe level)
    rating:questionable, rating:explicit
    rating:nsfw       questionable or explicit
    -rating:explicit  anything but explicit  (a minus works with every rating word)
    score:>10         a minimum (or maximum) score

What a site can't do itself, Packrat does on the pictures the site sends (rating, score) and says so in a note.
Words in a site's own short form (rating:e, order:mpixels, sort:updated ...) are passed on untouched.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# levels: 0 general (safe for work), 1 sensitive, 2 questionable, 3 explicit
ALL = frozenset({0, 1, 2, 3})
RATING_WORDS = {"sfw": {0}, "general": {0}, "safe": {0}, "sensitive": {1}, "suggestive": {1},
                "questionable": {2}, "explicit": {3}, "nsfw": {2, 3}}
LEVEL_NAMES = ("safe for work", "sensitive", "questionable", "explicit")

SORT_WORDS = {"popular": "popular", "score": "popular", "top": "popular", "best": "popular",
              "favorites": "favorites", "favourites": "favorites", "favcount": "favorites", "fav": "favorites",
              "faves": "favorites", "favs": "favorites",
              "newest": "newest", "new": "newest", "latest": "newest", "id_desc": "newest",
              "oldest": "oldest", "old": "oldest", "id": "oldest", "id_asc": "oldest",
              "random": "random"}
SORT_NAMES = {"popular": "best first", "favorites": "most favorited first", "newest": "newest first",
              "oldest": "oldest first", "random": "random order"}
# the site's own word for each sort, first match in the source's list of modifiers wins
SORT_CHOICES = {"popular": ("order:score", "order:toplist", "order:fav"),
                "favorites": ("order:favcount", "order:favorites", "order:faves", "order:fav"),
                "oldest": ("order:id_asc", "order:id"),
                "random": ("order:random",)}
# sorts these sources understand though their list doesn't say so
EXTRA_SORTS = {"Danbooru (2.0)": {"order:random"}, "Gelbooru (0.2)": {"order:random"}, "Moebooru": {"order:random"}}
# order:id means newest first there, not oldest
ID_IS_NEWEST = {"Zerochan"}
# Gelbooru-style sites sort with sort:<what>[:asc]; their list also names order: words they don't have
OWN_SORTS = {"Gelbooru (0.2)": {"popular": "sort:score", "oldest": "sort:id:asc", "random": "sort:random"}}

ALL_SAFE_HOSTS = {"safebooru.org", "e926.net"}
NO_NEGATION = {"WallHaven"}       # its rating words choose a purity filter; a minus is not understood

_SCORE = re.compile(r"^score:(>=|<=|>|<)?(-?\d+)$")
_PHILOMENA_OPS = {">": "gt", ">=": "gte", "<": "lt", "<=": "lte"}


@dataclass
class Translated:
    """A query rewritten for one site, plus what Packrat checks itself on the pictures it receives."""
    query: str
    notes: list = field(default_factory=list)
    rating: frozenset | None = None     # levels to keep, checked by Packrat (the site couldn't)
    scheme: str = "none"
    scores: list = field(default_factory=list)   # (op, value) checked by Packrat

    def keeps(self, img) -> bool:
        if self.rating is not None:
            lv = rating_level(img, self.scheme)
            if lv is not None and lv not in _collapse(self.rating, self.scheme):
                return False
        for op, v in self.scores:
            try:
                s = int(float(img.score))
            except (TypeError, ValueError):
                continue                 # no score from this site: can't judge, keep it
            if not {">": s > v, ">=": s >= v, "<": s < v, "<=": s <= v}[op]:
                return False
        return True

    @property
    def checks(self) -> bool:
        return self.rating is not None or bool(self.scores)


def rating_scheme(source_name: str, host: str, modifiers) -> str:
    """four (general/sensitive/questionable/explicit), three (safe/questionable/explicit), tags (Derpibooru),
    allsafe (only safe pictures) or none (no ratings to search by)."""
    host = host.split("/")[0].lower()
    if host in ALL_SAFE_HOSTS:
        return "allsafe"
    if source_name.startswith("Danbooru"):
        return "four"
    if source_name.startswith("Gelbooru") and (host == "gelbooru.com" or host.endswith(".gelbooru.com")):
        return "four"
    if source_name == "Philomena":
        return "tags"
    if any(m.startswith("rating:") for m in modifiers):
        return "three"
    return "none"


def _collapse(levels, scheme: str) -> frozenset:
    """Three-level sites have one 'safe' for general and sensitive."""
    if scheme in ("three", "none", "allsafe") and levels & {0, 1}:
        return frozenset(levels | {0, 1})
    return frozenset(levels)


def rating_level(img, scheme: str):
    """The picture's rating as a level, or None when the site gave none."""
    r = str(getattr(img, "rating", "") or "").strip().lower()
    if not r and scheme == "tags":
        names = {getattr(t, "name", t).lower() for t in getattr(img, "tags", [])}
        for lv, tag in ((3, "explicit"), (2, "questionable"), (1, "suggestive"), (0, "safe")):
            if tag in names:
                return lv
        return None
    if r in ("g", "general"):
        return 0
    if r in ("s", "safe"):
        return 1 if (scheme == "four" and r == "s") else 0
    if r == "sensitive":
        return 1
    if r in ("q", "questionable"):
        return 2
    if r in ("e", "explicit"):
        return 3
    return None


def _site_name(site) -> str:
    return getattr(site, "name", "") or site.host


def translate(query: str, site) -> Translated:
    """Rewrite Packrat's search words in `query` for `site` (a grab.sources.Site)."""
    source = site.source.name
    mods = set(site.source.meta.get("modifiers") or []) | EXTRA_SORTS.get(source, set())
    scheme = rating_scheme(source, site.host, mods)
    name = _site_name(site)
    out, notes, scores = [], [], []
    sort = None
    pos, minus = set(), set()          # ratings asked for, ratings left out
    for w in query.split():
        low = w.lower()
        neg = low.startswith("-")
        body = low[1:] if neg else low
        key, _, val = body.partition(":")
        if key in ("order", "sort") and not neg:
            parts = val.split(":")
            s = SORT_WORDS.get(parts[0])
            if key == "sort" and parts[0] == "id":
                s = "oldest" if parts[1:] == ["asc"] else "newest"
            elif key == "sort" and parts[1:] == ["asc"]:
                s = None                 # e.g. Gelbooru's sort:score:asc (worst first): its own word
            if s is None:
                out.append(w)            # the site's own sort word
                continue
            sort = s
            continue
        if key == "rating" and val in RATING_WORDS:
            (minus if neg else pos).update(RATING_WORDS[val])
            continue
        m = _SCORE.match(low)
        if m:
            op, v = m.group(1) or ">=", int(m.group(2))
            if "score:" in mods:
                out.append(w)
            elif source == "Philomena":
                out.append(f"score.{_PHILOMENA_OPS[op]}:{v}")
            else:
                scores.append((op, v))
                notes.append(f"{name} can't search by score; Packrat leaves out the pictures below it.")
            continue
        out.append(w)

    if sort is not None:
        word = _sort_word(sort, source, mods)
        if not word and sort == "favorites" and _sort_word("popular", source, mods):
            word = _sort_word("popular", source, mods)
            notes.append(f"{name} can't sort by favorites; best first instead.")
        elif not word and sort != "newest":
            notes.append(f"{name} can't sort {SORT_NAMES[sort]}; its pictures come in their normal order.")
        if word:
            out.append(word)

    rating = None
    want = ((pos or set(ALL)) - minus) if (pos or minus) else None
    if want is not None and frozenset(want) != ALL:
        words, check, note = _rating_words(frozenset(want), scheme, source, mods, name)
        out += words
        rating = check
        if note:
            notes.append(note)
    return Translated(" ".join(out), notes, rating, scheme, scores)


def _sort_word(sort: str, source: str, mods) -> str:
    if sort == "newest":
        return "order:date_added" if "order:date_added" in mods else ""
    if source in OWN_SORTS:
        return OWN_SORTS[source].get(sort, "")
    for word in SORT_CHOICES[sort]:
        if word == "order:id" and source in ID_IS_NEWEST:
            continue
        if word in mods:
            return word
    return ""


def _rating_words(want: frozenset, scheme: str, source: str, mods, name: str):
    """(words for the query, levels Packrat checks itself or None, a note or '')."""
    if not want:
        return [], want, f"No picture can match those ratings on {name}."
    if scheme == "allsafe":
        if want & {0, 1}:
            return [], None, ""
        return [], want, f"{name} has only safe pictures."
    if scheme == "none":
        return [], want, (f"{name} doesn't rate its pictures; Packrat leaves out those rated otherwise "
                          f"(most of its pictures have no rating, so they stay).")
    if scheme == "tags":                     # Derpibooru: safe, suggestive, questionable, explicit are tags
        tags = ("safe", "suggestive", "questionable", "explicit")
        if len(want) == 1:
            return [tags[next(iter(want))]], None, ""
        return [f"-{tags[lv]}" for lv in sorted(ALL - want)], None, ""
    if scheme == "four":
        words = ("general", "sensitive", "questionable", "explicit")
        if len(want) == 1:
            return [f"rating:{words[next(iter(want))]}"], None, ""
        if len(want) == 3:
            return [f"-rating:{words[next(iter(ALL - want))]}"], None, ""
        if source.startswith("Danbooru"):    # one word, so it doesn't use up a tag of Danbooru's 2
            return ["rating:" + ",".join(words[lv] for lv in sorted(want))], None, ""
        return [f"-rating:{words[lv]}" for lv in sorted(ALL - want)], None, ""
    # three levels: safe covers general and sensitive
    levels = sorted({0 if lv < 2 else lv for lv in want})
    note = ""
    if 1 in want and 0 not in want:
        note = f"{name} has no separate 'sensitive' rating; its 'safe' pictures are shown."
    long = "rating:safe" in mods
    names = {0: "safe" if long else "s", 2: "questionable" if long else "q", 3: "explicit" if long else "e"}
    if len(levels) == 1:
        return [f"rating:{names[levels[0]]}"], None, note
    if source in NO_NEGATION:
        return [], want, f"{name} can search one rating at a time; Packrat leaves out the other pictures."
    return [f"-rating:{names[lv]}" for lv in (0, 2, 3) if lv not in levels], None, note


# ---- the Sort and Rating menus of the Browse page ------------------------------------------------------
SORT_MENU = (("Newest first", ""), ("Most popular", "order:popular"), ("Most favorited", "order:favorites"),
             ("Oldest first", "order:oldest"), ("Random", "order:random"))
RATING_MENU = (("All ratings", ""), ("Safe for work", "rating:sfw"), ("Sensitive", "rating:sensitive"),
               ("Questionable", "rating:questionable"), ("Explicit", "rating:explicit"),
               ("Not safe for work", "rating:nsfw"), ("No explicit", "-rating:explicit"))


def _kind(word: str) -> str:
    low = word.lower()
    body = low[1:] if low.startswith("-") else low
    key, _, val = body.partition(":")
    if key in ("order", "sort") and not low.startswith("-") and SORT_WORDS.get(val.split(":")[0]):
        return "sort"
    if key == "rating" and val in RATING_WORDS:
        return "rating"
    return ""


def _sort_of(words) -> str:
    for w in words:
        key, _, val = w.lower().partition(":")
        parts = val.split(":")
        s = SORT_WORDS.get(parts[0])
        if key == "sort" and parts[0] == "id":
            s = "oldest" if parts[1:] == ["asc"] else "newest"
        if s:
            return "" if s == "newest" else f"order:{s}"
    return ""


def _rating_of(words):
    pos, minus = set(), set()
    for w in words:
        low = w.lower()
        (minus if low.startswith("-") else pos).update(RATING_WORDS[low.lstrip("-").split(":", 1)[1]])
    return frozenset((pos or set(ALL)) - minus) if (pos or minus) else ALL


def menu_value(query: str, kind: str):
    """The Sort / Rating menu word matching the search box, '' for the normal choice, None when the words in the
    box are a combination the menu doesn't list."""
    words = [w for w in query.split() if _kind(w) == kind]
    if kind == "sort":
        return _sort_of(words)
    want = _rating_of(words)
    for _label, word in RATING_MENU:
        if _rating_of(word.split()) == want:
            return word
    return None


def with_word(query: str, kind: str, word: str) -> str:
    """The search with its sort (or rating) words replaced by `word`."""
    kept = [w for w in query.split() if _kind(w) != kind]
    return " ".join(kept + ([word] if word else []))
