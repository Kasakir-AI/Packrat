"""Sites: the site list (tick which to search, read each site's guide), adding a site, a site's login and
options, and the built-in browser for sites where you log in on their own web page."""
from __future__ import annotations

import logging

from PySide6 import QtCore, QtGui, QtWidgets

from .. import guides as G
from .. import uikit
from . import common as K
from .common import Qt

log = logging.getLogger("packrat")
HOST = Qt.ItemDataRole.UserRole


def base_domain(host: str) -> str:
    h = host.split("/")[0].split(":")[0].lower()
    parts = h.split(".")
    return ".".join(parts[-2:]) if len(parts) > 2 else h


class SitePicker(QtWidgets.QDialog):
    """selected=None: edit the usual sites (new tabs start with them). Otherwise: pick sites for one tab / dialog."""

    def __init__(self, parent, app, focus: str = "", selected=None):
        super().__init__(parent)
        self.app = app
        self.result_hosts = None
        self.setWindowTitle("Sites and logins")
        K.fit(self, 1120, 700)
        chosen = set(selected if selected is not None else (app.st.get("sites") or []))
        self.find = QtWidgets.QLineEdit()
        self.find.setPlaceholderText("Find a site...")
        self.find.setClearButtonEnabled(True)
        self.find.textChanged.connect(self._filter)
        self.list = QtWidgets.QListWidget()
        self.list.setIconSize(QtCore.QSize(20, 20))
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list.currentItemChanged.connect(lambda *_: self._show())
        self.list.itemDoubleClicked.connect(lambda *_: self.options())
        self.count = K.label("", muted=True)
        self.list.itemChanged.connect(lambda *_: self._count())
        from .help import GuideView
        self.guide = GuideView(app, on_go=self._go)
        self.msg = K.label("", muted=True, wrap=True)
        self.login_btn = K.button("Log in / settings...", self.options, ico="key")
        self.web_btn = K.button("Log in with the built-in browser...", self.web_login, ico="globe",
                                tip="Opens the site inside Packrat. Log in there like in any browser; Packrat then uses "
                                    "that login. For sites without an API key (or behind a 'checking your browser' page).")
        self.test_btn = K.button("Test this site", self.test, ico="refresh", tip="Runs one small search to see if the site answers")
        self.remove_btn = K.button("Remove", self.remove, tip="Take this site out of the list (its saved login is deleted)")
        left = K.vbox(self.find, (self.list, 1), K.hbox(self.count, None, K.button("Add another site...", self.add, ico="plus")),
                      spacing=8)
        right = K.vbox((self.guide, 1), self.msg, K.hbox(self.login_btn, self.web_btn, self.test_btn, None, self.remove_btn),
                       spacing=8)
        split = QtWidgets.QSplitter()
        lw = K.widget(left)
        lw.setMinimumWidth(380)
        split.addWidget(lw)
        split.addWidget(K.widget(right))
        split.setStretchFactor(1, 1)
        split.setSizes([450, 590])
        ok = K.button("Use these sites", self._ok, accent=True)
        ok.setDefault(True)
        hint = K.label("Tick the sites to search. Click a name to read about the site.", muted=True)
        self.setLayout(K.vbox((split, 1), K.hbox(hint, None, K.button("Cancel", self.reject), ok),
                              margins=(16, 14, 16, 12), spacing=10))
        self._fill(chosen, focus)

    def _fill(self, chosen: set, focus: str = ""):
        self.list.blockSignals(True)
        self.list.clear()
        sites = sorted(self.app.store.all_sites(), key=lambda s: (
            G.POPULAR.index(s.host) if s.host in G.POPULAR else len(G.POPULAR), s.name.lower()))
        focus_row = 0
        for s in sites:
            d = G.site_info(s.host, s.source.name)
            it = QtWidgets.QListWidgetItem(f"{d['name']}\n{d.get('what') or s.source.name}  ·  "
                                           f"{G.CONTENT_SHORT.get(d.get('content'), '')}  ·  "
                                           f"login: {G.LOGIN_SHORT.get(d.get('login'), '?').lower()}")
            it.setData(HOST, s.host)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if s.host in chosen else Qt.CheckState.Unchecked)
            ic = s.source.icon
            if ic:
                it.setIcon(_square_icon(str(ic), self.list.iconSize().width(), self.list.devicePixelRatioF()))
            self.list.addItem(it)
            if s.host == focus:
                focus_row = self.list.count() - 1
        self.list.blockSignals(False)
        self.list.setCurrentRow(focus_row)
        self._count()
        self._show()

    def hosts(self) -> list[str]:
        return [self.list.item(i).data(HOST) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.CheckState.Checked]

    def _count(self):
        n = len(self.hosts())
        self.count.setText(f"{n} site{'s' if n != 1 else ''} ticked")

    def _filter(self, text):
        t = text.lower().strip()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(t) and t not in it.text().lower() and t not in it.data(HOST))

    def site(self):
        it = self.list.currentItem()
        return self.app.store.get(it.data(HOST)) if it else None

    def _show(self):
        s = self.site()
        self.msg.setText("")
        for b in (self.login_btn, self.web_btn, self.test_btn, self.remove_btn):
            b.setEnabled(s is not None)
        if s is None:
            self.guide.set_text("# No site selected")
            return
        try:
            saved = s.has_credentials()
        except Exception:  # noqa: BLE001
            saved = False
        self.guide.set_text(G.site_text(s.host, s.source.name, login_saved=saved))

    def _go(self, target: str):
        if target.startswith("site:"):
            host = target[5:]
            for i in range(self.list.count()):
                if self.list.item(i).data(HOST) == host:
                    self.list.setCurrentRow(i)
            return
        if target.startswith(("url:", "help:")):
            self.app.go(target)
            return
        self._ok()
        self.app.go(target)

    def _say(self, text, role=""):
        self.msg.setText(text)
        K.restyle(self.msg, role=role, muted=not role)

    def options(self):
        s = self.site()
        if s is not None:
            SiteOptions(self, self.app, s).exec()
            self._show()

    def web_login(self):
        s = self.site()
        if s is not None:
            if open_login_browser(self, self.app, s):
                self._show()

    def test(self):
        s = self.site()
        if s is None:
            return
        self._say(f"Asking {s.name}...")

        def work():
            pr = s.search("", 1, 5)
            if pr.error:
                raise ValueError(pr.error)
            return len(pr.images)

        def done(n):
            self._say(f"{s.name} answered: {n} picture(s) in a test search. It works." if n else
                      f"{s.name} answered, but sent no pictures. It may need a login, or an empty search is not allowed there.",
                      "ok" if n else "")
        self.app.worker.submit(work, on_done=done, on_error=lambda e: self._say(
            f"{s.name}: " + uikit.friendly(str(e), s.name), "error"))

    def add(self):
        dlg = AddSiteDialog(self, self.app)
        if dlg.exec() and dlg.site is not None:
            chosen = set(self.hosts()) | {dlg.site.host}
            self._fill(chosen, dlg.site.host)

    def remove(self):
        s = self.site()
        if s is None or not K.ask(self, "Remove a site", f"Remove {s.name} from the list?\n\nIts saved login is deleted. "
                                  "Pictures you downloaded stay.", "Remove", "Cancel", default_yes=False):
            return
        chosen = set(self.hosts()) - {s.host}
        self.app.store.remove_site(s.host)
        self._fill(chosen)

    def _ok(self):
        hosts = self.hosts()
        if not hosts:
            self._say("Tick at least one site.", "error")
            return
        self.result_hosts = hosts
        self.app.st.set("sites", hosts)
        self.accept()


