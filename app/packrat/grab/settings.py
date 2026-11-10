"""Browse / Downloads settings (the Grabber side of Packrat), with their defaults and help texts.

One table, SETTINGS, drives three things - like control_panel.OPTIONS does for the whole-site archive:
the defaults, the Settings tab (every row becomes a labelled field with a tooltip and a help line), and
"Import from Grabber" (the keys are Grabber's own settings.ini keys, so a Grabber profile maps 1:1).

Values live in data/settings.json (flat {key: value}); unknown keys are kept untouched, so files written
by a newer Packrat survive a downgrade. Lists that need their own editor (conditional filenames, custom
tokens, separate log files, web services, image conversions, metadata properties) are JSON lists.
"""
from __future__ import annotations

import copy
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .. import paths


@dataclass(frozen=True)
class S:
    key: str
    default: object
    kind: str            # text | int | float | bool | choice | dir | file | color | lines | filename | list | hidden
    page: str
    group: str
    label: str
    help: str = ""
    choices: tuple = ()  # for choice: ((value, label), ...)
    lo: float | None = None
    hi: float | None = None


TAG_CATEGORIES = (  # (token, label, empty default, multiple default) - Grabber's Image::generateTokens
    ("artist", "Artists", "anonymous", "multiple artists"),
    ("copyright", "Copyrights", "misc", "crossover"),
    ("character", "Characters", "unknown", "group"),
    ("model", "Models", "unknown", "multiple"),
    ("species", "Species", "unknown", "multiple"),
    ("meta", "Metas", "none", "multiple"),
    ("lore", "Lore", "none", "multiple"),
    ("contributor", "Contributors", "none", "multiple"),
    ("photo_set", "Photo sets", "unknown", "multiple"),
    ("general", "Generals", "", ""),
)
MULTIPLE_CHOICES = (("keepAll", "Keep all tags"), ("keepN", "Keep n tags"),
                    ("keepNThenAdd", "Keep n tags, then add"), ("replaceAll", "Replace all tags by"),
                    ("multiple", "One file per tag"))
DUP_CHOICES = (("save", "Save (download again)"), ("copy", "Copy the existing file"),
               ("move", "Move the existing file here"), ("link", "Shortcut / symbolic link"),
               ("hardlink", "Hard link"), ("ignore", "Don't save"))
TAG_COLORS = (("artists", "#aa0000"), ("circles", "#55bbff"), ("copyrights", "#aa00aa"),
              ("characters", "#00aa00"), ("species", "#ee6600"), ("metas", "#ee6600"), ("models", "#00aaff"),
              ("generals", "#000000"), ("favorites", "#ffaaff"), ("keptForLater", "#aaaa00"),
              ("blacklisteds", "#444444"), ("ignoreds", "#777777"))

G, SRC, SAVE, NAMES, TAGS, CONV, META, UI, VIEW, BL, MON, NET, CMD = (
    "General", "Sources", "Saving", "File names", "Tag tokens", "Conversion", "Metadata", "Interface",
    "Viewer", "Blacklist & tags", "Monitoring", "Network", "Commands")
PAGES = (G, SRC, SAVE, NAMES, TAGS, CONV, META, UI, VIEW, BL, MON, NET, CMD)

