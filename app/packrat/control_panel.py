"""Control panel (1.4, sign-in in 1.5): the window is the main way to use the archiver; the work runs in the CLI.

This module is the part without Tk, so all of it is testable headless:

* OPTIONS / MODES: one row per command-line option (flag, widget kind, tab, label, help, default,
  which subcommands accept it). tests/test_v14.py walks the real argparse parser and fails if a CLI
  option is neither here nor in OMITTED, so the panel cannot silently fall behind the CLI.
* build_command(): panel state -> the exact argv of `python -m packrat ...` (also shown in the
  window as a copyable command line). Every setting is passed explicitly with --no-config, so the
  command does the same thing when pasted into a terminal.
* validate() / path_warnings(): inline errors and warnings before Start.
* load_settings() / save_settings(): packrat.ini ([packrat] keys the CLI reads too, and
  a [gui] section the CLI ignores). The API key is never written and never on a command line.
* CliRunner: starts the child (no console window on Windows), reads stdout/stderr in threads into a
  queue (the window polls it with after(), so it never freezes), and stops it through a control file.
"""
from __future__ import annotations

import configparser
import json
import os
import queue
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .client import API_RATE_GUIDE, BROWSER_UA_MARKERS, DEFAULT_RATE
from .config import SECTION, SETTINGS, default_config_paths, env_get  # noqa: F401
from .events import CONTROL_WORDS, PROGRESS_PREFIX, write_control_word
from .naming import FOLDER_PRESETS, MULTI_DIRECTOR, NAME_PRESETS
from .theme import normalize as normalize_theme

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GUI_SECTION = "gui"
TABS = ("Basics", "Download order & filters", "Naming & folders", "Advanced")
META_KINDS = ("artists", "pools", "tags", "wiki_pages")
ALL = None  # Opt.cmds: global option (accepted by every subcommand)
PRIO = frozenset({"run", "full", "download", "preview", "sync-exports", "report", "retry-failed"})
GUI_EXTRAS = {"theme": "system", "notify": True}  # 1.9: panel-only [gui] settings (not CLI options)
FORCE_STOP_AFTER_S = 45  # Stop -> offer "Force stop" if the child has not exited after this long


@dataclass(frozen=True)
class Opt:
    key: str                    # panel state key (also the [gui] key for panel-only settings)
    flag: str | dict            # CLI flag, or {subcommand: flag} where the spelling differs
    kind: str                   # flag | neg | int | float | text | choice | combo | dir | file | savefile
                                # | ratings | kinds | secret
    tab: str
    group: str
    label: str
    help: str
    default: object = ""
    cmds: frozenset | None = ALL
    dests: tuple = ()           # argparse dest(s) this covers (default: derived from the flag)
    choices: tuple = ()
    lo: float | None = None
    hi: float | None = None
    step: float | None = None
    ini: str | None = None      # key in [packrat] the CLI reads; None = [gui] section; "" = never saved

    def flag_for(self, cmd: str) -> str:
        return self.flag.get(cmd) if isinstance(self.flag, dict) else self.flag

    def dest_names(self) -> tuple:
        if self.dests:
            return self.dests
        flags = self.flag.values() if isinstance(self.flag, dict) else [self.flag]
        return tuple(sorted({f.lstrip("-").replace("-", "_") for f in flags if f}))

    def applies(self, cmd: str) -> bool:
        return self.cmds is ALL or cmd in self.cmds


B, O, N, A = TABS
SIGNIN = "Sign in to the site (optional)"
API_KEY_PAGE = "https://e6ai.net/users/home"  # e6ai's account page; 3.0: api_key_page(site) for the chosen site
KEYRING_SERVICE = "e6ai-archiver"             # e6ai keys remembered by 1.5-2.0 (still read); 3.0 adds per site
SITE_CHOICES = ("e6ai", "e621")


def api_key_page(site: str | None) -> str:
    from . import siteprofile
    try:
        return siteprofile.get(site).api_key_page
    except ValueError:
        return API_KEY_PAGE


def keyring_service(site: str | None) -> str:
    return KEYRING_SERVICE if (site or "e6ai") == "e6ai" else f"packrat-{site}"
