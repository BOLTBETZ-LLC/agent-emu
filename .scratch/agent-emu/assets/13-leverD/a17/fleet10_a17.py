# Lever D Android 17 copy of fleet_diet.py (ids 30+, ports 7130+, own dirs). Guest diet runs: fleet_exp.py with own Device ids (16+), console ports (7116+), run dir and kill step.
# Boots one Device (default), puts the proof app on its first screen, dumps guest memory diagnostics,
# screenshots, then stops ONLY its own crosvm (broker command line contains fleet-diet).
# usage: python fleet_diet.py <tag> [--mem 896] [--run run-diet] [--params "kfence.sample_interval=0"]
#        [--sinks 20] [--keep]
import argparse, base64, json, os, re, socket, subprocess, time, uuid

W = "C:/dev/agent-emu-work"; F = f"{W}/leverD/a17/fleet"; BASE = 30  # diet Devices: own dirs, ids and ports; never touch other VMs
ap = argparse.ArgumentParser(); ap.add_argument("tag"); ap.add_argument("--n", type=int, default=1)
ap.add_argument("--mem", default="896"); ap.add_argument("--cpus", default="2"); ap.add_argument("--run", default="run-diet")
ap.add_argument("--params", default=""); ap.add_argument("--sinks", default="20"); ap.add_argument("--extra", default=""); ap.add_argument("--gpu", default="")
ap.add_argument("--settle", type=int, default=30); ap.add_argument("--timeout", type=int, default=600)
ap.add_argument("--keep", action="store_true"); ap.add_argument("--crosvm", default="crosvm-diet")
a = ap.parse_args()
RUN = a.run if ":" in a.run else f"{W}/{a.run}"
CROSVM = f"{W}/{a.crosvm}/target/release/crosvm.exe"
O = f"{W}/leverD/results/{a.tag}"; os.makedirs(O, exist_ok=True)
SHARED = ["kernel-dax", "initrd-dax-pmem.img", "boot.img", "init_boot.img", "vendor_boot.img",
          "vbmeta.img", "vbmeta_system.img", "vbmeta_system_dlkm.img", "vbmeta_vendor_dlkm.img", "super.img", "apk.img"]
GUARD_MB = 4000

def ps(cmd):
    return subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True).stdout

def available_mb():
    return int(float(ps("(Get-Counter '\\Memory\\Available MBytes').CounterSamples[0].CookedValue").strip()))

def kill_mine():
    # Lever D: stop only our ids (ae-vm-30+) and their bridges; never match other workers' fleet-diet / boot-diet processes.
    for k in range(a.n):
        print(subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", f"{W}/leverD/tools/stop.ps1", "-Id", str(BASE + k)],
                             capture_output=True, text=True).stdout.strip())

def gsh(port, cmd, timeout=120):
    marker = "__AE_" + uuid.uuid4().hex[:8] + "__"
    s = socket.create_connection(("127.0.0.1", port)); s.settimeout(1)
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
    return "\n".join(l for l in t.split("\n") if marker not in l and not l.startswith(("su 0", "<", "console:"))).strip()

