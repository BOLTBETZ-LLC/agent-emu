cd C:/dev/agent-emu-work/clone-exp; L=out/dax3_poll.log; : > $L
until grep -q DONE out/slim4dax2-640.log 2>/dev/null; do sleep 30; done
echo "$(date +%T) dax2 done; polling dax3" >> $L
D=C:/dev/agent-emu-work/run-slim4dax3; t0=$(date +%s)
while ! { [ -f $D/READY ] && [ -f $D/super.img ]; }; do [ $(( $(date +%s) - t0 )) -gt 10800 ] && { echo "$(date +%T) TIMEOUT" >> $L; echo ALLDONE >> $L; exit 0; }; sleep 60; done
M=$(tr -dc '0-9' < $D/READY); echo "$(date +%T) START slim4dax3 mem=$M" >> $L
bash out/s4run.sh slim4dax3 $M slim4dax3-$M C:/dev/agent-emu-work/clone-exp/snap-slim4dax3 >> $L 2>&1
echo "$(date +%T) END" >> $L; echo ALLDONE >> $L
