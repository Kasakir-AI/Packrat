@echo off
rem Double-click this file to open Packrat.
rem "-ExecutionPolicy Bypass" applies to this one PowerShell process only; it changes no Windows setting.
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\run.ps1" -Mode app
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
  echo.
  echo Packrat closed with exit code %CODE%. See "START HERE - README.txt", section "Exit codes".
  pause
)
exit /b %CODE%