def make_device(i):
    d = f"{F}/d{i}"; os.makedirs(d, exist_ok=True)
    for f in SHARED:  # hard links to this run's images (re-linked every run so a new run dir takes effect)
        if os.path.exists(f"{d}/{f}"): os.remove(f"{d}/{f}")
        os.link(f"{RUN}/{f}", f"{d}/{f}")
    for n, sz in (("frp", 1 << 20), ("metadata", 64 << 20), ("userdata", 8 << 30), ("misc", 1 << 20), ("out", 64 << 20)):
        if os.path.exists(f"{d}/{n}.img"): os.remove(f"{d}/{n}.img")
        open(f"{d}/{n}.img", "wb").truncate(sz)
    for f in ("kernel.log", "logcat.log", "crosvm.log", "console.log", "os_composite.img", "os_composite.img.filler",
              "os_composite.img.footer", "os_composite.img.header"):
        if os.path.exists(f"{d}/{f}"): os.remove(f"{d}/{f}")
    parts = ("misc:misc.img:writable frp:frp.img:writable boot_a:boot.img boot_b:boot.img init_boot_a:init_boot.img "
             "init_boot_b:init_boot.img vendor_boot_a:vendor_boot.img vendor_boot_b:vendor_boot.img vbmeta_a:vbmeta.img "
             "vbmeta_b:vbmeta.img vbmeta_system_a:vbmeta_system.img vbmeta_system_b:vbmeta_system.img "
             "vbmeta_system_dlkm_a:vbmeta_system_dlkm.img vbmeta_system_dlkm_b:vbmeta_system_dlkm.img "
             "vbmeta_vendor_dlkm_a:vbmeta_vendor_dlkm.img vbmeta_vendor_dlkm_b:vbmeta_vendor_dlkm.img super:super.img "
             "userdata:userdata.img:writable metadata:metadata.img:writable").split()
    subprocess.run([CROSVM, "create_composite", "os_composite.img"] + parts, cwd=d, capture_output=True, check=True)
    return d

DIAG = {
    "meminfo": "cat /proc/meminfo",
    "slabtop": "cat /proc/slabinfo | tail -n +3 | awk \"{printf \\\"%d %s\\\\n\\\", \\$3*\\$4/1024, \\$1}\" | sort -rn | head -40",
    "zoneinfo": "grep -E \"^Node|pages free|min |low |high |managed|present|protection\" /proc/zoneinfo",
    "zram": "cat /sys/block/zram0/disksize /sys/block/zram0/mm_stat; cat /proc/swaps",
    "vmstat": "cat /proc/vmstat",
    "dumpsys_meminfo": "dumpsys meminfo",
    "services": "dumpsys -l",
    "ps": "ps -A -o RSS,PID,NAME | sort -rn | head -60",
    "cmdline": "cat /proc/cmdline",
    "allocinfo_top": "sort -k1 -rn /proc/allocinfo 2>/dev/null | head -60",
    "sysvm": "for f in min_free_kbytes watermark_scale_factor watermark_boost_factor extra_free_kbytes swappiness page-cluster; do echo $f $(cat /proc/sys/vm/$f 2>/dev/null); done",
    "threads": "ls /proc/*/task 2>/dev/null | grep -c . ; ls -d /proc/[0-9]* | wc -l",
    "lsmod": "cat /proc/modules | sort -k2 -rn | head -50",
    "virtio": "for d in /sys/bus/virtio/devices/*; do echo $(basename $d) $(cat $d/device) $(basename $(readlink $d/driver) 2>/dev/null); done",
    "slabinfo_full": "cat /proc/slabinfo",
    "dmesg_early": "dmesg | head -400",
    "vmallocinfo_top": "awk \"{print \\$2, \\$3}\" /proc/vmallocinfo | sort | awk \"{s[\\$2]+=\\$1} END {for (k in s) print s[k]/1024, k}\" | sort -rn | head -25",
}

if available_mb() < GUARD_MB:
    raise SystemExit(f"abort: host Available {available_mb()} MB < {GUARD_MB} MB")

# ---- Lever D 10-Device Android 17 proof (appended to the fleet_a17.py helpers by build_fleet10.py) ----
# Boot one Device at a time (next starts after the previous app is on screen), settle, cap every Device,
# hold with taps, screenshot all, then stop them one at a time and record the Available MB each stop frees.
import ctypes, ctypes.wintypes as wt
k32 = ctypes.WinDLL("kernel32", use_last_error=True); k32.OpenProcess.restype = wt.HANDLE
k32.SetProcessWorkingSetSizeEx.argtypes = [wt.HANDLE, ctypes.c_size_t, ctypes.c_size_t, wt.DWORD]
CAP_MAIN, CAP_HELPER, HOLD = int(os.environ.get("CAP_MAIN", "200")), int(os.environ.get("CAP_HELPER", "16")), int(os.environ.get("HOLD", "300"))
IDS = [BASE + k for k in range(a.n)]

