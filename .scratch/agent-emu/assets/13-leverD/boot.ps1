# Lever D: boot one Device from a run dir with cmdline.txt (no bootconfig). Env: AE_DIR, AE_ID, AE_MEM, AE_CPUS, AE_EXTRA.
$W = "C:\dev\agent-emu-work"; $R = $env:AE_DIR; Set-Location $R
$env:AGENT_EMU_NO_NET = "1"; $env:AGENT_EMU_HEADLESS = "1"
$P = [string][char]92 + [char]92 + ".\pipe\"
$cmdline = (Get-Content "$R\cmdline.txt" -Raw).Trim()
$sinks = @(); foreach ($n in 4..20) { $sinks += "--serial"; $sinks += "hardware=virtio-console,num=$n,type=sink" }
$extra = if ($env:AE_EXTRA) { $env:AE_EXTRA -split " " } else { @() }
$extra = $extra | ForEach-Object { if ($_ -like "PIPE:*") { $P + $_.Substring(5) } else { $_ } }
$args = @("--log-level", "info", "run-mp", "--hypervisor", "whpx", "--disable-sandbox", "--cpus", $env:AE_CPUS, "--mem", $env:AE_MEM,
  "--block", "path=$R\os_composite.img", "--block", "path=$R\apk.img,ro=true", "--block", "path=$R\out.img",
  "--serial", "hardware=serial,num=1,type=sink",
  "--serial", "hardware=virtio-console,num=1,type=file,path=$R\kernel.log,console=true",
  "--serial", "hardware=virtio-console,num=2,type=namedpipe,path=${P}agentemu-console-$($env:AE_ID)",
  "--serial", "hardware=virtio-console,num=3,type=file,path=$R\logcat.log",
  "--socket", "${P}ae-vm-$($env:AE_ID)") + $extra + $sinks + @(
  "--gpu", "backend=2D,displays=[[mode=windowed[720,1280],dpi=[320,320],refresh-rate=60]]",
  "--initrd", "$R\initrd.img", "--params", $cmdline, "$R\kernel")
($args -join " ") | Out-File "$R\boot-args.txt"
& "$W\crosvm-pmem\target\release\crosvm.exe" @args *> "$R\crosvm.log"
"EXIT $LASTEXITCODE" | Out-File -Append "$R\crosvm.log"
