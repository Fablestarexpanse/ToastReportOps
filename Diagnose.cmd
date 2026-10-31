@echo off
rem Toast Report Workstation - double-click to write a read-only health report
rem (diagnostics.txt) for bug reports. Changes nothing.
title Toast Report - diagnostics
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\diagnose.ps1" %*
echo.
pause
