@echo off
rem Toast Report Workstation - double-click for a first-time install (safe to re-run).
rem Checks this computer (and starts Docker Desktop if needed), creates .env with fresh
rem local passwords, installs the UI packages, starts the stack, seeds a new database,
rem installs the AI models (it shows you the plan first), verifies, then opens the app.
rem Everything it prints is also saved to install.log in this folder.
rem   Install.cmd -CheckOnly    only check this computer; change nothing
title Toast Report - install
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1" %*
echo.
if errorlevel 1 (
  echo Something needs attention - read the message above, or open install.log.
) else (
  echo Install finished. The full log is in install.log.
)
pause
