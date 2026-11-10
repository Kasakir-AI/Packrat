"""Grabber's filename tests (src/lib/tests/src/filename/*.cpp), ported to Packrat's filename engine.

Same image, same settings, same expected strings as Grabber's own test suite, so Packrat names files
exactly like Grabber does.
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]   # the source tree (app\ next to tests\) or app\ itself
sys.path.insert(0, str(_ROOT / "app" if (_ROOT / "app" / "packrat").is_dir() else _ROOT))
os.environ.setdefault("PACKRAT_HOME", tempfile.mkdtemp(prefix="packrat-test-home-"))

from packrat.grab import filename as F  # noqa: E402
from packrat.grab.filters import MetaFilter, TagFilter, TokenFilter, PostFilter, Blacklist  # noqa: E402
from packrat.grab.image import Image  # noqa: E402
from packrat.grab.settings import BY_KEY  # noqa: E402
from datetime import datetime, timezone  # noqa: E402


class St(dict):
    def get(self, key, default=None):
        if key in self:
            return self[key]
        if key in BY_KEY:
            return BY_KEY[key].default
        return default


class FakeSite:
    key = "danbooru.donmai.us"
    name = "Danbooru"

    def fix_url(self, u, old=None):
        return u

    def fix_login_url(self, u):
        return u

    def tag_db_types(self, names):
        return {}

    def page_url_for(self, img):
        return ""


DETAILS = {
    "md5": "1bc29b36f623ba82aaf6724fd3b16718", "ext": "jpg", "author": "superauthor", "status": "tested",
    "filename": "oldfilename", "search": "testing well", "id": "7331", "score": "21", "parent_id": "1337",
    "file_size": "1234567", "creator_id": "1234", "has_children": "true", "has_note": "true",
    "has_comments": "true", "file_url": "https://test.com/img/oldfilename.jpg",
    "sample_url": "https://test.com/sample/oldfilename.jpg", "preview_url": "https://test.com/preview/oldfilename.jpg",
    "width": "800", "height": "600", "source": "https://google.com/",
    "tags_general": "tag1 tag2 tag3 test_tag1 test_tag2 test_tag3", "tags_artist": "artist1",
    "tags_copyright": "copyright1 copyright2", "tags_character": "character1 character2", "tags_species": "",
    "tags_meta": "", "created_at": "1471513944", "rating": "safe",
}


class FilenameTests(unittest.TestCase):
    def setUp(self):
        self.st = St({"Save/separator": " ", "Save/character_value": "group", "Save/character_multiple": "replaceAll",
                      "Save/copyright_value": "crossover", "Save/copyright_multiple": "replaceAll",
                      "Save/character_empty": "unknown", "Save/replaceblanks": True, "removedtags": "",
                      "ignoredtags": "", "Save/copyright_useshorter": True})
        self.details = dict(DETAILS)
        self.site = FakeSite()
        self.gallery = self.build(dict(self.details, name="Test gallery name"))
        self.img = self.build(self.details)
        self.img.parent_gallery = self.gallery
        self.tmp = Path(tempfile.mkdtemp(prefix="pr-fn-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self, d):
        img = Image.from_model(self.site, dict(d), search=None)
        if self.__dict__.get("gallery") is not None and "name" not in d:
            img.parent_gallery = self.gallery
        return img

    def tokens(self, img=None):
        return F.build_tokens(img or self.img, self.st, site=self.site)

    def check(self, fmt, expected, path="", fix=True, full=False, keep=False):
        if isinstance(expected, str):
            expected = [expected]
        flags = F.FLAG_CONDITIONALS | F.FLAG_CAP | (F.FLAG_FIX if fix else 0) | (F.FLAG_FOLDER if full else 0) \
            | (F.FLAG_KEEP_INVALID if keep else 0)
        got = F.Filename(fmt).path(self.tokens(), self.st, path or str(Path.home()), 7, flags)
        self.assertEqual(got, expected, fmt)

    # ---- PathSimple ... -------------------------------------------------------------------------
    def test_simple(self):
        self.check("%md5%.%ext%", "1bc29b36f623ba82aaf6724fd3b16718.jpg")

    def test_complex(self):
        self.check("%artist%/%copyright%/%character%/%md5%.%ext%",
                   "artist1/crossover/group/1bc29b36f623ba82aaf6724fd3b16718.jpg")

    def test_keep_all(self):
        self.st["Save/character_multiple"] = "keepAll"
        self.check("%artist%/%copyright%/%character%/%md5%.%ext%",
                   "artist1/crossover/character1 character2/1bc29b36f623ba82aaf6724fd3b16718.jpg")

    def test_keep_n(self):
        self.st["Save/character_multiple"] = "keepN"
        self.st["Save/character_multiple_keepN"] = 1
        self.check("%artist%/%copyright%/%character%/%md5%.%ext%",
                   "artist1/crossover/character1/1bc29b36f623ba82aaf6724fd3b16718.jpg")

    def test_sort(self):
        self.img = self.build(dict(self.details, tags_copyright="copyright2 copyright1"))
        self.st["Save/copyright_multiple"] = "keepAll"
        self.st["Save/copyright_sort"] = "name"
        self.check("%copyright%", "copyright1 copyright2")

    def test_keep_n_then_add(self):
        self.st["Save/character_multiple"] = "keepNThenAdd"
        self.st["Save/character_multiple_keepNThenAdd_keep"] = 1
        self.st["Save/character_multiple_keepNThenAdd_add"] = " (and %count% of %total%)"
        self.check("%artist%/%copyright%/%character%/%md5%.%ext%",
                   "artist1/crossover/character1 (and 1 of 2)/1bc29b36f623ba82aaf6724fd3b16718.jpg")
        self.st["Save/character_multiple_keepNThenAdd_keep"] = 2
        self.check("%artist%/%copyright%/%character%/%md5%.%ext%",
                   "artist1/crossover/character1 character2/1bc29b36f623ba82aaf6724fd3b16718.jpg")

    def test_removed_tags(self):
        self.st["removedtags"] = "character1"
        self.check("%artist%/%copyright%/%character%/%md5%.%ext%",
                   "artist1/crossover/character2/1bc29b36f623ba82aaf6724fd3b16718.jpg")
        self.st["removedtags"] = "character*"
        self.check("%artist%/%copyright%/%character%/%md5%.%ext%",
                   "artist1/crossover/unknown/1bc29b36f623ba82aaf6724fd3b16718.jpg")
        self.st["Save/character_empty"] = ""
        self.check("%artist%/%copyright%/%character%/%md5%.%ext%",
                   "artist1/crossover/1bc29b36f623ba82aaf6724fd3b16718.jpg")

    def test_empty_dirs(self):
        self.check("%artist%/%test%/%md5%.%ext%", "artist1/1bc29b36f623ba82aaf6724fd3b16718.jpg")

    def test_network_drive(self):
        self.check("%md5%.%ext%", "//NetworkDrive/Grabber/1bc29b36f623ba82aaf6724fd3b16718.jpg",
                   "//NetworkDrive/Grabber", True, True)

    def test_kept_tokens(self):
        self.check("%artist%/%path%/%md5%.%ext%", "artist1/%path%/1bc29b36f623ba82aaf6724fd3b16718.jpg")

    def test_full(self):
        for fmt in ("%md5%.%ext%", "/%md5%.%ext%"):
            for folder in ("tests/directory/", "tests/directory"):
                self.check(fmt, "tests/directory/1bc29b36f623ba82aaf6724fd3b16718.jpg", folder, True, True)

    def test_javascript(self):
        self.check("javascript:md5 + '.' + ext", "1bc29b36f623ba82aaf6724fd3b16718.jpg")
        self.st["Save/copyright_multiple"] = "keepAll"
        self.check("javascript:copyright", "copyright1 copyright2")
        self.check("javascript:copyrights.join('-')", "copyright1-copyright2")
        self.check("javascript:date.toISOString().slice(0, 10)", "2016-08-18")
        self.check("javascript:'", [])

    def test_expand_tag_simple(self):
        md = "1bc29b36f623ba82aaf6724fd3b16718.jpg"
        self.check('<image contains the tag "tag1"><"unknown" is one of the image tags> %md5%.%ext%',
                   "image contains the tag tag1 " + md)
        self.check('<image contains the tag "unknown"><"tag2" is one of the image tags> %md5%.%ext%',
                   "tag2 is one of the image tags " + md)
        self.check('<image contains the tag "tag1"><"tag2" is one of the image tags> %md5%.%ext%',
                   "image contains the tag tag1tag2 is one of the image tags " + md)
        self.check('<image contains the tag "unknown1"><"unknown2" is one of the image tags> %md5%.%ext%', md)

    def test_expand_tag_invalid_char(self):
        md = "1bc29b36f623ba82aaf6724fd3b16718.jpg"
        self.check('<"fate/stay_night"/>%md5%.%ext%', md)
        self.img = self.build(dict(self.details, tags_copyright="fate/stay_night"))
        self.check('<"fate/stay_night"/>%md5%.%ext%', "fate_stay_night/" + md)

    def test_expand_tag_invert(self):
        md = "1bc29b36f623ba82aaf6724fd3b16718.jpg"
        self.check('<image does not contain the tag !"tag1"><!"unknown" is not one of the image tags> %md5%.%ext%',
                   "unknown is not one of the image tags " + md)
        self.check('<image does not contain the tag !"unknown"><!"tag2" is not one of the image tags> %md5%.%ext%',
                   "image does not contain the tag unknown " + md)

    def test_expand_tag_multiple(self):
        md = "1bc29b36f623ba82aaf6724fd3b16718.jpg"
        self.check('<"tag1" "tag2"/>%md5%.%ext%', "tag1 tag2/" + md)
        self.check('<"tag1" "tag4"/>%md5%.%ext%', md)
        self.check('<"tag1" !"tag4"/>%md5%.%ext%', "tag1 tag4/" + md)
        self.check('<"tag1" !"tag2"/>%md5%.%ext%', md)

    def test_expand_tag_ignore(self):
        md = "1bc29b36f623ba82aaf6724fd3b16718.jpg"
        self.check('<"tag1"folder1/>%md5%.%ext%', "tag1folder1/" + md, "", False)
        self.check('<-"tag1"folder1/>%md5%.%ext%', "folder1/" + md, "", False)
        self.check('<"tag1""tag2"folder1/>%md5%.%ext%', "tag1tag2folder1/" + md, "", False)
        self.check('<-"tag1"-"tag2"folder1/>%md5%.%ext%', "folder1/" + md, "", False)
        self.check('<"tag1" "tag2"/>%md5%.%ext%', "tag1 tag2/" + md, "", False)
        self.check('<"tag1"-"tag2"/>%md5%.%ext%', "tag1/" + md, "", False)
        self.check('<-"tag1""tag2"/>%md5%.%ext%', "tag2/" + md, "", False)
        self.check('<-"tag1"-"tag2"/>%md5%.%ext%', "/" + md, "", False)

    def test_expand_token(self):
        md = "1bc29b36f623ba82aaf6724fd3b16718.jpg"
        self.check("image - <%artist% some text><text %nothing%> %md5%.%ext%", "image - artist1 some text " + md)
        self.check("image - <%artist:maxlength=4% some text><text %nothing:maxlength=4%> %md5%.%ext%",
                   "image - arti some text " + md)
        self.check("image - <!%artist% some text><text !%nothing%> %md5%.%ext%", "image - text  " + md)

    def test_expand_multiple_mixed(self):
        md = "1bc29b36f623ba82aaf6724fd3b16718.jpg"
        self.check('<"tag1" %artist%/>%md5%.%ext%', "tag1 artist1/" + md)
        self.check('<"tag1" %nothing%/>%md5%.%ext%', md)
        self.check('<"tag1"!%nothing%/>%md5%.%ext%', "tag1/" + md)
        self.check('<"tag1"!%artist%/>%md5%.%ext%', md)
        self.check('<"tag1"-%artist%/>%md5%.%ext%', "tag1/" + md)
        self.check('<-"tag1"%artist%/>%md5%.%ext%', "artist1/" + md)

    def test_escaping(self):
        self.check("<<Value>>%md5%<</Value>>", "<Value>1bc29b36f623ba82aaf6724fd3b16718</Value>", "", False)

    def test_conditions(self):
        md = "1bc29b36f623ba82aaf6724fd3b16718.jpg"
        self.check('<"tag1"?yes/>%md5%.%ext%', "yes/" + md)
        self.check('<"tag1"?yes:no>/%md5%.%ext%', "yes/" + md)
        self.check('<!"tag1"?yes/>%md5%.%ext%', md)
        self.check('<!"tag1"?yes:no>/%md5%.%ext%', "no/" + md)
        self.check('<"tag1" & "not_found"?yes/>%md5%.%ext%', md)
        self.check('<"tag1" & "tag2"?yes/>%md5%.%ext%', "yes/" + md)
        self.check('<"tag1" | "not_found"?yes/>%md5%.%ext%', "yes/" + md)
        self.check('<"not_found" | "not_found_2"?yes/>%md5%.%ext%', md)
        self.check('<"tag1"?%id% and %rating%/>%md5%.%ext%', "7331 and safe/" + md)

    def test_options(self):
        self.check("%md5:maxlength=8%.%ext%", "1bc29b36.jpg")
        self.check("%md5:maxlength=16,maxlength=8%.%ext%", "1bc29b36.jpg")
        self.check("%search:htmlescape%.%ext%", "testing well.jpg")

    def test_dates(self):
        self.check("%date:format=yyyy-MM-dd%.%ext%", "2016-08-18.jpg")
        self.check("%date:format=yyyy-MM-dd HH-mm%.%ext%", "2016-08-18 09-52.jpg")
        self.check("%date:format=yyyy-MM-dd HH-mm,timezone=UTC%.%ext%", "2016-08-18 09-52.jpg")
        self.check("%date:format=yyyy-MM-dd HH-mm,timezone=UTC+01:00%.%ext%", "2016-08-18 10-52.jpg")
        self.check("%date:format=yyyy-MM-dd HH-mm,timezone=error_not_found%.%ext%", "2016-08-18 09-52.jpg")

    def test_namespaces(self):
        self.st["Save/character_multiple"] = "keepAll"
        self.check("%character:includenamespace,unsafe%", "character:character1 character:character2", "", False)
        self.st["Save/separator"] = "c"
        self.check("%character:includenamespace,unsafe%", "character:character1ccharacter:character2", "", False)

    def test_namespace_complex(self):
        for k in ("artist", "copyright", "character"):
            self.st[f"Save/{k}_multiple"] = "keepAll"
        self.check("%all:ignorenamespace=general,includenamespace,unsafe,separator=\n%\n\n%general%",
                   "artist:artist1\ncharacter:character1\ncharacter:character2\ncopyright:copyright1\n"
                   "copyright:copyright2\n\ntag1 tag2 tag3 test_tag1 test_tag2 test_tag3", "", False)
        self.check("%all:includenamespace,excludenamespace=general character,unsafe%",
                   "tag1 tag2 tag3 test_tag1 test_tag2 test_tag3 artist:artist1 character1 character2 "
                   "copyright:copyright1 copyright:copyright2", "", False)

    def test_separators_case(self):
        self.check("%general:separator=+%", "tag1+tag2+tag3+test_tag1+test_tag2+test_tag3")
        self.check("%general:separator=^,%", "tag1,tag2,tag3,test_tag1,test_tag2,test_tag3")
        self.check("%general:case,separator=+%", "tag1+tag2+tag3+test_tag1+test_tag2+test_tag3")
        self.check("%general:case=invalid,separator=+%", "tag1+tag2+tag3+test_tag1+test_tag2+test_tag3")
        self.check("%general:case=lower,separator=+%", "tag1+tag2+tag3+test_tag1+test_tag2+test_tag3")
        self.check("%general:case=upper_first,separator=+%", "Tag1+Tag2+Tag3+Test_tag1+Test_tag2+Test_tag3")
        self.check("%general:case=upper,separator=+%", "Tag1+Tag2+Tag3+Test_Tag1+Test_Tag2+Test_Tag3")
        self.check("%general:case=caps,separator=+%", "TAG1+TAG2+TAG3+TEST_TAG1+TEST_TAG2+TEST_TAG3")

    def test_count(self):
        self.check("%md5% (%count%).%ext%", "1bc29b36f623ba82aaf6724fd3b16718 (7).jpg")
        self.check("%md5% (%count:length=3%).%ext%", "1bc29b36f623ba82aaf6724fd3b16718 (007).jpg")

    def test_num(self):
        d = str(self.tmp)
        self.check("%id% (%num%).%ext%", "7331 (1).jpg", d)
        self.check("%id% (%num:length=3%).%ext%", "7331 (001).jpg", d)
        (self.tmp / "7331 (1).jpg").write_bytes(b"x")
        (self.tmp / "7331 (2).jpg").write_bytes(b"x")
        self.check("%id% (%num%).%ext%", "7331 (3).jpg", d)
        (self.tmp / "7331 (2).jpg").unlink()
        (self.tmp / "7331 (2).png").write_bytes(b"x")
        (self.tmp / "7331 (3).png").write_bytes(b"x")
        self.check("%id% (%num%).%ext%", "7331 (2).jpg", d)
        self.check("%id% (%num:noext%).%ext%", "7331 (4).jpg", d)

    def test_option_sort(self):
        self.img = self.build(dict(self.details, tags_copyright="copyright2 copyright1"))
        self.st["Save/copyright_multiple"] = "keepAll"
        self.check("%copyright%", "copyright2 copyright1")
        self.check("%copyright:sort%", "copyright1 copyright2")

    def test_species_meta(self):
        self.img = self.build(dict(self.details, tags_species="test_species"))
        self.check("%species%.%ext%", "test_species.jpg")
        self.img = self.build(dict(self.details, tags_meta="test_meta"))
        self.check("%meta%.%ext%", "test_meta.jpg")

    def test_no_jpeg(self):
        self.img = self.build(dict(self.details, ext="jpeg"))
        self.st["Save/noJpeg"] = True
        self.check("%ext%", "jpg")
        self.st["Save/noJpeg"] = False
        self.check("%ext%", "jpeg")

    def test_keep_invalid(self):
        self.check("%invalid_token% %ext%", "%invalid_token% jpg", "", True, False, True)
        self.check("%ext% %invalid_token%", "jpg %invalid_token%", "", True, False, True)

    def test_forbidden_separator(self):
        self.st["Save/copyright_multiple"] = "keepAll"
        self.check("%copyright:separator=/%", "copyright1/copyright2")

    def test_gallery(self):
        md = "1bc29b36f623ba82aaf6724fd3b16718.jpg"
        self.check("%gallery.name%/%name%-%md5%.%ext%", "Test gallery name/-" + md)
        self.check("javascript:gallery.name + '/' + name + '-' + md5 + '.' + ext", "Test gallery name/-" + md)

    def test_expand_tokens(self):
        fn = F.Filename("%artist%/%copyright%/%character%/%md5%.%ext%")
        self.st["Save/character_multiple"] = "replaceAll"
        r = fn.expand_tokens(self.tokens(), self.st)
        self.assertEqual(len(r), 1)
        self.assertEqual((r[0]["artist"], r[0]["copyright"], r[0]["character"]), (["artist1"], "crossover", "group"))
        self.st["Save/character_multiple"] = "multiple"
        r = fn.expand_tokens(self.tokens(), self.st)
        self.assertEqual([x["character"] for x in r], ["character1", "character2"])
        self.st["Save/copyright_multiple"] = "multiple"
        r = fn.expand_tokens(self.tokens(), self.st)
        self.assertEqual([(x["copyright"], x["character"]) for x in r],
                         [("copyright1", "character1"), ("copyright1", "character2"),
                          ("copyright2", "character1"), ("copyright2", "character2")])

    def test_is_valid(self):
        self.assertFalse(F.Filename("").is_valid())
        self.assertFalse(F.Filename("%md5%").is_valid())
        self.assertFalse(F.Filename("toto").is_valid())
        self.assertFalse(F.Filename("%toto% %md5%.%ext%").is_valid())
        self.assertTrue(F.Filename("%md5%.%ext%").is_valid())
        self.assertTrue(F.Filename("%website%/%id%.%ext%").is_valid())
        self.assertTrue(F.Filename("%artist%/%copyright%/%character%/%md5%.%ext%").is_valid())
        self.assertTrue(F.Filename("javascript:md5 + '.' + ext;").is_valid())
        self.assertTrue(F.Filename("%md5% %date:format=yyyy-MM-dd%.%ext%").is_valid())
        self.assertTrue(F.Filename("%gallery.id%/%md5%.%ext%").is_valid())
        self.assertFalse(F.Filename("%toto.id%/%md5%.%ext%").is_valid())
        self.assertEqual(F.Filename("%id%.%ext%").check()[0], "info")  # Grabber: green hint, not an error

    def test_shorter_copyright(self):
        self.img = self.build(dict(self.details, tags_copyright="test test_2"))
        self.check("%copyright%", "test")
        self.st["Save/copyright_multiple"] = "keepAll"
        self.st["Save/copyright_useshorter"] = False
        self.check("%copyright%", "test test_2")
        self.img = self.build(dict(self.details, tags_copyright="test_2 test"))
        self.st["Save/copyright_useshorter"] = True
        self.check("%copyright%", "test")

    def _cond(self, rules):
        self.st["Filenames"] = [{"fn": fn, "dir": str(Path.home()), "cond": c} for c, fn in rules]

    def test_conditional_filenames(self):
        md = "1bc29b36f623ba82aaf6724fd3b16718.jpg"
        fmt = "%artist%/%copyright%/%character%/%md5%.%ext%"
        self._cond([("tag7", "%md5%.%ext%"), ("", "%id% %md5%.%ext%"), ("character1", "%id% %md5%.%ext%")])
        self.check(fmt, "7331 " + md)
        self._cond([("tag7", "%md5%.%ext%"), ("tag1 tag8", "%id% %md5%.%ext%")])
        self.check(fmt, "7331 " + md)
        self._cond([("%model%", "%md5%.%ext%"), ("%character%", "%id% %md5%.%ext%")])
        self.check(fmt, "7331 " + md)
        self._cond([("rating:explicit", "explicit %md5%.%ext%"), ("rating:safe", "safe %md5%.%ext%")])
        self.check(fmt, "safe " + md)
        self.st["Customs"] = [{"name": "custom1", "tags": "tag4 tag7"}, {"name": "custom2", "tags": "tag1"}]
        self._cond([("%custom1%", "%md5%.%ext%"), ("%custom2%", "%id% %md5%.%ext%")])
        self.check(fmt, "7331 " + md)
        self._cond([("javascript:width > 2000", "%md5%.%ext%"), ("javascript:'", "%id% %md5%.%ext%"),
                    ("javascript:width > 400", "%id% %md5%.%ext%")])
        self.check(fmt, "7331 " + md)

    def test_customs(self):
        self.st["Customs"] = [{"name": "custom1", "tags": "tag1 character1"}, {"name": "custom2", "tags": "tag3 tag4"}]
        self.check("%custom1%", "tag1 character1")
        self.check("%custom2%", "tag3")

    def test_replace_blanks(self):
        self.st["Save/separator"] = "+"
        self.st["Save/replaceblanks"] = False
        self.check("%all%", "tag1+tag2+tag3+test tag1+test tag2+test tag3+artist1+character1+character2+copyright1+"
                            "copyright2", "", False)
        self.check("%allo%", "tag1 tag2 tag3 test_tag1 test_tag2 test_tag3 artist1 character1 character2 copyright1 "
                             "copyright2", "", False)
        self.check("javascript:all", "tag1+tag2+tag3+test tag1+test tag2+test tag3+artist1+character1+character2+"
                                     "copyright1+copyright2", "", False)
        self.check("javascript:allo", "tag1 tag2 tag3 test_tag1 test_tag2 test_tag3 artist1 character1 character2 "
                                      "copyright1 copyright2", "", False)
        self.st["Save/replaceblanks"] = True
        self.check("%all%", "tag1+tag2+tag3+test_tag1+test_tag2+test_tag3+artist1+character1+character2+copyright1+"
                            "copyright2", "", False)

    def test_command(self):
        fn = F.Filename('curl -F "post[tags]=%all%" -F "post[rating]=%rating%" -F "post[file]=@%path%" x')
        got = fn.path(self.tokens(), self.st, "", 0, 0)
        self.assertEqual(got, ['curl -F "post[tags]=tag1 tag2 tag3 test_tag1 test_tag2 test_tag3 artist1 character1 '
                               'character2 copyright1 copyright2" -F "post[rating]=safe" -F "post[file]=@%path%" x'])

    def test_filename_token(self):
        self.img = self.build(dict(self.details, file_url="https://test.com/img/__fubuki_kantai_collection_drawn_by_"
                                                          "minosu__23d36b216c1a3f4e219c4642e221e1a2.jpg"))
        self.check("%filename%", "__fubuki_kantai_collection_drawn_by_minosu__23d36b216c1a3f4e219c4642e221e1a2")

    def test_need_temporary_file(self):
        t = {"md5": "", "filesize": 0, "id": 0}
        self.assertTrue(F.Filename("%md5%.%ext%").need_temporary_file(t))
        self.assertTrue(F.Filename("%id% (%filesize%).%ext%").need_temporary_file(t))
        self.assertFalse(F.Filename("%id%.%ext%").need_temporary_file(t))
        t = {"md5": "1bc29b36f623ba82aaf6724fd3b16718", "filesize": 123, "id": 456}
        self.assertFalse(F.Filename("%md5%.%ext%").need_temporary_file(t))

    def test_need_exact_tags(self):
        self.assertEqual(F.Filename("%md5%.%ext%").need_exact_tags(), 0)
        self.assertEqual(F.Filename("javascript:md5 + '.' + ext").need_exact_tags(), 2)
        self.assertEqual(F.Filename("%character% %md5%.%ext%").need_exact_tags(), 1)
        self.assertEqual(F.Filename("%all:includenamespace% %md5%.%ext%").need_exact_tags(), 1)
        self.assertEqual(F.Filename("%filename%.%ext%").need_exact_tags(), 0)
        self.assertEqual(F.Filename("%filename%.%ext%").need_exact_tags(["filename"]), 2)

    def test_escape_method(self):
        img = self.build(dict(self.details, md5="good'ol' md5"))
        fn = F.Filename("INSERT INTO test (%id:escape%, %md5:escape%, %ext:escape%);")
        fn.escape = lambda v: "'" + str(v).replace("'", "''") + "'"
        got = fn.path(F.build_tokens(img, self.st, site=self.site), self.st, "", 0, 0)
        self.assertEqual(got, ["INSERT INTO test ('7331', 'good''ol'' md5', 'jpg');"])


class ExecutionVisitorTests(unittest.TestCase):
    def run_(self, fmt, tokens):
        ast, err, _ = F.parse_cached(fmt)
        self.assertEqual(err, "")
        return F.Executor(tokens, St()).run(ast)

    def test_basic(self):
        self.assertEqual(self.run_("", {}), "")
        self.assertEqual(self.run_("image.jpg", {}), "image.jpg")
        self.assertEqual(self.run_("out/%md5%.%ext%", {"md5": "1bc29b36f623ba82aaf6724fd3b16718", "ext": "jpg"}),
                         "out/1bc29b36f623ba82aaf6724fd3b16718.jpg")
        self.assertEqual(self.run_("%a% %b% %c% %d%", {"a": 123, "b": 123, "c": 123, "d": 123}), "123 123 123 123")

    def test_floats(self):
        t = {"ratio": 100 / 3}
        self.assertEqual(self.run_("%ratio%", t), "33.333333")
        self.assertEqual(self.run_("%ratio:length=3%", t), "33.333333")
        self.assertEqual(self.run_("%ratio:length=10%", t), "033.333333")
        self.assertEqual(self.run_("%ratio:precision=1%", t), "33.3")
        self.assertEqual(self.run_("%ratio:length=5,precision=1%", t), "033.3")

    def test_list_count_and_gallery(self):
        self.assertEqual(self.run_("%list:count%", {"list": ["a", "b", "c"]}), "3")
        t = {"gallery": {"name": "some gallery"}, "md5": "1bc29b36f623ba82aaf6724fd3b16718", "ext": "jpg"}
        self.assertEqual(self.run_("<galleries/%gallery.name%/>%md5%.%ext%", t),
                         "galleries/some gallery/1bc29b36f623ba82aaf6724fd3b16718.jpg")
        self.assertEqual(self.run_("<galleries/%gallery.id%/>%md5%.%ext%", t), "1bc29b36f623ba82aaf6724fd3b16718.jpg")
        self.assertEqual(self.run_("%gallery.id%/%md5%.%ext%", {"md5": "1bc29b36f623ba82aaf6724fd3b16718", "ext": "jpg"}),
                         "/1bc29b36f623ba82aaf6724fd3b16718.jpg")

    def test_score_length(self):
        self.assertEqual(self.run_("%score:length=5%", {"score": "123"}), "00123")
        self.assertEqual(self.run_("%score:length=5%", {"score": "12.3"}), "00012.3")
        self.assertEqual(self.run_("%score:length=5%", {"score": "0.123"}), "00000.123")
        self.assertEqual(self.run_("%score:length=5%", {"score": ".123"}), "00000.123")


class FilterTests(unittest.TestCase):
    """Grabber's meta-filter-test.cpp / tag-filter-test.cpp / token-filter-test.cpp cases."""

    def test_meta(self):
        self.assertEqual(MetaFilter("meta", "val").to_string(), "meta:val")
        self.assertEqual(MetaFilter("meta", "val", True).to_string(), "-meta:val")
        t = {"grabber": ["downloaded"]}
        self.assertEqual(MetaFilter("grabber", "downloaded").match(t), "")
        self.assertEqual(MetaFilter("grabber", "nok").match(t), 'image is not "nok"')
        self.assertEqual(MetaFilter("grabber", "downloaded", True).match(t), 'image is "downloaded"')
        self.assertEqual(MetaFilter("grabber", "nok", True).match(t), "")
        t = {"id": 12345}
        ok, no, yes = "", "image's id does not match", "image's id match"
        for val, exp, expi in ((">1000", ok, yes), (">=1000", ok, yes), ("<1000", no, ok), ("<=1000", no, ok),
                               ("1000..", ok, yes), ("..1000", no, ok), ("10000..20000", ok, yes), ("10", no, ok)):
            self.assertEqual(MetaFilter("id", val).match(t), exp, val)
            self.assertEqual(MetaFilter("id", val, True).match(t), expi, val)
        t = {"date": datetime(2016, 8, 18, tzinfo=timezone.utc)}
        for val in (">08/16/2016", ">=2016-08-16", "<08/20/2016", "<=2016-08-20", "..08/20/2016", "2016-08-16..",
                    "08/16/2016..2016-08-20", "2016-08-18"):
            self.assertEqual(MetaFilter("date", val).match(t), "", val)
        self.assertEqual(MetaFilter("date", "someday").match(t), "image's date does not match")
        t = {"filesize": 2345678}
        for val in (">1234567", ">=1mb", "<3MB", "<=4567kb", "..10MB", "1mb..", "1MB..1GB", "2345678"):
            self.assertEqual(MetaFilter("filesize", val.lower()).match(t), "", val)
        t = {"rating": "safe"}
        self.assertEqual(MetaFilter("rating", "s").match(t), "")
        self.assertEqual(MetaFilter("rating", "safe").match(t), "")
        self.assertEqual(MetaFilter("rating", "e").match(t), 'image is not "explicit"')
        self.assertEqual(MetaFilter("rating", "s", True).match(t), 'image is "safe"')
        self.assertEqual(MetaFilter("rating", "e", True).match(t), "")
        t = {"source": "test.com/some/path"}
        self.assertEqual(MetaFilter("source", "test.com").match(t), "")
        self.assertEqual(MetaFilter("source", "nok.com").match(t), 'image\'s source does not starts with "nok.com"')
        self.assertEqual(MetaFilter("meta", "val").match({"meta": "val"}), "")
        self.assertEqual(MetaFilter("meta", "nok").match({"meta": "val"}), "image's meta does not match")

    def test_tag_token_post_blacklist(self):
        t = {"allos": ["tag1", "tag2", "test_tag"]}
        self.assertEqual(TagFilter("tag1").match(t), "")
        self.assertEqual(TagFilter("tag3").match(t), 'image does not contains "tag3"')
        self.assertEqual(TagFilter("tag1", True).match(t), 'image contains "tag1"')
        self.assertEqual(TagFilter("test_*").match(t), "")
        self.assertEqual(TokenFilter("allos").match(t), "")
        self.assertEqual(TokenFilter("nope").match(t), 'image does not have a "nope" token')
        t2 = dict(t, rating="safe", id=5)
        self.assertEqual(PostFilter("tag1 -tag3 rating:s id:<10").match(t2), [])
        self.assertEqual(PostFilter("(tag3 | tag2) & -rating:e").match(t2), [])
        self.assertEqual(PostFilter("tag3 & tag2").match(t2), ["post-filter"])
        bl = Blacklist(["tag9", "tag1 rating:safe"])
        self.assertEqual(bl.match(t2), ["tag1 rating:safe"])
        self.assertEqual(Blacklist(["tag1 rating:explicit"]).match(t2), [])


if __name__ == "__main__":
    unittest.main()
