; Per-user install only (no UAC prompt, no all-users page).
!macro customInstallMode
  StrCpy $isForceCurrentInstall "1"
!macroend

; Real uninstall (not an update): the app stops its own daemon, removes its MCP entries and the install root
; (%LOCALAPPDATA%\agent-emu\app: programs + phone images), then the app data folder (secrets, config) goes.
!macro customUnInstall
  ${ifNot} ${isUpdated}
    ExecWait '"$INSTDIR\${PRODUCT_FILENAME}.exe" --uninstall'
    RMDir /r "$APPDATA\${PRODUCT_NAME}"
  ${endIf}
!macroend
