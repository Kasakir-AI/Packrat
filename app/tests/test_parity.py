"""Grabber features added in the parity review: picture addresses, galleries, wiki text, tag types, site removal."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "app" if (_ROOT / "app" / "packrat").is_dir() else _ROOT))
os.environ.setdefault("PACKRAT_HOME", tempfile.mkdtemp(prefix="packrat-test-home-"))

from packrat.grab import search as S  # noqa: E402
from packrat.grab import secrets  # noqa: E402
from packrat.grab import sources  # noqa: E402
from packrat.grab.image import Image  # noqa: E402
from packrat.grab.sources import PageResult  # noqa: E402
from packrat.textutil import html_to_text  # noqa: E402


class FakeSite:
    def __init__(self, host):
        self.host = host
        self.name = host
        self.calls = []

    def search(self, q, page, limit, prev=None, cancel=None):
        self.calls.append(("search", q))
        im = Image(self.host)
        im.id = 12345
        pr = PageResult(self, q, page)
        pr.images = [im]
        return pr

    def gallery(self, gal, page, limit, cancel=None):
        self.calls.append(("gallery", gal.id))
        pr = PageResult(self, "", page)
        for n in range(3):
            im = Image(self.host)
            im.id = 100 + n
            im.md5 = f"{n:032x}"
            pr.images.append(im)
        return pr

    def details(self, im):
        self.calls.append(("details", im.id))


class FakeStore:
    def __init__(self, *hosts):
        self.sites = {h: FakeSite(h) for h in hosts}

    def get(self, host):
        return self.sites.get(host)


class FakeSettings(dict):
    def get(self, k, d=None):
        return super().get(k, d)

    def lines(self, k):
        return []


class Addresses(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore("e621.net", "danbooru.donmai.us", "gelbooru.com", "www.pixiv.net")

    def test_parse(self):
        cases = {
            "https://e621.net/posts/12345": ("e621.net", "12345"),
            "e621.net/posts/12345?q=cat": ("e621.net", "12345"),
            "https://danbooru.donmai.us/posts/777": ("danbooru.donmai.us", "777"),
            "https://gelbooru.com/index.php?page=post&s=view&id=9001": ("gelbooru.com", "9001"),
            "https://www.pixiv.net/en/artworks/112233": ("www.pixiv.net", "112233"),
            "https://pixiv.net/artworks/112233": ("www.pixiv.net", "112233"),
        }
        for addr, (host, pid) in cases.items():
            site, got = S.parse_post_address(self.store, addr)
            self.assertEqual((site.host, got), (host, pid), addr)

    def test_errors_are_plain(self):
        with self.assertRaisesRegex(ValueError, "not one of your sites"):
            S.parse_post_address(self.store, "https://example.org/posts/1")
        with self.assertRaisesRegex(ValueError, "no picture number"):
            S.parse_post_address(self.store, "https://e621.net/posts")

    def test_find_post(self):
        site, im = S.find_post(self.store, "https://e621.net/posts/12345")
        self.assertEqual(im.id, 12345)
        self.assertIn(("search", "id:12345"), site.calls)
        self.assertIn(("details", 12345), site.calls)
        with self.assertRaisesRegex(ValueError, "not found"):
            S.find_post(self.store, "https://e621.net/posts/99")


class Galleries(unittest.TestCase):
    def test_gallery_tab_lists_its_pictures(self):
        site = FakeSite("e-hentai.org")
        gal = Image(site.host)
        gal.id = 55
        gal.is_gallery = True
        res = S.run("some gallery", [site], 1, 20, FakeSettings(), None, gallery=gal, mark_downloaded=False)
        self.assertEqual(site.calls, [("gallery", 55)])          # no search request
        self.assertEqual([sh.image.id for sh in res.items], [100, 101, 102])


class Wiki(unittest.TestCase):
    def test_html(self):
        t = html_to_text('<p>A <b>cat</b> &amp; dog.</p><ul><li>one</li><li>two</li></ul><p>End</p>')
        self.assertEqual(t, "A cat & dog.\n\n  • one\n  • two\n\nEnd")

    def test_dtext(self):
        self.assertEqual(html_to_text("See [[cat ears|ears]] and [[tail]], [b]bold[/b]."),
                         "See ears and tail, bold.")
        self.assertEqual(html_to_text("  "), "")


class SiteRemoval(unittest.TestCase):
    def test_forgets_saved_login(self):
        st = sources.store()
        host = next(iter(st.sites))
        secrets.set_secret(host, "apiKey", "secret-key-123")
        secrets.set_secret(host, "session_cookies", "{}")
        st.remove_site(host)
        self.assertEqual(secrets.get_secret(host, "apiKey"), "")
        self.assertEqual(secrets.get_secret(host, "session_cookies"), "")


if __name__ == "__main__":
    unittest.main()
