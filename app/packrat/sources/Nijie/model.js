"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.source = void 0;
function completeImage(img) {
    // Detect galleries
    if ("additional" in img) {
        if (img.additional.includes('"漫画"')) {
            img.type = "gallery";
        }
    }
    // Generate full URL from thumbnail URL and vice-versa
    if (img.preview_url && !img.file_url) {
        const url = img.preview_url.replace(new RegExp("/__rs_l\d+x\d+/"), "/");
        if (url !== img.preview_url) {
            img.file_url = url;
        }
    }
    else if (img.file_url && !img.preview_url) {
        const url = img.file_url.replace(new RegExp("/nijie/"), "/__rs_l200x200/nijie/");
        if (url !== img.file_url) {
            img.preview_url = url;
        }
    }
    return img;
}
const meta = {
    user: {
        type: "input",
        parser: parseInt,
    },
    type: {
        type: "options",
        default: "images",
        options: ["images", "doujin", "bookmarks"],
    },
};
exports.source = {
    name: "Nijie",
    modifiers: [],
    searchFormat: {
        and: " ",
    },
    auth: {
        session: {
            type: "post",
            url: "/login_int.php",
            fields: [
                {
                    id: "pseudo",
                    key: "email",
                },
                {
                    id: "password",
                    key: "password",
                    type: "password",
                },
            ],
            check: {
                type: "redirect",
                url: "/index.php",
            },
        },
    },
    apis: {
        html: {
            name: "Regex",
            auth: ["session"],
            forcedTokens: ["tags"],
            forcedLimit: 48,
            search: {
                url: (query) => {
                    const search = Grabber.parseSearchQuery(query.search, meta);
                    if (search.user) {
                        if (search.type === "doujin") {
                            return "/members_dojin.php?id=" + search.user;
                        }
                        if (search.type === "bookmarks") {
                            return "/user_like_illust_view.php?p=" + query.page + "&id=" + search.user;
                        }
                        return "/members_illust.php?p=" + query.page + "&id=" + search.user;
                    }
                    return "/search.php?type=&word=" + encodeURIComponent(search.query) + "&p=" + query.page + "&mode=&illust_type=&sort=&pd=&con=";
                },
                parse: (src) => {
                    return {
                        images: Grabber.regexToImages('<div class="nijie[^"]*"[^>]*>(?<additional>.+?)<img class="mozamoza ngtag" illust_id="(?<id>[^"]+)" user_id="(?<author_id>[^"]+)" itemprop="image" src="(?<preview_url>[^"]+)" alt="(?<name>[^"]+)"', src).map(completeImage),
                        imageCount: Grabber.countToInt(Grabber.regexToConst("count", '<p class="mem-indent float-left">.*?<em>(?<count>[\\d,.]+)', src)),
                    };
                },
            },
            gallery: {
                url: (query) => {
                    return "/view_popup.php?id=" + query.id;
                },
                parse: (src) => {
                    const images = Grabber.regexToImages('<div id="diff_\\d*"[^>]*>.*?<img src="(?<file_url>[^"]+)"', src).map(completeImage);
                    return {
                        images,
                        pageCount: 1,
                        imageCount: images.length,
                    };
                },
            },
            details: {
                url: (id, md5) => {
                    return "/view.php?id=" + id;
                },
                parse: (src) => {
                    let imageUrl = Grabber.regexToConst("url", '<div id="gallery_open">.+?<a [^>]*href="(?<url>[^"]+)"', src);
                    if (!imageUrl || imageUrl.includes("view_popup.php")) {
                        imageUrl = Grabber.regexToConst("url", '<div id="gallery_open">.+?<img class="mozamoza ngtag" [^>]*src="(?<url>[^"]+)"', src);
                    }
                    return {
                        tags: Grabber.regexToTags('<span class="tag_name"><a[^>]*>(?<name>[^<]+)</a></span>', src),
                        imageUrl,
                    };
                },
            },
            check: {
                url: () => {
                    return "/";
                },
                parse: (src) => {
                    return src.indexOf("株式会社ニジエ") !== -1;
                },
            },
        },
    },
};
