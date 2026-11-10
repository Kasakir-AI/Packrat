"""Progress events and run control (pause / stop), shared by the worker and the optional GUI.

The download code calls emit(kind, **data). With no GUI attached there are no sinks and emit()
is a cheap no-op. The GUI registers a sink that only does queue.put_nowait(); it never touches
widgets from the worker thread. A failing sink is logged once and removed: progress display can
never break a download.

control.stop() / control.pause() are thread-safe. The worker calls control.checkpoint() before
every request (pause takes effect there) and control.check_stop() between file chunks; control.sleep()
is used for all backoff waits so a stop request interrupts long waits immediately.
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("packrat")
_sinks: list = []
_lock = threading.Lock()


class StopRequested(KeyboardInterrupt):
    """Raised in the worker after the user pressed Stop / closed the GUI. Subclasses
    KeyboardInterrupt so every existing clean-up path (delete .part, commit, release lock) runs."""


def add_sink(fn) -> None:
    with _lock:
        _sinks.append(fn)


def remove_sink(fn) -> None:
    with _lock:
        if fn in _sinks:
            _sinks.remove(fn)


def emit(kind: str, **data) -> None:
    if not _sinks:
        return
    for fn in list(_sinks):
        try:
            fn(kind, data)
        except Exception as e:  # never let a display problem reach the download code
            log.warning("progress display error (%s); display detached", e)
            remove_sink(fn)


class Control:
    def __init__(self):
        self._stop = threading.Event()
        self._run = threading.Event()
        self._run.set()
        self._recheck = threading.Event()  # 1.9: "check the free disk space now" (panel Resume)

    def reset(self) -> None:
        self._stop.clear()
        self._run.set()
        self._recheck.clear()

    def recheck(self) -> None:
        """1.9: wake a worker waiting for disk space so it checks again at once."""
        self._recheck.set()

    def take_recheck(self) -> bool:
        if self._recheck.is_set():
            self._recheck.clear()
            return True
        return False

    # --- called from the GUI / signal handlers ---------------------------
    def stop(self) -> None:
        self._stop.set()
        self._run.set()  # wake a paused worker so it can exit

    def pause(self) -> None:
        self._run.clear()
        emit("phase", name="pausing (after the current file)")

    def resume(self) -> None:
        self._run.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    @property
    def paused(self) -> bool:
        return not self._run.is_set()

    # --- called from the worker -------------------------------------------
    def check_stop(self) -> None:
        if self._stop.is_set():
            raise StopRequested("stop requested")

    def checkpoint(self) -> None:
        """Before each request: honour stop, and block while paused."""
        self.check_stop()
        if not self._run.is_set():
            emit("phase", name="paused")
            log.info("paused")
            while not self._run.wait(0.5):
                pass
            self.check_stop()
            log.info("resumed")
            emit("phase", name="resumed")

    def sleep(self, seconds: float) -> None:
        """time.sleep() in 0.5 s slices that stops early (StopRequested) when a stop is requested.
        Counts slices instead of reading the clock, so clock jumps and sleep/resume can't extend it."""
        left = max(0.0, float(seconds))
        while left > 0:
            self.check_stop()
            step = min(left, 0.5)
            time.sleep(step)
            left -= step
        self.check_stop()


control = Control()


# --- 1.4: the control panel runs the CLI as a child process ------------------------------------
# Progress goes to the panel as one JSON line per event on stdout (logs stay on stderr), and Stop /
# Pause / Resume come back through a small "control file" the panel writes. A file works the same on
# Windows and Linux, needs no console (the child runs with CREATE_NO_WINDOW, so Ctrl+Break events
# cannot reach it) and ends in the same clean StopRequested path as the Stop button of the old window.
PROGRESS_PREFIX = "@packrat-progress "
CONTROL_WORDS = ("run", "pause", "stop", "recheck")  # recheck: 1.9, re-test free disk space now


def attach_progress_stream(stream) -> None:
    """Mirror every event to `stream` as `@packrat-progress ["kind", {...}]` lines (ASCII JSON, flushed).
    If the panel goes away (broken pipe), the run is stopped cleanly instead of running headless."""
    import json
    lock = threading.Lock()

    def sink(kind, data):
        line = PROGRESS_PREFIX + json.dumps([kind, data], default=str, ensure_ascii=True) + "\n"
        try:
            with lock:
                stream.write(line)
                stream.flush()
        except (OSError, ValueError):  # BrokenPipeError / closed pipe: nobody is watching any more
            control.stop()
            import os
            import sys
            if stream is sys.stdout:     # later prints (and the flush at exit) must not fail as well
                try:
                    sys.stdout = open(os.devnull, "w", encoding="utf-8")
                except OSError:
                    pass
            raise
    add_sink(sink)


def read_control_word(path) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            word = f.read(64).strip().lower()
    except OSError:
        return None
    return word if word in CONTROL_WORDS else None


def write_control_word(path, word: str) -> None:
    """Atomically replace the control file (the reader never sees a half-written word)."""
    import os
    if word not in CONTROL_WORDS:
        raise ValueError(word)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(word + "\n")
    for _ in range(20):  # Windows: the reader may hold the file open for a moment
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05)
    os.replace(tmp, path)


def watch_control_file(path, interval: float = 0.3) -> threading.Thread:
    """Poll `path` in a daemon thread: "stop" -> control.stop(), "pause" -> pause(), "run" -> resume().
    Missing / empty / unknown content is ignored (the panel creates the file with "run")."""
    def loop():
        last = None
        while not control.stopping:
            word = read_control_word(path)
            if word and word != last:
                last = word
                if word == "stop":
                    log.warning("Stop requested by the control panel; finishing cleanly...")
                    control.stop()
                elif word == "pause":
                    control.pause()
                elif word == "run" and control.paused:
                    control.resume()
                elif word == "recheck":
                    control.recheck()
            time.sleep(interval)
    t = threading.Thread(target=loop, name="control-file", daemon=True)
    t.start()
    return t
