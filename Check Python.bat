@echo off
rem Double-click to see which Python Packrat would use. Installs and changes nothing.
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\run.ps1" -Mode check-python
echo.
if "%ERRORLEVEL%"=="0" (
  echo OK - Python is ready. Close this window and double-click "Start Packrat.bat".
) else (
  echo No usable Python was found. See step 1 in "START HERE - README.txt".
)
pause
