"""Plain-language search for the built-in help.

Questions like "how do I save a whole search" or "e621 wont log in" are matched against the guide topics, every
site's guide, the settings and a list of everyday questions. Words are reduced to a simple stem and grouped
with their synonyms (save = download = grab ...), then ranked with a small BM25-style score.
No network, no extra packages."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

# ---- everyday questions: (question, target). Targets may end in #Heading to jump to a section. ----------------
QUESTIONS: list[tuple[str, str]] = [
    ("How do I start? What do I do first?", "help:start"),
    ("How do I choose which sites to search?", "help:sites#The site list"),
    ("Which site should I use? Which sites are safe / not adult?", "help:sites"),
    ("How do I search for pictures?", "help:search"),
    ("How do I search for two things at once?", "help:search#Tags"),
    ("How do I exclude something from a search?", "help:search#Tags"),
    ("How do I show only safe pictures (no adult content)?", "help:search#Useful extras (on most tag sites)"),
    ("How do I sort by best / most popular / oldest?", "help:search#Useful extras (on most tag sites)"),
    ("How do I get the next page of results?", "help:search#Pages"),
    ("How do I go back to my previous search? Recent searches / history", "help:search#Going back, recent searches"),
    ("I have a link / URL / address of a picture - how do I get it?", "help:search#A picture's address"),
    ("How do I open a gallery, album or pool?", "help:search#Galleries and albums"),
    ("What does this tag mean? (wiki)", "help:search#About a tag"),
    ("How do I sort by best / random without special words? (more options)",
     "help:search#Sorting, safe only, big pictures"),
    ("How do I copy a picture to paste it somewhere?", "help:view#Buttons"),
    ("What are the keyboard shortcuts?", "help:start#Keyboard shortcuts"),
    ("How do I add one picture to the download list?", "help:download#The Downloads tab"),
    ("How do I search several sites together?", "help:search#Several sites at once"),
    ("Why are there no pictures / no results?", "help:problems#No pictures found"),
    ("How do I see a picture big / full screen?", "help:view"),
    ("What are the keyboard shortcuts in the picture window?", "help:view#Keys"),
    ("Can I watch videos and animated GIFs?", "help:view"),
    ("How do I save one picture?", "help:view"),
    ("How do I download everything from a search?", "help:download#Three ways (buttons under the results)"),
    ("How do I download only some pictures?", "help:download#Three ways (buttons under the results)"),
    ("I pressed download but nothing happens / nothing is saved", "help:download#The Downloads tab"),
    ("How do I pause or stop downloads and continue later?", "help:download#The Downloads tab"),
    ("Where are my pictures saved? How do I change the folder?", "help:download#Where the files go"),
    ("How do I name files by artist / character / series?", "help:names#Ready-made formats (the **Presets** button)"),
    ("How do I sort pictures into folders?", "help:names"),
    ("How do I rename files I already have?", "help:names#Files you already have"),
    ("Will it download the same picture twice? (duplicates)", "help:download#Duplicates"),
    ("Is it too fast? Will the site block me?", "help:download#Being polite"),
    ("How do I log in to a site? Where do I put my API key?", "help:sites#Logins"),
    ("Login refused / test login fails", "help:problems#\"Login refused (401)\" / login test fails"),
    ("Access refused / error 403", "help:problems#\"Access refused (403)\""),
    ("Too many requests / error 429 / site is slow", "help:problems#\"Too many requests (429)\" or the site is slow"),
    ("Checking your browser / Cloudflare page", "help:problems#The site shows a \"checking your browser\" page (Cloudflare)"),
    ("A site stopped working", "help:problems#A site stopped working"),
    ("How do I add a site that isn't in the list?", "help:sites#Adding a site"),
    ("How do I hide pictures with certain tags? (blacklist)", "help:filters#Blacklist"),
    ("How do I filter results by score / size / rating?", "help:filters#Filter (in each search tab)"),
    ("How do I keep a search for later? (favorites)", "help:favorites#Favorites"),
    ("How do I find new pictures since last time?", "help:favorites#Favorites"),
    ("How do I get new pictures automatically? (monitors)", "help:favorites#Monitors"),
    ("How do I copy a whole site (e6ai / e621 archive)?", "help:archive"),
    ("e6ai is closing - how do I save everything?", "help:archive"),
    ("How do I import my Grabber settings and favorites?", "help:tools"),
    ("Where is the log? How do I see what went wrong?", "help:problems#Where is the log?"),
    ("Where are the Monitors / Tools / Log pages?", "help:advanced"),
    ("How do I keep Packrat running next to the clock? (tray icon, background)", "help:advanced#The icon next to the clock"),
    ("How do I log in with the built-in browser? (Cloudflare, checking your browser, Pixiv)", "help:advanced#Logging in on a site's own page"),
    ("How do I play a video or an animated picture?", "help:view#Buttons"),
    ("How do I see more options / all settings?", "help:advanced"),
    ("How do I run the setup assistant again?", "setup"),
    ("How do I switch to dark mode?", "help:start"),
]

STOP = set("""a an the i im me my mine we our you your it its is are was were be been am to of in on at for with and or
do does did doing done can could would should will shall may might must how what why when which
who whom whose this that these those there here get gets got getting want wants need needs please help using use
used way any some from by as about into out up down so than then just only also very really thing things there
one ones if else does let lets make makes""".split())
# kept although common: "new", "all", "save", "more"

SYNONYMS = """
download save grab fetch store keep downloaded saving
picture image pic photo file post wallpaper illustration img jpg png
name rename filename naming title
folder directory location path place dir
login log sign signin account password key apikey api token credential user username auth
blacklist block blocked hide hidden filter exclude ban mute avoid unwanted
adult nsfw porn explicit lewd hentai questionable sexual
safe sfw clean family general
problem error broken fail failed failing work working stuck wrong issue bug crash freeze frozen empty nothing
new latest recent fresh since update
monitor watch automatic auto automatically schedule follow subscribe alert notify notification
favorite favourite fav bookmark star
site source booru website server
search find look browse query searching
tag keyword
page next previous more
duplicate dupe double twice already same again
video movie animation gif clip webm mp4 animated
big large full original highres fullsize resolution
small smaller thumbnail preview sample
start begin resume continue first
stop pause cancel halt
dark theme color colour light night mode
advanced expert simple option options setting settings
setup assistant wizard welcome
grabber import imgbrd
archive mirror whole entire backup everything copy
fast slow speed quick limit throttle rate
sort order best popular score top oldest newest
""".strip().splitlines()

# phrases that mean more than their words
PHRASES = [
    (r"\b(no|zero|0) (pictures?|results?|images?)\b|\bnothing (shows?|appears?|found|happens)\b|\bshows? up\b"
     r"|\b(doesn'?t|does not|don'?t|isn'?t|not|won'?t|can'?t|cannot) (work|show|load|find|appear|download|save)",
     " problem empty"),
    (r"\b(won'?t|can'?t|cannot|unable|refuse[sd]?|denied|invalid)\b", " problem"),
    (r"\bwhere\b.*\b(saved?|files?|pictures?|images?|downloads?|go|goes|went)\b", " folder"),
    (r"\b(no|not|without) (adult|nsfw|porn|explicit|lewd)\b", " safe"),
]
_CANON: dict[str, set[str]] = {}


def _stem(w: str) -> str:
    w = w.lower().replace("'", "")
    if len(w) > 5 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 5 and w.endswith("ing"):
        w = w[:-3]
        return w[:-1] if len(w) > 3 and w[-1] == w[-2] else w
    if len(w) > 4 and w.endswith("ed") and not w.endswith("eed"):
        return w[:-2] if not w.endswith("ied") else w[:-3] + "y"
    if len(w) > 4 and w.endswith("es") and w[-3] in "sxz":
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


for _line in SYNONYMS:
    _words = _line.split()
    for _w in _words:
        _CANON.setdefault(_stem(_w), set()).add(_words[0])

_WORD = re.compile(r"[a-z0-9][a-z0-9_.]*[a-z0-9]|[a-z0-9]", re.I)


def words(text: str) -> list[str]:
    """Stems of the meaningful words of a text ('e621.net' and 'cat_ears' stay one word)."""
    out = []
    low = text.lower().replace("\u2019", "'")
    for rx, extra in PHRASES:
        if re.search(rx, low):
            low += extra
    for raw in _WORD.findall(low.replace("'", "")):
        if raw in STOP or raw in ("no", "not", "dont", "doesnt", "isnt", "wont", "cant", "where"):
            continue
        out.append(_stem(raw))
        if "." in raw:   # e621.net -> also e621
            out.append(raw.split(".")[0])
    return out


def concepts(text: str) -> list[str]:
    """Words mapped to their synonym groups; words without a group stand for themselves."""
    out = []
    for w in words(text):
        out.extend(sorted(_CANON.get(w, {w})))
    return out


@dataclass
class Doc:
    target: str
    title: str
    body: str = ""
    kind: str = "guide"            # guide | site | setting | question
    where: str = ""                # shown under the title
    keys: str = ""                 # matched like the title, not shown (e.g. a site's address)
    tf_title: dict = field(default_factory=dict)
    tf_body: dict = field(default_factory=dict)
    blen: int = 1


def _tf(text: str) -> dict:
    d: dict[str, int] = {}
    for c in concepts(text):
        d[c] = d.get(c, 0) + 1
    return d


def _plain(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return text.replace("**", "").replace("`", "").replace("#", "").strip()


class Index:
    def __init__(self, docs: list[Doc]):
        self.docs = docs
        for d in docs:
            d.tf_title = _tf(d.title + " " + d.keys)
            d.tf_body = _tf(d.body)
            d.blen = max(1, sum(d.tf_body.values()))
        self.avg = sum(d.blen for d in docs) / max(1, len(docs))
        df: dict[str, int] = {}
        for d in docs:
            for c in set(d.tf_title) | set(d.tf_body):
                df[c] = df.get(c, 0) + 1
        n = len(docs)
        self.idf = {c: math.log(1 + (n - k + 0.5) / (k + 0.5)) for c, k in df.items()}

    def search(self, query: str, limit: int = 12) -> list[tuple[float, Doc, str]]:
        """[(score, doc, snippet)] best first; a question and the guide it points to are merged."""
        q = list(dict.fromkeys(concepts(query)))
        if not q:
            return []
        groups = [set(_CANON.get(w, {w})) for w in dict.fromkeys(words(query))]
        res = []
        for d in self.docs:
            s, hit = 0.0, 0
            for g in groups:
                best = 0.0
                for c in g:
                    idf = self.idf.get(c, 0.0)
                    if not idf:
                        continue
                    t = d.tf_title.get(c, 0)
                    b = d.tf_body.get(c, 0)
                    body = (b * 2.2) / (b + 1.2 * (0.25 + 0.75 * d.blen / self.avg)) if b else 0.0
                    best = max(best, idf * (3.0 * (1 if t else 0) + body))
                if best:
                    hit += 1
                s += best
            if not hit:
                continue
            s *= (hit / len(groups)) ** 1.5           # prefer answers that cover more of the question
            if d.kind == "question":
                s *= 1.25
            elif d.kind == "setting":
                s *= 0.8
            low = query.lower().strip(" ?!.")
            if len(low) > 3 and low in d.title.lower():
                s *= 1.5
            res.append((s, d))
        res.sort(key=lambda x: -x[0])
        out, seen = [], set()
        for s, d in res:
            key = d.target.split("#")[0] if d.kind != "setting" else d.target + d.title
            if key in seen and d.kind != "question":
                continue
            seen.add(key)
            out.append((s, d, self.snippet(d, groups)))
            if len(out) >= limit:
                break
        return out

    @staticmethod
    def snippet(d: Doc, groups) -> str:
        """The line of the page that answers the most of the question (not the page's own name)."""
        if d.kind in ("question", "setting"):
            return d.where
        in_title = set(d.tf_title)
        useful = [g for g in groups if not (g & in_title)] or groups
        best, best_n = "", 0
        for line in d.body.splitlines():
            plain = _plain(line).lstrip("-0123456789. ").strip()
            if len(plain) < 20 or plain.lower().startswith(("address:", "software:", "content:")):
                continue
            have = set(concepts(plain))
            n = sum(1 for g in useful if g & have)
            if n > best_n:
                best, best_n = plain, n
        if not best:
            return d.where
        return best if len(best) <= 170 else best[:167].rsplit(" ", 1)[0] + "..."


def build(sites: list[tuple[str, str]] | None = None, settings=None, advanced: bool = True,
          basics: dict | None = None) -> Index:
    """sites: [(host, source name)]; settings: grab.settings.SETTINGS; basics: {key: friendly label}."""
    from . import guides as G
    docs: list[Doc] = []
    titles = {tid: title for tid, title, _ in G.TOPICS}
    for tid, title, text in G.TOPICS:
        docs.append(Doc("help:" + tid, title, text, "guide", "Guide"))
        # each section on its own as well, so an answer can jump to the right heading
        parts = re.split(r"^## (.+)$", text, flags=re.M)
        for i in range(1, len(parts) - 1, 2):
            head, body = parts[i].strip(), parts[i + 1]
            docs.append(Doc(f"help:{tid}#{head}", f"{_plain(head)}", body, "guide", title))
    for q, target in QUESTIONS:
        tid = target[5:].split("#")[0] if target.startswith("help:") else ""
        where = ("Guide: " + titles.get(tid, tid)) if tid else "Opens the setup assistant"
        docs.append(Doc(target, q, "", "question", where))
    for host, src in sites or []:
        info = G.site_info(host, src)
        docs.append(Doc("site:" + host, info["name"], G.site_text(host, src), "site",
                        f"Site guide - {info['what']}", keys=host))
    if settings:
        for s in settings:
            if s.kind == "hidden":
                continue
            if basics and s.key in basics:
                label, page = basics[s.key], "Basics"
            elif advanced:
                label, page = s.label, s.page
            else:
                label, page = s.label, s.page
            docs.append(Doc("settings:" + page.replace(" (advanced view)", ""), label, s.help, "setting",
                            f"Setting - Settings > {page}"))
    return Index(docs)


def link(target: str) -> str:
    """A target safe inside (go:...) - section names can contain brackets."""
    return target.replace("(", "%28").replace(")", "%29")


def unlink(target: str) -> str:
    return target.replace("%28", "(").replace("%29", ")")


def results_text(query: str, found) -> str:
    """The results as guide text (rendered by ui_help.HelpView)."""
    def esc(t):
        return t.replace("[", "(").replace("]", ")").replace("`", "'").replace("**", "")
    if not found:
        lines = [f"# Nothing found for “{esc(query)}”", "",
                 "Try other words, or one of these:", ""]
        lines += [f"- [{esc(q)}](go:{link(t)})" for q, t in QUESTIONS[:10]]
        return "\n".join(lines)
    top = found[0][0]
    guides = [(d, sn) for s, d, sn in found if d.kind != "setting" and s >= top * 0.3]
    sets = [(d, sn) for s, d, sn in found if d.kind == "setting" and s >= top * 0.45][:3]
    lines = [f"# Help for \u201c{esc(query)}\u201d", ""]
    for i, (d, sn) in enumerate(guides[:6], 1):
        lines.append(f"{i}. [{esc(d.title)}](go:{link(d.target)})")
        if sn:
            lines.append(f"   ~ {esc(sn)}")
    if sets:
        lines += ["", "## Settings"]
        for d, sn in sets:
            lines.append(f"- [{esc(d.title)}](go:{link(d.target)})")
            lines.append(f"   ~ {esc(sn)}")
    lines += ["", "Not what you meant? Try other words, or pick a topic on the left."]
    return "\n".join(lines)
