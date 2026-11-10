"""Site logins, as described by each Grabber model's `auth` section.

  url         credentials added to every request URL (login=..&api_key=..), checked by loading a page
  http_basic  Authorization: Basic base64(login:password-or-api-key)
  get / post  a login form: fields (+ CSRF fields read from a page), success = a cookie or a redirect
  oauth2      client_credentials | password | password_json | refresh_token | authorization_code | pkce
              (tokens kept in the keyring; refreshed automatically when they expire or a request gets 401)
  oauth1      HMAC-SHA1 three-legged flow with a PIN (old Twitter API)

Interactive steps (open a browser, paste a code / PIN) go through the `ask` callback the window or the
command line provides; without one those methods explain what to do instead of failing silently.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.server
import json
import logging
import secrets as pysecrets
import threading
import time
import webbrowser
from urllib.parse import parse_qs, quote, urlencode, urlsplit

log = logging.getLogger("packrat.grab")
OAUTH_PORT = 58923
ASK = None   # set by the window / CLI: ask(title, message, default="") -> str | None


def login(site, auth: dict, ask=None) -> tuple[bool, str]:
    ask = ask or ASK
    t = auth.get("type")
    if t in ("url", "http_basic"):
        return _check_by_page(site, auth)
    if t in ("get", "post"):
        return _form_login(site, auth)
    if t == "oauth2":
        return oauth2_login(site, auth, ask)
    if t == "oauth1":
        return oauth1_login(site, auth, ask)
    return False, f"unknown login type {t!r}"


def _check_by_page(site, auth) -> tuple[bool, str]:
    """Like Grabber: the credentials are sent with every request; check them by loading a page (the
    'max_page' page when the model says anonymous visitors can't go that far)."""
    check = auth.get("check") or {}
    page = int(check.get("value") or 0) if check.get("type") == "max_page" else 0
    site.logged_in = True   # so APIs that need the login are used for the test
    pr = site.search("", page=max(1, page), limit=5)
    if pr.error:
        site.logged_in = False
        if "401" in pr.error or "403" in pr.error:
            return False, f"not accepted ({pr.error})"
        return False, pr.error
    if not pr.images:
        site.logged_in = False
        return False, "the site answered but returned no images (wrong credentials?)"
    return True, "signed in (credentials accepted)"


def _form_login(site, auth) -> tuple[bool, str]:
    url = auth.get("url") or site.setting("login/post/url") or ""
    if not url:
        return False, "this source has no login form URL"
    url = site.fix_url(url)
    cookie_name = (auth.get("check") or {}).get("key") if (auth.get("check") or {}).get("type") == "cookie" else \
        site.setting("login/post/cookie", "")
    if cookie_name and _has_cookie(site, cookie_name):
        return True, "already signed in (session cookie)"
    fields = {}
    for f in auth.get("fields") or []:
        if f.get("key"):
            fields[f["key"]] = site._field_value(f)
    csrf = auth.get("csrf") or {}
    if csrf.get("url"):
        st, text, _ = site.get_text(site.fix_url(csrf["url"]), "page")
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(text, "html.parser")
        for name in csrf.get("fields") or []:
            inp = soup.select_one(f'input[name="{name}"]')
            if inp is None:
                log.warning("%s: could not find HTML field '%s'", site.name, name)
                continue
            fields[name] = inp.get("value") or ""
    headers = dict(auth.get("headers") or {})
    method = "POST" if auth.get("type") == "post" else "GET"
    if method == "POST":
        r = site.request(url, "page", method="POST", data=fields, headers=headers, allow_redirects=False)
    else:
        sep = "&" if "?" in url else "?"
        r = site.request(url + sep + urlencode(fields), "page", headers=headers, allow_redirects=False)
    try:
        ok = bool(cookie_name) and _has_cookie(site, cookie_name)
        redirect = (auth.get("check") or {}).get("url") if (auth.get("check") or {}).get("type") == "redirect" else ""
        if redirect:
            ok = redirect in (r.headers.get("Location") or "")
        if not cookie_name and not redirect:
            ok = r.status_code < 400
    finally:
        r.close()
    if ok:
        site.save_session_cookies()
        return True, "signed in"
    return False, "the site did not accept the login (check the user name and password)"


def _has_cookie(site, name: str) -> bool:
    for c in site.cookies:
        if c.name == name and c.value and c.value != "0":
            return True
    return False


# ---- OAuth 2 ------------------------------------------------------------------------------------
def _store_tokens(site, data: dict) -> bool:
    at = data.get("access_token") or (data.get("response") or {}).get("access_token")
    if not at:
        return False
    site.set_auth_value("accessToken", at)
    rt = data.get("refresh_token") or (data.get("response") or {}).get("refresh_token")
    if rt:
        site.set_auth_value("refreshToken", rt)
    exp = data.get("expires_in") or (data.get("response") or {}).get("expires_in")
    expires = time.time() + float(exp) - 60 if exp else _jwt_exp(at)
    site.set_setting("auth/accessTokenExpiration", expires or 0)
    from . import secrets
    secrets.register_redaction(at)
    secrets.register_redaction(rt or "")
    return True


def _jwt_exp(token: str):
    if token.count(".") != 2:
        return None
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload)).get("exp")) - 60
    except (ValueError, TypeError, AttributeError):
        return None


