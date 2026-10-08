# agent-emu clone experiment: boot a template Device, snapshot it, restore clones.
# Devices 12+ (console ports 7100+id), own dirs under clone-exp/. Never touches other VMs.
# usage:
#   python clone_exp.py template 12               boot fresh, install + launch the proof app
#   python clone_exp.py snap 12 <snapdir>         suspend, snapshot, copy writable disks, stop
#   python clone_exp.py restore 13 <snapdir> [--cow]   start a clone from the snapshot
#   python clone_exp.py check 13 <tag>            console + screenshot (screencap and fb.bin)
#   python clone_exp.py stop 13
#   python clone_exp.py mem
import base64, json, os, re, shutil, socket, subprocess, sys, time, uuid

W = "C:/dev/agent-emu-work"
# AE_PROFILE=slim4: run-slim4 images, 640 MB, guest diet cmdline cuts, 11 consoles, one virtio-snd.
PROFILE = os.environ.get("AE_PROFILE", "")
SLIM4 = PROFILE in ("slim4", "slim5")  # slim5 = slim4 cuts + HomeStub image at 576 MB
RUN = f"{W}/run-{PROFILE}" if SLIM4 else f"{W}/run"
MEM = {"slim4": "640", "slim5": "576"}.get(PROFILE, "896")
DIET = dict(AE_PARAMS="virtio_blk.num_request_queues=1 virtio_blk.queue_depth=64 kfence.sample_interval=0 transparent_hugepage=never",
            AE_SINKS="11", AE_GPU_EXTRA="audio-device-mode=one-global") if SLIM4 else {}
BOOT = f"{W}/boot-diet.ps1" if SLIM4 else f"{W}/boot-stage1.ps1"
X = f"{W}/clone-exp"; O = f"{X}/out"
CROSVM = f"{W}/crosvm-clone/target/release/crosvm.exe"
SHARED = ["kernel-dax", "initrd-dax-pmem.img", "boot.img", "init_boot.img", "vendor_boot.img", "vbmeta.img",
          "vbmeta_system.img", "vbmeta_system_dlkm.img", "vbmeta_vendor_dlkm.img", "super.img", "apk.img"]
WRITABLE = {"frp": 1 << 20, "metadata": 64 << 20, "userdata": 8 << 30, "misc": 1 << 20, "out": 64 << 20}
MIN_AVAIL = 4000
PIPE_FB = r"\\.\pipe\ae-fb-{}"
os.makedirs(O, exist_ok=True)


def ps(cmd):
    return subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True).stdout


def host_mem():
    o = ps("$c=(Get-Counter '\\Memory\\Available MBytes','\\Memory\\Committed Bytes').CounterSamples; "
           "$mc=(Get-Process 'Memory Compression' -ErrorAction SilentlyContinue).WorkingSet64; "
           "'{0}|{1}|{2}' -f [int]$c[0].CookedValue,[int64]$c[1].CookedValue,[int64]$mc")
    av, com, mc = o.strip().split("|")
    return {"available_mb": int(av), "committed_mb": int(com) >> 20, "memcomp_ws_mb": int(mc or 0) >> 20}


def guard():
    m = host_mem()
    if m["available_mb"] < MIN_AVAIL:
        sys.exit(f"ABORT: host Available {m['available_mb']} MB < {MIN_AVAIL} MB")
    return m


def gsh(port, cmd, timeout=120):
    marker = "__AE_" + uuid.uuid4().hex[:8] + "__"
    s = socket.create_connection(("127.0.0.1", port), timeout=10); s.settimeout(1)
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
    if ("\n" + marker) not in t: raise TimeoutError(f"no reply to {cmd[:40]}")
    return "\n".join(l for l in t.split("\n") if marker not in l and not l.startswith(("su 0", "<", "console:"))).strip()


def ddir(i): return f"{X}/d{i}"
def sock(i): return f"\\\\.\\pipe\\ae-vm-{i}"


def ctl(*args):
    r = subprocess.run([CROSVM, *args], capture_output=True, text=True)
    return (r.returncode, (r.stdout + r.stderr).strip()[-400:])


def make_dir(i, from_dir=None):
    d = ddir(i)
    if os.path.exists(d): shutil.rmtree(d)
    os.makedirs(d)
    for f in SHARED: os.link(f"{RUN}/{f}", f"{d}/{f}")
    for n, sz in WRITABLE.items():
        if from_dir: shutil.copyfile(f"{from_dir}/{n}.img", f"{d}/{n}.img")
        else: open(f"{d}/{n}.img", "wb").truncate(sz)
    parts = ("misc:misc.img:writable frp:frp.img:writable boot_a:boot.img boot_b:boot.img init_boot_a:init_boot.img "
             "init_boot_b:init_boot.img vendor_boot_a:vendor_boot.img vendor_boot_b:vendor_boot.img vbmeta_a:vbmeta.img "
             "vbmeta_b:vbmeta.img vbmeta_system_a:vbmeta_system.img vbmeta_system_b:vbmeta_system.img "
             "vbmeta_system_dlkm_a:vbmeta_system_dlkm.img vbmeta_system_dlkm_b:vbmeta_system_dlkm.img "
             "vbmeta_vendor_dlkm_a:vbmeta_vendor_dlkm.img vbmeta_vendor_dlkm_b:vbmeta_vendor_dlkm.img super:super.img "
             "userdata:userdata.img:writable metadata:metadata.img:writable").split()
    subprocess.run([CROSVM, "create_composite", "os_composite.img"] + parts, cwd=d, capture_output=True, check=True)
    return d


