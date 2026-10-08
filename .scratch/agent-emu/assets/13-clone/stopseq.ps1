# Stop clones one at a time (ids in the given order), 20 s apart, sampling host memory every second.
# Per stop: the clone's private WS just before, Available 3 s before vs 3 s after, hypervisor pages,
# standby/modified lists, and whether any other crosvm process started or stopped in the window (noisy).
param([string]$Ids = "22,21,20,19,18,17,16,15,14,13", [string]$Out = "stopseq.json")
function Snap {
  $c = (Get-Counter '\Memory\Available Bytes', '\Hyper-V Hypervisor\Total Pages', '\Memory\Modified Page List Bytes',
    '\Memory\Standby Cache Normal Priority Bytes', '\Memory\Standby Cache Reserve Bytes', '\Memory\Standby Cache Core Bytes',
    '\Memory\Free & Zero Page List Bytes').CounterSamples
  $others = @(Get-CimInstance Win32_Process -Filter "Name='crosvm.exe'" | Where-Object { $_.CommandLine -notmatch 'crosvm-clone' } | ForEach-Object { $_.ProcessId }) | Sort-Object
  [ordered]@{ t = (Get-Date -f HH:mm:ss.fff); avail = [int]($c[0].CookedValue / 1MB); hv = [int]$c[1].CookedValue; mod = [int]($c[2].CookedValue / 1MB);
    standby = [int](($c[3].CookedValue + $c[4].CookedValue + $c[5].CookedValue) / 1MB); free = [int]($c[6].CookedValue / 1MB); others = ($others -join ',') }
}
$res = @()
foreach ($id in $Ids.Split(',')) {
  $all = Get-CimInstance Win32_Process -Filter "Name='crosvm.exe'"
  $b = $all | Where-Object { $_.CommandLine -match "ae-vm-$id " }
  if (-not $b) { $res += [ordered]@{ id = $id; error = 'not running' }; continue }
  $pids = @($b.ProcessId) + @($all | Where-Object { $_.ParentProcessId -eq $b.ProcessId } | ForEach-Object { $_.ProcessId })
  $perf = Get-CimInstance Win32_PerfFormattedData_PerfProc_Process | Where-Object { $pids -contains $_.IDProcess }
  $priv = [int](($perf | Measure-Object WorkingSetPrivate -Sum).Sum / 1MB); $ws = [int](($perf | Measure-Object WorkingSet -Sum).Sum / 1MB)
  $before = @(1..3 | ForEach-Object { Snap; Start-Sleep -Milliseconds 700 })
  foreach ($p in $pids) { Stop-Process -Id $p -Force -EA SilentlyContinue }
  $after = @(1..6 | ForEach-Object { Start-Sleep -Milliseconds 700; Snap })
  $a0 = ($before | ForEach-Object { $_.avail } | Measure-Object -Average).Average; $a1 = ($after[1..3] | ForEach-Object { $_.avail } | Measure-Object -Average).Average
  $noisy = (@($before + $after | ForEach-Object { $_.others } | Sort-Object -Unique)).Count -gt 1
  $res += [ordered]@{ id = $id; private_ws_mb = $priv; ws_mb = $ws; avail_before = [int]$a0; avail_after = [int]$a1; freed_mb = [int]($a1 - $a0);
    hv_pages_delta = $after[-1].hv - $before[-1].hv; standby_delta_mb = $after[3].standby - $before[-1].standby; mod_delta_mb = $after[3].mod - $before[-1].mod;
    noisy = $noisy; before = $before; after = $after }
  "d{0}: private={1} ws={2} freed={3} hv={4} standby_d={5} noisy={6}" -f $id, $priv, $ws, [int]($a1 - $a0), ($after[-1].hv - $before[-1].hv), ($after[3].standby - $before[-1].standby), $noisy | Out-Host
  Start-Sleep -Seconds 14
}
$res | ConvertTo-Json -Depth 5 | Out-File -Encoding utf8 $Out
