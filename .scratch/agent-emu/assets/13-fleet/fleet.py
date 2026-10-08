# Boot N Devices at once, put the proof app on its first screen on each, and measure the Fleet.
# Fleet memory = drop in host Available MBytes from before boot to settled (honest "how many fit"),
# plus per-process working sets grouped by Device.
# usage: python fleet.py <tag> --n 2 [--mem 1024] [--cpus 2] [--pmem]
import argparse, base64, json, os, re, shutil, socket, subprocess, time, uuid

W = "C:/dev/agent-emu-work"; RUN = f"{W}/run"; F = f"{W}/fleet"
ap = argparse.ArgumentParser(); ap.add_argument("tag"); ap.add_argument("--n", type=int, default=2)
ap.add_argument("--mem", default="1024"); ap.add_argument("--cpus", default="2"); ap.add_argument("--pmem", action="store_true")
ap.add_argument("--settle", type=int, default=30)
a = ap.parse_args()
O = f"{W}/results/{a.tag}"; os.makedirs(O, exist_ok=True)
SHARED = ["kernel-dax", "initrd-dax-pmem.img", "initrd-slim.img", "boot.img", "init_boot.img", "vendor_boot.img",
          "vbmeta.img", "vbmeta_system.img", "vbmeta_system_dlkm.img", "vbmeta_vendor_dlkm.img", "super.img", "apk.img"]
CROSVM = f"{W}/crosvm-pmem/target/release/crosvm.exe"

def ps(cmd):
    return subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True).stdout

def available_mb():
    return int(float(ps("(Get-Counter '\\Memory\\Available MBytes').CounterSamples[0].CookedValue").strip()))

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
    for f in SHARED:  # hard links: one copy on disk, one Windows file cache for every Device
        if not os.path.exists(f"{d}/{f}"):
            os.link(f"{RUN}/{f}", f"{d}/{f}")
    for n, sz in (("frp", 1 << 20), ("metadata", 64 << 20), ("userdata", 8 << 30), ("misc", 1 << 20), ("out", 64 << 20)):
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

res = {"tag": a.tag, "n": a.n, "mem": a.mem, "cpus": a.cpus, "pmem": a.pmem, "devices": []}
ps("Get-Process crosvm -ErrorAction SilentlyContinue | Stop-Process -Force; "
   "Get-CimInstance Win32_Process -Filter \"Name='powershell.exe'\" | Where-Object { $_.CommandLine -like '*console_bridge*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
time.sleep(5)
dirs = [make_device(i) for i in range(a.n)]
res["available_before_mb"] = available_mb()
t0 = time.time()
for i, d in enumerate(dirs):
    extra = f"--socket PIPE:ae-vm-{i}"
    if a.pmem: extra += f" --pmem path={RUN}/system-pmem.img,ro=true"
    env = dict(os.environ, AE_DIR=d.replace("/", "\\"), AE_ID=str(i), AE_MEM=a.mem, AE_CPUS=a.cpus, AE_EXTRA=extra,
               AE_KERNEL="kernel-dax", AE_INITRD="initrd-dax-pmem.img" if a.pmem else "initrd-slim.img",
               AE_CROSVM=CROSVM.replace("/", "\\"))
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", f"{W}/boot-stage1.ps1"], env=env,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", f"{W}/console_bridge.ps1",
                      "-Id", str(i), "-Port", str(7100 + i), "-Log", f"{d}/console.log".replace("/", "\\")],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
    time.sleep(3)

n_apk = int(open(f"{RUN}/apk.size").read())
for i, d in enumerate(dirs):
    dev = {"id": i, "dir": d}; port = 7100 + i
    while True:
        try:
            if gsh(port, "getprop sys.boot_completed; getprop dev.bootcomplete", 20).split() == ["1", "1"]: break
        except OSError: pass
        if time.time() - t0 > 600: dev["error"] = "boot timeout"; break
        time.sleep(3)
    dev["ready_s"] = round(time.time() - t0, 1)
    if "error" not in dev:
        gsh(port, "insmod /system_dlkm/lib/modules/virtio_balloon.ko 2>/dev/null; true")
        dev["install"] = gsh(port, f"head -c {n_apk} /dev/block/vdb > /data/local/tmp/p.apk && chmod 644 /data/local/tmp/p.apk && pm install -r /data/local/tmp/p.apk; rm -f /data/local/tmp/p.apk", 300)
        gsh(port, "cmd connectivity airplane-mode enable; settings put global window_animation_scale 0; settings put global transition_animation_scale 0; "
                  "settings put global animator_duration_scale 0; svc power stayon true; input keyevent KEYCODE_WAKEUP; wm dismiss-keyguard; "
                  "settings put secure immersive_mode_confirmations confirmed")
        dev["launch"] = gsh(port, "am start -W -n com.boltbetz.staging/com.boltbetz.MainActivity | grep -E \"Status|TotalTime\"", 120)
    res["devices"].append(dev)
time.sleep(a.settle)
for dev in res["devices"]:
    port = 7100 + dev["id"]
    if "error" in dev: continue
    dev["app_pid"] = gsh(port, "pidof com.boltbetz.staging")
    dev["guest_used"] = gsh(port, "dumpsys meminfo | grep \"Used RAM\"")
    t = gsh(port, "screencap -p /data/local/tmp/s.png; echo B64START; base64 -w 0 /data/local/tmp/s.png; echo; echo B64END", 300)
    m = re.search(r"B64START\n(.*?)\nB64END", t, re.S)
    if m:
        open(f"{O}/first-screen-d{dev['id']}.png", "wb").write(base64.b64decode(re.sub(r"\s", "", m.group(1))))
res["available_after_mb"] = available_mb()
res["fleet_cost_mb"] = res["available_before_mb"] - res["available_after_mb"]
res["per_device_mb"] = round(res["fleet_cost_mb"] / a.n)
procs = ps("Get-CimInstance Win32_PerfRawData_PerfProc_Process | Where-Object { $_.Name -like 'crosvm*' } | "
           "ForEach-Object { '{0}|{1}' -f $_.IDProcess,$_.WorkingSet }")
res["crosvm_ws_total_mb"] = sum(int(l.split("|")[1]) for l in procs.split()) >> 20
json.dump(res, open(f"{O}/result.json", "w"), indent=1)
print(json.dumps(res, indent=1))
