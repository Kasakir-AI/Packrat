"use strict";
Grabber.makeArray = (val, allowFalsy = false) => {
    if (!val && !allowFalsy) {
        return [];
    }
    if (!Array.isArray(val)) {
        return [val];
    }
    return val;
};
Grabber.regexMatch = (regexp, src) => {
    const matches = Grabber.regexMatches(regexp, src);
    if (matches && matches.length > 0) {
        return matches[0];
    }
    return undefined;
};
Grabber.mapObject = (obj, fn) => {
    const ret = {};
    for (const k in obj) {
        ret[k] = fn(obj[k]);
    }
    return ret;
};
Grabber.typedXML = (val) => {
    if (val && typeof val === "object" && ("#text" in val || "@attributes" in val)) {
        const txt = val["#text"];
        const isNil = "@attributes" in val && "nil" in val["@attributes"] && val["@attributes"]["nil"] === "true";
        if (isNil) {
            return null;
        }
        const type = "@attributes" in val && "type" in val["@attributes"] ? val["@attributes"]["type"] : undefined;
        if (type === "integer") {
            return parseInt(txt, 10);
        }
        else if (type === "array") {
            delete val["@attributes"]["type"];
            if (Object.keys(val["@attributes"]).length === 0) {
                delete val["@attributes"];
            }
            return Grabber.mapObject(val, Grabber.typedXML);
        }
        if (txt !== undefined) {
            return txt;
        }
    }
    if (val && val instanceof Array) {
        return val.map(Grabber.typedXML);
    }
    if (val && typeof val === "object") {
        if (Object.keys(val).length === 0) {
            return "";
        }
        return Grabber.mapObject(val, Grabber.typedXML);
    }
    return val;
};
/**
 * Set a value in an object using the dot ("a.b.c") path notation.
 */
function _set(obj, path, value) {
    const parts = path.split(".");
    for (let i = 0; i < parts.length - 1; ++i) {
        const part = parts[i];
        if (!(part in obj)) {
            obj[part] = {};
        }
        obj = obj[part];
    }
    obj[parts[parts.length - 1]] = value;
}
/**
 * Get a value in an object using the dot ("a.b.c") path notation.
 */