class AddSiteDialog(QtWidgets.QDialog):
    AUTO = "Find out automatically"

    def __init__(self, parent, app, host: str = ""):
        super().__init__(parent)
        self.app, self.site = app, None
        self.setWindowTitle("Add another site")
        self.setMinimumWidth(560)
        self.host = QtWidgets.QLineEdit(host)
        self.host.setPlaceholderText("for example konachan.net")
        self.kind = QtWidgets.QComboBox()
        self.kind.addItems([self.AUTO] + sorted(app.store.types))
        self.msg = K.label("", muted=True, wrap=True)
        self.ok = K.button("Add", self._add, accent=True)
        self.ok.setDefault(True)
        form = QtWidgets.QFormLayout()
        form.addRow("Address", self.host)
        form.addRow("Software", self.kind)
        self.setLayout(K.vbox(
            K.label("Add a site that isn't in the list", role="heading"),
            K.label("Many image sites run the same software as the sites in the list (Danbooru, Gelbooru, Moebooru, "
                    "Shimmie...). Type the site's address and Packrat finds out which one it uses: it tries each kind "
                    "once, or finds a matching add-on in Grabber's online list.", muted=True, wrap=True),
            form, self.msg, K.hbox(None, K.button("Cancel", self.reject), self.ok), margins=(18, 16, 18, 14), spacing=12))

    def _say(self, text, error=False):
        self.msg.setText(text)
        K.restyle(self.msg, role="error" if error else "", muted=not error)

    def _add(self):
        host = self.host.text().strip()
        if not host:
            self._say("Type the site's address first.", True)
            return
        t = self.kind.currentText()
        auto = t == self.AUTO
        self._say("Checking the site (this can take a minute)..." if auto else "Adding...")
        self.ok.setEnabled(False)
        bare = host.split("://")[-1].rstrip("/")
        app = self.app

        def work():
            if auto:
                guess = app.store.guess_type(bare)
                if guess is None:
                    reg = uikit._registry_match(host)
                    if reg is None:
                        raise ValueError("Packrat couldn't recognise this site. Check the address (open it in your "
                                         "browser), or choose its software in the list if you know it.")
                    return ("registry", reg)
                return ("site", app.store.add_site(host, guess))
            return ("site", app.store.add_site(host, t))

        def done(res):
            kind, val = res
            if kind == "registry":
                self.ok.setEnabled(True)
                if K.ask(self, "Add a site", f"The add-on '{val['name']}' from Grabber's online list supports this site. "
                         "Install it?", "Install", "Cancel"):
                    self.ok.setEnabled(False)
                    app.worker.submit(uikit._install_and_add, app, val, host, on_done=self._finish, on_error=self._err)
                return
            self._finish(val)
        app.worker.submit(work, on_done=done, on_error=self._err)

    def _finish(self, site):
        self.site = site
        self.accept()

    def _err(self, e):
        self.ok.setEnabled(True)
        self._say(uikit.friendly(str(e)), True)


