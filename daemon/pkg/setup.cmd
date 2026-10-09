@echo off
rem Double-click to install agent-emu from this folder (see setup.ps1 for options).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
pause