_D = {k: v[2] for k, v in SETTINGS.items()}  # CLI defaults, so "default" means the same in both places
OPTIONS: tuple[Opt, ...] = (
    # ---- Basics -------------------------------------------------------------------------------
    Opt("site", "--site", "choice", B, "What to do", "Site",
        "Which site this archive is for. e6ai and e621 run the same software, so everything here works the same "
        "on both (daily exports, checksums, completeness report). One archive folder holds one site: pick a "
        "different archive folder for each site. e6ai closes at the end of November 2026. For any other site "
        "(Danbooru, Gelbooru, Pixiv ...) use the Browse and Downloads tabs.", "e6ai", choices=SITE_CHOICES,
        ini="site"),
    Opt("username", "--username", "text", B, "Who you are", "Your username on the site",
        "Your account name on the chosen site. It goes into the User-Agent, e.g. 'Packrat/3.0 (by NAME on "
        "e6ai)', so site staff can contact you. Required for anything that talks to the site.", "",
        ini="username"),
    Opt("out", "--out", "dir", B, "Where to save", "Archive folder",
        "Folder for the archive: files/, the database, exports and logs. Use a local drive with plenty of "
        "space (a full e6ai archive is hundreds of GB, e621 many TB), not OneDrive/Dropbox or a network share. "
        "One folder per site.",
        _D["out"], ini="out"),
    Opt("workers", "--workers", "int", B, "Speed", "Parallel downloads",
        "How many files download at the same time from the static file server (1-64, default 16). "
        "Downloads are limited by your bandwidth, not a request rate. If the file server answers "
        "403 / 429 / 503 that file is marked failed and that download slot backs off; after 20 "
        "403s in a row the run stops (exit code 3).", str(_D["workers"]), lo=1, hi=64, step=1, ini="workers"),
    Opt("rate", "--rate", "float", B, "Speed", "API requests per second",
        f"Requests per second for API calls (post lists, metadata). File downloads are not counted here. "
        f"Default {DEFAULT_RATE:g}. Note: the e621-family API documentation asks for at most about "
        f"{API_RATE_GUIDE:g} requests/s.", str(_D["rate"]), lo=0.1, hi=1000, step=0.5, ini="rate"),
    Opt("check_md5", "--check-md5", "flag", B, "Checksums", "Check md5 while downloading",
        "Hash every file as it downloads and re-download it on a mismatch. Off by default: sizes are "
        "checked while downloading and 'Verify checksums' (or 'Verify when finished') checks md5s "
        "afterwards.", False, ini="check_md5"),
    Opt("verify_after", "--no-verify-after", "neg", B, "Checksums", "Verify when finished (full archive)",
        "After the full archive's downloads, check the md5 of every file not checked yet and re-download "
        "bad ones.", True, cmds=frozenset({"full"}), dests=("verify_after",), ini="verify_after"),
    Opt("verify_action", "--verify-action", "choice", B, "Checksums", "Bad files (wrong md5 / truncated / empty)",
        "delete: remove them (they are downloaded again). quarantine: move them to <archive>/quarantine/ "
        "first, to inspect later.", _D["verify_action"], cmds=frozenset({"full", "verify"}),
        choices=("delete", "quarantine"), ini="verify_action"),
    Opt("report_after", "--no-report-after", "neg", B, "Checksums", "Completeness report when finished",
        "After the full archive (and verify) or 'Retry failed files', compare the archive with the latest "
        "posts export and write completeness-report-*.txt/.html and missing.csv in the archive folder "
        "('Open report' shows it).",
        True, cmds=frozenset({"full", "retry-failed"}), dests=("report_after",), ini="report_after"),
    Opt("redownload", "--redownload", "flag", B, "Checksums", "Re-download bad / missing files (verify)",
        "Verify mode: after checking, download the files that were bad or missing. Untick for a check "
        "without network access.", True, cmds=frozenset({"verify"})),
    Opt("min_free_gb", "--min-free-gb", "float", B, "Disk and power", "Keep free on the drive (GB)",
        "Never let free disk space drop below this many GB. When the next file would, downloads pause (or "
        "stop, see below) until space is freed. Start also warns first when the files still to download "
        "will not fit.",
        str(_D["min_free_gb"]), lo=0, hi=100000, step=1, ini="min_free_gb"),
    Opt("on_low_disk", "--on-low-disk", "choice", B, "Disk and power", "When the drive is nearly full",
        "pause (default): downloads wait, you get a notification, and they continue by themselves as soon as "
        "enough space is free (or press Resume to check again right away). stop: end the run cleanly with "
        "exit code 4 (as before 1.9); Start again later to resume.",
        _D["on_low_disk"], choices=("pause", "stop"), ini="on_low_disk"),
    Opt("keep_awake", "--no-keep-awake", "neg", B, "Disk and power", "Keep the PC awake while running",
        "Windows: prevents sleep while a network run is active (the screen may still turn off). "
        "Untick to let the PC sleep (the run pauses while asleep and continues afterwards).",
        True, dests=("no_keep_awake",), ini="no_keep_awake"),
    # ---- Download order & filters --------------------------------------------------------------
    Opt("order", "--order", "choice", O, "Order", "Download order",
        "default/newest: newest first. popular: score, then favorites. favorites: favorites, then score. "
        "oldest. smallest-first. balanced: 0.5 score + 0.3 favorites + 0.2 small-file (percentiles).",
        _D["order"], cmds=PRIO, choices=("default", "popular", "favorites", "newest", "oldest",
                                         "smallest-first", "balanced"), ini="order"),
    Opt("min_score", "--min-score", "int", O, "Filters (local metadata, no extra requests)", "Minimum score",
        "Only posts with score >= N (a missing score counts as 0). Blank = no limit.", "", cmds=PRIO,
        lo=-100000, hi=10**9, ini="min_score"),
    Opt("min_favs", "--min-favs", "int", O, "Filters (local metadata, no extra requests)", "Minimum favorites",
        "Only posts with at least N favorites. Blank = no limit.", "", cmds=PRIO, lo=0, hi=10**9, ini="min_favs"),
    Opt("rating", "--rating", "ratings", O, "Filters (local metadata, no extra requests)", "Ratings",
        "Only these ratings. All ticked (or none) = no filter.", "", cmds=PRIO, ini="rating"),
    Opt("director", "--director", "text", O, "Filters (local metadata, no extra requests)", "Directors / artists",
        "Only posts by any of these directors (e6ai) or artists (e621), comma-separated (spaces become _). "
        "Blank = everyone.", "",
        cmds=PRIO, ini="director"),
    Opt("filter_tags", "--filter-tags", "text", O, "Filters (local metadata, no extra requests)",
        "Must have tags", "Only posts that have all of these tags (space or comma separated; -tag excludes), "
        "checked against the local database.", "", cmds=PRIO - {"sync-exports"}, ini="filter_tags"),
    Opt("max_size_mb", "--max-size-mb", "float", O, "Filters (local metadata, no extra requests)",
        "Skip files larger than (MiB)", "Skip files bigger than this. Blank = no limit.", "", cmds=PRIO,
        lo=0.001, hi=10**7, ini="max_size_mb"),
    Opt("exclude_ext", "--exclude-ext", "text", O, "Filters (local metadata, no extra requests)",
        "Skip file types", "Comma-separated extensions to skip, e.g. webm,mp4.", "", cmds=PRIO, ini="exclude_ext"),
    Opt("limit_gb", "--limit-gb", "float", O, "Stop after (tier)", "Only the first N GiB",
        "Download only the first N GiB of the ordered queue, then stop. Blank = everything.", "", cmds=PRIO,
        lo=0.001, hi=10**7, ini="limit_gb"),
    Opt("top", "--top", "int", O, "Stop after (tier)", "Only the first N files",
        "Download only the first N files of the ordered queue. Blank = everything.", "", cmds=PRIO,
        lo=1, hi=10**9, ini="top"),
    Opt("api_json", "--no-api-json", "neg", O, "Downloads", "Refresh API metadata before downloading",
        "Before downloading files that came from the daily export, fetch their current API JSON in batches "
        "of 100 (1 request per 100 files). Untick to skip.", True, cmds=PRIO, dests=("api_json",),
        ini="api_json"),
    Opt("retry_failed", "--retry-failed", "flag", O, "Downloads", "Retry missing / failed files first",
        "Re-queue files that ended as missing (404), md5 mismatch or failed, then download them.", False,
        cmds=frozenset({"download"})),
    Opt("dry_run", "--dry-run", "flag", O, "Downloads", "Dry run (no network)",
        "Show what would be downloaded (files, size, free space, minimum time) without any request.", False,
        cmds=frozenset({"run", "download"})),
    Opt("max_posts", "--max-posts", "int", O, "API crawl (update / full / recheck)", "Stop after N posts",
        "Stop after saving this many posts from the API in this run. Blank = no limit.", "",
        cmds=frozenset({"run", "full", "recheck"}), lo=1, hi=10**9),
    Opt("tags", "--tags", "text", O, "API crawl (update / full / recheck)", "API tag search (update)",
        "Update mode only: restrict the API crawl to this tag search (each query has its own checkpoint).", "",
        cmds=frozenset({"run"})),
    Opt("since_id", "--since-id", "int", O, "API crawl (update / full / recheck)", "Only posts after id",
        "Update mode only: only archive posts with an id greater than this.", "", cmds=frozenset({"run"}),
        lo=0, hi=10**10),
    Opt("include_deleted", "--include-deleted", "flag", O, "API crawl (update / full / recheck)",
        "Also record deleted posts", "Update mode only: add status:any so deleted posts' metadata is "
        "recorded too (their files are usually gone).", False, cmds=frozenset({"run"})),
    Opt("metadata_only", "--metadata-only", "flag", O, "API crawl (update / full / recheck)",
        "Metadata only (no files)", "Update mode only: save post metadata and skip file downloads.", False,
        cmds=frozenset({"run"})),
    # ---- Naming & folders ---------------------------------------------------------------------
    Opt("name_template", "--name-template", "combo", N, "File names", "File name",
        "A preset (default = {source_name}_{id}.{ext}, detailed, full, short, minimal, md5) or your own "
        "template, e.g. {id}_{director}.{ext}. Fields: id md5 ext source_name rating score fav_count width "
        "height year month day date director (= artist on e621) character species franchise (= copyright) "
        "... ({field:3} keeps at most 3 values). For Grabber-style %token% names use the Browse tabs.",
        _D["name_template"], choices=tuple(NAME_PRESETS), dests=("name_template", "name_template_preset"),
        ini="name_template"),
    Opt("folder_template", "--folder-template", "combo", N, "Folders", "Folders",
        "A preset (director-rating, director, director-year, year-month, franchise, species, rating, ext, "
        "flat, md5) or your own, e.g. {director}/{year}. 'flat' = everything in files/.",
        _D["folder_template"], choices=tuple(FOLDER_PRESETS), dests=("folder_template", "folder_template_preset"),
        ini="folder_template"),
    Opt("multi_director", "--multi-director", "choice", N, "Folders", "Posts with several directors / artists",
        "first: folder of the first director. multi: one joined folder (a,b). copy / hardlink: one copy in "
        "every director's folder (hardlink uses no extra space on NTFS).", _D["multi_director"],
        choices=MULTI_DIRECTOR, ini="multi_director"),
    Opt("director_exclude", "--director-exclude", "text", N, "Folders", "Never use as director / artist",
        "Director-category tags that are warnings, not people (comma-separated). Blank = built-in list "
        "(conditional_dnp, avoid_posting, sound_warning, ...).", "", ini="director_exclude"),
    Opt("rating_style", "--rating-style", "choice", N, "Fields", "Rating written as",
        "word: safe / questionable / explicit. letter: s / q / e.", _D["rating_style"],
        choices=("word", "letter"), ini="rating_style"),
    Opt("max_items", "--name-max-items", "int", N, "Fields", "Max tags per field",
        "How many tags a multi-value field like {character} or {species} lists (1-50).", str(_D["max_items"]),
        lo=1, hi=50, dests=("max_items",), ini="max_items"),
    Opt("multi_sep", "--multi-sep", "text", N, "Fields", "Separator between tags",
        "Joins several values in one field, e.g. ',' gives krystal,fox. No <>:\"/\\|?* characters.",
        _D["multi_sep"], ini="multi_sep"),
    Opt("name_max", "--name-max", "int", N, "Length limits", "Max file name length",
        "0 = automatic (60 for source-name templates, 120 otherwise); else 30-200 characters. Long names "
        "are shortened but always keep the post id and extension.", str(_D["name_max"]), lo=0, hi=200,
        ini="name_max"),
    Opt("max_path", "--max-path", "int", N, "Length limits", "Max full path length",
        "Whole path including the archive folder (default 240, under the classic Windows 260 limit).",
        str(_D["max_path"]), lo=100, hi=32000, ini="max_path"),
    # ---- Basics: sign in (built by panel_ui._build_signin) -------------------------------------
    Opt("login", "--login", "text", B, SIGNIN, "Login name",
        "Your login name on the site. Blank = the username above. e6ai and e621 do not let tools sign in "
        "with a password: they use your login name plus an API key.", "", ini="login"),
    Opt("api_key", "", "secret", B, SIGNIN, "API key",
        "Create it on the site: account page > 'Manage API Keys' (older versions: 'Manage API Access'). "
        "It is not your password. Passed to "
        "the archiver only in the PACKRAT_API_KEY environment variable: never on the command line, never in "
        "the settings file.", "", dests=("api_key",), ini=""),
    Opt("login_retry", "--no-login-retry", "neg", B, SIGNIN, "When signed in, re-check posts that needed sign-in",
        "At the start of Full / Update / Download, re-fetch posts that were saved without a file URL "
        "(hidden from anonymous visitors), 100 per request, and queue the ones now visible.", True,
        cmds=frozenset({"run", "full", "download"}), dests=("login_retry",), ini="login_retry"),
    # ---- Advanced -----------------------------------------------------------------------------
    Opt("user_agent", "--user-agent", "text", A, "Identity", "Custom User-Agent",
        "Leave blank to use the standard one built from your username. Never a browser User-Agent "
        "(refused: the site bans those for tools).", "", ini="user_agent"),
    Opt("max_outage_hours", "--max-outage-hours", "float", A, "Network", "Wait for outages up to (hours)",
        "If the network or the site is down, keep retrying (with growing pauses) this long before giving up.",
        str(_D["max_outage_hours"]), lo=0.01, hi=720, ini="max_outage_hours"),
    Opt("verbose", "--verbose", "flag", A, "Logging", "Verbose log (debug)",
        "Log every request and decision. Useful when reporting a problem.", False),
    Opt("log_file", "--log-file", "savefile", A, "Logging", "Log file",
        "Blank = <archive folder>/logs/control-panel.log (appended). The 'Open log' button opens it.", "",
        ini="log_file"),
    Opt("failure_log_max_mb", "--failure-log-max-mb", "float", A, "Logging", "Trim the failure log above (MB)",
        "failed-downloads.csv / .log in the archive folder grow with every failure. Above this size they are "
        "trimmed at the end of a run: entries of files that downloaded later (or are no longer failed) move to "
        "failed-downloads-archive.csv.gz / .log.gz - nothing is deleted - and entries of files that are still "
        "failed always stay. 0 = never trim.",
        str(_D["failure_log_max_mb"]), lo=0, hi=100000, step=5, ini="failure_log_max_mb"),
    Opt("exports", "--exports", "combo", A, "Daily exports (full / sync)", "Export bundle",
        "all = every daily export; core = posts, tags, pools; or a comma list of export names.",
        _D["exports"], cmds=frozenset({"full", "sync-exports"}), choices=("all", "core"), ini="exports"),
    Opt("refresh_exports", {"full": "--refresh-export", "sync-exports": "--force"}, "flag", A,
        "Daily exports (full / sync)", "Check the exports now",
        "Check / download the daily exports even if they were checked in the last 20 hours.", False,
        cmds=frozenset({"full", "sync-exports"}), dests=("refresh_export", "force")),
    Opt("exports_download", "--download", "text", A, "Daily exports (full / sync)",
        "Download exports (exports mode)", "Exports mode: names to download (posts, tags, pools, ... or all). "
        "Blank = only list what is available.", "", cmds=frozenset({"exports"}), dests=("download",)),
    Opt("seed_csv", "--csv", "file", A, "Seed from export file", "posts.csv.gz",
        "Seed mode: posts export to load. Blank = <archive>/db_export/posts.csv.gz.", "",
        cmds=frozenset({"seed-from-export"}), dests=("csv",)),
    Opt("tags_csv", "--tags-csv", "file", A, "Seed from export file", "tags.csv.gz",
        "Seed mode: tags export for tag categories. Blank = <archive>/db_export/tags.csv.gz if present.", "",
        cmds=frozenset({"seed-from-export"})),
    Opt("keep_backfill", "--keep-backfill", "flag", A, "Seed from export file", "Keep the API backfill",
        "Seed mode: don't mark the API backfill as done (if you also want full API JSON for old posts).",
        False, cmds=frozenset({"seed-from-export"})),
    Opt("report_export", "--export", "file", A, "Report", "Compare with this posts export",
        "Report mode: a posts-YYYY-MM-DD.csv.gz to compare with. Blank = the archive's own "
        "db_export/posts.csv.gz (kept current by the full archive / Sync).", "", cmds=frozenset({"report"}),
        dests=("export",)),
    Opt("report_no_disk_check", "--no-disk-check", "flag", A, "Report", "Trust the database (skip the disk scan)",
        "Report mode: don't check that saved files are still on disk and don't look for extra files. Faster "
        "on a slow or network drive.", False, cmds=frozenset({"report"}), dests=("no_disk_check",)),
    Opt("fetch_tags", "--no-fetch-tags", "neg", A, "Report", "Fetch the tags export if it is missing / old",
        "Report and Retry failed files: if the archive has no tags export, or it is older than the posts "
        "export, download it first (anonymous, sha256-verified, about 6 MB) so files and counts are filed under "
        "their director instead of _unknown_director. Untick for no network at all.", True,
        cmds=frozenset({"report", "retry-failed"}), dests=("fetch_tags",), ini="fetch_tags"),
    Opt("retry_reason", "--reason", "combo", A, "Retry failed files", "Only these failures",
        "Retry failed files: which failures to re-attempt. Blank = all. Comma-separated: 403, 404, 429, "
        "503, http (any HTTP error), network, md5 (md5 or size mismatch), size, verify, unknown (failed "
        "before 1.7), or an exact reason such as http_403.", "",
        cmds=frozenset({"retry-failed"}), choices=("", "403", "404", "http", "network", "md5", "network,md5"),
        dests=("reason",)),
    Opt("retry_from_csv", "--from-csv", "file", A, "Retry failed files", "Only the posts in this CSV",
        "Retry failed files: re-attempt the posts listed in a CSV with a post_id column, e.g. missing.csv "
        "(from the report) or failed-downloads.csv. Blank = every file currently marked failed.", "",
        cmds=frozenset({"retry-failed"}), dests=("from_csv",)),
    Opt("report_json", "--json", "flag", A, "Report", "Machine-readable (JSON) output",
        "Report mode: print the full result as JSON in the log (the report files are written either way).",
        False, cmds=frozenset({"report"}), dests=("json",)),
    Opt("preview_count", "--count", "int", A, "Preview", "Rows to show",
        "Preview mode: how many of the next files to list.", "20", cmds=frozenset({"preview"}), lo=1, hi=100000,
        dests=("limit",)),
    Opt("preview_status", "--status", "choice", A, "Preview", "Files to list",
        "any: everything in the queue; pending: not yet downloaded; ok: already downloaded.", "any",
        cmds=frozenset({"preview"}), choices=("any", "pending", "ok"), dests=("status",)),
    Opt("assume_mbps", "--assume-mbps", "float", A, "Preview", "Assumed bandwidth (Mbit/s)",
        "Preview mode: download speed used for the time estimate.", "50", cmds=frozenset({"preview"}),
        lo=0.1, hi=100000),
    Opt("preview_json", "--json", "flag", A, "Preview", "Machine-readable (JSON) output",
        "Preview mode: print JSON instead of a table.", False, cmds=frozenset({"preview"}), dests=("json",)),
    Opt("reorg_limit", "--limit", "int", A, "Reorganize", "Only check the first N files",
        "Reorganize modes: limit to the first N saved files (blank = all).", "", cmds=frozenset({"reorganize"}),
        lo=1, hi=10**9, dests=("limit",)),
    Opt("meta_kinds", "", "kinds", A, "Other metadata (meta mode)", "Fetch",
        "Meta mode: which metadata to fetch (resumable).", "pools,tags", cmds=frozenset({"meta"}),
        dests=("kinds",)),
    Opt("meta_max_items", "--max-items", "int", A, "Other metadata (meta mode)", "Stop after N items",
        "Meta mode: stop after N items per kind. Blank = all.", "", cmds=frozenset({"meta"}), lo=1, hi=10**9,
        dests=("meta_max_items",)),
)
OPT = {o.key: o for o in OPTIONS}
DEFAULTS = {o.key: o.default for o in OPTIONS}

