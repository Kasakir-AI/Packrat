"""Packrat's built-in guides: how to use each part of the window, and a guide for every site.

Text format (rendered by ui_help.HelpView): "# title", "## heading", "- bullet", "1. step", **bold**,
`example` (shown in a box; click to copy), [label](go:TARGET) links. Targets: help:TOPIC, site:HOST,
tab:browse|downloads|favorites|monitors|archive|settings|tools|log, settings:PAGE, sources, url:https://...

Site facts (login, content, search syntax) were checked against each source's code in November 2026.
Sites change without warning: when one stops working, Tools > Source registries may have a newer version.
"""
from __future__ import annotations

# ---- topics --------------------------------------------------------------------------------------
TOPICS: list[tuple[str, str, str]] = []   # (id, title, text)


def topic(tid, title):
    def deco(text):
        TOPICS.append((tid, title, text.strip("\n")))
        return text
    return deco


topic("start", "Getting started")("""
# Getting started

Packrat finds pictures on image sites, shows them to you, and saves the ones you want - with sensible
names, without duplicates, and it can keep looking for new ones by itself.

## In five steps
1. **Pick your sites.** In the [Browse](go:tab:browse) tab, click the site button (left of the search box; it shows which sites this tab searches)
   and tick the sites you want. Not sure? Start with **Safebooru** (safe anime art) or **e926** (safe furry art).
   [Open the site list](go:sources)
2. **Search.** Type what you want in the search box and press **Enter**. Most sites use *tags*: short words
   joined with underscores, like `cat_ears` or `blue_sky`. See [How to search](go:help:search).
3. **Look.** Click a picture to see it big. Use the arrow keys for the next one.
4. **Save.** Press **S** in the picture window to save one picture. To save many: **Download all results**
   (the whole search), **Download this page**, or hold **Ctrl**, click pictures, then **Download selected**.
5. **Start the downloads.** Go to the [Downloads](go:tab:downloads) tab and press **Start**. You can stop
   at any time; **Start** continues where it stopped.

Your pictures go to the `downloads` folder inside the Packrat folder. To change that, or to give the files
names like `artist/123456.jpg`, see [File names and folders](go:help:names).

## Keyboard shortcuts
- **F1**: help for the tab you're in
- **Ctrl+T**: new search tab;  **Ctrl+W**: close it;  **F5**: search again
- **Alt+←** / **Alt+→**: back and forward through a tab's searches
- **Ctrl+Shift+D**: light / dark
- In the picture window: see [Viewing pictures](go:help:view#Keys)

## Where to get help
- Press **F1** or **Help** (bottom left) at any time.
- Type a question in the search box at the top of this window, in your own words: `how do I save a whole
  search` or `e621 login`.
- Every tab has a **?** button that opens the guide for that tab.
- Point at a button or setting for a moment to see what it does.
- Each site has its own guide: [list of sites](go:help:sites).

## Change the basics later
[Run the setup assistant again](go:setup) to change your sites, the download folder,
and how files are named.
""")

