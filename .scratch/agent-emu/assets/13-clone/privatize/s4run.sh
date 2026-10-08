# usage: bash s4run.sh <profile> <mem_mb> <tag> <snapdir>
PROF=$1; MEM=$2; TAG=$3; SNAP=$4
cd C:/dev/agent-emu-work/clone-exp; export AE_PROFILE=$PROF AE_MEM_MB=$MEM; unset AE_MORE_ARGS AE_MORE_PARAMS AE_SWAPOFF AE_SWAPOFF_EARLY AGENT_EMU_COW_PAGEDUMP_AT
L=out/$TAG.log; : > $L
a=$(powershell -NoProfile -Command "[int](Get-Counter '\Memory\Available MBytes').CounterSamples[0].CookedValue"); echo "avail $a" >> $L
timeout 900 python clone_exp.py template 12 > out/$TAG-template.log 2>&1; grep -E "ready_s|install|launch|app_pid|ABORT|timeout" out/$TAG-template.log >> $L
AE_PORT=7112 timeout 30 python ../gsh.py "grep -E 'SwapTotal|MemFree|MemAvailable' /proc/meminfo; pidof com.boltbetz.staging" 20 | grep -vE "AE_|console" >> $L
timeout 300 python clone_exp.py check 12 $TAG-template >/dev/null 2>&1
timeout 600 python clone_exp.py snap 12 $SNAP | grep -A1 '"snapshot"' | tr -d '\n' >> $L; echo >> $L
for i in 13 14 15; do r=$(AGENT_EMU_COW_DUMP="C:/dev/agent-emu-work/clone-exp/out/$TAG-d$i" timeout 200 python clone_exp.py restore $i $SNAP --cow 2>&1 | grep -E '"restore_s"|"console_s"|ABORT' | tr -d '\n '); echo "d$i $r" >> $L; done
python drive.py $TAG --ids 13,14,15 --minutes 10 --relaunch-at 5 --tap-every 20 > out/$TAG-drive.log 2>&1; echo "DRIVE EXIT $?" >> $L
python latency.py $TAG --ids 13,14,15 --n 20 --warmup 2 > out/$TAG-latency.log 2>&1; echo "LATENCY EXIT $?" >> $L
powershell -NoProfile -ExecutionPolicy Bypass -File stopseq.ps1 -Ids "15,14,13" -Out out/$TAG-stopseq.json >> $L 2>&1; echo "STOPSEQ EXIT $?" >> $L
for i in 12 13 14 15; do python clone_exp.py stop $i >/dev/null 2>&1; done; echo DONE >> $L
