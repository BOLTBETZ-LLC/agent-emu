# Stage 1 boot: crosvm on WHPX, Cuttlefish only_phone 15581820, direct kernel boot.
$W = "C:\dev\agent-emu-work"
$R = "$W\run"
Set-Location $R
if ($env:AGENT_EMU_NO_NET -eq $null) { $env:AGENT_EMU_NO_NET = "1" }
$cmdline = (Select-String -Path "$W\stage1\unpack\vendor_boot.txt" -Pattern '^vendor command line args: (.*)$').Matches[0].Groups[1].Value
$sinks = @()
foreach ($n in 4..20) { $sinks += "--serial"; $sinks += "hardware=virtio-console,num=$n,type=sink" }
$mem = if ($env:AE_MEM) { $env:AE_MEM } else { "4096" }
$extra = if ($env:AE_EXTRA) { $env:AE_EXTRA -split " " } else { @() }
# PIPE:<name> expands to a Windows named pipe path, so callers never escape backslashes.
$extra = $extra | ForEach-Object { if ($_ -like "PIPE:*") { "\\.\pipe\" + $_.Substring(5) } else { $_ } }
$pipe = '\\.\pipe\agentemu-console'
$args = @(
  "--log-level", "info", "run-mp",
  "--hypervisor", "whpx", "--disable-sandbox", "--cpus", "4", "--mem", $mem,
  "--block", "path=$R\os_composite.img",
  "--block", "path=$R\apk.img,ro=true",
  "--block", "path=$R\out.img",
  "--serial", "hardware=serial,num=1,type=sink",
  "--serial", "hardware=virtio-console,num=1,type=file,path=$R\kernel.log,console=true",
  "--serial", "hardware=virtio-console,num=2,type=namedpipe,path=$pipe",
  "--serial", "hardware=virtio-console,num=3,type=file,path=$R\logcat.log"
) + $extra + $sinks + @(
  "--gpu", "backend=2D,displays=[[mode=windowed[720,1280],dpi=[320,320],refresh-rate=60]]",
  "--initrd", $(if ($env:AE_INITRD) { "$R\$env:AE_INITRD" } else { "$R\initrd.img" }),
  "--params", $cmdline,
  $(if ($env:AE_KERNEL) { "$R\$env:AE_KERNEL" } else { "$R\kernel" })
)
"cmdline: $cmdline" | Out-File "$R\boot-args.txt"
($args -join " ") | Out-File -Append "$R\boot-args.txt"
$crosvm = if ($env:AE_CROSVM) { $env:AE_CROSVM } else { "$W\crosvm\target\release\crosvm.exe" }
& $crosvm @args *> "$R\crosvm.log"
"EXIT $LASTEXITCODE" | Out-File -Append "$R\crosvm.log"
