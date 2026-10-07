#!/bin/bash
# usage: measure.sh <image-tag> <port>   e.g. measure.sh google_atd 5590
# Boots a fresh stock AVD on API 36 x86_64, installs the proof APK, launches it
# offline (airplane mode, so no OTA replaces the bundled JS), and records memory.
set -u
export MSYS_NO_PATHCONV=1
TAG=$1; PORT=$2
SDK="C:/Users/aaron/AppData/Local/Android/Sdk"
ADB="$SDK/platform-tools/adb.exe"; EMU="$SDK/emulator/emulator.exe"
AVDM="$SDK/cmdline-tools/latest/bin/avdmanager.bat"
W="C:/Users/aaron/AppData/Local/Temp/claude/C--dev/00877a5e-8802-4a28-b23f-a763e5d5b403/scratchpad/baseline"
O="$W/out-$TAG"; mkdir -p "$O"
AVD="agentemu-base-$TAG"; S="emulator-$PORT"
log(){ echo "$(date +%T) $*" | tee -a "$O/run.log"; }

log "create $AVD"
echo no | "$AVDM" create avd -f -n "$AVD" -k "system-images;android-36;$TAG;x86_64" -d pixel_7 >"$O/avd.log" 2>&1
CFG="$USERPROFILE/.android/avd/$AVD.avd/config.ini"
grep -E "^hw.ramSize|^hw.lcd|^hw.gpu|^vm.heapSize" "$CFG" | tee "$O/config.txt"

log "boot (headless, cold, no snapshot)"
T0=$(date +%s)
"$EMU" -avd "$AVD" -port "$PORT" -no-window -no-audio -no-snapshot -no-boot-anim -gpu host >"$O/emu.log" 2>&1 &
"$ADB" -s "$S" wait-for-device
until [ "$("$ADB" -s "$S" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ]; do
  sleep 2; [ $(( $(date +%s) - T0 )) -gt 600 ] && { log "BOOT TIMEOUT"; break; }
done
T1=$(date +%s); log "boot_completed after $((T1-T0)) s"
sleep 20  # let boot settle

"$ADB" -s "$S" shell svc power stayon true
"$ADB" -s "$S" shell input keyevent KEYCODE_WAKEUP
"$ADB" -s "$S" shell wm dismiss-keyguard
"$ADB" -s "$S" shell settings put system screen_off_timeout 1800000
"$ADB" -s "$S" shell cmd connectivity airplane-mode enable
log "install"
"$ADB" -s "$S" install -r "$W/apk/proof.apk" >"$O/install.log" 2>&1; tail -1 "$O/install.log" | tee -a "$O/run.log"
PKG=$("$ADB" -s "$S" shell pm list packages -3 | tr -d '\r' | sed 's/package://' | grep -i bolt | head -1)
log "package $PKG"
"$ADB" -s "$S" shell setprop debug.hwui.drawing_enabled true
"$ADB" -s "$S" shell getprop debug.hwui.drawing_enabled | tee -a "$O/run.log"
"$ADB" -s "$S" logcat -c
T2=$(date +%s%3N)
"$ADB" -s "$S" shell monkey -p "$PKG" -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1
sleep 45  # expo-updates offline timeout (~10 s) plus render
"$ADB" -s "$S" shell input keyevent KEYCODE_WAKEUP
"$ADB" -s "$S" exec-out screencap -p > "$O/first-screen.png"
"$ADB" -s "$S" emu screenrecord screenshot "$(cygpath -w "$O")" > "$O/host-shot.log" 2>&1
"$ADB" -s "$S" shell uiautomator dump /sdcard/ui.xml >/dev/null 2>&1; "$ADB" -s "$S" exec-out cat /sdcard/ui.xml > "$O/ui.xml"
"$ADB" -s "$S" shell dumpsys power | grep -E "mWakefulness=|Display Power" > "$O/power.txt"
log "screenshot taken $(( ($(date +%s%3N)-T2)/1000 )) s after launch"
"$ADB" -s "$S" logcat -d -b crash > "$O/crash.log"; "$ADB" -s "$S" logcat -d > "$O/logcat.log"
log "crash buffer lines: $(wc -l <"$O/crash.log")  FATAL in logcat: $(grep -c 'FATAL EXCEPTION' "$O/logcat.log")"
"$ADB" -s "$S" shell pidof "$PKG" | tee -a "$O/run.log"

log "guest memory"
"$ADB" -s "$S" shell cat /proc/meminfo > "$O/proc-meminfo.txt"
"$ADB" -s "$S" shell dumpsys meminfo > "$O/dumpsys-meminfo.txt"
"$ADB" -s "$S" shell dumpsys meminfo "$PKG" > "$O/dumpsys-meminfo-app.txt"

log "host memory"
powershell -NoProfile -Command "\$p = Get-CimInstance Win32_Process | Where-Object { \$_.CommandLine -like '*-port $PORT*' -or (\$_.Name -like 'qemu-system*' -and \$_.CommandLine -like '*$AVD*') }; foreach (\$x in \$p) { \$g = Get-Process -Id \$x.ProcessId; \$priv = (Get-Counter (\"\\Process(\" + \$g.ProcessName + \")\\Working Set - Private\") -ErrorAction SilentlyContinue).CounterSamples | Select -First 1; '{0} pid={1} WS_MB={2:N0} Private_Commit_MB={3:N0} WS_Private_MB={4:N0}' -f \$g.ProcessName,\$g.Id,(\$g.WorkingSet64/1MB),(\$g.PrivateMemorySize64/1MB),(\$priv.CookedValue/1MB) }" | tee "$O/host-mem.txt"

log "shutdown"
"$ADB" -s "$S" emu kill >/dev/null 2>&1; sleep 10
log "DONE"