topic("search", "How to search")("""
# How to search

Type in the search box and press **Enter**. While you type, Packrat suggests tags it has seen
(**Up/Down** to choose, **Tab** to accept).

## Colours in the search box
Words you type take the colour of what they are: artists, characters, series and species in their tag colours
(the same as in the tag list), your **favorites** pink, **blacklisted** tags red, and special words such as
`rating:safe` or `order:score` in the meta colour. Packrat knows a tag's kind once it has appeared in results;
[Tools > Tag loader](go:tab:tools) teaches it a site's whole tag list. Turn the colours off in
[Settings > Interface](go:settings:Interface).

## Tags
Most sites (Danbooru, Gelbooru, Safebooru, e621, yande.re, Konachan, rule34...) search by **tags**:
- One tag is one thing in the picture, written with underscores: `long_hair`, `cat`, `night_sky`.
- Several tags = pictures that have **all** of them: `cat_ears blue_eyes`
- A minus sign **excludes**: `landscape -people`
- Tilde means **or** (on sites that support it): `~cat ~dog`
- Characters, series and artists are tags too: `hatsune_miku`, `pokemon`.
- If a search finds nothing, check the spelling on the site itself - tags must match exactly.

## Useful extras (on most tag sites)
- `rating:general` or `rating:safe` - only safe pictures (`rating:s` on older sites)
- `order:score` - best first;  `order:id` - oldest first
- `width:>=1920` - big pictures only

## Sorting, safe only, big pictures
Add these words to the search (most tag sites understand them): `order:score` best first, `rating:safe` only safe pictures,
`width:>=1920` big pictures only. Each [site's guide](go:help:sites) says what that site understands.

## Pages
Results come one page at a time. Use the **◀ ▶** buttons, or turn on "Load more" in
[Settings > Interface](go:settings:Interface).

## Going back, recent searches
- **Alt+←** / **Alt+→**: back to the search you did before in this tab, and forward again.
- An empty tab lists your **recent searches**; click one to run it again.
- Right-click a tab's title: **Back**, **Forward**, **Recent searches** (and *Clear the list*).

## A picture's address
Paste the address of a picture's page (e.g. `https://e621.net/posts/12345`) into the search box and press
Enter: Packrat finds that picture on its site. The site must be in your [site list](go:sources).

## Galleries and albums
Some results are a whole set: an E-Hentai or NHentai gallery, a Pixiv album, an ArtStation project. They show
**▤** and the number of pictures. Click one: it opens in its own tab with the pictures inside.
**Download all results** in that tab saves the whole gallery.

## About a tag
When the site has a wiki entry for what you searched, **About this tag...** appears under the tag list.

## Several sites at once
Tick several sites in the [site list](go:sources): every site is searched, and the results appear together.
**Merge sites** removes pictures that appear on more than one site.

## Filter results
The **Filter** box removes pictures from what the site sent, without limits:
- `score:>10` - only pictures with a score over 10
- `-rating:explicit` - no explicit pictures
- `width:>=1920` - only large pictures
- `-grabber:downloaded` - hide pictures you already have

Useful on sites that limit how many tags you may search (Danbooru allows only 2 without a paid account): put
the most important tags in the search and the rest in the Filter box.
More in [Filters and the blacklist](go:help:filters).

## Sites that don't use tags
Pixiv, DeviantArt, Reddit, ArtStation, Zerochan and a few others search differently - see each
[site's guide](go:help:sites).
""")

topic("sites", "Sites and logins")("""
# Sites and logins

Click **Sites and logins** (bottom left of the window), or the site button left of the search box in [Browse](go:tab:browse), to choose where to search. [Open the site list](go:sources)

## The site list
- **Tick** the sites to search: click the box in front of the name (or select it and press Space). Your choice is remembered.
- Each site shows what it has, whether it has **adult content**, and whether it **needs a login**.
- Click a site to read its guide on the right; **Log in / settings** opens its login and options.
- **Test this site** runs one search to check that the site answers.

## Logins
Many sites work without a login. A login can show more pictures, allow longer searches, or be required.
Most sites don't use your password with programs: they give you an **API key** (a long code) in your account
settings instead. Each site's guide says exactly where to find it.
Passwords and keys are kept in the **Windows Credential Manager**, never in a file.

## Adding a site
Many sites run the same software as the built-in ones. **Add another site...** in the site list: type its address
(for example `konachan.net`) and Packrat finds out which software it uses.

## The sites
""")

topic("view", "Viewing pictures")("""
# Viewing pictures

Click a picture in [Browse](go:tab:browse) to open it in the picture window.

## Keys
- **← →** or the mouse wheel: previous / next picture
- **S**: save it (to your downloads folder, with your file name settings)
- **Ctrl+S**: save it somewhere else
- **Ctrl+Alt+S**: save it to your favorites folder (set one in Settings > Saving)
- **F5**: load it again
- **Space**: slideshow on / off
- **F11** or double-click: full screen;  **Esc**: close
- **Ctrl + mouse wheel**, **+** / **-**: zoom;  **0**: fit the window;  **1**: actual size;  drag to move a zoomed picture
- **Ctrl+C**: copy the picture
- **Middle click**: close

## The left side
The picture's **tags** (coloured by kind: artist, character, series...). Click one to search for it in a new
tab; right-click for more (favorite, blacklist...). Under them: site, size, date and **source** (click to open).

## Buttons
- **Search on**: find where else the picture is (reverse image search: IQDB, SauceNAO, Google Lens...).
- **Browser**: open the picture's page on the site.
- **Open file**: open the saved file (when you have it).
- **More**: save to the favorites folder, **copy the picture** (paste it into a chat, a document...), copy
  its address or its page's address, show the saved file, reload.
- **Videos and animated pictures play inside the window.** **Space** pauses a video, **M** turns the sound off, and the
  bar under it jumps to a moment. Volume and Repeat are remembered.

## In the results
- Hold **Ctrl** and click to select pictures; **Shift**+click selects a range; right-click for a menu.
- A green border means you already have the picture; a pink one that it has a tag from your favorites.
- Point at a picture to see its details.
""")

