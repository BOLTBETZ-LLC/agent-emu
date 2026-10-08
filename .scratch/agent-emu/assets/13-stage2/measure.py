# Stage 1/2 measurement driver: boot a Device on crosvm/WHPX, install the proof app, launch it
# offline, take a screenshot, and record host + guest memory. Each step is checked.
# usage: python measure.py <tag> [--mem 2048] [--extra "--balloon-page-reporting"]
import argparse, base64, json, os, re, socket, subprocess, sys, time, uuid

W = "C:/dev/agent-emu-work"; R = f"{W}/run"
ap = argparse.ArgumentParser(); ap.add_argument("tag"); ap.add_argument("--mem", default="4096")
ap.add_argument("--extra", default=""); ap.add_argument("--trim", action="store_true"); ap.add_argument("--balloon-mb", type=int, default=0); ap.add_argument("--keep-data", action="store_true"); ap.add_argument("--settle", type=int, default=30)
a = ap.parse_args()
O = f"{W}/results/{a.tag}"; os.makedirs(O, exist_ok=True)
res = {"tag": a.tag, "mem": a.mem, "extra": a.extra}

def ps(cmd):
    return subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True).stdout

def gsh(cmd, timeout=120):
    marker = "__AE_" + uuid.uuid4().hex[:8] + "__"
    s = socket.create_connection(("127.0.0.1", 7000)); s.settimeout(1)
    s.sendall(f"su 0 sh -c '{cmd}'; echo {marker}\n".encode())
    out, end = b"", time.time() + timeout
    while time.time() < end:
        try: d = s.recv(65536)
        except socket.timeout: continue
        if not d: break
        out += d
        if ("\n" + marker).encode() in out.replace(b"\r", b""): break
    s.close()
    t = out.decode(errors="replace").replace("\r", "")
    # drop the echoed command line(s) and the marker
    lines = [l for l in t.split("\n") if marker not in l and not l.startswith("su 0") and not l.startswith("<") and not l.startswith("console:")]
    return "\n".join(lines).strip()