class SiteOptions(QtWidgets.QDialog):
    """A site's login and options. Only the fields the chosen login method uses are shown."""
    THROTTLES = (("page", "Between search pages"), ("details", "Between picture details"), ("image", "Between pictures"),
                 ("thumbnail", "Between previews"), ("retry", "After an error"))

    def __init__(self, parent, app, site):
        super().__init__(parent)
        self.app, self.site = app, site
        d = G.site_info(site.host, site.source.name)
        self.setWindowTitle(f"{d['name']} - login and settings")
        self.setMinimumWidth(620)
        self.auths = dict(site.source.meta.get("auth", {}) or {})
        self.fields: dict[str, QtWidgets.QLineEdit] = {}
        self.rows: dict[str, tuple] = {}
        tabs = QtWidgets.QTabWidget()

        # ---- login
        lg = K.vbox(margins=(14, 14, 14, 10), spacing=10)
        self.test_msg = K.label("Passwords and keys are kept in the Windows Credential Manager, never in a file.",
                                muted=True, wrap=True)
        if not self.auths:
            lg.addWidget(K.label("This site has no login for programs. If it shows more when you are logged in on its "
                                 "web page, use 'Log in with the built-in browser'.", wrap=True))
        else:
            lg.addWidget(K.label(f"Login: {G.LOGIN_LABEL.get(d.get('login'), '')}", role="heading"))
            steps = d.get("login_steps") or ["Fill in the fields below, then press Test login."]
            lg.addWidget(K.label("\n".join(f"{i + 1}. {t}".replace("**", "").replace("`", "") for i, t in enumerate(
                _plain_links(s) for s in steps)), wrap=True, selectable=True))

            def rank(k):
                a = self.auths[k]
                if "apiKey" in uikit.auth_fields(a):
                    return 0
                return 1 if a.get("authType") in ("pkce", "authorization_code") else 2
            self.auths = {k: self.auths[k] for k in sorted(self.auths, key=rank)}
            cur = (site.login_type() if site.has_credentials() else "") or next(iter(self.auths))
            self.method = QtWidgets.QComboBox()
            for k, a in self.auths.items():
                self.method.addItem(uikit.auth_label(k, a), k)
            self.method.setCurrentIndex(max(0, list(self.auths).index(cur) if cur in self.auths else 0))
            self.method.currentIndexChanged.connect(lambda *_: self._show_fields())
            self.form = QtWidgets.QFormLayout()
            self.form.addRow("How to log in", self.method)
            seen = []
            for a in self.auths.values():
                for fid in uikit.auth_fields(a):
                    if fid not in seen:
                        seen.append(fid)
            for fid in seen:
                ed = QtWidgets.QLineEdit(site.auth_value(fid))
                if fid in uikit.SECRET_IDS:
                    ed.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
                lab = QtWidgets.QLabel(uikit.FIELD_LABELS.get(fid, fid))
                self.form.addRow(lab, ed)
                self.fields[fid] = ed
                self.rows[fid] = (lab, ed)
            lg.addLayout(self.form)
        lg.addWidget(self.test_msg)
        row = K.hbox()
        if self.auths:
            row.addWidget(K.button("Test login", self.test, accent=True))
            row.addWidget(K.button("Forget this login", self.clear))
        row.addWidget(K.button("Log in with the built-in browser...", self.web_login, ico="globe"))
        row.addWidget(K.button("Open the site", lambda: uikit.open_url(site.base_url)))
        row.addStretch(1)
        lg.addLayout(row)
        lg.addStretch(1)
        tabs.addTab(K.widget(lg), "Login")

        # ---- speed
        sp = QtWidgets.QFormLayout()
        sp.setContentsMargins(14, 14, 14, 10)
        sp.addRow(K.label("How long Packrat waits between requests to this site, in seconds. The defaults are polite; "
                          "lowering them can get you blocked.", muted=True, wrap=True))
        self.speed: dict[str, QtWidgets.QDoubleSpinBox] = {}
        for k, lab in self.THROTTLES:
            sb = QtWidgets.QDoubleSpinBox()
            sb.setRange(0, 3600)
            sb.setDecimals(1)
            sb.setSingleStep(0.5)
            sb.setSuffix(" s")
            try:
                sb.setValue(float(site.throttle(k)))
            except Exception:  # noqa: BLE001
                sb.setValue(1.0)
            sp.addRow(lab, sb)
            self.speed[k] = sb
        self.simul = QtWidgets.QSpinBox()
        self.simul.setRange(1, 16)
        self.simul.setValue(int(site.max_simultaneous()))
        sp.addRow("Downloads at the same time", self.simul)
        tabs.addTab(K.widget(sp), "Speed limits")

        # ---- other
        ot = QtWidgets.QFormLayout()
        ot.setContentsMargins(14, 14, 14, 10)
        self.name = QtWidgets.QLineEdit(site.name)
        self.ssl = QtWidgets.QCheckBox("Use a secure connection (https)")
        self.ssl.setChecked(bool(site.ssl))
        ot.addRow("Name shown in Packrat", self.name)
        ot.addRow("", self.ssl)
        ot.addRow(K.label(f"Software: {site.source.name}.   Address: {site.base_url}", muted=True, selectable=True))
        tabs.addTab(K.widget(ot), "Other")

        save = K.button("Save", self.save, accent=True)
        save.setDefault(True)
        self.setLayout(K.vbox((tabs, 1), K.hbox(None, K.button("Cancel", self.reject), save), margins=(14, 12, 14, 12)))
        self._initial_speed = {k: sb.value() for k, sb in self.speed.items()}
        self._initial_simul = self.simul.value()
        if self.auths:
            self._show_fields()

    def _method(self) -> str:
        return self.method.currentData()

    def _show_fields(self):
        a = self.auths.get(self._method(), {})
        defaults = self.site.source.site_defaults(self.site.host)
        want = [f for f in uikit.auth_fields(a) if not (f.startswith("consumer") and defaults.get("auth/" + f))]
        for fid, (lab, ed) in self.rows.items():
            lab.setVisible(fid in want)
            ed.setVisible(fid in want)

    def _apply(self):
        s = self.site
        for fid, ed in self.fields.items():
            val = ed.text().strip()
            if val != s.auth_value(fid):
                s.set_auth_value(fid, val)
        for k, sb in self.speed.items():
            if abs(sb.value() - self._initial_speed[k]) > 1e-6:
                s.set_setting(f"download/throttle_{k}", sb.value())
        if self.simul.value() != self._initial_simul:
            s.set_setting("download/simultaneous", self.simul.value())
        if self.name.text().strip() and self.name.text().strip() != s.name:
            s.set_setting("name", self.name.text().strip())
        if self.ssl.isChecked() != bool(s.ssl):
            s.set_setting("ssl", self.ssl.isChecked())
        if self.auths:
            key = self._method()
            filled = lambda a: any(self.fields[f].text().strip() for f in uikit.auth_fields(a) if f in self.fields)  # noqa: E731
            if not filled(self.auths.get(key, {})):
                key = next((k for k, a in self.auths.items() if filled(a)), key)
            s.set_setting("login/type", key)
        s.reset_login()

    def _say(self, text, role=""):
        self.test_msg.setText(text)
        K.restyle(self.test_msg, role=role, muted=not role)

    def test(self):
        self._apply()
        self._say("Testing the login...")

        def done(res):
            ok, msg = res
            self._say(("The login works. " if ok else "The login was refused: ") + uikit.friendly(str(msg or ""), self.site.name)
                      if not ok else "The login works.", "ok" if ok else "error")
        self.app.worker.submit(self.site.login, on_done=done,
                               on_error=lambda e: self._say("The test failed: " + uikit.friendly(str(e), self.site.name), "error"))

    def clear(self):
        for _fid, ed in self.fields.items():
            ed.clear()
        from ..grab import secrets
        try:
            secrets.forget_scope(self.site.host, ["session_cookies"])
            self.site.cookies.clear()
            headers = dict(self.app.store.user_settings(self.site.host).get("headers") or {})
            if headers.pop("User-Agent", None) is not None:      # the browser name a web login had set
                self.app.store.set_user_setting(self.site.host, "headers", headers)
        except Exception:  # noqa: BLE001
            pass
        self._apply()
        self._say("This login was forgotten.")

    def web_login(self):
        self._apply()
        if open_login_browser(self, self.app, self.site):
            self._say("The login from the built-in browser is saved.", "ok")

    def save(self):
        self._apply()
        self.accept()


