$n = @(Get-CimInstance Win32_Process -Filter "Name='crosvm.exe'" | Where-Object { $_.CommandLine -match 'ae-vm-(1[2-9]|2[0-2]) ' }).Count
$av = [int](Get-Counter '\Memory\Available MBytes').CounterSamples[0].CookedValue
"$n $av"
