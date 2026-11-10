"""The built-in help: every link in the guides leads somewhere, and plain-language questions find the right page."""
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "app" if (_ROOT / "app" / "packrat").is_dir() else _ROOT))
os.environ.setdefault("PACKRAT_HOME", tempfile.mkdtemp(prefix="packrat-test-home-"))

from packrat import guides as G  # noqa: E402
from packrat import helpsearch as H  # noqa: E402
from packrat.grab.settings import PAGES, SETTINGS  # noqa: E402

TABS = {"browse", "downloads", "favorites", "monitors", "archive", "settings", "tools", "log"}
TOPIC_IDS = {tid for tid, _, _ in G.TOPICS}


def headings(tid):
    text = next(t for i, _, t in G.TOPICS if i == tid)
    return {h.strip() for h in re.findall(r"^## (.+)$", text, flags=re.M)}


def valid(target):
    if target in ("setup", "sources", "advanced:on", "advanced:off") or target.startswith("url:"):
        return True
    if target.startswith("tab:"):
        return target[4:] in TABS
    if target.startswith("settings:"):
        return target[9:] in PAGES or target[9:] == "Basics"
    if target.startswith("site:"):
        return target[5:] in G.SITES
    if target.startswith("help:"):
        tid, _, anchor = target[5:].partition("#")
        return tid in TOPIC_IDS and (not anchor or anchor in headings(tid))
    return False


class Links(unittest.TestCase):
    def test_guide_links(self):
        texts = [t for _, _, t in G.TOPICS] + [G.site_text(h) for h in G.SITES]
        for text in texts:
            for target in re.findall(r"\]\(go:([^)]+)\)", text):
                self.assertTrue(valid(target), target)

    def test_question_targets(self):
        for q, target in H.QUESTIONS:
            self.assertTrue(valid(target), f"{q} -> {target}")

    def test_brackets_survive_links(self):
        t = 'help:problems#"Access refused (403)"'
        self.assertNotIn(")", H.link(t))
        self.assertEqual(H.unlink(H.link(t)), t)
        md = H.results_text("403", H.build([], None).search("403"))
        for target in re.findall(r"\]\(go:([^)]+)\)", md):
            self.assertTrue(valid(H.unlink(target)), target)


class Search(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ix = H.build([(h, "") for h in G.SITES], SETTINGS, False)

    def top(self, q, n=3):
        return [d.target.split("#")[0] for _, d, _ in self.ix.search(q)[:n]]

    def test_everyday_questions(self):
        cases = {
            "how do I save a whole search": "help:download",
            "where are my files": "help:download",
            "name files by artist": "help:names",
            "no pictures show up": "help:problems",
            "hide gore": "help:filters",
            "error 429": "help:problems",
            "copy whole e6ai site": "help:archive",
            "where did tools go": "help:tools",
            "keep packrat running next to the clock": "help:advanced",
            "setup again": "setup",
            "get new pictures automatically": "help:favorites",
            "e621 wont log in": "site:e621.net",
            "pixiv login": "site:www.pixiv.net",
            "stop downloading": "help:download",
            "dark mode": "help:start",
        }
        for q, want in cases.items():
            self.assertIn(want, self.top(q), q)

    def test_words(self):
        self.assertEqual(H.words("Downloading pictures"), ["download", "picture"])
        self.assertIn("e621", H.words("e621.net"))
        self.assertIn("download", H.concepts("save"))      # synonyms
        self.assertIn("problem", H.concepts("search doesn't work"))

    def test_nothing_found(self):
        self.assertEqual(self.ix.search("qqqzzz"), [])
        self.assertIn("Nothing found", H.results_text("qqqzzz", []))
        self.assertEqual(self.ix.search("the and of"), [])


if __name__ == "__main__":
    unittest.main()
