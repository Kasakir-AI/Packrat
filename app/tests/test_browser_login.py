"""'Use my login from a browser': only the one site's cookies are taken, and Windows Chrome is refused clearly."""
import sqlite3
import time

import requests

from packrat.grab import browser_login as BL
from packrat.grab import secrets


def firefox_cookies(path):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE moz_cookies (id INTEGER PRIMARY KEY, originAttributes TEXT, name TEXT, value TEXT, "
               "host TEXT, path TEXT, expiry INTEGER, lastAccessed INTEGER, creationTime INTEGER, isSecure INTEGER, "
               "isHttpOnly INTEGER, inBrowserElement INTEGER, sameSite INTEGER, rawSameSite INTEGER, "
               "schemeMap INTEGER, isPartitionedAttributeSet INTEGER)")
    exp = int(time.time()) + 3600
    for name, value, host in (("_danbooru2_session", "s3cr3t-session-value-1234", ".donmai.us"),
                              ("remember", "yes", "danbooru.donmai.us"), ("other", "x", ".example.com")):
        db.execute("INSERT INTO moz_cookies (originAttributes, name, value, host, path, expiry, lastAccessed, "
                   "creationTime, isSecure, isHttpOnly, inBrowserElement, sameSite, rawSameSite, schemeMap, "
                   "isPartitionedAttributeSet) VALUES ('', ?, ?, ?, '/', ?, 0, 0, 1, 1, 0, 0, 0, 2, 0)",
                   (name, value, host, exp))
    db.commit()
    db.close()


def test_only_the_sites_cookies_are_imported(tmp_path, monkeypatch):
    f = tmp_path / "cookies.sqlite"
    firefox_cookies(f)
    saved = []
    site = type("Site", (), {"host": "danbooru.donmai.us", "name": "Danbooru", "cookies": requests.cookies.RequestsCookieJar(),
                             "save_session_cookies": lambda self: saved.append(1)})()
    monkeypatch.setattr(secrets, "register_redaction", lambda v: None)
    n = BL.import_login(site, BL.browser("firefox"), cookie_file=str(f))
    assert n == 2 and saved == [1]
    assert {c.name for c in site.cookies} == {"_danbooru2_session", "remember"}


def test_windows_chromium_browsers_are_refused_with_a_reason():
    b = BL.browser("chrome", "win32")
    assert not b.supported and "built-in browser" in b.why
    assert BL.browser("firefox", "win32").supported
    assert BL.site_domain("chan.sankakucomplex.com") == "sankakucomplex.com"
