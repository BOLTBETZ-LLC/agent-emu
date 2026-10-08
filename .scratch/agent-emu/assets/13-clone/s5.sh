cd C:/dev/agent-emu-work/clone-exp; export AE_PROFILE=slim5 AE_MEM_MB=640; L=out/s5.log; : > $L
while true; do
  o=$(powershell -NoProfile -Command "@(Get-CimInstance Win32_Process -Filter \"Name='crosvm.exe'\" | Where-Object { \$_.CommandLine -match 'ae-vm-\d+ ' -and \$_.CommandLine -notmatch 'ae-vm-0 ' -and \$_.CommandLine -notmatch 'crosvm-clone' }).Count")
  a=$(powershell -NoProfile -Command "[int](Get-Counter '\Memory\Available MBytes').CounterSamples[0].CookedValue")
  echo "$(date +%T) gate others=$o avail=$a" >> $L; [ "$o" -eq 0 ] && [ "$a" -gt 9000 ] && break; sleep 60
done
timeout 900 python clone_exp.py template 12 > out/template12h.log 2>&1; grep -E "ready_s|install|launch|app_pid|ABORT|Traceback" out/template12h.log >> $L
AE_PORT=7112 timeout 30 python ../gsh.py "echo RIL_NICE; ps -T -o NI= -p \$(pidof libcuttlefish-rild) | sort | uniq -c" 20 | grep -A5 RIL_NICE >> $L
timeout 300 python clone_exp.py check 12 templateH-d12 > /dev/null 2>&1
timeout 600 python clone_exp.py snap 12 C:/dev/agent-emu-work/clone-exp/snapH | grep -A1 '"snapshot"' >> $L
powershell -NoProfile -ExecutionPolicy Bypass -File attrib.ps1 > out/s5-base.json
for i in 13 14 15; do r=$(timeout 200 python clone_exp.py restore $i C:/dev/agent-emu-work/clone-exp/snapH --cow 2>&1 | grep -E '"restore_s"|"console_s"|ABORT' | tr -d '\n '); echo "d$i $r" >> $L; done
python drive.py s5-plateau --ids 13,14,15 --minutes 10 --relaunch-at 5 --tap-every 20 > out/s5-plateau.log 2>&1; echo "PLATEAU EXIT $?" >> $L
python latency.py s5 --ids 13,14,15 --n 20 --warmup 2 > out/s5-latency.log 2>&1; echo "LATENCY EXIT $?" >> $L
powershell -NoProfile -ExecutionPolicy Bypass -File attrib.ps1 > out/s5-prestop.json
powershell -NoProfile -ExecutionPolicy Bypass -File stopseq.ps1 -Ids "15,14,13" -Out out/s5-stopseq.json >> $L 2>&1; echo "STOPSEQ EXIT $?" >> $L
for i in 13 14 15; do python clone_exp.py stop $i >/dev/null 2>&1; done; echo DONE >> $L
