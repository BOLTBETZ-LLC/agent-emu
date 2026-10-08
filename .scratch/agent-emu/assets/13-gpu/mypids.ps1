# PIDs of this worker's Devices only (broker command line names gpu\d4x, plus its children).
$all = Get-CimInstance Win32_Process -Filter "Name='crosvm.exe'"
$b = @($all | ? { $_.CommandLine -like "*agent-emu-work\gpu\d4*" -and $_.CommandLine -like "*run-mp*" } | % { $_.ProcessId })
$b + @($all | ? { $b -contains $_.ParentProcessId } | % { $_.ProcessId })
