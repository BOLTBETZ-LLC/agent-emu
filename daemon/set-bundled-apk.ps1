# Makes an APK the one `install_bundled` (and `fleet`) installs: writes <Work>\apk\<name>.img (the APK padded to
# 4 KiB, the guest's /dev/block/vdb) and <name>.size, then hard-links both as apk.img / apk.size into every run dir.
# Running Devices keep the file they booted with (old names are renamed, never written; each Device dir has its own
# links, apk.size included), so a swap is safe while phones run. They get the new APK on their next start.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File daemon\set-bundled-apk.ps1 C:\dev\agent-emu-work\apk\boltbetz-staging-4cd7f3dc.apk
param([Parameter(Mandatory)][string]$Apk, [string]$Work = "C:\dev\agent-emu-work")
$ErrorActionPreference = "Stop"
$Apk = (Resolve-Path $Apk).Path
$dir = Join-Path $Work "apk"
New-Item -ItemType Directory -Force $dir | Out-Null
$base = Join-Path $dir ([IO.Path]::GetFileNameWithoutExtension($Apk))
$n = (Get-Item $Apk).Length
$img = "$base.img"; $size = "$base.size"
if (-not (Test-Path $img)) {
  Copy-Item $Apk "$img.tmp"
  $f = [IO.File]::Open("$img.tmp", "Open"); $f.SetLength([math]::Ceiling($n / 4096) * 4096); $f.Close()
  Move-Item "$img.tmp" $img
}
[IO.File]::WriteAllText($size, "$n")
foreach ($run in Get-ChildItem $Work -Directory -Filter "run*") {
  if (-not (Test-Path "$($run.FullName)\apk.img")) { continue }
  foreach ($pair in @(@($img, "apk.img"), @($size, "apk.size"))) {
    $dst = Join-Path $run.FullName $pair[1]
    if (Test-Path $dst) { Remove-Item $dst }  # a name only; running Devices hold their own link
    New-Item -ItemType HardLink -Path $dst -Target $pair[0] | Out-Null
  }
  "$($run.Name): apk.img -> $(Split-Path $img -Leaf) ($n bytes)"
}
