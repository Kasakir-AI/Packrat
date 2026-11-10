<#
  Packrat launcher for Windows PowerShell.

  Normally you never run this yourself: double-click "Start Packrat.bat" in the Packrat folder.

  From PowerShell, in the Packrat folder:
      Set-ExecutionPolicy -Scope CurrentUser RemoteSigned    # once, if scripts are blocked
      .\app\run.ps1 -Mode app                               # the Packrat window
  The first run needs Python 3.11+ (python.org installer, tick "Add python.exe to PATH"). It looks for it
  in this order: an existing .venv, py -3, python, python3, then %LOCALAPPDATA%\Programs\Python\Python3*
  and %ProgramFiles%\Python3*. Each one is run once to check it is real (the Microsoft Store "python"
  shortcut is skipped) and new enough. .\app\run.ps1 -Mode check-python only shows what it would use.
  Launcher exit codes: 9 = no suitable Python found (install help is printed), 10 = installing Packrat
  into .venv failed (no internet?). Packrat's own codes (0-8, 130) are listed in the README.

  Window modes:
      app       the Packrat window (Browse, Downloads, Favorites, Monitors, Site archive, Settings, Tools)
      classic   the older Packrat window (3.0), kept as a fallback
      panel     only the site archive window (the e6ai-archiver 2.0 control panel)
      check-python  show which Python would be used (creates and installs nothing); exit 9 if none

  Browse / Downloads side, no window (good for Task Scheduler):
      monitors  check the monitors that are due and download what they found
      batch     run the download list (what the Start button of the Downloads tab does)

  Site archive modes (a complete copy of e6ai or e621; -Site e6ai is the default):
      test     5 posts, to check everything works
      full     official daily exports (verified) -> seed DB -> download files in -Order -> fetch newer posts
      sync     refresh the export bundle (max once a day; -Force to check now), merge it, re-rank the queue
      update   incremental: only posts newer than last run (good for Task Scheduler)
      download download any queued files
      retry    re-queue files that failed/mismatched/404'd, then download them
      dryrun   no network: what would be downloaded (in -Order / filters), total size, free space, minimum time
      stats    offline summary + size numbers
      preview  offline: the next -Count files in -Order (rank, score, favs, size, cumulative GiB, save path)
               plus a tier table (top 1k/10k/50k/all: files, GiB, hours)
      reorganize        offline DRY RUN: what would move to the current layout
      reorganize-apply  offline: actually move saved files (never deletes; audit CSV in logs\)
      repairdb offline: back up the database and rebuild it from the JSONL journals
      verify   check every saved file against its md5 (from posts.csv / the API); bad, truncated and
               zero-byte files are deleted (or -VerifyAction quarantine) and downloaded again
      verify-only  the same, offline: bad files are only removed and re-queued, nothing is downloaded
      report   offline completeness report: the archive vs the latest posts.csv export
      retry-failed  re-download ONLY the files currently marked failed / missing / md5_mismatch
               -Reason  403 | 404 | 429 | 503 | http | network | md5 | size | verify | unknown, or a list
               -FromCsv retry the posts listed in a CSV (post_id column), e.g. the report's missing.csv
               -DryRun  no network: only count what would be retried, by reason

  -Site                e6ai (default) | e621. One archive folder holds one site: use a different -Out per site.
  -Workers             parallel file downloads, 1-64 (default 16; files only, the API keeps -Rate)
  -OnLowDisk           pause (default) | stop (exit 4)
  -FailureLogMaxMB     trim failed-downloads.csv/.log above this many MB (default 10, 0 = never)
  -CheckMd5            also check each file's md5 while downloading (default off; verify does it later)
  -VerifyAction        delete (default) | quarantine
  -NoVerifyAfter / -NoReportAfter / -NoFetchTags / -ReportExport   see README
  -Gui                 run this one mode with the simple progress window
  -NamePreset / -FolderPreset / -NameTemplate / -FolderTemplate / -MultiDirector   file names and folders
  -Order / -MinScore / -MinFavs / -Rating / -Director / -Tags / -MaxSizeMB / -ExcludeExt / -LimitGB / -Top
  -Exports / -Count / -Autostart / -Force
  Example: .\app\run.ps1 -Mode full -Site e621 -Out "D:\e621-archive" -Order popular -LimitGB 50 -Gui

  Settings can also come from packrat.ini (or an old e6ai-archiver.ini) or PACKRAT_* environment variables.
  Optional sign-in (login name + API key, never your password): set $env:PACKRAT_API_KEY (and
  $env:PACKRAT_LOGIN if it differs from the username), or use the Site archive tab.
