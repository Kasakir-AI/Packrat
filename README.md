# Packrat

Packrat finds, downloads and organizes pictures and videos from image boards: Danbooru, Gelbooru, e621, e926,
e6ai, yande.re, Konachan, rule34, Safebooru, Sankaku, Zerochan, Pixiv and many more. It runs
Grabber's site sources, so any site Grabber supports works here too.
It can also keep a complete, verified copy of e6ai or e621.

Windows 10 / 11. Linux works from source.

![Packrat's Browse page](docs/screenshots/packrat-qt-browse.png)

## Download

**[Download Packrat for Windows (Packrat-windows.zip)](../../releases/latest/download/Packrat-windows.zip)**

Unzip to `C:\` and double-click `Packrat.exe`. Nothing else to install. Windows warns the first time because the
program is not signed: "More info" > "Run anyway".

Smaller alternative if you have Python 3.11 - 3.14:
[Packrat-python.zip](../../releases/latest/download/Packrat-python.zip). Unzip, double-click `Start Packrat.bat`.

Both links always point at the newest version. Older ones are under [Releases](../../releases).

`START HERE - README.txt` in either download has the step-by-step instructions.

## What it does

- **Browse** several sites at once: thumbnail grid, tag list in colours, tag completion and coloured words in the
  search box, filters, blacklist.
- **View** pictures with zoom; videos and animated pictures play inside Packrat.
- **Download** whole searches or picked pictures. Sites download side by side, each at its own polite speed. Stop
  any time; Start continues. Pictures you already have are recognised by MD5, also across sites. A transfer that
  breaks halfway is continued, not restarted.
- **Name and sort files** with Grabber's file name tokens (`%artist%/%id%.%ext%`), conditions and JavaScript.
- **Favorites and monitors**: keep searches, see what is new, download new pictures automatically. Packrat can sit
  next to the clock and keep checking.
- **Logins**: API keys and passwords in the Windows Credential Manager, or log in on the site's own page in the
  built-in browser.
- **Site archive**: a complete copy of e6ai or e621 from the sites' daily exports, checked file by file.
- **Tools**: import everything from Grabber, rename existing files, fix lists, load tag lists. Saved pictures can
  be uploaded to your own booru (Szurubooru, Danbooru, Shimmie, MyImouto).
- Help is built in (F1), and every feature is also on the command line (`packrat --help`).

## Not there yet

- English only. No auto-update. No custom themes.
- Sites that need an account (Pixiv, Twitter/X) and the built-in login browser have not been tested with real
  accounts.
- `Packrat.exe` is not code-signed.

## From source

```
git clone https://github.com/Kasakir-AI/Packrat.git
cd Packrat/app
python -m pip install -e . pytest
python -m pytest -q
python -m packrat gui
```

Every push is tested on GitHub's Windows machines: the start-up scripts, the tests, a self-test of the window,
and the `Packrat.exe` build ([workflow](.github/workflows/windows.yml)).

## More

- [Manual](docs/README.md) - [Site archive manual](docs/SITE-ARCHIVE.md) - [Change log](docs/CHANGELOG.md)
- The site sources come from Grabber by Bionus (Apache License 2.0); the license text is in `app/packrat/sources/`.
- This project was called e6ai-archiver until version 3.0. Old archives and settings are picked up automatically.