topic("download", "Downloading")("""
# Downloading

## Three ways (buttons under the results)
- **Download all results** - everything the search finds, on every ticked site. You can say how many.
- **Download this page** - the pictures you see.
- **Download selected** - hold **Ctrl** and click pictures first.

They are added to the [Downloads](go:tab:downloads) tab. Nothing is downloaded until you press **Start**
there. (To save a single picture right away, open it and press **S**.)

## The Downloads tab
- **Start** - downloads everything in the list that isn't finished.
- **Pause / Stop** - stop at any time; **Start** later continues exactly where it stopped, even after
  closing Packrat or restarting the PC.
- Double-click a row to change it. Right-click for more (open its folder, start again from the beginning...).
- **Add a picture...** adds one picture by its page address (copy it from the browser first).
- **When everything is downloaded:** do nothing, tell you, play a sound or shut down the PC.

## Where the files go
The `downloads` folder inside the Packrat folder, unless you choose another folder in
[Settings](go:settings:Saving), with **Change...** next to "Saving to" in Browse, or in the download window. See [File names](go:help:names).

## Duplicates
Packrat remembers every picture it saved (by its fingerprint, called MD5). A picture you already have is
skipped, even if it comes from another site. To use pictures you saved with another program, add their folder
with [Tools > MD5 list fixer](go:tab:tools).

## Being polite
Sites ask programs not to download too fast. Packrat waits about a second between requests to each site.
Please don't lower that: a site that thinks you're too fast may block you.
""")

topic("names", "File names and folders")("""
# File names and folders

Quickest: in Browse, click **Change...** next to "Saving to" and pick one. Or open
[Settings](go:settings:File names): **Name files like this** (the **File names** page has
every option). A `/` makes a folder. The line under it shows an example and whether the format is valid.

## Ready-made formats (the **Presets** button)
- `%md5%.%ext%` - the picture's fingerprint (the default; never two files with the same name)
- `%website%/%id%.%ext%` - a folder per site, the post number as name
- `%artist%/%id%.%ext%` - a folder per artist
- `%copyright%/%character%/%id%.%ext%` - folders per series, then character
- `%search%/%id%.%ext%` - a folder per search

## The most useful tokens
- `%id%` post number - `%md5%` fingerprint - `%ext%` file type (jpg, png...)
- `%artist%` `%character%` `%copyright%` (series) `%species%` `%general%` - tags by kind
- `%website%` site address - `%rating%` - `%score%` - `%date%` - `%width%` `%height%`
- `%all%` every tag - `%search%` what you searched for

## Options (after a colon)
- `%date:format=yyyy-MM-dd%` - date as 2026-11-08
- `%artist:maxlength=40%` - at most 40 characters
- `%id:length=8%` - padded with zeros: 00123456

## Only when something is there
`<%artist%/>%id%.%ext%` puts the file in an artist folder only when the picture has an artist.
`<rating:explicit?nsfw/:sfw/>%id%.%ext%` sorts by rating.

When a picture has no artist, `%artist%` becomes `anonymous`; with several, the names are joined. Change that
in [Settings > Tag tokens](go:settings:Tag tokens).

Everything else (JavaScript, conditional rules, custom tokens) works exactly like in Grabber, so a format written
for Grabber can be pasted in unchanged.

## Files you already have
[Tools > Rename existing files](go:tab:tools) renames them with a new format.
""")

