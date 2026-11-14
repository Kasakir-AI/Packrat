"""Packrat: a polite multi-site image archiver (formerly e6ai-archiver).

Two engines share one window:
  * the whole-site archive (e6ai / e621): daily database exports, the JSON API, checksums, reports;
  * Browse / Downloads (packrat.grab): every Grabber source (Danbooru, Gelbooru, Pixiv, e621, ...),
    with Grabber's filename language, MD5 list, blacklist, favorites and monitors.
"""
__version__ = "3.4.0"
