"""Monitors: searches checked in the background for new images (Grabber's monitors, and favorites with a
monitoring interval).

A check only looks at what is newer than the last check (by post id, or date when a site has no ids): the
first check of a new monitor only records where "now" is, so adding a monitor never floods you with old
images - use Get all for those. The delay ignores the newest images for a while (gives taggers time).
New images are added to the download list (with the monitor's own folder / file name, if set) when
"Download" is on, and a notification says how many were found when "Notify" is on.

Works inside the window (MonitorScheduler, a background thread) and from the command line
(`packrat monitors --run-due`, e.g. from Windows Task Scheduler while the window is closed).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from .filters import Blacklist, PostFilter
from . import filename as F

log = logging.getLogger("packrat.grab")
MAX_PAGES = 10
PER_PAGE = 50


def check(mon: dict, store, st, lib, cancel: threading.Event | None = None) -> dict:
    """Check one monitor now. Returns {"new": [Image], "errors": [str], "first": bool}."""
    seen = dict(mon.get("seen") or {})
    new_imgs, errors = [], []
    first = not seen
    delay = timedelta(minutes=int(mon.get("delay_min") or 0))
    now = datetime.now(timezone.utc)
    pf = PostFilter(mon.get("postfilter") or "")
    bl = None if mon.get("blacklisted") else Blacklist(st.lines("blacklistedtags"))
    for host in mon.get("sites") or []:
        site = store.get(host)
        if site is None:
            errors.append(f"unknown site {host}")
            continue
        last = seen.get(host) or {}
        max_id = int(last.get("max_id") or 0)
        max_date = _dt(last.get("max_date"))
        best_id, best_date = max_id, max_date
        found = []
        prev = None
        for page in range(1, MAX_PAGES + 1):
            if cancel is not None and cancel.is_set():
                break
            pr = site.search(mon["search"], page, PER_PAGE, prev, cancel=cancel)
            if pr.error:
                errors.append(f"{site.name}: {pr.error}")
                break
            if not pr.images:
                break
            older = False
            for im in pr.images:
                iid = im.id if isinstance(im.id, int) else 0
                d = im.date
                if delay and d is not None and d > now - delay:
                    continue  # too new: next time
                if iid and iid > best_id:
                    best_id = iid
                if d is not None and (best_date is None or d > best_date):
                    best_date = d
                is_new = (iid > max_id) if (iid and max_id) else (d is not None and max_date is not None and d > max_date)
                if not first and not is_new:
                    older = True
                    continue
                if first:
                    continue
                toks = F.build_tokens(im, st, site=site)
                if pf and pf.match(toks):
                    continue
                if bl and bl.match(toks):
                    continue
                found.append(im)
            prev = pr.info()
            if first or older:
                break
        seen[host] = {"max_id": best_id, "max_date": best_date.isoformat() if best_date else None}
        new_imgs += found
    nxt = time.time() + max(1, int(mon.get("interval_min") or 1440)) * 60
    lib.update_monitor(mon["id"], seen=seen, last_check=time.time(), next_check=nxt,
                       new_count=int(mon.get("new_count") or 0) + len(new_imgs),
                       total_new=int(mon.get("total_new") or 0) + len(new_imgs), last_error="; ".join(errors)[:500])
    if new_imgs and mon.get("download"):
        for im in new_imgs:
            lib.batch_add("unique", {"image": im.to_json(), "path": mon.get("path") or "",
                                     "filename": mon.get("filename") or "", "from_monitor": mon["id"]})
    return {"new": new_imgs, "errors": errors, "first": first}


def _dt(v):
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class MonitorScheduler:
    """Background thread in the window: checks due monitors one by one, then calls on_result(mon, result)
    on the worker thread (the window marshals it to Tk)."""

    def __init__(self, store, st, lib, on_result, on_checking=None):
        self.store, self.st, self.lib = store, st, lib
        self.on_result, self.on_checking = on_result, on_checking
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._now: set[int] = set()
        self.thread = None
        self.running_id = None

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._loop, name="monitors", daemon=True)
            self.thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def check_now(self, ids=None):
        if ids is None:
            ids = [m["id"] for m in self.lib.monitors()]
        self._now.update(ids)
        self._wake.set()

    def _loop(self):
        delay = int(self.st.get("Monitoring/startupDelay") or 0)
        if self._wake.wait(delay):
            self._wake.clear()
        while not self._stop.is_set():
            try:
                todo = []
                if self.st.get("Monitoring/enabled", True):
                    todo = self.lib.due_monitors()
                forced = [m for m in self.lib.monitors() if m["id"] in self._now and m not in todo]
                self._now.clear()
                for mon in forced + todo:
                    if self._stop.is_set():
                        break
                    self.running_id = mon["id"]
                    if self.on_checking:
                        self.on_checking(mon)
                    try:
                        res = check(mon, self.store, self.st, self.lib, self._stop)
                    except Exception as e:  # noqa: BLE001
                        log.exception("monitor %s failed", mon.get("search"))
                        res = {"new": [], "errors": [str(e)], "first": False}
                        self.lib.update_monitor(mon["id"], last_error=str(e)[:500], last_check=time.time(),
                                                next_check=time.time() + int(mon.get("interval_min") or 1440) * 60)
                    self.running_id = None
                    self.on_result(mon, res)
            except Exception:  # noqa: BLE001
                log.exception("monitor loop error")
            self._wake.wait(30)
            self._wake.clear()