topic("filters", "Filters and the blacklist")("""
# Filters and the blacklist

## Filter (in each search tab)
Removes pictures from the results. Words are combined: all must be true.
- `score:>10`  `width:>=1920`  `height:<1000`  `filesize:<5mb`
- `rating:general`  `-rating:explicit`
- `date:>=2025-01-01`  `age:<1w` (posted less than a week ago)
- `-grabber:downloaded` hide pictures you have;  `grabber:favorited` only with a favorite tag
- `tag` / `-tag` - must have / must not have a tag
- `(cat | dog) & -rating:e` - with brackets, | means or, & means and
[Settings > General](go:settings:General) has a filter used in every tab.

## Blacklist
Pictures with blacklisted tags are greyed out (or hidden) and never downloaded unless you ask.
Edit it in [Settings](go:settings:Blacklist & tags) ("Never show or download pictures with these tags"), one rule
per line:
- `gore` - blocks every picture tagged gore
- `cat dog` - blocks pictures that have both
Quick way: right-click a tag > **Blacklist**.

## Ignored and removed tags
- **Ignored** tags are left out of file names.
- **Removed** tags are removed from pictures completely.
""")

topic("advanced", "The window: pages, tray icon, shortcuts")("""
# The window

The list on the left switches pages: Browse, Downloads, Favorites, Monitors, Site archive, Settings, Tools, Log.
Every page and every option is always shown (there is no separate "advanced view" any more).

## Keyboard
- **Ctrl+1** ... **Ctrl+8**: go to a page;  **Ctrl+L**: jump to the search box
- **Ctrl+T** / **Ctrl+W**: new search tab / close it;  **F5**: search again
- **Ctrl+Shift+D**: light / dark;  **F1**: help;  **Ctrl+Q**: quit

## The icon next to the clock
Packrat puts a small icon in the notification area (bottom right of the screen; Windows may hide it behind the
**^** arrow). Right-click it: open Packrat, check monitors now, start or stop the download list, quit.
- **Closing the window keeps Packrat running**: turn it on in [Settings](go:settings:Monitoring) and the X button
  only hides the window, so monitors keep checking. Quit from the icon's menu.
- **Start hidden** and **Minimizing hides the window** are on the same page.

## Logging in on a site's own page
**Sites and logins > Log in with the built-in browser** opens the site inside Packrat. Log in like in any browser,
then click **I am logged in**: Packrat uses that login from then on. Use it for sites without an API key, and for
sites that show a "checking your browser" page.

## The older window
If this window gives trouble, **Start Packrat (classic window).bat** opens the 3.0 window. Both use the same
settings, favorites and downloads.
""")

topic("favorites", "Favorites and monitors")("""
# Favorites and monitors

## Favorites
A favorite is a search you keep. Add one with **☆ Favorite** in a search tab, or right-click a tag >
**Add to favorites**. In the [Favorites](go:tab:favorites) tab:
- Double-click to open it.
- **Check for new pictures** counts what was posted since you last opened each favorite.
- **Download new pictures** downloads just those.
- Click a column title to sort the list.
- To choose a favorite's picture: open it, right-click a picture > **Use as the picture of favorite**.

**Kept for later** (right side) is a reminder list: right-click a tag > **Keep for later**.

## Monitors
A monitor checks a search by itself every few hours, tells you when there are new pictures, and can download
them. Add one with **Monitor...** in a search tab or in Favorites. See them in [Monitors](go:tab:monitors).
- The first check only notes where "now" is: a monitor reports pictures posted *after* it was created.
  For older ones, use **Download all results**.
- Monitors run while Packrat is open. It can stay open as an icon next to the clock ([Settings](go:settings:Monitoring):
  "Closing the window keeps Packrat running"). To check them with Packrat fully closed: [Tools > Schedule monitors](go:tab:tools).
""")

topic("archive", "Site archive (e6ai / e621)")("""
# Site archive

The [Site archive](go:tab:archive) tab makes a **complete copy** of e6ai or e621: every post, file and its
details, checked file by file, using the sites' official daily lists. It is the old *e6ai Archiver*.

- Choose the **Site** and your **username** on it, pick an **archive folder** with plenty of space, leave
  the mode on **Full archive**, press **Start**.
- It can take days and hundreds of GB. Stop whenever you like; Start continues.
- **e6ai.net closes at the end of November 2026.**
- Point at any option in that tab to see what it does. The full manual is `docs\\SITE-ARCHIVE.md`.

Use the archive for "everything on the site"; use Browse and Downloads for "these searches".
""")