def start(i, extra="", env_extra=None):
    d = ddir(i)
    extra = (f"--socket PIPE:ae-vm-{i} --pmem path={RUN}/system-pmem.img,ro=true "
             f"--input multi-touch[path=\\\\.\\pipe\\ae-touch-{i}] --input keyboard[path=\\\\.\\pipe\\ae-kbd-{i}] " + extra).strip()
    env = dict(os.environ, AE_DIR=d.replace("/", "\\"), AE_ID=str(i), AE_MEM=MEM, AE_CPUS="2", AE_EXTRA=extra,
               AE_KERNEL="kernel-dax", AE_INITRD="initrd-dax-pmem.img", AE_CROSVM=CROSVM.replace("/", "\\"),
               AGENT_EMU_HEADLESS="1", AGENT_EMU_NO_NET="1", AGENT_EMU_INPROC="1", AGENT_EMU_FB=f"{d}/fb.bin".replace("/", "\\"), AGENT_EMU_FB_PIPE=PIPE_FB.format(i),
               **DIET, **(env_extra or {}))
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", BOOT], env=env,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", f"{W}/console_bridge.ps1",
                      "-Id", str(i), "-Port", str(7100 + i), "-Log", f"{d}/console.log".replace("/", "\\")],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)


def wait_boot(i, limit=600):
    t0 = time.time()
    while time.time() - t0 < limit:
        try:
            if gsh(7100 + i, "getprop sys.boot_completed; getprop dev.bootcomplete", 20).split() == ["1", "1"]:
                return round(time.time() - t0, 1)
        except OSError: pass
        time.sleep(3)
    sys.exit("boot timeout")


def shot(i, tag):
    port = 7100 + i; r = {}
    t = gsh(port, "screencap -p /data/local/tmp/s.png; echo B64START; base64 -w 0 /data/local/tmp/s.png; echo; echo B64END", 300)
    m = re.search(r"B64START\n(.*?)\nB64END", t, re.S)
    if m:
        p = f"{O}/{tag}-screencap.png"; open(p, "wb").write(base64.b64decode(re.sub(r"\s", "", m.group(1)))); r["screencap"] = p
    return r


def kill_bridge(i):
    ps(f"Get-CimInstance Win32_Process -Filter \"Name='powershell.exe'\" | Where-Object {{ $_.CommandLine -match 'console_bridge.ps1.*-Id {i} ' }} | "
       "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")


def stop(i):
    rc = ctl("stop", sock(i))
    time.sleep(3)
    # stop only this Device's processes: the broker's command line carries ae-vm-<i>
    ps(f"$all = Get-CimInstance Win32_Process -Filter \"Name='crosvm.exe'\"; "
       f"$all | Where-Object {{ $_.CommandLine -match 'ae-vm-{i} ' }} | ForEach-Object {{ $b = $_.ProcessId; "
       "$all | Where-Object { $_.ParentProcessId -eq $b } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; "
       "Stop-Process -Id $b -Force -ErrorAction SilentlyContinue }")
    kill_bridge(i)
    return rc


cmd, args = sys.argv[1], sys.argv[2:]
res = {"cmd": cmd, "args": args}
if cmd == "mem":
    res.update(host_mem())
elif cmd == "template":
    i = int(args[0]); res["before"] = guard()
    make_dir(i); start(i); res["ready_s"] = wait_boot(i); port = 7100 + i
    n_apk = int(open(f"{RUN}/apk.size").read())
    gsh(port, "insmod /system_dlkm/lib/modules/virtio_balloon.ko 2>/dev/null; true")
    res["install"] = gsh(port, f"head -c {n_apk} /dev/block/vdb > /data/local/tmp/p.apk && chmod 644 /data/local/tmp/p.apk && pm install -r /data/local/tmp/p.apk; rm -f /data/local/tmp/p.apk", 300)
    gsh(port, "settings put global hide_error_dialogs 1; cmd connectivity airplane-mode enable; settings put global window_animation_scale 0; "
              "settings put global transition_animation_scale 0; settings put global animator_duration_scale 0; svc power stayon true; "
              "input keyevent KEYCODE_WAKEUP; wm dismiss-keyguard; settings put secure immersive_mode_confirmations confirmed")
    res["launch"] = gsh(port, "am start -W -n com.boltbetz.staging/com.boltbetz.MainActivity | grep -E \"Status|TotalTime\"", 120)
    time.sleep(30)
    res["app_pid"] = gsh(port, "pidof com.boltbetz.staging")
    res.update(shot(i, f"template-d{i}")); res["after"] = host_mem()
