# Stages what electron-builder packs: dist\agent-emu (daemon\package.ps1, unchanged) + payload\python
# (official Windows embeddable Python, sha256-pinned to python.org's sigstore digest, PyYAML vendored for plan.py).
#   -SkipPackage   reuse the existing dist\agent-emu (no cargo build, no restage)
param([switch]$SkipPackage)
$ErrorActionPreference = "Stop"
$Desk = Split-Path $PSScriptRoot -Parent
$Repo = Split-Path $Desk -Parent
if (-not $SkipPackage) {
  & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Repo "daemon\package.ps1") -NoZip
  if ($LASTEXITCODE -ne 0) { throw "package.ps1 failed ($LASTEXITCODE)" }
}
if (-not (Test-Path (Join-Path $Repo "dist\agent-emu\agent-emud.exe"))) { throw "dist\agent-emu missing: run without -SkipPackage" }

$PyVer = "3.14.7"
$PySha = "d297e5ff019966817ad8502465176139f2d3d840fa4ed84b13bed399a6ab1f15"
$zip = Join-Path $Desk "payload\python-embed.zip"
$py = Join-Path $Desk "payload\python"
New-Item -ItemType Directory -Force (Split-Path $zip) | Out-Null
if (-not (Test-Path $zip) -or (Get-FileHash $zip -Algorithm SHA256).Hash -ne $PySha) {
  Invoke-WebRequest "https://www.python.org/ftp/python/$PyVer/python-$PyVer-embed-amd64.zip" -OutFile $zip
  if ((Get-FileHash $zip -Algorithm SHA256).Hash -ne $PySha) { Remove-Item $zip; throw "python embed sha256 mismatch" }
}
if (Test-Path $py) { Remove-Item -Recurse -Force $py }
Expand-Archive $zip $py
# The ._pth file fixes sys.path: add vendored site-packages and the runner folder (ROOT\tests\boltbetz).
$pth = Get-ChildItem $py -Filter "python*._pth" | Select-Object -First 1
Add-Content $pth.FullName "Lib\site-packages`r`n..\tests\boltbetz"
& python -m pip install --quiet --no-deps --only-binary ":all:" --platform win_amd64 --python-version $PyVer `
  --target (Join-Path $py "Lib\site-packages") pyyaml
if ($LASTEXITCODE -ne 0) { throw "pip pyyaml failed" }
"payload ready: dist\agent-emu + payload\python ($PyVer, PyYAML)"
