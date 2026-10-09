# Base image pack for the first-run download. Holds NO app under test: apk.img is a 4 KiB zero placeholder and
# apk.size is 0, so the pack is the bare phone OS (+ Firefox). set_default_app / install_app add the app later.
# Stages hard links from -Work (same file list as daemon\package.ps1), checks no "boltbetz" string is inside,
# zips it (paths relative to images\) and prints name, size, sha256 for the feed manifest.
param([string]$Work = "C:\dev\agent-emu-work", [string[]]$Images = @("slim5"), [string]$Out = "",
  [string]$Version = (Get-Date -Format "yyyy-MM-dd"), [switch]$NoScan)
$ErrorActionPreference = "Stop"
$Repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (-not $Out) { $Out = Join-Path $Repo "desktop\payload\feed" }
$Stage = Join-Path $Repo "desktop\payload\images-stage"
if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
New-Item -ItemType Directory -Force $Stage, $Out | Out-Null
function Link([string]$src, [string]$dir) {
  if (-not (Test-Path $src)) { throw "missing: $src" }
  $d = Join-Path $Stage $dir; New-Item -ItemType Directory -Force $d | Out-Null
  New-Item -ItemType HardLink -Path (Join-Path $d (Split-Path $src -Leaf)) -Target $src | Out-Null
}
$src = Get-Content -Raw (Join-Path $Repo "daemon\agent-emud\src\device.rs")
$shared = [regex]::Matches([regex]::Match($src, '(?s)const SHARED: &\[&str\] = &\[(.*?)\];').Groups[1].Value, '"([^"]+)"') |
  ForEach-Object { $_.Groups[1].Value } | Where-Object { $_ -notin "apk.img", "apk.size" }
foreach ($name in $Images) {
  $m = [regex]::Match($src, "(?m)^\s*(?:""[^""]*""\s*\|\s*)*""$name""(?:\s*\|\s*""[^""]*"")*\s*=>\s*Ok\(\(""([^""]+)""")
  if (-not $m.Success) { throw "image $name not in device.rs image()" }
  $run = $m.Groups[1].Value
  foreach ($f in @($shared) + @("system-pmem.img", "app-pmem.img", "system_ext-pmem.img", "product-pmem.img", "vendor-pmem.img")) {
    $p = Join-Path $Work "$run\$f"
    if (Test-Path $p) { Link $p "run\$run" }
  }
  $r = Join-Path $Stage "run\$run"
  [IO.File]::WriteAllBytes((Join-Path $r "apk.img"), (New-Object byte[] 4096))
  Set-Content -NoNewline -Encoding ASCII (Join-Path $r "apk.size") "0"
}
Link (Join-Path $Work "stage1\unpack\vendor_boot.txt") "stage1\unpack"
foreach ($f in "browser.img", "browser.size", "browser.pkg") { Link (Join-Path $Work "browser\$f") "browser" }
Link (Join-Path $Work "gpu\kmod\virtio-gpu-nobacking.cpio") "gpu\kmod"
# run\<dir> -> <dir> at the top of the pack (images\run-slim5\...).
Get-ChildItem (Join-Path $Stage "run") -Directory | ForEach-Object { Move-Item $_.FullName (Join-Path $Stage $_.Name) }
Remove-Item (Join-Path $Stage "run")

if (-not $NoScan) {
  $hits = & rg -a -i -l "boltbetz" $Stage
  if ($hits) { throw "BoltBetz strings found in the pack: $hits" }
  "scan: no 'boltbetz' string in any packed file"
}
$name = "agent-emu-images-$Version.zip"
$zip = Join-Path $Out $name
if (Test-Path $zip) { Remove-Item $zip }
& "$env:SystemRoot\System32\tar.exe" -a -c -f $zip -C $Stage .
if ($LASTEXITCODE -ne 0) { throw "tar failed ($LASTEXITCODE)" }
$f = Get-Item $zip
"{0} {1} {2}" -f $name, $f.Length, (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