elif cmd == "snap":
    i, snap = int(args[0]), args[1]
    if os.path.exists(snap): shutil.rmtree(snap)
    gsh(7100 + i, "sync; echo 3 > /proc/sys/vm/drop_caches; true")  # flush guest dirty data before the pause
    t = time.time(); res["suspend"] = ctl("suspend", "--full", sock(i)); res["suspend_s"] = round(time.time() - t, 2)
    t = time.time(); res["snapshot"] = ctl("snapshot", "take", snap.replace("/", "\\"), sock(i)); res["snapshot_s"] = round(time.time() - t, 2)
    res["stop"] = stop(i)  # crosvm locks writable disks; the suspended VM wrote nothing after the pause
    os.makedirs(f"{snap}-disks", exist_ok=True)
    for n in WRITABLE: shutil.copyfile(f"{ddir(i)}/{n}.img", f"{snap}-disks/{n}.img")
elif cmd == "restore":
    i, snap = int(args[0]), args[1]; cow = "--cow" in args
    res["before"] = guard()
    make_dir(i, from_dir=f"{snap}-disks")
    t0 = time.time()
    start(i, f"--restore {snap}", {"AGENT_EMU_COW_RAM": "1"} if cow else None)
    # restore is done when the control socket answers (cmdline: resume waits for restore)
    while time.time() - t0 < 300:
        rc, out = ctl("resume", sock(i))
        if rc == 0: break
        time.sleep(0.2)
    res["restore_s"] = round(time.time() - t0, 2); res["resume"] = (rc, out)
    t = time.time()
    while time.time() - t < 120:
        try:
            res["console"] = gsh(7100 + i, "uptime; pidof com.boltbetz.staging", 10); break
        except OSError: time.sleep(1)
    res["console_s"] = round(time.time() - t0, 2)
elif cmd == "check":
    i, tag = int(args[0]), args[1]
    res["console"] = gsh(7100 + i, "uptime; pidof com.boltbetz.staging; dumpsys activity activities | grep -m1 topResumedActivity", 60)
    res.update(shot(i, tag))
    fb = f"{ddir(i)}/fb.bin"
    try:  # AGENT_EMU_FB_PIPE: ask crosvm to re-copy the current scanout first (flush copies can be a frame old)
        with open(PIPE_FB.format(i), "r+b", buffering=0) as fp:
            fp.write(b"x"); res["fb_pipe_seq"] = int.from_bytes(fp.read(8), "little")
    except OSError as e:
        res["fb_pipe"] = str(e)
    if os.path.exists(fb):  # host scanout (AGENT_EMU_FB): 64-byte header, then 32-bit pixels
        import struct
        from PIL import Image
        b = open(fb, "rb").read()
        seq, w, h, stride, fourcc = struct.unpack_from("<QIIII", b, 0)
        if w and h:
            Image.frombuffer("RGBA", (w, h), b[64:64 + h * stride], "raw", "RGBA", stride, 1).convert("RGB").save(f"{O}/{tag}-scanout.png")
            res["scanout"] = f"{O}/{tag}-scanout.png"; res["scanout_seq"] = seq
elif cmd == "clones":  # clones <snap> <settle_s> <id> [<id> ...]: cow restore each in turn, measure
    snap, settle, ids = args[0], int(args[1]), [int(a) for a in args[2:]]
    res["clones"] = []
    for i in ids:
        c = {"id": i, "before": guard()}
        r = subprocess.run([sys.executable, __file__, "restore", str(i), snap] + ([] if os.environ.get("AE_EAGER") else ["--cow"]), capture_output=True, text=True)
        c["restore"] = json.loads(r.stdout[r.stdout.index("{"):]) if "{" in r.stdout else r.stdout + r.stderr
        time.sleep(settle)
        c["after"] = host_mem()
        c["available_drop_mb"] = c["before"]["available_mb"] - c["after"]["available_mb"]
        c["committed_delta_mb"] = c["after"]["committed_mb"] - c["before"]["committed_mb"]
        log = subprocess.run(["iconv", "-f", "UTF-16LE", "-t", "UTF-8", f"{ddir(i)}/crosvm.log"], capture_output=True, text=True).stdout
        c["cow_log"] = [l[l.index("agent-emu"):] for l in log.splitlines() if "agent-emu: cow" in l][-3:]
        res["clones"].append(c)
        print(json.dumps(c), flush=True)
    for i in ids:
        r = subprocess.run([sys.executable, __file__, "check", str(i), f"{os.environ.get('AE_TAG', 'cow')}-d{i}"], capture_output=True, text=True, timeout=400)
        res[f"check{i}"] = json.loads(r.stdout[r.stdout.index("{"):]) if "{" in r.stdout else r.stdout + r.stderr
    res["final"] = host_mem()
elif cmd == "stop":
    res["stop"] = stop(int(args[0]))
print(json.dumps(res, indent=1))
json.dump(res, open(f"{O}/{cmd}-{'-'.join(a.replace('/', '_').replace(':', '') for a in args)[-60:]}-{int(time.time())}.json", "w"), indent=1)