topic("tools", "Tools and Grabber")("""
# Tools

In the [Tools](go:tab:tools) tab:
- **Import from Grabber** - your Grabber settings, favorites, monitors, logins, downloaded-file list and
  download queue. Grabber itself is not changed.
- **Rename existing files** - give files you already have new names (shows the list before moving anything).
- **MD5 list fixer** - teach Packrat the pictures in a folder, so they are never downloaded again.
- **Blacklist fixer** - find saved pictures that match your blacklist.
- **Empty folders** - remove empty folders.
- **Tag loader** - download a site's tag list so artist / character names are known faster.
- **Source registries** - add or update sites from Grabber's online list.
- **Schedule monitors** - check monitors every hour while Packrat is closed.
- **Back up the library** - a copy of your favorites, monitors and lists.
""")

topic("problems", "Problems and fixes")("""
# Problems and fixes

## No pictures found
- Check the spelling: tags must match the site exactly (`cat_ears`, not `cat ears`).
- Try fewer tags. Danbooru allows only **2 tags** without a paid account.
- Some sites hide pictures unless you log in (see the site's guide).
- Is the site ticked? Look at the site button left of the search box.

## "Access refused (403)"
The site refused Packrat. Wait a while and try again. On e621 / e926 / e6ai, make sure your name is filled
in ([Settings](go:settings:General), "Your user name"). Some sites need a login or an API key. Don't lower the speed
limits.

## "Too many requests (429)" or the site is slow
Packrat waits and tries again by itself. If it keeps happening, raise the site's speed limits
(site list > **Log in / settings** > Speed limits).

## "Login refused (401)" / login test fails
Check the user name and the key. Most sites want an **API key**, not your password: see the site's guide.

## The site shows a "checking your browser" page (Cloudflare)
Some sites block programs. Try **Sites and logins > Log in with the built-in browser**: pass the check there like in
any browser, then click **I am logged in**. It works on many sites, not on all.

## A site stopped working
Sites change. [Tools > Source registries](go:tab:tools) may have a newer version. Twitter/X no longer
allows free access and is not expected to work.

## Where is the log?
The [Log](go:tab:log) tab, and `data\\logs` in the Packrat folder.
""")

TAB_TOPIC = {"browse": "search", "downloads": "download", "favorites": "favorites", "monitors": "favorites",
             "archive": "archive", "settings": "names", "tools": "tools", "log": "problems"}

