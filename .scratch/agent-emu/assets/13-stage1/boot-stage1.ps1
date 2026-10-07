# Stage 1 boot: crosvm on WHPX, Cuttlefish only_phone 15581820, direct kernel boot.
$W = "C:\dev\agent-emu-work"
$R = "$W\run"
Set-Location $R
if ($env:AGENT_EMU_NO_NET -eq $null) { $env:AGENT_EMU_NO_NET = "1" }
$cmdline = (Select-String -Path "$W\stage1\unpack\vendor_boot.txt" -Pattern '^vendor command line args: (.*)$').Matches[0].Groups[1].Value
$sinks = @()
foreach ($n in 4..20) { $sinks += "--serial"; $sinks += "hardware=virtio-console,num=$n,type=sink" }
$pipe = '\\.\pipe\agentemu-console'
$args = @(
  "--log-level", "info", "run-mp",
  "--hypervisor", "whpx", "--disable-sandbox", "--cpus", "4", "--mem", "4096",
  "--block", "path=$R\os_composite.img",
  "--block", "path=$R\apk.img,ro=true",
  "--block", "path=$R\out.img",
  "--serial", "hardware=serial,num=1,type=sink",
  "--serial", "hardware=virtio-console,num=1,type=file,path=$R\kernel.log,console=true",
  "--serial", "hardware=virtio-console,num=2,type=namedpipe,path=$pipe",
  "--serial", "hardware=virtio-console,num=3,type=file,path=$R\logcat.log"
) + $sinks + @(
  "--gpu", "backend=2D,displays=[[mode=windowed[720,1280],dpi=[320,320],refresh-rate=60]]",
  "--initrd", "$R\initrd.img",
  "--params", $cmdline,
  "$R\kernel"
)
"cmdline: $cmdline" | Out-File "$R\boot-args.txt"
($args -join " ") | Out-File -Append "$R\boot-args.txt"
& "$W\crosvm\target\release\crosvm.exe" @args *> "$R\crosvm.log"
"EXIT $LASTEXITCODE" | Out-File -Append "$R\crosvm.log"
