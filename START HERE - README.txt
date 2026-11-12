==========================================================================
 Packrat 3.2 - START HERE
==========================================================================

Packrat finds, downloads and organizes pictures from many sites
(Danbooru, Gelbooru, e621, e926, e6ai, yande.re, Konachan, rule34,
Safebooru, Sankaku, Zerochan, Pixiv and many more). It can name and sort
the files by artist, character, series and so on, watch searches for new
pictures, play videos, and keep a complete copy of e6ai or e621.

You need: Windows 10 or 11, or a Mac with macOS 12 or newer, and an
internet connection.

There are three downloads. All are the same program.

  A. "Packrat-windows"  - has Packrat.exe. Nothing else to install.
  B. "Packrat-python"   - smaller; needs Python installed (steps below).
                          Works on Windows, Mac and Linux.
  C. "Packrat-macOS.dmg" - the Mac app (Apple Silicon: M1 and newer).
                          Nothing else to install.

Keep the whole folder together. Put it somewhere simple such as
C:\Packrat - NOT inside OneDrive, Dropbox, Program Files or a network drive.
Your settings, favorites and downloads live inside this folder (data\,
downloads\), so moving the folder moves everything.


==========================================================================
 A. THE Packrat.exe DOWNLOAD
==========================================================================

  1. Right-click the zip > "Extract All..." and pick C:\  (you get C:\Packrat).
     Do not run it from inside the zip window.
  2. Double-click Packrat.exe.
  3. Windows may say "Windows protected your PC" the first time, because the
     program is new and not signed. Click "More info" > "Run anyway".

  Packrat shows up in the taskbar with its own name and icon, and you can
  pin it there (right-click its taskbar button > "Pin to taskbar").


==========================================================================
 B. THE PYTHON DOWNLOAD
==========================================================================

STEP 1 - Install Python (once)
  1. Go to https://www.python.org/downloads/windows/ and download
     "Python 3.14" (or 3.13) - "Windows installer (64-bit)".
  2. Run the installer. IMPORTANT: on the first screen, tick the box
        [x] Add python.exe to PATH
     Then click "Install Now". Wait for "Setup was successful".
  3. Double-click "Check Python.bat". It should end with
        "OK - Python is ready."
     If it does not, see the FAQ below ('py' is not recognized).

STEP 2 - Unzip
  Right-click the zip > "Extract All..." and pick C:\ (you get C:\Packrat).

STEP 3 - Start
  Double-click "Start Packrat.bat". The first time it downloads its
  packages (about 250 MB, a few minutes; it needs the internet for this).
  A black window shows the progress - leave it open. Then Packrat opens.

  The same as commands (Start menu > type  powershell  > Enter):
      cd C:\Packrat
      .\"Check Python.bat"
      .\"Start Packrat.bat"

  The first-start assistant can put a Packrat icon on your desktop.
  Packrat shows its own icon in the taskbar. To pin it, pin the desktop
  icon (right-click > "Pin to taskbar"), not the running window.

  On a Mac or Linux: install Python 3.11 or newer (Mac: python.org or
  "brew install python"), unzip, and double-click "Start Packrat.command"
  (or run it in Terminal:  bash "Start Packrat.command"). The first time
  it downloads its packages, like on Windows.


==========================================================================
 C. THE MAC APP (Packrat-macOS.dmg)
==========================================================================

  1. Double-click Packrat-macOS.dmg. In the window that opens, drag
     Packrat onto the Applications folder.
  2. Open Packrat from Applications (or Launchpad / Spotlight).
  3. The first time, the Mac says "Apple could not verify Packrat",
     because the program is new and not signed by Apple. Click Done, then:
     System Settings > Privacy & Security > scroll down to
     "Packrat was blocked" > "Open Anyway" > "Open Anyway" again
     (enter your Mac password if asked). Only needed once.

  On a Mac, Packrat keeps its settings, favorites and lists in
  ~/Library/Application Support/Packrat (Finder > Go > Go to Folder...)
  and saves pictures to your Pictures folder > Packrat. Change that in
  Settings. Passwords and API keys go in the Mac's Keychain.
  Packrat's icon sits in the menu bar at the top of the screen.
  Not on the Mac: hourly monitor checks while Packrat is closed, and the
  desktop / Start menu shortcuts (keep Packrat open in the menu bar
  instead). An Intel Mac uses the Python download (B).


==========================================================================
 FIRST STEPS
==========================================================================

  1. A short assistant asks a few questions: your sites, where to save,
     how to name files. Everything can be changed later in Settings.
     If you used Grabber before, it offers to import Grabber's settings
     and favorites (Grabber itself is not changed).
  2. Type what you are looking for (tags, like  cat_ears  or  landscape)
     and press Enter. Pictures appear. The button left of the search box
     chooses the sites.
  3. Click a picture to see it big. Arrow keys: next / previous.
     S saves it. Videos and animated pictures play right there.
  4. To download many: "Download all results...", "Download this page", or
     hold Ctrl and click pictures, then "Download selected".
     They go to the Downloads page. Press Start there.
     You can close Packrat in the middle: Start continues where it stopped.
     Pictures you already have are skipped.

  HELP IS BUILT IN: click Help (bottom left) or press F1, and type a
  question in your own words, e.g.   how do I save a whole search