#>
param(
    [string]$Username = $(if ($env:PACKRAT_USERNAME) { $env:PACKRAT_USERNAME } else { $env:E6AI_USERNAME }),
    [string]$Out = "",
    [ValidateSet("e6ai", "e621")]
    [string]$Site,
    [ValidateSet("app", "classic", "panel", "monitors", "batch", "test", "full", "sync", "update", "download", "retry", "dryrun", "stats",
                 "preview", "reorganize", "reorganize-apply", "repairdb", "verify", "verify-only", "report",
                 "retry-failed", "check-python")]
    [string]$Mode = "update",
    [double]$Rate,
    [double]$MinFreeGB,
    [ValidateSet("pause", "stop")]
    [string]$OnLowDisk,
    [double]$FailureLogMaxMB,
    [ValidateRange(1, 64)]
    [int]$Workers,
    [switch]$CheckMd5,
    [ValidateSet("delete", "quarantine")]
    [string]$VerifyAction,
    [switch]$NoVerifyAfter,
    [switch]$NoReportAfter,
    [string]$ReportExport,
    [switch]$NoFetchTags,
    [string]$Reason,
    [string]$FromCsv,
    [switch]$DryRun,
    [switch]$Gui,
    [string]$NamePreset,
    [string]$FolderPreset,
    [string]$NameTemplate,
    [string]$FolderTemplate,
    [ValidateSet("first", "multi", "copy", "hardlink")]
    [string]$MultiDirector,
    [ValidateSet("default", "popular", "favorites", "newest", "oldest", "smallest-first", "balanced")]
    [string]$Order,
    [int]$MinScore,
    [int]$MinFavs,
    [string]$Rating,
    [string]$Director,
    [string]$Tags,
    [double]$MaxSizeMB,
    [string]$ExcludeExt,
    [double]$LimitGB,
    [int]$Top,
    [string]$Exports,
    [int]$Count,
    [switch]$Autostart,
    [switch]$Force
)
$ErrorActionPreference = "Stop"
# In the distribution zip this script lives in app\ and the install folder (the one with "Start Packrat.bat")
# is its parent. Settings (packrat.ini), data\, downloads\, .venv and the default archive folder stay in the
# install folder (where e6ai-archiver kept them), so an upgrade over e6ai-archiver changes no paths.
$PackratApp = $PSScriptRoot
$PackratRoot = $PSScriptRoot
if ((Split-Path -Leaf $PSScriptRoot) -eq "app" -and (Test-Path -LiteralPath (Join-Path $PSScriptRoot "packrat"))) {
    $PackratRoot = Split-Path -Parent $PSScriptRoot
}
if (-not $Out) { $Out = Join-Path $PackratRoot "archive" }
Set-Location $PackratRoot
# Python must import Packrat from app\ (via .venv), never an old copy left in the install folder by an
# upgrade "unzip over". 3.11+: do not put the current folder on sys.path.
$env:PYTHONSAFEPATH = "1"

. (Join-Path $PSScriptRoot "find_python.ps1")

function Get-AppVersion {
    $init = Join-Path $PackratApp "packrat\__init__.py"
    if (-not (Test-Path -LiteralPath $init)) { return $null }
    $m = Select-String -LiteralPath $init -Pattern '__version__\s*=\s*"([^"]+)"' | Select-Object -First 1
    if ($m) { return $m.Matches[0].Groups[1].Value }
    return $null
}

