"""Web services: buttons in the viewer that open the current image on another site - reverse image
search mostly (Grabber's "web services"). Each is a name and an address with placeholders:

  {url} / %url%     the image's file address (encoded)
  {md5} / %md5%     its MD5          {page} / %page%   its page on the site
  {id} / %id%       its post id      {site} / %website% the site

The defaults are Grabber's list, updated (waifu2x's old site is gone) plus e621's own reverse search,
which also finds AI and furry art the general engines miss.
"""
from __future__ import annotations

import webbrowser
from urllib.parse import quote

DEFAULTS = [
    {"name": "IQDB", "url": "https://iqdb.org/?url={url}"},
    {"name": "SauceNAO", "url": "https://saucenao.com/search.php?db=999&url={url}"},
    {"name": "Google Lens", "url": "https://lens.google.com/uploadbyurl?url={url}"},
    {"name": "TinEye", "url": "https://www.tineye.com/search/?url={url}"},
    {"name": "Yandex", "url": "https://yandex.com/images/search?rpt=imageview&url={url}"},
    {"name": "Bing", "url": "https://www.bing.com/images/search?view=detailv2&iss=sbi&q=imgurl:{url}"},
    {"name": "e621 reverse search", "url": "https://e621.net/iqdb_queries?url={url}"},
]


def services(st) -> list[dict]:
    lst = st.get("WebServices") or []
    out = [s for s in lst if isinstance(s, dict) and s.get("url")]
    return out or [dict(d) for d in DEFAULTS]


def expand(template: str, img) -> str:
    vals = {
        "url": quote(img.file_url or img.sample_url or img.preview_url or "", safe=""),
        "md5": img.md5 or "",
        "id": str(img.id or ""),
        "page": quote(img.page_url or "", safe=""),
        "site": img.site_key or "",
        "website": img.site_key or "",
    }
    out = template
    for k, v in vals.items():
        out = out.replace("{" + k + "}", v).replace("%" + k + "%", v)
    return out


def open_service(svc: dict, img) -> str:
    url = expand(str(svc.get("url") or ""), img)
    if url.startswith(("http://", "https://")):
        webbrowser.open(url)
    return url
