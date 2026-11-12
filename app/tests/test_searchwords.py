"""Packrat's search words are rewritten for each site: sort, rating and score, in the site's own form."""
from types import SimpleNamespace

from packrat.grab import searchwords as SW

MODS = {
    "Danbooru (2.0)": ["rating:general", "rating:safe", "rating:questionable", "rating:explicit", "rating:g", "rating:s",
                       "order:id", "order:score", "order:favcount", "score:"],
    "E621": ["rating:safe", "rating:questionable", "rating:explicit", "rating:s", "rating:q", "rating:e", "order:id",
             "order:score", "order:favcount", "order:random", "score:"],
    "Gelbooru (0.2)": ["rating:general", "rating:safe", "rating:questionable", "rating:explicit", "order:id",
                       "order:score", "order:favcount", "sort:score", "score:"],
    "Philomena": ["order:id_asc", "order:score", "order:faves", "order:wilson_score"],
    "Zerochan": ["order:id", "order:fav", "order:random"],
    "WallHaven": ["rating:s", "rating:safe", "rating:q", "rating:e", "order:random", "order:date_added",
                  "order:favorites", "order:toplist"],
}


def site(source, host, name=""):
    return SimpleNamespace(host=host, name=name or host, source=SimpleNamespace(name=source, meta={"modifiers": MODS[source]}))


DANBOORU = site("Danbooru (2.0)", "danbooru.donmai.us", "Danbooru")
E621 = site("E621", "e621.net", "e621")
E926 = site("E621", "e926.net", "e926")
GELBOORU = site("Gelbooru (0.2)", "gelbooru.com", "Gelbooru")
RULE34 = site("Gelbooru (0.2)", "api.rule34.xxx", "Rule34")
DERPI = site("Philomena", "derpibooru.org", "Derpibooru")
ZEROCHAN = site("Zerochan", "www.zerochan.net", "Zerochan")
WALLHAVEN = site("WallHaven", "wallhaven.cc", "Wallhaven")


def q(query, s):
    return SW.translate(query, s).query


def test_popular_is_written_the_way_each_site_wants():
    assert q("cat order:popular", DANBOORU) == "cat order:score"
    assert q("cat order:popular", GELBOORU) == "cat sort:score"
    assert q("cat order:popular", WALLHAVEN) == "cat order:toplist"
    assert q("cat order:popular", ZEROCHAN) == "cat order:fav"
    assert q("cat sort:score:desc", E621) == "cat order:score"      # Gelbooru's spelling works elsewhere too


def test_favorites_oldest_random_and_their_fallbacks():
    assert q("cat order:favorites", E621) == "cat order:favcount"
    assert q("cat order:favorites", DERPI) == "cat order:faves"
    t = SW.translate("cat order:favorites", GELBOORU)          # Gelbooru has no favorites sort: best first
    assert t.query == "cat sort:score" and "best first" in t.notes[0]
    assert q("cat order:oldest", GELBOORU) == "cat sort:id:asc"
    assert q("cat order:oldest", DERPI) == "cat order:id_asc"
    t = SW.translate("cat order:oldest", ZEROCHAN)             # order:id is newest there: not sent
    assert t.query == "cat" and t.notes
    assert q("cat order:newest", DANBOORU) == "cat"
    assert q("cat order:random", DANBOORU) == "cat order:random"
    assert q("cat order:mpixels", DANBOORU) == "cat order:mpixels"   # the site's own words pass untouched


def test_ratings_on_four_three_and_tag_sites():
    assert q("cat rating:sfw", DANBOORU) == "cat rating:general"
    assert q("cat rating:sfw", E621) == "cat rating:safe"
    assert q("cat rating:sfw", DERPI) == "cat safe"
    assert q("cat -rating:explicit", DANBOORU) == "cat -rating:explicit"
    assert q("cat -rating:explicit", DERPI) == "cat -explicit"
    assert q("cat rating:nsfw", DANBOORU) == "cat rating:questionable,explicit"
    assert q("cat rating:nsfw", E621) == "cat -rating:safe"
    assert q("cat rating:nsfw", GELBOORU) == "cat -rating:general -rating:sensitive"
    assert q("cat rating:sfw", E926) == "cat"                 # only safe pictures there anyway
    assert q("cat rating:e", E621) == "cat rating:e"          # the site's own short form passes untouched
    t = SW.translate("cat rating:sensitive", E621)
    assert t.query == "cat rating:safe" and "sensitive" in t.notes[0]


def test_what_a_site_cant_do_packrat_checks_on_the_pictures():
    t = SW.translate("cat rating:sfw score:>=20", ZEROCHAN)
    assert t.query == "cat" and t.checks and len(t.notes) == 2
    img = lambda r, s: SimpleNamespace(rating=r, score=s, tags=[])  # noqa: E731
    assert t.keeps(img("", 25))                  # no rating from the site: kept
    assert not t.keeps(img("explicit", 25))
    assert not t.keeps(img("", 5))
    assert q("cat score:>=20", DERPI) == "cat score.gte:20"
    w = SW.translate("cat -rating:explicit", WALLHAVEN)        # one rating at a time there
    assert w.query == "cat" and not w.keeps(img("e", 0)) and w.keeps(img("q", 0))


def test_rating_levels_read_the_way_each_site_means_them():
    pic = lambda r: SimpleNamespace(rating=r, tags=[])  # noqa: E731
    assert SW.rating_level(pic("s"), "four") == 1               # Danbooru: s is sensitive
    assert SW.rating_level(pic("s"), "three") == 0              # e621: s is safe
    assert SW.rating_level(SimpleNamespace(rating="", tags=["suggestive", "pony"]), "tags") == 1


def test_menus_read_and_write_the_search_box():
    assert SW.menu_value("cat order:score", "sort") == "order:popular"
    assert SW.menu_value("cat sort:id:asc", "sort") == "order:oldest"
    assert SW.menu_value("cat", "sort") == ""
    assert SW.menu_value("cat rating:general", "rating") == "rating:sfw"
    assert SW.menu_value("cat -rating:explicit", "rating") == "-rating:explicit"
    assert SW.menu_value("cat rating:sfw rating:explicit", "rating") is None      # not one of the menu's choices
    assert SW.with_word("cat order:score rating:sfw", "sort", "order:random") == "cat rating:sfw order:random"
    assert SW.with_word("cat -rating:explicit", "rating", "") == "cat"
