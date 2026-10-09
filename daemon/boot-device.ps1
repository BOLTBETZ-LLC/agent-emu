# agent-emud Device boot (owned by the daemon; other workers' cleanup scripts match boot-diet.ps1 by name).
# AE_SINKS: last sink console num (<4 = none); AE_PARAMS: extra kernel cmdline; AE_GPU_EXTRA: extra --gpu keys.
# Boot one Device: crosvm on WHPX, Cuttlefish-based guest, direct kernel boot.
# Env: AE_DIR (Device dir, default run), AE_ID (pipe suffix), AE_MEM, AE_CPUS, AE_EXTRA,
#      AE_KERNEL, AE_INITRD (file names inside AE_DIR), AE_CROSVM (binary path).
$W = if ($env:AE_WORK) { $env:AE_WORK } elseif (Test-Path "$PSScriptRoot\images") { "$PSScriptRoot\images" } else { "C:\dev\agent-emu-work" }
$R = if ($env:AE_DIR) { $env:AE_DIR } else { "$W\run" }
Set-Location $R
# Network (slirp) only when an adb host port is asked for.
if ($env:AGENT_EMU_NO_NET -eq $null -and -not $env:AGENT_EMU_ADB_PORT) { $env:AGENT_EMU_NO_NET = "1" }
$cmdline0 = (Select-String -Path "$W\stage1\unpack\vendor_boot.txt" -Pattern '^vendor command line args: (.*)$').Matches[0].Groups[1].Value
$cmdline = if ($env:AE_PARAMS) { "$cmdline0 $env:AE_PARAMS" } else { $cmdline0 }
$sinks = @()
$last = if ($env:AE_SINKS) { [int]$env:AE_SINKS } else { 20 }
foreach ($n in @(4..$last | Where-Object { $last -ge 4 })) { $sinks += "--serial"; $sinks += "hardware=virtio-console,num=$n,type=sink" }
$mem = if ($env:AE_MEM) { $env:AE_MEM } else { "4096" }
$cpus = if ($env:AE_CPUS) { $env:AE_CPUS } else { "4" }
# AE_PATH: crosvm's whole PATH (gfxstream: the SDK's ANGLE and lib64 first, never the inherited PATH).
if ($env:AE_PATH) { $env:PATH = $env:AE_PATH }
# AE_GPU_BACKEND: the --gpu backend keys (default the 2D software renderer).
$gpu = if ($env:AE_GPU_BACKEND) { $env:AE_GPU_BACKEND } else { "backend=2D" }
# AE_DISPLAY "w,h" and AE_DPI: the guest display (default the old 720x1280 at 320 dpi).
$disp = if ($env:AE_DISPLAY) { $env:AE_DISPLAY } else { "720,1280" }
$dpi = if ($env:AE_DPI) { $env:AE_DPI } else { "320" }
$hz = if ($env:AE_REFRESH) { $env:AE_REFRESH } else { "60" }
$extra = if ($env:AE_EXTRA) { $env:AE_EXTRA -split " " } else { @() }
# PIPE:<name> expands to a Windows named pipe path, so callers never escape backslashes.
$extra = $extra | ForEach-Object { if ($_ -like "PIPE:*") { "\\.\pipe\" + $_.Substring(5) } else { $_ } }
$suffix = if ($env:AE_ID) { "-" + $env:AE_ID } else { "" }
$pipe = "\\.\pipe\agentemu-console$suffix"
$initrd = if ($env:AE_INITRD) { "$R\$env:AE_INITRD" } else { "$R\initrd.img" }
$kernel = if ($env:AE_KERNEL) { "$R\$env:AE_KERNEL" } else { "$R\kernel" }
$args = @(
  "--log-level", "info", "run-mp",
  "--hypervisor", "whpx", "--disable-sandbox", "--cpus", $cpus, "--mem", $mem,
  "--block", "path=$R\os_composite.img",
  "--block", "path=$R\apk.img,ro=true",
  "--block", "path=$R\out.img"
) + $(if ($env:AE_BROWSER_IMG) { @("--block", "path=$env:AE_BROWSER_IMG,ro=true") } else { @() }) + @(
  "--serial", "hardware=serial,num=1,type=sink",
  "--serial", "hardware=virtio-console,num=1,type=file,path=$R\kernel.log,console=true",
  "--serial", "hardware=virtio-console,num=2,type=namedpipe,path=$pipe",
  "--serial", "hardware=virtio-console,num=3,type=file,path=$R\logcat.log"
) + $extra + $sinks + @(
  "--gpu", ("$gpu,displays=[[mode=windowed[$disp],dpi=[$dpi,$dpi],refresh-rate=$hz]]" + $(if ($env:AE_GPU_EXTRA) { "," + $env:AE_GPU_EXTRA } else { "" })),
  "--initrd", $initrd,
  "--params", $cmdline,
  $kernel
)
"cmdline: $cmdline" | Out-File "$R\boot-args.txt"
($args -join " ") | Out-File -Append "$R\boot-args.txt"
$crosvm = if ($env:AE_CROSVM) { $env:AE_CROSVM } else { "$W\crosvm\target\release\crosvm.exe" }
& $crosvm @args *> "$R\crosvm.log"
"EXIT $LASTEXITCODE" | Out-File -Append "$R\crosvm.log"