def _plain_links(text: str) -> str:
    import re
    return re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)


# ---- built-in browser -------------------------------------------------------------------------------------
def open_login_browser(parent, app, site) -> bool:
    """Open the site's web page inside Packrat so the person can log in there. True when a login was kept."""
    try:
        from PySide6 import QtWebEngineCore, QtWebEngineWidgets  # noqa: F401
    except Exception as e:  # noqa: BLE001
        K.warn(parent, "Built-in browser", "The built-in browser is not available in this installation "
               f"({e}).\n\nUse an API key or user name and password instead (Log in / settings).")
        return False
    dlg = LoginBrowser(parent, app, site)
    dlg.exec()
    return dlg.kept


class LoginBrowser(QtWidgets.QDialog):
    def __init__(self, parent, app, site):
        super().__init__(parent)
        from PySide6 import QtWebEngineCore, QtWebEngineWidgets

        from .. import paths
        self.app, self.site, self.kept = app, site, False
        self.cookies: dict = {}
        self.setWindowTitle(f"Log in to {site.name}")
        K.fit(self, 1100, 780)
        store = paths.cache_dir("browser") / "".join(ch if ch.isalnum() else "_" for ch in site.host)
        self.profile = QtWebEngineCore.QWebEngineProfile("packrat-" + store.name)   # no parent: it must outlive its page
        self.profile.setPersistentStoragePath(str(store))
        self.profile.setCachePath(str(store / "cache"))
        self.profile.setPersistentCookiesPolicy(QtWebEngineCore.QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies)
        self.profile.cookieStore().cookieAdded.connect(self._cookie)
        self.profile.cookieStore().cookieRemoved.connect(self._cookie_gone)
        self.profile.cookieStore().loadAllCookies()
        self.web = QtWebEngineWidgets.QWebEngineView()
        self.page = QtWebEngineCore.QWebEnginePage(self.profile, self.web)
        self.web.setPage(self.page)
        self.addr = QtWidgets.QLineEdit()
        self.addr.returnPressed.connect(lambda: self.web.setUrl(QtCore.QUrl.fromUserInput(self.addr.text())))
        self.web.urlChanged.connect(lambda u: self.addr.setText(u.toString()))
        self.note = K.label(f"Log in to {site.name} below, like in any browser. When you see that you are logged in, click "
                            "'I am logged in'. Packrat keeps only this site's login cookies, in the Windows Credential Manager.",
                            wrap=True)
        keep = K.button("I am logged in - use this login", self._keep, accent=True)
        bar = K.hbox(K.button("", self.web.back, flat=True, ico="left"), K.button("", self.web.forward, flat=True, ico="right"),
                     K.button("", self.web.reload, flat=True, ico="refresh"), (self.addr, 1))
        self.setLayout(K.vbox(self.note, bar, (self.web, 1), K.hbox(None, K.button("Cancel", self.reject), keep),
                              margins=(12, 10, 12, 10), spacing=8))
        self.web.setUrl(QtCore.QUrl(site.base_url))

    def _match(self, c) -> bool:
        dom = c.domain().lstrip(".").lower()
        base = base_domain(self.site.host)
        return dom == base or dom.endswith("." + base)

    def _cookie(self, c):
        if self._match(c):
            self.cookies[(bytes(c.name()).decode("utf-8", "replace"), c.domain(), c.path())] = bytes(c.value()).decode(
                "utf-8", "replace")

    def _cookie_gone(self, c):
        self.cookies.pop((bytes(c.name()).decode("utf-8", "replace"), c.domain(), c.path()), None)

    def _keep(self):
        if not self.cookies:
            K.info(self, "Built-in browser", "The site has not set any cookies yet. Log in first, then click again.")
            return
        s = self.site
        for (name, dom, path), value in self.cookies.items():
            s.cookies.set(name, value, domain=dom.lstrip(".") if not dom.startswith(".") else dom, path=path or "/")
        try:
            s.save_session_cookies()
            if not s.is_e621_family():     # cookies from a 'checking your browser' page only work with the same browser name
                headers = dict(self.app.store.user_settings(s.host).get("headers") or {})
                headers["User-Agent"] = self.profile.httpUserAgent()
                self.app.store.set_user_setting(s.host, "headers", headers)
            s.reset_login()
        except Exception as e:  # noqa: BLE001
            K.warn(self, "Built-in browser", f"The login could not be saved: {e}")
            return
        log.info("%s: kept %d login cookie(s) from the built-in browser", s.name, len(self.cookies))
        self.kept = True
        self.accept()

    def done(self, r):
        page, profile = self.page, self.profile
        self.web.setPage(None)
        page.deleteLater()
        QtCore.QTimer.singleShot(500, profile.deleteLater)      # after the page is gone
        super().done(r)


def _square_icon(path: str, size: int, dpr: float = 1.0) -> QtGui.QIcon:
    """Site icons come in all sizes and shapes; Qt sizes the icon column by each picture, so names would not
    line up. Draw every icon centred on a square of the same size (sharp on high-DPI screens too)."""
    ico = QtGui.QIcon(path)
    sizes = ico.availableSizes()
    src = ico.pixmap(max(sizes, key=lambda z: z.width() * z.height()) if sizes else QtCore.QSize(64, 64))
    px = max(1, round(size * dpr))
    canvas = QtGui.QPixmap(px, px)
    canvas.fill(Qt.GlobalColor.transparent)
    if not src.isNull():
        src = src.scaled(px, px, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        src.setDevicePixelRatio(1.0)
        p = QtGui.QPainter(canvas)
        p.drawPixmap((px - src.width()) // 2, (px - src.height()) // 2, src)
        p.end()
    canvas.setDevicePixelRatio(dpr)
    return QtGui.QIcon(canvas)