function Test-ArchiverCurrent([string]$py) {
    # True if .venv imports THIS copy of Packrat (an older .venv may point at e6ai-archiver).
    $want = Get-AppVersion
    $ErrorActionPreference = "Continue"
    $out = & $py -P -c "import os, packrat as m; print('PACKRAT-APP', m.__version__, os.path.dirname(os.path.dirname(os.path.abspath(m.__file__))))" 2>$null
    $ErrorActionPreference = "Stop"
    $line = @($out) | Where-Object { "$_".StartsWith("PACKRAT-APP ") } | Select-Object -First 1
    if (-not $line) { return $false }
    $parts = "$line".Split(" ", 3)
    if ($parts.Count -lt 3) { return $false }
    $here = [System.IO.Path]::GetFullPath($PackratApp).TrimEnd("\", "/")
    $there = [System.IO.Path]::GetFullPath($parts[2]).TrimEnd("\", "/")
    if ($there -ne $here -and $there.ToLowerInvariant() -ne $here.ToLowerInvariant()) { return $false }
    return ($null -eq $want -or $parts[1] -eq $want)
}

function Write-LeftoverNote {
    # After unzipping over an old e6ai-archiver 1.x folder, its program files are still there. They are not used.
    if ($PackratRoot -eq $PackratApp) { return }
    $old = @("e6ai_archiver", "run.ps1", "run.bat", "run_gui.bat", "find_python.ps1", "pyproject.toml") |
        Where-Object { Test-Path -LiteralPath (Join-Path $PackratRoot $_) }
    if ($old.Count -gt 0) {
        Write-Host "Note: files from an older version are still in $PackratRoot ($($old -join ', '))." -ForegroundColor Yellow
        Write-Host "      They are not used any more and can be deleted. Keep archive\, data\, downloads\, .venv\ and the .ini file."
    }
}

function Test-VenvPython([string]$py) {
    # True when .venv's python.exe still runs and is new enough (a removed or upgraded system Python
    # leaves a .venv whose python.exe no longer starts).
    $v = Get-PythonVersion -Exe $py
    return ($null -ne $v -and $v -ge $PackratMinPython)
}

function Install-Archiver([string]$py) {
    # pip output goes to the console (Out-Host), never into the function's return value.
    Write-Host "Installing Packrat and its packages into .venv (needs internet; about 250 MB, a few minutes) ..."
    $ErrorActionPreference = "Continue"   # local: pip notices on stderr must not stop the script
    & $py -m pip install --upgrade pip | Out-Host
    & $py -m pip install -e $PackratApp | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "Installing Packrat's packages into .venv failed (pip exit code $LASTEXITCODE)." -ForegroundColor Red
        Write-Host "Check the internet connection (or proxy), then run the same command again; it picks up where it stopped."
        Write-Host "(Exit code $PackratExitInstallFailed = install into .venv failed.)"
        exit $PackratExitInstallFailed
    }
}

function Initialize-Venv {
    # Returns the path of .venv\Scripts\python.exe, creating .venv first if needed.
    # Exits 9 when no suitable Python is found, 10 when installing into .venv fails.
    $venvDir = Join-Path $PackratRoot ".venv"
    $py = Join-Path $venvDir "Scripts\python.exe"
    # 1. the existing .venv
    if (Test-Path -LiteralPath $py) {
        if (Test-VenvPython $py) {
            if (-not (Test-ArchiverCurrent $py)) {
                # missing, half-installed, or an older version's .venv (e.g. e6ai-archiver 2.0): reuse it,
                # point it at this copy and add the new packages. Nothing in the archive is touched.
                Write-Host "Updating .venv to Packrat $(Get-AppVersion) ..."
                Install-Archiver $py
            }
            return $py
        }
    }
    if (Test-Path -LiteralPath $venvDir) {
        # Broken or half-made .venv: keep it aside (not deleted) and make a new one.
        $aside = $venvDir + ".broken-" + (Get-Date -Format "yyyyMMdd-HHmmss")
        Write-Host "The existing .venv does not work (its Python was removed, is too old, or setup was interrupted)." -ForegroundColor Yellow
        Write-Host "Moving it to $(Split-Path $aside -Leaf) and creating a new one. You can delete that folder later."
        try {
            Move-Item -LiteralPath $venvDir -Destination $aside
        } catch {
            Write-Host "Could not move .venv ($($_.Exception.Message))." -ForegroundColor Red
            Write-Host "Close Packrat (and any running archive), delete the .venv folder by hand, and run again."
            exit 1
        }
    }
    # 2.-5. py -3, python, python3, the usual install folders
    $found = Find-Python
    if ($null -eq $found) { Write-NoPythonHelp; exit $PackratExitNoPython }
    Write-Host "Using Python $($found.Version): $($found.Label)"
    if (-not (Test-PythonModule -Exe $found.Exe -PreArgs $found.PreArgs -Modules "venv, ensurepip")) {
        Write-NoVenvHelp $found
        exit $PackratExitNoPython
    }
    Write-Host "Creating virtual environment (.venv) ..."
    $ErrorActionPreference = "Continue"
    & $found.Exe @($found.PreArgs) -m venv $venvDir | Out-Host
    $code = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    if ($code -ne 0 -or -not (Test-Path -LiteralPath $py)) {
        Write-Host ""
        Write-Host "Python $($found.Version) could not create .venv (exit code $code)." -ForegroundColor Red
        Write-Host "Check that this folder is writable (not inside OneDrive / Program Files), delete any half-made .venv,"
        Write-Host "and run the same command again. If it keeps failing, reinstall Python from https://www.python.org/downloads/windows/"
        Write-Host "(Exit code $PackratExitNoPython = no suitable Python.)"
        exit $PackratExitNoPython
    }
    Install-Archiver $py
    return $py
}

if ($Mode -eq "check-python") {
    # 1.9.1: only report which Python would be used (nothing is created or installed).
    $venvPy = Join-Path $PackratRoot ".venv\Scripts\python.exe"
    Write-Host "Install folder: $PackratRoot (settings, .venv and the default archive folder live here)"
    Write-Host "Program files:  $PackratApp"
    if ((Test-Path -LiteralPath $venvPy) -and (Test-VenvPython $venvPy)) {
        Write-Host "OK: .venv already works (Python $(Get-PythonVersion -Exe $venvPy)): $venvPy" -ForegroundColor Green
    }
    $found = Find-Python
    if ($null -eq $found) { Write-NoPythonHelp; exit $PackratExitNoPython }
    foreach ($t in $script:PackratPythonTried) { Write-Host "Skipped:$t" }
    Write-Host "OK: would use Python $($found.Version): $($found.Label)" -ForegroundColor Green
    exit 0
}

if ($Mode -eq "app" -or $Mode -eq "classic" -or $Mode -eq "panel") {
    # A window without a console. Downloads and archive runs happen in the background (child processes).
    Write-LeftoverNote
    $py = Initialize-Venv
    $pyw = Join-Path $PackratRoot ".venv\Scripts\pythonw.exe"
    if (-not (Test-Path $pyw)) { $pyw = $py }
    if ($Mode -eq "app") {
        $winArgs = @("-m", "packrat", "gui")
    } elseif ($Mode -eq "classic") {
        $winArgs = @("-m", "packrat", "gui", "--classic")
    } else {
        $winArgs = @("-m", "packrat.control_panel")
        if ($PSBoundParameters.ContainsKey("Username")) { $winArgs += @("--username", $Username) }
        if ($PSBoundParameters.ContainsKey("Out"))      { $winArgs += @("--out", $Out) }
        if ($Site)                                     { $winArgs += @("--site", $Site) }
    }
    Start-Process -FilePath $pyw -ArgumentList $winArgs -WorkingDirectory $PackratRoot
    exit 0
}

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

if ($Mode -eq "monitors" -or $Mode -eq "batch") {
    $py = Initialize-Venv
    if ($Mode -eq "monitors") { & $py -m packrat monitors --run-due } else { & $py -m packrat batch }
    $code = $LASTEXITCODE
    if ($code -ne 0) { Write-Host "Exited with code $code (see data\logs\packrat.log)." -ForegroundColor Red }
    exit $code
}

$hasIni = (Test-Path (Join-Path $PackratRoot "packrat.ini")) -or (Test-Path (Join-Path $PackratRoot "e6ai-archiver.ini"))
if (-not $Username -and -not $hasIni) {
    Write-Host "Your username on the site is required (it goes in the User-Agent)." -ForegroundColor Yellow
    Write-Host "Example: .\app\run.ps1 -Username myname -Mode test"
    exit 2
}
if ($Username) { $env:PACKRAT_USERNAME = $Username }

$venvPy = Initialize-Venv

New-Item -ItemType Directory -Force -Path $Out | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Out "logs") | Out-Null
$log = Join-Path $Out ("logs\run-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log")
$inv = [System.Globalization.CultureInfo]::InvariantCulture
$common = @("-m", "packrat", "--out", $Out, "--log-file", $log)
if ($Site)           { $common += @("--site", $Site) }
if ($PSBoundParameters.ContainsKey("Rate"))      { $common += @("--rate", $Rate.ToString($inv)) }
if ($PSBoundParameters.ContainsKey("MinFreeGB")) { $common += @("--min-free-gb", $MinFreeGB.ToString($inv)) }
if ($PSBoundParameters.ContainsKey("Workers"))   { $common += @("--workers", "$Workers") }
if ($OnLowDisk)      { $common += @("--on-low-disk", $OnLowDisk) }
if ($PSBoundParameters.ContainsKey("FailureLogMaxMB")) { $common += @("--failure-log-max-mb", $FailureLogMaxMB.ToString($inv)) }
if ($CheckMd5)       { $common += @("--check-md5") }
if ($Gui)            { $common += @("--gui") }
if ($NamePreset)     { $common += @("--name-preset", $NamePreset) }
if ($FolderPreset)   { $common += @("--folder-preset", $FolderPreset) }
if ($NameTemplate)   { $common += @("--name-template", $NameTemplate) }
if ($FolderTemplate) { $common += @("--folder-template", $FolderTemplate) }
if ($MultiDirector)  { $common += @("--multi-director", $MultiDirector) }

