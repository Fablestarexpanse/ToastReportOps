@echo off
rem Toast Report Workstation - double-click to launch.
rem Starts the backend + UI and opens the workstation in its own app window.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch.ps1"
if errorlevel 1 pause
