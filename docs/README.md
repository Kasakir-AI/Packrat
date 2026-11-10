# Packrat manual

Packrat browses, downloads and organizes images from many sites: Danbooru, Gelbooru, e621, e926, e6ai,
yande.re, Konachan, rule34, Safebooru, Sankaku, Anime-Pictures, behoimi, Zerochan, Shimmie and Philomena
boorus, Pixiv, E-Hentai, Twitter/X, Reddit, DeviantArt, ArtStation, Tumblr and more. It does what Grabber (imgbrd-grabber) does, and keeps the whole-site archiver it grew out of
(e6ai-archiver) as the **Site archive** page, which now also works for e621.

- **Browse**: search several sites at once, thumbnails, a viewer, tag lists with colours, autocomplete,
  post-filters, blacklist, favorites, kept-for-later, reverse image search.
- **Download**: whole searches or single images, resumable, in the background; duplicates recognised by
  MD5 before downloading; 4 files at a time by default.
- **Customize**: Grabber's complete file name language (tokens, options, conditionals, JavaScript), per-tag-
  category rules, conditional file names, custom tokens, metadata (ExifTool), conversions, commands, SQL.
- **Organize**: rename files you already have, find blacklisted ones, an MD5 list fixer, empty folders, a
  local booru (Szurubooru / Danbooru / Shimmie / MyImouto) with no extra scripts.
- **Site archive**: a complete, verified copy of e6ai or e621 (see [SITE-ARCHIVE.md](SITE-ARCHIVE.md)).

Most people only need **Start Packrat.bat** and the plain-text **START HERE - README.txt**. Inside Packrat:

- **? Help** (top right) opens the help menu; **F1** opens the guide for the tab you're in. The help window has
  a question box: type in your own words (`how do I save a whole search`, `e621 login`) and it finds the right
  page or setting. There's a guide for every site: what it has, whether it needs a login, and how to get one.
- **Help > Run the setup assistant again** changes your sites, download folder, file names and view.
- **Simple and advanced view.** Packrat starts simple: Browse, Downloads, Favorites, Site archive and a short
  Settings page. Tick **Advanced options** (top right) for the Monitors, Tools and Log tabs, filters, merge,
  pictures per page, list ordering, every setting (200+) and each site's speed limits. Hidden options keep
  working; switching loses nothing. A guide link to something hidden asks before showing it.

