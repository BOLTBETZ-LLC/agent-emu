cd C:/dev/agent-emu-work/clone-exp; L=out/dax_poll.log; : > $L; t0=$(date +%s); done_list=""
while true; do
  best=""; bestmem=99999
  for p in slim4dax slim4nsdax; do
    case " $done_list " in *" $p "*) continue;; esac
    D=C:/dev/agent-emu-work/run-$p
    if [ -f $D/READY ] && [ -f $D/super.img ]; then m=$(tr -dc '0-9' < $D/READY); [ -n "$m" ] && [ "$m" -lt $bestmem ] && { best=$p; bestmem=$m; }; fi
  done
  if [ -n "$best" ]; then
    echo "$(date +%T) START $best mem=$bestmem" >> $L
    bash out/s4run.sh $best $bestmem ${best}$bestmem C:/dev/agent-emu-work/clone-exp/snap-$best >> $L 2>&1
    echo "$(date +%T) END $best" >> $L; done_list="$done_list $best"
    [ $(echo $done_list | wc -w) -ge 2 ] && break
    continue
  fi
  [ $(( $(date +%s) - t0 )) -gt 10800 ] && { echo "$(date +%T) TIMEOUT done=[$done_list]" >> $L; break; }
  sleep 60
done
echo ALLDONE >> $L
