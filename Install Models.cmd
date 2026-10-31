@echo off
rem Toast Report Workstation - double-click to download the AI models SAFELY.
rem Official models only, checked against pinned fingerprints before and after
rem download, skipped if too big for your graphics card, test-run afterwards.
rem Add  -Profile bakeoff-12gb  (or bakeoff-24gb) to also fetch comparison models.
title Toast Report - install models
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\models.ps1" %*
if errorlevel 1 (
  echo.
  echo Something needs attention - read the message above.
)
pause