Screenshots (in `screenshots\`): [Browse](screenshots/packrat-browse.png), [dark](screenshots/packrat-browse-dark.png),
[Sites](screenshots/packrat-sources.png), [Viewer](screenshots/packrat-viewer.png),
[Downloads](screenshots/packrat-downloads.png), [Favorites](screenshots/packrat-favorites.png),
[Monitors](screenshots/packrat-monitors.png), [File names](screenshots/packrat-settings-filenames.png),
[Tools](screenshots/packrat-tools.png), [Site archive](screenshots/packrat-site-archive.png),
[Help](screenshots/packrat-help.png), [Help search](screenshots/packrat-help-search.png),
[More search options](screenshots/packrat-search-options.png), [Advanced view](screenshots/packrat-browse-advanced.png),
[First start](screenshots/packrat-first-start.png).

Contents: [Quick start](#quick-start) - [Browse](#browse) - [Viewer](#viewer) - [Downloads](#downloads) -
[File names](#file-names) - [Post-filters and the blacklist](#post-filters-and-the-blacklist) -
[Favorites, kept for later, monitors](#favorites-kept-for-later-monitors) - [Sites, sources and
logins](#sites-sources-and-logins) - [Settings](#settings) - [Tools](#tools) - [Local booru](#local-booru) -
[Command line](#command-line) - [Coming from Grabber](#coming-from-grabber) - [Upgrading from
e6ai-archiver](#upgrading-from-e6ai-archiver-20) - [Folders and files](#folders-and-files) - [Exit
codes](#exit-codes) - [Limits and differences](#limits-and-differences) - [Credits](#credits)

---

> **3.1 note.** The window was rebuilt in 3.1. This manual's chapters on searching, file names, filters, favorites,
> monitors, sites, settings, tools and the command line still apply. What changed is where things are: pages are in
> a list on the left instead of tabs along the top, "Sites and logins" and "Help" are at the bottom left, every page
> is always visible (there is no "Advanced options" box), videos play inside the picture window, and Packrat has an
> icon next to the clock. See `CHANGELOG.md`, "3.1.0", and the built-in help (F1).

## Quick start

1. Install Python 3.13 or 3.14 from <https://www.python.org/downloads/windows/> and tick **Add python.exe to
   PATH** on the installer's first screen. (Double-click **Check Python.bat** to see that it is found.)
2. Unzip Packrat to a simple folder such as `C:\Packrat` - not inside OneDrive or Program Files.
3. Double-click **Start Packrat.bat**. The first start sets itself up (about two minutes, needs internet),
   then a short assistant asks which sites you want, where to save and how to name files.
4. In **Browse**, press **Sites** (the button left of the search box) and tick the sites to search. Type
   tags, press Enter. Click a picture to view it.
5. To download: **Download all results...**, **Download this page**, or Ctrl+click pictures and
   **Download selected**. They go to the **Downloads** tab; press **Start** there.

Files go to `downloads\` in the Packrat folder by default (Settings > Saving), named by their MD5. To name
them by artist, character, etc. see [File names](#file-names).

**Be polite.** Every site asks for a reasonable speed; Packrat waits 1 second between pages and between files
on each site by default (Grabber's defaults; e621-family static files have no limit). Sites that require it
(e621, e926, e6ai) get a User-Agent with your name: set **Settings > Your user name on e621 / e926 / e6ai**.

---

## Browse

| Where | What it does |
|---|---|
| **Sites** button | which sites this tab searches (remembered for new tabs). Each site shows what it has, whether it has adult content and whether it needs a login, with its guide on the right. **Log in / settings...** (or double-click) for login, speed limits, API order, referer; **Test this site** checks it works. **Add another site...** guesses the source type of any booru (or finds it in Grabber's source registry). |
| **More options...** | sort (oldest, best score, most favorited, random), only safe / no explicit pictures, only big ones, tags to leave out - in plain words. Packrat writes the search words. Only what every ticked site supports is offered (taken from each site source's own list); the rest is greyed out and left out. Like Grabber's *Plus* window. |
| Search box | tags, as on the site. Autocomplete from tags you have seen, your favorites and each site's tag database (Tools > Tag loader). `-tag` excludes, `~tag` is "or" on sites that support it. Enter searches. |
| ◀ page ▶ / per page | paging (*Pictures per page* is in the advanced view, next to the filter). Sites that page by id (e621, Danbooru) are followed exactly, so nothing is skipped. |
| Filter results *(advanced)* | filters applied after the site answered (Grabber's "post-filter", see below). Settings > General has a global one for every tab. The tab's filter only applies in the advanced view. |
| Merge sites *(advanced)* | one list for several sites; the same image (same MD5) found twice is shown once. |
| **Saving to ... Change...** | where pictures go and how they're named, always in view (like Grabber's *Destination* panel). **Change...** picks the folder and a name format, with an example. |
| Tags list | the tags of the results with counts and colours by type. Click: search it. Ctrl+click: add to the search. Middle-click: new tab. Right-click: favorite, keep for later, blacklist, ignore, exclude, copy. |
| Pictures | click: open the viewer. Ctrl+click: select (Shift+click: a range). Right-click: save now, add to the list, open in browser, copy addresses, reverse image search, show the saved file. Borders: green = already saved, pink = has a favorite tag, grey = blacklisted (or hidden, Settings > Interface). Badges: video, GIF, gallery, saved. Hover for details. |
| **Download selected / Download this page / Download all results...** | add to the download list. *Download all results* asks for the folder, file name, how many images, and whether to include blacklisted ones. |
| **Favorite / Monitor...** | keep the search as a favorite / check it regularly for new images (*Monitor...* is in the advanced view). |

Tabs: **+** opens one, Ctrl+T too, Ctrl+W or middle-click closes, right-click a tab to duplicate it, go
**Back** / **Forward** (also Alt+← / Alt+→) or pick one of your **Recent searches** (an empty tab lists them
too). F5 reloads.

- **A picture's address** (e.g. `https://e621.net/posts/12345`) pasted in the search box finds that picture on
  its site.
- **Galleries** (E-Hentai, NHentai, Pixiv albums, ArtStation projects, Mastodon posts...) show **▤** and their
  size; a click opens them in their own tab (Grabber's gallery tab). E-Hentai's sprite-sheet thumbnails are
  cropped like Grabber does. *Download all results* in a gallery tab saves the whole gallery.
- **About this tag...** under the tag list shows the site's wiki text for the search, when it has one
  (Grabber's Wiki panel).
- Right-click a picture: also *Find this picture on the ticked sites (MD5)*, *Copy all tags* (with or without
  their types) and *Use as the picture of favorite ...*. Right-click a tag: also *Copy all tags in the list*.
- When a site answers with an error page (403, 503...), the tab says so instead of showing an empty page. The open tabs come back next time (Settings > General > When Packrat opens). "Load more" or
infinite scrolling: Settings > Interface.

## Viewer

Fits the picture to the window (Settings > Viewer: samples instead of originals for speed, scale up small
pictures, background colour). Animated GIFs play. Videos have a **Play in your video player** button (Packrat
downloads it to its cache once, then opens it with the program Windows uses for that type).

Keys: ← → or the mouse wheel: previous / next. **S**: save (with the normal file name). **Ctrl+S**: save as.
**Ctrl+Alt+S**: save to the favorites folder (Settings > Saving > Favorites folder / file name, as in Grabber).
F5: reload. Space: slideshow. F11 or double-click: full screen. Esc: close. Middle-click: close.

**More**: save to the favorites folder, **copy the picture** to the clipboard (Windows; paste it anywhere),
copy its file or page address, show the saved file, reload.

The left side lists the tags (click: search in a new tab; right-click: the same menu as in Browse) and the
details: site, id, rating, score, size, type, date, MD5, sources (clickable) and where it is saved. **Search
on** opens the picture on a reverse image search site (IQDB, SauceNAO, Google Lens, TinEye, Yandex, Bing,
e621's own search - edit the list in Settings > Network > Web services). The next and previous pictures are
loaded in advance (Settings > Viewer > Preload).

## Downloads

The download list holds **searches** (from *Download all results*) and **single images** (from Browse, or
**Add a picture...** with a page address - Grabber's *Add unique*).
**When everything is downloaded:** (do nothing, notify, sound, shut down) and **Open the download folder** are
under the progress bar. **Start** runs everything not yet
finished, **Start selected** only the selected rows. It runs in the background (a separate process), so the
window stays fast; **Pause** and **Stop** work at once, and **Start** later continues exactly where it stopped
(each search remembers its last page).

For each image, in this order: blacklist and post-filter checks (they need the image's details on some sites,
which Packrat loads only when needed) - the MD5 list (*already saved elsewhere?*: Settings > Saving decides:
save again, copy, move, link, hard link or skip) - the file name - downloading to a `.part` file, checking its
size, renaming it - then the after-save steps: separate log files, the date of the file, conversion, resizing,
metadata, a `.json` sidecar, commands, SQL, the local booru.

Rows can be edited (double-click), reordered, reset to start over, removed. **Save list...** writes a `.prl`
file; **Load list...** reads `.prl` and Grabber's `.igl` lists. Failed images are listed in
`data\logs\failed-*.jsonl`. When the list finishes, Settings > Saving > *When a download list is finished* can
play a sound or shut the PC down.

## File names

**Settings > File names**, Grabber's complete language, so formats written for Grabber work unchanged. The short version:

| | |
|---|---|
| `%md5%.%ext%` | the default |
| `%artist%/%copyright%/%character%/%md5%.%ext%` | folders by artist, then series, then character (`/` makes a folder) |
| `%website% - %id%.%ext%` | site and post id |
| `%search_1%/%id%.%ext%` | a folder per search |

Tokens: `id md5 ext filename website websitename date rating score width height ratio mpixels filesize
filetype author authorid parentid has_children has_note has_comments source sources url_file url_sample
url_thumbnail url_page url_original pool pools fav_count description search search_N position count num
gallery name tags all allo allos general artist copyright character species meta model lore contributor
circle photo_set invalid current_date grabber`, plus your **custom tokens** (Settings > File names > Custom
tokens: e.g. `hair` = the image's tags found in your list `blonde_hair red_hair ...`).

Options after a colon, e.g. `%artist:maxlength=40,separator=+,unsafe%`, `%date:format=yyyy-MM-dd%`,
`%id:length=8%` (zero padded), `%md5:maxlength=8%`, `%all:includenamespace,excludenamespace=general%`,
`%all:sort%`, `%rating:case=upper%`, `%ext:nojpeg%`, `%count:length=3%`, `%num%` (a number that grows when the
name is taken).

Conditionals: `<%artist%/>` is written only if there is an artist; `<rating:explicit?nsfw/:sfw/>`;
`<"cat_ears"?neko - >`; `<!tag?text>` negation, `&`, `|` and parentheses. **JavaScript**: start the format with
`javascript:` and return the name, e.g.
`javascript:(artist ? artist + "/" : "") + md5 + "." + ext` (every token is a variable; dates are Date objects).

**Per tag category** (Settings > Tag tokens): what `%artist%` etc. become when there are none
(`anonymous`, `unknown` ...) or several (keep all / keep N / keep N then add / replace all by `multiple artists`
/ **one file per tag**), separators, sorting, and namespaces.

**Conditional file names** (Settings > File names > Rules): another format and folder when the image matches a
condition, e.g. condition `rating:explicit`, folder `D:\nsfw`. The last matching rule wins.

The field shows at once whether the format is valid; names are always made safe for Windows (forbidden
characters replaced, each part and the whole path kept under Windows' 260-character limit).

## Post-filters and the blacklist

Both use the same language, one rule per line for the blacklist and one per word for post-filters:

| | |
|---|---|
| `tag`, `-tag`, `tag*` | has / hasn't a tag (wildcards allowed) |
| `rating:safe`, `rating:s,q` | rating (letters or words) |
| `score:>10`, `width:>=1920`, `filesize:<5mb`, `id:100..200`, `mpixels:>2` | numbers and ranges |
| `date:>=2024-01-01`, `age:<1w` | dates; age in s, mi, h, d, w, mo, y |
| `source:*pixiv*`, `md5:abc*` | text fields |
| `%artist%:name` | any token |
| `grabber:downloaded`, `grabber:alreadyExists`, `grabber:inMd5List`, `grabber:favorited` | Packrat's own state, e.g. `-grabber:downloaded` hides what you have |
| `(tag1 | tag2) & -tag3` | the advanced form (used as soon as a filter has `( ) & |`) |

**Blacklist** (Settings > Blacklist & tags): a line `tag1 tag2` blocks images that have both. Blacklisted
images are shown greyed out (or hidden) and not downloaded unless you ask for them. **Ignored tags** are left
out of `%all%` and the category tokens; **removed tags** are removed from the image entirely.

## Favorites, kept for later, monitors

**Favorites** are saved searches with a priority, the sites to search and a post-filter. **Check for new
images** counts what was posted since you last opened each one (and picks a thumbnail); **Download new
images** adds a download for just those. Double-click opens it in Browse. **Kept for later** is a simple list of
searches to look at someday (right-click a tag > Keep for later).

**Monitors** check a search every N minutes and report (notification) and/or download what is new. The first
check only notes where "now" is, so a new monitor never floods you with old images (use *Download all results* for those).
*Ignore images newer than* leaves the newest ones for a while so taggers can finish. While Packrat is open,
monitors run by themselves (Settings > Monitoring). While it's closed: Tools > **Schedule monitors**, or
`.\app\install_daily_task.ps1 -What monitors`, which runs `packrat monitors --run-due` every hour.

## Sites, sources and logins

A *source* is the code for one kind of site (Danbooru 2, Gelbooru 0.2, Moebooru, Shimmie, e621 ...); a *site*
is one address using it. Packrat ships Grabber's 31 sources and their default sites, plus e6ai.net and e926.net.
Any other booru running the same software works too: **Sites > Add another site...**, and Packrat guesses which
source fits (or finds a matching one in Grabber's official source registry and offers to install it).

Each site's options (Sites > click the site > **Log in / settings...**):

- **Login**: the method the source supports - API key in the address, HTTP basic, a login form, OAuth 1 or 2
  (Pixiv, Twitter...). **Test login** tries it. Passwords, keys and tokens go to the **Windows Credential
  Manager**, never into a file.
- **Speed limits**: seconds between pages, details, files and thumbnails, wait before a retry, files at once.
- **API order**: which of the source's APIs (JSON, XML, HTML, RSS) to try first.
- **Referer**, https, name. Cookies and headers from Grabber are imported.

New sources: **Tools > Source registries** lists Grabber's official registry (or any other); install or
update a source there. Packrat checks each download against the registry's SHA-256 and keeps its own patched
E621 source (typed tags without extra requests; e6ai and e926 support).

## Settings

Every setting has a help line (point at its name). Changes are saved as you make them, in
`data\settings.json`. **Basics** (the whole Settings tab in the simple view) has the everyday ones in plain
words: your user name, the download folder, file names, what to do with pictures you already have, full-size
or smaller pictures, the blacklist, preview size, pictures per page, start-up and closing. The find box
searches every setting in both views. The advanced view adds the pages: General (start-up, your name, auto-download whitelisted images, tags and filters
added to every search), Sources (per page, merge, API order, autocomplete), Saving (folder, duplicates,
retries, samples, video/zip/animated extension rotation, temporary folder), File names, Tag tokens, Conversion
(WEBM to MP4 with FFmpeg, image conversions with Pillow or ImageMagick, Pixiv ugoira to GIF / WEBP / APNG / MP4, resizing),
Metadata (ExifTool tags such as `XMP:Subject` = `%all:separator=^,%`, Windows properties, a `.json` sidecar),
Interface (thumbnail size, tag colours, favorites display), Viewer, Blacklist & tags, Monitoring, Network
(proxy, timeout, web services) and Commands (commands per image / per tag / start / end, an SQL database,
the local booru).

The theme (light, dark, follow Windows) is at the top right of the window; Ctrl+Shift+D switches.

## Tools

| | |
|---|---|
| **Import from Grabber** | settings, favorites, monitors, kept for later, blacklist, ignored tags, autocomplete words, the MD5 list (`md5s.txt` or `md5s.sqlite`), the download list (`restore.igl`), sites added by hand and their logins. Grabber is not changed; importing twice is safe. |
| **Rename existing files** | moves files you already have to a new file name format: each file is looked up on the site (by MD5 from its name or its content, or by the id in its name). The list is shown before anything moves. |
| **MD5 list fixer** | adds every file of a folder to the MD5 list, so duplicates are recognised even if another program saved them. |
| **Blacklist fixer** | finds saved files whose tags match the blacklist (and deletes them if you say so). |
| **Empty folders** | finds and removes empty folders. |
| **Tag loader** | downloads a site's tag list once, so tag types are known without extra requests. e621 / e926 / e6ai use the daily tags export (one file). |
| **Source registries** | install / update / remove sources. |
| **Schedule monitors** | a Windows scheduled task that checks monitors hourly while Packrat is closed. |
| **Back up the library** | a dated copy of `library.sqlite3` (favorites, monitors, MD5 list, download list). |

## Local booru

Settings > Commands > **Local booru**: every saved image is also uploaded to your own Szurubooru, Danbooru
2.x, Shimmie or MyImouto/Moebooru, with its tags (and their categories on Szurubooru), rating and source. Grabber
needs PHP, Node.js or curl scripts for this; Packrat does it itself. An image the booru already has counts as
done; upload problems never stop a download (5 failures in a row turn the uploads off for that run). The
classic way (a command per image, SQL commands with `%tag%`, `%md5:escape%`...) is there too.

## Command line

The window covers everything; the command line is for scripts and Task Scheduler. From the Packrat folder,
after the first start:

```bat
cd /d C:\Packrat
.venv\Scripts\python.exe -m packrat --help
```

| | |
|---|---|
| `packrat grab -t "cat_ears" -s "danbooru.donmai.us gelbooru.com" --return-images` | Grabber's command line, same options: `-p` page, `-i` per page, `-m` max, `-n` no duplicates, `-r` post-filter, `-b` include blacklisted, `-u/-w` login for this run, `--proxy`, `--no-login`, `-j` JSON, `--load-details`, `--get-details URL`, `--load-tag-database`, and one of `--return-count`, `--return-tags` (with `--tags-min`, `--tags-format "%tag\t%count\t%type"`), `--return-pure-tags`, `--return-images`, `--download` (with `-l` folder and `-f` file name). |
| `packrat batch` | run the download list (`--ids 3 4` for some rows) |
| `packrat monitors --run-due` / `--list` / `--check ID` | monitors |
| `packrat sources --list` / `--types` / `--add HOST [--type T]` / `--test HOST` / `--registry [URL] [--install NAME]` / `--registries` / `--add-registry URL` / `--remove-registry URL` | sites and source registries (Grabber's `source-registry add / list / remove`) |
| `packrat import-grabber [FOLDER] [--dry-run]` | import a Grabber profile |
| `packrat tools md5-fixer / empty-dirs / rename / blacklist-fixer / tag-loader / md5-import ...` | tools |
| `packrat gui` | the window |
| `packrat run`, `full`, `report`, ... `--site e621` | the site archive ([SITE-ARCHIVE.md](SITE-ARCHIVE.md)) |

## Coming from Grabber

Use **Tools > Import from Grabber** (Packrat finds Grabber's settings folder by itself, normally
`%LOCALAPPDATA%\Bionus\Grabber`). Your file name formats, filters, blacklist and settings keep their meaning:
Packrat uses Grabber's setting names, its source code for every site, and its file name and filter languages.
Different on purpose: Packrat's defaults skip already-saved MD5s instead of downloading them again and use 4
simultaneous downloads instead of 10.

## Upgrading from e6ai-archiver 2.0

Unzip Packrat into a **new** folder, start it, then in the **Site archive** tab use **Import settings** (or
the banner that offers it): it reads your old `e6ai-archiver.ini`, and points the archive folder at your
existing archive. The archive itself is used as it is - same database, same files, **nothing is downloaded
again**, and e6ai-archiver 2.0 can still open it. Your saved API key is found again (same Credential Manager
entry). Unzipping over the old folder also works: the old files are simply not used, and
`e6ai-archiver.ini` is read until you save settings in Packrat (then `packrat.ini` is written). `E6AI_*`
environment variables still work.

## Folders and files

```
Packrat\
  START HERE - README.txt     the plain-text guide
  Start Packrat.bat           double-click to start
  Check Python.bat            shows which Python would be used
  app\                        the program (packrat\, run.ps1, ...); app\packrat\sources\ = the site sources
  docs\                       this manual, SITE-ARCHIVE.md, CHANGELOG.md
after the first start:
  .venv\                      Packrat's private Python packages
  data\settings.json          Browse / Downloads settings
  data\sites.json             sites you added, per-site options (no passwords)
  data\library.sqlite3        favorites, monitors, kept for later, MD5 list, download list, history
  data\tags\                  tag databases per site
  data\sources\               sources installed from registries
  data\cache\                 thumbnails and viewer cache (safe to delete)
  data\logs\                  logs, failed-download lists
  downloads\                  default download folder
  packrat.ini                 site archive settings
  archive\                    the default site archive folder
```

Set the environment variable `PACKRAT_HOME` to keep `data\` somewhere else.

## Exit codes

0 done - 1 error (see the log) - 2 settings/option problem - 3 a site answered 403 (stopped; don't retry with
a browser User-Agent) - 4 disk full - 5 the archive is in use by another run - 6 drive gone / not writable -
7 database problem - 8 login rejected (401) - 9 no suitable Python - 10 installing into .venv failed - 11 the
archive is from a newer version - 130 stopped by you (everything resumes).

## Limits and differences

- **Videos** are played by your usual video player, not inside Packrat (Tk cannot play video).
- **Twitter/X, Pixiv, DeviantArt, Tumblr, Reddit** and other non-boorus depend on those sites' current APIs
  and logins; they work exactly as well as Grabber's own sources for them, which change when the sites change
  (update them in Tools > Source registries). Twitter/X in particular needs a login and is often limited.
- **Sankaku and Tumblr** are not in Grabber's official registry; Packrat ships the versions included in
  Grabber's source code.
- Not included: Grabber's translations (English only), its tray icon, its built-in browser for logins
  (OAuth logins open your normal browser instead) and Android support.
- **e6ai.net** announced in November 2026 that it is closing at the end of the month; the Site archive tab
  is the way to keep a complete copy while it lasts.

## Credits

Packrat contains the site sources, file name and filter languages of **Grabber** by Bionus
(Apache License 2.0; see `app\packrat\sources\GRABBER-LICENSE.txt`
and `PACKRAT-CHANGES.txt` for what was changed). The site archive is e6ai-archiver 2.0.
