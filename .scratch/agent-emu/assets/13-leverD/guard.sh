#!/bin/sh
# Every 30 s: host Available; under 3000 MB stop Lever D Device $1. Exits when its crosvm is gone.
id=$1
while :; do
  av=$(powershell -NoProfile -Command "[int](Get-Counter '\Memory\Available MBytes').CounterSamples[0].CookedValue")
  n=$(powershell -NoProfile -Command "@(Get-CimInstance Win32_Process -Filter \"Name='crosvm.exe'\" | ? { \$_.CommandLine -like '*ae-vm-$id *' }).Count")
  echo "$(date +%T) avail=$av crosvm=$n"
  if [ "$n" != "0" ]; then seen=1; fi
  if [ "$n" = "0" ] && [ -n "$seen" ]; then echo gone; exit 0; fi
  if [ "$av" -lt 3000 ]; then powershell -NoProfile -ExecutionPolicy Bypass -File C:/dev/agent-emu-work/leverD/tools/stop.ps1 -Id $id; echo STOPPED_LOWMEM; exit 1; fi
  sleep 30
done
