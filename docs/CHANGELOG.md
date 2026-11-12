# Changelog

## 3.3.0 (2026-11-12) - one search language for every site, and a site setup wizard

### New
- **Sort and Rating menus** next to the search box: newest, most popular, most favorited, oldest, random; all
  ratings, safe for work, sensitive, questionable, explicit, not safe for work, no explicit. They work on every site.
- **Packrat's search words, rewritten for each site.** `order:popular`, `order:favorites`, `order:oldest`,
  `order:random`, `rating:sfw`, `rating:sensitive`, `rating:questionable`, `rating:explicit`, `rating:nsfw`,
  `-rating:explicit` and `score:>=50` can be typed (or picked in the menus) once, and Packrat writes them the way each
  chosen site wants: Danbooru and Gelbooru have four ratings and Danbooru's `rating:s` means *sensitive*; e621,
  Moebooru and Sankaku have three; Derpibooru uses the tags safe / suggestive / explicit and `score.gte:`; Gelbooru
  sorts with `sort:`; Wallhaven with `order:toplist`; Zerochan with `order:fav`. Downloads, monitors and favorites
  use it too. What a site can't do itself, Packrat does on the pictures it receives (ratings, scores) and says so
  under the results. A site's own words pass unchanged. Off switch: Settings > Interface.
- **Set up a site** (Sites and logins > *Set up a site...*, or *...* > *Set up a site* on a search): pick a site, see
  what an account changes there, open the page where it gives out its API key, fill in and test the login, then
  choose what to show from it (everything, safe for work only, no explicit) and start a first search.
- **Use my login from a browser**: if you are logged in to a site in your own browser, Packrat can use that login.
  It reads only that site's cookies, only when you press the button. Firefox and LibreWolf work everywhere; Chrome,
  Edge, Brave, Arc, Vivaldi and Opera on a Mac (macOS asks once; choose *Always Allow*) and on Linux; Safari on a Mac
  with Full Disk Access. **Not on Windows for Chrome, Edge and Brave**: since 2024 they lock their logins so no other
  program can read them; use Firefox or the built-in login browser there.
- Help: "Sort and rating: the same words on every site" and "Logging in to a site" in How to search.

## 3.2.2 (2026-11-12) - Mac fixes

### Fixed
- **Mac: typing in Packrat did nothing, and it had no Dock icon or menu.** The Mac app was marked as a background
  program, so macOS never gave it the keyboard: the cursor blinked in the search box, but the keys went to the app
  that was active before. Packrat is now a normal app, with its Dock icon and menu bar. Every build checks for this.

### New
- **Opening Packrat again brings its window back.** When Packrat is running hidden next to the clock (Windows) or in
  the menu bar (Mac), starting it again from Spotlight, Finder, the Dock, the Start menu or a shortcut shows the
  window instead of seeming to do nothing. On Windows the window also comes to the front reliably.

## 3.2.1 (2026-11-12) - security fixes, and Packrat for Mac

### Mac
- **Packrat runs on Macs.** A new download, **Packrat-macOS.dmg**: open it and drag Packrat into Applications
  (Apple Silicon Macs, M1 and newer, macOS 12+). Settings live in `~/Library/Application Support/Packrat`, pictures
  go to Pictures > Packrat, logins to the Keychain, and the icon sits in the menu bar. Not on the Mac yet: hourly
  monitor checks while Packrat is closed, and desktop / Start menu shortcuts.
- The Python download has a **Start Packrat.command** for Mac and Linux (Intel Macs use this).
- Every change is now tested on GitHub's Mac machines too (tests, the app build and the window self-test), and the
  .dmg is put on the Releases page with the Windows downloads.

### Security
- **Commands after saving can no longer be hijacked by a tag.** Settings > Commands puts tag names and other
  values from the site into a command line. On Windows that line runs through cmd.exe, so a tag such as
  `black_&_white` split the command, and a crafted tag could start any program. Values from sites now have the
  characters that end or chain a command (`& | < > ^ " % !`) replaced with `_`; an unquoted `%path%` / `%dir%`
  is put in quotes, so a file name with `&` or spaces stays one argument.
- **Database commands:** `%original%` and `%path%` are escaped too (a tag or file name with `'` broke the
  query). SQLite and PostgreSQL no longer get doubled backslashes in paths (only MySQL wants that).
- **Your API key no longer ends up in picture page addresses.** On sites that log in with `login=..&api_key=..`
  in the address, the key was added to every picture's page address, which is written into `.json` sidecars,
  log files and `%url_page%` file names, copied by "Copy the page address" and sent as the Referer to file
  servers. The key is now only added to the requests that need it.
- **Logins stay on their site.** The password / API key / token header was sent with every request, including
  file downloads from other hosts. It now goes only to the site and its own subdomains (cdn.donmai.us for
  Danbooru, static1.e621.net for e621, ...).
- A source registry can no longer name a source `..` (installing it replaced Packrat's data folder).

### Window
- Browse: the empty Tags box is hidden until a search shows tags; the search hint is no longer cut off.
- Sites and logins: site names line up again (icons of different sizes pushed some to the right).
- Settings and Site archive: forms stop growing at a readable width on wide screens.

## 3.2.0 (2026-11-10) - the download engine

Measured first: searching and naming files were already fast (a result page costs 30-70 ms of work next to about
0.7 s of waiting for the site; a file name 0.1 ms). The time went into how the download list was organized, so
that is what was rewritten.

### Faster
- **A search on several sites downloads from all of them at once.** Before, the second site waited until the
  first was completely done. Each site keeps its own waiting times.
- **The next result page is fetched while the current one is still downloading.** Before, one slow file (a long
  video) held up the whole list until it finished.
- **Pictures you picked ("Download selected", "Download this page") download side by side.** Before, they went
  strictly one at a time, whatever "downloads at the same time" said.
- On a simulated site: a 120-picture search on two sites 10.8 s -> 5.7 s; 40 picked pictures 6.2 s -> 1.6 s.
  On real sites the gain depends on the site's waiting time (most: 1 s between files, as in Grabber; change it
  under Sites and logins > Log in / settings > Speed limits).

### New
- **A transfer that breaks halfway is continued** from where it stopped (up to 4 times per file), instead of
  starting the file again. Matters for big videos on a shaky connection.
- **"How many" now counts per site.** A limit of 100 on two sites gives up to 100 from each. Before, the first site
  used up the limit and the second got nothing.

### Fixed
- **A site that could not be read was marked as finished.** A network problem or an error page on one result page
  ended that site for good and the row showed "Finished". It now shows "Problem" and Start continues at the page
  that failed.
- A picked picture refused by its site (403) no longer stops the rest of the list.

### Tests
- The download path had no automatic tests. It now has 7, run against a small web server: saving and recognising,
  several sites at once, page order, picked pictures in parallel, a failing site, stop and start again, and a
  transfer that breaks halfway. 106 tests in total.

### Names
- A front-page README for GitHub. The Site archive manual now uses the real command and file names (`packrat`,
  `packrat.ini`). Mentions of e6ai-archiver remain only where they are needed: picking up old settings and archives.

## 3.1.0 (2026-11-10) - a new window

The window was rebuilt from scratch with Qt (the toolkit Grabber itself uses). The engine underneath - sites,
searching, downloading, file names, the site archive - is the 3.0 engine; your settings, favorites, monitors and
download lists carry over unchanged.

### New
- **A new window.** A list on the left switches pages; light and dark; a real thumbnail grid (drag to select,
  Ctrl / Shift click, badges for videos, GIFs, galleries, saved and blacklisted pictures); a search box that
  completes tags.
- **Videos and animated pictures play inside Packrat** (play / pause, time bar, volume, repeat). No codecs to
  install: the player brings its own.
