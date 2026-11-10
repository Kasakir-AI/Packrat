<#
  find_python.ps1 - finds a working Python for Packrat. Dot-sourced by run.ps1;
  you do not run it yourself. Windows PowerShell 5.1 compatible (no ?? / ?: / && / ||).

  Order tried (the first one that really runs and is new enough wins):
    1. the existing .venv\Scripts\python.exe        (handled in run.ps1)
    2. py -3.14, py -3.13, py -3.12, py -3.11, py -3   (the python.org launcher, if it is installed)
    3. python                                        (every python.exe on PATH, in PATH order)
    4. python3
    5. %LOCALAPPDATA%\Programs\Python\Python3*\python.exe, %ProgramFiles%\Python3*\python.exe,
       %ProgramFiles(x86)%\Python3*\python.exe, C:\Python3*\python.exe   (newest first)
  Each candidate is found with Get-Command / Test-Path (never by calling an unknown name) and then
  RUN once with -c "import sys; ..." - only a candidate that prints its version counts. That is what
  skips the Microsoft Store shortcut (%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe): when Python
  is not installed from the Store it prints nothing on stdout and exits with 9009.
  Versions from 3.11 to $PackratMaxTested are preferred: a newer Python is only used when nothing else is
  installed, because some packages (dukpy, Pillow) publish ready-made Windows builds a while after a new
  Python comes out, and without them installing needs a C compiler.
#>

# Keep in step with requires-python in pyproject.toml (a test checks this).
$PackratMinPython = [version]"3.11"
$PackratMaxTested = [version]"3.14.99"
# run.ps1 exit codes: 9 = no suitable Python, 10 = installing the archiver into .venv failed.
$PackratExitNoPython = 9
$PackratExitInstallFailed = 10

function Get-PythonVersion {
    # Runs the candidate and returns its [version], or $null if it does not run / is not Python.
    param([string]$Exe, [string[]]$PreArgs = @())
    if (-not $Exe) { return $null }
    $ErrorActionPreference = "Continue"   # local: a candidate writing to stderr must not stop run.ps1
    $out = $null
    $code = 1
    try {
        $out = & $Exe @PreArgs -c "import sys; print('PACKRAT-PY %d.%d.%d' % tuple(sys.version_info[:3]))" 2>$null
        $code = $LASTEXITCODE
    } catch {
        return $null
    }
    if ($code -ne 0) { return $null }
    foreach ($line in @($out)) {
        if ("$line" -match '^PACKRAT-PY (\d+\.\d+\.\d+)\s*$') { return [version]$Matches[1] }
    }
    return $null
}

