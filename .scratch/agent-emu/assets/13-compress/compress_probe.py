# Honest RAM of one squeezed Device: working set plus what the caps pushed into the Memory Compression store.
# Boot d0 (896 MB, slim3 pmem), open the proof app, squeeze (caps 250/16, balloon 0), then two 6-minute phases
# with 1 tap every 10 s and a sample every 30 s: A = caps only, B = caps + MEMORY_PRIORITY_VERY_LOW + EcoQoS on
# the Device's crosvm processes. Samples: Device WS and private commit, Memory Compression WS, Committed, pagefile
# in use, Modified list, Available, plus other workers' crosvm brokers (boots/stops change the global numbers).
# Host Available is checked every 30 s; the daemon stops a boot under 4000 MB. Stops d0.
# usage: python compress_probe.py <out_dir> [phase_s]
import base64, ctypes, ctypes.wintypes as wt, json, os, socket, subprocess, sys, time

EXE = "C:/dev/agent-emu/daemon/target/release/agent-emud.exe"; W = "C:/dev/agent-emu-work"; PKG = "com.boltbetz.staging"
OUT = sys.argv[1]; PHASE = int(sys.argv[2]) if len(sys.argv) > 2 else 360; os.makedirs(OUT, exist_ok=True)
INSTALL = (f"head -c {int(open(f'{W}/run/apk.size').read())} /dev/block/vdb > /data/local/tmp/p.apk && "
           "chmod 644 /data/local/tmp/p.apk && pm install -r /data/local/tmp/p.apk; rm -f /data/local/tmp/p.apk")
k32 = ctypes.WinDLL("kernel32", use_last_error=True); k32.OpenProcess.restype = wt.HANDLE
k32.SetProcessInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
k32.GetProcessInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]

def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)

def call(c, **kw):
    s = socket.create_connection(("127.0.0.1", 7400)); f = s.makefile("rb")
    s.sendall((json.dumps({"id": 1, "call": c, "device": "d0", **kw}) + "\n").encode()); r = json.loads(f.readline()); s.close()
    if not r.get("ok"): raise RuntimeError(f"{c}: {r.get('error')}")
    return r

PS = (r"$c=(Get-Counter '\Memory\Available MBytes','\Memory\Committed Bytes','\Memory\Modified Page List Bytes',"
      r"'\Paging File(_total)\% Usage').CounterSamples; $mc=(Get-Process 'Memory Compression').WorkingSet64; "
      "$pf=(Get-CimInstance Win32_PageFileUsage | Measure-Object CurrentUsage -Sum).Sum; "
      "$cv=Get-CimInstance Win32_Process -Filter \"Name='crosvm.exe'\"; "
      "$b=($cv | ? { $_.CommandLine -match ' run-mp ' } | % { $_.ProcessId }) -join ','; "
      "$pv=($cv | % { '{0}:{1}' -f $_.ProcessId,$_.PageFileUsage }) -join ','; "
      "'{0}|{1}|{2}|{3}|{4}|{5}|{6}|{7}' -f [int]$c[0].CookedValue,[int64]$c[1].CookedValue,[int64]$c[2].CookedValue,$c[3].CookedValue,$mc,$pf,$b,$pv")

def host():
    o = subprocess.run(["powershell", "-NoProfile", "-Command", PS], capture_output=True, text=True).stdout.strip().split("|")
    priv = {int(p): int(k) for p, k in (x.split(":") for x in o[7].split(",") if ":" in x)}  # KB
    return {"available_mb": int(o[0]), "committed_mb": int(o[1]) >> 20, "modified_list_mb": int(o[2]) >> 20,
            "pagefile_pct": round(float(o[3]), 2), "pagefile_used_mb": int(o[5] or 0), "compression_ws_mb": int(o[4]) >> 20,
            "brokers": sorted(int(b) for b in o[6].split(",") if b), "priv": priv}

def sample(tag, pids, my_broker):
    h = host(); m = call("memory")
    s = {"t": time.strftime("%H:%M:%S"), "phase": tag, "device_ws_mb": m["ws_mb"],
         "device_private_commit_mb": sum(h["priv"].get(p, 0) for p in pids) >> 10,
         **{k: v for k, v in h.items() if k not in ("priv", "brokers")}, "other_brokers": [b for b in h["brokers"] if b != my_broker]}
    log(tag, {k: v for k, v in s.items() if k not in ("t", "phase")}); return s

def lower(pids):
    out = {}
    for p in pids:
        h = k32.OpenProcess(0x0200 | 0x1000, False, p)  # PROCESS_SET_INFORMATION | QUERY_LIMITED
        prio = wt.ULONG(1)  # MEMORY_PRIORITY_VERY_LOW
        a = k32.SetProcessInformation(h, 0, ctypes.byref(prio), 4)  # ProcessMemoryPriority
        thr = (wt.ULONG * 3)(1, 1, 1)  # Version 1, ControlMask = StateMask = EXECUTION_SPEED (EcoQoS)
        b = k32.SetProcessInformation(h, 4, ctypes.byref(thr), 12)  # ProcessPowerThrottling
        got = wt.ULONG(0); k32.GetProcessInformation(h, 0, ctypes.byref(got), 4); k32.CloseHandle(h)
        out[p] = {"mem_prio_set": bool(a), "ecoqos_set": bool(b), "mem_prio_now": got.value}
    return out

def phase(tag, pids, broker, secs):
    rows, t_end, n = [], time.time() + secs, 0
    while time.time() < t_end:
        if n % 3 == 0: rows.append(sample(tag, pids, broker))
        call("tap", x=360, y=780, screenshot=False); n += 1; time.sleep(10)
    rows.append(sample(tag, pids, broker)); return rows

env = dict(os.environ, AE_ALLOW_OTHER_CROSVM="1")
dmn = subprocess.Popen([EXE], env=env, stdout=open(f"{OUT}/daemon.log", "w"), stderr=subprocess.STDOUT)
res = {"phase_s": PHASE}
try:
    time.sleep(1); res["ready_s"] = round(call("start")["ready_s"], 1); log("ready", res["ready_s"])
    call("shell", cmd=INSTALL, timeout_s=300); call("shell", cmd=f"am start -W -n {PKG}/com.boltbetz.MainActivity"); time.sleep(10)
    mem = call("memory"); pids = [p["pid"] for p in mem["processes"]]
    broker = [b for b in host()["brokers"] if b in pids]; broker = broker[0] if broker else None
    rows = [sample("open", pids, broker)]
    sq = call("squeeze", balloon_mb=0, cap_main_mb=250, cap_helper_mb=16)
    res["squeeze"] = {k: sq[k] for k in ("ws_before_mb", "ws_after_mb", "squeeze_ms")}; log("squeeze", res["squeeze"])
    rows += phase("A_caps", pids, broker, PHASE)
    res["lowered"] = lower(pids); log("lowered", res["lowered"])
    rows += phase("B_caps_lowprio", pids, broker, PHASE)
    res["rows"] = rows; res["app_pid"] = call("shell", cmd=f"pidof {PKG}")["out"]
    f = call("screenshot")["frame"]; open(f"{OUT}/end.jpg", "wb").write(base64.b64decode(f["jpeg"]))
finally:
    try: call("stop"); log("stopped d0")
    except Exception as e: log("stop failed", e)
    dmn.terminate(); json.dump(res, open(f"{OUT}/result.json", "w"), indent=1)