# argparse dests the panel sets on purpose in another way, or leaves out (with the reason)
OMITTED = {
    "cmd": "chosen with the 'What to do' list (Mode)",
    "config": "the panel reads/writes packrat.ini itself and passes every value explicitly (--no-config)",
    "no_config": "always passed by the panel, so the command is self-contained",
    "gui": "the old in-process progress window; the panel is the window now",
    "gui_autostart": "only for the old window's start panel",
    "progress_json": "added at launch: the panel's progress channel (not shown in the command preview)",
    "control_file": "added at launch: how Stop / Pause reach the run (not shown in the command preview)",
    "apply": "Mode 'Reorganize: move files' adds --apply",
    "dry_run@preview": "preview never uses the network; --dry-run is accepted there only for symmetry",
    "help": "argparse help", "version": "argparse --version",
    "hash_workers": "verify uses one hashing thread per CPU core; command line only (--hash-workers)",
    "only_new": "verify: 'Verify when finished' checks only unchecked files; command line only (--only-new)",
    "restart": "verify resumes by default; command line only (--restart starts over)",
}


@dataclass(frozen=True)
class Mode:
    key: str
    label: str
    cmd: str
    fixed: tuple = ()           # extra args the mode always adds
    network: bool = True
    confirm: str = ""           # confirmation question before Start ("" = none)
    help: str = ""

    def fixed_dests(self) -> set:
        return {a.lstrip("-").replace("-", "_") for a in self.fixed if a.startswith("--")}


MODES: tuple[Mode, ...] = (
    Mode("test", "Test run (5 newest posts)", "run", ("--max-posts", "5"),
         help="Fetches the 5 newest posts and their files. A quick check that everything works (~15 requests)."),
    Mode("full", "Full archive (recommended)", "full",
         confirm="Start the FULL archive?\n\nThis downloads the daily exports and then every file in the chosen "
                 "order (hundreds of GB; hours to a day depending on your bandwidth). It is resumable: Stop any "
                 "time and Start again.",
         help="Everything in one resumable command: daily exports, merge, files in your order, then API "
              "catch-up for the newest posts. Run it again to resume or update."),
    Mode("update", "Update: new posts via the API", "run",
         help="Incremental update + resumable backfill through the API, downloading files as it goes."),
    Mode("sync", "Sync the daily exports", "sync-exports",
         help="Refresh the export bundle (at most once per 20 h unless forced), verify it and merge it."),
    Mode("download", "Download pending files", "download",
         help="Download files still pending (e.g. after seeding or metadata-only), in the chosen order."),
    Mode("retry-failed", "Retry failed files only", "retry-failed",
         help="Re-download ONLY the files currently marked failed / missing / md5 mismatch (optionally only "
              "some reasons, or the posts in a CSV such as missing.csv: Advanced tab). Nothing else is "
              "downloaded. Failures are appended to failed-downloads.csv / .log; the report is rewritten."),
    Mode("retry", "Re-queue failed files + download all pending", "download", ("--retry-failed",),
         help="Re-queue files that ended as missing / md5 mismatch / failed, then download them together "
              "with every other pending file, in the chosen order."),
    Mode("dry-run", "Dry run: what would download (no network)", "download", ("--dry-run",), network=False,
         help="No requests: pending files, total size, free space and minimum time at the chosen rate."),
    Mode("preview", "Preview the download order (no network)", "preview", network=False,
         help="The next files in download order with their paths, and tier sizes / times."),
    Mode("stats", "Statistics (no network)", "stats", network=False,
         help="Local statistics and a size estimate."),
    Mode("report", "Completeness report (fetches only the tags export, if needed)", "report", network=False,
         help="Compare the archive with the latest posts export: expected, verified, missing, failed (with the "
              "reason), skipped by your filters, extra files, per director. Writes completeness-report-*.txt/"
              ".html and missing.csv in the archive folder; 'Open report' opens it. If the tags export is "
              "missing or older than the posts export it is downloaded first (anonymous, one file; untick "
              "'Fetch the tags export' on the Advanced tab for no network at all)."),
    Mode("verify", "Verify checksums (re-download bad files)", "verify",
         help="Check the md5 of every saved file against posts.csv / the database (parallel, resumable). "
              "Empty, truncated and mismatched files are deleted (or quarantined) and downloaded again."),
    Mode("reorganize", "Reorganize: show planned moves (no network)", "reorganize", network=False,
         help="Dry run: which saved files would move to match the current naming / folder settings."),
    Mode("reorganize-apply", "Reorganize: move files (no network)", "reorganize", ("--apply",), network=False,
         confirm="Move saved files to match the current naming and folder settings?\n\nRun 'Reorganize: show "
                 "planned moves' first. Moves are journaled and the run can be stopped and resumed.",
         help="Actually move saved files to the current naming / folder layout."),
    Mode("repair-db", "Repair the database (no network)", "repair-db", network=False,
         confirm="Back up the database and rebuild it from the JSONL journals?\n\nOnly needed after a database "
                 "error (exit code 7). No files are downloaded.",
         help="Back up the database and rebuild it from posts.jsonl / files.jsonl."),
    Mode("meta", "Other metadata (pools, tags, wiki, artists)", "meta",
         help="Fetch pools / tags / wiki pages / artists via the API (resumable)."),
    Mode("recheck", "Recheck posts that had no file URL", "recheck",
         help="Re-query posts saved without a file URL (e.g. after adding an API login)."),
    Mode("exports", "List / download daily exports", "exports",
         help="List the daily database exports, optionally download some (verified, no merge)."),
    Mode("seed", "Seed the database from an export file (no network)", "seed-from-export", network=False,
         help="Load a downloaded posts.csv.gz into the database."),
)
MODE = {m.key: m for m in MODES}
DEFAULT_MODE = "test"