function _get(obj, path) {
    return path.split(".").reduce((ctx, part) => ctx === null || ctx === void 0 ? void 0 : ctx[part], obj);
}
Grabber.mapFields = (data, map) => {
    const result = {};
    if (typeof data !== "object") {
        return result;
    }
    for (const to in map) {
        const from = map[to];
        const value = _get(data, from);
        if (value !== null) {
            _set(result, to, value);
        }
    }
    return result;
};
Grabber.countToInt = (str) => {
    if (!str) {
        return undefined;
    }
    let count;
    const normalized = str.toLowerCase().trim().replace(/,/g, "");
    if (normalized.slice(-1) === "m") {
        const withoutK = normalized.substring(0, normalized.length - 1).trim();
        count = parseFloat(withoutK) * 1000000;
    }
    else if (normalized.slice(-1) === "k") {
        const withoutK = normalized.substring(0, normalized.length - 1).trim();
        count = parseFloat(withoutK) * 1000;
    }
    else {
        count = parseFloat(normalized);
    }
    return Math.round(count);
};
Grabber.fileSizeToInt = (str) => {
    if (typeof str !== "string") {
        return str;
    }
    const res = str.match(/^(\d+(?:\.\d+)?)\s*(\w+)$/);
    if (res) {
        const val = parseFloat(res[1]);
        const unit = res[2].toLowerCase();
        if (unit === "mb") {
            return Math.round(val * 1024 * 1024);
        }
        if (unit === "kb") {
            return Math.round(val * 1024);
        }
        return Math.round(val);
    }
    return parseInt(str, 10);
};
Grabber.fixPageUrl = (url, page, previous, pageTransformer) => {
    if (!pageTransformer) {
        pageTransformer = (p) => p;
    }
    url = url.replace("{page}", String(pageTransformer(page)));
    if (previous) {
        url = url.replace("{min}", previous.minId);
        url = url.replace("{max}", previous.maxId);
        url = url.replace("{min-1}", previous.minIdM1);
        url = url.replace("{max+1}", previous.maxIdP1);
    }
    return url;
};
Grabber.pageUrl = (page, previous, limit, ifBelow, ifPrev, ifNext, pageTransformer) => {
    const pageLimit = pageTransformer ? pageTransformer(page) : page;
    if (pageLimit <= limit || limit < 0) {
        return Grabber.fixPageUrl(ifBelow, page, previous, pageTransformer);
    }
    if (previous && previous.page === page + 1) {
        return Grabber.fixPageUrl(ifPrev, page, previous, pageTransformer);
    }
    if (previous && previous.page === page - 1) {
        return Grabber.fixPageUrl(ifNext, page, previous, pageTransformer);
    }
    throw new Error("You need valid previous page information to browse that far");
};
Grabber.regexToImages = (regexp, src) => {
    const images = [];
    const matches = Grabber.regexMatches(regexp, src);
    for (const match of matches) {
        if ("json" in match) {
            const json = JSON.parse(match["json"]);
            for (const key in json) {
                match[key] = json[key];
            }
        }
        if (match.id) {
            match.id = parseInt(match.id, 10);
        }
        if (match.file_size) {
            match.file_size = Grabber.fileSizeToInt(match.file_size);
        }
        images.push(match);
    }
    return images;
};
Grabber.pick = (obj, keys) => {
    return keys.reduce((ret, key) => {
        if (key in obj && obj[key] !== undefined) {
            ret[key] = obj[key];
        }
        return ret;
    }, {});
};
Grabber.regexToTags = (regexp, src) => {
    const tags = [];
    const uniques = {};
    const matches = Grabber.regexMatches(regexp, src);
    for (const match of matches) {
        if (match["name"] in uniques) {
            continue;
        }
        if ("count" in match) {
            match["count"] = Grabber.countToInt(match["count"]);
        }
        tags.push(Grabber.pick(match, ["id", "name", "count", "type", "typeId"]));
        uniques[match["name"]] = true;
    }
    return tags;
};
Grabber.regexToPools = (regexp, src) => {
    const pools = [];
    const matches = Grabber.regexMatches(regexp, src);
    for (const match of matches) {
        pools.push(match);
    }
    return pools;
};
Grabber.regexToConst = (key, regexp, src) => {
    const match = Grabber.regexMatch(regexp, src);
    if (match) {
        return match[key];
    }
    return undefined;
};
function _visitSearch(search, tag, and, or) {
    if ("operator" in search) {
        const left = _visitSearch(search.left, tag, and, or);
        const right = _visitSearch(search.right, tag, and, or);
        const func = search.operator === "and" ? and : or;
        return func(left, right);
    }
    else {
        return tag(search);
    }
}
Grabber.visitSearch = _visitSearch;
Grabber.buildQueryParams = (params) => {
    const ret = [];
    for (const key in params) {
        ret.push(encodeURIComponent(key) + "=" + encodeURIComponent(params[key]));
    }
    return ret.join("&");
};
function parseSearchVal(value, meta) {
    if (meta.type === "options") {
        for (const opt of meta.options) {
            const optVal = typeof opt === "string" ? opt : opt.value;
            if (optVal === value) {
                return value;
            }
        }
    }
    else if (meta.type === "input") {
        return meta.parser ? meta.parser(value) : value;
    }
    else if (meta.type === "bool") {
        if (value === "true" || value === "yes" || value === "1") {
            return true;
        }
        else if (value === "false" || value === "no" || value === "0") {
            return false;
        }
        console.warn(`Unknown boolean value: "${value}"`);
        return Boolean(value);
    }
    return null;
}
Grabber.parseSearchQuery = (query, metas) => {
    const tags = [];
    const ret = {};
    // Default values
    for (const key in metas) {
        if (metas[key].default) {
            ret[key] = metas[key].default;
        }
    }
    // Parse search
    for (const part of query.split(" ")) {
        const index = part.indexOf(":");
        if (index !== -1) {
            const key = part.substring(0, index);
            if (key in metas) {
                const val = parseSearchVal(part.substring(index + 1), metas[key]);
                if (val !== null) {
                    ret[key] = val;
                    continue;
                }
            }
        }
        tags.push(part);
    }
    ret.query = tags.join(" ");
    return ret;
};
// Fix console calls since C++ handlers can't get variadic arguments
const originalConsole = console;
function argToString(arg) {
    if (typeof arg === "object") {
        return JSON.stringify(arg);
    }
    return String(arg);
}
function argsToString(args) {
    return args.map(argToString).join(' ');
}
// @ts-ignore
console = {
    debug: (...args) => originalConsole.debug(argsToString(args)),
    error: (...args) => originalConsole.error(argsToString(args)),
    info: (...args) => originalConsole.info(argsToString(args)),
    log: (...args) => originalConsole.log(argsToString(args)),
    warn: (...args) => originalConsole.warn(argsToString(args)),
};
