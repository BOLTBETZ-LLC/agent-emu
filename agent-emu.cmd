@echo off
rem agent-emu: start the agent-emud daemon if it is not running, then open the control panel.
rem Closing the browser leaves the daemon and its Devices running; the panel's Quit button stops both.
rem Two layouts: this checkout (daemon\target\release\agent-emud.exe, images in C:\dev\agent-emu-work),
rem or an install (agent-emud.exe next to this file; config.cmd, written by the installer, sets AE_WORK and ports).
setlocal
set "ROOT=%~dp0"
set "AE_PORT=7401"
set "AE_AGENT_PORT=7400"
set "LOGDIR=%LOCALAPPDATA%\agent-emu"
if exist "%ROOT%config.cmd" call "%ROOT%config.cmd"
if exist "%ROOT%agent-emud.exe" (
  set "EXE=%ROOT%agent-emud.exe"
  set "WD=%ROOT%"
  set "AE_CROSVM=%ROOT%crosvm\crosvm.exe"
  set "AE_BOOT_SCRIPT=%ROOT%boot-device.ps1"
  set "PATH=%ROOT%adb;%PATH%"
) else (
  set "EXE=%ROOT%daemon\target\release\agent-emud.exe"
  set "WD=%ROOT%daemon"
)
set "AE_UI_ADDR=127.0.0.1:%AE_PORT%"
set "URL=http://127.0.0.1:%AE_PORT%/"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
curl -s -m 2 %URL%health >nul 2>&1
if errorlevel 1 (
  if not exist "%EXE%" (
    echo agent-emud is not built: %EXE%
    pause
    exit /b 1
  )
  rem Other crosvm processes on this PC (other tools' VMs) do not block the panel's Devices.
  set AE_ALLOW_OTHER_CROSVM=1
  powershell -NoProfile -Command "Start-Process -FilePath '%EXE%' -ArgumentList '--addr','127.0.0.1:%AE_AGENT_PORT%' -WorkingDirectory '%WD%' -WindowStyle Hidden -RedirectStandardError '%LOGDIR%\agent-emud.log' -RedirectStandardOutput '%LOGDIR%\agent-emud.out'"
  for /l %%i in (1,1,20) do (
    curl -s -m 1 %URL%health >nul 2>&1 && goto open
    timeout /t 1 /nobreak >nul
  )
  echo agent-emud did not start; see %LOGDIR%\agent-emud.log
  pause
  exit /b 1
)
:open
start "" %URL%
