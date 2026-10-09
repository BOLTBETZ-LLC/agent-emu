# Stages what electron-builder packs: dist\agent-emu from daemon\package.ps1 (unchanged; builds the daemon from this
# tree). Python, PyYAML, pict.exe and the phone images are not packed: the app downloads them on first run from the
# feed (package.json agentEmu.feed; build the feed with pack-images.ps1 + publish-feed.ps1).
#   -SkipPackage   reuse the existing dist\agent-emu (no cargo build, no restage)
param([switch]$SkipPackage)
$ErrorActionPreference = "Stop"
$Repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (-not $SkipPackage) {
  & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Repo "daemon\package.ps1") -NoZip
  if ($LASTEXITCODE -ne 0) { throw "package.ps1 failed ($LASTEXITCODE)" }
}
if (-not (Test-Path (Join-Path $Repo "dist\agent-emu\agent-emud.exe"))) { throw "dist\agent-emu missing: run without -SkipPackage" }
"payload ready: dist\agent-emu"
