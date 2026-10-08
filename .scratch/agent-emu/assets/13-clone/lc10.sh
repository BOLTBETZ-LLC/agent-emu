cd C:/dev/agent-emu-work/clone-exp; export AE_PROFILE=slim4; L=out/lc10.log; : > $L
seen=0; ok=0; t0=$(date +%s)
while [ $ok -lt 2 ]; do
  if [ $(( $(date +%s) - t0 )) -gt 10800 ]; then echo "$(date +%T) GAVE UP: A17 10-Device run not seen finishing in 3 h" >> $L; echo DONE >> $L; exit 0; fi
  big=$(powershell -NoProfile -Command "@(Get-CimInstance Win32_Process -Filter \"Name='crosvm.exe'\" | Where-Object { \$_.CommandLine -match 'ae-vm-3[3-9] ' }).Count")
  n=$(powershell -NoProfile -Command "@(Get-CimInstance Win32_Process -Filter \"Name='crosvm.exe'\" | Where-Object { \$_.CommandLine -match 'ae-vm-3[0-9] ' }).Count")
  a=$(powershell -NoProfile -Command "[int](Get-Counter '\Memory\Available MBytes').CounterSamples[0].CookedValue")
  [ "$big" -gt 0 ] && seen=1
  if [ $seen -eq 1 ] && [ "$n" -eq 0 ] && [ "$a" -gt 9000 ]; then ok=$((ok+1)); else ok=0; fi
  echo "$(date +%T) wait a17_10run_seen=$seen a17=$n avail=$a ok=$ok" >> $L; [ $ok -lt 2 ] && sleep 60
done
powershell -NoProfile -ExecutionPolicy Bypass -File attrib.ps1 > out/lc10-base.json
for i in $(seq 13 22); do
  a=$(powershell -NoProfile -Command "[int](Get-Counter '\Memory\Available MBytes').CounterSamples[0].CookedValue")
  if [ "$a" -lt 3000 ]; then echo "GUARD avail=$a before d$i" >> $L; break; fi
  r=$(timeout 200 python clone_exp.py restore $i C:/dev/agent-emu-work/clone-exp/snapG --cow 2>&1 | grep -E '"restore_s"|"console_s"|ABORT' | tr -d '\n ')
  echo "d$i avail_before=$a $r" >> $L; echo "$r" | grep -q ABORT && break
done
python drive.py lc10-plateau --ids $(seq -s, 13 22) --minutes 10 --relaunch-at 5 --tap-every 20 > out/lc10-plateau.log 2>&1; echo "PLATEAU EXIT $?" >> $L
python drive.py lc10-cap --ids $(seq -s, 13 22) --minutes 5 --relaunch-at 99 --tap-every 20 --cap-main 300 --cap-helper 16 > out/lc10-cap.log 2>&1; echo "CAP EXIT $?" >> $L
python latency.py lc10-cap --ids 13,14,15 --n 20 --warmup 2 > out/lc10-latency.log 2>&1; echo "LATENCY EXIT $?" >> $L
powershell -NoProfile -ExecutionPolicy Bypass -File attrib.ps1 > out/lc10-prestop.json
python - >> $L 2>&1 <<'PY'
from PIL import Image, ImageDraw
W,H=360,540; sheet=Image.new("RGB",(W*5,H*2+40),"white"); d=ImageDraw.Draw(sheet)
for n,i in enumerate(range(13,23)):
    im=Image.open(f"out/lc10-cap-d{i}-screencap.png").convert("RGB").resize((W,H))
    x,y=(n%5)*W,(n//5)*(H+20)+20; sheet.paste(im,(x,y)); d.rectangle([x,y-20,x+W,y],fill="black"); d.text((x+5,y-17),f"d{i} screencap (capped)",fill="yellow")
sheet.save("out/lc10-sheet.png"); print("SHEET ok")
PY
powershell -NoProfile -ExecutionPolicy Bypass -File stopseq.ps1 -Out out/lc10-stopseq.json >> $L 2>&1; echo "STOPSEQ EXIT $?" >> $L
for i in $(seq 13 22); do python clone_exp.py stop $i >/dev/null 2>&1; done; echo DONE >> $L