# ---- sites -----------------------------------------------------------------------------------------
# content: "safe" (no adult content), "mixed" (all ratings), "adult"
# login: "no" (not needed / not available), "optional", "key" (API key recommended or needed), "required"
SITES: dict[str, dict] = {
    "danbooru.donmai.us": dict(
        name="Danbooru", what="Anime art, the most carefully tagged booru", content="mixed", login="optional",
        tips=["Tags with underscores: `hatsune_miku long_hair`",
              "Only **2 tags per search** without a paid account. Put the rest in the Filter box.",
              "`rating:general` for safe pictures only",
              "Artists: `artist_name` (the artist's tag); series: `touhou`"],
        login_steps=["Log in on danbooru.donmai.us, click your name > **API Key** (or *Profile > API Key*).",
                     "Create a key (no permissions need ticking), copy it.",
                     "In Packrat: User name = your Danbooru name, API key = the key."],
        notes="Some pictures (paid rewards, banned artists) are hidden from everyone."),
    "hijiribe.donmai.us": dict(name="Danbooru (alternate address)", what="Another address of Danbooru",
                               content="mixed", login="optional", see="danbooru.donmai.us",
                               notes="Old alternate address; use danbooru.donmai.us unless it doesn't work."),
    "sonohara.donmai.us": dict(name="Danbooru (alternate address)", what="Another address of Danbooru",
                               content="mixed", login="optional", see="danbooru.donmai.us",
                               notes="Old alternate address; use danbooru.donmai.us unless it doesn't work."),
    "safebooru.org": dict(
        name="Safebooru", what="Anime art, safe pictures only", content="safe", login="no",
        tips=["Tags with underscores: `scenery no_humans`", "No limit on the number of tags",
              "A good first site to try"]),
    "gelbooru.com": dict(
        name="Gelbooru", what="Very large anime art booru", content="mixed", login="key",
        tips=["Tags with underscores: `cat_ears solo`", "`rating:general` for safe pictures",
              "`sort:score` for the best first"],
        login_steps=["Gelbooru's fast way in (its API) asks for a key. Without one Packrat reads the web pages, "
                     "which is slower but works.",
                     "Log in on gelbooru.com > **My Account** > **Options** (Settings) > **API Access "
                     "Credentials**.",
                     "You see `&api_key=...&user_id=...`: put the user_id number in **User ID** and the api_key in "
                     "**API key**."]),
    "api.rule34.xxx": dict(
        name="Rule 34 (rule34.xxx)", what="Adult art of every kind", content="adult", login="key",
        tips=["Tags with underscores", "`sort:score` for the best first"],
        login_steps=["The site's API may ask for a key. Log in on rule34.xxx > **My Account** > **Options** > "
                     "**API Access Credentials**, and copy user_id and api_key into Packrat as for Gelbooru."]),
    "rule34.us": dict(name="Rule34.us", what="Adult art", content="adult", login="no",
                      tips=["Tags with underscores"]),
    "rule34.paheal.net": dict(name="Rule 34 (paheal)", what="Adult art", content="adult", login="optional",
                              tips=["Tags with underscores", "Searches with many tags can be slow on this site"]),
    "tentaclerape.net": dict(name="tentaclerape.net", what="Adult art (Shimmie booru)", content="adult",
                             login="optional", tips=["Tags with underscores"]),
    "konachan.com": dict(
        name="Konachan", what="Anime wallpapers", content="mixed", login="optional",
        tips=["Tags with underscores", "`order:score` for the best first", "`width:>=1920` for big wallpapers",
              "For safe pictures only, add the site **konachan.net** (Add another site...)"]),
    "yande.re": dict(name="yande.re", what="High-resolution anime scans and art", content="mixed",
                     login="optional", tips=["Tags with underscores", "`order:score` for the best first"]),
    "e621.net": dict(
        name="e621", what="Furry art", content="mixed", login="optional",
        tips=["Tags with underscores: `wolf solo`", "`rating:s` for safe pictures (or use **e926**)",
              "`order:score` for the best first", "Artists are tags: `artist_name`"],
        login_steps=["e621 asks every program to say who uses it: fill in your e621 name in "
                     "[Settings](go:settings:General) (\"Your user name on e621\").",
                     "Login is optional. Log in on e621.net > **Account** > **Manage API Access** > create a key.",
                     "In Packrat: User name = your e621 name, API key = the key."]),
    "e926.net": dict(name="e926", what="The safe version of e621 (furry art)", content="safe", login="optional",
                     see="e621.net", tips=["Same tags as e621", "Only safe pictures"]),
    "e6ai.net": dict(name="e6ai", what="AI-generated furry art", content="mixed", login="optional", see="e621.net",
                     notes="**e6ai.net closes at the end of November 2026.** For a complete copy use the "
                           "[Site archive](go:tab:archive) tab."),
    "anime-pictures.net": dict(name="Anime-Pictures", what="Anime wallpapers and art", content="mixed",
                               login="optional", tips=["Search with English tag names, spaces allowed"]),
    "behoimi.org": dict(name="3dbooru (behoimi)", what="Cosplay and real-life photos", content="mixed",
                        login="optional", tips=["Tags with underscores"]),
    "www.zerochan.net": dict(
        name="Zerochan", what="Anime pictures sorted by character and series", content="safe", login="optional",
        tips=["Use the full names the site uses: `Hatsune Miku` (spaces are fine)",
              "Several names = pictures with all of them"],
        notes="Zerochan sometimes refuses programs for a while; try again later."),
    "derpibooru.org": dict(
        name="Derpibooru", what="My Little Pony fan art", content="mixed", login="optional",
        tips=["Separate tags with **commas**, as on the site: `twilight sparkle, solo`",
              "`order:score` for the best first"],
        login_steps=["Optional. Without a key the site's default filter is used (it hides explicit pictures). "
                     "With your key, your own filter on the site applies.",
                     "Log in on derpibooru.org > **Settings** (your account) > copy your **API key**."]),
    "www.pixiv.net": dict(
        name="Pixiv", what="Japanese illustration community", content="mixed", login="required",
        tips=["Search words or tags, in Japanese or English: `初音ミク` or `landscape`",
              "`user:12345` - all works of one artist (the number from their profile address)",
              "`mode:partial` - also partial tag matches"],
        login_steps=["Pixiv needs an account and a special sign-in. In Packrat: **Log in / settings** > How to log "
                     "in: **Sign in with your browser** > **Test login**. Your browser opens; sign in to Pixiv.",
                     "Pixiv then tries to open its app, so the browser seems to do nothing. Press **F12** first "
                     "(developer tools), open the **Network** tab, then sign in. Find the line that contains "
                     "`callback?state=` and copy the `code=...` value from it.",
                     "Paste it into the box Packrat shows. You only do this once.",
                     "If you already have a Pixiv refresh token (for example from gallery-dl), choose **Refresh "
                     "token** and paste it there instead."],
        notes="Pixiv's sign-in is the hardest of all sites; this is a limit of Pixiv, not of Packrat."),
    "api.twitter.com": dict(
        name="Twitter / X", what="Pictures posted on X", content="mixed", login="required",
        tips=["`user_id:12345` - media of one account (its number, not its name)"],
        notes="**Not expected to work.** X closed its free API in 2023; this source uses that old API. "
              "It's kept only for people who have their own paid API keys."),
    "www.reddit.com": dict(
        name="Reddit", what="Pictures posted on Reddit", content="mixed", login="no",
        tips=["`subreddit:wallpapers` - one subreddit", "`user:name` - posts of one user",
              "`sort:top since:week` - the week's best", "Other words search post titles"],
        notes="Reddit limits programs that don't log in; if it stops answering, wait a while."),
    "www.deviantart.com": dict(
        name="DeviantArt", what="Art community", content="mixed", login="optional",
        tips=["Search words: `dragon watercolor`", "Mature pictures need a login"]),
    "www.artstation.com": dict(
        name="ArtStation", what="Professional artists' portfolios", content="safe", login="no",
        tips=["`user:username` - all projects of one artist (the name from their page address)",
              "An empty search shows the latest projects",
              "Free-text search isn't supported by this site's API"]),
    "e-hentai.org": dict(
        name="E-Hentai", what="Galleries (comics, doujinshi, picture sets)", content="adult", login="optional",
        tips=["Search words or `tag:value`", "Results are **galleries**: download one to get all its pages"]),
    "exhentai.org": dict(name="ExHentai", what="E-Hentai's members-only section", content="adult",
                         login="required", see="e-hentai.org",
                         notes="Needs an E-Hentai account that has access to ExHentai."),
    "nhentai.net": dict(name="nhentai", what="Adult comic galleries", content="adult", login="no",
                        tips=["Search words, `tag:name` or `parodies:name`", "Results are galleries"],
                        notes="Often protected by Cloudflare, which blocks programs."),
    "hentai-cosplays.com": dict(name="Hentai Cosplays", what="Adult cosplay photo sets", content="adult",
                                login="no", tips=["Search words"]),
    "slushe.com": dict(name="Slushe", what="Adult 3D art", content="adult", login="no", tips=["Search words"]),
    "kemono.cr": dict(name="Kemono", what="Archive of posts from Patreon, Fanbox and similar", content="mixed",
                      login="optional", tips=["Search words; an empty search shows recent posts"],
                      notes="Large and sometimes slow or unavailable."),
    "pawoo.net": dict(name="Pawoo (Mastodon)", what="Japanese art social network", content="mixed",
                      login="required", tips=["`user:name` - media of one user", "Search needs a login"]),
    "www.newgrounds.com/art": dict(name="Newgrounds Art", what="Art portal of Newgrounds", content="mixed",
                                   login="optional", tips=["Search words or an artist's name"]),
    "nijie.info": dict(name="Nijie", what="Japanese adult illustration site", content="adult", login="required",
                       tips=["`user:12345` - works of one artist"], notes="Needs a Nijie account."),
    "chan.sankakucomplex.com": dict(
        name="Sankaku Channel", what="Anime art (very large)", content="mixed", login="required",
        tips=["Tags with underscores"],
        notes="Sankaku changes often and blocks programs at times; it may not work."),
    "idol.sankakucomplex.com": dict(name="Sankaku Idol", what="Real-life photos", content="mixed",
                                    login="required", see="chan.sankakucomplex.com",
                                    notes="Sankaku changes often and blocks programs at times; it may not work."),
    "wallhaven.cc": dict(
        name="Wallhaven", what="Wallpapers", content="mixed", login="optional",
        tips=["Search words or tags: `mountains`", "`category:anime` / `category:general` / `category:people`",
              "`order:toplist` for the best", "Safe pictures only unless you add an API key"],
        login_steps=["For more than safe pictures: log in on wallhaven.cc > **Settings** > **Account** > copy the "
                     "**API Key** into Packrat."]),
    "booru.io": dict(name="booru.io", what="Image board", content="mixed", login="no", tips=["Search tags"]),
}