# Order/filter options: only passed when given, so ini/env values apply otherwise.
$prio = @()
if ($Order)      { $prio += @("--order", $Order) }
if ($PSBoundParameters.ContainsKey("MinScore"))  { $prio += @("--min-score", "$MinScore") }
if ($PSBoundParameters.ContainsKey("MinFavs"))   { $prio += @("--min-favs", "$MinFavs") }
if ($Rating)     { $prio += @("--rating", $Rating) }
if ($Director)   { $prio += @("--director", $Director) }
if ($PSBoundParameters.ContainsKey("MaxSizeMB")) { $prio += @("--max-size-mb", $MaxSizeMB.ToString($inv)) }
if ($ExcludeExt) { $prio += @("--exclude-ext", $ExcludeExt) }
if ($PSBoundParameters.ContainsKey("LimitGB"))   { $prio += @("--limit-gb", $LimitGB.ToString($inv)) }
if ($PSBoundParameters.ContainsKey("Top"))       { $prio += @("--top", "$Top") }
if ($Autostart)  { $prio += @("--autostart") }
$filt = @(); if ($Tags) { $filt += @("--filter-tags", $Tags) }
$bundle = @(); if ($Exports) { $bundle += @("--exports", $Exports) }
$cnt = @(); if ($PSBoundParameters.ContainsKey("Count")) { $cnt += @("--count", "$Count") }
$forceArg = @(); if ($Force) { $forceArg += @("--force") }
$va = @(); if ($VerifyAction) { $va += @("--verify-action", $VerifyAction) }
$fullExtra = @(); if ($NoVerifyAfter) { $fullExtra += @("--no-verify-after") }
if ($NoReportAfter) { $fullExtra += @("--no-report-after") }
$rep = @(); if ($ReportExport) { $rep += @("--export", $ReportExport) }
$tagsArg = @(); if ($NoFetchTags) { $tagsArg += @("--no-fetch-tags") }
$rf = @()
if ($Reason)  { $rf += @("--reason", $Reason) }
if ($FromCsv) { $rf += @("--from-csv", $FromCsv) }
if ($DryRun)  { $rf += @("--dry-run") }
if ($NoReportAfter) { $rf += @("--no-report-after") }