- **Built-in login browser.** Sites and logins > "Log in with the built-in browser..." opens the site inside Packrat;
  log in there and Packrat uses that login. For sites without an API key, and many "checking your browser" pages.
- **Icon next to the clock.** Open Packrat, check monitors, start or stop downloads, quit. Options to keep Packrat
  running when the window is closed, hide it when minimized, and start hidden (Settings > Monitoring).
- **Packrat is "Packrat" in Windows**: its own icon and taskbar button instead of Python's, notifications under
  its own name, an optional desktop / Start menu shortcut.
- **Packrat.exe.** A build that needs no Python, made and tested on a Windows machine each time the code changes.
- **Coloured words in the search box**, like Grabber: artist, character, series and species tags in their tag
  colours, favorites pink, blacklisted tags red, special words (`rating:safe`) in the meta colour. It never gets in
  the way of typing, also with a Korean / Japanese / Chinese keyboard. Off switch: Settings > Interface.
- **Zoom in the picture window** (Ctrl + wheel, + / -, 0 fit, 1 actual size, drag to move); Ctrl+C copies the picture.
- **One window at a time**: starting Packrat twice brings the first window to the front.
- Settings: a box to find any setting by name; every page always visible (no more "advanced view").
- Tools: clear the caches; create shortcuts. The picture cache now cleans itself above 600 MB.
- Site archive: the same options as before, drawn from the same descriptions the command line uses.

### Fixed
- **Long saved logins were lost when Packrat closed.** Windows keeps at most 1280 characters per saved credential;
  longer values (login cookies, long tokens) could not be saved and only lasted until Packrat closed. They are now
  split over several entries and put back together.
- The built-in notifications no longer appear as "Windows PowerShell".
- A PC without a sound device no longer risks a crash when a video opens.
- Scheduled monitor checks no longer flash a black window every hour.

### Tested on real Windows
On GitHub's Windows 11 machines, on every change: the start-up scripts with Windows PowerShell 5.1 and Python 3.14,
all automatic tests, and a self-test that opens every page of the window and its dialogs, plays a video, and checks
the taskbar identity and the icon next to the clock - for the Python version and for Packrat.exe.

### Still missing compared to Grabber
- Translations (English only), an auto-updater, custom themes.
- "More options..." (the helper that wrote sort / rating words for you) has not moved to the new window yet;
  type the words yourself (Help > How to search). The classic window still has it.

The classic window (3.0) is still included: `Start Packrat (classic window).bat`.

## 3.0.0 (2026-11-09) - Packrat

e6ai-archiver becomes **Packrat**: a browser and downloader for many sites, built on Grabber's (imgbrd-grabber)
site sources and languages, with the whole-site archiver kept as the **Site archive** tab.

