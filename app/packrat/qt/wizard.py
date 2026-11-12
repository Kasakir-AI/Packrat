"""'Set up a site': a step-by-step window for logging in to a popular site and getting started with it.

1. Pick a site (the popular ones first, with what each needs).
2. Log in: what an account changes there, where to click on the site (a button opens the right page), the
   fields to fill in, a Test button; or use the login of your own browser, or the built-in browser.
3. Ready: what to show from the site (all ratings, safe for work only, no explicit) and a first search.
"""
from __future__ import annotations

import re

from PySide6 import QtCore, QtWidgets

from .. import guides as G
from .. import uikit
from . import common as K
from .common import Qt

SHOW = (("Everything the site shows", ""), ("Safe for work only", "rating:sfw"), ("No explicit pictures", "-rating:explicit"))


def _html(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    return re.sub(r"`(.+?)`", r"<code>\1</code>", text)


class SiteWizard(QtWidgets.QDialog):
    def __init__(self, parent, app, host: str = ""):
        super().__init__(parent)
        self.app, self.site = app, None
        self.setWindowTitle("Set up a site")
        self.setMinimumSize(700, 540)
        self.fields: dict[str, QtWidgets.QLineEdit] = {}
        self.stack = QtWidgets.QStackedWidget()
        self.stack.addWidget(self._pick_page())
        self.login_page = QtWidgets.QWidget()
        self.ready_page = QtWidgets.QWidget()
        self.stack.addWidget(self.login_page)
        self.stack.addWidget(self.ready_page)
        self.back = K.button("Back", lambda: self._go(self.stack.currentIndex() - 1))
        self.next = K.button("Next", self._next, accent=True)
        self.next.setDefault(True)
        self.setLayout(K.vbox((self.stack, 1), K.hbox(self.back, None, K.button("Cancel", self.reject), self.next),
                              margins=(18, 16, 18, 14), spacing=12))
        if host and app.store.get(host) is not None:
            self._choose(host)
        self._update_buttons()

    # ---- 1. which site --------------------------------------------------------------------------
    def _pick_page(self):
        from .sites import _square_icon
        self.list = QtWidgets.QListWidget()
        self.list.setIconSize(QtCore.QSize(20, 20))
        hosts = [h for h in G.SETUP if self.app.store.get(h) is not None]
        hosts += sorted(s.host for s in self.app.store.all_sites()
                        if s.host not in hosts and s.source.meta.get("auth"))
        for h in hosts:
            s = self.app.store.get(h)
            d = G.site_info(h, s.source.name)
            it = QtWidgets.QListWidgetItem(f"{d['name']}\n{d.get('what') or s.source.name}  ·  login: "
                                           f"{G.LOGIN_LABEL.get(d.get('login'), 'optional').lower()}"
                                           + ("  ·  logged in" if s.has_credentials() else ""))
            it.setData(Qt.ItemDataRole.UserRole, h)
            if s.source.icon:
                it.setIcon(_square_icon(str(s.source.icon), 20, self.list.devicePixelRatioF()))
            self.list.addItem(it)
        self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(lambda *_: self._next())
        return K.widget(K.vbox(K.label("Which site do you want to set up?", role="heading"),
                               K.label("Packrat shows what the site needs, opens the right page on it and checks that "
                                       "the login works. The popular sites are at the top.", muted=True, wrap=True),
                               (self.list, 1), spacing=10))

    def _choose(self, host: str):
        self.site = self.app.store.get(host)
        self._build_login()
        self._build_ready()
        self._go(1)

    # ---- 2. log in ------------------------------------------------------------------------------
    def _auths(self) -> dict:
        auths = dict(self.site.source.meta.get("auth", {}) or {})
        how = G.setup_info(self.site.host).get("how", "")

        def rank(k):
            fields = uikit.auth_fields(auths[k])
            if how == "password" and "password" in fields:
                return 0
            return 0 if "apiKey" in fields else (1 if "password" in fields else 2)
        return {k: auths[k] for k in sorted(auths, key=rank)}

    def _build_login(self):
        s = self.site
        d, info = G.site_info(s.host, s.source.name), G.setup_info(s.host)
        how = info.get("how") or ("key" if s.source.meta.get("auth") else "none")
        self.fields, self.browser = {}, None
        lay = K.vbox(spacing=10)
        lay.addWidget(K.label(f"Log in to {d['name']}", role="heading"))
        lay.addWidget(K.label(_html(info.get("why") or f"Login on {d['name']}: "
                                    f"{G.LOGIN_LABEL.get(d.get('login'), 'optional').lower()}."), wrap=True))
        self.auths = self._auths()
        if how == "none" and not self.auths:
            lay.addWidget(K.label(f"Nothing to set up: {d['name']} works without an account. Press Next.", wrap=True))
        else:
            steps = d.get("login_steps") or G.SETUP_STEPS.get(how) or []
            if steps:
                lay.addWidget(K.label("<ol style='margin-left:-20px'>" + "".join(f"<li>{_html(t)}</li>" for t in steps)
                                      + "</ol>", wrap=True, selectable=True))
            row = K.hbox()
            if info.get("key_url"):
                text = "Open the key page" if how == "key" else "Open the site"
                row.addWidget(K.button(text, lambda: uikit.open_url(info["key_url"]), ico="globe",
                                       tip=info["key_url"]))
            row.addWidget(browser_login_button(self, d["name"], self._from_browser))
            row.addWidget(K.button("Use the built-in browser...", self._web_login, flat=True,
                                   tip="Opens the site inside Packrat: log in there like in any browser (also through "
                                       "'checking your browser' pages); Packrat keeps that login."))
            row.addStretch(1)
            lay.addLayout(row)
            if self.auths and how != "browser":
                form = QtWidgets.QFormLayout()
                self.method = QtWidgets.QComboBox()
                for k, a in self.auths.items():
                    self.method.addItem(uikit.auth_label(k, a), k)
                cur = s.login_type() if s.has_credentials() else ""
                if cur in self.auths:
                    self.method.setCurrentIndex(list(self.auths).index(cur))
                self.method.setVisible(len(self.auths) > 1)
                if len(self.auths) > 1:
                    form.addRow("How to log in", self.method)
                self.rows = {}
                for a in self.auths.values():
                    for fid in uikit.auth_fields(a):
                        if fid in self.fields:
                            continue
                        ed = QtWidgets.QLineEdit(s.auth_value(fid))
                        if fid in uikit.SECRET_IDS:
                            ed.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
                        lab = QtWidgets.QLabel(uikit.FIELD_LABELS.get(fid, fid))
                        form.addRow(lab, ed)
                        self.fields[fid] = ed
                        self.rows[fid] = (lab, ed)
                self.method.currentIndexChanged.connect(lambda *_: self._show_fields())
                self._show_fields()
                lay.addLayout(form)
                lay.addWidget(K.button("Test the login", self._test, accent=True))
        self.msg = K.label("Passwords and keys are kept in the Windows Credential Manager (Mac: the Keychain), never "
                           "in a file. You can skip this and log in later.", muted=True, wrap=True)
        lay.addWidget(self.msg)
        lay.addStretch(1)
        _replace(self.login_page, lay)

    def _show_fields(self):
        a = self.auths.get(self.method.currentData(), {})
        defaults = self.site.source.site_defaults(self.site.host)
        want = [f for f in uikit.auth_fields(a) if not (f.startswith("consumer") and defaults.get("auth/" + f))]
        for fid, (lab, ed) in self.rows.items():
            lab.setVisible(fid in want)
            ed.setVisible(fid in want)

    def _apply(self):
        s = self.site
        for fid, ed in self.fields.items():
            if ed.text().strip() != s.auth_value(fid):
                s.set_auth_value(fid, ed.text().strip())
        if self.fields:
            s.set_setting("login/type", self.method.currentData())
            name = self.fields.get("pseudo")
            if name is not None and name.text().strip() and s.is_e621_family() and not self.app.st.get("username"):
                self.app.st.set("username", name.text().strip())    # e621 wants it in every request too
        s.reset_login()

    def _say(self, text, role=""):
        self.msg.setText(text)
        K.restyle(self.msg, role=role, muted=not role)

    def _test(self):
        self._apply()
        self._say("Testing the login...")

        def done(res):
            ok, why = res
            self._say("The login works." if ok else "The login was refused: " + uikit.friendly(str(why or ""),
                                                                                              self.site.name),
                      "ok" if ok else "error")
        self.app.worker.submit(self.site.login, on_done=done,
                               on_error=lambda e: self._say("The test failed: " + uikit.friendly(str(e), self.site.name),
                                                            "error"))

    def _from_browser(self, b):
        from ..grab import browser_login as BL
        self.browser = b
        self._say(f"Reading your {self.browser.title} login for {self.site.name}... (macOS may ask whether Packrat may "
                  "use the browser's key: choose Always Allow)")

        def done(n):
            if not n:
                self._say(f"{self.browser.title} has no login for {self.site.name}. Log in there first, then try again.",
                          "error")
                return
            self._say(f"Packrat now uses your {self.browser.title} login ({n} cookies). Checking it...", "ok")
            self.app.worker.submit(self.site.check, on_done=lambda ok: self._say(
                f"Packrat now uses your {self.browser.title} login." if ok is not False else
                f"The {self.browser.title} login was imported, but {self.site.name} didn't accept it. The site may tie "
                "its login to the browser; use the built-in browser instead.", "ok" if ok is not False else "error"))
        self.app.worker.submit(lambda: BL.import_login(self.site, self.browser), on_done=done,
                               on_error=lambda e: self._say(str(e), "error"))

    def _web_login(self):
        from .sites import open_login_browser
        self._apply()
        if open_login_browser(self, self.app, self.site):
            self._say("The login from the built-in browser is saved.", "ok")

    # ---- 3. ready -------------------------------------------------------------------------------
    def _build_ready(self):
        from ..grab.searchwords import RATING_WORDS
        s = self.site
        d = G.site_info(s.host, s.source.name)
        lay = K.vbox(spacing=10)
        lay.addWidget(K.label(f"{d['name']} is ready", role="heading"))
        lay.addWidget(K.label("What should Packrat show from this site? (You can change it any time in the Rating menu "
                              "of a search, or here again.)", wrap=True))
        self.show_box = QtWidgets.QComboBox()
        for label, word in SHOW:
            self.show_box.addItem(label, word)
        added = str(s.setting("search/add", "") or "").split()
        self._other_words = [w for w in added if w.lower().lstrip("-").split(":", 1)[-1] not in RATING_WORDS
                             or not w.lower().lstrip("-").startswith("rating:")]
        cur = " ".join(w for w in added if w not in self._other_words)
        self.show_box.setCurrentIndex(max(0, self.show_box.findData(cur)))
        lay.addWidget(self.show_box)
        self.search_now = QtWidgets.QCheckBox(f"Open a search on {d['name']} now")
        self.search_now.setChecked(True)
        lay.addWidget(self.search_now)
        tips = (d.get("tips") or []) + ["The **Sort** and **Rating** menus next to the search box work on every "
                                        "site: Packrat writes them the way each site wants."]
        if tips:
            lay.addWidget(K.label("Good to know", role="heading"))
            lay.addWidget(K.label("<ul style='margin-left:-20px'>" + "".join(f"<li>{_html(t)}</li>" for t in tips)
                                  + "</ul>", wrap=True))
        lay.addStretch(1)
        _replace(self.ready_page, lay)

    def _finish(self):
        s = self.site
        word = self.show_box.currentData()
        s.set_setting("search/add", " ".join(self._other_words + ([word] if word else [])))
        if self.search_now.isChecked():
            self.app.new_search_tab("", sites=[s.host], run=True)
        self.accept()

    # ---- moving between the steps ---------------------------------------------------------------------
    def _next(self):
        i = self.stack.currentIndex()
        if i == 0:
            it = self.list.currentItem()
            if it is not None:
                self._choose(it.data(Qt.ItemDataRole.UserRole))
        elif i == 1:
            if self.fields:
                self._apply()
            self._go(2)
        else:
            self._finish()

    def _go(self, i: int):
        self.stack.setCurrentIndex(max(0, min(2, i)))
        self._update_buttons()

    def _update_buttons(self):
        i = self.stack.currentIndex()
        self.back.setEnabled(i > 0)
        self.next.setText("Finish" if i == 2 else "Next")


def browser_login_button(parent, site_name: str, use) -> QtWidgets.QPushButton:
    """'Use my login from Chrome' (or a menu of the browsers on this computer, the default one first). `use` gets
    the chosen grab.browser_login.Browser."""
    from ..grab import browser_login as BL
    found = BL.installed_browsers() if BL.env_supported() else []
    tip = (f"If you are logged in to {site_name} in your browser, Packrat uses that login: it reads this site's "
           "cookies only, only when you press this.")
    if not found:
        b = K.button("Use my login from my browser", None, ico="key")
        b.setEnabled(False)
        b.setToolTip("No browser Packrat can read was found on this computer.")
        return b
    if len(found) == 1:
        b = found[0]
        btn = K.button(f"Use my login from {b.title}", lambda: use(b), ico="key", tip=tip if b.supported else b.why)
        btn.setEnabled(b.supported)
        return btn
    m = QtWidgets.QMenu(parent)
    for i, b in enumerate(found):
        act = m.addAction(b.title + ("  (your default browser)" if i == 0 and b.key == (BL.default_browser() or b).key
                                     else ""), lambda b=b: use(b))
        act.setEnabled(b.supported)
        if not b.supported:
            act.setText(f"{b.title}  (not possible on Windows)")
            act.setToolTip(b.why)
    m.setToolTipsVisible(True)
    return K.button("Use my login from a browser", ico="key", tip=tip, menu=m)


def _replace(page: QtWidgets.QWidget, lay):
    old = page.layout()
    if old is not None:
        QtWidgets.QWidget().setLayout(old)       # detach the old layout (and its widgets) from the page
    page.setLayout(lay)
