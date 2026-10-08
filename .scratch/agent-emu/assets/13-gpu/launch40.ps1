# Launch a GPU-worker Device (id 40+) detached: crosvm-gpu via gpu-boot.ps1 plus a console bridge on 7100+id.
if (-not $env:AE_ID) { $env:AE_ID = "40" }
$id = [int]$env:AE_ID
$env:AE_DIR = "C:\dev\agent-emu-work\gpu\d$id"
$env:AE_MEM = if ($env:AE_MEM) { $env:AE_MEM } else { "2048" }; $env:AE_CPUS = if ($env:AE_CPUS) { $env:AE_CPUS } else { "4" }
$env:AGENT_EMU_HEADLESS = "1"; $env:AGENT_EMU_FB = "$env:AE_DIR\fb.bin"; $env:AGENT_EMU_FB_PIPE = "\\.\pipe\ae-fb-$id"
if ($env:AE_NO_NET) { $env:AGENT_EMU_NO_NET = "1"; Remove-Item Env:AGENT_EMU_ADB_PORT -ErrorAction SilentlyContinue } else { $env:AGENT_EMU_ADB_PORT = "$(6520 + $id)" }
foreach ($f in "kernel.log","logcat.log","crosvm.log","console.log","fb.bin") { Remove-Item "$env:AE_DIR\$f" -ErrorAction SilentlyContinue }
$p = Start-Process powershell -ArgumentList "-NoProfile","-ExecutionPolicy","Bypass","-File","C:\dev\agent-emu-work\gpu\gpu-boot.ps1" -WindowStyle Hidden -PassThru
Start-Sleep 2
$b = Start-Process python -ArgumentList "C:\dev\agent-emu-work\gpu\bridge2.py","\\.\pipe\agentemu-console-$id","$env:AE_DIR\console.log","$(7100 + $id)" -WindowStyle Hidden -PassThru
"boot $($p.Id) bridge $($b.Id)"
