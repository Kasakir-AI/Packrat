"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.source = void 0;
const map = {
    "id": "id",
    "created_at": "creationTime",
    "md5": "checksumMD5",
    "source": "source",
    "rating": "safety", // FIXME
    "width": "canvasWidth",
    "height": "canvasHeight",
    "file_url": "contentUrl",
    "preview_url": "thumbnailUrl",
    "tags": "tags", // FIXME (names, category, usages)
    "author": "user.name",
    "score": "score",
};
const safetyMap = {
    "safe": "safe",
    "sketchy": "questionable",
    "unsafe": "explicit",
};
function buildImage(data) {
    const img = Grabber.mapFields(data, map);
    img.tags = data["tags"].map((tag) => ({
        name: tag.names[0],
        type: tag.category,
        count: tag.usages,
    }));
    img.rating = safetyMap[data["safety"]];
    return img;
}
exports.source = {
    name: "Szurubooru",
    modifiers: [
        "id:", "tag:", "score:", "uploader:", "upload:", "submit:", "comment:", "fav:", "source:", "pool:",
        "tag-count:", "comment-count:", "fav-count:", "note-count:", "note-text:", "relation-count:", "feature-count:",
        "type:", "flag:", "sha1:", "md5:", "content-checksum:", "file-size:", "image-width:", "image-height:",
        "image-area:", "image-aspect-ratio:", "image-ar:", "width:", "height:", "area:", "aspect-ratio:", "ar:",
        "creation-date:", "creation-time:", "date:", "time:", "last-edit-date:", "last-edit-time:", "edit-date:",
        "edit-time:", "comment-date:", "comment-time:", "fav-date:", "fav-time:", "feature-date:", "feature-time:",
        "safety:", "rating:general", "rating:safe", "rating:questionable", "rating:explicit", "rating:g", "rating:s",
        "rating:q", "rating:e",
        "sort:random", "sort:id", "sort:score", "sort:tag-count", "sort:comment-count", "sort:fav-count",
        "sort:note-count", "sort:relation-count", "sort:feature-count", "sort:file-size", "sort:image-width",
        "sort:image-height", "sort:image-area", "sort:width", "sort:height", "sort:area", "sort:creation-date",
        "sort:creation-time", "sort:date", "sort:last-edit-date", "sort:last-edit-time", "sort:edit-date",
        "sort:edit-time", "sort:comment-date", "sort:comment-time", "sort:fav-date", "sort:fav-time",
        "sort:feature-date", "sort:feature-time",
        "special:liked", "special:disliked", "special:fav", "special:tumbleweed",
    ],
    auth: {
        basic: {
            type: "http_basic",
            passwordType: "password",
        },
        token: {
            type: "http_basic",
            passwordType: "apiKey",
            tokenType: "Token",
        },
    },
    apis: {
        json: {
            name: "JSON",
            auth: ["basic", "token"],
            maxLimit: 100,
            search: {
                url: (query, opts) => {
                    const offset = (query.page - 1) * opts.limit;
                    return {
                        url: "/api/posts/?offset=" + offset + "&limit=" + opts.limit + "&query=" + query.search,
                        headers: {
                            "Accept": "application/json",
                        },
                    };
                },
                parse: (src) => {
                    const data = JSON.parse(src);
                    return {
                        images: data.results.map(buildImage),
                        imageCount: data.total
                    };
                },
            },
            details: {
                fullResults: true,
                url: (id) => {
                    return {
                        url: "/api/post/" + id,
                        headers: {
                            "Accept": "application/json",
                        },
                    };
                },
                parse: (src) => {
                    const data = JSON.parse(src);
                    return buildImage(data);
                },
            },
            tagTypes: {
                url: () => {
                    return {
                        url: "/api/tag-categories",
                        headers: {
                            "Accept": "application/json",
                        },
                    };
                },
                parse: (src) => {
                    const data = JSON.parse(src);
                    const types = data.results.map((raw) => ({
                        name: raw.name,
                    }));
                    return { types };
                },
            },
            tags: {
                url: (query, opts) => {
                    const orderMap = {
                        "count": "usages",
                        "date": "creation-date",
                        "name": "name",
                    };
                    const offset = (query.page - 1) * opts.limit;
                    return {
                        url: "/api/tags?offset=" + offset + "&limit=" + opts.limit + "&query=sort:" + orderMap[query.order],
                        headers: {
                            "Accept": "application/json",
                        },
                    };
                },
                parse: (src) => {
                    const data = JSON.parse(src);
                    const tags = data.results.map((raw) => ({
                        name: raw.names[0],
                        count: raw.usages,
                        type: raw.category,
                    }));
                    return { tags };
                },
            },
        },
    },
};
