$os = Get-CimInstance Win32_OperatingSystem
$avail = [int]((Get-Counter '\Memory\Available MBytes').CounterSamples[0].CookedValue)
$vm = Get-Process vmmemWSL -ErrorAction SilentlyContinue | Select-Object -First 1
$ws = if ($vm) { [int]($vm.WorkingSet64/1MB) } else { 0 }
$priv = if ($vm) { [int]($vm.PrivateMemorySize64/1MB) } else { 0 }
$cv = @(Get-Process crosvm -ErrorAction SilentlyContinue).Count
$mc = Get-Process 'Memory Compression' -ErrorAction SilentlyContinue
$mcws = if ($mc) { [int]($mc.WorkingSet64/1MB) } else { 0 }
"{0} avail={1} vmmemWSL_ws={2} vmmemWSL_priv={3} crosvm={4} memcomp={5}" -f (Get-Date -Format HH:mm:ss), $avail, $ws, $priv, $cv, $mcws
