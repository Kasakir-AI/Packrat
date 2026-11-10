"""Signed-in access to e6ai / e621 (whole-site archive): checking a login + API key, and re-fetching posts that were hidden
from anonymous visitors.

e6ai and e621 (e621ng) do not accept passwords from tools. API calls are signed in with HTTP Basic auth
using the login name and an API key generated on the account page. This module never sees or
stores a password.

How login-gated posts look (e621ng app/models/post.rb, `visible?` / `hidden_attributes`): when a
post is hidden from the current visitor the API omits file.url / sample / preview URLs and blanks
md5 and ext. The archiver records such posts with file_status = 'no_url'. Posts from the daily
export keep their md5, so their static URL is derived and they are not 'no_url'.
"""
from __future__ import annotations

import hashlib
import logging
import time
from urllib.parse import quote

from .client import Client
from .events import emit

log = logging.getLogger("packrat")

# Fields /users/<name>.json only returns to that user when signed in as them (e621ng
# UsersController#show -> User#api_attributes, the "self" branch).
SELF_FIELDS = ("blacklisted_tags", "api_burst_limit", "remaining_api_limit", "per_page", "email",
               "enable_safe_mode", "upload_limit")
API_KEY_PAGE = "https://e6ai.net/users/home"
CHUNK = 100
RETRY_AFTER_S = 24 * 3600  # same account + same hidden set: check again at most once a day


def test_login(c: Client, login: str) -> dict:
    """ONE signed-in request (GET /users/<login>.json). Raises AuthError (401), ForbiddenError (403)."""
    if not c.authenticated:
        raise ValueError("no API key: set PACKRAT_API_KEY (the control panel does this for you)")
    data = c.get_json(f"/users/{quote(login, safe='')}.json")
    if isinstance(data, dict) and isinstance(data.get("user"), dict):
        data = data["user"]
    if not isinstance(data, dict):
        raise RuntimeError("unexpected reply from /users: not a JSON object")
    self_view = any(k in data for k in SELF_FIELDS)
    res = {"ok": self_view, "login": login, "user_id": data.get("id"), "name": data.get("name"),
           "level": data.get("level_string"), "safe_mode": data.get("enable_safe_mode"),
           "requests": c.request_count}
    if self_view:
        res["message"] = f"Signed in as {data.get('name') or login}" + (
            f" ({data['level_string']})" if data.get("level_string") else "") + "."
        if data.get("enable_safe_mode"):
            res["warning"] = ("Safe mode is on in your account settings on the site: questionable and explicit "
                              "posts stay hidden even when signed in.")
    else:
        res["message"] = ("The site answered, but not with your private account fields: the key may "
                          "belong to another account, or the login name is misspelled.")
    return res


def login_required_count(s, include_deleted: bool = False) -> int:
    where = "file_status = 'no_url'" + ("" if include_deleted else " AND COALESCE(is_deleted, 0) = 0")
    return s.db.execute(f"SELECT COUNT(*) FROM posts WHERE {where}").fetchone()[0]


def _ids(s, include_deleted: bool, max_posts: int | None) -> list[int]:
    where = "file_status = 'no_url'" + ("" if include_deleted else " AND COALESCE(is_deleted, 0) = 0")
    ids = [r[0] for r in s.db.execute(f"SELECT id FROM posts WHERE {where} ORDER BY id DESC")]
    return ids[:max_posts] if max_posts else ids


def retry_login_required(c: Client, s, max_posts: int | None = None, include_deleted: bool = False) -> dict:
    """Re-fetch metadata for posts recorded without a file URL, 100 per request, and queue the ones
    whose file is now visible (file_status -> 'pending'). Uses the client's limiter and Stop/Pause."""
    ids = _ids(s, include_deleted, max_posts)
    res = {"checked": len(ids), "now_downloadable": 0, "still_hidden": 0, "not_returned": 0, "requests": 0}
    if not ids:
        return res
    emit("phase", name=f"re-checking {len(ids)} posts that needed sign-in")
    before = c.request_count
    for i in range(0, len(ids), CHUNK):
        chunk = ids[i:i + CHUNK]
        got = c.posts(None, 320, "status:any id:" + ",".join(map(str, chunk)))
        seen = set()
        for p in got:
            seen.add(p.get("id"))
            if s.upsert_post(p) == "pending":
                res["now_downloadable"] += 1
            else:
                res["still_hidden"] += 1
        res["not_returned"] += len(set(chunk) - seen)
        s.commit()
    res["requests"] = c.request_count - before
    return res


def auto_retry(c: Client, s, enabled: bool = True) -> dict | None:
    """Called at the start of signed-in run / full / download. Skipped when not signed in, when
    disabled, when nothing is waiting, or when this account already checked the same posts in the
    last 24 hours (so repeated runs don't re-ask for posts it still can't see)."""
    if not c.authenticated:
        n = login_required_count(s)
        if n:
            log.info("%d posts were saved without a file URL (hidden from anonymous visitors?); "
                     "sign in with a login name + API key to fetch them", n)
        return None
    if not enabled:
        log.info("signed in; automatic retry of login-required posts is off (--no-login-retry)")
        return None
    ids = _ids(s, False, None)
    if not ids:
        log.info("signed in; no posts are waiting for sign-in")
        return None
    sig = hashlib.sha256((c.login or "").encode() + b"|" + ",".join(map(str, ids)).encode()).hexdigest()[:16]
    last = s.get_state("login_retry_last") or {}
    if isinstance(last, dict) and last.get("sig") == sig and time.time() - float(last.get("at", 0)) < RETRY_AFTER_S:
        log.info("signed in; %d posts still hidden were already re-checked with this account in the last "
                 "24 h; skipping (run 'recheck' to force)", len(ids))
        return None
    log.info("signed in; re-checking %d posts saved without a file URL (%d request(s))", len(ids),
             -(-len(ids) // CHUNK))
    res = retry_login_required(c, s)
    left = _ids(s, False, None)
    sig = hashlib.sha256((c.login or "").encode() + b"|" + ",".join(map(str, left)).encode()).hexdigest()[:16]
    s.set_state("login_retry_last", {"sig": sig, "at": time.time(), "login": c.login})
    s.commit()
    log.info("login-required posts: %d now downloadable, %d still hidden, %d not returned by the site",
             res["now_downloadable"], res["still_hidden"], res["not_returned"])
    emit("login_retry", **res)
    return res
