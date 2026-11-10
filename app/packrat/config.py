"""Settings resolution: command line > environment variables > config file > built-in defaults.

Config file (INI), first found of: --config PATH, $PACKRAT_CONFIG, ./packrat.ini, ./e6ai-archiver.ini (an
e6ai-archiver 1.x/2.0 file, read as it is), %APPDATA%\\Packrat\\config.ini (Windows) or
~/.config/packrat/config.ini.
Environment variables are PACKRAT_<NAME>; the old E6AI_<NAME> names are still read (3.0).

    [packrat]                        ; e6ai-archiver.ini files use [e6ai-archiver]; both are read
    username = your_e6ai_name
    out = D:\\e6ai-archive
    rate = 1.0                       ; API requests per second
    workers = 16                     ; parallel file downloads (1-64)
    check_md5 = no                   ; md5 while downloading (verify checks afterwards)
    min_free_gb = 20
    name_template = default          ; or a preset (detailed, short, minimal, md5) or e.g. {id}_{director}.{ext}
    folder_template = {director}/{rating}
    multi_director = first           ; first | multi | copy | hardlink
    gui = yes
    order = popular                  ; default | popular | favorites | newest | oldest | smallest-first | balanced
    rating = s,q                     ; optional filters: min_score, min_favs, director, filter_tags,
    limit_gb = 200                   ;   max_size_mb, exclude_ext, top
"""
from __future__ import annotations

import configparser
import logging
import os
from pathlib import Path

from .client import DEFAULT_MAX_OUTAGE_H, DEFAULT_RATE

log = logging.getLogger("packrat")
SECTION = "packrat"
LEGACY_SECTION = "e6ai-archiver"   # 1.x / 2.0 files
ENV_PREFIX, LEGACY_ENV_PREFIX = "PACKRAT_", "E6AI_"

# name: (env var, type, default)
SETTINGS = {
    "out": ("PACKRAT_OUT", str, "archive"),
    "site": ("PACKRAT_SITE", str, "e6ai"),          # 3.0: whole-site archive: e6ai | e621
    "username": ("PACKRAT_USERNAME", str, None),
    "user_agent": ("PACKRAT_USER_AGENT", str, None),
    "rate": ("PACKRAT_RATE", float, DEFAULT_RATE),
    "min_free_gb": ("PACKRAT_MIN_FREE_GB", float, 5.0),
    "log_file": ("PACKRAT_LOG_FILE", str, None),
    "login": ("PACKRAT_LOGIN", str, None),
    "api_key": ("PACKRAT_API_KEY", str, None),
}
TRUE = {"1", "true", "yes", "on"}
FALSE = {"0", "false", "no", "off", ""}


def _bool(v) -> bool:
    if isinstance(v, bool):
        return v
    v = str(v).strip().lower()
    if v in TRUE:
        return True
    if v in FALSE:
        return False
    raise ValueError(v)


SETTINGS.update({
    "name_template": ("PACKRAT_NAME_TEMPLATE", str, "default"),
    "folder_template": ("PACKRAT_FOLDER_TEMPLATE", str, "director-rating"),
    "multi_director": ("PACKRAT_MULTI_DIRECTOR", str, "first"),
    "director_exclude": ("PACKRAT_DIRECTOR_EXCLUDE", str, None),  # comma-separated; None = built-in list
    "rating_style": ("PACKRAT_RATING_STYLE", str, "word"),
    "max_items": ("PACKRAT_MAX_ITEMS", int, 3),
    "multi_sep": ("PACKRAT_MULTI_SEP", str, ","),
    "name_max": ("PACKRAT_NAME_MAX", int, 0),
    "max_path": ("PACKRAT_MAX_PATH", int, 240),
    "max_outage_hours": ("PACKRAT_MAX_OUTAGE_HOURS", float, DEFAULT_MAX_OUTAGE_H),
    "gui": ("PACKRAT_GUI", _bool, False),
    # 1.3 priority order + filters (see priority.py / README "Download order")
    "order": ("PACKRAT_ORDER", str, "default"),
    "min_score": ("PACKRAT_MIN_SCORE", int, None),
    "min_favs": ("PACKRAT_MIN_FAVS", int, None),
    "rating": ("PACKRAT_RATING", str, None),              # e.g. s,q
    "director": ("PACKRAT_DIRECTOR", str, None),          # comma-separated director tags (any)
    "filter_tags": ("PACKRAT_FILTER_TAGS", str, None),    # space/comma list, all required; -tag excludes
    "max_size_mb": ("PACKRAT_MAX_SIZE_MB", float, None),
    "exclude_ext": ("PACKRAT_EXCLUDE_EXT", str, None),    # e.g. webm,mp4
    "limit_gb": ("PACKRAT_LIMIT_GB", float, None),
    "top": ("PACKRAT_TOP", int, None),
    "exports": ("PACKRAT_EXPORTS", str, "all"),           # all | core | comma list of export names
    "api_json": ("PACKRAT_API_JSON", _bool, True),
    "login_retry": ("PACKRAT_LOGIN_RETRY", _bool, True),  # signed in: re-fetch posts saved without a file URL
    "gui_autostart": ("PACKRAT_GUI_AUTOSTART", _bool, False),
    # 1.6 parallel downloads + checksums
    "workers": ("PACKRAT_WORKERS", int, 16),
    "check_md5": ("PACKRAT_CHECK_MD5", _bool, False),
    "verify_after": ("PACKRAT_VERIFY_AFTER", _bool, True),
    "verify_action": ("PACKRAT_VERIFY_ACTION", str, "delete"),
    "hash_workers": ("PACKRAT_HASH_WORKERS", int, 0),
    # 1.7 completeness report
    "report_after": ("PACKRAT_REPORT_AFTER", _bool, True),
    # 1.8 tags export pulled automatically (director folders / per-director report counts)
    "fetch_tags": ("PACKRAT_FETCH_TAGS", _bool, True),
    # 1.9 low disk space: pause (wait for space) or stop (exit 4); failure log size cap (0 = never trim)
    "on_low_disk": ("PACKRAT_ON_LOW_DISK", str, "pause"),
    "failure_log_max_mb": ("PACKRAT_FAILURE_LOG_MAX_MB", float, 10.0),
})