SETTINGS: tuple[S, ...] = (
    # ---- General ----------------------------------------------------------------------------
    S("start", "restore", "choice", G, "At start", "When Packrat opens",
      "What the Browse tab shows when Packrat starts: nothing, the first page of a new search, or the tabs "
      "you had open last time.", (("none", "Do nothing"), ("loadfirst", "Load the first page"),
                                  ("restore", "Restore last session"))),
    S("preloadAllTabs", False, "bool", G, "At start", "Load every restored tab at once",
      "Off: restored tabs load when you first click them (fewer requests)."),
    S("confirm_close", True, "bool", G, "At start", "Ask before closing while something runs",
      "Ask for confirmation before closing the window while the download list is running (off: it is "
      "stopped cleanly and the window closes)."),
    S("username", "", "text", G, "Who you are", "Your name for User-Agents",
      "Sites that ask tools to identify themselves (e621, e926, e6ai) get a User-Agent like "
      "'Packrat/3.0 (by NAME on e621)'. Use your account name on those sites."),
    S("whitelistedtags", "", "text", G, "Auto-download", "Whitelisted tags",
      "Images having any of these tags (space-separated) are downloaded automatically while you browse, "
      "when the option below is on."),
    S("whitelist_download", "never", "choice", G, "Auto-download", "Download whitelisted images",
      "When to download whitelisted images automatically.",
      (("never", "Don't download automatically"), ("image", "When opening the image"),
       ("page", "When the page loads"))),
    S("add", "", "text", G, "Added to every search", "Tags",
      "These tags are added to every search, on every site (e.g. -rating:explicit)."),
    S("globalPostFilter", "", "text", G, "Added to every search", "Post-filters",
      "A post-filter applied to every search (see the post-filter syntax in Help)."),
    S("globalPostFilterExplicit", False, "bool", G, "Added to every search",
      "Show the global post-filter in each tab's post-filter field", ""),
    # ---- Sources ----------------------------------------------------------------------------
    S("limit", 20, "int", SRC, "Searching", "Images per page",
      "How many results each site returns per page in a search tab (most sites allow 1-100, some more).", (),
      1, 1000),
    S("mergeresults", False, "bool", SRC, "Searching", "Merge results of several sites",
      "Show the results of all selected sites in one list and remove duplicates (same MD5)."),
    S("sites", ["e6ai.net"], "hidden", SRC, "Searching", "Selected sites",
      "The sites new search tabs use (chosen with the Sources button)."),
    S("source_1", "json", "choice", SRC, "API order (default for every site)", "Source 1",
      "Which kind of answer to ask sites for first. Sites can override this in their options.",
      (("json", "JSON"), ("xml", "XML"), ("html", "Regex / HTML"), ("rss", "RSS"), ("", "-"))),
    S("source_2", "xml", "choice", SRC, "API order (default for every site)", "Source 2", "",
      (("json", "JSON"), ("xml", "XML"), ("html", "Regex / HTML"), ("rss", "RSS"), ("", "-"))),
    S("source_3", "html", "choice", SRC, "API order (default for every site)", "Source 3", "",
      (("json", "JSON"), ("xml", "XML"), ("html", "Regex / HTML"), ("rss", "RSS"), ("", "-"))),
    S("source_4", "rss", "choice", SRC, "API order (default for every site)", "Source 4", "",
      (("json", "JSON"), ("xml", "XML"), ("html", "Regex / HTML"), ("rss", "RSS"), ("", "-"))),
    S("tagsautoadd", 10, "int", SRC, "Tags", "Auto-complete: remember tags used at least",
      "Tags seen at least this many times in search results are added to the auto-complete list.", (), 1, 10**6),
    S("autocompletion", True, "bool", SRC, "Tags", "Auto-complete tags in the search field", ""),
    S("useregexfortags", True, "bool", SRC, "Tags", "Get more precise tags (details page) when needed",
      "When a site's list does not say which tags are artists / characters ..., load the image's details "
      "before saving if the file name needs them (one extra request per image)."),
    S("userAgent", "", "text", SRC, "Identity", "Default User-Agent",
      "Used for sites whose source sets none. Blank = 'Packrat/3.0 (by YOUR NAME)'. e621, e926 and e6ai "
      "always get the descriptive one (their rules forbid browser User-Agents)."),
    S("extensionRotationStatic", "jpg png gif jpeg webm swf mp4", "text", SRC, "Extension rotation",
      "Images", "When a file URL is a guess and answers 404, try these extensions in order."),
    S("extensionRotationAnimated", "mp4 webm gif jpg png jpeg swf", "text", SRC, "Extension rotation",
      "Animated", ""),
    # ---- Saving -----------------------------------------------------------------------------
    S("Save/path", "", "dir", SAVE, "Where", "Folder",
      "Where downloaded files go (the file name format can add sub-folders). Blank = the downloads folder "
      "next to 'Start Packrat.bat'."),
    S("Save/path_favorites", "", "dir", SAVE, "Where", "Favorites folder",
      "Folder for 'Save (fav)' in the viewer. Blank = the folder above."),
    S("Save/simultaneous", 4, "int", SAVE, "Downloading", "Simultaneous downloads",
      "Files downloaded at the same time (each site still keeps its own interval, see the site options).",
      (), 1, 64),
    S("Save/downloadoriginals", True, "bool", SAVE, "Downloading", "Download original images",
      "Off: download the smaller 'sample' version when a site has one."),
    S("Save/samplefallback", True, "bool", SAVE, "Downloading", "Download the sample if the original fails", ""),
    S("Save/keepDate", True, "bool", SAVE, "Downloading", "Keep original creation date",
      "Set the file's date to the date the post was made (when the site says)."),
    S("Save/headerDetection", True, "bool", SAVE, "Downloading", "Get extension from file header",
      "Fix the extension from the file's first bytes when the site gives a wrong one (e.g. a .jpg that is "
      "really a .png)."),
    S("Save/automaticretries", 2, "int", SAVE, "Downloading", "Retry failed downloads", "", (), 0, 20),
    S("Save/md5Duplicates", "ignore", "choice", SAVE, "Duplicates", "If a file already exists globally",
      "A file with the same MD5 was already downloaded by Packrat somewhere else (the MD5 list).", DUP_CHOICES),
    S("Save/md5DuplicatesSameDir", "ignore", "choice", SAVE, "Duplicates", "If it's in the same folder",
      "A file with the same MD5 is already in the target folder.", DUP_CHOICES),
    S("Save/keepDeletedMd5", False, "bool", SAVE, "Duplicates", "Remember files you deleted",
      "Keep deleted files in the MD5 list, so they are not downloaded again."),
    S("Save/multiple_files", "copy", "choice", SAVE, "Duplicates",
      "If an image gets several file names ('one file per tag')",
      "", (("copy", "Copy"), ("link", "Symbolic link"), ("hardlink", "Hard link"), ("move", "Only the first"))),
    S("Save/tempPathOverride", "", "dir", SAVE, "Downloading", "Temporary folder",
      "Where files are written while downloading. Blank = next to the final file (.part)."),
    S("Save/md5DownloadedCheck", True, "bool", SAVE, "Duplicates", "Mark already-downloaded images",
      "Show a badge on thumbnails of images whose MD5 is in the MD5 list or whose file exists."),
    S("Batch/end", "nothing", "choice", SAVE, "When a download list is finished", "Then",
      "", (("nothing", "Do nothing"), ("notify", "Notify me"), ("sound", "Play a sound"),
           ("cdtray", "Open CD tray"), ("shutdown", "Shut down the PC (asks first, 60 s to cancel)"))),
    # ---- File names -------------------------------------------------------------------------
    S("Save/filename", "%md5%.%ext%", "filename", NAMES, "File name", "File name format",
      "Grabber's format: %token% fields, options like %artist:maxlength=20%, conditionals "
      "<%artist%/> and <\"tag\"?a:b>, or javascript:... A / makes folders. See 'Help: file names'."),
    S("Save/filename_favorites", "", "filename", NAMES, "File name", "Favorites file name",
      "Used by 'Save (fav)'. Blank = the format above."),
    S("Save/separator", " ", "text", NAMES, "Tokens", "Tags separator", "Between tags in list tokens."),
    S("Save/replaceblanks", False, "bool", NAMES, "Tokens", "Replace spaces by underscores", ""),
    S("Save/noJpeg", True, "bool", NAMES, "Tokens", "Replace JPEG by JPG", ""),
    S("Save/limit", 0, "int", NAMES, "Tokens", "Max length",
      "Longest file name (characters). 0 = automatic (keeps full paths under the Windows limit).", (), 0, 255),
    S("Save/copyright_useshorter", True, "bool", NAMES, "Tokens", "Copyrights: use shortest if possible",
      "pokemon + pokemon_bw -> pokemon."),
    S("Filenames", [], "list", NAMES, "Conditional file names", "Rules",
      "Rules applied top to bottom; a later match wins. Condition: tags or tokens, like a conditional."),
    S("Customs", [], "list", NAMES, "Custom tokens", "Tokens",
      "A token whose value is the image's tags found in your list, e.g. 'hair' = blonde_hair red_hair."),
    S("LogFiles", [], "list", NAMES, "Separate log files", "Log files",
      "Write a line per saved image to a text file (any %tokens%), e.g. a CSV of id, md5, path."),
    # ---- Conversion -------------------------------------------------------------------------
    S("Save/resizeMaxWidth", 0, "int", CONV, "Image size", "Max width (0 = off)",
      "Images wider than this are resized after downloading (needs Pillow, included).", (), 0, 100000),
    S("Save/resizeMaxHeight", 0, "int", CONV, "Image size", "Max height (0 = off)", "", (), 0, 100000),
    S("Save/FFmpegRemuxWebmToMp4", False, "bool", CONV, "Videos (needs FFmpeg)", "Remux WEBM to MP4 when possible",
      "Changes the container only (fast, no quality loss) when the codecs allow it."),
    S("Save/FFmpegConvertWebmToMp4", False, "bool", CONV, "Videos (needs FFmpeg)", "Convert WEBM to MP4",
      "Re-encodes (slow). Remux is tried first when both are on."),
    S("Save/FFmpegConvertTimeout", 300, "int", CONV, "Videos (needs FFmpeg)", "Timeout (s)", "", (), 5, 86400),
    S("Save/ImageConversions", [], "list", CONV, "Images", "Image conversions",
      "Convert images of a type into another, e.g. webp -> png (Pillow; ImageMagick if found)."),
    S("Save/ConvertUgoira", False, "bool", CONV, "Pixiv ugoira (ZIP)", "Convert ugoira ZIP files", ""),
    S("Save/ConvertUgoiraFormat", "gif", "choice", CONV, "Pixiv ugoira (ZIP)", "Target format", "",
      (("gif", "GIF"), ("webp", "WEBP (animated)"), ("apng", "APNG"), ("mp4", "MP4 (needs FFmpeg)"))),
    S("Save/ConvertUgoiraDeleteOriginal", False, "bool", CONV, "Pixiv ugoira (ZIP)", "Delete the ZIP afterwards", ""),
    S("Save/ConversionOverwrite", True, "bool", CONV, "All", "Overwrite the destination if it exists", ""),
    S("ffmpegPath", "", "file", CONV, "Programs", "FFmpeg", "Blank = found on PATH or next to Packrat."),
    S("magickPath", "", "file", CONV, "Programs", "ImageMagick (magick)", "Blank = found on PATH."),
    # ---- Metadata ---------------------------------------------------------------------------
    S("Save/MetadataExiftool", [], "list", META, "ExifTool", "Properties",
      "Write tags into the files, e.g. XMP:Subject = %all:separator=^,%. Needs exiftool."),
    S("Save/MetadataExiftoolExtensions", "jpg jpeg png gif webp mp4", "text", META, "ExifTool", "Extensions", ""),
    S("Save/MetadataExiftoolClear", False, "bool", META, "ExifTool", "Clear existing metadata first", ""),
    S("Save/MetadataExiftoolKeepColorProfile", True, "bool", META, "ExifTool", "Keep the color profile", ""),
    S("Save/MetadataExiftoolSidecar", "on_error", "choice", META, "ExifTool", "XMP sidecar files", "",
      (("disabled", "Disabled"), ("on_error", "On error"), ("both", "Both sidecar and original"),
       ("only", "Only sidecar"))),
    S("Save/MetadataExiftoolSidecarNoExtension", False, "bool", META, "ExifTool",
      "Sidecar named image.xmp (not image.jpg.xmp)", ""),
    S("Save/MetadataPropsys", [], "list", META, "Windows Property System", "Properties",
      "Windows only, through ExifTool's Windows tags (System.Keywords -> XPKeywords ...)."),
    S("exiftoolPath", "", "file", META, "Programs", "ExifTool", "Blank = found on PATH or next to Packrat."),
    S("Save/WriteSidecarJson", False, "bool", META, "Packrat", "Also save the post's details as .json",
      "Next to each file, a JSON with every token (site, id, tags by type, sources ...)."),
    # ---- Interface --------------------------------------------------------------------------
    S("thumbnailSize", 150, "int", UI, "Search results", "Thumbnail size (pixels)", "", (), 60, 600),
    S("resizeInsteadOfCropping", True, "bool", UI, "Search results", "Resize previews instead of cropping", ""),
    S("colorSearchWords", True, "bool", UI, "Search results", "Colour the words in the search box",
      "Tags you type take the colour of their kind (artist, character, series...), favorites and blacklisted tags "
      "theirs. A tag's kind is known once it appeared in results, or after Tools > Tag loader."),
    S("infiniteScroll", "disabled", "choice", UI, "Search results", "Infinite scroll",
      "", (("disabled", "Disabled"), ("button", "'Load more' button"), ("scroll", "Load when scrolled down"))),
    S("invertToggle", False, "bool", UI, "Search results", "Invert click and Ctrl+click",
      "On: a click marks an image for download, Ctrl+click opens it."),
    S("showFilteredImagesCount", True, "bool", UI, "Search results", "Show the filtered images count", ""),
    S("previewVideoIndicator", True, "bool", UI, "Search results", "Show a video badge on previews", ""),
    S("hideblacklisted", False, "bool", UI, "Search results", "Hide blacklisted images",
      "Off: they are shown greyed out with a 'blacklisted' badge."),
    S("favorites_display", "ind", "choice", UI, "Favorites", "Favorites display", "",
      (("ind", "Image, name and details"), ("in", "Image and name"), ("id", "Image and details"),
       ("nd", "Name and details"), ("i", "Image only"), ("n", "Name only"), ("d", "Details only"))),
    S("favoritesSort", "name", "choice", UI, "Favorites", "Sort by", "",
      (("name", "Name"), ("note", "Priority"), ("lastviewed", "Last view"))),
    S("Coloring", dict(TAG_COLORS), "list", UI, "Coloring", "Tag colors", ""),
    # ---- Viewer -----------------------------------------------------------------------------
    S("Viewer/singleWindow", False, "bool", VIEW, "Window", "Use a single image window", ""),
    S("Viewer/rememberGeometry", True, "bool", VIEW, "Window", "Remember window size and position", ""),
    S("Viewer/viewSamples", False, "bool", VIEW, "Window", "Show samples (faster) instead of originals", ""),
    S("Viewer/scaleUp", False, "bool", VIEW, "Window", "Scale up small images to fit the window", ""),
    S("Viewer/tagOrder", "type", "choice", VIEW, "Window", "Tag order", "",
      (("type", "Type"), ("name", "Name"), ("count", "Count"))),
    S("Viewer/showTagCount", False, "bool", VIEW, "Window", "Show tag counts", ""),
    S("Viewer/preload", 1, "int", VIEW, "Window", "Preload next / previous images", "", (), 0, 10),
    S("Viewer/slideshow", 5, "int", VIEW, "Window", "Slideshow interval (s)", "", (), 1, 3600),
    S("Viewer/videoAutoplay", True, "bool", VIEW, "Videos", "Play videos as soon as they open", ""),
    S("Viewer/videoLoop", True, "bool", VIEW, "Videos", "Repeat videos", ""),
    S("Viewer/videoVolume", 60, "int", VIEW, "Videos", "Volume (0-100)", "", (), 0, 100),
    S("Viewer/videoMuted", False, "bool", VIEW, "Videos", "Start videos muted", ""),
    S("Viewer/imageCloseMiddleClick", True, "bool", VIEW, "Window", "Middle click closes the window", ""),
    S("Viewer/imageNavigateScroll", True, "bool", VIEW, "Window", "Mouse wheel goes to next / previous", ""),
    S("Viewer/imageBackgroundColor", "", "color", VIEW, "Window", "Background color", "Blank = theme"),
    # ---- Blacklist & tags -------------------------------------------------------------------
    S("blacklistedtags", "", "lines", BL, "Blacklist", "Blacklisted tags",
      "One rule per line; several tags on a line = all of them (AND). Post-filter syntax works: "
      "'rating:explicit', 'score:<0', '-solo' ..."),
    S("downloadblacklist", False, "bool", BL, "Blacklist", "Download images containing blacklisted tags", ""),
    S("warnblacklisted", True, "bool", BL, "Blacklist", "Warn before opening a blacklisted image", ""),
    S("ignoredtags", "", "lines", BL, "Tags", "Ignored tags",
      "Ignored tags have no type: they never appear in %artist%, %copyright% ... (still in %all%)."),
    S("removedtags", "", "lines", BL, "Tags", "Removed tags",
      "Never taken into account when saving: they don't appear in any token, not even %all%."),
    # ---- Monitoring -------------------------------------------------------------------------
    S("Monitoring/enabled", True, "bool", MON, "Monitoring", "Run monitors while Packrat is open", ""),
    S("Monitoring/enableTray", True, "bool", MON, "Icon next to the clock", "Show Packrat's icon next to the clock",
      "A small Packrat icon in the notification area (bottom right). Right-click it to open Packrat, check "
      "monitors or start the downloads. Takes effect the next time Packrat starts."),
    S("Monitoring/closeToTray", False, "bool", MON, "Icon next to the clock", "Closing the window keeps Packrat running",
      "On: the X button only hides the window; monitors keep checking. Quit from the icon's menu."),
    S("Monitoring/minimizeToTray", False, "bool", MON, "Icon next to the clock", "Minimizing hides the window",
      "On: minimizing removes Packrat from the taskbar; click the icon next to the clock to bring it back."),
    S("Monitoring/startMinimized", False, "bool", MON, "Icon next to the clock", "Start hidden",
      "On: Packrat starts as an icon next to the clock, without opening its window."),
    S("Monitoring/startupDelay", 30, "int", MON, "Monitoring", "Delay on startup (s)", "", (), 0, 86400),
    S("Monitoring/defaultInterval", 1440, "int", MON, "Default monitor settings", "Interval (min)", "", (), 1, 10**6),
    S("Monitoring/defaultDelay", 0, "int", MON, "Default monitor settings", "Delay (min)",
      "Ignore images newer than this (gives taggers time to tag them).", (), 0, 10**6),
    S("Monitoring/defaultNotificationEnabled", True, "bool", MON, "Default monitor settings", "Notify", ""),
    S("Monitoring/defaultDownloadEnabled", True, "bool", MON, "Default monitor settings", "Download", ""),
    # ---- Network ----------------------------------------------------------------------------
    S("Proxy/use", False, "bool", NET, "Proxy", "Use a proxy", ""),
    S("Proxy/useSystem", False, "bool", NET, "Proxy", "Use the system proxy settings", ""),
    S("Proxy/type", "http", "choice", NET, "Proxy", "Type",
      "SOCKS needs the optional 'PySocks' package.", (("http", "HTTP"), ("socks5", "SOCKS v5"))),
    S("Proxy/hostName", "", "text", NET, "Proxy", "Host", ""),
    S("Proxy/port", 8080, "int", NET, "Proxy", "Port", "", (), 1, 65535),
    S("Proxy/user", "", "text", NET, "Proxy", "User", ""),
    S("Proxy/password", "", "secret", NET, "Proxy", "Password", "Kept in the Windows Credential Manager."),
    S("timeout", 30, "int", NET, "Requests", "Timeout (s)", "", (), 5, 600),
    S("WebServices", [], "list", NET, "Web services", "Reverse image search",
      "Buttons in the viewer: open the image on these sites. %url% = file URL, %md5% ..."),
    # ---- Commands ---------------------------------------------------------------------------
    S("Exec/tag_before", "", "text", CMD, "Commands", "Tag (before)", "Run for each tag, before the image."),
    S("Exec/image", "", "text", CMD, "Commands", "Image", "Run for each saved image, e.g. a copy into a local "
      "booru. %path% = the saved file."),
    S("Exec/tag_after", "", "text", CMD, "Commands", "Tag (after)", ""),
    S("Exec/Group/init", "", "text", CMD, "Commands", "Start", "Run once before a download list."),
    S("Exec/Group/end", "", "text", CMD, "Commands", "End", "Run once after a download list."),
    S("Exec/SQL/driver", "QSQLITE", "choice", CMD, "Database", "Driver",
      "SQLite works out of the box; MySQL needs 'pymysql', PostgreSQL 'psycopg'.",
      (("QSQLITE", "SQLite"), ("QMYSQL", "MySQL / MariaDB"), ("QPSQL", "PostgreSQL"))),
    S("Exec/SQL/host", "", "text", CMD, "Database", "Host (SQLite: the database file)", ""),
    S("Exec/SQL/user", "", "text", CMD, "Database", "User", ""),
    S("Exec/SQL/password", "", "secret", CMD, "Database", "Password", ""),
    S("Exec/SQL/database", "", "text", CMD, "Database", "Database", ""),
    S("Exec/SQL/before", "", "text", CMD, "Database", "Start", ""),
    S("Exec/SQL/tag_before", "", "text", CMD, "Database", "Tag (before)", ""),
    S("Exec/SQL/image", "", "text", CMD, "Database", "Image", "Use %token:escape% for values."),
    S("Exec/SQL/tag_after", "", "text", CMD, "Database", "Tag (after)", ""),
    S("Exec/SQL/after", "", "text", CMD, "Database", "End", ""),
    S("Exec/dry_run", False, "bool", CMD, "Commands", "Dry run (only log the commands)", ""),
    S("LocalBooru/type", "", "choice", CMD, "Local booru", "Copy saved images to",
      "Upload every saved image, with its tags, rating and source, to your own booru. Built in: no PHP, "
      "Node.js or curl needed (Grabber needs those scripts).",
      (("", "Off"), ("szurubooru", "Szurubooru"), ("danbooru", "Danbooru (2.x)"), ("shimmie", "Shimmie"),
       ("moebooru", "MyImouto / Moebooru"))),
    S("LocalBooru/url", "", "text", CMD, "Local booru", "Address", "e.g. http://localhost:8080"),
    S("LocalBooru/user", "", "text", CMD, "Local booru", "User name", ""),
    S("LocalBooru/password", "", "secret", CMD, "Local booru", "Password / token / API key",
      "Szurubooru: a login token. Danbooru: an API key. Shimmie, MyImouto: your password. "
      "Kept in the Windows Credential Manager."),
    S("LocalBooru/extraTags", "", "text", CMD, "Local booru", "Extra tags", "Added to every upload, e.g. 'packrat'."),
)
for _tok, _label, _empty, _multi in TAG_CATEGORIES:
    SETTINGS += (
        S(f"Save/{_tok}_empty", _empty, "text", TAGS, _label, "If empty", "Value when the image has none."),
        S(f"Save/{_tok}_sep", "", "text", TAGS, _label, "Separator", "Blank = the general tags separator."),
        S(f"Save/{_tok}_sort", "original", "choice", TAGS, _label, "Sort", "",
          (("original", "Original"), ("name", "Name"))),
        S(f"Save/{_tok}_multiple_limit", 1, "int", TAGS, _label, "If more than n tags", "", (), 1, 1000),
        S(f"Save/{_tok}_multiple", "keepAll", "choice", TAGS, _label, "Then", "", MULTIPLE_CHOICES),
        S(f"Save/{_tok}_multiple_keepN", 1, "int", TAGS, _label, "Keep n (Keep n tags)", "", (), 1, 1000),
        S(f"Save/{_tok}_multiple_keepNThenAdd_keep", 1, "int", TAGS, _label, "Keep n (then add)", "", (), 1, 1000),
        S(f"Save/{_tok}_multiple_keepNThenAdd_add", " (+ %count%)", "text", TAGS, _label, "Then add",
          "%count% = how many were left out, %total% = all."),
        S(f"Save/{_tok}_value", _multi, "text", TAGS, _label, "Replace all tags by", ""),
    )