TYPE_GUIDE = {   # by source type, for sites added by hand
    "Danbooru (2.0)": "Danbooru software. Tags with underscores; free accounts are limited to 2 tags per search. "
                      "Login: user name + API key from your profile.",
    "Danbooru": "Old Danbooru software. Tags with underscores.",
    "Gelbooru (0.2)": "Gelbooru software. Tags with underscores. The API may need a user ID + API key from "
                      "Account > Options.",
    "Gelbooru (0.1)": "Old Gelbooru software. Tags with underscores.",
    "Moebooru": "Moebooru software (like yande.re). Tags with underscores; `order:score` sorts by score.",
    "Shimmie": "Shimmie software. Tags with underscores.",
    "Philomena": "Philomena software (like Derpibooru). Separate tags with commas.",
    "E621": "e621 software. Tags with underscores; put your user name in Settings.",
    "Szurubooru": "Szurubooru software. Tags; login with user name + token.",
}

CONTENT_LABEL = {"safe": "Safe only", "mixed": "All ratings (adult possible)", "adult": "Adult"}
CONTENT_SHORT = {"safe": "Safe only", "mixed": "All ratings", "adult": "Adult"}
LOGIN_LABEL = {"no": "Not needed", "optional": "Optional", "key": "Recommended (API key)", "required": "Needed"}
LOGIN_SHORT = {"no": "Not needed", "optional": "Optional", "key": "API key advised", "required": "Needed"}

