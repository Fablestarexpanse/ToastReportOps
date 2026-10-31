@echo off
rem Toast Report Workstation - double-click to UPDATE to the latest version, then launch.
rem Takes a safety snapshot first, pulls the new code, rebuilds only what changed,
rem restarts the stack and opens the workstation. Use ToastReport.cmd to launch
rem WITHOUT updating.
title Toast Report - update and launch
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\update.ps1" %*
if errorlevel 1 (
  echo.
  echo Something needs attention - read the message above.
  pause
)