### New
- **Grabber parity review** (against Grabber's source as of 2026-09-26):
  - **Gallery tab**: clicking a gallery (E-Hentai, NHentai, Pixiv album, ArtStation project...) opens its pictures
    in a tab; *Download all results* there saves the gallery. Sprite-sheet thumbnails (E-Hentai) are cropped.
  - **Wiki**: *About this tag...* shows the site's wiki text for the search.
  - **Search history**: Back / Forward per tab (Alt+← / Alt+→), recent searches on the empty tab and in the tab
    menu, with *Clear the list*.
  - **Add a picture** by its page address (Downloads, or paste the address in the search box).
  - **Viewer**: *Save to the favorites folder* (Ctrl+Alt+S; the favorites folder and file name settings were
    there but unused), copy the picture to the clipboard, copy addresses, reload (F5).
  - Menus: *Find this picture (MD5)*, *Copy all tags* (with types), *Use as the picture of favorite*.
  - Tag loader refreshes the site's tag type list first, as Grabber does.
  - Source registries: list / add / forget (command line and the registry window).
  - Lists (Favorites, Monitors, registries) sort by clicking a column title.
  - **About Packrat** in the Help menu.
- **Fixes**: removing a site now also deletes its saved password / key / cookies from the Credential Manager; an
  error page from a site (403, 503) is reported instead of "0 pictures"; the e621 user-name hint only shows for
  e621 / e926 / e6ai; windows never open bigger than the screen; *Open folder* moved out of the Downloads toolbar
  so it fits at 200% scaling. Unused code removed.
- **Simple and advanced view**: Packrat starts simple (Browse, Downloads, Favorites, Site archive and a short
  plain-language Settings page). **Advanced options** (top right, or the Help menu) adds the Monitors, Tools and
  Log tabs, filters, merge, pictures per page, list ordering, all 200+ settings and site speed limits. Nothing
  is lost when switching; links to hidden things ask before showing them.
- **Ask the help a question**: a search box in the help window that understands everyday wording (synonyms,
  "won't", "where are my files"...) and finds guides, sections, site pages and settings.
- **Help menu**: help for this tab, ask a question, guides, **Run the setup assistant again** (with your current
  choices filled in), the simple / advanced switch.
- **More options...** next to Search (after Grabber's *Plus* window): sort, safe only / no explicit, big
  pictures, tags to leave out, offered only when every ticked site supports them.
- **Saving to ... Change...** under the results (after Grabber's *Destination* panel): the folder and name format
  always in view, changed with presets and a live example. **When everything is downloaded** is in the
  Downloads tab.
- **Built-in help**: **? Help** / F1 opens a guide with a page for every site (what it has, adult or not,
  whether it needs a login and the exact steps to get a key), how to search, download, name files, filter and
  fix problems. Every tab has its own **?** button; links in the guide open the right tab or setting.
- **First-start assistant**: user name, sites, download folder, file names (with a live example) and Grabber
  import, one question per page.
- **Sites chooser**: plain-language list (what each site has, content, login) grouped into good places to
  start and the rest, with a find box, "Hide adult sites", the site's guide beside it and **Test this site**.
  Login options use plain names ("API key", "Sign in with your browser") and only show the fields that method
  needs.
- **Works at any Windows display scale** (125%, 150%, 200%): list rows, tick boxes, columns, thumbnails and
  dialogs are sized from the system font, so nothing is clipped.
- **One window, eight tabs**: Browse, Downloads, Favorites, Monitors, Site archive, Settings, Tools, Log. Light,
  dark or follow Windows, for everything.
- **31 site sources** from Grabber, run by a JavaScript engine (dukpy/QuickJS) with Grabber's helper functions
  (regex, XML and HTML parsing in Python): Danbooru, Gelbooru, e621 / e926 / e6ai, Moebooru (yande.re,
  Konachan), Shimmie, Philomena, Sankaku, Zerochan, Anime-Pictures, behoimi, Pixiv, E-Hentai, Twitter,
  Reddit, DeviantArt, ArtStation, Tumblr, Kemono and more, each with its default sites. Logins: API key,
  HTTP basic, login forms (with CSRF tokens), OAuth 1 and 2; secrets in the Windows Credential Manager.
  Per-site speed limits, retries with Retry-After, API order, referer, proxy.
- **Browse**: search tabs with thumbnails, several sites at once (merged or not), tag list with colours,
  autocomplete, post-filters, blacklist (greyed out or hidden), session restore, hover details, reverse image
  search, a **viewer** (fit, animated GIFs, videos in your player, slideshow, full screen, preloading).
- **Downloads**: searches and single images in a resumable list run in a separate process (Start / Pause /
  Stop), MD5 duplicate handling (skip, save, copy, move, link, hard link), save / load lists (`.prl`, Grabber's
  `.igl`).
- **File names**: Grabber's complete language - every token, options, conditionals, JavaScript, custom tokens,
  per-category rules (keep N, replace, one file per tag...), conditional file names; 51 of Grabber's own tests
  pass.
- **After saving**: separate log files, file dates, image / video / ugoira conversion, resizing, ExifTool
  metadata, `.json` sidecars, commands, SQL, and a **built-in local booru upload** (Szurubooru, Danbooru,
  Shimmie, MyImouto) that needs no PHP / Node.js scripts.
- **Favorites** with "new since last viewed" counts and thumbnails, **kept for later**, **monitors** (in the
  window, or hourly with Task Scheduler while it's closed).
- **Tools**: import from Grabber (settings, favorites, monitors, MD5 list, download list, sites and logins),
  rename existing files, MD5 list fixer, blacklist fixer, empty folders, tag loader (e621 family: the daily
  tags export), source registries (SHA-256 checked), library backup.
- **Command line**: `packrat grab` with Grabber's CLI options and outputs; `batch`, `monitors`, `sources`,
  `import-grabber`, `tools`, `gui`.
- **Site archive for e621** too (`--site e621`); one archive folder per site, checked.

### Better than Grabber, on purpose
- MD5s known before downloading on more sites (Gelbooru's `hash`, MD5 file names), so duplicates are skipped
  without downloading them.
- e621 / e926 / e6ai search results carry typed tags (patched source): `%artist%` and friends need no extra
  request per image.
- Gelbooru 0.2 tag lists work with today's page layout (patched source).
- Monitors never flood you with old images on their first check.
- Registry sources are checked against their published SHA-256.

### Upgrading from e6ai-archiver 2.0
Nothing is downloaded again: the archive, its database (schema unchanged, 2.0 can still open it), the old
`e6ai-archiver.ini`, `E6AI_*` variables and the saved API key all keep working. See README, *Upgrading*.

## 2.0.0 (2026-11-04) - distribution release

A ready-to-unzip download for people who have never used a command line, the "Whole archive" dashboard
fix, a polished window, and safe upgrades from 1.6 - 1.9.1. No live requests were made for this release;
everything was tested offline against the local fake server and real copies of the old versions.

### Download layout
```
e6ai-archiver/
  START HERE - README.txt     plain-text step-by-step guide + FAQ (opens in Notepad)
  Start e6ai Archiver.bat     double-click: opens the control panel
  Check Python.bat            double-click: shows which Python would be used (changes nothing)
  app/                        the program (package, run.ps1, find_python.ps1, run.bat, run_gui.bat, ...)
  docs/                       README.md (this manual), CHANGELOG.md, screenshots/, review/
```
The .bat files start PowerShell with `-ExecutionPolicy Bypass` for that one process only; no Windows setting
is changed. The install folder (the one with the .bat files) holds the data, as before: `archive\`, `.venv\`,
`e6ai-archiver.ini`. `run.ps1` / `install_daily_task.ps1` find it themselves whether they sit in `app\` (2.0)
or in the install folder (1.x layout, the git checkout). `tools/build_dist.py` builds the zip.

### Fixed
- **"Whole archive" dashboard stuck at a handful of files with no ETA.** Root cause: the CLI sent the
  whole-archive snapshot (`archive` event) only once, at the very start of `full`, i.e. *before* the posts
  export was loaded and merged into the database. The panel therefore kept the totals of the nearly empty
  database (e.g. "12 / 55 files") for the whole run, never matched the Progress box, and "ETA (all)" stayed
  blank. On top of that, a new snapshot reset the run counters instead of adding only what was saved after it,
  so saved files could be counted twice. Now the archiver sends fresh totals right before every download queue
  (after the export merge), the panel adds only files saved after the latest snapshot, and expected = every
  non-deleted post with a file, saved = files present. "ETA (all)" shows a value as soon as there is a rate.
- **"no checksum in listing" for the artists export** is now an INFO line, not a warning: the site never
  publishes a checksum for that export, so it was a false "Last problem". The posts export still warns.

### Upgrading from 1.6 - 1.9.1 (nothing is downloaded again)
- **Automatic, transactional database upgrade.** On the first open, 2.0 copies the database to
  `<archive>\db-backups\e6ai.sqlite3.before-2.0.0-from-<old version>-<time>` (SQLite online backup; refuses
  with "nothing was changed" if the disk has no room for it), then upgrades the schema in one transaction. If
  anything fails it is rolled back and the old database is left exactly as it was. Each upgrade is recorded
  (`upgrade_history`). Saved files, `.part` leftovers, journals, `failed-downloads.csv/.log` and the folder
  layout are used as they are.
- **Old settings are read as they are** (`e6ai-archiver.ini` from 1.x, including the default archive folder
  that 1.x did not write into the file). The default archive location does not change: `archive\` next to the
  .bat files.
- **Import settings** (button, and a banner on the first start of a new copy that finds an earlier install in
  `C:\e6ai-archiver`, the usual user folders, `e6ai-archiver*` folders next to it, or `%E6AI_PREVIOUS_INSTALL%`):
  copies the old settings and points the archive folder at the old archive. Nothing old is moved or changed;
  an existing settings file is kept as `.before-import-<time>`.
- **Unzipping over a 1.x folder** works: the existing `.venv` is reused (the archiver is reinstalled into it
  from `app\` if needed, or the venv is rebuilt if its Python is gone); `PYTHONSAFEPATH=1` makes sure the old
  `e6ai_archiver\` folder left in the install folder is never imported. The launcher lists the old files that
  can be deleted.
- **An old version still running** on the same archive: 2.0 stops with exit code 5 and a message that names
  the older version; nothing is touched.
- **A database from a newer version**: new exit code **11**; the archive is left untouched (no backup, no
  upgrade).
- **Going back**: 1.9.1 opens a database that 2.0 has upgraded (tested).

### Window polish (no features removed)
- Default size 1280x860 (scaled for the display DPI, capped at the screen), minimum 980x640; nothing is cut
  off at either size. The log always keeps at least 8 lines; the status boxes scroll instead.
- Readable log / command font (Cascadia Mono or Consolas on Windows instead of Courier New).
- A big **Start** button on the right, Pause / Stop beside it, secondary buttons in two tidy rows.
- Tabs without the "1 2 3 4" prefixes; consistent spacing; "Whole archive" figures on two lines and in one
  column when the box is narrow; tooltips on every button and the status line; clearer status wording.
- Dark and light themes checked for every new element (banners, buttons, dashboard).

### Docs
- New `START HERE - README.txt` (plain ASCII, Windows line endings): install Python with "Add python.exe to
  PATH", unzip, start, stop and resume, what the buttons mean, upgrading, and a FAQ ('py' is not recognized,
  SmartScreen, execution policy, the 403 stop, low disk, every exit code).
- Screenshots (dark and light, simulated data, labelled as such) in `docs/screenshots/`.

## 1.9.1 (2026-11-04)

Fix for the first run on Windows. The owner's PC stopped with
`py : The term 'py' is not recognized as the name of a cmdlet...` at `run.ps1:131` (`py -3 -m venv .venv`):
1.9.0 called the python.org launcher `py` blindly, and it is not installed on every PC (for example when
Python came from the Microsoft Store or winget, or `py` was left out). No live requests were made for this
release.

### Fixed
- **`run.ps1` now finds a working Python itself** (new `find_python.ps1`, dot-sourced by `run.ps1`). It tries,
  in order, and uses the first that really runs and is **3.11 or newer** (from `requires-python`):
  1. the existing `.venv\Scripts\python.exe`;
  2. `py -3`, only if the launcher exists (`Get-Command py`, `%SystemRoot%\py.exe`, or
     `%LOCALAPPDATA%\Programs\Python\Launcher\py.exe`);
  3. every `python` on PATH (`Get-Command -All`), in PATH order;
  4. every `python3` on PATH;
  5. `%LOCALAPPDATA%\Programs\Python\Python3*\python.exe`, `%ProgramFiles%\Python3*\python.exe`,
     `%ProgramFiles(x86)%\Python3*\python.exe`, `C:\Python3*\python.exe`, newest first.

  Nothing is called by a bare name. Each candidate is run once with `-c "import sys; print(...)"` and counts
  only if it prints its version, so the **Microsoft Store shortcut**
  (`%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe`, which prints "Python was not found" and exits 9009)
  is skipped. A Python that is too old is skipped and named in the message.
- **No suitable Python: a clear message and exit code 9** instead of a red PowerShell error. The message lists
  what was checked and explains how to install Python from python.org: tick "Add python.exe to PATH", reopen
  PowerShell, run the same command again. It also mentions `winget install -e --id Python.Python.3.12` as a
  hint; the tool never runs winget.
- **Missing venv module:** if the Python found has no `venv` / `ensurepip` module (trimmed or embedded builds),
  the tool says so and exits 9.
- **Broken `.venv`:** a `.venv` whose Python no longer starts (Python uninstalled) or is too old is moved aside
  to `.venv.broken-<date>` (not deleted) and rebuilt. A half-finished install (the `e6ai_archiver` import
  fails) is completed on the next run.
- **Install errors are checked now.** If `python -m venv` fails, the tool exits 9 with advice. If
  `pip install -e .` fails, it exits **10** ("check the internet connection, rerun").
- **pip's output no longer ends up in the launcher's Python path.** In 1.9.0 it went into the function's
  return value, which would have broken the first run even with `py` present. It now goes to the console.
- `run.ps1` has a message for exit code 8 (sign-in rejected).

### New
- `.\run.ps1 -Mode check-python`: shows which Python would be used and what was skipped. It creates and
  installs nothing; it exits 0, or 9 if there is no suitable Python.
- `install_daily_task.ps1` warns when `.venv` does not exist yet: run `-Mode test` once first, so a missing
  Python is not a silent daily failure.
- README: the Windows first-run section explains the search order, the Store shortcut, reopening PowerShell
  after installing, winget, and the launcher exit codes 9 and 10. The manual setup uses
  `python -m venv` (or `py -3`).

### Checked
- Added `tests/test_v191.py`:
  - Static checks: no bare `py -3`, the minimum version matches `pyproject.toml`, exit codes and help
    documented, no PS7-only operators.
  - Behaviour tests that run the real `find_python.ps1` / `run.ps1` under PowerShell 7 with fake python
    executables: the Store stub, a too-old Python, the `py` launcher first, install folders newest first,
    no Python (exit 9 with help, and no `.venv` made), and a missing venv module. These tests skip when
    `pwsh` is absent.
- `run.ps1`, `find_python.ps1` and `install_daily_task.ps1` parse cleanly. PSScriptAnalyzer 1.25 found
  nothing with PSUseCompatibleSyntax (target 5.1), PSUseCompatibleCommands / PSUseCompatibleTypes (the
  Windows 10 PowerShell 5.1 profile), and its default error/warning rules (style rules about Write-Host
  excluded).
- Not run on real Windows: Windows PowerShell 5.1 itself and the real Store shortcut were not available here.

## 1.9.0 (2026-11-04)

The owner asked for polish / quality-of-life ideas and a dark mode. This release adds the dark mode plus
six quality-of-life features. No live requests at all were needed or made for this release: no API key,
no authenticated requests, no file downloads. All tests are offline; see the test count in REVIEW.md.

### New
- **Dark mode** in the control panel:
  - **Theme: Light / Dark / Follow Windows** buttons in the header. Follow Windows (the default) reads
    `HKCU\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize\AppsUseLightTheme` and re-checks
    it every 3 s; on other systems it means light.
  - Applies instantly, no restart. Saved at once to `[gui] theme` in `e6ai-archiver.ini` (the CLI ignores
    `[gui]`). Ctrl+Shift+D toggles light/dark.
  - Every widget is themed: notebook tabs, entries, checkboxes, radio buttons, combos, spinboxes, tooltips,
    the log (info/warning/error/command colours, each at least 4.5:1 contrast on the log background), the
    command preview, all buttons, progress bars and scroll bars. Windows 10/11 also get a dark title bar
    (DWM attribute 20, then 19).
  - No white flash: the window is built hidden, coloured, then shown.
  - New module `theme.py` (palettes, contrast check, ttk styles).
  - The HTML completeness report has a `prefers-color-scheme: dark` style.
- **Whole-archive dashboard** above *Progress*: files saved / expected with a bar and percent, GiB saved,
  this run's files and GiB, MB/s and files/s (60 s window), failures (now / to retry), ETA
  for the rest of the archive, and a low-disk line. Idle, it reads the archive database read-only. New
  `archive` progress event (`store.archive_counts`).
- **Desktop notification at the end of a run** (new `notify.py`): Windows toast via PowerShell (WinRT,
  PowerShell's app id), falling back to a tray balloon, then a small window inside the panel; macOS
  `osascript`; Linux `notify-send`. For finished, HTTP 403 stop, disk full, error, sign-in rejected, and
  low-disk pauses. Not for Stop or settings errors, nor for local runs under a minute. Basics > *Notify
  me when a run ends* (`[gui] notify`).
- **Disk-space guard:**
  - `--on-low-disk pause|stop` (`E6AI_ON_LOW_DISK`, `on_low_disk`, panel Basics > Disk and power). The
    default, `pause`, makes all workers wait when the next file would go below `--min-free-gb`. The
    run emits `disk_low` and the phase *paused: low disk space* (panel: status, red dashboard line,
    notification, Pause button turns into Resume). Free space is re-checked every 2 s and downloads
    continue by themselves; Resume (control word `recheck`) re-checks at once. Stop still works while
    waiting. `stop` is the 1.8 behaviour (exit 4).
  - Before Start, network modes compare what is left (pending, or failed for retry-failed; capped by
    *first N GiB*) with free space minus the reserve and ask first when it will not fit.
- **Resume prompt:** runs record `last_run` (command, start, pid, end, exit code) in the archive database.
  At launch the panel offers a banner for an interrupted run (killed / crashed, error, disk full, storage,
  database or Stop) with files left: **Resume** with the last run's settings (API key never stored),
  **Not now**, **Don't ask for this run**.
- **Failure-log trimming:** `--failure-log-max-mb` (default 10, 0 = never; `E6AI_FAILURE_LOG_MAX_MB`,
  `failure_log_max_mb`, panel Advanced > Logging). At the end of a run, larger files are trimmed: rows of
  posts still failed / missing / md5_mismatch always stay, the others are appended to
  `failed-downloads-archive.csv.gz` / `.log.gz` first (no loss even on a crash in between).
- **Buttons:** new **Open failure log**; *Open report* is now **Open latest report**; the four Open buttons
  (archive folder, log, failure log, latest report) have their own row under Save / Reset / Retry failed
  files / Pause / Stop / Start, so nothing is clipped at the minimum window width.
- `run.ps1`: `-OnLowDisk`, `-FailureLogMaxMB`. `install_daily_task.ps1` passes `-OnLowDisk stop`.

### Changed
- Light mode now uses the same flat ("clam") ttk style on every OS, Windows included (before 1.9 Windows
  used the native "vista" look), so both themes look alike.
- Low disk space during downloads pauses by default instead of stopping with exit 4.
- `save_ui_state` merges into `panel-state.json` instead of replacing it.

### Not changed
- The 20-consecutive-403 stop, the API key only in `E6AI_API_KEY`, anonymous-only export downloads, the
  ~2 req/s note from the API docs.

### Not verified
- Windows-only parts (registry read, toast / balloon, dark title bar, run.ps1 changes) could not be run
  here (no Windows / PowerShell on the build machine); they are written defensively and fall back
  silently. Screenshots use simulated data on Linux.

## 1.8.0 (2026-11-04)

The owner asked for three things: pull the tags export automatically, log each file that fails to
download, and add an option to rerun only the failed files. Live use for this release: one anonymous
export listing request plus the anonymous tags export file. No API key, no authenticated requests and no
file downloads from the live site. 263 offline tests pass: 233 earlier, 2 updated (version string, and the
1.7 report helper now passes `--no-fetch-tags`), plus 30 new.

### New
- **Tags export pulled automatically:**
  - `full`, `sync-exports` and `exports --download posts` always fetch `tags` along with `posts`
    (`exports.with_tags`).
  - A missing tags export is fetched even inside the once-a-day export window.
  - `report`, and the report at the end of `full` / `retry-failed`, first run `ensure_tags`. If the tags
    export is missing or more than 6 h older than the posts export, they download that file anonymously
    (never the API key or login): same sha256 verify and cache-bust retry, one listing request plus one
    file. They then load it and categorize the seeded posts.
  - If it is unavailable, the report is still written with a clear note at the top (`tags_export.note` in
    JSON). It explains that directors fall back to `_no_director` / `_unknown_director`.
  - To turn it off: `--no-fetch-tags`, `fetch_tags = no`, `E6AI_FETCH_TAGS=0`, or panel Advanced > Report
    "Fetch the tags export for the report".
- **Failure log**, `failed-downloads.csv` + `failed-downloads.log` in the archive folder:
  - Appended per run and never rotated.
  - One row per failed file: local time with offset, run id, post id, md5, ext, director, status, reason,
    HTTP status, tries this run, failures in total, and URL. URLs are stripped of user info, query and
    fragment, then redacted.
  - A `recovered` row when a previously failed file is saved.
  - Run start/end lines and a summary in the `.log`.
  - In the panel log: one warning per failure plus an end-of-run summary. New GUI events `file_failed`,
    `file_recovered`, `failures`.
  - The DB reason is kept, and `file_errors` gets an `attempts` counter (migrated automatically).
- **`retry-failed`** (CLI; panel mode "Retry failed files only" + a **Retry failed files** button;
  `run.ps1 -Mode retry-failed -Reason ... -FromCsv ... -DryRun`; `run.bat ... retry-failed`):
  - Re-downloads only posts whose file is `failed` / `missing` / `md5_mismatch`.
  - Optional `--reason` filter: 403, 404, 429, 503, http, network, md5, size, verify, unknown, an exact
    reason, or a list. An unknown word is a config error (exit 2).
  - `--from-csv` accepts missing.csv, failed-downloads.csv or any `post_id` list, and also re-fetches `ok`
    files gone from disk.
  - `--dry-run` makes no network requests.
  - Afterwards: the failure log is updated (recovered / failed again) and the completeness report is
    rewritten (`--no-report-after` skips it).
  - If it is stopped part-way, files it didn't reach keep their failed status.
  - The 20-consecutive-403 stop, Pause/Stop and back-off are unchanged.

### Changed
- Panel mode labels:
  - "Completeness report (fetches only the tags export, if needed)"
  - The old "retry" mode is now "Re-queue failed files + download all pending", to tell it apart from
    `retry-failed`.
- `set_file_error` now upserts and returns the attempt count.

### Live check (2026-11-04, anonymous)
- The tags export in the listing is 6,394,700 bytes, at `https://static1.e6ai.net/data/db_export/tags.csv.gz`.
- `report` on a copy of the simulated test archive (compared with the 2026-11-03 posts export):
  - fetched it, sha256 OK;
  - loaded 321,143 tag categories, including **5,173 director tags**;
  - categorized 193,537 seeded posts;
  - split the report into **4,130 directors**, with no `_unknown_director`.
  - Note: `unknown_director` and `anonymous_director` are real site tags.

## 1.7.0 (2026-11-04)

**Completeness report** (suggestion #1 from the 1.6 review, requested by the owner). Offline only: nothing
in this release made authenticated or bulk requests; the one anonymous download of the posts export was
used to check the figures. 233 offline tests pass (209 earlier, 1 updated for the version, + 24 new).

### New
- **`report`** command (`report.py`; panel mode "Completeness report (no network)"; `run.ps1 -Mode report`):
  compares the archive database and disk with the latest `posts.csv.gz` export. Expected (not deleted, with
  a file), saved (verified / not yet verified), missing (`not_downloaded_yet`, `not_in_archive`,
  `file_missing_on_disk`, `replaced_on_site`), failed with the last reason (`http_403`, `http_404`,
  `http_429`/`http_503`, `network_error`, `md5_mismatch`, `wrong_size`, `verify_*`, `login_only`,
  `marked_deleted`), skipped (`filter`, `tier_limit`), extra / `.part` / quarantined files, saved posts
  deleted on the site or absent from the export, and a per-director breakdown. Options: the usual order and
  filter options, `--filter-tags`, `--export FILE`, `--no-disk-check`, `--json`.
- Output in the archive folder: `completeness-report-<time>.txt` and `.html`, `missing-<time>.csv` and a
  `missing.csv` copy (post_id, md5, ext, size, director, category, reason, last_error, url), and
  `extra-files-<time>.csv` when there are extras. A one-line summary in the log and panel, a `report`
  progress event, and `state.last_report`.
- **`full` writes the report automatically at the end**, after the checksum pass (`--report-after`, default
  on; `--no-report-after`, env `E6AI_REPORT_AFTER`, ini `report_after`, panel "Completeness report when
  finished"). The exit code is unchanged unless the report itself fails (exit 1). An archive without a
  posts export skips it with a note.
- Panel **Open report** button (enabled once a report exists) and Advanced > Report options.
- Failure reasons are kept: new table `file_errors` (post id, reason, detail/URL, time), written by the
  parallel downloader and by `verify`, cleared when the file is saved.
- `run.ps1 -NoReportAfter -ReportExport FILE`; `run.bat ... report`.

### Fixed
- **Stale export from the CDN:** `static1.e6ai.net` serves `db_export/*.csv.gz` with a 31-day Cloudflare
  cache, and on 2026-11-04 a plain request returned the 2026-11-02 posts export although the listing already
  described 2026-11-03 (sha256 mismatch). A mismatching export is now retried once with a cache-busting query;
  if it still mismatches the error says the CDN copy is stale and to rerun later. Nothing is kept either way.

### Figures (2026-11-03 posts export, counted from every row)
- 193,772 rows, 31,477 deleted, **162,295 posts with a file** (114 pending), 565,031,687,943 bytes =
  **526.23 GiB**. 156,961 images (png 113,098, jpg 42,859, webp 588, gif 416) and 5,334 videos (webm 4,311,
  mp4 1,023). Source: https://e6ai.net/db_exports.json and
  https://static1.e6ai.net/data/db_export/posts.csv.gz (sha256 checked against the listing).

## 1.6.0 (2026-11-04)

**Fast mode** (parallel file downloads) and **md5 checksum verification**, at the owner's request after a
Discord message attributed to e6ai's chief architect (DRLa). That authorization is a screenshot only and
was **not independently verified**. Nothing in this release was run against the live site: all speed and
behaviour numbers come from local test servers. 209 offline tests pass (181 earlier, 6 of them updated, + 28 new).

### New
- **Parallel downloads** from the static file host (`fast.py`): `--workers N` (1-64, default 16), env
  `E6AI_WORKERS`, ini `workers`, panel Basics > Speed > "Parallel downloads". One keep-alive session per
  worker, no per-request limit for static files, same descriptive User-Agent. Workers only download; the
  main thread is still the single SQLite writer. Each file streams to `<name>.part` and is moved into place
  with an atomic replace. Duplicate md5s are fetched once and hard-linked. Fast mode is the default.
- 403 / 429 / 503 for a file: the file is marked `failed` (429/503 after 3 more tries, honouring
  `Retry-After`) and that worker backs off. **20 consecutive file 403s** stop the run cleanly (exit 3).
  A 403 from the API host still stops at once, as before.
- **Check md5 while downloading** (`--check-md5`, `check_md5`, panel checkbox), default off.
- **`verify`** rewritten: parallel hashing (`--hash-workers`, default one per CPU core), resumable
  (checkpoint per batch, `--restart`, `--only-new`), progress events (files, MiB/s, ok/bad/absent).
  Zero-byte, truncated and mismatched files are deleted (default) or quarantined (`--action quarantine`,
  `archive/quarantine/<same path>`), then re-queued with absent files. `--redownload` downloads them again
  (default off on the command line so `verify` stays offline; on in the panel).
- Panel mode **"Verify checksums (re-download bad files)"**, and **"Verify when finished"** for the full
  archive (`full --verify-after`, default on): files not yet md5-checked are verified, bad ones re-fetched.
- Progress: aggregate speed of all workers, in-flight bytes in the totals and ETA, "N of W parallel
  downloads active" in the window.
- `download --dry-run` / `preview`: time estimates are bandwidth-bound (50 / 100 / 500 Mbit/s) and show
  `parallel_downloads` and `api_rate_req_per_s`.
- Launchers: `run.ps1 -Workers N -CheckMd5 -VerifyAction delete|quarantine -NoVerifyAfter`, modes `verify`
  (with re-download) and `verify-only`; `run.bat NAME MODE [WORKERS]`.

### Changed
- **Removed the 2 requests/second hard cap** and the "above the polite rate" warnings (panel and CLI).
  `--rate` now only limits API requests and can be set freely (default 1). A note is logged above 2/s,
  because the e621-family API documentation asks for about 2 requests/second or less; it does not block.
- Basics tab: "Politeness" became **Speed** (parallel downloads, API requests per second), **Checksums**
  and **Disk and power**.
- The descriptive User-Agent, browser-UA refusal, API-host 403 stop, disk-space checks, resume, Stop and
  Pause are unchanged.

### Tests updated (with reasons)
- `test_offline.test_rate_cap`: expected a clamp to 2/s; now expects the rate kept and an INFO note
  mentioning the ~2/s API guide (cap removed at the owner's request).
- `test_offline.test_md5_mismatch_and_404`: runs with `check_md5=True`, since same-size corruption is now
  caught by `verify` rather than inline by default.
- `test_v12.test_folder_deleted_mid_run`: uses `workers=1` (the scenario depends on strict order); the
  parallel case is covered in test_v16. Also fixed: a folder deleted between the last write and the rename
  is now re-created and the file fetched again instead of stopping the run.
- `test_v12.test_stop_mid_download`: completion order is not fixed with workers, so Stop is pressed on the
  first finished file of 40 and the test checks DB/disk agreement and no `.part` instead of an exact count.
- `test_v13` order switch: compares the set of downloaded posts, not their completion order.
- `test_v14` / `test_v15` rate tests: 2.5 / 9 / 50 req/s are no longer errors or clamped (cap removed);
  `test_errors_disable_start` uses an invalid value ("fast") instead; the round-trip sampler flips a flag's
  default (verify's `--redownload` is on in the panel).

## 1.5.0 (2026-11-04)

Optional **sign-in** so posts that e6ai hides from anonymous visitors can be archived. e6ai (e621ng) does not
accept passwords from tools: the archiver signs API requests with HTTP Basic auth using your **login name +
API key**. It never asks for, sends or stores your password and never uses the website's login form.
Politeness unchanged (UA with username, 1 request/s default, 2/s hard cap, immediate stop on 403).
No signed-in live requests were made for this release. 181 offline tests pass (146 earlier + 35 new).

### New
- Control panel, **Basics** tab, group **Sign in to e6ai (optional)**: login name (blank = username), API key
  (masked, **Show** toggle), **Where do I get this?** (opens https://e6ai.net/users/home), **Test sign-in**
  (one request in the background, result shown inline: signed in as NAME (level) / HTTP 401 wrong login or
  key / 403 stopped / account safe mode warning; 3 s cool-down; after a 403 it asks before testing again).
  The login / key fields moved here from Advanced (now "Identity": custom User-Agent).
- **Remember API key on this PC** (optional `keyring` package: Windows Credential Manager, macOS Keychain,
  Secret Service) and **Forget saved key**. Hidden, with a short note, when `keyring` is missing or has no
  usable backend. `pip install e6ai-archiver[keyring]` or `pip install keyring`.
- Signed-in `full` / `run` / `download` first re-check posts recorded as `no_url` (hidden from anonymous
  visitors; deleted posts excluded): 100 ids per request (`status:any id:...`), visible ones go back to
  `pending` and are downloaded in the normal order. Skipped when the same account already checked the same
  hidden set within 24 h. `--no-login-retry` / `login_retry = false` / panel checkbox turn it off.
- `recheck` uses the same code (100 per request, deleted posts included) and reports now_downloadable /
  still_hidden / not_returned.
- New command **`test-login`**: one request to `/users/<login>.json`; "signed in" only if the reply contains
  fields e621ng shows only to the account owner. Exit 0 ok, 8 rejected (401) or not your account, 3 = 403,
  2 = no key / no username. Same UA, rate limiter (2/s cap) and 403 stop as every run.
- New exit code **8**: sign-in rejected (HTTP 401). Previously a 401 was a generic error.
- `stats` reports `login_required` (posts without a file URL, not deleted); the panel shows the count under
  the sign-in group and refreshes it after each run.
- The log states `auth: signed in as NAME (API key from E6AI_API_KEY, not shown)` or `auth: no (anonymous)`.

### Security
- The API key goes only in the `E6AI_API_KEY` environment variable of the child process: never on the command
  line, never in the ini (also not written by Save settings), never in the command preview.
- The key is masked as `***` in log files, console output, uncaught tracebacks, the panel log pane and the
  test result (`redact.py`, `RedactingFormatter`).
- Basic auth is sent only to `https://e6ai.net` (API), no longer to the file server or other hosts
  (it used to be set on the whole HTTP session).
- A login name without a key no longer aborts (ValueError); the run is signed out with a warning.

### Unchanged / not changed
- No password login, no HTML login form, no cookies. 403 still stops everything.
- Posts from the daily export keep their md5 and file URL and are never `no_url`.

## 1.4.0 (2026-11-04)

The **control panel** is now the main window. It builds a normal command line and runs it as a hidden
background process, so the panel and the CLI behave identically. Politeness rules unchanged (descriptive
User-Agent with your username, 1 request/s default, hard cap 2/s, immediate stop on 403, md5 checks,
`.part` files, SQLite WAL). No live requests were made for this release. 146 offline tests pass
(103 from 1.3 + 43 new).

### New
- Control panel (`e6ai_archiver/control_panel.py` logic, `panel_ui.py` Tk window). Opens from
  `run_gui.bat` (no arguments), `run.ps1 -Mode panel`, `e6ai-archiver --gui` with no command,
  `python -m e6ai_archiver.control_panel`, and the new `e6ai-archiver-gui` entry point (no console).
- 17 modes (every command, plus retry / dry run / reorganize-apply variants) and 51 option widgets in four
  tabs (Basics, Download order & filters, Naming & folders, Advanced): checkboxes, lists, spin boxes,
  editable preset lists, Browse buttons (folder chooser for the archive folder, file choosers for the log
  and export files). Options the chosen mode does not use are greyed out and left out of the command.
- Archive folder: free space on that drive, an example file path, warnings for OneDrive / Dropbox /
  Google Drive / iCloud folders, network (UNC / mapped) drives and very long paths.
- Live name preview (three example posts) for the naming templates; invalid templates are shown in red.
- Inline validation (Start stays disabled until fixed): username required for network modes, browser-like
  User-Agents refused, rate > 2 refused and > 1 warned, numbers / ranges checked, mode-specific inputs.
- Command preview with Copy; tooltips and a help line for every option; confirmations before Full
  archive, Reorganize (move) and Repair; closing during a run asks and then stops cleanly.
- Progress / log view (overall, tier, current file, speed, ETA, counters, coloured log), friendly
  messages for every exit code, Open archive folder, Open log.
- Start / Pause / Stop. Stop and Pause go through a small control file (`--control-file`) that the run
  polls; Stop finishes the current request or file chunk, discards the `.part` file, checkpoints and
  exits 130 (resumable). Force stop after 45 s. If the panel disappears, the run sees the closed pipe and
  stops cleanly too.
- Settings: Save writes `e6ai-archiver.ini` (other keys kept, `.bak` made once; panel-only values in
  `[gui]`, ignored by the CLI). Reset to defaults. Window size / position / tab remembered in
  `%APPDATA%\e6ai-archiver\panel-state.json`. DPI-aware on Windows; fits 1280x800.
- The API key is never written to the ini or the command line: the panel passes it to the run as
  `E6AI_API_KEY` in the child environment only (and strips other `E6AI_*` variables, since every value is
  passed explicitly).
- CLI: `--no-config`, `--login`, naming options `--director-exclude`, `--rating-style`,
  `--name-max-items`, `--multi-sep`, `--name-max`, `--max-path` (previously ini-only); hidden
  `--progress-json` (JSON progress lines on stdout) and `--control-file`.

### Changed
- `meta --max-items` no longer collides with the naming key `max_items`: the naming option is
  `--name-max-items` (ini key still `max_items`), `--max-items` is only the meta limit.
- User-Agent version is now `e6ai-archiver/1.4`.
- `run_gui.bat` with no arguments opens the control panel; `run_gui.bat NAME MODE` keeps the 1.3 behaviour
  (one mode with the simple progress window). `run.ps1` gains `-Mode panel`.
- The 1.3 progress window still exists for `--gui <command>` / `run.ps1 -Gui`.

## 1.3.0 (2026-11-04)

Priority download order driven by the official daily exports. Politeness rules unchanged (1 request/s
default, hard cap 2/s, descriptive UA, immediate stop on 403). Live requests for this release: 8
(1 API `id:` lookup, 1 export listing, 6 small exports, all with the UA
`e6ai-archiver/1.3 (testing; contact via owner)`, no 403). 103 offline tests pass.

### New
- `--order default|popular|favorites|newest|oldest|smallest-first|balanced` (CLI, ini `order`,
  `E6AI_ORDER`, `run.ps1 -Order`, GUI dropdown). `balanced` = 0.5 score pct + 0.3 favs pct + 0.2 small-file pct.
- Filters `--min-score`, `--min-favs`, `--rating`, `--director`, `--tags`/`--filter-tags`, `--max-size-mb`,
  `--exclude-ext`; tiers `--limit-gb`, `--top`.
- SQLite `queue` table (rank, cumulative bytes, indexed): about 0.4 s to rank 162k files, stable across
  resumes, rebuilt only when order/filters/exports change; finished files keep their rank.
- `sync-exports` (`run.ps1 -Mode sync`): refresh the bundle at most once per 20 h (`--force`), sha256
  verify, decompress + header check, keep `previous/` copy, `manifest.json`; incremental merge (new
  posts, updated scores/favs/tags, deleted-since-last-export marked deleted, no file re-downloads);
  pools export loaded into `pool_posts`; `parent_id` stored.
- `--exports all|core|list` for `full` and `sync-exports`. `exports --download` now uses the same
  verified path.
- Before downloading export-seeded files, their API JSON is refreshed in batches of 100 ids
  (`posts.json?tags=id:...`, 1 request per 100 files; `--no-api-json` to skip).
- `preview --order ... --count N [--json]`: top of the queue (rank, score, favs, rating, size,
  cumulative GiB, path) plus a tier table (top 1k/10k/50k/all: files, GiB, hours, remaining).
  `download --dry-run` reports the selected tier.
- Window: **Download order and filters** panel before Start (validated; `--autostart` skips it),
  tier progress bar, "rank X of N in tier" for the current file.
- `run.ps1`: `-Order -MinScore -MinFavs -Rating -Director -Tags -MaxSizeMB -ExcludeExt -LimitGB -Top
  -Exports -Count -Autostart -Force`, new `sync` mode.

### Changed / fixed
- Start button no longer covers the Director(s) box in the order panel (grid overlap; test added).
- **403 during an export sync now stops** (before, `ForbiddenError` subclassing `RuntimeError` was
  caught as an ordinary export failure and the next export was tried). Same for disk full / drive gone.
- The posts export merge is one transaction: a stop mid-seed leaves the DB unchanged (no partial seed).
- Export columns are mapped by header name with aliases; unknown columns are logged; a corrupt/truncated
  gzip is rejected and the previous copy is kept, and an export with far fewer rows than last time
  never marks the missing posts as deleted.
- `preview --json` output is now `{"order", "tier_end_rank", "rows", "tiers"}` (was a bare list);
  `--limit` is still accepted as an alias of `--count`.
- `--api-json/--no-api-json` and `--autostart` are given after the subcommand (they belong to
  `run`/`full`/`download`/`preview`/`sync-exports`), like the other order options.
- Requires SQLite 3.33+ (any Python 3.10+ from python.org has it).

## 1.2.0 (2026-11-04)

Progress window, metadata file names, custom folders with director subfolders, and a crash-proofing
pass. Politeness rules unchanged (1 request/s default, hard cap 2/s, descriptive UA, immediate stop
on 403). No new live requests were made for this release; 86 offline tests pass.

### New
- **Progress window** (`--gui`, `run.ps1 -Gui`, `run_gui.bat`, `gui = true`): overall bar (files and
  bytes), current file with its own bar (post id, director, name, size), current/average speed, ETA,
  counts, free disk, phase, requests made, network state, last problem, elapsed time and a live log.
  Pause/Resume and Stop buttons; closing the window stops cleanly. Worker thread + message queue
  (the worker never touches Tk); falls back to the console if tkinter or a display is missing.
- **File names from metadata**: `name_template`, default `{source_name}_{id}.{ext}` (max 60 chars).
  `{source_name}` is a best-effort stem from the source URLs, because e6ai keeps no original filename.
  Presets `default`, `detailed`, `full`, `short`, `minimal`, `md5` (`--name-preset`).
- **Custom folders**: `folder_template`, default `{director}/{rating}` (one subfolder per director;
  `_no_director` when there is none). Presets via `--folder-preset`. `--multi-director first|multi|copy|hardlink`.
  Warning tags in the director category are excluded (`director_exclude`).
- **Windows-safe sanitizer** for every path component (illegal/control/bidi characters, trailing dots,
  reserved names incl. `COM¹`, NFC, UTF-16 length) and a whole-path budget (`max_path`, 240).
- `full` command: exports + tags + seed + downloads + API catch-up in one resumable step
  (`run.ps1 -Mode full` now uses it). The tags export is joined so seeded posts get director categories.
- `preview` (offline): where the next files would go. `reorganize` (dry run by default, `--apply`
  moves; never deletes or overwrites; audit CSV; resumable). `repair-db`: backup + rebuild from journals.
- New exit codes `6` (archive drive gone / not writable) and `7` (database damaged).
- 1.1 archives (`data/<md5>/...`) keep working; their files are moved into the new layout.

### Crash cases fixed (each has a test)
- Null / missing fields, unknown tag categories, huge tag lists, emoji / RTL / combining tags, names
  that are reserved on Windows, paths over 260 characters, non-ASCII archive paths.
- Duplicate md5 across posts; file or folder deleted mid-run; leftover `.part` and zero-byte files;
  repeated md5 mismatch (set aside, not looped).
- USB drive removed / read-only folder (clean stop, exit 6, files not marked failed).
- Damaged SQLite database (exit 7 + `repair-db`), half-written JSONL line after power loss.
- Network down for hours, DNS failures, 5xx storms: waits with backoff (max 5 min) up to
  `max_outage_hours` (12) instead of exiting; `Retry-After` honoured (seconds or HTTP date, capped).
- Clock changes / sleep: rate limiter and timers use a monotonic clock.
- Malformed or non-UTF-8 ini file: clear error, exit 2.
- Two instances on one archive (exit 5); Ctrl-C during seeding; GUI window closed abruptly.

### Notes
- Paths are decided when a file is saved; later template changes or tag edits on the site do not
  rename existing files (a warning says so). Use `reorganize`.
- Most posts have no usable source, so most default names are `<id>.<ext>` (see README for the numbers).

## 1.1.0 (2026-11-04)

Full code review and optimization pass. No change to the politeness rules: default 1 request/s,
hard cap 2/s across API + files, descriptive User-Agent (browser UAs refused), immediate stop on 403.
No new live requests were made for this release; everything was tested offline (40 tests) plus an
offline seed of the real 2026-11-02 `posts.csv.gz` and a 1.0-archive compatibility check.

### Bugs fixed
- **`--metadata-only` then `download` did nothing.** 1.0 stored metadata-only posts as `skipped`, but
  `download` only processes `pending`. Metadata-only posts are now stored as `pending` (they simply
  aren't downloaded during that run). Existing 1.0 databases are migrated (`skipped` -> `pending`)
  automatically on first open (schema_version 2). `seed-from-export --metadata-only` was removed for
  the same reason (seeding never downloads anyway).
- **A connection drop in the middle of a file crashed the whole run.** Errors raised while reading the
  body (`ChunkedEncodingError`, `ConnectionError`, read timeouts) were not retried. They are now retried
  3 times with backoff; after that the file is marked `failed` and the run continues. Local disk errors
  still stop the run (so files aren't wrongly marked failed). Same retry for export downloads.
- **Failed files were never retried.** `missing` / `md5_mismatch` / `failed` were final. New
  `download --retry-failed` (and `run.ps1 -Mode retry`) re-queues them.
- **A 200 response that isn't JSON** (e.g. a maintenance page) crashed with a decode error. It is now
  retried twice with backoff, then reported clearly (exit 1, resumable).
- **Fragile default-subcommand detection.** 1.0 looked for words like `run` anywhere in the command
  line, so `--out run` or `--tags run` were misparsed. The parser now skips global options and their
  values and inserts `run` only where the first non-global token is not a subcommand.
- **Resource leaks / uncaught errors in `main`.** If opening the DB failed, the lock file and
  keep-awake were not released, and generic errors (`RuntimeError`, `OSError`) produced tracebacks.
  `main` now uses an `ExitStack` (everything released in reverse order) and maps errors to exit codes
  (new `1` = other error; disk-full `OSError`s ENOSPC/ERROR_DISK_FULL -> `4`).
- **Windows: `csv.field_size_limit(sys.maxsize)` raises `OverflowError`** (C `long` is 32-bit on
  Windows), so `seed-from-export` would have failed on the target machine. Now uses `2**31-1`.
- **`verify` used an `Archiver.__new__` hack**; the Archiver now works without a client for offline use.
- **`order:` in `--tags`** silently had no effect (cursor pagination always orders by id). Now warns.
- **Half-set credentials** (only `E6AI_LOGIN` or only `E6AI_API_KEY`) were silently ignored; now an error.
- Seeding: malformed rows (bad id) are counted as `bad_rows` instead of aborting; non-hex md5 values are
  treated as "no file" instead of producing bad paths.
- `exports --download` warns about unknown export names; `seed-from-export` gives a clear error when
  the CSV is missing (instead of a traceback).
- `--log-file` in a folder that doesn't exist yet no longer crashes.

### Optimizations
- **SQLite:** WAL + `synchronous=NORMAL` kept; per-file status updates are now committed in batches
  (every 25 files or 10 s) instead of one commit + fsync per file. JSONL is fsynced only when it has
  new data. `PRAGMA optimize` and a WAL checkpoint on close keep the `-wal` file small.
  Status index is now `(file_status, id)` for the download queue (rebuilt automatically for 1.0 databases).
- **Pending queue streams in keyset batches of 500** (`id < last ORDER BY id DESC`) instead of loading
  every pending row into memory (162k rows for a full archive).
- **Seeding ~193k rows:** batched `executemany` + `INSERT OR IGNORE` (5,000 rows per batch) instead of
  one `SELECT` + `INSERT` per row. Measured on the real export: 7.7 s, 56 MiB peak RSS, exact counts
  (162,064 pending files, 564,232,033,788 bytes).
- **HTTP keep-alive:** one `requests.Session` with an explicit `HTTPAdapter` pool (2 connections,
  urllib3 retries off so our own backoff is the only retry layer). Verified in tests: dozens of
  requests reuse 1-2 TCP connections.
- **Timeouts:** separate connect/read timeouts `(15 s, 120 s)` instead of a single 60 s.
- **1 MiB chunks** for streaming md5/sha256 and writes (was 64 KiB).
- Each post's JSON is serialized once (was twice).
- Backoff now retries up to 8 times (about 8.5 min of patience) instead of 6.

### New features
- **Progress/ETA logging** during downloads every 30 s: files done/total, bytes, MiB/s, ETA
  (max of files-left/rate and bytes-left/measured speed), plus a batch summary.
- **`--dry-run`** for `run` and `download`: no network and no username needed. Reports pending files,
  exact total bytes, free disk, whether it fits, minimum time at the configured rate, and the
  checkpoint cursors. `run.ps1 -Mode dryrun` wraps it.
- **Config file + environment variables.** Precedence: command line > `E6AI_*` env vars > INI file >
  defaults. New env vars `E6AI_OUT`, `E6AI_RATE`, `E6AI_MIN_FREE_GB`, `E6AI_LOG_FILE`,
  `E6AI_NO_KEEP_AWAKE`, `E6AI_CONFIG`; new `--config`. `e6ai-archiver.ini.example` included. Notepad
  BOMs and inline `;` comments are accepted; unknown keys and bad values are errors.
- `--version`. `stats` also reports `pending_files`. `seed-from-export` prints the pending totals.

### Windows / scripts
- `run.ps1`: new modes `retry` and `dryrun`; `-Rate`/`-MinFreeGB` are passed only if given (so the
  config file / env apply otherwise) and are formatted culture-invariant (no `1,5` in comma locales);
  username may come from `e6ai-archiver.ini`; exit code 1 explained.
- `install_daily_task.ps1` and the README now say clearly that the daily task is **OPTIONAL and not
  needed**; nothing runs it automatically.

### Tests
- `tests/test_offline.py` rewritten as a `unittest` suite: 40 tests (was 1 scripted scenario).
- Run with `python -m unittest discover -s tests -v`.

### Docs
- `REVIEW.md` now contains key code snippets with file paths and line numbers for reviewers, the
  pre-run checklist, and known limits. `CHANGELOG.md` added.

## 1.0.0 (2026-11-03)
- Initial release: API crawl with b/a cursors, resumable checkpoints, md5-verified downloads,
  SQLite + JSONL storage, DB export download + seeding, Windows launchers.
