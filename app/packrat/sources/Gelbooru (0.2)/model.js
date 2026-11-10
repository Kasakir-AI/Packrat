"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.source = void 0;
const tagTypeMap = {
    "tag": "general",
    "metadata": "meta",
};
function completeImage(img) {
    img.author = img.owner;
    if ("tag_info" in img) {
        const tagInfo = img["tag_info"];
        img.tags = tagInfo.map((tag) => ({
            name: tag.tag,
            type: tagTypeMap[tag.type] || tag.type,
            count: tag.count,
        }));
    }
    if ((!img.file_url || img.file_url.length < 5) && img.preview_url) {
        img.file_url = img.preview_url
            .replace("/thumbnails/", "/images/")
            .replace("/thumbnail_", "/");
    }
    return img;
}
exports.source = {
    name: "Gelbooru (0.2)",
    modifiers: ["rating:general", "rating:safe", "rating:questionable", "rating:explicit", "user:", "fav:", "fastfav:", "md5:", "source:", "id:", "width:", "height:", "score:", "mpixels:", "filesize:", "date:", "gentags:", "arttags:", "chartags:", "copytags:", "approver:", "parent:", "sub:", "order:id", "order:id_desc", "order:score", "order:score_asc", "order:mpixels", "order:mpixels_asc", "order:filesize", "order:landscape", "order:portrait", "order:favcount", "order:rank", "parent:none", "unlocked:rating", "sort:updated", "sort:id", "sort:score", "sort:rating", "sort:user", "sort:height", "sort:width", "sort:parent", "sort:source", "sort:updated"],
    tagFormat: {
        case: "lower",
        wordSeparator: "_",
    },
    searchFormat: {
        and: " ",
    },
    auth: {
        url: {
            type: "url",
            fields: [
                {
                    id: "userId",
                    key: "user_id",
                },
                {
                    id: "apiKey",
                    key: "api_key",
                },
            ],
        },
        session: {
            type: "post",
            url: "/index.php?page=account&s=login&code=00",
            fields: [
                {
                    id: "pseudo",
                    key: "user",
                },
                {
                    id: "password",
                    key: "pass",
                    type: "password",
                },
            ],
            check: {
                type: "cookie",
                key: "user_id",
            },
        },
    },
    apis: {
        xml: {
            name: "XML",
            auth: [],
            maxLimit: 100,
            search: {
                url: (query, opts) => {
                    const page = query.page - 1;
                    const search = query.search.replace(/(^| )order:/gi, "$1sort:");
                    const fav = search.match(/(?:^| )fav:(\d+)(?:$| )/);
                    if (fav) {
                        return { error: "XML API cannot search favorites" };
                    }
                    return "/index.php?page=dapi&s=post&q=index&limit=" + opts.limit + "&pid=" + page + "&tags=" + encodeURIComponent(search);
                },
                parse: (src) => {
                    var _a, _b, _c;
                    const parsed = Grabber.parseXML(src);
                    if (!parsed) {
                        return { error: "Invalid XML response" };
                    }
                    // Handle error messages
                    if (((_b = (_a = parsed === null || parsed === void 0 ? void 0 : parsed["response"]) === null || _a === void 0 ? void 0 : _a["@attributes"]) === null || _b === void 0 ? void 0 : _b["success"]) === "false") {
                        return { error: parsed["response"]["@attributes"]["reason"] };
                    }
                    if ("error" in parsed) {
                        const error = parsed["error"];
                        return { error: typeof error === "string" ? error : String(error["#text"] || "Unknown API error") };
                    }
                    // Validate the shape of the response
                    if (!(parsed === null || parsed === void 0 ? void 0 : parsed["posts"])) {
                        return { error: "Invalid XML response (no posts found)" };
                    }
                    const data = Grabber.makeArray(parsed.posts.post);
                    const images = [];
                    for (const image of data) {
                        if (image && "id" in image) {
                            images.push(completeImage(Grabber.typedXML(image)));
                        }
                        else if (image && "@attributes" in image) {
                            images.push(completeImage(image["@attributes"]));
                        }
                    }
                    return {
                        images,
                        imageCount: (_c = parsed.posts["@attributes"]) === null || _c === void 0 ? void 0 : _c["count"],
                    };
                },
            },
        },
        json: {
            name: "JSON",
            auth: [],
            maxLimit: 100,
            search: {
                url: (query, opts) => {
                    const page = query.page - 1;
                    const search = query.search.replace(/(^| )order:/gi, "$1sort:");
                    const fav = search.match(/(?:^| )fav:(\d+)(?:$| )/);
                    if (fav) {
                        return { error: "JSON API cannot search favorites" };
                    }
                    return "/index.php?page=dapi&s=post&q=index&limit=" + opts.limit + "&pid=" + page + "&tags=" + encodeURIComponent(search) + "&json=1";
                },
                parse: (src) => {
                    let parsed = JSON.parse(src);
                    // Handle error messages
                    if (typeof parsed === "string") {
                        return { error: parsed };
                    }
                    // Handle the old format
                    if (Array.isArray(parsed)) {
                        parsed = {
                            "@attributes": {},
                            "post": parsed,
                        };
                    }
                    const images = [];
                    for (const image of parsed["post"]) {
                        images.push(completeImage(image));
                    }
                    return {
                        images,
                        imageCount: parsed["@attributes"]["count"],
                    };
                },
            },
            details: {
                url: (id, md5, opts) => {
                    return "/index.php?page=dapi&s=post&q=index&id=" + id + "&fields=tag_info&json=1";
                },
                parse: (src) => {
                    const parsed = JSON.parse(src);
                    const post = "post" in parsed ? parsed["post"] : parsed;
                    const image = Array.isArray(post) ? post[0] : post;
                    return completeImage(image);
                },
            },
        },
        html: {
            name: "Regex",
            auth: [],
            forcedLimit: 42,
            search: {
                url: (query, opts, previous) => {
                    try {
                        const baseUrl = opts.baseUrl.replace("//api.", "//");
                        const search = query.search.replace(/(^| )order:/gi, "$1sort:");
                        const fav = search.match(/(?:^| )fav:(\d+)(?:$| )/);
                        if (fav) {
                            const pagePart = Grabber.pageUrl(query.page, previous, 20000, "&pid={page}", "&pid={page}", " id:<{min}&p=1", (p) => (p - 1) * 50);
                            return baseUrl + "/index.php?page=favorites&s=view&id=" + fav[1] + pagePart;
                        }
                        else {
                            const pagePart = Grabber.pageUrl(query.page, previous, 20000, "&pid={page}", "&pid={page}", " id:<{min}&p=1", (p) => (p - 1) * 42);
                            return baseUrl + "/index.php?page=post&s=list&tags=" + encodeURIComponent(search) + pagePart;
                        }
                    }
                    catch (e) {
                        return { error: e.message };
                    }
                },
                parse: (src) => {
                    var _a, _b, _c, _d;
                    if (src.indexOf("Unable to search this deep") !== -1) {
                        return { error: "Page too far" };
                    }
                    const pageCountRaw = Grabber.regexMatch('<a href="[^"]+pid=(?<page>\\d+)[^"]*"[^>]*>[^<]+</a>\\s*(?:<b>(?<page_2>\\d+)</b>\\s*)?(?:</div>|<br ?/>)|<a href="[^"]+pid=(?<page_3>\\d+)[^"]*"[^>]*>(?:&raquo;|>>)</a>', src);
                    const pageCount = pageCountRaw && pageCountRaw["page"];
                    const images = Grabber.regexToImages('(?:<span[^>]*(?:\\sid="?\\w(?<id>\\d+)"?)?>\\s*)?<a[^>]*(?:\\sid="?\\w(?<id_2>\\d+)"?)[^>]*>\\s*<img [^>]*(?:src|data-original)="(?<preview_url>[^"]+/thumbnail_(?<md5>[^.]+)\\.[^"]+)" [^>]*title="\\s*(?<tags>[^"]+)"[^>]*/?>\\s*</a>|<img\\s+class="preview"\\s+src="(?<preview_url_2>[^"]+/thumbnail_(?<md5_2>[^.]+)\\.[^"]+)" [^>]*title="\\s*(?<tags_2>[^"]+)"[^>]*/?>', src);
                    for (const img of images) {
                        const json = (_a = src.match(new RegExp("posts\\[" + img.id + "\\]\\s*=\\s*({[\\s*\\S]+?})"))) === null || _a === void 0 ? void 0 : _a[1];
                        if (json) {
                            img.rating = (_b = json.match(/["']?rating["']?\s*:\s*["']([^"']+)["']/)) === null || _b === void 0 ? void 0 : _b[1].toLowerCase();
                            img.score = (_c = json.match(/["']?score["']?\s*:\s*["'](\d+)["']/)) === null || _c === void 0 ? void 0 : _c[1];
                            img.author = (_d = json.match(/["']?user["']?\s*:\s*["']([^'"]+)["']/)) === null || _d === void 0 ? void 0 : _d[1];
                        }
                    }
                    return {
                        images: images.map(completeImage),
                        tags: Grabber.regexToTags('<li class="tag-type-(?<type>[^"]+)">(?:[^<]*<a[^>]*>[^<]*</a>)*[^<]*<a[^>]*>(?<name>[^<]*)</a>[^<]*<span[^>]*>\\s*(?<count>\\d+)\\s*</span>', src),
                        pageCount: pageCount ? parseInt(pageCount, 10) / 42 + 1 : undefined,
                    };
                },
            },
            details: {
                url: (id, md5, opts) => {
                    const baseUrl = opts.baseUrl.replace("//api.", "//");
                    return baseUrl + "/index.php?page=post&s=view&id=" + id;
                },
                parse: (src) => {
                    var _a, _b;
                    return {
                        tags: Grabber.regexToTags('<li class="tag-type-(?<type>[^"]+)">(?:[^<]*(?:<span[^>]*>[^<]*)?<a[^>]*>[^<]*</a>(?:[^<]*</span>)?)*[^<]*<a[^>]*>(?<name>[^<]*)</a>[^<]*<span[^>]*>(?<count>\\d+)</span>[^<]*</li>', src),
                        imageUrl: Grabber.regexToConst("url", '<img[^>]+src="([^"]+)"[^>]+onclick="Note\\.toggle\\(\\);"[^>]*/>', src),
                        createdAt: (_a = src.match(/>Posted:\s*([^<]+)</i)) === null || _a === void 0 ? void 0 : _a[1],
                        source: (_b = src.match(/>Source:\s*<a href="([^"]+)"/i)) === null || _b === void 0 ? void 0 : _b[1],
                    };
                },
            },
            endpoints: {
                pool_list: {
                    name: "Pools",
                    input: {},
                    url: (query, opts) => {
                        const pid = (opts.page - 1) * 25;
                        return "/index.php?page=pool&s=list&pid=" + String(pid);
                    },
                    parse: (src) => {
                        const html = Grabber.parseHTML(src);
                        const images = [];
                        const rows = html.find("table tr");
                        for (const row of rows) {
                            const parts = row.find("td");
                            const link = parts[1].find("a")[0];
                            const id = link.attr("href").match(/id=(\d+)/)[1];
                            images.push({
                                id,
                                name: link.innerText(),
                                type: "gallery",
                                gallery_count: parts[2].innerText().match(/(\d+)\s+Images/)[1],
                                details_endpoint: {
                                    endpoint: "pool_details",
                                    input: { id },
                                },
                            });
                        }
                        return { images };
                    },
                },
                pool_details: {
                    input: {
                        id: {
                            type: "input",
                        },
                    },
                    url: (query) => {
                        return "/index.php?page=pool&s=show&id=" + String(query.id);
                    },
                    parse: (src) => {
                        const images = Grabber.regexToImages('<span[^>]*(?: id="?\\w(?<id>\\d+)"?)?>\\s*<a[^>]*(?: id="?\\w(?<id_2>\\d+)"?)[^>]*>\\s*<img [^>]*(?:src|data-original)="(?<preview_url>[^"]+/thumbnail_(?<md5>[^.]+)\\.[^"]+)" [^>]*title="\\s*(?<tags>[^"]+)"[^>]*/?>\\s*</a>|<img\\s+class="preview"\\s+src="(?<preview_url_2>[^"]+/thumbnail_(?<md5_2>[^.]+)\\.[^"]+)" [^>]*title="\\s*(?<tags_2>[^"]+)"[^>]*/?>', src);
                        return {
                            images: images.map(completeImage),
                        };
                    },
                },
            },
            tagTypes: false,
            tags: {
                url: (query, opts) => {
                    const baseUrl = opts.baseUrl.replace("//api.", "//");
                    const sorts = { count: "desc", date: "asc", name: "asc" };
                    const orderBys = { count: "index_count", date: "updated", name: "tag" };
                    const page = (query.page - 1) * 50;
                    return baseUrl + "/index.php?page=tags&s=list&pid=" + page + "&sort=" + sorts[query.order] + "&order_by=" + orderBys[query.order];
                },
                parse: (src) => {
                    return {
                        tags: Grabber.regexToTags('(?:<td>\\s*(?<count_2>\\d+)\\s*</td>\\s*)?<td>\\s*<span class="tag-type-(?<type>[^"]+)">\\s*<a[^>]+>(?<name>.+?)</a>\\s*</span>(?:\\s*<span class="tag-count">(?<count>\\d+)</span>)?\\s*</td>', src),
                    };
                },
            },
            check: {
                url: () => {
                    return "/";
                },
                parse: (src) => {
                    return src.search(/Running Gelbooru(?: Beta)? 0\.2/) !== -1
                        || src.search(/Running <a[^>]*>Gelbooru<\/a>(?: Beta)? 0\.2/) !== -1;
                },
            },
        },
    },
};
