# Device-setup soak for agent-emud: boot d0 with the daemon defaults (image, memory) and AE_SMOKE_CPUS vCPUs,
# install and open the proof app, check the RIL is stopped, measure HOME -> first frame (2 warm-ups, then 20),
# then soak for AE_SOAK_S seconds: once a minute a HOME + relaunch, system_server pid, app pid, crash_events
# by process, host Available. Ends with a second HOME measurement and a screenshot. Stops d0.
# usage: [AE_SMOKE_IMAGE=slim3n] [AE_SMOKE_CPUS=2] [AE_SOAK_S=900] python soak_smoke.py [out_dir]
import base64, json, os, socket, subprocess, sys, threading, time

D = os.path.dirname(os.path.abspath(__file__)); EXE = f"{D}/target/release/agent-emud.exe"; W = "C:/dev/agent-emu-work"
OUT = sys.argv[1] if len(sys.argv) > 1 else f"{W}/results/soak"; os.makedirs(OUT, exist_ok=True)
CPUS, SOAK = int(os.environ.get("AE_SMOKE_CPUS", "2")), int(os.environ.get("AE_SOAK_S", "900"))
PKG, ACT = "com.boltbetz.staging", "com.boltbetz.staging/com.boltbetz.MainActivity"
INSTALL = (f"head -c {int(open(f'{W}/run/apk.size').read())} /dev/block/vdb > /data/local/tmp/p.apk && "
           "chmod 644 /data/local/tmp/p.apk && pm install -r /data/local/tmp/p.apk; rm -f /data/local/tmp/p.apk")

def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)

def call(c, **kw):
    s = socket.create_connection(("127.0.0.1", 7400)); f = s.makefile("rb")
    s.sendall((json.dumps({"id": 1, "call": c, "device": "d0", **kw}) + "\n").encode()); r = json.loads(f.readline()); s.close()
    if not r.get("ok"): raise RuntimeError(f"{c}: {r.get('error')}")
    return r

def sh(cmd, t=120): return call("shell", cmd=cmd, timeout_s=t)["out"]

def avail():
    o = subprocess.run(["powershell", "-NoProfile", "-Command", r"(Get-Counter '\Memory\Available MBytes').CounterSamples[0].CookedValue"],
                       capture_output=True, text=True).stdout.strip()
    return int(float(o or 0))

done = threading.Event()
def watch():
    while not done.wait(30): log("host Available", avail(), "MB")
threading.Thread(target=watch, daemon=True).start()

def home_series(n=20, warm=2):
    ff = []
    for i in range(warm + n):
        r = call("key", name="home", deadline_ms=1500)
        if i >= warm: ff.append(r.get("first_frame_ms"))
        time.sleep(0.5); sh(f"am start -W -n {ACT} >/dev/null"); time.sleep(1)
    xs = sorted(x for x in ff if x is not None)
    return {"p50": xs[len(xs) // 2] if xs else None, "p95": xs[min(len(xs) - 1, int(len(xs) * .95))] if xs else None,
            "frames": len(xs), "n": n, "raw": ff}

dmn = subprocess.Popen([EXE], env=dict(os.environ, AE_ALLOW_OTHER_CROSVM=os.environ.get("AE_ALLOW_OTHER_CROSVM", "1")),
                       stdout=open(f"{OUT}/daemon.log", "w"), stderr=subprocess.STDOUT)
res = {"cpus": CPUS, "soak_s": SOAK}
try:
    time.sleep(1)
    res["ready_s"] = round(call("start", cpus=CPUS, **({"image": os.environ["AE_SMOKE_IMAGE"]} if os.environ.get("AE_SMOKE_IMAGE") else {}))["ready_s"], 1); log("ready", res["ready_s"], "cpus", CPUS)
    res["install"] = sh(INSTALL, 300)
    sh(f"am start -W -n {ACT}"); time.sleep(20)
    res["ril"] = sh("getprop init.svc.vendor.ril-daemon; p=$(pidof libcuttlefish-rild); echo pid=$p; "
                    "for t in $(ls /proc/$p/task); do awk '{print $19}' /proc/$p/task/$t/stat; done | sort | uniq -c")
    res["rild_cpu"] = sh("top -H -b -d 1 -n 3 -o TID,%CPU,CMD,NAME | grep -i ril | sort -k2 -nr | head -4")
    sh(f"dumpsys gfxinfo {PKG} reset >/dev/null")
    for fwd in ([True] * 3 + [False] * 3) * 4:
        x1, x2 = (600, 120) if fwd else (120, 600)
        call("swipe", x1=x1, y1=150, x2=x2, y2=150, ms=150, deadline_ms=1500, screenshot=False); time.sleep(0.5)
    g = sh(f"dumpsys gfxinfo {PKG}")
    res["hwui_swipes"] = [l.strip() for l in g.splitlines() if l.strip().startswith(("50th percentile", "95th percentile", "Janky frames:"))][:3]
    log("hwui during swipes", res["hwui_swipes"], "rild cpu", " | ".join(res["rild_cpu"].splitlines()))
    ss0, ev0 = sh("pidof system_server"), call("crash_events")["last"]
    res["system_server_start"] = ss0; log("ril", res["ril"].replace("\n", " "), "system_server", ss0, "events so far", ev0)
    res["home_start"] = home_series(); log("home start", {k: v for k, v in res["home_start"].items() if k != "raw"})
    rows, t_end = [], time.time() + SOAK
    while time.time() < t_end:
        time.sleep(60)
        call("key", name="home", screenshot=False); time.sleep(1); sh(f"am start -W -n {ACT} >/dev/null")
        ev = call("crash_events", after=ev0)["events"]
        row = {"t": time.strftime("%H:%M:%S"), "system_server": sh("pidof system_server"), "app": sh(f"pidof {PKG}"),
               "events_by_process": {p: sum(e.get("process") == p for e in ev) for p in {e.get("process") for e in ev}},
               "available_mb": avail()}
        rows.append(row); log("soak", row)
    res["soak"] = rows
    res["system_server_end"] = sh("pidof system_server")
    res["system_server_unchanged"] = res["system_server_end"] == ss0 and all(r["system_server"] == ss0 for r in rows)
    res["app_alive_all"] = all(r["app"] for r in rows) and bool(sh(f"pidof {PKG}"))
    res["home_end"] = home_series(); log("home end", {k: v for k, v in res["home_end"].items() if k != "raw"})
    open(f"{OUT}/end.jpg", "wb").write(base64.b64decode(call("screenshot")["frame"]["jpeg"]))
    log("system_server unchanged", res["system_server_unchanged"], "app alive", res["app_alive_all"])
finally:
    try: call("stop"); log("stopped d0")
    except Exception as e: log("stop failed", e)
    done.set(); dmn.terminate(); json.dump(res, open(f"{OUT}/result.json", "w"), indent=1)
