cd C:/dev/agent-emu-work/clone-exp; export AE_PROFILE=slim5; unset AE_MEM_MB; L=out/s576.log; : > $L
a=$(powershell -NoProfile -Command "[int](Get-Counter '\Memory\Available MBytes').CounterSamples[0].CookedValue"); echo "avail $a" >> $L
for i in 13 14 15; do r=$(timeout 200 python clone_exp.py restore $i C:/dev/agent-emu-work/clone-exp/snapF --cow 2>&1 | grep -E '"restore_s"|"console_s"|ABORT' | tr -d '\n '); echo "d$i $r" >> $L
  AE_PORT=71$i timeout 30 python ../gsh.py 'su 0 sh -c "setprop log.tag.RIL S; for t in \$(ls /proc/\$(pidof libcuttlefish-rild)/task); do renice -n 19 -p \$t; done; ps -T -o NI= -p \$(pidof libcuttlefish-rild) | sort | uniq -c"' 20 | grep -E "^ +[0-9]+ +-?[0-9]+$" >> $L; done
python drive.py s576 --ids 13,14,15 --minutes 10 --relaunch-at 5 --tap-every 20 > out/s576-drive.log 2>&1; echo "DRIVE EXIT $?" >> $L
python latency.py s576 --ids 13,14,15 --n 20 --warmup 2 > out/s576-latency.log 2>&1; echo "LATENCY EXIT $?" >> $L
powershell -NoProfile -ExecutionPolicy Bypass -File stopseq.ps1 -Ids "15,14,13" -Out out/s576-stopseq.json >> $L 2>&1; echo "STOPSEQ EXIT $?" >> $L
for i in 13 14 15; do python clone_exp.py stop $i >/dev/null 2>&1; done; echo DONE >> $L