function Invoke-Archiver([string[]]$cmdArgs) {
    & $venvPy @common @cmdArgs
    $code = $LASTEXITCODE
    switch ($code) {
        0   { }
        1   { Write-Host "Stopped on an error; progress is saved, rerun to resume. See $log" -ForegroundColor Red; exit $code }
        2   { Write-Host "Configuration error (see log)." -ForegroundColor Red; exit $code }
        3   { Write-Host "HTTP 403 from the site (API, or 20 file downloads in a row): STOPPED. Do not retry with another User-Agent; wait, or lower -Workers. See $log" -ForegroundColor Red; exit $code }
        4   { Write-Host "Disk almost full: stopped cleanly. Free space and rerun to resume." -ForegroundColor Red; exit $code }
        5   { Write-Host "Another Packrat / e6ai-archiver run is still using $Out. Stop it first (Stop button / Ctrl+C in its window), then run again." -ForegroundColor Red; exit $code }
        6   { Write-Host "Archive folder not writable or drive gone (USB unplugged?). Reconnect it and rerun." -ForegroundColor Red; exit $code }
        7   { Write-Host "Database problem. Run: .\run.ps1 -Mode repairdb   (it makes a backup first)" -ForegroundColor Red; exit $code }
        8   { Write-Host "Sign-in rejected (HTTP 401): check the login name and API key." -ForegroundColor Red; exit $code }
        11  { Write-Host "This archive was made by a NEWER Packrat; it was left untouched. Use that version (or later)." -ForegroundColor Red; exit $code }
        130 { Write-Host "Interrupted. Progress saved; rerun to resume." -ForegroundColor Yellow; exit $code }
        default { Write-Host "Exited with code $code (see $log)." -ForegroundColor Red; exit $code }
    }
}