def applies(opt: Opt, mode: Mode) -> bool:
    """Is this option used by the mode (and not already fixed by it)?"""
    if not opt.applies(mode.cmd):
        return False
    return not (set(opt.dest_names()) & mode.fixed_dests())


# ---------------------------------------------------------------------------------------------
def _num(opt: Opt, raw):
    """Parse a numeric field: None for blank, else int/float; ValueError with a friendly message."""
    s = str(raw).strip()
    if s == "":
        return None
    try:
        v = int(s) if opt.kind == "int" else float(s.replace(",", "."))
    except ValueError:
        raise ValueError("enter a whole number" if opt.kind == "int" else "enter a number") from None
    if opt.lo is not None and v < opt.lo:
        raise ValueError(f"must be at least {opt.lo:g}")
    if opt.hi is not None and v > opt.hi:
        raise ValueError(f"must be at most {opt.hi:g}")
    return v


def _truthy(v) -> bool:
    return v is True or str(v).strip().lower() in ("1", "true", "yes", "on")


def _fmt_num(v) -> str:
    return str(int(v)) if isinstance(v, float) and v.is_integer() and abs(v) < 1e15 else str(v)


def normalized(state: dict) -> dict:
    """Defaults filled in, strings stripped (no validation)."""
    st = dict(DEFAULTS)
    st.update({k: v for k, v in state.items() if k in DEFAULTS or k == "mode"})
    for k, v in st.items():
        if isinstance(v, str) and k != "multi_sep":
            st[k] = v.strip()
    return st


def _is_default(opt: Opt, v) -> bool:
    d = opt.default
    if opt.kind in ("int", "float"):
        try:
            return _num(opt, v) == (_num(opt, d) if str(d).strip() else None)
        except ValueError:
            return False
    if opt.kind == "ratings":
        return _ratings(v) in ("", "e,q,s")
    return v == d


def _ratings(v) -> str:
    vals = sorted({x.strip().lower() for x in str(v or "").replace(" ", ",").split(",") if x.strip()})
    return ",".join(vals)


def _abs_out(out: str) -> str:
    return os.path.abspath(os.path.expanduser(out or _D["out"]))


def default_log_file(out: str) -> str:
    return os.path.join(_abs_out(out), "logs", "control-panel.log")


def build_command(state: dict, *, python: str | None = None) -> list[str]:
    """Full argv for the child process, e.g. [python, -m, packrat, --no-config, ..., full, ...].

    Global options come first (argparse needs them before the subcommand). Values equal to the CLI
    defaults are left out; --out and --log-file are always absolute. Not included: the API key (it goes
    into the child's environment) and the panel-only --progress-json / --control-file (CliRunner adds them).
    """
    st = normalized(state)
    mode = MODE[st.get("mode") or DEFAULT_MODE]
    args = ["--no-config", "--out", _abs_out(st["out"])]
    if st["username"]:
        args += ["--username", st["username"]]
    args += ["--log-file", os.path.abspath(st["log_file"]) if st["log_file"] else default_log_file(st["out"])]
    tail, positional = [], []
    for opt in OPTIONS:
        if opt.key in ("out", "username", "log_file", "api_key") or not applies(opt, mode):
            continue
        v = st[opt.key]
        target = args if opt.cmds is ALL else tail
        flag = opt.flag_for(mode.cmd)
        if opt.kind == "flag":
            if v is True or str(v).lower() in ("1", "true", "yes", "on"):
                target.append(flag)
        elif opt.kind == "neg":
            if not (v is True or str(v).lower() in ("1", "true", "yes", "on")):
                target.append(flag)
        elif opt.kind == "kinds":
            positional += [k for k in META_KINDS if k in str(v).replace(" ", ",").split(",")]
        elif opt.key == "exports_download":
            if v:
                tail += [flag, *str(v).replace(",", " ").split()]
        elif opt.kind == "ratings":
            r = _ratings(v)
            if r not in ("", "e,q,s"):
                target += [flag, r]
        elif v in ("", None) or _is_default(opt, v):
            continue
        elif opt.kind in ("int", "float"):
            try:
                target += [flag, _fmt_num(_num(opt, v))]
            except ValueError:
                target += [flag, str(v)]  # invalid: validate() reports it; keep the preview honest
        else:
            target += [flag, str(v)]
    exports_dl = [i for i, a in enumerate(tail) if a == "--download"]
    if exports_dl:  # nargs="*" option: keep it last so nothing after it is taken as a name
        i = exports_dl[0]
        n = 1 + len(str(st["exports_download"]).replace(",", " ").split())
        tail = tail[:i] + tail[i + n:] + tail[i:i + n]
    return [python or child_python(), "-m", "packrat", *args, mode.cmd, *positional, *mode.fixed, *tail]


def quote_command(argv: list[str], windows: bool | None = None) -> str:
    """One copyable command line: Windows (cmd.exe) quoting on Windows, POSIX shell quoting elsewhere."""
    windows = os.name == "nt" if windows is None else windows
    if windows:
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def child_python() -> str:
    """python.exe next to pythonw.exe (the panel may run windowless; the child needs real std streams)."""
    exe = sys.executable or "python"
    p = Path(exe)
    if getattr(sys, "frozen", False):
        # Packrat.exe (built with PyInstaller): its console twin runs the commands; it ignores "-m packrat"
        cand = p.with_name("packrat-cli.exe" if os.name == "nt" else "packrat-cli")
        return str(cand if cand.exists() else p)
    if p.name.lower() == "pythonw.exe":
        cand = p.with_name("python.exe")
        if cand.exists():
            return str(cand)
    return exe


def child_env(state: dict, base: dict | None = None) -> dict:
    """Environment for the child: no PACKRAT_* / E6AI_* settings (the command line is the whole truth) except the API
    key, UTF-8 everywhere, unbuffered output, and this project on PYTHONPATH."""
    src = os.environ if base is None else base
    env = {k: v for k, v in src.items() if not k.upper().startswith(("PACKRAT_", "E6AI_"))}
    key = (str(state.get("api_key") or "").strip() or src.get("PACKRAT_API_KEY", "") or src.get("E6AI_API_KEY", ""))
    if key:
        env["PACKRAT_API_KEY"] = key
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    pp = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(PROJECT_ROOT) + (os.pathsep + pp if pp else "")
    return env


# ---------------------------------------------------------------------------------------------
CLOUD_MARKERS = ("onedrive", "dropbox", "google drive", "googledrive", "my drive", "icloud", "box sync",
                 "mega", "pcloud", "sync.com", "nextcloud", "owncloud")


def _drive_type(path: str) -> int | None:
    if os.name != "nt":
        return None
    try:
        import ctypes
        root = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
        return ctypes.windll.kernel32.GetDriveTypeW(root)
    except Exception:
        return None


def path_warnings(path: str) -> list[str]:
    """Problems with an archive folder that are allowed but worth a warning."""
    w = []
    if not path:
        return w
    p = os.path.abspath(os.path.expanduser(path))
    low = p.replace("\\", "/").lower()
    parts = [x for x in low.split("/") if x]
    if any(m in part for part in parts for m in CLOUD_MARKERS):
        w.append("This looks like a cloud-synced folder (OneDrive / Dropbox / Google Drive ...). Syncing "
                 "hundreds of GB and a live SQLite database is slow and can corrupt it: use a plain local "
                 "folder such as D:\\packrat-archive.")
    if path.startswith(("\\\\", "//")) or _drive_type(p) == 4:
        w.append("This is a network location. It works, but it is slower, and a dropped connection stops the "
                 "run (resumable); the database is safest on a local drive.")
    if len(p) > 120:
        w.append(f"The folder path is long ({len(p)} characters); file names get shortened to stay under the "
                 "Windows path limit. A short folder like D:\\e6ai is better.")
    if os.path.isfile(p):
        w.append("This is a file, not a folder.")
    return w


def free_space(path: str) -> int | None:
    from .platform_utils import free_bytes
    try:
        return free_bytes(Path(_abs_out(path)))
    except OSError:
        return None


