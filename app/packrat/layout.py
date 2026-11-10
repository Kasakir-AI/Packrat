"""`preview` (where files would go) and `reorganize` (move saved files to the current layout).

reorganize is a dry run unless apply=True, never deletes anything, commits after every move
(so it can be stopped and rerun at any point), and writes an audit CSV of every action.
"""
from __future__ import annotations

import csv
import re
import logging
import time
from pathlib import Path

from .archiver import md5_of, sweep_part_files
from .events import control, emit
from .naming import FILES_DIR, Naming
from .platform_utils import link_or_copy, safe_replace
from .store import Store, abspath, legacy_relpath

log = logging.getLogger("packrat")


def preview(store: Store, naming: Naming, limit: int = 20, status: str = "any") -> list[dict]:
    """Offline: the paths the next `limit` posts would get (newest first)."""
    where = {"any": "file_status IN ('pending','ok')", "pending": "file_status='pending'",
             "ok": "file_status='ok'"}[status]
    rows = store.db.execute(f"SELECT id, file_status, file_path FROM posts WHERE {where} "
                            "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for pid, st, cur in rows:
        info = store.post_info(pid)
        planned = naming.plan(info, len(str(store.out)))
        out.append({"id": pid, "status": st, "current": cur, "path": planned[0], "extra": planned[1:],
                    "categorized": info.categorized})
    return out


def _free_target(out: Path, rel: str) -> str:
    """rel, or rel with __2, __3 ... before the extension if something else is already there."""
    folder, _, name = rel.rpartition("/")
    stem, dot, ext = name.rpartition(".")
    for n in range(2, 1000):
        cand = f"{folder}/{stem}__{n}{dot}{ext}"
        if not abspath(out, cand).exists():
            return cand
    raise RuntimeError(f"too many name collisions for {rel}")


def _is_collision_variant(cur: str, target: str) -> bool:
    """True if cur is target with __N before the extension (a name _free_target handed out earlier)."""
    folder, _, name = target.rpartition("/")
    stem, dot, ext = name.rpartition(".")
    return re.fullmatch(re.escape(f"{folder}/{stem}__") + r"[0-9]{1,3}" + re.escape(f"{dot}{ext}"), cur) is not None


def _prune_empty_dirs(out: Path, start: Path) -> None:
    """Remove now-empty folders left by moves (only empty ones; stops at files/ or data/)."""
    roots = {abspath(out, FILES_DIR), abspath(out, "data"), abspath(out, ".")}
    d = start
    while d not in roots and d != d.parent:
        try:
            d.rmdir()  # fails (and we stop) if the folder isn't empty
        except OSError:
            return
        d = d.parent


def reorganize(store: Store, naming: Naming, apply: bool = False, limit: int | None = None,
               examples: int = 20) -> dict:
    out = store.out
    res = {"checked": 0, "unchanged": 0, "move": 0, "db_only": 0, "collision_renamed": 0,
           "missing": 0, "extra_created": 0, "old_extras_left": 0, "examples": [], "applied": apply}
    audit = None
    if apply:
        sweep_part_files(out)
        (out / "logs").mkdir(exist_ok=True)
        res["audit_csv"] = str(out / "logs" / f"reorganize-{time.strftime('%Y%m%d-%H%M%S')}.csv")
        audit_f = open(res["audit_csv"], "w", newline="", encoding="utf-8")
        audit = csv.writer(audit_f)
        audit.writerow(["post_id", "action", "old_path", "new_path"])
    emit("phase", name="reorganizing" if apply else "reorganize (dry run)")
    last = 0
    try:
        while limit is None or res["checked"] < limit:
            rows = store.db.execute("SELECT id, md5, ext, file_path FROM posts WHERE file_status='ok' AND id > ? "
                                    "ORDER BY id LIMIT 500", (last,)).fetchall()
            if not rows:
                break
            for pid, md5, ext, cur in rows:
                if limit is not None and res["checked"] >= limit:
                    break
                control.checkpoint()
                res["checked"] += 1
                last = pid
                cur = cur or legacy_relpath(md5, ext)
                planned = naming.plan(store.post_info(pid), len(str(out)))
                action, new = _one(store, pid, md5, cur, planned[0], apply, res)
                if action != "unchanged" and len(res["examples"]) < examples:
                    res["examples"].append({"id": pid, "action": action, "from": cur, "to": new})
                if audit and action != "unchanged":
                    audit.writerow([pid, action, cur, new])
                if planned[1:]:
                    _extras(store, pid, new, planned[1:], naming, apply, res)
                if apply:
                    store.commit()  # per move: interrupting leaves DB and disk consistent
            emit("reorganize", **{k: v for k, v in res.items() if isinstance(v, int)})
    finally:
        if audit:
            audit_f.close()
    return res


def _one(store, pid, md5, cur, target, apply, res) -> tuple[str, str]:
    out = store.out
    if cur == target or _is_collision_variant(cur, target):
        res["unchanged"] += 1  # already at target (or at target__N because target is taken by another file)
        return "unchanged", cur
    src, dst = abspath(out, cur), abspath(out, target)
    if not src.exists():
        if dst.exists() and md5_of(dst) == md5:  # moved earlier, DB update was interrupted
            res["db_only"] += 1
            if apply:
                store.move_file_record(pid, md5, target)
            return "db_only", target
        res["missing"] += 1
        if apply:  # the next download run fetches it again (to the new layout)
            store.db.execute("UPDATE posts SET file_status='pending', file_path=NULL WHERE id=?", (pid,))
        return "missing", ""
    if dst.exists():
        if md5_of(dst) == md5:  # same content already there: point the DB at it, keep the old file
            res["db_only"] += 1
            if apply:
                store.move_file_record(pid, md5, target)
            return "db_only", target
        target = _free_target(out, target)
        dst = abspath(out, target)
        res["collision_renamed"] += 1
    res["move"] += 1
    if apply:
        dst.parent.mkdir(parents=True, exist_ok=True)
        safe_replace(src, dst)
        store.move_file_record(pid, md5, target)
        _prune_empty_dirs(out, src.parent)
    return "move", target


def _extras(store, pid, primary, wanted, naming, apply, res) -> None:
    have = set(store.extra_paths(pid))
    res["old_extras_left"] += len(have - set(wanted))  # left in place, never deleted
    for rel in wanted:
        if rel in have and abspath(store.out, rel).exists():
            continue
        res["extra_created"] += 1
        if apply:
            link_or_copy(abspath(store.out, primary), abspath(store.out, rel),
                         "hardlink" if naming.multi_director == "hardlink" else "copy")
            store.db.execute("INSERT OR IGNORE INTO extra_files VALUES(?,?)", (pid, rel))