def host_mem():
    o = ps("Get-CimInstance Win32_PerfRawData_PerfProc_Process | Where-Object { $_.Name -like 'crosvm*' } | "
           "ForEach-Object { '{0}|{1}|{2}|{3}' -f $_.IDProcess,$_.WorkingSet,$_.WorkingSetPrivate,$_.PrivateBytes }")
    procs = []
    for l in o.split():
        pid, ws, wsp, pb = l.split("|")
        cl = ps(f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine")
        role = re.search(r"(device|run-mp|run)\b", cl)
        procs.append({"pid": int(pid), "ws_mb": int(ws) // 2**20, "wspriv_mb": int(wsp) // 2**20, "private_mb": int(pb) // 2**20,
                      "role": (re.search(r"--(block|gpu|net|snd|slirp)", cl) or role).group(0) if (role or re.search(r"--(block|gpu)", cl)) else "?"})
    return procs

# 1. stop old, fresh writable disks
ps("Get-Process crosvm -ErrorAction SilentlyContinue | Stop-Process -Force; "
   "Get-CimInstance Win32_Process -Filter \"Name='powershell.exe'\" | Where-Object { $_.CommandLine -like '*console_bridge*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
time.sleep(3)
if not a.keep_data:
    for n, sz in (("frp", 1 << 20), ("metadata", 64 << 20), ("userdata", 8 << 30), ("misc", 1 << 20)):
        open(f"{R}/{n}.img", "wb").truncate(sz)
for f in ("kernel.log", "logcat.log", "crosvm.log", "console.log"):
    try: os.remove(f"{R}/{f}")
    except FileNotFoundError: pass

# 2. boot
env = dict(os.environ, AE_MEM=a.mem, AE_EXTRA=a.extra)
t0 = time.time()
subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", f"{W}/boot-stage1.ps1"], env=env,
                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", f"{W}/console_bridge.ps1"],
                 stdin=subprocess.DEVNULL, stdout=open(f"{W}/logs/bridge.log", "w"), stderr=subprocess.STDOUT, creationflags=0x08000000)
def booted():
    for f in ("kernel.log", "logcat.log"):
        try:
            if "processing action (sys.boot_completed=1" in open(f"{R}/{f}", errors="replace").read(): return True
        except FileNotFoundError: pass
    return False
while not booted():
    if time.time() - t0 > 300: res["error"] = "boot timeout"; break
    time.sleep(1)
res["boot_s"] = round(time.time() - t0, 1)
while gsh("getprop sys.boot_completed; getprop dev.bootcomplete", 30).split() != ["1", "1"]:
    if time.time() - t0 > 400: res["error"] = "pm never ready"; break
    time.sleep(2)
res["pm_ready_s"] = round(time.time() - t0, 1)
gsh("insmod /system_dlkm/lib/modules/virtio_balloon.ko 2>/dev/null; true")

if a.trim:
    pk = [l.strip() for l in open(f"{W}/trim.txt") if l.strip()]
    out = gsh("; ".join(f"pm disable-user --user 0 {p} >/dev/null 2>&1" for p in pk) + "; am kill-all; echo trimmed", 300)
    res["trim"] = f"{len(pk)} packages, {out.splitlines()[-1] if out else ''}"
    time.sleep(10)
# 3. install + launch offline
n = int(open(f"{R}/apk.size").read())
res["install"] = gsh(f"head -c {n} /dev/block/vdb > /data/local/tmp/p.apk && chmod 644 /data/local/tmp/p.apk && pm install -r /data/local/tmp/p.apk; rm -f /data/local/tmp/p.apk", 300)
gsh("cmd connectivity airplane-mode enable; settings put global window_animation_scale 0; settings put global transition_animation_scale 0; "
    "settings put global animator_duration_scale 0; svc power stayon true; input keyevent KEYCODE_WAKEUP; wm dismiss-keyguard; settings put secure immersive_mode_confirmations confirmed")
res["launch"] = gsh("am start -W -n com.boltbetz.staging/com.boltbetz.MainActivity | grep -E \"Status|TotalTime\"", 120)
time.sleep(a.settle)
res["app_pid"] = gsh("pidof com.boltbetz.staging")

if a.balloon_mb:
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", f"{W}/balloon.ps1", str(a.balloon_mb << 20)], capture_output=True)
    for _ in range(36):
        time.sleep(5)
        pages = int((re.search(r"balloon_inflate (\d+)", gsh("grep balloon_inflate /proc/vmstat")) or [0, 0])[1])
        if pages * 4096 >= (a.balloon_mb << 20) * 0.95: break
    res["balloon_inflated_mb"] = pages * 4096 >> 20
    res["app_pid_after_balloon"] = gsh("pidof com.boltbetz.staging")
# 4. screenshot
t = gsh("screencap -p /data/local/tmp/s.png; echo B64START; base64 -w 0 /data/local/tmp/s.png; echo; echo B64END", 300)
m = re.search(r"B64START\n(.*?)\nB64END", t, re.S)
if m:
    open(f"{O}/first-screen.png", "wb").write(base64.b64decode(re.sub(r"\s", "", m.group(1))))
    res["screenshot"] = f"{O}/first-screen.png"

# 5. memory
g = gsh("dumpsys meminfo | grep -E \"Total RAM|Free RAM|Used RAM\"; dumpsys meminfo com.boltbetz.staging | grep \"TOTAL PSS\"")
res["guest"] = g
res["host"] = host_mem()
res["host_total_ws_mb"] = sum(p["ws_mb"] for p in res["host"])
res["host_total_private_mb"] = sum(p["private_mb"] for p in res["host"])
json.dump(res, open(f"{O}/result.json", "w"), indent=1)
print(json.dumps({k: v for k, v in res.items() if k != "host"}, indent=1))
for p in res["host"]: print(p)