def validate(state: dict) -> tuple[dict, list[str]]:
    """(errors {key: message}, warnings [message]) for the selected mode. Only options the mode uses are
    checked, so a half-typed filter on another mode never blocks a run."""
    from argparse import Namespace

    from .naming import DEFAULT_DIRECTOR_EXCLUDE, Naming
    from .priority import Spec
    st = normalized(state)
    mode = MODE.get(st.get("mode") or DEFAULT_MODE)
    errors, warnings = {}, []
    if mode is None:
        return {"mode": "choose what to do"}, warnings
    used = [o for o in OPTIONS if applies(o, mode)]
    nums = {}
    for o in used:
        if o.kind in ("int", "float"):
            try:
                nums[o.key] = _num(o, st[o.key])
            except ValueError as e:
                errors[o.key] = str(e)
    if not st["out"]:
        errors["out"] = "choose a folder"
    elif os.path.isfile(_abs_out(st["out"])):
        errors["out"] = "this is a file; choose a folder"
    if st["rate"] == "":
        errors["rate"] = "enter a number of requests per second, e.g. 1"
    network = mode.network and not (mode.cmd == "verify" and not _truthy(st["redownload"]))
    if network:
        if not st["username"] and not st["user_agent"]:
            errors["username"] = "required: it identifies you in the User-Agent"
    if st["username"]:
        if any(c.isspace() for c in st["username"]) or any(c in st["username"] for c in "()\"'<>"):
            errors["username"] = "a username has no spaces, quotes or brackets (use _ for spaces)"
        elif len(st["username"]) > 64:
            errors["username"] = "too long"
    if st["user_agent"]:
        if any(m in st["user_agent"].lower() for m in BROWSER_UA_MARKERS):
            errors["user_agent"] = "browser-like User-Agents are refused (the site bans them for tools)"
        elif "\n" in st["user_agent"] or len(st["user_agent"]) > 200:
            errors["user_agent"] = "one line, at most 200 characters"
    key = st["api_key"]
    if key:
        if any(c.isspace() for c in key):
            errors["api_key"] = "an API key has no spaces (copy it again from the account page)"
        elif key == st["username"] or key == st["login"]:
            errors["api_key"] = "this is your name, not the API key"
        elif not (st["login"] or st["username"]):
            errors["login"] = "fill in the login name (or the username above)"
    if st["login"] and (any(c.isspace() for c in st["login"]) or len(st["login"]) > 64):
        errors["login"] = "a login name has no spaces (use _ for spaces)"
    if st["login"] and not key and network:
        warnings.append("A login name without an API key is not used: this run is signed out.")
    # naming: the same constructor the CLI uses
    if not any(k in errors for k in ("max_items", "name_max", "max_path")):
        ft = st["folder_template"]
        ft = FOLDER_PRESETS.get(ft, ft) if ft not in ("none",) else ""
        ex = DEFAULT_DIRECTOR_EXCLUDE if not st["director_exclude"] else tuple(
            t.strip() for t in st["director_exclude"].split(",") if t.strip())
        try:
            Naming(name_template=NAME_PRESETS.get(st["name_template"], st["name_template"]),
                   folder_template=ft, multi_director=st["multi_director"],
                   max_items=nums.get("max_items") or int(_D["max_items"]), sep=st["multi_sep"],
                   rating_style=st["rating_style"], name_max=nums.get("name_max") or 0,
                   max_path=nums.get("max_path") or int(_D["max_path"]), director_exclude=ex)
        except ValueError as e:
            msg = str(e)
            key = next((k for k in ("folder_template", "multi_sep", "multi_director", "rating_style")
                        if msg.startswith(k)), "name_template")
            if msg.startswith("max_items must be"):   # the only range Opt.lo/hi can't express: 1-29
                key, msg = "name_max", "0 (automatic) or 30-200"
            errors.setdefault(key, msg)
    if mode.cmd in PRIO and not any(o.cmds == PRIO and o.key in errors for o in OPTIONS):
        ns = Namespace(order=st["order"], rating=_ratings(st["rating"]) or None, director=st["director"] or None,
                       filter_tags=st["filter_tags"] or None, exclude_ext=st["exclude_ext"] or None,
                       **{k: nums.get(k) for k in ("min_score", "min_favs", "max_size_mb", "limit_gb", "top")})
        try:
            Spec.from_args(ns)
        except ValueError as e:
            msg = str(e)
            errors.setdefault(next((k for k in ("rating", "order", "top", "limit_gb", "max_size_mb", "min_score",
                                                "min_favs") if k in msg), "order"), msg)
    if mode.key == "meta" and not [k for k in META_KINDS if k in str(st["meta_kinds"]).split(",")]:
        errors["meta_kinds"] = "tick at least one kind"
    for k in ("seed_csv", "tags_csv"):
        if mode.cmd == "seed-from-export" and st[k] and not os.path.isfile(st[k]):
            errors[k] = "file not found"
    if "out" not in errors:
        warnings += path_warnings(st["out"])
    return errors, warnings


# ---------------------------------------------------------------------------------------------
def sample_posts():
    """Example posts for the live name preview (made up, but shaped like real e6ai / e621 posts)."""
    from .naming import PostInfo
    return [
        ("one director, original file name in the source",
         PostInfo(193829, "0f3b5c7a9e1d2f4a6b8c0d1e2f3a4b5c", "png", "s", 812, 1440, 1024, 1536,
                  "2026-05-14T10:22:31.000-04:00",
                  {"director": ["namsi"], "character": ["krystal"], "species": ["fox", "canid"],
                   "franchise": ["star_fox"], "general": ["solo"]}, True, ["https://example.net/art/123_p0.png"])),
        ("two directors, no source",
         PostInfo(161263, "a1b2c3d4e5f60718293a4b5c6d7e8f90", "webm", "q", 95, 210, 1920, 1080,
                  "2025-12-02T18:05:00.000-05:00",
                  {"director": ["alpha_ai", "beta_ai"], "character": [], "species": ["dragon"]}, True, [])),
        ("no director tag",
         PostInfo(2, "ffeeddccbbaa99887766554433221100", "jpg", "e", 3, 5, 512, 512, "2024-01-30T08:00:00.000Z",
                  {"species": ["wolf"]}, True, ["https://example.org/posts/2"])),
    ]


def preview_paths(state: dict) -> list[tuple[str, str]]:
    """[(description, full path)] for the sample posts with the current naming settings; raises ValueError
    (the same message validate() shows) when a template is invalid."""
    from .naming import DEFAULT_DIRECTOR_EXCLUDE, Naming
    st = normalized(state)
    num = lambda k: _num(OPT[k], st[k]) if str(st[k]).strip() else _num(OPT[k], OPT[k].default)  # noqa: E731
    ft = st["folder_template"]
    ft = "" if ft in ("flat", "none") else FOLDER_PRESETS.get(ft, ft)
    ex = DEFAULT_DIRECTOR_EXCLUDE if not st["director_exclude"] else tuple(
        t.strip() for t in st["director_exclude"].split(",") if t.strip())
    nm = Naming(name_template=NAME_PRESETS.get(st["name_template"], st["name_template"]), folder_template=ft,
                multi_director=st["multi_director"], max_items=num("max_items"), sep=st["multi_sep"],
                rating_style=st["rating_style"], name_max=num("name_max"), max_path=num("max_path"),
                director_exclude=ex)
    out = st["out"] or _D["out"]
    win = os.name == "nt" or "\\" in out or (len(out) > 1 and out[1] == ":")
    base = out if win else _abs_out(out)
    res = []
    for desc, info in sample_posts():
        paths = nm.plan(info, len(base))
        rel = paths[0].replace("/", "\\") if win else paths[0]
        extra = f"   (+{len(paths) - 1} more cop{'y' if len(paths) == 2 else 'ies'})" if len(paths) > 1 else ""
        res.append((desc, base.rstrip("\\/") + ("\\" if win else "/") + rel + extra))
    return res


# ---------------------------------------------------------------------------------------------
def settings_path(explicit: str | None = None) -> Path:
    """The ini the panel saves: --config / PACKRAT_CONFIG, else packrat.ini in the install folder (next to
    "Start Packrat.bat"). 3.0: an e6ai-archiver.ini found there (2.0 unzipped over) is only READ until the
    first save (see legacy_settings_source); it is never changed."""
    conf = os.environ.get("PACKRAT_CONFIG") or os.environ.get("E6AI_CONFIG")
    if explicit or conf:
        return Path(explicit or conf)
    from . import paths
    return paths.settings_ini()


def legacy_settings_source(path: Path) -> Path | None:
    """3.0: when packrat.ini does not exist yet, the e6ai-archiver.ini to take the first values from."""
    from . import paths
    if path.is_file() or path != paths.settings_ini():
        return None
    for p in paths.legacy_ini_candidates():
        if p.is_file():
            return p
    return None


def _parser() -> configparser.ConfigParser:
    return configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";", "#"))


def _to_state(opt: Opt, raw: str):
    raw = raw.strip()
    if opt.kind in ("flag", "neg"):
        val = raw.lower() in ("1", "true", "yes", "on")
        return (not val) if opt.key == "keep_awake" else val   # ini stores no_keep_awake
    return raw


