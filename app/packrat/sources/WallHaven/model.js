"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.source = void 0;
function parseSearch(search) {
    let query = "";
    let purity = "111";
    let category = "111";
    let order = "date_added";
    let sort = "desc";
    const ratios = [];
    for (const tag of search.split(" ")) {
        if (tag.indexOf("rating:") === 0) {
            const val = tag.substr(7);
            purity = val === "s" || val === "safe" ? "100" : (val === "e" || val === "explicit" ? "001" : "010");
        }
        else if (tag.indexOf("-rating:") === 0) {
            const val = tag.substr(8);
            purity = val === "s" || val === "safe" ? "011" : (val === "e" || val === "explicit" ? "110" : "101");
        }
        else if (tag.indexOf("category:") === 0) {
            const val = tag.substr(9);
            category = val === "anime" ? "010" : (val === "people" ? "001" : "100");
        }
        else if (tag.indexOf("-category:") === 0) {
            const val = tag.substr(10);
            category = val === "anime" ? "101" : (val === "people" ? "110" : "011");
        }
        else if (tag.indexOf("order:") === 0) {
            const val = tag.substr(6);
            if (val.substr(-5) === "_desc") {
                order = val.substr(0, val.length - 5);
                sort = "desc";
            }
            else if (val.substr(-4) === "_asc") {
                order = val.substr(0, val.length - 4);
                sort = "asc";
            }
            else {
                order = val;
            }
        }
        else if (tag.indexOf("ratio:") === 0) {
            const val = tag.substr(6);
            ratios.push(val.replace(/[:/]/g, "x"));
        }
        else {
            query += (query ? " " : "") + tag;
        }
    }
    return { query, purity, category, order, sort, ratios };
}
exports.source = {
    name: "WallHaven",
    modifiers: ["rating:s", "rating:safe", "rating:q", "rating:questionable", "rating:e", "rating:explicit", "order:relevance", "order:random", "order:date_added", "order:views", "order:favorites", "order:toplist", "order:hot", "category:general", "category:anime", "category:people"],
    forcedTokens: ["tags"],
    auth: {
        url: {
            type: "url",
            fields: [
                {
                    id: "apiKey",
                    key: "apikey",
                },
            ],
        },
    },
    apis: {
        json: {
            name: "JSON",
            auth: [],
            forcedLimit: 24,
            search: {
                url: (query) => {
                    const search = parseSearch(query.search);
                    const params = {
                        q: search.query,
                        purity: search.purity,
                        categories: search.category,
                        page: query.page,
                        sorting: search.order,
                        order: search.sort,
                    };
                    if (search.ratios.length > 0) {
                        params["ratios"] = search.ratios.join(",");
                    }
                    return "/api/v1/search?" + Grabber.buildQueryParams(params);
                },
                parse: (src) => {
                    const map = {
                        "id": "id",
                        // "page_url": "url",
                        "source": "source",
                        "width": "dimension_x",
                        "height": "dimension_y",
                        "file_size": "file_size",
                        "created_at": "created_at",
                        "file_url": "path",
                        "sample_url": "path",
                        "preview_url": "thumbs.original",
                    };
                    const data = JSON.parse(src);
                    const images = [];
                    for (const image of data["data"]) {
                        const img = Grabber.mapFields(image, map);
                        img.page_url = "/api/v1/w/" + img.id;
                        img.rating = image.purity === "sfw" ? "safe" : (image.purity === "nsfw" ? "explicit" : "questionable");
                        img.ext = image.file_type === "image/png" ? "png" : (image.file_type === "image/jpeg" ? "jpg" : undefined);
                        images.push(img);
                    }
                    return {
                        images,
                        pageCount: data["meta"]["last_page"],
                        imageCount: data["meta"]["total"],
                    };
                },
            },
            details: {
                url: (id, md5) => {
                    return "/api/v1/w/" + id;
                },
                parse: (src) => {
                    const data = JSON.parse(src)["data"];
                    return {
                        tags: data["tags"].map((tag) => ({
                            id: tag["id"],
                            name: tag["name"],
                            type: tag["category"],
                            typeId: tag["category_id"],
                        })),
                        imageUrl: data["path"],
                        createdAt: data["created_at"],
                    };
                },
            },
        },
    },
};
