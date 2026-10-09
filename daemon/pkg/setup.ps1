# agent-emu setup: checks this PC, installs this folder to -Root, makes shortcuts, registers the MCP server
# for Claude Code, starts the daemon. Run setup.cmd (same thing, no execution-policy prompt). No admin needed.
#   -Root <dir>      install root (default %LOCALAPPDATA%\agent-emu). Root = this folder installs in place.
#   -Port / -AgentPort   panel (HTTP) and agent (TCP) ports, default 7401 / 7400
#   -NoShortcuts -NoMcp -NoStart   skip those steps
# Then: doctor.ps1 in the root boots one phone and checks it end to end.
param(
  [string]$Root = "$env:LOCALAPPDATA\agent-emu",
  [int]$Port = 7401,
  [int]$AgentPort = 7400,
  [switch]$NoShortcuts,
  [switch]$NoMcp,
  [switch]$NoStart
)
$ErrorActionPreference = "Stop"
$Src = $PSScriptRoot
$fail = @()
function Ok($m) { Write-Host "  ok    $m" }
function Warn($m) { Write-Host "  WARN  $m" -ForegroundColor Yellow }
function Bad($m) { Write-Host "  FAIL  $m" -ForegroundColor Red; $script:fail += $m }

Write-Host "Checking this PC"
# 1. Hypervisor: crosvm runs on the Windows Hypervisor Platform API (WinHvPlatform.dll). Ask the API itself,
#    since the feature that provides it differs by Windows build.
Add-Type -Namespace AE -Name Whp -MemberDefinition @'
[DllImport("WinHvPlatform.dll")] public static extern int WHvGetCapability(int code, out int buf, uint size, out uint written);
'@
$whp = $false
try { $v = 0; $n = 0; $whp = ([AE.Whp]::WHvGetCapability(0, [ref]$v, 4, [ref]$n) -eq 0) -and ($v -ne 0) } catch { }
if ($whp) { Ok "Windows Hypervisor Platform is on" } else {
  Bad "Windows Hypervisor Platform is off. In an admin PowerShell run:  Enable-WindowsOptionalFeature -Online -FeatureName HypervisorPlatform -All   then restart Windows (and turn on CPU virtualization in the BIOS if it stays off)."
}
# 2. GPU: phones draw on the host GPU through ANGLE/Vulkan; without a real GPU driver use render "software".
$gpus = @(Get-CimInstance Win32_VideoController | Where-Object { $_.Name -notmatch 'Basic Display|Remote Display|Virtual Display|Hyper-V' })
if ($gpus) { Ok ("GPU: " + (($gpus | ForEach-Object { "$($_.Name) driver $($_.DriverVersion)" }) -join "; ")) }
else { Warn "no GPU driver found (only a basic/virtual display). Install the GPU vendor's driver, or start phones with render: software." }
if (-not (Test-Path "$env:SystemRoot\System32\vulkan-1.dll")) { Warn "no Vulkan runtime (System32\vulkan-1.dll); update the GPU driver." }
# 3. RAM: one lean phone settles near 450 MB on the host, boots need more; the daemon refuses a boot under 1.5 GB free.
$os = Get-CimInstance Win32_OperatingSystem
$totalGb = [math]::Round($os.TotalVisibleMemorySize / 1MB, 1); $availMb = [math]::Round($os.FreePhysicalMemory / 1KB)
if ($totalGb -lt 8) { Bad "RAM $totalGb GB; need 8 GB or more" } else { Ok "RAM $totalGb GB, $availMb MB free" }
if ($availMb -lt 3000) { Warn "only $availMb MB free now; close apps before booting phones (a boot wants about 3 GB free)" }
# 4. Disk: the install itself (when copied) plus about 9 GB per phone (8 GB userdata disk each).
$inPlace = (Resolve-Path $Src).Path.TrimEnd('\') -ieq [IO.Path]::GetFullPath($Root).TrimEnd('\')
$payload = if ($inPlace) { 0 } else { (Get-ChildItem -Recurse -File $Src | Measure-Object Length -Sum).Sum }
$drive = [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($Root))
$free = (Get-PSDrive -Name $drive.Substring(0, 1)).Free
$need = $payload + 20GB
if ($free -lt $need) { Bad ("disk {0}: {1:N0} GB free, need {2:N0} GB (install + 2 phones)" -f $drive, ($free / 1GB), ($need / 1GB)) }
else { Ok ("disk {0}: {1:N0} GB free" -f $drive, ($free / 1GB)) }
# 5. Ports.
foreach ($p in $Port, $AgentPort) {
  if (Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue) { Warn "port $p is already in use (another agent-emu? pass -Port/-AgentPort)" }
}
if ($fail) { Write-Host "`nSetup stopped: fix the FAIL lines above, then run setup again." -ForegroundColor Red; exit 1 }

Write-Host "Installing to $Root"
if (-not $inPlace) {
  robocopy $Src $Root /E /NFL /NDL /NJH /NJS /NP | Out-Null
  if ($LASTEXITCODE -ge 8) { throw "copy to $Root failed (robocopy $LASTEXITCODE)" }
}
New-Item -ItemType Directory -Force "$Root\logs" | Out-Null
Set-Content -Encoding ASCII "$Root\config.cmd" @(
  "@rem Written by setup.ps1.",
  "set ""AE_PORT=$Port""",
  "set ""AE_AGENT_PORT=$AgentPort""",
  "set ""LOGDIR=$Root\logs"""
)
Ok "files and config.cmd"

if (-not $NoShortcuts) {
  $sh = New-Object -ComObject WScript.Shell
  foreach ($dir in [Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs")) {
    $lnk = $sh.CreateShortcut((Join-Path $dir "agent-emu.lnk"))
    $lnk.TargetPath = "$Root\agent-emu.cmd"; $lnk.WorkingDirectory = $Root; $lnk.WindowStyle = 7
    $lnk.IconLocation = "$env:SystemRoot\System32\shell32.dll,15"; $lnk.Description = "agent-emu control panel"
    $lnk.Save()
  }
  Ok "Desktop and Start menu shortcuts"
}

if (-not $NoMcp) {
  $claude = Get-Command claude -ErrorAction SilentlyContinue
  if ($claude) {
    & claude mcp remove agent-emu -s user 2>$null | Out-Null
    & claude mcp add -s user agent-emu -- "$Root\agent-emud.exe" mcp --addr "127.0.0.1:$AgentPort"
    if ($LASTEXITCODE -eq 0) { Ok "MCP server agent-emu registered for Claude Code (user scope)" } else { Warn "claude mcp add failed ($LASTEXITCODE)" }
  } else { Write-Host "  skip  Claude Code not found; MCP server not registered" }
}

if (-not $NoStart) {
  cmd /c """$Root\agent-emu.cmd"" --no-open < NUL"
  try { $h = Invoke-RestMethod -TimeoutSec 5 "http://127.0.0.1:$Port/health" } catch { $h = $null }
  if ("$h".Trim() -eq "ok") { Ok "daemon answers on http://127.0.0.1:$Port/" } else { Bad "daemon did not answer; see $Root\logs\agent-emud.log"; exit 1 }
}
Write-Host "`nDone. Open the panel with the agent-emu shortcut. Check a phone end to end: powershell -ExecutionPolicy Bypass -File ""$Root\doctor.ps1"" -Port $Port"