def load_settings(path: Path | None = None, env=None) -> tuple[dict, list[str]]:
    """Panel state from defaults < ini [packrat] (or an old [e6ai-archiver]) < PACKRAT_* environment < ini [gui]. Returns (state,
    notes). Unreadable files never stop the panel; the notes say what was skipped."""
    env = os.environ if env is None else env
    path = path or settings_path()
    st = dict(DEFAULTS, mode=DEFAULT_MODE, **GUI_EXTRAS)
    notes = []
    cp = _parser()
    src = path if path.is_file() else legacy_settings_source(path)
    if src is not None and src != path:
        notes.append(f"Settings taken from the e6ai-archiver file {src} (it is not changed); Save settings "
                     f"writes them to {path.name}.")
    if src is not None:
        try:
            with open(src, encoding="utf-8-sig") as f:
                cp.read_file(f)
        except (OSError, UnicodeDecodeError, configparser.Error) as e:
            notes.append(f"Could not read {src} ({str(e).splitlines()[0]}); using defaults.")
            cp = _parser()
    by_ini = {o.ini: o for o in OPTIONS if o.ini}
    sec = SECTION if cp.has_section(SECTION) else "e6ai-archiver"
    if cp.has_section(sec):
        for k, v in cp.items(sec):
            k = k.replace("-", "_")
            if k == "api_key":
                if v.strip():
                    notes.append(f"{path.name} contains api_key; the panel does not show or save it "
                                 "(prefer 'Remember API key on this PC' or the PACKRAT_API_KEY environment variable).")
                continue
            if k in by_ini:
                st[by_ini[k].key] = _to_state(by_ini[k], v)
    for o in OPTIONS:  # environment wins over the file, like the CLI
        var = SETTINGS.get(o.ini, (None,))[0] if o.ini else None
        if o.key == "keep_awake":
            var = "PACKRAT_NO_KEEP_AWAKE"
        val = env_get(env, var)[0] if var else None
        if val:
            st[o.key] = _to_state(o, val)
    if cp.has_section(GUI_SECTION):
        for k, v in cp.items(GUI_SECTION):
            if k == "mode" and v.strip() in MODE:
                st["mode"] = v.strip()
            elif k == "theme":
                st["theme"] = normalize_theme(v)
            elif k == "notify":
                st["notify"] = v.strip().lower() not in ("0", "false", "no", "off")
            elif k in OPT and OPT[k].ini is None:
                st[k] = _to_state(OPT[k], v)
    st["api_key"] = ""  # never loaded from a file; CliRunner passes PACKRAT_API_KEY through from the env
    return st, notes


def _ini_value(opt: Opt, v) -> str:
    if opt.kind in ("flag", "neg"):
        on = v is True or str(v).lower() in ("1", "true", "yes", "on")
        if opt.key == "keep_awake":
            on = not on
        return "true" if on else "false"
    if opt.kind == "ratings":
        return _ratings(v)
    return str(v)


# --- 2.0: upgrading from an earlier install ------------------------------------------------------
INI_NAME = "e6ai-archiver.ini"


def _old_out(ini: Path | None, base: Path) -> Path:
    """Where an install keeps its archive: the ini's `out` (relative = to that install folder, the
    working folder 1.x used), else <install>/archive."""
    v = ""
    if ini and ini.is_file():
        cpr = _parser()
        try:
            with open(ini, encoding="utf-8-sig") as f:
                cpr.read_file(f)
            v = cpr.get(SECTION, "out", fallback="")
        except (OSError, UnicodeDecodeError, configparser.Error):
            v = ""
    p = Path(os.path.expanduser(v.strip() or _D["out"] or "archive"))
    return p if p.is_absolute() else base / p


def install_info(d: str | os.PathLike) -> dict | None:
    """What an e6ai-archiver install folder holds: {"dir", "ini", "out", "has_db"}; None if it has
    neither a settings file nor an archive database."""
    from .store import DB_NAME
    d = Path(d)
    ini = d / INI_NAME
    ini = ini if ini.is_file() else None
    out = _old_out(ini, d)
    has_db = (out / DB_NAME).is_file()
    if not ini and not has_db:
        return None
    return {"dir": str(d), "ini": str(ini) if ini else None, "out": str(out), "has_db": has_db}


def find_previous_installs(here: str | os.PathLike | None = None, env=None) -> list[dict]:
    """Earlier installs to offer 'Import settings' for: $E6AI_PREVIOUS_INSTALL, C:\\e6ai-archiver and
    the usual Windows folders, and e6ai-archiver* folders next to this one (e.g. an older unzip)."""
    env = os.environ if env is None else env
    here = Path(here or os.getcwd()).resolve()
    cands: list[Path] = []
    if env.get("E6AI_PREVIOUS_INSTALL"):
        cands.append(Path(env["E6AI_PREVIOUS_INSTALL"]))
    if os.name == "nt":
        home = Path.home()
        cands += [Path("C:/e6ai-archiver"), home / "e6ai-archiver", home / "Desktop" / "e6ai-archiver",
                  home / "Downloads" / "e6ai-archiver", home / "Documents" / "e6ai-archiver"]
    try:
        for p in sorted(here.parent.iterdir()):
            if p.is_dir() and p.name.lower().startswith("e6ai-archiver"):
                cands += [p, p / "e6ai-archiver"]   # Windows "Extract all" makes a folder inside a folder
    except OSError:
        pass
    seen, out = {here}, []
    for c in cands:
        try:
            r = c.resolve()
        except OSError:
            continue
        if r in seen or not r.is_dir():
            continue
        seen.add(r)
        info = install_info(r)
        if info:
            out.append(info)
    return out


