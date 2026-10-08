@echo off
rem agent-emu: start the agent-emud daemon if it is not running, then open the control panel.
rem Closing the browser leaves the daemon and its Devices running; the panel's Quit button stops both.
setlocal
set "EXE=%~dp0daemon\target\release\agent-emud.exe"
set "LOGDIR=%LOCALAPPDATA%\agent-emu"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
curl -s -m 2 http://127.0.0.1:7401/health >nul 2>&1
if errorlevel 1 (
  if not exist "%EXE%" (
    echo agent-emud is not built: %EXE%
    pause
    exit /b 1
  )
  rem Other crosvm processes on this PC (other tools' VMs) do not block the panel's Devices.
  set AE_ALLOW_OTHER_CROSVM=1
  powershell -NoProfile -Command "Start-Process -FilePath '%EXE%' -WorkingDirectory '%~dp0daemon' -WindowStyle Hidden -RedirectStandardError '%LOGDIR%\agent-emud.log' -RedirectStandardOutput '%LOGDIR%\agent-emud.out'"
  for /l %%i in (1,1,20) do (
    curl -s -m 1 http://127.0.0.1:7401/health >nul 2>&1 && goto open
    timeout /t 1 /nobreak >nul
  )
  echo agent-emud did not start; see %LOGDIR%\agent-emud.log
  pause
  exit /b 1
)
:open
start "" http://127.0.0.1:7401/
