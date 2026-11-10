"""Cross-platform helpers, with Windows in mind.

- long-path safe paths (\\\\?\\ prefix on Windows for data files)
- os.replace / unlink with retries when antivirus or indexers briefly lock a file
- free-space checks via shutil.disk_usage
- Ctrl-C / Ctrl-Break / SIGTERM -> KeyboardInterrupt (clean checkpointed exit)
- single-instance lock file so two runs never share one SQLite DB
- keep the PC awake while running (Windows SetThreadExecutionState)
- UTF-8-safe console output on cp1252 consoles
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import signal
import sys
import time
from pathlib import Path

log = logging.getLogger("packrat")
IS_WINDOWS = os.name == "nt"
_SAFE_EXT = re.compile(r"^[a-z0-9]{1,8}$")
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def safe_ext(ext: str | None) -> str | None:
    """File extensions come from the API; only allow short lowercase alphanumerics."""
    if not ext:
        return None
    ext = ext.lower().lstrip(".")
    return ext if _SAFE_EXT.match(ext) else None


def safe_filename(name: str, fallback: str = "file") -> str:
    """Make a name safe on Windows: no ':' '\\' '/' '*' '?' etc., no reserved device names."""
    name = _SAFE_NAME.sub("_", name).strip(" .") or fallback
    if name.split(".")[0].lower() in _RESERVED:
        name = "_" + name
    return name[:150]


def long_path(p: Path) -> Path:
    """On Windows, return an extended-length (\\\\?\\) absolute path so deep dirs exceed MAX_PATH safely."""
    if not IS_WINDOWS:
        return p
    s = str(Path(os.path.abspath(p)))
    if s.startswith("\\\\?\\"):
        return Path(s)
    if s.startswith("\\\\"):  # UNC share
        return Path("\\\\?\\UNC\\" + s[2:])
    return Path("\\\\?\\" + s)


def _retry(fn, *args, what: str, attempts: int = 10):
    delay = 0.1
    for i in range(attempts):
        try:
            return fn(*args)
        except PermissionError:
            if i == attempts - 1:
                raise
            log.debug("%s locked (antivirus/indexer?); retry in %.1fs", what, delay)
            time.sleep(delay)
            delay = min(delay * 2, 5.0)


def safe_replace(src: Path, dst: Path) -> None:
    """Atomic rename that tolerates transient PermissionError (Windows file locks)."""
    _retry(os.replace, src, dst, what=f"rename {src}")


def safe_unlink(p: Path) -> None:
    def _u(x):
        try:
            os.unlink(x)
        except FileNotFoundError:
            pass
    _retry(_u, p, what=f"delete {p}")


def free_bytes(path: Path) -> int:
    path = Path(path)
    while not path.exists() and path.parent != path:
        path = path.parent
    return shutil.disk_usage(path).free


class DiskFullError(RuntimeError):
    pass


def ensure_free(path: Path, need: int, reserve: int) -> None:
    free = free_bytes(path)
    if free < need + reserve:
        raise DiskFullError(
            f"Not enough free disk space at {path}: {free / 2**30:.1f} GiB free, need "
            f"{need / 2**20:.1f} MiB + {reserve / 2**30:.1f} GiB reserve. Free space or use --out on another drive; "
            f"rerun to resume.")


def is_disk_full(e: BaseException) -> bool:
    """True for 'disk full' OS errors (ENOSPC on Linux/macOS, ERROR_DISK_FULL/HANDLE_DISK_FULL on Windows)."""
    import errno
    return isinstance(e, OSError) and (
        e.errno == errno.ENOSPC or getattr(e, "winerror", None) in (39, 112))


def csv_field_limit() -> int:
    """Largest csv field size accepted on this platform. sys.maxsize overflows the C long on
    Windows (32-bit long), so csv.field_size_limit(sys.maxsize) raises OverflowError there."""
    return 2**31 - 1


def install_signal_handlers() -> None:
    """Make Ctrl-Break (Windows) and SIGTERM behave like Ctrl-C (clean, checkpointed stop)."""
    def _raise(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")
    for name in ("SIGBREAK", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, _raise)
            except (ValueError, OSError):
                pass


def setup_console() -> None:
    """Never crash on characters a cp1252 console can't show."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass


class KeepAwake:
    """Ask Windows not to sleep while archiving (display may still turn off)."""
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001

    def __enter__(self):
        if IS_WINDOWS:
            try:
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(self.ES_CONTINUOUS | self.ES_SYSTEM_REQUIRED)
                log.info("Keep-awake enabled (Windows will not sleep while this runs)")
            except Exception as e:  # pragma: no cover
                log.warning("could not enable keep-awake: %s", e)
        return self

    def __exit__(self, *exc):
        if IS_WINDOWS:
            try:
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(self.ES_CONTINUOUS)
            except Exception:
                pass
        return False


class InstanceLock:
    """Exclusive lock on <out>/.lock so only one archiver uses a given archive dir."""

    def __init__(self, out: Path):
        self.path = Path(out) / ".lock"
        self.f = None

    def __enter__(self):
        self.f = open(self.path, "a+")
        try:
            if IS_WINDOWS:
                import msvcrt
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.f.close()
            raise RuntimeError(
                f"Another Packrat run is already using {self.path.parent} (lock: {self.path}). It may be "
                f"another window of Packrat or an older e6ai-archiver (e.g. 2.0) that is still running. Stop it "
                f"first (press Stop in its control panel, or Ctrl+C in its window, and wait until it says "
                f"Stopped), then start again. Nothing was changed.")
        return self

    def __exit__(self, *exc):
        try:
            if IS_WINDOWS:
                import msvcrt
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        self.f.close()
        return False


# ----------------------------------------------------------------------------
class StorageError(RuntimeError):
    """The archive drive is gone (USB unplugged, network share dropped), read-only, or failing."""


# errno / Windows error codes meaning "the drive went away or can't be written"
_GONE_ERRNO = {"EIO", "ENODEV", "ENXIO", "EROFS", "ESTALE", "ENOTCONN", "EACCES", "EPERM"}
_GONE_WINERROR = {3, 5, 15, 19, 21, 53, 55, 64, 67, 1006, 1117, 1167}  # path/device not ready, write-protect, ...


def storage_problem(e: BaseException, out: Path) -> str | None:
    """Explain an OSError that means the archive location is unusable, else None."""
    import errno
    if not isinstance(e, OSError) or is_disk_full(e):
        return None
    out = Path(out)
    try:
        gone = not out.exists()
    except OSError:
        gone = True
    if gone:
        return f"the archive folder {out} is no longer reachable (drive unplugged or share disconnected?)"
    code = errno.errorcode.get(e.errno or -1, "")
    if code in ("EROFS",) or getattr(e, "winerror", None) == 19:
        return f"the drive holding {out} is read-only / write-protected"
    if isinstance(e, PermissionError):
        return f"no permission to write in {out} (read-only folder? check its properties/permissions)"
    if code in _GONE_ERRNO or getattr(e, "winerror", None) in _GONE_WINERROR:
        return f"I/O error on the drive holding {out}: {e}"
    return None


def check_writable(out: Path) -> None:
    """Startup preflight: create, write, fsync and delete a probe file in the archive folder."""
    out = Path(out)
    try:
        out.mkdir(parents=True, exist_ok=True)
        probe = out / f".write-test-{os.getpid()}"
        with open(probe, "wb") as f:
            f.write(b"ok")
            f.flush()
            os.fsync(f.fileno())
        os.unlink(probe)
    except OSError as e:
        if is_disk_full(e):
            raise DiskFullError(f"The drive holding {out} is full.") from e
        raise StorageError(storage_problem(e, out) or f"cannot write to {out}: {e}") from e


def link_or_copy(src: Path, dst: Path, mode: str = "hardlink") -> str:
    """Make dst a hardlink to src (no extra space; same drive only) or a copy. Returns what was done."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".part")
    safe_unlink(tmp)
    if mode == "hardlink":
        try:
            os.link(src, tmp)
            safe_replace(tmp, dst)
            return "hardlink"
        except OSError as e:
            if is_disk_full(e):
                raise
            log.debug("hardlink %s -> %s failed (%s); copying instead", src, dst, e)
            safe_unlink(tmp)
    try:
        shutil.copyfile(src, tmp)
        safe_replace(tmp, dst)
    except BaseException:
        safe_unlink(tmp)
        raise
    return "copy"
