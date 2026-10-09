# agent-emu doctor: proves an install works end to end. The daemon answers, one phone boots to phase "ready",
# the bundled BoltBetz app launches, a screenshot is not black, ui_tree returns nodes. Then it stops that phone.
#   -Port 7401  panel port of the daemon to check     -Device d9  phone id to use (must not be running)
#   -Image slim5 -Screen iphone17promax-half           -Keep  leave the phone running
# Exit 0 = PASS. The screenshot is saved next to this script in logs\doctor-<device>.png.
param(
  [int]$Port = 7401,
  [string]$Device = "d9",
  [string]$Image = "slim5",
  [string]$Screen = "iphone17promax-half",
  [switch]$Keep
)
$ErrorActionPreference = "Stop"
$api = "http://127.0.0.1:$Port"
$t0 = Get-Date
function Say($m) { Write-Host ("[{0,5:N0}s] {1}" -f ((Get-Date) - $t0).TotalSeconds, $m) }
function Fail($m) { Say "FAIL $m"; exit 1 }
function Call($body, [int]$timeout = 60) {
  $r = Invoke-RestMethod -Method Post -Uri "$api/api" -ContentType "application/json" -TimeoutSec $timeout -Body ($body | ConvertTo-Json -Compress)
  if (-not $r.ok) { Fail "$($body.call): $($r.error)" }
  $r
}

try { $h = Invoke-RestMethod -TimeoutSec 5 "$api/health" } catch { Fail "daemon does not answer on $api ($_)" }
if ("$h".Trim() -ne "ok") { Fail "health said: $h" }
Say "ok   daemon answers on $api"

$st = Call @{ call = "status" }
if ($st.devices | Where-Object { $_.id -eq $Device }) { Fail "$Device is already running; pick a free id with -Device" }
Say "ok   host available $($st.available_mb) MB"

Say "...  starting $Device ($Image, $Screen); a fresh boot takes 2-5 min"
$null = Call @{ call = "start"; device = $Device; image = $Image; screen = $Screen; async = $true }
$deadline = (Get-Date).AddMinutes(10); $phase = ""
while ($true) {
  Start-Sleep 10
  $d = (Call @{ call = "status" }).devices | Where-Object { $_.id -eq $Device }
  if (-not $d) { Fail "$Device went away while booting; see the daemon log and images\fleet\$Device\crosvm.log" }
  if ($d.phase -ne $phase) { $phase = $d.phase; Say "     phase $phase" }
  if ($phase -eq "ready") { break }
  if ($phase -eq "stopped") { Fail "$Device stopped during boot; see images\fleet\$Device\crosvm.log" }
  if ((Get-Date) -gt $deadline) { Fail "$Device not ready after 10 min (phase $phase)" }
}
Say "ok   $Device ready"

$null = Call @{ call = "install_bundled"; device = $Device } 300
$null = Call @{ call = "app"; device = $Device; launch = "com.boltbetz.staging" } 120
Start-Sleep 15
Say "ok   bundled app installed and launched"

$png = Join-Path $PSScriptRoot "logs\doctor-$Device.png"
New-Item -ItemType Directory -Force (Split-Path $png) | Out-Null
Invoke-WebRequest -UseBasicParsing -TimeoutSec 60 "$api/screenshot.png?device=$Device" -OutFile $png
Add-Type -AssemblyName System.Drawing
$bmp = [System.Drawing.Bitmap]::FromFile($png)
$lit = 0; $n = 0
for ($x = 0; $x -lt $bmp.Width; $x += [math]::Max(1, [int]($bmp.Width / 40))) {
  for ($y = 0; $y -lt $bmp.Height; $y += [math]::Max(1, [int]($bmp.Height / 80))) {
    $c = $bmp.GetPixel($x, $y); $n++
    if (($c.R + $c.G + $c.B) -gt 60) { $lit++ }
  }
}
$size = "$($bmp.Width)x$($bmp.Height)"; $bmp.Dispose()
$pct = [math]::Round(100 * $lit / $n, 1)
if ($pct -lt 2) { Fail "screenshot $size is black ($pct% lit): $png" }
Say "ok   screenshot $size, $pct% of sampled pixels lit: $png"

$xml = (Call @{ call = "ui_tree"; device = $Device }).xml
$nodes = ([regex]::Matches($xml, "<node")).Count
if ($nodes -lt 1) { Fail "ui_tree returned no nodes" }
Say "ok   ui_tree $nodes nodes"

if (-not $Keep) { $null = Call @{ call = "stop"; device = $Device } 120; Say "ok   $Device stopped" }
Say "PASS"
