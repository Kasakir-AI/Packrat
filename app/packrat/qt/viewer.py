"""The picture window: one picture big, with its tags and details. Animated pictures play, and videos play
inside the window (Qt Multimedia with its own FFmpeg, so no codecs have to be installed)."""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from .. import paths, uikit
from . import common as K
from .common import C, Qt

log = logging.getLogger("packrat")
TYPE_ORDER = ("artist", "contributor", "circle", "copyright", "character", "species", "model", "lore", "meta", "general")


def _fmt_time(ms: int) -> str:
    s = max(0, int(ms // 1000))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


class ImageView(QtWidgets.QGraphicsView):
    """Shows one picture: fitted to the window, Ctrl+wheel (or + / -) zooms, drag moves it around."""
    wheel_nav = QtCore.Signal(int)
    double_clicked = QtCore.Signal()
    middle_clicked = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self.setScene(QtWidgets.QGraphicsScene(self))
        self.item = QtWidgets.QGraphicsPixmapItem()
        self.item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.scene().addItem(self.item)
        self.setRenderHints(QtGui.QPainter.RenderHint.SmoothPixmapTransform | QtGui.QPainter.RenderHint.Antialiasing)
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.fit, self.scale_up, self.nav_wheel = True, False, True
        self.movie: QtGui.QMovie | None = None
        self.msg = ""

    def set_background(self, color: str):
        self.setBackgroundBrush(QtGui.QColor(color))

    def clear(self, msg: str = ""):
        self.stop_movie()
        self.item.setPixmap(QtGui.QPixmap())
        self.msg = msg
        self.viewport().update()

    def stop_movie(self):
        if self.movie is not None:
            self.movie.stop()
            self.movie.deleteLater()
            self.movie = None

    def set_pixmap(self, pm: QtGui.QPixmap, keep_zoom: bool = False):
        self.msg = ""
        self.item.setPixmap(pm)
        self.scene().setSceneRect(QtCore.QRectF(pm.rect()))
        if not keep_zoom:
            self.fit = True
            self.refit()

    def set_movie(self, path: str) -> bool:
        self.stop_movie()
        mv = QtGui.QMovie(path)
        if not mv.isValid():
            return False
        mv.setCacheMode(QtGui.QMovie.CacheMode.CacheAll)
        self.movie = mv
        first = [True]

        def frame(_n):
            self.set_pixmap(mv.currentPixmap(), keep_zoom=not first[0])
            first[0] = False
        mv.frameChanged.connect(frame)
        mv.start()
        return True

    def refit(self):
        pm = self.item.pixmap()
        if pm.isNull() or not self.fit:
            return
        self.setDragMode(QtWidgets.QGraphicsView.DragMode.NoDrag)
        vw, vh = self.viewport().width() - 4, self.viewport().height() - 4
        k = min(vw / max(1, pm.width()), vh / max(1, pm.height()))
        if not self.scale_up:
            k = min(k, 1.0)
        self.resetTransform()
        self.scale(k, k)
        self.centerOn(self.item)

    def zoom(self, factor: float | None):
        """None = fit to the window, 0 = actual size, otherwise multiply."""
        if self.item.pixmap().isNull():
            return
        if factor is None:
            self.fit = True
            self.refit()
            return
        self.fit = False
        self.setDragMode(QtWidgets.QGraphicsView.DragMode.ScrollHandDrag)
        if factor == 0:
            self.resetTransform()
        else:
            cur = self.transform().m11()
            if 0.02 < cur * factor < 40:
                self.scale(factor, factor)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.refit()

    def wheelEvent(self, e):
        d = e.angleDelta().y()
        if not d:
            return
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier or not self.nav_wheel or not self.fit:
            if e.modifiers() & Qt.KeyboardModifier.ControlModifier or not self.nav_wheel:
                self.zoom(1.2 if d > 0 else 1 / 1.2)
            else:
                super().wheelEvent(e)
            return
        self.wheel_nav.emit(-1 if d > 0 else 1)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.MiddleButton:
            self.middle_clicked.emit()
            return
        super().mousePressEvent(e)

    def drawForeground(self, p, rect):
        if self.msg:
            p.resetTransform()
            p.setPen(QtGui.QColor(C["muted"]))
            f = p.font()
            f.setPointSize(12)
            p.setFont(f)
            p.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.msg)


class VideoPane(QtWidgets.QWidget):
    """A video player: picture, play / pause, a time bar, volume."""

    def __init__(self, st):
        super().__init__()
        from PySide6 import QtMultimedia, QtMultimediaWidgets
        self.st = st
        # A PC without any sound device gets a silent player (creating an audio output there can crash Qt).
        has_sound = bool(QtMultimedia.QMediaDevices.audioOutputs())
        self.player = QtMultimedia.QMediaPlayer(self)
        self.audio = QtMultimedia.QAudioOutput(self) if has_sound else None
        if self.audio is not None:
            self.player.setAudioOutput(self.audio)
        self.video = QtMultimediaWidgets.QVideoWidget()
        self.player.setVideoOutput(self.video)
        self.play_btn = K.button("", self.toggle, flat=True, ico="pause", tip="Play / pause (Space)")
        self.pos = QtWidgets.QSlider(Qt.Orientation.Horizontal)
        self.pos.setRange(0, 0)
        self.pos.sliderMoved.connect(self.player.setPosition)
        self.time = K.label("0:00 / 0:00", muted=True)
        self.mute_btn = K.button("", self.toggle_mute, flat=True, ico="volume", tip="Sound on / off (M)")
        self.vol = QtWidgets.QSlider(Qt.Orientation.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setFixedWidth(90)
        self.vol.setValue(int(st.get("Viewer/videoVolume") or 60))
        self.vol.valueChanged.connect(self._volume)
        self.loop = QtWidgets.QCheckBox("Repeat")
        self.loop.setChecked(bool(st.get("Viewer/videoLoop", True)))
        self.loop.toggled.connect(lambda on: (st.set("Viewer/videoLoop", on), self._apply_loop()))
        if self.audio is not None:
            self.audio.setMuted(bool(st.get("Viewer/videoMuted")))
        else:
            self.mute_btn.setEnabled(False)
            self.vol.setEnabled(False)
            self.mute_btn.setToolTip("This PC has no sound device")
        self._volume(self.vol.value(), save=False)
        self.setLayout(K.vbox((self.video, 1), K.hbox(self.play_btn, (self.pos, 1), self.time, self.mute_btn, self.vol,
                                                       self.loop, margins=(8, 4, 8, 6)), spacing=0))
        self.player.positionChanged.connect(self._pos)
        self.player.durationChanged.connect(lambda d: self.pos.setRange(0, d))
        self.player.playbackStateChanged.connect(self._state)
        self.player.errorOccurred.connect(lambda _e, text: self.failed.emit(text))
        self._apply_loop()
        self._icons()

    failed = QtCore.Signal(str)

    def _apply_loop(self):
        from PySide6 import QtMultimedia
        self.player.setLoops(QtMultimedia.QMediaPlayer.Loops.Infinite if self.loop.isChecked() else 1)

    def _icons(self):
        from PySide6 import QtMultimedia
        playing = self.player.playbackState() == QtMultimedia.QMediaPlayer.PlaybackState.PlayingState
        self.play_btn.setIcon(K.icon("pause" if playing else "play"))
        self.mute_btn.setIcon(K.icon("mute" if self.audio is None or self.audio.isMuted() else "volume"))

    def _state(self, _s):
        self._icons()

    def _pos(self, p):
        if not self.pos.isSliderDown():
            self.pos.setValue(p)
        self.time.setText(f"{_fmt_time(p)} / {_fmt_time(self.player.duration())}")

    def _volume(self, v, save=True):
        if self.audio is not None:
            self.audio.setVolume(v / 100.0)
        if save:
            self.st.set("Viewer/videoVolume", int(v))

    def toggle(self):
        from PySide6 import QtMultimedia
        if self.player.playbackState() == QtMultimedia.QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def toggle_mute(self):
        if self.audio is None:
            return
        self.audio.setMuted(not self.audio.isMuted())
        self.st.set("Viewer/videoMuted", self.audio.isMuted())
        self._icons()

    def play(self, path: str):
        self.player.setSource(QtCore.QUrl.fromLocalFile(path))
        if self.st.get("Viewer/videoAutoplay", True):
            self.player.play()

    def stop(self):
        self.player.stop()
        self.player.setSource(QtCore.QUrl())


class Viewer(QtWidgets.QWidget):
    def __init__(self, app, tab, index: int):
        super().__init__(None, Qt.WindowType.Window)
        self.app, self.tab, self.index = app, tab, index
        self.req = 0
        self.local_path: str | None = None
        self.cache_path: str | None = None
        self.video: VideoPane | None = None
        self._closed = False
        self.setWindowIcon(K.app_icon())
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        st = app.st

        # toolbar
        self.prev_btn = K.button("", lambda: self.step(-1), ico="left", tip="Previous picture (Left arrow)")
        self.next_btn = K.button("", lambda: self.step(1), ico="right", tip="Next picture (Right arrow)")
        self.save_btn = K.button("Save", lambda: self.save(), accent=True, ico="save", tip="Save with your settings (S)")
        self.saveas_btn = K.button("Save as...", self.save_as, tip="Save somewhere else (Ctrl+S)")
        self.list_btn = K.button("Add to Downloads", self.to_list, ico="download")
        self.open_btn = K.button("Open file", self.open_file, tip="Open the saved file")
        self.page_btn = K.button("Open page", lambda: uikit.open_url(self.site.page_url_for(self.img)), ico="globe",
                                 tip="This picture's page on the site, in your browser")
        self.ws_menu = QtWidgets.QMenu(self)
        self.ws_btn = K.button("Search on", ico="search", menu=self.ws_menu,
                               tip="Find where else this picture is (reverse image search)")
        self.slide_btn = K.button("Slideshow", self.slideshow, tip="Next picture every few seconds (Space)")
        self.slide_btn.setCheckable(True)
        self.full_btn = K.button("", self.fullscreen, ico="expand", tip="Full screen (F11 or double-click)")
        more = QtWidgets.QMenu(self)
        more.addAction("Save to the favorites folder\tCtrl+Alt+S", lambda: self.save(favorite=True))
        more.addAction("Copy the picture\tCtrl+C", self.copy_picture)
        more.addAction("Copy the picture's address", lambda: self._copy(self.img.file_url))
        more.addAction("Copy the page's address", lambda: self._copy(self.site.page_url_for(self.img)))
        more.addAction("Show the saved file in its folder", lambda: uikit.reveal(self.local_path) if self.local_path
                       else self.say("This picture is not saved yet."))
        more.addSeparator()
        more.addAction("Fit to the window\t0", lambda: self.view.zoom(None))
        more.addAction("Actual size\t1", lambda: self.view.zoom(0))
        more.addAction("Load again\tF5", self.load)
        self.more_btn = K.button("", ico="more", menu=more, tip="More")
        self.info = K.label("", muted=True)
        self.info.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        self.toolbar = K.widget(K.hbox(self.prev_btn, self.next_btn, 6, self.save_btn, self.saveas_btn, self.list_btn,
                                       self.open_btn, self.page_btn, self.ws_btn, (self.info, 1), self.slide_btn,
                                       self.full_btn, self.more_btn, margins=(10, 8, 10, 8), spacing=6))

        # left: tags + details
        self.tags = QtWidgets.QListWidget()
        self.tags.itemClicked.connect(self._tag_click)
        self.tags.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tags.customContextMenuRequested.connect(self._tag_menu)
        self.tags.setToolTip("Click a tag to search it in a new tab. Right-click for more.")
        self.details = K.label("", wrap=True, rich=True, selectable=True)
        self.details.setOpenExternalLinks(True)
        self.side = K.widget(K.vbox((self.tags, 1), self.details, margins=(10, 0, 0, 10), spacing=8))
        self.side.setFixedWidth(250)

        # centre: picture / video / progress
        self.view = ImageView()
        self.view.scale_up = bool(st.get("Viewer/scaleUp"))
        self.view.nav_wheel = bool(st.get("Viewer/imageNavigateScroll", True))
        self.view.wheel_nav.connect(self.step)
        self.view.double_clicked.connect(self.fullscreen)
        self.view.middle_clicked.connect(lambda: self.close() if st.get("Viewer/imageCloseMiddleClick", True) else None)
        self.center = QtWidgets.QStackedWidget()
        self.center.addWidget(self.view)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setFixedHeight(4)
        self.progress.setVisible(False)
        body = K.hbox(self.side, (K.vbox(self.progress, (self.center, 1), spacing=0), 1), spacing=8)
        self.setLayout(K.vbox(self.toolbar, (body, 1), spacing=0))
        self.slide_timer = QtCore.QTimer(self, timeout=lambda: self.step(1))

        for seq, fn in (("Left", lambda: self.step(-1)), ("Right", lambda: self.step(1)), ("S", lambda: self.save()),
                        ("Ctrl+S", self.save_as), ("Ctrl+Alt+S", lambda: self.save(favorite=True)), ("F5", self.load),
                        ("Space", self._space), ("F11", self.fullscreen), ("Escape", self._escape),
                        ("Ctrl+C", self.copy_picture), ("+", lambda: self.view.zoom(1.25)), ("=", lambda: self.view.zoom(1.25)),
                        ("-", lambda: self.view.zoom(0.8)), ("0", lambda: self.view.zoom(None)), ("1", lambda: self.view.zoom(0)),
                        ("M", lambda: self.video.toggle_mute() if self.video else None), ("Ctrl+W", self.close)):
            QtGui.QShortcut(QtGui.QKeySequence(seq), self, activated=fn)
        g = app.lib.get_state("qt_viewer_geometry") if st.get("Viewer/rememberGeometry", True) else None
        if not (g and self.restoreGeometry(QtCore.QByteArray.fromHex(str(g).encode()))):
            scr = QtWidgets.QApplication.primaryScreen().availableGeometry()
            w, h = min(1280, scr.width() - 80), min(900, scr.height() - 80)
            self.setGeometry(scr.x() + (scr.width() - w) // 2, scr.y() + (scr.height() - h) // 2, w, h)
        self.themed()
        QtCore.QTimer.singleShot(0, self.load)

    # ---- which picture -----------------------------------------------------------------------------
    @property
    def img(self):
        return self.tab.items[self.index].image

    @property
    def site(self):
        return self.tab.items[self.index].site

    def themed(self):
        bg = str(self.app.st.get("Viewer/imageBackgroundColor") or "") or ("#0d0e12" if K.DARK[0] else "#e4e6ec")
        self.view.set_background(bg)
        K.refresh_icons(self)
        self._fill_tags()
        self._fill_details()

    def show_list(self, tab, index):
        self.tab, self.index = tab, index
        self.load()

    def step(self, d: int):
        n = len(self.tab.items)
        if not n:
            return
        j = self.index + d
        if 0 <= j < n:
            self.index = j
            self.load()
        elif d > 0 and self.slide_btn.isChecked():
            self.slide_btn.setChecked(False)
            self.slideshow()

    def say(self, text: str):
        self.info.setText(text)
        self.info.setToolTip(text)

    def _head(self) -> str:
        im = self.img
        return f"{self.index + 1} / {len(self.tab.items)}" + (f"    {im.width} x {im.height}" if im.width else "")

    # ---- loading -----------------------------------------------------------------------------------
    def load(self):
        if not 0 <= self.index < len(self.tab.items):
            return
        self.req += 1
        req = self.req
        img, site, app = self.img, self.site, self.app
        self.setWindowTitle(f"{site.name} #{img.id} - Packrat")
        self.say(self._head() + "    loading...")
        self._stop_video()
        self.center.setCurrentWidget(self.view)
        self.view.clear("Loading...")
        self._fill_tags()
        self._fill_details()
        from ..grab import websvc
        self.ws_menu.clear()
        for svc in websvc.services(app.st):
            self.ws_menu.addAction(svc["name"], lambda s=svc: websvc.open_service(s, img))
        self.local_path = app.lib.md5_path(img.md5) if img.md5 else None
        if self.local_path and not os.path.isfile(self.local_path):
            self.local_path = None
        self.open_btn.setEnabled(bool(self.local_path))
        self.prev_btn.setEnabled(self.index > 0)
        self.next_btn.setEnabled(self.index < len(self.tab.items) - 1)
        if (not self.local_path and (app.st.get("whitelist_download") or "never") == "image" and app.whitelisted(img)):
            self.save()
        self.progress.setValue(0)
        local = self.local_path

        def progress(done, total):
            if req == self.req and total:
                self.progress.setVisible(True)
                self.progress.setValue(int(done / total * 1000))

        def work():
            if not img.details_loaded and site.source.meta.get("apis"):
                try:
                    site.details(img)
                except Exception:  # noqa: BLE001 - the picture itself is enough
                    pass
            return self._fetch(site, img, local, lambda d, t: app.worker.call(progress, d, t), lambda: req != self.req)

        def done(res):
            if req != self.req or self._closed:
                return
            self.progress.setVisible(False)
            self._fill_tags()
            self._fill_details()
            self._show(res)

        def failed(e):
            if req == self.req and not self._closed:
                self.progress.setVisible(False)
                self.view.clear("Could not load this picture.\n\n" + uikit.friendly(str(e), site.name))
                self.say(self._head())
        app.worker.submit(work, on_done=done, on_error=failed)
        self._preload()

    def _fetch(self, site, img, local, progress=None, cancelled=None):
        """In a thread. Returns (kind, path, QImage or None): kind is image | movie | video."""
        src = local if local and os.path.isfile(local) else None
        video = img.is_video
        if src is None:
            use_sample = bool(self.app.st.get("Viewer/viewSamples")) and img.sample_url and not video
            url = (img.sample_url if use_sample else img.file_url) or img.sample_url or img.preview_url
            if not url:
                raise ValueError("The site gave no address for this picture (it may need a login).")
            ext = (img.ext if not use_sample else "") or os.path.splitext(url.split("?")[0])[1].lstrip(".") or "img"
            cache = paths.cache_dir("viewer") / (hashlib.sha1(url.encode()).hexdigest() + "." + ext[:5])
            if not cache.is_file():
                r = site.request(url, "image", referer=site.referer("image", img.page_url, img), stream=True)
                try:
                    if r.status_code != 200:
                        raise ValueError(f"HTTP {r.status_code}")
                    total = int(r.headers.get("Content-Length") or img.file_size or 0)
                    tmp = cache.with_suffix(cache.suffix + f".part{os.getpid()}")
                    got = 0
                    try:
                        with open(tmp, "wb") as f:
                            for chunk in r.iter_content(1 << 16):
                                if cancelled and cancelled() and (video or total > 8_000_000):
                                    raise ValueError("cancelled")
                                f.write(chunk)
                                got += len(chunk)
                                if progress and (video or total > 1_500_000):
                                    progress(got, total)
                        os.replace(tmp, cache)
                    finally:
                        if tmp.exists():
                            try:
                                tmp.unlink()
                            except OSError:
                                pass
                finally:
                    r.close()
            src = str(cache)
        if video:
            return ("video", src, None)
        reader = QtGui.QImageReader(src)
        reader.setDecideFormatFromContent(True)
        reader.setAutoTransform(True)
        if reader.supportsAnimation() and reader.imageCount() > 1:
            return ("movie", src, None)
        qi = reader.read()
        if qi.isNull():      # a format Qt does not read: let Pillow try
            from PIL import Image as PI
            with PI.open(src) as im:
                im.seek(0)
                qi = K.pil_to_qimage(im)
        return ("image", src, qi)

    def _preload(self):
        n = int(self.app.st.get("Viewer/preload") or 0)
        for d in range(1, n + 1):
            for j in (self.index + d, self.index - d):
                if 0 <= j < len(self.tab.items):
                    sh = self.tab.items[j]
                    if not sh.image.is_video:
                        local = self.app.lib.md5_path(sh.image.md5) if sh.image.md5 else None
                        self.app.worker.submit(self._fetch_quiet, sh.site, sh.image, local)

    def _fetch_quiet(self, site, img, local):
        try:
            self._fetch(site, img, local)
        except Exception:  # noqa: BLE001
            pass

    def _show(self, res):
        kind, path, qi = res
        self.cache_path = path
        note = ""
        if kind == "video":
            if not self._play_video(path):
                self.view.clear("This video could not be played here.\nUse 'Open page', or save it and open the file.")
        elif kind == "movie" and self.view.set_movie(path):
            self.center.setCurrentWidget(self.view)
            note = "    animated"
        else:
            if qi is None:
                qi = QtGui.QImage(path)
            self.center.setCurrentWidget(self.view)
            self.view.set_pixmap(QtGui.QPixmap.fromImage(qi))
        self.say(self._head() + note + (f"    saved: {self.local_path}" if self.local_path else ""))

    def _play_video(self, path: str) -> bool:
        try:
            if self.video is None:
                self.video = VideoPane(self.app.st)
                self.video.failed.connect(lambda text: self.say(self._head() + f"    video problem: {text}"))
                self.center.addWidget(self.video)
            self.center.setCurrentWidget(self.video)
            self.video.play(path)
            return True
        except Exception as e:  # noqa: BLE001 - no multimedia support on this PC
            log.warning("video playback is not available: %s", e)
            return False

    def _stop_video(self):
        if self.video is not None:
            self.video.stop()

    # ---- side panel --------------------------------------------------------------------------------
    def _fill_tags(self):
        st, im = self.app.st, self.img
        self.tags.clear()
        tags = list(im.tags)
        if (st.get("Viewer/tagOrder") or "type") == "type":
            tags.sort(key=lambda t: (TYPE_ORDER.index(t.type) if t.type in TYPE_ORDER else len(TYPE_ORDER), t.name))
        elif st.get("Viewer/tagOrder") == "name":
            tags.sort(key=lambda t: t.name)
        counts = bool(st.get("Viewer/showTagCount"))
        for t in tags:
            it = QtWidgets.QListWidgetItem(f"{t.name}   {t.count:,}" if counts and t.count else t.name)
            it.setData(Qt.ItemDataRole.UserRole, t.name)
            col = uikit.tag_color(t.type or "general", st, K.DARK[0])
            if not K.DARK[0] and col.lower() in ("#000000", "#000"):
                col = C["text"]
            it.setForeground(QtGui.QColor(col))
            self.tags.addItem(it)

    def _fill_details(self):
        im, site = self.img, self.site
        rows = [("Site", K.esc(site.name)), ("Number", K.esc(im.id)), ("Rating", K.esc(im.rating or "")),
                ("Score", K.esc(im.score if im.score not in (None, "") else "")),
                ("Size", f"{im.width} x {im.height}" if im.width else ""),
                ("File", K.esc(" ".join(x for x in ((im.ext or "").upper(), K.fmt_bytes(im.file_size) if im.file_size else "") if x))),
                ("Posted", im.date.strftime("%Y-%m-%d %H:%M") if im.date else ""),
                ("By", K.esc(im.data.get("author") or "")), ("MD5", K.esc(im.md5 or ""))]
        for s in (im.sources or [])[:3]:
            s = str(s)
            if s.startswith(("http://", "https://")):
                short = s.split("//", 1)[1][:34]
                rows.append(("Source", f'<a style="color:{C["accent"]}" href="{K.esc(s)}">{K.esc(short)}</a>'))
            elif s:
                rows.append(("Source", K.esc(s[:60])))
        self.details.setText("<table cellspacing='2'>" + "".join(
            f"<tr><td style='color:{C['muted']}; padding-right:8px;'>{k}</td><td>{v}</td></tr>" for k, v in rows if v)
            + "</table>")

    def _tag_click(self, it):
        self.app.new_search_tab(it.data(Qt.ItemDataRole.UserRole), self.tab.sites, focus=True, run=True)
        self.app.show_page("browse")
        self.app.bring_up()

    def _tag_menu(self, pos):
        it = self.tags.itemAt(pos)
        if it is not None:
            from .browse import tag_menu
            tag_menu(self.app, it.data(Qt.ItemDataRole.UserRole), self.tags.viewport().mapToGlobal(pos), None, parent=self)

    # ---- actions -----------------------------------------------------------------------------------
    def _copy(self, text):
        QtWidgets.QApplication.clipboard().setText(text or "")
        self.say("Copied.")

    def copy_picture(self):
        pm = self.view.item.pixmap()
        if pm.isNull() or self.center.currentWidget() is not self.view:
            self.say("Only pictures can be copied (not videos).")
            return
        QtWidgets.QApplication.clipboard().setPixmap(pm)
        self.say("Picture copied - paste it anywhere.")

    def save(self, favorite: bool = False):
        img = self.img

        def each(im, res):
            if not res or self._closed:
                return
            r = res[0]
            if im is self.img:
                self.say(self._head() + f"    {r.status}: {r.paths[0] if r.paths else r.message}")
            if r.paths:
                if im is self.img:
                    self.local_path = r.paths[0]
                    self.open_btn.setEnabled(True)
                self.tab.mark_saved(im)
        if favorite and not self.app.st.get("Save/path_favorites"):
            self.say("No favorites folder yet: set one in Settings > Saving.")
            return
        self.app.save_images([(self.site, img)], each, path=self.app.st.save_path(favorite=True) if favorite else None,
                             filename=self.app.st.filename(favorite=True) if favorite else None)

    def save_as(self):
        img = self.img
        p, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save as", f"{img.md5 or img.id}.{img.ext or 'jpg'}")
        if not p:
            return
        folder, name = os.path.dirname(p), os.path.basename(p)
        self.app.save_images([(self.site, img)], lambda i, r: self.say(self._head() + "    " + (r[0].status if r else "")),
                             path=folder, filename=name.replace("%", "_"))

    def to_list(self):
        self.app.add_to_downloads_images([(self.site, self.img)])
        self.say(self._head() + "    added to Downloads")

    def open_file(self):
        if self.local_path:
            uikit.open_path(self.local_path)

    def _space(self):
        if self.video is not None and self.center.currentWidget() is self.video:
            self.video.toggle()
        else:
            self.slide_btn.toggle()
            self.slideshow()

    def slideshow(self):
        if self.slide_btn.isChecked():
            self.slide_timer.start(max(1, int(self.app.st.get("Viewer/slideshow") or 5)) * 1000)
        else:
            self.slide_timer.stop()

    def fullscreen(self):
        full = not self.isFullScreen()
        self.toolbar.setVisible(not full)
        self.side.setVisible(not full)
        if full:
            self.showFullScreen()
        else:
            self.showNormal()

    def _escape(self):
        if self.isFullScreen():
            self.fullscreen()
        else:
            self.close()

    def closeEvent(self, e):
        self._closed = True
        self.req += 1
        self.slide_timer.stop()
        self.view.stop_movie()
        self._stop_video()
        try:
            if not self.isFullScreen():
                self.app.lib.set_state("qt_viewer_geometry", bytes(self.saveGeometry().toHex()).decode())
        except Exception:  # noqa: BLE001
            pass
        if self in self.app.viewers:
            self.app.viewers.remove(self)
        super().closeEvent(e)


def clear_viewer_cache(max_mb: int = 600) -> None:
    """Keep the picture window's cache folder below max_mb (oldest files go first). Called at start, in a thread."""
    try:
        d = Path(paths.cache_dir("viewer"))
        files = sorted((p for p in d.iterdir() if p.is_file()), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
        for p in files:
            if total <= max_mb * 1024 * 1024:
                break
            total -= p.stat().st_size
            p.unlink()
    except OSError:
        pass
