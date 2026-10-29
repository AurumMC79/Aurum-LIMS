@echo off
rem Double-click to (re)create the "Aurum LIMS" desktop and Start menu shortcuts.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0create_shortcut.ps1" -StartMenu
pause
