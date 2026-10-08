# One snapshot of host memory accounting for the ~260 MB/clone hunt. Prints JSON.
$names = '\Memory\Available MBytes', '\Memory\Committed Bytes', '\Memory\Pool Nonpaged Bytes', '\Memory\Pool Paged Bytes',
  '\Memory\System Driver Resident Bytes', '\Memory\Modified Page List Bytes', '\Memory\Standby Cache Reserve Bytes',
  '\Memory\Standby Cache Normal Priority Bytes', '\Memory\Standby Cache Core Bytes', '\Memory\Free & Zero Page List Bytes',
  '\Hyper-V Hypervisor\Total Pages', '\Hyper-V Hypervisor\Partitions', '\Hyper-V Hypervisor Root Partition(_total)\Deposited Pages'
$o = [ordered]@{}
foreach ($n in $names) { try { $o[$n] = [int64](Get-Counter $n -EA Stop).CounterSamples[0].CookedValue } catch { $o[$n] = $null } }
foreach ($n in '\Hyper-V Hypervisor Partition(*)\Deposited Pages', '\Hyper-V Hypervisor Partition(*)\GPA Pages', '\Hyper-V Hypervisor Partition(*)\Virtual TLB Pages',
               '\Hyper-V Hypervisor Partition(*)\4K GPA pages', '\Hyper-V Hypervisor Partition(*)\2M GPA pages',
               '\Hyper-V VM Vid Partition(*)\Physical Pages Allocated', '\Hyper-V VM Vid Partition(*)\Remote Physical Pages') {
  try { $o[$n] = @((Get-Counter $n -EA Stop).CounterSamples | ForEach-Object { @{ i = $_.InstanceName; v = [int64]$_.CookedValue } }) } catch { $o[$n] = $null }
}
$o['proc_ws_mb'] = @(Get-Process 'Memory Compression', 'Secure System', 'Registry', 'System', 'vmmem*', 'vmcompute', 'vmwp' -EA SilentlyContinue |
  ForEach-Object { @{ n = $_.Name; ws = [int]($_.WorkingSet64 / 1MB); priv = [int]($_.PrivateMemorySize64 / 1MB) } })
$all = Get-CimInstance Win32_Process -Filter "Name='crosvm.exe'"
$mine = @($all | Where-Object { $_.CommandLine -match 'crosvm-clone' })
$perf = Get-CimInstance Win32_PerfFormattedData_PerfProc_Process | Where-Object { $_.Name -like 'crosvm*' }
$ids = $mine | ForEach-Object { $_.ProcessId }
$o['mine_procs'] = $mine.Count; $o['other_crosvm'] = ($all.Count - $mine.Count)
$o['mine_ws_mb'] = [int](($perf | Where-Object { $ids -contains $_.IDProcess } | Measure-Object WorkingSet -Sum).Sum / 1MB)
$o['mine_private_ws_mb'] = [int](($perf | Where-Object { $ids -contains $_.IDProcess } | Measure-Object WorkingSetPrivate -Sum).Sum / 1MB)
$o['mine_pagefile_bytes_mb'] = [int](($perf | Where-Object { $ids -contains $_.IDProcess } | Measure-Object PageFileBytes -Sum).Sum / 1MB)
$o | ConvertTo-Json -Depth 5 -Compress
