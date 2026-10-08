# JSON stats of this worker's Device (crosvm-gpu processes): CPU seconds, working set, private bytes, GPU memory.
$mine = & "$PSScriptRoot\mypids.ps1"
$ps = @(Get-Process -Id $mine -ErrorAction SilentlyContinue)
$ids = $ps | % { $_.Id }
$gpu = @{ dedicated = 0; shared = 0 }
try {
  $c = Get-Counter "\GPU Process Memory(*)\Dedicated Usage", "\GPU Process Memory(*)\Shared Usage" -ErrorAction Stop
  foreach ($s in $c.CounterSamples) {
    if ($s.InstanceName -match "^pid_(\d+)_" -and $ids -contains [int]$Matches[1]) {
      if ($s.Path -like "*dedicated*") { $gpu.dedicated += $s.CookedValue } else { $gpu.shared += $s.CookedValue }
    }
  }
} catch {}
$main = $ps | Sort-Object WorkingSet64 -Descending | Select-Object -First 1
[pscustomobject]@{
  t = [DateTimeOffset]::Now.ToUnixTimeMilliseconds() / 1000.0
  n = $ps.Count
  cpu_s_sum = [double](($ps | % { $_.TotalProcessorTime.TotalSeconds }) | Measure-Object -Sum).Sum
  ws_mb = [math]::Round((($ps | Measure-Object WorkingSet64 -Sum).Sum) / 1MB, 1)
  private_mb = [math]::Round((($ps | Measure-Object PrivateMemorySize64 -Sum).Sum) / 1MB, 1)
  main_ws_mb = [math]::Round($main.WorkingSet64 / 1MB, 1)
  main_private_mb = [math]::Round($main.PrivateMemorySize64 / 1MB, 1)
  gpu_dedicated_mb = [math]::Round($gpu.dedicated / 1MB, 1)
  gpu_shared_mb = [math]::Round($gpu.shared / 1MB, 1)
  host_avail_mb = [int]((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1024)
} | Select-Object t, n, cpu_s_sum, ws_mb, private_mb, main_ws_mb, main_private_mb, gpu_dedicated_mb, gpu_shared_mb, host_avail_mb | ConvertTo-Json -Compress