BY_KEY = {s.key: s for s in SETTINGS}
SECRET_KEYS = {s.key for s in SETTINGS if s.kind == "secret"}


class Settings:
    """Thread-safe settings store backed by data/settings.json (atomic writes)."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else paths.data_dir() / "settings.json"
        self._lock = threading.RLock()
        self._values: dict = {}
        self._listeners: list = []
        self.load()

    def load(self) -> None:
        with self._lock:
            try:
                data = json.loads(self.path.read_text("utf-8"))
                self._values = data if isinstance(data, dict) else {}
            except FileNotFoundError:
                self._values = {}
            except (OSError, ValueError):
                bad = self.path.with_name(self.path.name + f".unreadable-{time.strftime('%Y%m%d-%H%M%S')}")
                try:
                    os.replace(self.path, bad)
                except OSError:
                    pass
                self._values = {}

    def get(self, key: str, default=None):
        with self._lock:
            if key in self._values:
                return copy.deepcopy(self._values[key])
        if key in BY_KEY:
            return copy.deepcopy(BY_KEY[key].default)
        return default

    def __getitem__(self, key):
        return self.get(key)

    def set(self, key: str, value, save: bool = True) -> None:
        if key in SECRET_KEYS:
            from . import secrets
            secrets.set_secret("settings", key, str(value or ""))
            return
        with self._lock:
            self._values[key] = value
            if save:
                self.save()
        for fn in list(self._listeners):
            try:
                fn(key, value)
            except Exception:  # noqa: BLE001
                pass

    def update(self, values: dict) -> None:
        for k, v in values.items():
            self.set(k, v, save=False)
        self.save()

    def secret(self, key: str) -> str:
        from . import secrets
        return secrets.get_secret("settings", key)

    def reset(self, keys=None) -> None:
        with self._lock:
            for k in (keys or list(self._values)):
                self._values.pop(k, None)
            self.save()

    def on_change(self, fn) -> None:
        self._listeners.append(fn)

    def all(self) -> dict:
        out = {s.key: copy.deepcopy(s.default) for s in SETTINGS}
        with self._lock:
            out.update(copy.deepcopy(self._values))
        return out

    def save(self) -> None:
        with self._lock:
            text = json.dumps(self._values, indent=1, ensure_ascii=False, sort_keys=True, default=str)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(text, "utf-8")
            os.replace(tmp, self.path)

    # ---- helpers used everywhere -------------------------------------------------------------
    def save_path(self, favorite: bool = False) -> str:
        p = (self.get("Save/path_favorites") if favorite else "") or self.get("Save/path") or ""
        return p or str(paths.default_downloads())

    def filename(self, favorite: bool = False) -> str:
        return (self.get("Save/filename_favorites") if favorite else "") or self.get("Save/filename") or "%md5%.%ext%"

    def lines(self, key: str) -> list[str]:
        v = self.get(key) or ""
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return [ln.strip() for ln in str(v).splitlines() if ln.strip()]


_shared: Settings | None = None


def shared() -> Settings:
    global _shared
    if _shared is None:
        _shared = Settings()
    return _shared
