@echo off
rem Double-click: opens Packrat (the same as "Start Packrat.bat" in the folder above).
rem
rem Only the site archive, one mode with the simple progress window:
rem   run_gui.bat YOUR_NAME [mode]     e.g.  run_gui.bat myname full
setlocal
cd /d "%~dp0"
if not "%~1"=="" goto :legacy
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" -Mode app
goto :done
:legacy
set "MODE=%~2"
if "%MODE%"=="" set "MODE=full"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" -Username "%~1" -Gui -Mode %MODE%
:done
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" pause
exit /b %CODE%