def host():
    o = ps("$c=(Get-Counter '\\Memory\\Available MBytes').CounterSamples[0].CookedValue; "
           "'{0}|{1}' -f [int]$c,((Get-Process 'Memory Compression').WorkingSet64 -shr 20)").strip()
    av, mc = o.split("|"); return {"available_mb": int(av), "compression_mb": int(mc)}

def procs(i):
    rows = [l.split("|", 2) for l in ps("Get-CimInstance Win32_Process -Filter \"Name='crosvm.exe'\" | % { '{0}|{1}|{2}' -f $_.ProcessId,$_.ParentProcessId,$_.CommandLine }").splitlines() if l.count("|") >= 2]
    b = [int(p) for p, pp, cl in rows if f"ae-vm-{i} " in cl + " " and " run-mp " in f" {cl} "]
    return [(int(p), cl) for p, pp, cl in rows if b and (int(p) == b[0] or int(pp) == b[0])]

def ws(pr): return sum(int(ps(f"(Get-Process -Id {p} -EA SilentlyContinue).WorkingSet64") or 0) for p, _ in pr) >> 20

def stop(i):
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", f"{W}/leverD/tools/stop.ps1", "-Id", str(i)], capture_output=True)

def stop_all(): [stop(i) for i in IDS]

def guard():
    av = available_mb()
    if av < 3000:
        res["error"] = f"host guard: Available {av} MB"; stop_all(); json.dump(res, open(f"{O}/result.json", "w"), indent=1); raise SystemExit(res["error"])

res = {"tag": a.tag, "ids": IDS, "mem": a.mem, "params": a.params, "sinks": a.sinks, "crosvm": a.crosvm, "cap": [CAP_MAIN, CAP_HELPER], "hold_s": HOLD, "devices": {}}
res["host_before"] = host(); n_apk = int(open(f"{RUN}/apk.size").read()); t_all = time.time()
for i in IDS:
    guard(); d = make_device(i); dev = {"dir": d}; port = 7100 + i; t0 = time.time()
    extra = f"--socket PIPE:ae-vm-{i} --pmem path={RUN}/system-pmem.img,ro=true {a.extra}".strip()
    env = dict(os.environ, AE_DIR=d.replace("/", "\\"), AE_ID=str(i), AE_MEM=a.mem, AE_CPUS=a.cpus, AE_EXTRA=extra,
               AE_KERNEL="kernel-dax", AE_INITRD="initrd-dax-pmem.img", AE_PARAMS=a.params, AE_SINKS=a.sinks, AE_GPU_EXTRA=a.gpu,
               AE_CROSVM=CROSVM.replace("/", "\\"))
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", f"{W}/boot-diet.ps1"], env=env,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", f"{W}/console_bridge.ps1",
                      "-Id", str(i), "-Port", str(port), "-Log", f"{d}/console.log".replace("/", "\\")],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
    last = 0
    while True:
        try:
            if gsh(port, "getprop sys.boot_completed; getprop dev.bootcomplete", 20).split() == ["1", "1"]: break
        except OSError: pass
        if time.time() - t0 > a.timeout: dev["error"] = "boot timeout"; break
        if time.time() - last > 30: last = time.time(); guard()
        time.sleep(3)
    dev["ready_s"] = round(time.time() - t0, 1)
    if "error" not in dev:
        dev["install"] = gsh(port, f"head -c {n_apk} /dev/block/vdb > /data/local/tmp/p.apk && chmod 644 /data/local/tmp/p.apk && pm install -r /data/local/tmp/p.apk; rm -f /data/local/tmp/p.apk", 300)
        gsh(port, "settings put global hide_error_dialogs 1; cmd connectivity airplane-mode enable; settings put global window_animation_scale 0; settings put global transition_animation_scale 0; "
                  "settings put global animator_duration_scale 0; svc power stayon true; input keyevent KEYCODE_WAKEUP; wm dismiss-keyguard; "
                  "settings put secure immersive_mode_confirmations confirmed")
        dev["launch"] = gsh(port, "am start -W -n com.boltbetz.staging/com.boltbetz.MainActivity | grep -E \"Status|TotalTime\"", 180)
    dev["host_after_boot"] = host(); res["devices"][i] = dev
    print(i, dev.get("ready_s"), dev.get("launch", dev.get("error")), dev["host_after_boot"], flush=True)
    json.dump(res, open(f"{O}/result.json", "w"), indent=1)
