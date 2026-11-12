"""The Site archive page: a complete copy of e6ai or e621 (the old e6ai Archiver).

The form is drawn from control_panel.OPTIONS / MODES, the same descriptions the command line and the classic
window use, so every option is here and they can never drift apart. The run itself is a child process
(control_panel.CliRunner); progress comes back as events and is summed up by gui.GuiModel."""
from __future__ import annotations

import logging
import os

from PySide6 import QtCore, QtGui, QtWidgets

from .. import control_panel as cp
from .. import uikit
from ..gui import GuiModel
from . import common as K
from .common import C, Qt

log = logging.getLogger("packrat")
TRUE = ("1", "true", "yes", "on")


def _on(v) -> bool:
    return v is True or str(v).lower() in TRUE


class ArchivePage(QtWidgets.QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.path = cp.settings_path()
        self.state, warnings = cp.load_settings(self.path)
        self.runner = cp.CliRunner()
        self.model = GuiModel()
        self.paused = False
        self.last_exit = None
        self.log_path = ""
        self.get: dict = {}       # key -> function returning the widget's value
        self.rows: dict = {}      # key -> (label widget, field widget)
        self._loading = True

        self.mode = QtWidgets.QComboBox()
        for m in cp.MODES:
            self.mode.addItem(m.label, m.key)
        cur = self.state.get("mode") or cp.DEFAULT_MODE
        self.mode.setCurrentIndex(max(0, [m.key for m in cp.MODES].index(cur) if cur in cp.MODE else 0))
        self.mode.currentIndexChanged.connect(lambda *_: self._changed())
        self.mode.setMinimumWidth(380)
        self.mode_help = K.label("", muted=True, wrap=True)
        self.start_btn = K.button("Start", self.start, accent=True, ico="play")
        self.start_btn.setMinimumWidth(120)
        self.pause_btn = K.button("Pause", self.pause, ico="pause")
        self.stop_btn = K.button("Stop", self.stop, ico="stop")
        head = K.vbox(K.label("Site archive", role="title"),
                      K.label("A complete copy of e6ai or e621: every post, file and its details, checked file by file. It can "
                              "take days and hundreds of GB; stop whenever you like, Start continues.", muted=True, wrap=True),
                      spacing=2)
        top = K.hbox(K.label("What to do"), self.mode, None, self.pause_btn, self.stop_btn, self.start_btn)

        self.tabs = QtWidgets.QTabWidget()
        for tab in cp.TABS:
            self.tabs.addTab(self._build_tab(tab), tab.replace("&", "&&"))
        # progress + log
        self.phase = K.label("Ready.", role="heading")
        self.bar = QtWidgets.QProgressBar()
        self.bar.setRange(0, 1000)
        self.overall = K.label("", muted=True)
        self.speed = K.label("", muted=True)
        self.file = K.label("", muted=True)
        self.file.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        self.counts = K.label("", muted=True)
        self.msg = K.label("", wrap=True)
        self.logview = QtWidgets.QPlainTextEdit()
        self.logview.setReadOnly(True)
        self.logview.setMaximumBlockCount(3000)
        f = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)
        f.setPointSize(9)
        self.logview.setFont(f)
        tools = K.hbox(K.button("Open the archive folder", self.open_folder, ico="folder"),
                       K.button("Open the report", self.open_report, tip="The completeness report, after a 'report' or full run"),
                       K.button("Copy the command", self.copy_command, tip="The command line that Start runs, for a console "
                                                                           "or Task Scheduler"),
                       K.button("Import settings from an older copy...", self.import_previous,
                                tip="Use the settings and the archive folder of an e6ai Archiver / older Packrat folder. "
                                    "Nothing there is changed or moved, and nothing is downloaded again."),
                       K.button("Help", lambda: app.open_help("archive"), ico="help"), None)
        prog = K.widget(K.vbox(self.phase, self.bar, K.hbox(self.overall, None, self.speed), self.file, self.counts,
                               margins=(14, 12, 14, 12), spacing=5), "card")
        run_tab = K.widget(K.vbox(prog, (self.logview, 1), margins=(0, 10, 0, 0), spacing=10))
        self.tabs.addTab(run_tab, "Progress and log")
        self.setLayout(K.vbox(head, top, self.mode_help, (self.tabs, 1), self.msg, tools, margins=(18, 14, 18, 12), spacing=9))

        self.save_timer = QtCore.QTimer(self, singleShot=True, interval=900, timeout=self._save)
        self.poll = QtCore.QTimer(self, interval=300, timeout=self._poll)
        self.poll.start()
        self._loading = False
        for w in warnings:
            self._log(w, logging.WARNING)
        self._changed(save=False)
        self._buttons()
        if not self.path.exists():
            try:
                found = cp.find_previous_installs()
            except Exception:  # noqa: BLE001
                found = []
            if found:
                self._say(f"An older copy was found in {found[0]['dir']}. Click 'Import settings from an older copy...' "
                          "(bottom) to keep using its archive.", "")

    def import_previous(self):
        if self.runner.running:
            return
        try:
            found = cp.find_previous_installs()
        except Exception:  # noqa: BLE001
            found = []
        src = QtWidgets.QFileDialog.getExistingDirectory(
            self, "The older e6ai Archiver / Packrat folder (the one with its .ini file or the 'archive' folder)",
            found[0]["dir"] if found else "")
        if not src:
            return
        try:
            info = cp.import_previous_install(src, self.path)
        except (ValueError, OSError) as e:
            K.warn(self, "Import settings", str(e))
            return
        K.info(self, "Import settings", f"Settings imported from {info['dir']}.\n\nArchive folder: {info['out']}\n"
               + "\n".join(info.get("notes") or []))
        self.app.reload_archive()

    # ---- the form --------------------------------------------------------------------------------
    def _build_tab(self, tab: str) -> QtWidgets.QWidget:
        body = QtWidgets.QVBoxLayout()
        body.setContentsMargins(4, 10, 12, 10)
        body.setSpacing(12)
        groups: dict = {}
        for opt in cp.OPTIONS:
            if opt.tab != tab:
                continue
            form = groups.get(opt.group)
            if form is None:
                box = QtWidgets.QGroupBox(opt.group)
                form = QtWidgets.QFormLayout(box)
                form.setVerticalSpacing(9)
                form.setHorizontalSpacing(16)
                form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                groups[opt.group] = form
                body.addWidget(box)
            w = self._field(opt)
            w.setToolTip(opt.help)
            if opt.kind in ("flag", "neg"):
                sp = QtWidgets.QLabel("")
                sp.setMinimumWidth(200)
                form.addRow(sp, w)
                self.rows[opt.key] = (None, w)
            else:
                lab = QtWidgets.QLabel(opt.label)
                lab.setToolTip(opt.help)
                lab.setMinimumWidth(200)
                lab.setWordWrap(True)
                lab.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                form.addRow(lab, w)
                self.rows[opt.key] = (lab, w)
        body.addStretch(1)
        holder = QtWidgets.QWidget()
        holder.setLayout(body)
        holder.setMaximumWidth(K.FORM_MAX_WIDTH)   # a 2000 px wide text box is hard to read
        sc = QtWidgets.QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sc.setWidget(holder)
        return sc

    def _field(self, opt) -> QtWidgets.QWidget:
        v = self.state.get(opt.key, opt.default)
        key, kind = opt.key, opt.kind
        ch = self._changed
        if kind in ("flag", "neg"):
            w = QtWidgets.QCheckBox(opt.label)
            w.setChecked(_on(v))
            w.toggled.connect(lambda *_: ch())
            self.get[key] = w.isChecked
            return w
        if kind in ("choice", "combo"):
            w = QtWidgets.QComboBox()
            w.setEditable(kind == "combo")
            for c in opt.choices:
                val, lab = (c[0], c[1]) if isinstance(c, (tuple, list)) else (c, c)
                w.addItem(str(lab), str(val))
            i = w.findData(str(v))
            if i >= 0:
                w.setCurrentIndex(i)
            elif kind == "combo":
                w.setCurrentText(str(v or ""))
            w.setMaximumWidth(460)
            if kind == "combo":
                w.currentTextChanged.connect(lambda *_: ch())
                self.get[key] = lambda w=w: (w.itemData(w.findText(w.currentText())) if w.findText(w.currentText()) >= 0
                                            else w.currentText().strip())
            else:
                w.currentIndexChanged.connect(lambda *_: ch())
                self.get[key] = lambda w=w: w.currentData()
            return w
        if kind in ("ratings", "kinds"):
            names = (("e", "Explicit"), ("q", "Questionable"), ("s", "Safe")) if kind == "ratings" else tuple(
                (k, k.replace("_", " ")) for k in cp.META_KINDS)
            have = {x.strip() for x in str(v or "").replace(" ", ",").split(",") if x.strip()}
            boxes = []
            lay = K.hbox(spacing=14)
            for val, lab in names:
                cb = QtWidgets.QCheckBox(lab)
                cb.setChecked(val in have or (kind == "ratings" and not have))
                cb.toggled.connect(lambda *_: ch())
                boxes.append((val, cb))
                lay.addWidget(cb)
            lay.addStretch(1)
            self.get[key] = lambda boxes=boxes: ",".join(val for val, cb in boxes if cb.isChecked())
            return K.widget(lay)
        ed = QtWidgets.QLineEdit("" if v is None else str(v))
        if kind == "secret":
            ed.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
            if not ed.text():
                try:
                    ed.setText(cp.saved_key(cp.effective_login(self.state), self.state.get("site")) or "")
                except Exception:  # noqa: BLE001
                    pass
        if kind in ("int", "float"):
            ed.setMaximumWidth(160)
            if opt.default not in ("", None):
                ed.setPlaceholderText(str(opt.default))
        ed.textChanged.connect(lambda *_: ch())
        self.get[key] = lambda ed=ed: ed.text().strip()
        if kind in ("dir", "file", "savefile"):
            def pick(ed=ed, kind=kind):
                if kind == "dir":
                    p = QtWidgets.QFileDialog.getExistingDirectory(self, opt.label, ed.text() or "")
                elif kind == "file":
                    p, _ = QtWidgets.QFileDialog.getOpenFileName(self, opt.label, ed.text() or "")
                else:
                    p, _ = QtWidgets.QFileDialog.getSaveFileName(self, opt.label, ed.text() or "")
                if p:
                    ed.setText(os.path.normpath(p))
            return K.widget(K.hbox((ed, 1), K.button("Browse...", pick)))
        return ed

    def get_state(self) -> dict:
        st = dict(self.state)
        for k, fn in self.get.items():
            st[k] = fn()
        st["mode"] = self.mode.currentData()
        return st

    def _changed(self, save: bool = True):
        if self._loading:
            return
        st = self.get_state()
        mode = cp.MODE[st["mode"]]
        self.mode_help.setText(mode.help)
        for opt in cp.OPTIONS:
            lab, w = self.rows.get(opt.key, (None, None))
            on = cp.applies(opt, mode)
            if w is not None:
                w.setEnabled(on)
            if lab is not None:
                lab.setEnabled(on)
        try:
            self.errors, warnings = cp.validate(st)
        except Exception as e:  # noqa: BLE001
            self.errors, warnings = {"settings": str(e)}, []
        if not self.runner.running:
            if self.errors:
                key = next(iter(self.errors))
                name = cp.OPT[key].label if key in cp.OPT else key
                self._say(f"{name}: {self.errors[key]}", "error")
            elif warnings:
                self._say(warnings[0], "")
            else:
                self._say("", "")
        if save:
            self.save_timer.start()

    def _say(self, text, role=""):
        self.msg.setText(text)
        self.msg.setVisible(bool(text))
        K.restyle(self.msg, role=role, muted=not role)

    def _save(self):
        try:
            st = self.get_state()
            key = st.pop("api_key", "")
            cp.save_settings({**st, "api_key": ""}, self.path)
            self.state = {**st, "api_key": key}
        except Exception as e:  # noqa: BLE001
            log.warning("could not save the site archive settings: %s", e)

    # ---- running ---------------------------------------------------------------------------------
    def running(self) -> bool:
        return self.runner.running

    def _buttons(self):
        run = self.running()
        self.start_btn.setEnabled(not run)
        self.pause_btn.setEnabled(run)
        self.stop_btn.setEnabled(run)
        self.pause_btn.setText("Resume" if self.paused else "Pause")
        self.mode.setEnabled(not run)

    def start(self):
        if self.runner.running:
            return
        self._changed(save=False)
        st = self.get_state()
        mode = cp.MODE[st["mode"]]
        if self.errors:
            key = next(iter(self.errors))
            if key in cp.OPT:
                self.tabs.setCurrentIndex(cp.TABS.index(cp.OPT[key].tab))
            self._say(f"Not started: {cp.OPT[key].label if key in cp.OPT else key}: {self.errors[key]}", "error")
            return
        if self.last_exit == 3 and mode.network and not K.ask(
                self, "The last run was stopped by a 403", "The site answered 403 Forbidden last time. Starting again right "
                "away is not recommended: check your username and API key first, or wait.\n\nStart anyway?", "Start anyway",
                "Cancel", default_yes=False):
            return
        if mode.confirm and not K.ask(self, mode.label, mode.confirm, "Start", "Cancel", default_yes=mode.key == "full"):
            return
        self._save()
        try:
            key, login = str(st.get("api_key") or "").strip(), cp.effective_login(st)
            if key and login and cp.keyring_available():
                cp.save_key(login, key, st.get("site"))
        except Exception:  # noqa: BLE001
            pass
        argv = cp.build_command(st)
        self.log_path = argv[argv.index("--log-file") + 1]
        self.model = GuiModel()
        self.model.apply("phase", {"name": "starting"})
        self._log("")
        self._log("> " + cp.quote_command(argv))
        try:
            self.runner.start(argv, env=cp.child_env(st))
        except OSError as e:
            self._say(f"Could not start: {e}", "error")
            return
        self.started_mode, self.paused = mode, False
        self.tabs.setCurrentIndex(self.tabs.count() - 1)
        self.app.busy("Site archive running...")
        self._say("", "")
        self._buttons()

    def stop(self):
        if self.runner.running:
            self.runner.stop()
            self.phase.setText("Stopping (finishing the file being written)...")

    def kill(self):
        if self.runner.running:
            self.runner.kill()

    def pause(self):
        if not self.runner.running:
            return
        self.paused = not self.paused
        (self.runner.pause if self.paused else self.runner.resume)()
        self._buttons()

    def _log(self, text: str, level: int = logging.INFO):
        col = C["error"] if level >= logging.ERROR else C["warn"] if level >= logging.WARNING else C["text"]
        fmt = QtGui.QTextCharFormat()
        fmt.setForeground(QtGui.QColor(col))
        sb = self.logview.verticalScrollBar()
        at_end = sb.value() >= sb.maximum() - 4
        cur = self.logview.textCursor()
        cur.movePosition(QtGui.QTextCursor.MoveOperation.End)
        cur.insertText(text + "\n", fmt)
        if at_end:
            sb.setValue(sb.maximum())

    def _poll(self):
        got = False
        try:
            for _ in range(500):
                msg = self.runner.q.get_nowait()
                got = True
                if msg[0] == "event":
                    self.model.apply(msg[1], msg[2] if isinstance(msg[2], dict) else {})
                elif msg[0] == "line":
                    if msg[1].strip():
                        self._log(msg[1], msg[2])
                elif msg[0] == "exit":
                    self._exited(msg[1], msg[2])
        except Exception:  # noqa: BLE001 - queue.Empty
            pass
        if self.runner.running or got:
            self._paint()

    def _paint(self):
        s = self.model.snapshot()
        if self.runner.running:
            self.phase.setText(("Paused - " if self.paused else "") + str(s["phase"]).capitalize())
        self.bar.setValue(int(s["overall"] * 1000))
        self.overall.setText(s["overall_text"])
        self.speed.setText(f"{s['speed']}   ·   about {s['eta']} left   ·   {s['elapsed']} so far   ·   free: {s['free']}")
        self.file.setText(" ".join(x for x in (s.get("file_head", ""), s.get("file_name", ""), s.get("file_text", "")) if x))
        self.counts.setText(f"downloaded {s['downloaded']}   ·   already there {s['skipped']}   ·   failed {s['failed']}   ·   "
                            f"missing {s['missing']}   ·   posts {s['posts']}   ·   last problem: {s['last_error']}")

    def _exited(self, code, crashed):
        self.last_exit = code
        self.model.apply("done", {"code": code})
        level, text = cp.exit_message(code, crashed)
        self.phase.setText("Finished" if code == 0 else "Stopped")
        self._say(text, "ok" if level == "ok" else "error" if level == "error" else "")
        self._log(text, logging.INFO if level == "ok" else logging.WARNING)
        self.paused = False
        self.app.busy("")
        self._buttons()
        self._paint()
        if not self.app.isActiveWindow() and _on(self.state.get("notify", True)):
            self.app.notify("Packrat - site archive", text[:200])

    # ---- extras ----------------------------------------------------------------------------------
    def open_folder(self):
        out = cp._abs_out(self.get_state().get("out") or "")
        os.makedirs(out, exist_ok=True)
        uikit.open_path(out)

    def open_report(self):
        p = (getattr(self.model, "report", None) or {}).get("html")
        if not p:
            out = cp._abs_out(self.get_state().get("out") or "")
            try:
                found = sorted((f for f in os.listdir(out) if f.startswith("completeness-report") and f.endswith(".html")))
                p = os.path.join(out, found[-1]) if found else ""
            except OSError:
                p = ""
        if p and os.path.isfile(p):
            uikit.open_path(p)
        else:
            self._say("There is no report yet. Choose 'Completeness report' in 'What to do' and press Start.", "")

    def copy_command(self):
        try:
            QtWidgets.QApplication.clipboard().setText(cp.quote_command(cp.build_command(self.get_state())))
            self.app.flash("Command copied (the API key is not part of it).")
        except Exception as e:  # noqa: BLE001
            self._say(str(e), "error")

    def themed(self):
        pass