def import_previous_install(src: str | os.PathLike, dest_ini: str | os.PathLike) -> dict:
    """Copy an earlier install's settings into dest_ini and point `out` at that install's archive (as an
    absolute path), so the new copy keeps archiving into the same folder and nothing is downloaded again.
    Nothing in the old install or its archive is changed or moved. An existing dest_ini is kept as
    <name>.before-import-<time>. Raises ValueError if src is not an e6ai-archiver install."""
    import shutil
    info = install_info(src)
    if not info:
        raise ValueError(f"{src}: no {INI_NAME} and no archive database there; choose the folder that has "
                         f"'{INI_NAME}' or the 'archive' folder in it (for example C:\\e6ai-archiver).")
    dest_ini = Path(dest_ini)
    if info["ini"] and Path(info["ini"]).resolve() == dest_ini.resolve():
        raise ValueError("That is this install's own settings file; nothing to import.")
    cpr = _parser()
    notes = []
    if info["ini"]:
        with open(info["ini"], encoding="utf-8-sig") as f:
            cpr.read_file(f)
        if not cpr.has_section(SECTION) and cpr.has_section("e6ai-archiver"):  # 3.0: rename the section
            cpr.add_section(SECTION)
            for k, v in cpr.items("e6ai-archiver"):
                cpr.set(SECTION, k, v)
            cpr.remove_section("e6ai-archiver")
        if cpr.get(SECTION, "api_key", fallback="").strip():
            notes.append("The old settings file contains an API key; it was copied as it is. Safer: delete it "
                         "from the file and use 'Remember API key on this PC'.")
    if not cpr.has_section(SECTION):
        cpr.add_section(SECTION)
    cpr.set(SECTION, "out", info["out"])
    if not cpr.get(SECTION, "site", fallback=""):
        cpr.set(SECTION, "site", "e6ai")      # e6ai-archiver archives are e6ai archives
    backup = None
    if dest_ini.is_file():
        backup = dest_ini.with_name(dest_ini.name + ".before-import-" + time.strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(dest_ini, backup)
    dest_ini.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest_ini.with_name(dest_ini.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"; imported from {info['dir']} by Packrat {__version__} on "
                f"{time.strftime('%Y-%m-%d %H:%M')}\n")
        cpr.write(f)
    os.replace(tmp, dest_ini)
    return dict(info, dest=str(dest_ini), backup=str(backup) if backup else None, notes=notes)


def save_settings(state: dict, path: Path | None = None) -> Path:
    """Read-modify-write: CLI settings into [packrat] (defaults removed, unknown keys and other
    sections kept), panel-only ones into [gui]. The first save makes a .bak copy of a file it changes.
    Never writes the API key."""
    path = path or settings_path()
    st = normalized(state)
    cp, old_text = _read_for_update(path)
    if cp is None:
        cp = _parser()
    for sec in (SECTION, GUI_SECTION):
        if not cp.has_section(sec):
            cp.add_section(sec)
    for o in OPTIONS:
        if o.ini == "":
            continue
        v = st[o.key]
        sec, key = (SECTION, o.ini) if o.ini else (GUI_SECTION, o.key)
        if o.kind == "ratings" and _ratings(v) == "e,q,s":
            v = ""
        if (o.kind not in ("flag", "neg") and str(v) == "") or (sec == SECTION and _is_default(o, v)):
            cp.remove_option(sec, key)
        else:
            cp.set(sec, key, _ini_value(o, v))
    cp.set(GUI_SECTION, "mode", st.get("mode") or DEFAULT_MODE)
    for k in GUI_EXTRAS:  # 1.9: theme, notify (kept as they are when the caller does not pass them)
        if k not in state:
            continue
        v = state[k]
        cp.set(GUI_SECTION, k, normalize_theme(v) if k == "theme" else ("true" if _truthy(v) else "false"))
    _write_ini(path, cp, old_text)
    return path


def save_gui_values(values: dict, path: Path | None = None) -> Path | None:
    """1.9: change only [gui] keys right away (theme switch, notifications) without saving the other,
    possibly unsaved, options. Returns None (and changes nothing) if the file cannot be parsed."""
    path = path or settings_path()
    cp, old_text = _read_for_update(path)
    if cp is None:
        return None
    for sec in (SECTION, GUI_SECTION):   # the CLI refuses an ini without [packrat]
        if not cp.has_section(sec):
            cp.add_section(sec)
    for k, v in values.items():
        if k == "theme":
            v = normalize_theme(v)
        elif isinstance(v, bool):
            v = "true" if v else "false"
        cp.set(GUI_SECTION, k, str(v))
    _write_ini(path, cp, old_text)
    return path


def _read_for_update(path: Path):
    """(parser, old text) of the settings file to update. 3.0: while packrat.ini does not exist yet, start
    from an e6ai-archiver.ini in the install folder, with its [e6ai-archiver] section renamed [packrat]
    (that old file itself is never changed). (None, text) if the file cannot be parsed."""
    cp = _parser()
    old_text = None
    src = path if path.is_file() else legacy_settings_source(path)
    if src is not None:
        text = src.read_text(encoding="utf-8-sig")
        try:
            cp.read_string(text)
        except configparser.Error:
            return (None, text) if src == path else (_parser(), None)
        if src == path:
            old_text = text
    if not cp.has_section(SECTION) and cp.has_section("e6ai-archiver"):
        cp.add_section(SECTION)
        for k, v in cp.items("e6ai-archiver"):
            cp.set(SECTION, k, v)
        cp.remove_section("e6ai-archiver")
    return cp, old_text


def _write_ini(path: Path, cp, old_text) -> None:
    import io
    buf = io.StringIO()
    buf.write(f"; Packrat settings (saved by Packrat {__version__}, "
              f"{time.strftime('%Y-%m-%d %H:%M')}).\n; The command line reads [packrat]; [gui] is only "
              "for the window. Never put your API key here: use PACKRAT_API_KEY or 'Remember API key'.\n\n")
    cp.write(buf)
    text = buf.getvalue()
    path.parent.mkdir(parents=True, exist_ok=True)
    if old_text is not None and old_text != text:
        bak = path.with_name(path.name + ".bak")
        if not bak.exists():
            bak.write_text(old_text, encoding="utf-8")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def state_file() -> Path:
    """Window size/position and the last tab (not settings) live outside the project folder."""
    base = os.environ.get("APPDATA") if os.name == "nt" else None
    root = Path(base) if base else Path.home() / ".config"
    return root / "Packrat" / "panel-state.json"


def load_ui_state() -> dict:
    try:
        d = json.loads(state_file().read_text("utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_ui_state(d: dict, merge: bool = True) -> None:
    """Writes panel-state.json; by default merged into what is there (window, last run, dismissals)."""
    if merge:
        d = {**load_ui_state(), **d}
    try:
        p = state_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d), encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------------------------------------
EXIT_MESSAGES = {
    0: ("ok", "Finished successfully."),
    1: ("error", "The run stopped because of an error (see the log). Everything up to the last checkpoint "
                 "is saved; press Start to resume."),
    2: ("error", "A setting was rejected (see the log). Fix it and press Start again."),
    3: ("forbidden", "STOPPED: the site answered 403 Forbidden. Do not retry right away and never switch to a "
                     "browser User-Agent. Check your username / User-Agent / API key, wait, or ask site staff."),
    4: ("error", "Stopped: the disk is full (below 'Keep free on the drive'). Free some space, then press Start "
                 "to resume."),
    5: ("error", "Another Packrat run is still using this archive folder: another window, or an older "
                 "e6ai-archiver (e.g. 2.0) that is still running, or a program holding the database. Stop it first (press "
                 "Stop in its window and wait for 'Stopped'), then press Start here. Nothing was changed."),
    11: ("error", "This archive folder was made by a NEWER version of Packrat, so this version left it "
                  "untouched. Use the newer version (or later) for it."),
    6: ("error", "Storage problem: the archive drive disappeared or is read-only. Reconnect it / fix "
                 "permissions, then press Start to resume."),
    8: ("error", "STOPPED: the site did not accept the sign-in (HTTP 401). Check the login name and API key "
                 "(the key is not your password) with 'Test sign-in', or clear the key to run signed out."),
    7: ("error", "Database error. Run 'Repair the database' (it backs up the DB and rebuilds it from the "
                 "journals), then resume."),
    130: ("stopped", "Stopped. Progress is saved; press Start to continue where it left off."),
}


def exit_message(code: int | None, crashed: bool = False) -> tuple[str, str]:
    if code in EXIT_MESSAGES and not (crashed and code == 1):
        return EXIT_MESSAGES[code]
    if crashed or code is None or code < 0 or code not in EXIT_MESSAGES:
        return ("error", f"The archiver stopped unexpectedly (exit code {code}). Progress up to the last "
                         "checkpoint is saved; see the log, then press Start to resume.")
    return EXIT_MESSAGES[code]


def parse_log_level(line: str) -> int:
    """Level of a log line 'YYYY-mm-dd HH:MM:SS,ms LEVEL message' (anything else: INFO, or ERROR for a
    traceback)."""
    import logging
    parts = line.split(" ", 3)
    if len(parts) >= 3 and parts[2] in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        return getattr(logging, parts[2])
    if line.startswith(("Traceback", "error:")) or "Error:" in line[:60]:
        return logging.ERROR
    return logging.INFO


class CliRunner:
    """Runs one archiver command as a child process.

    Messages arrive on .q as tuples: ("event", kind, data) for progress, ("line", text, level) for log /
    output lines, ("exit", code, crashed) once at the end. Reader threads only put to the queue; the Tk
    window drains it with after(), so the UI never blocks on the child.

    Stop: writes "stop" to the control file; the child's watcher thread calls control.stop(), the worker
    raises StopRequested at its next checkpoint (between requests / file chunks / backoff slices), deletes
    the .part file, commits, releases the lock and exits 130. kill() is the last resort (still resumable:
    .part files + atomic replace + SQLite WAL).
    """

    def __init__(self):
        self.q: queue.Queue = queue.Queue()
        self.proc: subprocess.Popen | None = None
        self.control_path: str | None = None
        self.started_at = self.stop_requested_at = None
        self.saw_traceback = False
        self._threads: list[threading.Thread] = []

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, argv: list[str], env: dict | None = None, cwd: str | None = None) -> None:
        if self.running:
            raise RuntimeError("a run is already in progress")
        fd, self.control_path = tempfile.mkstemp(prefix="packrat-control-", suffix=".txt")
        os.close(fd)
        write_control_word(self.control_path, "run")
        full = [*argv[:3], "--progress-json", "--control-file", self.control_path, *argv[3:]]
        kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd or str(PROJECT_ROOT),
                  env=env, bufsize=1, text=True, encoding="utf-8", errors="replace")
        kw.update(popen_platform_kwargs())
        self.saw_traceback = False
        self.stop_requested_at = None
        self.proc = subprocess.Popen(full, **kw)
        self.started_at = time.monotonic()
        self._threads = [threading.Thread(target=self._read, args=(self.proc.stdout, True), daemon=True),
                         threading.Thread(target=self._read, args=(self.proc.stderr, False), daemon=True)]
        for t in self._threads:
            t.start()
        threading.Thread(target=self._wait, daemon=True).start()

    def _read(self, stream, is_stdout: bool) -> None:
        import logging
        for line in iter(stream.readline, ""):
            line = line.rstrip("\r\n")
            if is_stdout and line.startswith(PROGRESS_PREFIX):
                try:
                    kind, data = json.loads(line[len(PROGRESS_PREFIX):])
                    self.q.put(("event", kind, data))
                    continue
                except (ValueError, TypeError):
                    pass
            if line.startswith("Traceback"):
                self.saw_traceback = True
            self.q.put(("line", line, parse_log_level(line) if not is_stdout else logging.INFO))
        stream.close()

    def _wait(self) -> None:
        code = self.proc.wait()
        for t in self._threads:
            t.join(10)
        if self.control_path:
            try:
                os.unlink(self.control_path)
            except OSError:
                pass
        crashed = self.saw_traceback or code not in EXIT_MESSAGES
        self.q.put(("exit", code, crashed))

    def send(self, word: str) -> None:
        if word not in CONTROL_WORDS:
            raise ValueError(word)
        if self.running and self.control_path:
            write_control_word(self.control_path, word)
            if word == "stop" and self.stop_requested_at is None:
                self.stop_requested_at = time.monotonic()

    def stop(self) -> None:
        self.send("stop")

    def pause(self) -> None:
        self.send("pause")

    def resume(self) -> None:
        self.send("run")

    def recheck_disk(self, current: str = "run") -> None:
        """1.9: ask a run that waits for disk space to look again now (then restore the current word)."""
        self.send("recheck")
        def back():
            time.sleep(1.0)
            try:
                self.send(current)
            except (OSError, ValueError):
                pass
        threading.Thread(target=back, daemon=True).start()

    def kill(self) -> None:
        if self.running:
            self.proc.kill()


# ---------------------------------------------------------------------------------------------
# Sign in: one-request test (run through the CLI like everything else), saved key, hidden-post count

def effective_login(state: dict) -> str:
    st = normalized(state)
    return st["login"] or st["username"]


def test_login_command(state: dict, python: str | None = None) -> list[str]:
    """argv for `test-login`: identity, User-Agent and rate only. The key goes in the environment."""
    st = normalized(state)
    args = ["--no-config", "--site", st.get("site") or "e6ai", "--username", st["username"]]
    if st["user_agent"]:
        args += ["--user-agent", st["user_agent"]]
    try:
        r = float(st["rate"])
        if r != float(_D["rate"]):
            args += ["--rate", _fmt_num(r)]
    except (TypeError, ValueError):
        pass
    if st["login"]:
        args += ["--login", st["login"]]
    return [python or child_python(), "-m", "packrat", *args, "test-login"]


def parse_test_login(code: int | None, stdout: str, stderr: str, key: str = "") -> tuple[str, str]:
    """(level, message) for the inline result: level is ok | warn | error | forbidden."""
    from .redact import redact
    res = None
    try:
        start = stdout.index("{")
        res = json.loads(stdout[start:]).get("test_login")
    except (ValueError, AttributeError):
        pass
    last = next((ln for ln in reversed(stderr.strip().splitlines()) if ln.strip()), "")
    if code == 0 and isinstance(res, dict):
        msg = res.get("message") or "Signed in."
        if res.get("warning"):
            return "warn", f"{msg} {res['warning']}"
        return "ok", msg
    if code == 8:
        return "error", ("Not accepted (HTTP 401): wrong login name or API key. The key is the one from your "
                         "account page, not your password.")
    if code == 3:
        return "forbidden", ("STOPPED: the site answered 403 Forbidden. Do not retry right away; check your "
                             "username / User-Agent, or ask site staff.")
    if code == 2:
        return "error", redact(last.removeprefix("error: ") or "a setting was rejected", key)
    return "error", redact(f"Could not test the sign-in (exit {code}): {last or 'no output'}", key)


