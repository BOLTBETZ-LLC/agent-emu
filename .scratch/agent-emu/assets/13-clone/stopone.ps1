param([int]$Id)
function S { $c=(Get-Counter '\Memory\Available Bytes','\Memory\Free & Zero Page List Bytes','\Memory\Modified Page List Bytes','\Memory\Pool Nonpaged Bytes','\Memory\Committed Bytes','\Hyper-V Hypervisor\Total Pages').CounterSamples; '{0} avail={1} free={2} mod={3} npp={4} commit={5} hvpages={6}' -f (Get-Date -f HH:mm:ss.fff),[int]($c[0].CookedValue/1MB),[int]($c[1].CookedValue/1MB),[int]($c[2].CookedValue/1MB),[int]($c[3].CookedValue/1MB),[int]($c[4].CookedValue/1MB),[int]$c[5].CookedValue }
$all = Get-CimInstance Win32_Process -Filter "Name='crosvm.exe'"
$b = $all | Where-Object { $_.CommandLine -match "ae-vm-$Id " }
$pids = @($b.ProcessId) + @($all | Where-Object { $_.ParentProcessId -eq $b.ProcessId } | ForEach-Object { $_.ProcessId })
$perf = Get-CimInstance Win32_PerfFormattedData_PerfProc_Process | Where-Object { $pids -contains $_.IDProcess }
'clone d{0}: ws={1} private_ws={2} pagefile_bytes={3} pool_np_bytes={4} pool_p_bytes={5} other_crosvm={6}' -f $Id, [int](($perf|Measure WorkingSet -Sum).Sum/1MB), [int](($perf|Measure WorkingSetPrivate -Sum).Sum/1MB), [int](($perf|Measure PageFileBytes -Sum).Sum/1MB), [int](($perf|Measure PoolNonpagedBytes -Sum).Sum/1MB), [int](($perf|Measure PoolPagedBytes -Sum).Sum/1MB), @($all | Where-Object { $_.CommandLine -notmatch 'crosvm-clone' }).Count
1..4 | ForEach-Object { S }
foreach ($p in $pids) { Stop-Process -Id $p -Force -EA SilentlyContinue }
Start-Sleep -Milliseconds 1500
1..6 | ForEach-Object { S }