switch ($Mode) {
    "test"     { Invoke-Archiver @("run", "--max-posts", "5") }
    "full"     { $r = @(); if ($Force) { $r = @("--refresh-export") }; Invoke-Archiver (@("full") + $prio + $filt + $bundle + $r + $va + $fullExtra) }
    "sync"     { Invoke-Archiver (@("sync-exports") + $prio + $bundle + $forceArg) }
    "update"   { Invoke-Archiver (@("run") + $prio + $filt) }
    "download" { Invoke-Archiver (@("download") + $prio + $filt) }
    "retry"    { Invoke-Archiver (@("download", "--retry-failed") + $prio + $filt) }
    "dryrun"   { Invoke-Archiver (@("download", "--dry-run") + $prio + $filt) }
    "stats"    { Invoke-Archiver @("stats") }
    "preview"  { Invoke-Archiver (@("preview") + $prio + $filt + $cnt) }
    "reorganize"       { Invoke-Archiver @("reorganize") }
    "reorganize-apply" { Invoke-Archiver @("reorganize", "--apply") }
    "repairdb" { Invoke-Archiver @("repair-db") }
    "verify"   { Invoke-Archiver (@("verify", "--redownload") + $va) }
    "verify-only" { Invoke-Archiver (@("verify") + $va) }
    "report"   { Invoke-Archiver (@("report") + $prio + $filt + $rep + $tagsArg) }
    "retry-failed" { Invoke-Archiver (@("retry-failed") + $prio + $filt + $rf + $tagsArg) }
}
Write-Host "Done. Log: $log"