res["all_up_s"] = round(time.time() - t_all, 1)
time.sleep(45)
P = {i: procs(i) for i in IDS}
res["host_settled"] = host(); res["ws_settled_mb"] = {i: ws(P[i]) for i in IDS}
for i in IDS:
    for p, cl in P[i]:
        h = k32.OpenProcess(0x0100 | 0x0400, False, p)
        k32.SetProcessWorkingSetSizeEx(h, 1 << 20, (CAP_MAIN if "run-main" in cl else CAP_HELPER) << 20, 0x4 | 0x2); k32.CloseHandle(h)
res["host_after_cap"] = host(); res["hold"] = []; taps = []; end = time.time() + HOLD; k = 0
while time.time() < end:  # every Device gets a tap every ~10 s; host sampled every ~30 s
    for i in IDS:
        t = time.time(); gsh(7100 + i, "input tap 360 780", 30); taps.append(round((time.time() - t) * 1000))
    k += 1
    if k % 3 == 0: res["hold"].append({"t": round(HOLD - (end - time.time())), **host(), "ws_total_mb": sum(ws(P[i]) for i in IDS)}); guard()
    time.sleep(max(0, 10 - 0.2 * len(IDS)))
res["host_after_hold"] = host(); res["ws_capped_mb"] = {i: ws(P[i]) for i in IDS}
taps.sort(); res["tap_ms_p50_p95_max"] = [taps[len(taps) // 2], taps[int(len(taps) * .95)], taps[-1]]
for i in IDS:
    port = 7100 + i; dev = res["devices"][i]
    dev["app_pid"] = gsh(port, "pidof com.boltbetz.staging")
    dev["guest"] = gsh(port, "dumpsys meminfo | grep -E \"Used RAM|ZRAM\"")
    t = gsh(port, "screencap -p /data/local/tmp/s.png; echo B64START; base64 -w 0 /data/local/tmp/s.png; echo; echo B64END", 300)
    m = re.search(r"B64START\n(.*?)\nB64END", t, re.S)
    if m: dev["screenshot"] = f"{O}/d{i}.png"; open(dev["screenshot"], "wb").write(base64.b64decode(re.sub(r"\s", "", m.group(1))))
pids = [res["devices"][i].get("app_pid", "") for i in IDS]; res["distinct_app_pids"] = len({p for p in pids if p.strip().isdigit()})
cap_ws = sum(res["ws_capped_mb"].values()); comp = res["host_after_hold"]["compression_mb"] - res["host_settled"]["compression_mb"]
res["per_device_ws_mb"] = round(cap_ws / len(IDS)); res["compression_growth_mb"] = comp
res["per_device_ram_mb"] = round((cap_ws + max(0, comp)) / len(IDS))
res["stop_freed"] = []
for i in IDS:  # marginal cost: Available freed by each stop
    before = host(); stop(i); time.sleep(15); after = host()
    res["stop_freed"].append({"id": i, "available_freed_mb": after["available_mb"] - before["available_mb"], "compression_freed_mb": before["compression_mb"] - after["compression_mb"]})
res["host_end"] = host()
json.dump(res, open(f"{O}/result.json", "w"), indent=1); print(json.dumps({k: v for k, v in res.items() if k != "devices"}, indent=1))
