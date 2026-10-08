# Stop only Lever D Device <Id> (crosvm processes whose broker carries ae-vm-<Id>) and its console bridge.
param([string]$Id = "30")
$all = Get-CimInstance Win32_Process -Filter "Name='crosvm.exe'"
$b = $all | ? { $_.CommandLine -like "*\pipe\ae-vm-$Id *" -and $_.CommandLine -like "* run-mp *" }
foreach ($x in $b) { $all | ? { $_.ParentProcessId -eq $x.ProcessId } | % { Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue }; Stop-Process -Id $x.ProcessId -Force -EA SilentlyContinue; "stopped $($x.ProcessId)" }
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | ? { $_.CommandLine -like "*console_bridge.ps1*-Id $Id *" } | % { Stop-Process -Id $_.ProcessId -Force; "bridge $($_.ProcessId)" }
