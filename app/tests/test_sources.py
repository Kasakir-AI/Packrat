"""Grabber's own model tests (src/sites/*/model.spec.ts), run through Packrat's JavaScript engine, plus
offline tests of the other Browse / Downloads parts (no network).

Run from the Packrat folder:   python -m unittest discover -s tests
The fixtures in tests/fixtures come from Grabber (Apache-2.0, see app/packrat/sources/GRABBER-LICENSE.txt).
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "app" if (HERE.parent / "app" / "packrat").is_dir() else HERE.parent))
os.environ.setdefault("PACKRAT_HOME", tempfile.mkdtemp(prefix="packrat-test-"))

from packrat import paths  # noqa: E402
from packrat.grab.jsrt import SourceRuntime, esm_to_commonjs  # noqa: E402

FIX = HERE / "fixtures"
SOURCES = paths.bundled_sources_dir()
OPTS = {"page": 2, "limit": 10, "loggedIn": False, "baseUrl": "/"}


def runtime(name):
    return SourceRuntime(SOURCES / name / "model.js", SOURCES / "helper.js", name)


def call(rt, path, *args):
    r = rt.call(path, *args)
    if "__error" in r:
        raise AssertionError(r["__error"])
    return r.get("result")


def fixture(name):
    return (FIX / name).read_text("utf-8")


class Danbooru2(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rt = runtime("Danbooru (2.0)")

    def search_url(self, api, q):
        return call(self.rt, ["apis", api, "search", "url"], {"search": q, "page": 2}, OPTS, None)

    def test_json_search_url(self):
        self.assertEqual(self.search_url("json", "tag1"), "/posts.json?limit=10&page=2&tags=tag1")

    def test_json_search_parse(self):
        res = call(self.rt, ["apis", "json", "search", "parse"], fixture("danbooru2-search.json"), 200)
        self.assertEqual(len(res["images"]), 10)
        self.assertEqual([i["id"] for i in res["images"][:3]], [4018204, 4018203, 4018202])

    def test_json_tags(self):
        url = call(self.rt, ["apis", "json", "tags", "url"], {"page": 2, "order": "count"}, OPTS)
        self.assertEqual(url, "/tags.json?limit=10&search[order]=count&page=2")
        res = call(self.rt, ["apis", "json", "tags", "parse"], fixture("danbooru2-tags.json"), 200)
        self.assertEqual([t["name"] for t in res["tags"][:3]], ["multiple_girls", "blue_eyes", "blonde_hair"])

    def test_xml_search(self):
        self.assertEqual(self.search_url("xml", "tag1"), "/posts.xml?limit=10&page=2&tags=tag1")
        res = call(self.rt, ["apis", "xml", "search", "parse"], fixture("danbooru2-search.xml"), 200)
        self.assertEqual(len(res["images"]), 10)
        self.assertEqual([int(i["id"]) for i in res["images"][:3]], [4018204, 4018203, 4018202])

    def test_xml_tags(self):
        res = call(self.rt, ["apis", "xml", "tags", "parse"], fixture("danbooru2-tags.xml"), 200)
        self.assertEqual(len(res["tags"]), 10)
        self.assertEqual([t["name"] for t in res["tags"][:3]], ["multiple_girls", "blue_eyes", "blonde_hair"])

    def test_html_details(self):
        self.assertEqual(call(self.rt, ["apis", "html", "details", "url"], "123", "",
                              {"baseUrl": "", "loggedIn": False}), "/posts/123")
        res = call(self.rt, ["apis", "html", "details", "parse"], fixture("danbooru2-details.html"), 200)
        self.assertEqual(res["imageUrl"], "https://danbooru.donmai.us/data/__kokkoro_princess_connect_and_1_more_"
                                          "drawn_by_hitotsuki_no_yagi__9b1bb70c1f123a07c8c1966a1ff76b79.jpg")

    def test_html_tag_types(self):
        res = call(self.rt, ["apis", "html", "tagTypes", "parse"], fixture("danbooru2-tags.html"))
        self.assertEqual([t["name"] for t in res["types"]], ["general", "character", "copyright", "artist", "meta"])

    def test_check(self):
        self.assertTrue(call(self.rt, ["apis", "html", "check", "parse"], fixture("danbooru2-home.html"), 200))
        self.assertFalse(call(self.rt, ["apis", "html", "check", "parse"], fixture("danbooru2-search.xml"), 200))


class Shimmie(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rt = runtime("Shimmie")

    def url(self, api, q):
        return call(self.rt, ["apis", api, "search", "url"], {"search": q, "page": 2}, OPTS, None)

    def test_rss(self):
        self.assertEqual(self.url("rss", ""), "/rss/images/2")
        self.assertIn("Tag search is impossible", self.url("rss", "tag")["error"])
        res = call(self.rt, ["apis", "rss", "search", "parse"], fixture("shimmie-search.rss"), 200)
        self.assertEqual(len(res["images"]), 24)
        self.assertEqual([int(i["id"]) for i in res["images"][:3]], [3410, 3409, 3408])

    def test_html_urls(self):
        self.assertEqual(self.url("html", ""), "/post/list/2")
        self.assertEqual(self.url("html", "tag"), "/post/list/tag/2")
        self.assertEqual(self.url("html", "Fate/Requiem"), "/post/list/Fate^sRequiem/2")
        self.assertEqual(self.url("html", "width:800 height:600"), "/post/list/size:800x600/2")
        self.assertEqual(self.url("html", "width:>=800 height:>=600"), "/post/list/size:>=800x600/2")
        self.assertEqual(self.url("html", "width:600..800"), "/post/list/width:>=600 width:<=800/2")
        self.assertEqual(self.url("html", "tagcount:3"), "/post/list/tags:3/2")

    def test_html_errors(self):
        self.assertEqual(call(self.rt, ["apis", "html", "search", "parse"], "", 404)["images"], [])
        self.assertEqual(call(self.rt, ["apis", "html", "search", "parse"], "", 500)["error"], "Loading error: HTTP 500")

    def test_html_parse(self):
        res = call(self.rt, ["apis", "html", "search", "parse"], fixture("shimmie-search.html"), 200)
        self.assertEqual(len(res["images"]), 24)
        self.assertEqual([int(i["id"]) for i in res["images"][:3]], [3410, 3409, 3408])

    def test_details(self):
        res = call(self.rt, ["apis", "html", "details", "parse"], fixture("shimmie-details.html"), 200)
        self.assertEqual([t["name"] if isinstance(t, dict) else t for t in res["tags"]], ["puchiko"])
        self.assertEqual(res["imageUrl"], "/_images/33ca85f6ab72d3d31bf95bf9a137d7df/3410%20-%20puchiko.jpg")
        self.assertEqual(res["createdAt"], "2019-08-21T09:21:25+00:00")

    def test_check(self):
        self.assertTrue(call(self.rt, ["apis", "html", "check", "parse"], fixture("shimmie-home.html"), 200))
        self.assertFalse(call(self.rt, ["apis", "html", "check", "parse"], fixture("shimmie-search.rss"), 200))


class EveryModelLoads(unittest.TestCase):
    def test_all(self):
        n = 0
        for d in sorted(SOURCES.iterdir()):
            if (d / "model.js").is_file():
                rt = runtime(d.name)
                self.assertTrue(rt.meta.get("apis"), d.name)
                n += 1
        self.assertGreaterEqual(n, 30)


class EsModules(unittest.TestCase):
    def test_export_var(self):
        out = esm_to_commonjs("export var source = {a: 1};\nexport function f() {}\nexport { f as g };")
        self.assertIn("var source =", out)
        self.assertIn("exports.source = source;", out)
        self.assertIn("exports.f = f;", out)
        self.assertIn("exports.g = f;", out)
        self.assertNotIn("export ", out.replace("exports.", ""))

    def test_untouched(self):
        self.assertEqual(esm_to_commonjs("exports.source = {};"), "exports.source = {};")


class Registry(unittest.TestCase):
    def _zip(self, files):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for n, data in files.items():
                z.writestr(n, data)
        return buf.getvalue()

    def test_install_and_uninstall(self):
        from packrat.grab import registry as R
        model = (SOURCES / "Shimmie" / "model.js").read_text("utf-8")
        import hashlib
        h = hashlib.sha256(model.encode()).hexdigest()
        d = R.install_zip(self._zip({"model.js": model, "sites.txt": "example.org\n"}), "Test Source", h)
        self.assertTrue((d / "model.js").is_file())
        self.assertEqual(R.load_state()["installed"]["Test Source"]["hash"], h)
        self.assertTrue(R.uninstall("Test Source"))

    def test_rejects_zip_slip(self):
        from packrat.grab import registry as R
        with self.assertRaises(ValueError):
            R.install_zip(self._zip({"../evil.js": "x", "model.js": "x"}), "Evil")

    def test_rejects_no_model(self):
        from packrat.grab import registry as R
        with self.assertRaises(ValueError):
            R.install_zip(self._zip({"readme.txt": "x"}), "Empty")

    def test_checksum_mismatch(self):
        from packrat.grab import registry as R
        model = (SOURCES / "Shimmie" / "model.js").read_text("utf-8")
        with self.assertRaises(R.ChecksumMismatch):
            R.install_zip(self._zip({"model.js": model}), "Bad Hash", "0" * 64)

    def test_protected(self):
        from packrat.grab import registry as R
        with self.assertRaises(ValueError):
            R.install({"name": "E621", "slug": "E621", "zip": "https://invalid.example/E621.zip"})


class GrabberImport(unittest.TestCase):
    def test_profile(self):
        from packrat.grab import grabber_import as G, library, settings, sources
        prof = Path(tempfile.mkdtemp(prefix="grabber-profile-"))
        (prof / "settings.ini").write_text(
            "[General]\nadd=rating:safe\nblacklistedtags=gore\nlimit=40\nsites=safebooru.org, e621.net\n\n"
            "[Save]\nfilename=%artist%/%md5%.%ext%\nmd5Duplicates=move\nCustoms\\hair=blonde_hair red_hair\n"
            "MetadataExiftool\\1\\key=XMP:Subject\nMetadataExiftool\\1\\value=%all%\nMetadataExiftool\\size=1\n\n"
            "[Filenames]\n0_cond=rating:explicit\n0_fn=%md5%.%ext%\n0_dir=C:/nsfw\n", "utf-8")
        (prof / "favorites.json").write_text(json.dumps({"favorites": [
            {"tag": "cat_ears", "note": 80, "lastViewed": "2024-05-01T10:00:00", "sites": ["safebooru.org"]}]}))
        (prof / "monitors.json").write_text(json.dumps({"monitors": [
            {"query": {"tags": ["dragon"]}, "sites": ["e621.net"], "interval": 7200, "delay": 600}]}))
        (prof / "viewitlater.txt").write_text("later_tag\n")
        (prof / "md5s.txt").write_text("0123456789abcdef0123456789abcdefC:/x/a.jpg\n")
        st = settings.Settings(Path(tempfile.mkdtemp()) / "s.json")
        lib = library.Library(Path(tempfile.mkdtemp()) / "l.sqlite3")
        store = sources.SiteStore(sites_file=Path(tempfile.mkdtemp()) / "sites.json")
        rep = G.import_profile(prof, st, lib, store)
        self.assertEqual(st.get("add"), "rating:safe")
        self.assertEqual(st.get("limit"), 40)
        self.assertEqual(st.get("sites"), ["safebooru.org", "e621.net"])
        self.assertEqual(st.get("Save/md5Duplicates"), "move")
        self.assertEqual(st.get("Customs"), [{"name": "hair", "tags": "blonde_hair red_hair"}])
        self.assertEqual(st.get("Filenames")[0]["dir"], "C:/nsfw")
        self.assertEqual(st.get("Save/MetadataExiftool"), [{"key": "XMP:Subject", "value": "%all%"}])
        self.assertIn("gore", st.lines("blacklistedtags"))
        self.assertEqual(lib.favorite("cat_ears")["note"], 80)
        m = lib.monitors()[0]
        self.assertEqual((m["search"], m["interval_min"], m["delay_min"]), ("dragon", 120, 10))
        self.assertEqual(lib.later(), ["later_tag"])
        self.assertEqual(lib.md5_count(), 1)
        self.assertEqual(rep["monitors"], 1)
        rep2 = G.import_profile(prof, st, lib, store)   # twice: nothing duplicated
        self.assertEqual(rep2["monitors"], 0)
        self.assertEqual(len(lib.monitors()), 1)


class LibraryState(unittest.TestCase):
    def test_state_roundtrip(self):
        from packrat.grab import library
        lib = library.Library(Path(tempfile.mkdtemp()) / "l.sqlite3")
        self.assertIsNone(lib.get_state("x"))
        lib.set_state("x", True)
        self.assertIs(lib.get_state("x"), True)
        lib.set_state("tabs", [{"query": "a"}])
        self.assertEqual(lib.get_state("tabs"), [{"query": "a"}])


class WebServices(unittest.TestCase):
    def test_expand(self):
        from packrat.grab.image import Image
        from packrat.grab.websvc import expand
        im = Image("safebooru.org")
        im.file_url, im.md5, im.id = "https://x.org/a b.jpg", "abc", 5
        self.assertEqual(expand("https://iqdb.org/?url={url}&m=%md5%&i={id}", im),
                         "https://iqdb.org/?url=https%3A%2F%2Fx.org%2Fa%20b.jpg&m=abc&i=5")


if __name__ == "__main__":
    unittest.main()
