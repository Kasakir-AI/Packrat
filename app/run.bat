@echo off
rem Packrat site archive from cmd.exe (the window is "Start Packrat.bat" in the folder above). Usage:
rem   run.bat YOUR_NAME [test^|full^|update^|download^|retry^|dryrun^|stats^|preview^|sync^|reorganize^|repairdb^|verify^|verify-only^|report^|retry-failed] [WORKERS] [SITE]
rem WORKERS = parallel file downloads (1-64, default 16), SITE = e6ai (default) or e621
rem   e.g.  run.bat myname full 8 e621
rem Press Ctrl-C (or Ctrl-Break) to stop; progress is saved and the next run resumes.
setlocal
cd /d "%~dp0"
if "%~1"=="" (
  echo Usage: run.bat YOUR_NAME [test^|full^|update^|download^|retry^|dryrun^|stats^|preview^|sync^|reorganize^|repairdb^|verify^|verify-only^|report^|retry-failed] [WORKERS] [SITE]
  exit /b 2
)
set "MODE=%~2"
if "%MODE%"=="" set "MODE=update"
set "WARG="
if not "%~3"=="" set "WARG=-Workers %~3"
set "SARG="
if not "%~4"=="" set "SARG=-Site %~4"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" -Username "%~1" -Mode %MODE% %WARG% %SARG%
exit /b %ERRORLEVEL%
