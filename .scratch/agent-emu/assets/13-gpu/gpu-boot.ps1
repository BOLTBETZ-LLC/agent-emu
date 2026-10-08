# GPU worker: boot one Device on crosvm-gpu (WHPX + gfxstream from the Android emulator's libgfxstream_backend.dll).
# Env: AE_ID (40+), AE_DIR, AE_MEM, AE_CPUS, AE_DISPLAY "w,h", AE_DPI, AE_GPU (the --gpu backend part), AE_INITRD.
$W = "C:\dev\agent-emu-work"
$R = $env:AE_DIR
Set-Location $R
$E = "$env:LOCALAPPDATA\Android\Sdk\emulator"
# gles_angle first: gfxstream loads libEGL/libGLESv2 by name, and WezTerm's old ANGLE on PATH crashed it.
$env:PATH = "$E\lib64\gles_angle;$E\lib64;$E;" + (($env:PATH -split ";" | Where-Object { $_ -notlike "*WezTerm*" }) -join ";")
$cmdline = (Select-String -Path "$W\stage1\unpack\vendor_boot.txt" -Pattern '^vendor command line args: (.*)$').Matches[0].Groups[1].Value
$cmdline = "$cmdline $env:AE_PARAMS virtio_blk.num_request_queues=1 virtio_blk.queue_depth=64 transparent_hugepage=never kfence.sample_interval=0"
$sinks = @(); foreach ($n in 4..11) { $sinks += "--serial"; $sinks += "hardware=virtio-console,num=$n,type=sink" }
$id = $env:AE_ID
$disp = if ($env:AE_DISPLAY) { $env:AE_DISPLAY } else { "1320,2868" }
$dpi = if ($env:AE_DPI) { $env:AE_DPI } else { "480" }
$gpu = if ($env:AE_GPU) { $env:AE_GPU } else { "backend=gfxstream,context-types=gfxstream-gles:gfxstream-vulkan:gfxstream-composer,egl=true,gles=true,glx=false,surfaceless=true,vulkan=true" }
$initrd = if ($env:AE_INITRD) { "$R\$env:AE_INITRD" } else { "$R\initrd-gfx.img" }
$args = @(
  "--log-level", "info", "run-mp",
  "--hypervisor", "whpx", "--disable-sandbox", "--cpus", $env:AE_CPUS, "--mem", $env:AE_MEM,
  "--block", "path=$R\os_composite.img",
  "--block", "path=$R\apk.img,ro=true",
  "--block", "path=$R\out.img",
  "--serial", "hardware=serial,num=1,type=sink",
  "--serial", "hardware=virtio-console,num=1,type=file,path=$R\kernel.log,console=true",
  "--serial", "hardware=virtio-console,num=2,type=namedpipe,path=\\.\pipe\agentemu-console-$id",
  "--serial", "hardware=virtio-console,num=3,type=file,path=$R\logcat.log",
  "--socket", "\\.\pipe\ae-vm-$id",
  "--input", "multi-touch[path=\\.\pipe\ae-touch-$id]",
  "--input", "keyboard[path=\\.\pipe\ae-kbd-$id]"
) + $(if ($env:AE_PMEM) { @("--pmem", "path=$R\$env:AE_PMEM,ro=true") } else { @() }) + $sinks + @(
  "--gpu", "$gpu,displays=[[mode=windowed[$disp],dpi=[$dpi,$dpi],refresh-rate=60]],audio-device-mode=one-global",
  "--initrd", $initrd,
  "--params", $cmdline,
  "$R\$(if ($env:AE_KERNEL) { $env:AE_KERNEL } else { "kernel" })"
)
($args -join " ") | Out-File "$R\boot-args.txt"
& "$W\crosvm-gpu\target\release\crosvm.exe" @args *> "$R\crosvm.log"
"EXIT $LASTEXITCODE" | Out-File -Append "$R\crosvm.log"
