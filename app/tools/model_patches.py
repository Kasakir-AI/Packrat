"""Packrat patches to Grabber's compiled source models: (old, new) text replacements that must match
exactly once (build_sources.py stops if Grabber changed the code they apply to).

Gelbooru (0.2): the tag list pattern allows the whitespace today's sites put between table cells.

E621: e621's "v2" post list carries no tag categories, so Grabber has to load every image's details page
(one more request per image, at 1 per second) whenever the file name uses %artist%, %character% ...
The classic answer (without v2=true) includes the categories in the list itself. The patched model reads
both formats, so it keeps working if e621 ever drops the classic one; it also passes favorites count,
uploader, pools and description as tokens.
"""

E621_V1_MAP = '''const imgMapV1 = {
    "ext": "file.ext", "change": "change_seq", "creator_id": "uploader_id", "id": "id", "rating": "rating",
    "file_url": "file.url", "width": "file.width", "height": "file.height", "file_size": "file.size",
    "preview_url": "preview.url", "preview_width": "preview.width", "preview_height": "preview.height",
    "sample_url": "sample.url", "sample_height": "sample.height", "sample_width": "sample.width",
    "md5": "file.md5", "has_children": "relationships.has_children", "parent_id": "relationships.parent_id",
    "score": "score.total", "sources": "sources",
};
function parseImage(raw) {
    var _a;
    const img = Grabber.mapFields(raw, raw.files ? imgMap : imgMapV1);
    img.tokens = { "fav_count": raw.stats ? raw.stats.fav_count : raw.fav_count,
                   "author": raw.uploader_name || "", "pools": (raw.pools || []).join(" "),
                   "description": raw.description || "" };
    if (!raw.files && raw.sample && raw.sample.has === false) { delete img.sample_url; }'''

GELBOORU02_TAGS = ('tags: Grabber.regexToTags(\'(?:<td>(?<count_2>\\\\d+)</td>\\\\s*)?<td><span class="tag-type-(?<type>[^"]+)"><a[^>]+>(?<name>.+?)</a></span>(?:\\\\s*<span class="tag-count">(?<count>\\\\d+)</span>)?</td>\', src),', 'tags: Grabber.regexToTags(\'(?:<td>\\\\s*(?<count_2>\\\\d+)\\\\s*</td>\\\\s*)?<td>\\\\s*<span class="tag-type-(?<type>[^"]+)">\\\\s*<a[^>]+>(?<name>.+?)</a>\\\\s*</span>(?:\\\\s*<span class="tag-count">(?<count>\\\\d+)</span>)?\\\\s*</td>\', src),')

PATCHES = {
    # Gelbooru 0.2 tag list (tag loader, --return-pure-tags): the sites now indent their HTML, which the
    # original pattern doesn't allow; whitespace between the cells is accepted now.
    "Gelbooru (0.2)": [GELBOORU02_TAGS],
    "E621": [
        ('''function parseImage(raw) {
    var _a;
    const img = Grabber.mapFields(raw, imgMap);''', E621_V1_MAP),
        ('"&tags=" + encodeURIComponent(query.search) + "&v2=true";', '"&tags=" + encodeURIComponent(query.search);'),
        ('return "/posts/" + id + ".json?v2=true&mode=extended";', 'return "/posts/" + id + ".json";'),
    ],
}