def run_test_login(state: dict, timeout: float = 120, python: str | None = None) -> tuple[str, str]:
    """Run `test-login` in a child (UA + rate limiter + 403 stop of the CLI); blocking, call from a thread."""
    key = str(state.get("api_key") or "").strip()
    if not key:
        return "error", "Enter the API key first."
    if not normalized(state)["username"]:
        return "error", "Fill in your username on the site first (it goes into the User-Agent)."
    argv = test_login_command(state, python)
    try:
        p = subprocess.run(argv, env=child_env(state), capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, stdin=subprocess.DEVNULL, **popen_platform_kwargs())
    except subprocess.TimeoutExpired:
        return "error", "No answer from the site in time; try again later."
    except OSError as e:
        return "error", f"Could not start the test: {e}"
    return parse_test_login(p.returncode, p.stdout, p.stderr, key)


def _keyring():
    """The keyring module if a usable backend exists (Windows Credential Manager, macOS Keychain,
    Secret Service ...), else None. keyring is optional: any problem means 'not available'."""
    try:
        import keyring  # type: ignore
        from keyring.backends import fail  # type: ignore
        kr = keyring.get_keyring()
        if isinstance(kr, fail.Keyring) or getattr(kr, "priority", 1) <= 0:
            return None
        return keyring
    except Exception:  # noqa: BLE001 - ImportError, broken backend, D-Bus missing ...
        return None


def keyring_available() -> bool:
    return _keyring() is not None


def saved_key(login: str, site: str | None = None) -> str:
    kr = _keyring()
    if kr is None or not login:
        return ""
    try:
        return kr.get_password(keyring_service(site), login) or ""
    except Exception:  # noqa: BLE001
        return ""


def save_key(login: str, key: str, site: str | None = None) -> bool:
    kr = _keyring()
    if kr is None or not login or not key:
        return False
    try:
        kr.set_password(keyring_service(site), login, key)
        return True
    except Exception:  # noqa: BLE001
        return False


def forget_key(login: str, site: str | None = None) -> bool:
    kr = _keyring()
    if kr is None or not login:
        return False
    try:
        kr.delete_password(keyring_service(site), login)
        return True
    except Exception:  # noqa: BLE001 - PasswordDeleteError when nothing was saved
        return False


def login_required_in_archive(out: str) -> int | None:
    """Posts in this archive saved without a file URL (not deleted): what signing in may unlock.
    Read-only; None when there is no archive yet or it cannot be read right now."""
    import sqlite3
    from .siteprofile import find_db
    if not out:
        return None
    db, _site = find_db(_abs_out(out))
    if db is None:
        return None
    try:
        con = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True, timeout=2)
        try:
            return con.execute("SELECT COUNT(*) FROM posts WHERE file_status = 'no_url' "
                               "AND COALESCE(is_deleted, 0) = 0").fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error:
        return None


def popen_platform_kwargs() -> dict:
    """Windows: no console window flashes up (CREATE_NO_WINDOW) and the child gets its own process group,
    so a Ctrl+C in a console that started the panel does not hit the child mid-write; elsewhere a new
    session for the same reason."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)}
    return {"start_new_session": True}


def main(argv=None) -> int:
    """Open the control panel. Accepts the global options of the CLI to prefill fields (e.g. --username)."""
    raw = [a for a in (sys.argv[1:] if argv is None else argv) if a != "--gui"]
    prefill, cfg = {}, None
    if raw:
        from .cli import build_parser
        try:
            ns = build_parser().parse_args(raw + ["stats"])
        except SystemExit as e:
            return int(e.code or 0)
        cfg = ns.config
        for o in OPTIONS:
            for d in o.dest_names():
                v = getattr(ns, d, None)
                if v not in (None, False) and o.cmds is ALL and o.kind not in ("neg",):
                    prefill[o.key] = str(v) if not isinstance(v, bool) else v
        if ns.no_keep_awake:
            prefill["keep_awake"] = False
    from .panel_ui import run_panel
    return run_panel(settings_path(cfg), prefill)


if __name__ == "__main__":
    raise SystemExit(main())


# ---------------------------------------------------------------------------------------------
# 1.9: archive status (dashboard when idle), resume prompt, disk-space check, notifications
RESUMABLE_CODES = {None, 1, 4, 6, 7, 130}   # not 0 (done), 2 (settings), 3 (403), 5 (in use), 8 (sign-in)
RESUME_MODE = {"full": "full", "download": "download", "retry-failed": "retry-failed", "run": "update"}
DISK_MARGIN = 0.03   # +3 % on top of the export sizes and the reserve (file system overhead, new posts)


def archive_status(out: str) -> dict | None:
    """Read-only totals + the last run record from the archive database; None when there is none yet
    (or it cannot be read right now)."""
    import sqlite3
    from .siteprofile import find_db
    from .store import archive_counts
    if not out:
        return None
    db, _site = find_db(_abs_out(out))
    if db is None:
        return None
    try:
        con = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True, timeout=2)
        try:
            st = archive_counts(con)
            row = con.execute("SELECT value FROM state WHERE key='last_run'").fetchone()
            try:
                lr = json.loads(row[0]) if row else None
            except (TypeError, ValueError):
                lr = None
            st["last_run"] = lr if isinstance(lr, dict) else None
            return st
        finally:
            con.close()
    except sqlite3.Error:
        return None


def pid_alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return False
            try:
                code = ctypes.c_ulong()
                return bool(k.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
            finally:
                k.CloseHandle(h)
        except Exception:  # noqa: BLE001
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _gib(n) -> str:
    n = float(n or 0)
    return f"{n / 2**30:,.1f} GiB" if n >= 2**30 else f"{n / 2**20:,.0f} MiB"


def resume_offer(status: dict | None, dismissed: str | None = None, alive=pid_alive) -> dict | None:
    """Should the panel offer 'Resume' at launch? Only for a download run that did not finish (no end
    recorded, or exit 1 / 4 / 6 / 7 / 130) while files are still to download, not one still running and
    not one the user dismissed. 403 / sign-in / settings / in-use exits need attention first, never a
    one-click resume."""
    lr = (status or {}).get("last_run")
    if not isinstance(lr, dict) or lr.get("cmd") not in RESUME_MODE:
        return None
    if dismissed and dismissed == lr.get("started"):
        return None
    ended, code = lr.get("ended"), lr.get("code")
    if ended is None:
        if alive(lr.get("pid")):
            return None            # still running (another window / a console)
        why = "was interrupted (the window was closed, the PC slept or crashed, or power was lost)"
    elif code in RESUMABLE_CODES:
        why = {1: "ended with an error", 4: "stopped because the disk was full", 6: "lost the archive drive",
               7: "hit a database error (run 'Repair the database' first if it happens again)",
               130: "was stopped"}.get(code, "did not finish")
    else:
        return None
    files = status.get("failed_files", 0) if lr["cmd"] == "retry-failed" else \
        status.get("pending_files", 0) + status.get("failed_files", 0)
    nbytes = status.get("failed_bytes", 0) if lr["cmd"] == "retry-failed" else \
        status.get("pending_bytes", 0) + status.get("failed_bytes", 0)
    if not files:
        return None
    started = str(lr.get("started") or "")
    when = started[:16].replace("T", " ")
    return {"cmd": lr["cmd"], "mode": RESUME_MODE[lr["cmd"]], "started": started, "files": files,
            "bytes": nbytes, "code": code,
            "text": f"The last run ('{lr['cmd']}', started {when}) {why}. {files:,} file(s) "
                    f"({_gib(nbytes)}) are still to download. Resume it with the same settings?"}


def remaining_bytes(status: dict | None, cmd: str, limit_gb=None) -> int | None:
    """Bytes the next run may still write (export sizes of pending + failed files, or only the failed
    ones for retry-failed), capped by --limit-gb. None = unknown (no archive database yet)."""
    if not status:
        return None
    n = status.get("failed_bytes", 0) + (0 if cmd == "retry-failed" else status.get("pending_bytes", 0))
    try:
        if limit_gb not in (None, "") and float(limit_gb) > 0:
            n = min(n, int(float(limit_gb) * 2**30))
    except ValueError:
        pass
    return int(n)


def disk_check(need: int | None, free: int | None, reserve_gb) -> tuple[bool, str]:
    """Before Start: will the files still to download fit above the reserve (+ a small margin)?"""
    if need is None or free is None:
        return True, ""
    try:
        reserve = int(float(reserve_gb or 0) * 2**30)
    except ValueError:
        reserve = 0
    want = int(need * (1 + DISK_MARGIN)) + reserve
    if free >= want:
        return True, f"{_gib(need)} still to download; {_gib(free)} free - fits."
    return False, (f"About {_gib(need)} of files are still to download (export sizes), plus the "
                   f"{_gib(reserve)} you keep free and a {DISK_MARGIN:.0%} margin = {_gib(want)}, but the drive "
                   f"has only {_gib(free)} free (short by {_gib(want - free)}).\n\nThe run will pause when the "
                   f"drive gets full (or stop, if you chose that) and can continue after you free space or move "
                   f"the archive folder to a bigger drive.")



def notification_for(code, crashed: bool, mode_label: str, summary: str = "") -> tuple[str, str] | None:
    """(title, text) for the desktop notification at the end of a run, or None (no notification for a
    Stop the user pressed or a rejected setting: they are looking at the window)."""
    if code in (130, 2) and not crashed:
        return None
    kind, text = exit_message(code, crashed)
    title = {0: f"Packrat: finished - {mode_label}", 3: "Packrat: STOPPED on 403 Forbidden",
             4: "Packrat: stopped - disk full", 8: "Packrat: sign-in rejected (401)"}.get(
        None if crashed else code, f"Packrat: error - {mode_label}")
    body = (summary + "\n" if summary else "") + text
    return title, body[:400]


def failure_log_path(out: str) -> Path:
    from .failures import FAIL_LOG
    return Path(_abs_out(out or ".")) / FAIL_LOG
