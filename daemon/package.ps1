# Builds dist\agent-emu-<date>.zip: one folder that runs agent-emu on another Windows PC.
# Unzip it anywhere, then run setup.cmd (or setup.ps1) inside it. Layout of the folder (= the install root):
#   agent-emud.exe agent-emu.cmd boot-device.ps1 setup.cmd setup.ps1 doctor.ps1 API.md
#   crosvm\      2D crosvm (crosvm-pmem build) + DLLs        crosvm-gpu\  gfxstream crosvm + patched libgfxstream_backend.dll
#   emulator\    the SDK emulator 37 DLLs gfxstream loads   adb\         platform-tools adb
#   images\      run dirs for -Images, stage1\unpack\vendor_boot.txt, browser\ (Firefox disk), gpu\kmod
#   tests\boltbetz\  the BoltBetz test runner and cases (no results); MCP run_case/run_lanes find it here
# The daemon finds all of it beside its exe (device.rs home()); AE_HOME overrides. Image files are hard-linked
# into dist\agent-emu (same volume, no copy), so the staging folder costs no disk until zipped.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File daemon\package.ps1 [-Images slim5,phone] [-NoBuild] [-NoZip]
param(
  [string[]]$Images = @("slim5"),
  [string]$Work = "C:\dev\agent-emu-work",
  [string]$Sdk = "$env:LOCALAPPDATA\Android\Sdk",
  [string]$Target = "",
  [switch]$NoBuild,
  [switch]$NoZip
)
$ErrorActionPreference = "Stop"
$Repo = Split-Path $PSScriptRoot -Parent
$Dist = Join-Path $Repo "dist"
if (-not $Target) { $Target = Join-Path $Dist "cargo-target" }
$Out = Join-Path $Dist "agent-emu"

# 1. Daemon release build in its own target dir (the dev daemon holds daemon\target\release\agent-emud.exe open).
if (-not $NoBuild) {
  $env:CARGO_TARGET_DIR = $Target
  $cargo = (Get-Command cargo -ErrorAction SilentlyContinue).Source
  if (-not $cargo) { $cargo = "$env:USERPROFILE\.cargo\bin\cargo.exe" }
  & $cargo build --release --manifest-path (Join-Path $PSScriptRoot "Cargo.toml")
  if ($LASTEXITCODE -ne 0) { throw "cargo build failed ($LASTEXITCODE)" }
}
$exe = Join-Path $Target "release\agent-emud.exe"
if (-not (Test-Path $exe)) { throw "$exe missing; build first" }

if (Test-Path $Out) { Remove-Item -Recurse -Force $Out }
New-Item -ItemType Directory -Force $Out | Out-Null
function Put([string]$src, [string]$destDir, [switch]$Link) {
  if (-not (Test-Path $src)) { throw "missing: $src" }
  $d = Join-Path $Out $destDir
  New-Item -ItemType Directory -Force $d | Out-Null
  $dst = Join-Path $d (Split-Path $src -Leaf)
  if ($Link) { New-Item -ItemType HardLink -Path $dst -Target $src | Out-Null } else { Copy-Item $src $dst }
}

# 2. Programs and scripts.
Put $exe "."
foreach ($f in "agent-emu.cmd", "daemon\boot-device.ps1", "daemon\pkg\setup.cmd", "daemon\pkg\setup.ps1", "daemon\pkg\doctor.ps1", "daemon\API.md") {
  Put (Join-Path $Repo $f) "."
}
$cv = Join-Path $Work "crosvm-pmem\target\release"
Put "$cv\crosvm.exe" "crosvm"; Get-ChildItem "$cv\*.dll" | ForEach-Object { Put $_.FullName "crosvm" }
$cg = Join-Path $Work "crosvm-gpu\target\release"
Put "$cg\crosvm.exe" "crosvm-gpu"; Get-ChildItem "$cg\*.dll" | ForEach-Object { Put $_.FullName "crosvm-gpu" }
# gfxstream's PATH is emulator\lib64\gles_angle; lib64; emulator (device.rs gfx_path): only those DLLs, no Qt/QEMU.
$emu = Join-Path $Sdk "emulator"
Get-ChildItem "$emu\*.dll" | ForEach-Object { Put $_.FullName "emulator" }
Get-ChildItem "$emu\lib64\*.dll" | ForEach-Object { Put $_.FullName "emulator\lib64" }
Get-ChildItem "$emu\lib64\gles_angle\*" -File | ForEach-Object { Put $_.FullName "emulator\lib64\gles_angle" }
Put "$emu\source.properties" "emulator"
foreach ($f in "adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll", "libwinpthread-1.dll") { Put (Join-Path $Sdk "platform-tools\$f") "adb" }

# 3. Images: run dir names come from device.rs image(); per run the files the daemon links into a Device dir
#    (SHARED, apk.img + apk.size included) and its read-only pmems.
$src = Get-Content -Raw (Join-Path $PSScriptRoot "agent-emud\src\device.rs")
$shared = [regex]::Matches([regex]::Match($src, '(?s)const SHARED: &\[&str\] = &\[(.*?)\];').Groups[1].Value, '"([^"]+)"') |
  ForEach-Object { $_.Groups[1].Value }
if (-not $shared) { throw "could not parse SHARED from device.rs" }
foreach ($name in $Images) {
  $m = [regex]::Match($src, "(?m)^\s*(?:""[^""]*""\s*\|\s*)*""$name""(?:\s*\|\s*""[^""]*"")*\s*=>\s*Ok\(\(""([^""]+)""")
  if (-not $m.Success) { throw "image $name not in device.rs image()" }
  $run = $m.Groups[1].Value
  foreach ($f in @($shared) + @("system-pmem.img", "app-pmem.img", "system_ext-pmem.img", "product-pmem.img", "vendor-pmem.img")) {
    $p = Join-Path $Work "$run\$f"
    if (Test-Path $p) { Put $p "images\$run" -Link }
  }
}
Put (Join-Path $Work "stage1\unpack\vendor_boot.txt") "images\stage1\unpack"
foreach ($f in "browser.img", "browser.size", "browser.pkg") { Put (Join-Path $Work "browser\$f") "images\browser" -Link }
Put (Join-Path $Work "gpu\kmod\virtio-gpu-nobacking.cpio") "images\gpu\kmod"

# 4. Test runner: tests\boltbetz scripts and cases, not results.
$Tests = Join-Path $Repo "tests\boltbetz"
Get-ChildItem "$Tests\*.py" | ForEach-Object { Put $_.FullName "tests\boltbetz" }
Copy-Item -Recurse (Join-Path $Tests "cases") (Join-Path $Out "tests\boltbetz\cases")

$bytes = (Get-ChildItem -Recurse -File $Out | Measure-Object Length -Sum).Sum
"staged $Out : $([math]::Round($bytes / 1GB, 2)) GB, images: $($Images -join ', ')"

# 5. One zip (bsdtar ships with Windows 10/11 and writes zip64, so the 8 GB super.img fits).
if (-not $NoZip) {
  $zip = Join-Path $Dist ("agent-emu-" + (Get-Date -Format "yyyy-MM-dd") + ".zip")
  if (Test-Path $zip) { Remove-Item $zip }
  & "$env:SystemRoot\System32\tar.exe" -a -c -f $zip -C $Dist agent-emu
  if ($LASTEXITCODE -ne 0) { throw "tar failed ($LASTEXITCODE)" }
  "{0}  {1:N2} GB" -f $zip, ((Get-Item $zip).Length / 1GB)
}