def default_config_paths() -> list[Path]:
    paths = [Path("packrat.ini"), Path("e6ai-archiver.ini")]
    if os.name == "nt" and os.environ.get("APPDATA"):
        paths.append(Path(os.environ["APPDATA"]) / "Packrat" / "config.ini")
        paths.append(Path(os.environ["APPDATA"]) / "e6ai-archiver" / "config.ini")
    else:
        paths.append(Path.home() / ".config" / "packrat" / "config.ini")
        paths.append(Path.home() / ".config" / "e6ai-archiver" / "config.ini")
    return paths


def env_get(env, var: str):
    """PACKRAT_X, else the 1.x/2.0 name E6AI_X (so old scheduled tasks and shells keep working)."""
    if env.get(var):
        return env[var], var
    if var.startswith(ENV_PREFIX):
        old = LEGACY_ENV_PREFIX + var[len(ENV_PREFIX):]
        if env.get(old):
            return env[old], old
    return None, None


def load_config_file(explicit: str | None, env=os.environ) -> tuple[dict, Path | None]:
    path = explicit or env_get(env, "PACKRAT_CONFIG")[0]
    if path:
        p = Path(path)
        if not p.is_file():
            raise ValueError(f"config file not found: {p}")
        candidates = [p]
    else:
        candidates = [p for p in default_config_paths() if p.is_file()]
    if not candidates:
        return {}, None
    cp = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";", "#"))
    try:
        with open(candidates[0], encoding="utf-8-sig") as f:  # -sig: tolerate Notepad's BOM
            cp.read_file(f)
    except UnicodeDecodeError as e:
        raise ValueError(f"{candidates[0]}: not valid UTF-8 (save it as UTF-8 in Notepad): {e}") from None
    except configparser.Error as e:
        raise ValueError(f"{candidates[0]}: cannot parse config file: {e}".replace("\n", " ")) from None
    section = SECTION if cp.has_section(SECTION) else LEGACY_SECTION if cp.has_section(LEGACY_SECTION) else None
    if section is None:
        raise ValueError(f"{candidates[0]}: missing [{SECTION}] section")
    values = {k.replace("-", "_"): v.strip() for k, v in cp.items(section)}
    unknown = set(values) - set(SETTINGS) - {"no_keep_awake"}
    if unknown:
        raise ValueError(f"{candidates[0]}: unknown setting(s): {', '.join(sorted(unknown))}")
    if values.get("api_key"):
        log.warning("api_key is stored in %s; prefer the PACKRAT_API_KEY environment variable (or the window's Remember API key)", candidates[0])
    return values, candidates[0]


def resolve(args, env=os.environ) -> Path | None:
    """Fill None attributes on argparse namespace `args` from env, then config, then defaults."""
    if getattr(args, "no_config", False):
        file_vals, path = {}, None
    else:
        file_vals, path = load_config_file(getattr(args, "config", None), env)
    for name, (var, typ, default) in SETTINGS.items():
        if getattr(args, name, None) is not None:
            continue
        raw, src = env_get(env, var)
        if raw is None and file_vals.get(name):
            raw, src = file_vals[name], str(path)
        if raw is None:
            setattr(args, name, default)
            continue
        try:
            setattr(args, name, typ(raw))
        except (TypeError, ValueError):
            raise ValueError(f"invalid value for {name} from {src}: {raw!r}") from None
    if not getattr(args, "no_keep_awake", False):
        v = env_get(env, "PACKRAT_NO_KEEP_AWAKE")[0] or file_vals.get("no_keep_awake") or ""
        args.no_keep_awake = v.strip().lower() in TRUE
    return path