def _post_token(site, auth, body: dict, headers: dict | None = None, as_json=False) -> dict:
    url = site.fix_url(auth.get("tokenUrl") or "")
    h = dict(headers or {})
    key, secret = site.auth_value("consumerKey"), site.auth_value("consumerSecret")
    if key and secret and not as_json:
        if auth.get("clientAuthentication") == "header":
            cred = base64.b64encode(f"{quote(key, safe='')}:{quote(secret, safe='')}".encode()).decode()
            h["Authorization"] = "Basic " + cred
        else:
            body.setdefault("client_id", key)
            body.setdefault("client_secret", secret)
    if auth.get("scopes") and "scope" not in body:
        body["scope"] = " ".join(auth["scopes"])
    if as_json:
        h["Content-Type"] = "application/json"
        r = site.request(url, "page", method="POST", data=json.dumps(body), headers=h)
    else:
        h["Content-Type"] = "application/x-www-form-urlencoded;charset=UTF-8"
        r = site.request(url, "page", method="POST", data=urlencode(body), headers=h)
    try:
        try:
            data = r.json()
        except ValueError:
            data = {"error": f"HTTP {r.status_code}: {r.text[:200]}"}
        if r.status_code >= 400 and "error" not in data:
            data["error"] = f"HTTP {r.status_code}"
        return data
    finally:
        r.close()


def oauth2_refresh(site, auth) -> bool:
    rt = site.auth_value("refreshToken")
    if not rt:
        return False
    if auth.get("authType") == "password_json":
        data = _post_token(site, auth, {"refresh_token": rt}, as_json=True)
    else:
        body = {"grant_type": "refresh_token", "refresh_token": rt,
                "client_id": site.auth_value("consumerKey"), "client_secret": site.auth_value("consumerSecret")}
        if auth.get("authType") == "pkce" or "pixiv" in site.host:
            body["include_policy"] = "true"
        data = _post_token(site, auth, body)
    ok = _store_tokens(site, data)
    if not ok:
        log.warning("%s: refreshing the login token failed: %s", site.name, data.get("error") or data)
    return ok


def oauth2_login(site, auth, ask=None) -> tuple[bool, str]:
    now = time.time()
    exp = float(site.setting("auth/accessTokenExpiration") or 0)
    if site.auth_value("accessToken") and exp and exp > now:
        return True, "signed in (saved token)"
    if site.auth_value("refreshToken") and oauth2_refresh(site, auth):
        return True, "signed in (token refreshed)"
    t = auth.get("authType")
    if t == "client_credentials":
        data = _post_token(site, auth, {"grant_type": "client_credentials"})
    elif t == "password":
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
        hdr = {"X-Client-Time": stamp,
               "X-Client-Hash": hashlib.md5((stamp + "28c1fdd170a5204386cb1313c7077b34f83e4aaf4aa829ce78c231e05b0bae2c")
                                            .encode()).hexdigest()}
        data = _post_token(site, auth, {"grant_type": "password", "username": site.auth_value("pseudo"),
                                        "password": site.auth_value("password")}, hdr)
    elif t == "password_json":
        data = _post_token(site, auth, {"login": site.auth_value("pseudo"), "password": site.auth_value("password")},
                           as_json=True)
    elif t == "refresh_token":
        return False, ("this site signs in with a refresh token: paste it in the site's Login settings "
                       "(Refresh token) and test again")
    elif t in ("authorization_code", "pkce"):
        return _oauth2_code(site, auth, ask)
    else:
        return False, f"unsupported OAuth 2 type {t!r}"
    if _store_tokens(site, data):
        return True, "signed in"
    return False, str(data.get("error_description") or data.get("error") or data.get("message") or "login refused")