==========================================================================
 THE PAGES (list on the left)
==========================================================================
  Browse        search, look at pictures, pick what to download
  Downloads     the download list: Start / Pause / Stop
  Favorites     searches you keep; "Check for new pictures" shows what is new
  Monitors      searches checked by themselves every few hours; they can
                download new pictures and show a notification
  Site archive  a complete copy of e6ai or e621 (the old e6ai Archiver)
  Settings      Basics first; every other setting in the pages below it
  Tools         import from Grabber, rename files you have, fix lists ...
  Log           what happened, and any problems

  Bottom left:  Sites and logins (which sites, logging in, adding a site),
                Help, and the light / dark switch.

  NEXT TO THE CLOCK: Packrat puts a small icon in the notification area
  (Windows may hide it behind the ^ arrow). Right-click it to open Packrat,
  check monitors, start downloads or quit. To keep Packrat running there
  when you close the window: Settings > Monitoring.

  LOGGING IN: Sites and logins > pick the site > "Log in / settings..."
  (API key or password), or "Log in with the built-in browser..." to log in
  on the site's own page inside Packrat.

  THE OLDER WINDOW: if the new window gives trouble, double-click
  "Start Packrat (classic window).bat" (Python download only). It is the
  3.0 window and uses the same settings and downloads.


==========================================================================
 UPGRADING FROM e6ai ARCHIVER 2.0
==========================================================================
Your archive is kept. Nothing is downloaded again.

  1. FIRST stop the old version: click "Stop" in its window and wait for
     "Stopped", then close it.
  2. Extract this zip to a NEW folder (for example C:\Packrat).
  3. Start Packrat, open the "Site archive" page. A line offers to import
     the settings of the old copy: click "Import settings from an older
     copy..." at the bottom and pick the old folder. Packrat then keeps
     using the old archive where it is. Nothing is moved or downloaded again.
  4. Your saved API key is found again by itself.

  (Extracting over the old folder works too; the old program files are
  then just not used any more.)
  Going back: e6ai Archiver 2.0 can still open the archive.

  Note: e6ai.net announced it closes at the end of November 2026.


==========================================================================
 FAQ / PROBLEMS
==========================================================================

'py' is not recognized / 'python' is not recognized / "Python was not found"
  Python is not installed, or "Add python.exe to PATH" was not ticked.
  Fix: run the Python installer again, choose "Modify" (or reinstall), and
  make sure "Add python.exe to PATH" is ticked. Then close every black
  window, and double-click "Check Python.bat" again. If Windows opens the
  Microsoft Store when you type python, that is only a shortcut, not real
  Python - install it from python.org as in step 1.

The first start fails with exit code 10 while "Installing ..."
  It could not download Packrat's packages. Check the internet connection
  and start again. If you only have a very new Python (newer than 3.14),
  also install Python 3.14 and start again: some packages need a few weeks
  before they work with a brand-new Python.

Windows protected your PC (SmartScreen) / "This file came from another computer"
  Windows warns about .bat files downloaded from the internet. Click
  "More info" > "Run anyway". To stop the warning: right-click the zip
  BEFORE extracting > Properties > tick "Unblock" > OK, then extract again.

"running scripts is disabled on this system" / execution policy
  The .bat files already start PowerShell with "-ExecutionPolicy Bypass"
  for that one window only, so you should not see this. You do not need to
  change any Windows setting.

A site shows no pictures / "HTTP 403" / "HTTP 429"
  The site refused or asked to slow down. Packrat waits and retries by
  itself; a 403 stops that site on purpose. Wait a while, check the site's
  login (Sites > click the site > "Log in / settings..." > Test login), and do not
  lower the speed limits. Some sites need a login for some pictures.

A site I use is not in the list
  Sites > "Add another site..." and type its address. Packrat recognizes most
  boorus by itself, or finds a matching source to install.

A video doesn't play
  Use "Open page" in the picture window to watch it on the site, or save it
  (S) and open the file. Tell me which site and picture, so it can be fixed.

Exit codes (if a black window shows "exit code N")
    0    finished OK
    1    stopped because of an error - see the Log tab
    2    a setting was wrong - fix it and try again
    3    a site answered 403 Forbidden - see above
    4    the disk is full - free some space, then Start
    5    another run is using the same archive folder - stop it first
    6    the drive disappeared or is read-only
    7    database problem (Site archive: choose "Repair the database")
    8    the login / API key was refused - check it
    9    Python was not found - see step 1
    10   setting up the program failed (no internet?) - see above
    11   the archive was made by a NEWER version - use that version
    130  stopped by you - Start continues where it left off

Where are my passwords kept?
  In the Windows Credential Manager, never in a file.


==========================================================================
 MORE HELP
==========================================================================
  docs\README.md        the full manual (Browse, Downloads, file names,
                        filters, favorites, monitors, sites and logins,
                        settings, tools, command line)
  docs\SITE-ARCHIVE.md  the Site archive manual (the old e6ai Archiver)
  docs\CHANGELOG.md     what changed in each version

Please be kind to the sites: the speed limits are polite on purpose.