function Test-PythonModule {
    # True when the interpreter can import every module named (e.g. "venv, ensurepip").
    param([string]$Exe, [string[]]$PreArgs = @(), [string]$Modules)
    $ErrorActionPreference = "Continue"
    try {
        & $Exe @PreArgs -c "import $Modules" 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function New-PythonCandidate([string]$Exe, [string[]]$PreArgs, [string]$Label) {
    return [pscustomobject]@{ Exe = $Exe; PreArgs = $PreArgs; Label = $Label; Version = $null }
}

function Get-InstallDirPython {
    # python.exe in Python3* folders under the usual install roots, newest version first.
    $roots = @()
    if ($env:LOCALAPPDATA) { $roots += (Join-Path (Join-Path $env:LOCALAPPDATA "Programs") "Python") }
    if ($env:ProgramFiles) { $roots += $env:ProgramFiles }
    $pf86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
    if ($pf86) { $roots += $pf86 }
    if ($env:SystemDrive) { $roots += ($env:SystemDrive + "\") }
    $found = @()
    foreach ($root in $roots) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        $dirs = @(Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue |
                  Where-Object { $_.Name -match '^Python3(\d+)' })
        foreach ($d in $dirs) {
            $exe = Join-Path $d.FullName "python.exe"
            if (Test-Path -LiteralPath $exe) {
                $minor = 0
                if ($d.Name -match '^Python3(\d+)') { $minor = [int]$Matches[1] }
                $found += [pscustomobject]@{ Exe = $exe; Minor = $minor }
            }
        }
    }
    return @($found | Sort-Object -Property Minor -Descending | ForEach-Object { $_.Exe })
}

function Get-PythonCandidateList {
    $list = @()
    # 2. py -3 (the launcher): on PATH, or in its usual places even when PATH was not updated.
    $launchers = @()
    foreach ($cmd in @(Get-Command "py" -CommandType Application -All -ErrorAction SilentlyContinue)) {
        $launchers += $cmd.Path
    }
    if ($env:SystemRoot) { $launchers += (Join-Path $env:SystemRoot "py.exe") }
    if ($env:LOCALAPPDATA) {
        $launchers += (Join-Path (Join-Path (Join-Path (Join-Path $env:LOCALAPPDATA "Programs") "Python") "Launcher") "py.exe")
    }
    foreach ($p in $launchers) {
        if ($p -and (Test-Path -LiteralPath $p)) {
            foreach ($v in @("-3.14", "-3.13", "-3.12", "-3.11", "-3")) {
                $list += New-PythonCandidate -Exe $p -PreArgs @($v) -Label "py $v ($p)"
            }
        }
    }
    # 3. / 4. python and python3 on PATH (all of them: the Store shortcut may come first).
    foreach ($name in @("python", "python3")) {
        foreach ($cmd in @(Get-Command $name -CommandType Application -All -ErrorAction SilentlyContinue)) {
            $list += New-PythonCandidate -Exe $cmd.Path -PreArgs @() -Label "$name ($($cmd.Path))"
        }
    }
    # 5. the usual install folders.
    foreach ($exe in @(Get-InstallDirPython)) {
        $list += New-PythonCandidate -Exe $exe -PreArgs @() -Label $exe
    }
    # Drop duplicates (same file + same arguments), keeping the first.
    $seen = @{}
    $unique = @()
    foreach ($c in $list) {
        $key = ($c.Exe + "|" + ($c.PreArgs -join " ")).ToLowerInvariant()
        if (-not $seen.ContainsKey($key)) { $seen[$key] = $true; $unique += $c }
    }
    return $unique
}

function Find-Python {
    # Returns the first candidate that runs and is >= $PackratMinPython (with .Version set), else $null.
    # Every candidate tried is recorded in $script:PackratPythonTried for the error message.
    param([object[]]$Candidates = $null)
    if ($null -eq $Candidates) { $Candidates = @(Get-PythonCandidateList) }
    $script:PackratPythonTried = @()
    $newer = $null
    foreach ($c in $Candidates) {
        $v = Get-PythonVersion -Exe $c.Exe -PreArgs $c.PreArgs
        if ($null -eq $v) {
            $why = "did not run"
            if ($c.Exe -match '[\\/]Microsoft[\\/]WindowsApps[\\/]') { $why = "Microsoft Store shortcut, not a real Python" }
            $script:PackratPythonTried += "  $($c.Label): $why"
            continue
        }
        if ($v -lt $PackratMinPython) {
            $script:PackratPythonTried += "  $($c.Label): Python $v is too old (need $PackratMinPython or newer)"
            continue
        }
        $c.Version = $v
        if ($v -gt $PackratMaxTested) {
            if ($null -eq $newer) { $newer = $c }
            $script:PackratPythonTried += "  $($c.Label): Python $v is newer than Packrat was tested with (kept as a last resort)"
            continue
        }
        return $c
    }
    if ($null -ne $newer) {
        Write-Host "Note: only Python $($newer.Version) was found. Packrat was tested with 3.11 - 3.14. If installing" -ForegroundColor Yellow
        Write-Host "      fails, also install Python 3.14 from https://www.python.org/downloads/windows/ and start again." -ForegroundColor Yellow
    }
    return $newer
}

function Write-NoPythonHelp {
    Write-Host ""
    Write-Host "Python $PackratMinPython or newer was not found on this PC." -ForegroundColor Red
    if ($script:PackratPythonTried -and $script:PackratPythonTried.Count -gt 0) {
        Write-Host "Checked:"
        foreach ($t in $script:PackratPythonTried) { Write-Host $t }
    }
    Write-Host ""
    Write-Host "To fix it (about 2 minutes):" -ForegroundColor Yellow
    Write-Host "  1. Download Python from https://www.python.org/downloads/windows/"
    Write-Host "     (Python 3.14 or 3.13, 'Windows installer (64-bit)')."
    Write-Host "  2. On the installer's FIRST screen tick  'Add python.exe to PATH', then click Install Now."
    Write-Host "  3. Close this PowerShell window, open a new one in this folder, and run the same command again."
    Write-Host ""
    Write-Host "Or, if you use winget, this installs it too (then reopen PowerShell):"
    Write-Host "     winget install -e --id Python.Python.3.14"
    Write-Host ""
    Write-Host "Note: a 'python' that only opens the Microsoft Store is a shortcut, not Python. It is skipped."
    Write-Host "(Exit code $PackratExitNoPython = no suitable Python.)"
}

function Write-NoVenvHelp([object]$Found) {
    Write-Host ""
    Write-Host "Found Python $($Found.Version) ($($Found.Label)), but it cannot create a virtual environment:" -ForegroundColor Red
    Write-Host "its 'venv' / 'ensurepip' modules are missing. This happens with trimmed-down or embedded Pythons."
    Write-Host "Install the normal Python from https://www.python.org/downloads/windows/ (tick 'Add python.exe to PATH'),"
    Write-Host "reopen PowerShell and run the same command again. (On Linux: install the python3-venv package.)"
    Write-Host "(Exit code $PackratExitNoPython = no suitable Python.)"
}