def _oauth2_code(site, auth, ask) -> tuple[bool, str]:
    """Authorization code / PKCE: open the browser; the code comes back to http://127.0.0.1:58923/ or
    is pasted by you (sites like Pixiv redirect to an app link: copy the 'code' from that address)."""
    key = site.auth_value("consumerKey")
    if not key:
        return False, "this login needs a client ID (consumer key) in the site's Login settings"
    verifier = base64.urlsafe_b64encode(pysecrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = pysecrets.token_urlsafe(16)
    redirect = site.fix_url(auth["redirectUrl"]) if auth.get("redirectUrl") else f"http://127.0.0.1:{OAUTH_PORT}/"
    params = {"response_type": "code", "client_id": key, "redirect_uri": redirect, "state": state}
    if auth.get("scopes"):
        params["scope"] = " ".join(auth["scopes"])
    if auth.get("authType") == "pkce":
        params.update(code_challenge=challenge, code_challenge_method="S256", client="pixiv-android")
    url = site.fix_url(auth.get("authorizationUrl") or "") + "?" + urlencode(params)
    got: dict = {}
    server = None
    if redirect.startswith(f"http://127.0.0.1:{OAUTH_PORT}"):
        server = _start_callback_server(got)
    webbrowser.open(url)
    code = None
    if server is not None:
        for _ in range(600):  # 5 minutes
            if got.get("code"):
                code = got["code"]
                break
            time.sleep(0.5)
        server.shutdown()
    if not code and ask:
        pasted = ask("Finish signing in", "Sign in in the browser window that opened. Then copy the address "
                     "you end up on (or just its code=... part) and paste it here:")
        if pasted:
            q = parse_qs(urlsplit(pasted.strip()).query) if "code=" in pasted else {}
            code = (q.get("code") or [pasted.strip()])[0]
    if not code:
        return False, "no authorization code received"
    body = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect}
    if auth.get("authType") == "pkce":
        body.update(code_verifier=verifier, include_policy="true")
    data = _post_token(site, auth, body)
    if _store_tokens(site, data):
        return True, "signed in"
    return False, str(data.get("error_description") or data.get("error") or "login refused")


def _start_callback_server(got: dict):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = parse_qs(urlsplit(self.path).query)
            if q.get("code"):
                got["code"] = q["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<html><body><h2>Packrat: you can close this tab.</h2></body></html>")

        def log_message(self, *a):
            pass
    try:
        srv = http.server.HTTPServer(("127.0.0.1", OAUTH_PORT), H)
    except OSError as e:
        log.warning("OAuth callback port %d unavailable: %s", OAUTH_PORT, e)
        return None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ---- OAuth 1 (HMAC-SHA1) -----------------------------------------------------------------------
def oauth1_header(method: str, url: str, key: str, secret: str, token: str = "", token_secret: str = "",
                  extra: dict | None = None) -> str:
    sp = urlsplit(url)
    base_url = f"{sp.scheme}://{sp.netloc}{sp.path}"
    oauth = {"oauth_consumer_key": key, "oauth_nonce": pysecrets.token_hex(16),
             "oauth_signature_method": "HMAC-SHA1", "oauth_timestamp": str(int(time.time())),
             "oauth_version": "1.0"}
    if token:
        oauth["oauth_token"] = token
    oauth.update(extra or {})
    params = dict(oauth)
    for k, vs in parse_qs(sp.query, keep_blank_values=True).items():
        params[k] = vs[0]
    enc = "&".join(f"{quote(k, safe='')}={quote(str(params[k]), safe='')}" for k in sorted(params))
    base = "&".join(quote(x, safe="") for x in (method.upper(), base_url, enc))
    sig = base64.b64encode(hmac.new(f"{quote(secret, safe='')}&{quote(token_secret, safe='')}".encode(),
                                    base.encode(), hashlib.sha1).digest()).decode()
    oauth["oauth_signature"] = sig
    return "OAuth " + ", ".join(f'{quote(k, safe="")}="{quote(v, safe="")}"' for k, v in sorted(oauth.items()))


def oauth1_login(site, auth, ask=None) -> tuple[bool, str]:
    if site.auth_value("accessToken") and site.auth_value("accessTokenSecret"):
        return True, "signed in (saved token)"
    key, secret = site.auth_value("consumerKey"), site.auth_value("consumerSecret")
    if not key or not secret:
        return False, "this login needs a consumer key and secret in the site's Login settings"
    if ask is None:
        return False, "this login needs a PIN from the site: use 'Test' in the site's Login settings window"
    url = site.fix_url(auth["temporaryCredentialsUrl"])
    r = site.request(url, "page", method="POST",
                     headers={"Authorization": oauth1_header("POST", url, key, secret, extra={"oauth_callback": "oob"})})
    q = parse_qs(r.text)
    r.close()
    tok, tsec = (q.get("oauth_token") or [""])[0], (q.get("oauth_token_secret") or [""])[0]
    if not tok:
        return False, "the site did not give a temporary token"
    webbrowser.open(site.fix_url(auth["authorizationUrl"]) + "?oauth_token=" + quote(tok))
    pin = ask("Enter the PIN", "Authorize Packrat in the browser window, then type the PIN it shows:")
    if not pin:
        return False, "no PIN entered"
    url = site.fix_url(auth["tokenCredentialsUrl"])
    r = site.request(url, "page", method="POST",
                     headers={"Authorization": oauth1_header("POST", url, key, secret, tok, tsec,
                                                             {"oauth_verifier": pin.strip()})})
    q = parse_qs(r.text)
    r.close()
    at, ats = (q.get("oauth_token") or [""])[0], (q.get("oauth_token_secret") or [""])[0]
    if not at:
        return False, "the PIN was not accepted"
    site.set_auth_value("accessToken", at)
    site.set_auth_value("accessTokenSecret", ats)
    return True, "signed in"
