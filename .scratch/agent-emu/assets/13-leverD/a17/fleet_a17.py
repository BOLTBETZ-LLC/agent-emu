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
res = {"tag": a.tag, "n": a.n, "mem": a.mem, "cpus": a.cpus, "run": a.run, "params": a.params, "sinks": a.sinks, "crosvm": a.crosvm, "gpu": a.gpu, "devices": []}
dirs = [make_device(BASE + i) for i in range(a.n)]
res["available_before_mb"] = available_mb()
t0 = time.time()
for i, d in enumerate(dirs, BASE):
    extra = f"--socket PIPE:ae-vm-{i} --pmem path={RUN}/system-pmem.img,ro=true {a.extra}".strip()
    env = dict(os.environ, AE_DIR=d.replace("/", "\\"), AE_ID=str(i), AE_MEM=a.mem, AE_CPUS=a.cpus, AE_EXTRA=extra,
               AE_KERNEL="kernel-dax", AE_INITRD="initrd-dax-pmem.img", AE_PARAMS=a.params, AE_SINKS=a.sinks, AE_GPU_EXTRA=a.gpu,
               AE_CROSVM=CROSVM.replace("/", "\\"))
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", f"{W}/boot-diet.ps1"], env=env,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", f"{W}/console_bridge.ps1",
                      "-Id", str(i), "-Port", str(7100 + i), "-Log", f"{d}/console.log".replace("/", "\\")],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
    time.sleep(3)

n_apk = int(open(f"{RUN}/apk.size").read())
for i, d in enumerate(dirs, BASE):
    dev = {"id": i, "dir": d}; port = 7100 + i; last = 0
    while True:
        try:
            if gsh(port, "getprop sys.boot_completed; getprop dev.bootcomplete", 20).split() == ["1", "1"]: break
        except OSError: pass
        klog = open(f"{d}/kernel.log", errors="replace").read() if os.path.exists(f"{d}/kernel.log") else ""
        if "Kernel panic" in klog or "Out of memory: Killed process" in klog and "system_server" in klog.split("Out of memory")[-1][:200]:
            dev["error"] = "kernel panic / system_server OOM"; break
        if "EXIT" in (open(f"{d}/crosvm.log", errors="replace").read()[-300:] if os.path.exists(f"{d}/crosvm.log") else ""):
            dev["error"] = "crosvm exited"; break
        if time.time() - t0 > a.timeout: dev["error"] = "boot timeout"; break
        if time.time() - last > 30:
            last = time.time(); av = available_mb()
            if av < 3000: dev["error"] = f"host guard: Available {av} MB"; break
        time.sleep(3)
    dev["ready_s"] = round(time.time() - t0, 1)
    if "error" not in dev:
        gsh(port, "insmod /system_dlkm/lib/modules/virtio_balloon.ko 2>/dev/null; true")
        dev["install"] = gsh(port, f"head -c {n_apk} /dev/block/vdb > /data/local/tmp/p.apk && chmod 644 /data/local/tmp/p.apk && pm install -r /data/local/tmp/p.apk; rm -f /data/local/tmp/p.apk", 300)
        gsh(port, "settings put global hide_error_dialogs 1; cmd connectivity airplane-mode enable; settings put global window_animation_scale 0; settings put global transition_animation_scale 0; "
                  "settings put global animator_duration_scale 0; svc power stayon true; input keyevent KEYCODE_WAKEUP; wm dismiss-keyguard; "
                  "settings put secure immersive_mode_confirmations confirmed")
        dev["launch"] = gsh(port, "am start -W -n com.boltbetz.staging/com.boltbetz.MainActivity | grep -E \"Status|TotalTime\"", 180)
    res["devices"].append(dev)
time.sleep(a.settle)
for dev in res["devices"]:
    port = 7100 + dev["id"]
    if "error" in dev: continue
    dev["app_pid"] = gsh(port, "pidof com.boltbetz.staging")
    dev["guest_used"] = gsh(port, "dumpsys meminfo | grep -E \"Used RAM|Free RAM|Lost RAM|ZRAM\"")
    dev["uptime"] = gsh(port, "cat /proc/uptime")
    t = gsh(port, "screencap -p /data/local/tmp/s.png; echo B64START; base64 -w 0 /data/local/tmp/s.png; echo; echo B64END", 300)
    m = re.search(r"B64START\n(.*?)\nB64END", t, re.S)
    if m:
        dev["screenshot"] = f"{O}/first-screen-d{dev['id']}.png"
        open(dev["screenshot"], "wb").write(base64.b64decode(re.sub(r"\s", "", m.group(1))))
    for k, c in DIAG.items():
        open(f"{O}/{k}-d{dev['id']}.txt", "w").write(gsh(port, c, 180))
for d in dirs:  # boot logs for failure analysis
    for f in ("kernel.log",):
        if os.path.exists(f"{d}/{f}"):
            open(f"{O}/{os.path.basename(d)}-{f}", "w").write(open(f"{d}/{f}", errors="replace").read()[-400000:])
res["available_after_mb"] = available_mb()
json.dump(res, open(f"{O}/result.json", "w"), indent=1)
if not a.keep: kill_mine()
print(json.dumps(res, indent=1))