POPULAR = ["safebooru.org", "e926.net", "danbooru.donmai.us", "e621.net", "gelbooru.com", "yande.re",
           "konachan.com", "www.zerochan.net", "wallhaven.cc"]


def site_info(host: str, source_name: str = "") -> dict:
    d = dict(SITES.get(host) or {})
    if d.get("see") and d["see"] in SITES:
        base = dict(SITES[d["see"]])
        base.update(d)
        d = base
    if not d:
        d = {"name": host, "what": f"Uses the {source_name} software" if source_name else "Image site",
             "content": "mixed", "login": "optional",
             "tips": [TYPE_GUIDE.get(source_name, "Try the same search words as on the site itself.")]}
    return d


def site_text(host: str, source_name: str = "", login_saved: bool = False) -> str:
    d = site_info(host, source_name)
    out = [f"# {d['name']}", f"**{d['what']}**", "",
           f"- Address: `{host}`",
           f"- Content: **{CONTENT_LABEL.get(d['content'], d['content'])}**",
           f"- Login: **{LOGIN_LABEL.get(d['login'], d['login'])}**" + ("  (saved)" if login_saved else ""),
           f"- Software: {source_name}" if source_name else ""]
    if d.get("notes"):
        out += ["", d["notes"]]
    if d.get("tips"):
        out += ["", "## How to search"] + [f"- {t}" for t in d["tips"]]
    if d.get("login_steps"):
        out += ["", "## Login"] + [f"{i + 1}. {t}" for i, t in enumerate(d["login_steps"])]
    elif d.get("login") == "required":
        out += ["", "## Login", "This site needs an account. Use **Log in / settings** and fill in the fields "
                "it asks for, then **Test login**."]
    out += ["", "[Problems with a site?](go:help:problems)"]
    return "\n".join(x for x in out if x is not None)


def sites_index(hosts_with_types) -> str:
    """The 'Sites and logins' topic plus one line per site."""
    base = next(t for i, _, t in TOPICS if i == "sites")
    lines = [base]
    for host, src in sorted(hosts_with_types, key=lambda x: site_info(x[0], x[1])["name"].lower()):
        d = site_info(host, src)
        lines.append(f"- [{d['name']}](go:site:{host}) - {d['what']} ({CONTENT_LABEL.get(d['content'], '')})")
    return "\n".join(lines)
