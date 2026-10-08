# Cheap levers for swipe -> first frame, one Device (slim5, 896 MB, AE_CPUS vCPUs, uncapped), applied one
# after another on the same boot, 24 carousel swipes each (3 pages forward, 3 back):
#   base     as the daemon sets it up;
#   rild     + stop the Cuttlefish RIL daemon (it spins ~32% of a vCPU retrying a modem that isn't there);
#   hwc      + SurfaceFlinger 1008 back to 0 (HW overlays on: no GPU composition; stale pixels can return);
#   skiavk   + debug.hwui.renderer=skiavk (HWUI on the guest Vulkan instead of GL over ANGLE; app restarted).
# Each step: daemon first-frame p50/p95, gfxinfo frame percentiles, system_server pid (must not change).
# usage: [AE_CPUS=2] [AE_CROSVM_DIR=crosvm-diet] python lever_smoke.py [out_dir]
import base64, json, os, socket, subprocess, sys, threading, time

D = os.path.dirname(os.path.abspath(__file__)); EXE = f"{D}/target/release/agent-emud.exe"; W = "C:/dev/agent-emu-work"
OUT = sys.argv[1] if len(sys.argv) > 1 else f"{W}/results/levers"; os.makedirs(OUT, exist_ok=True)
PKG, ACT = "com.boltbetz.staging", "com.boltbetz.staging/com.boltbetz.MainActivity"
INSTALL = (f"head -c {int(open(f'{W}/run-slim5/apk.size').read())} /dev/block/vdb > /data/local/tmp/p.apk && "
           "chmod 644 /data/local/tmp/p.apk && pm install -r /data/local/tmp/p.apk; rm -f /data/local/tmp/p.apk")

def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)

def call(c, **kw):
    s = socket.create_connection(("127.0.0.1", 7400)); f = s.makefile("rb")
    s.sendall((json.dumps({"id": 1, "call": c, "device": "d0", **kw}) + "\n").encode()); r = json.loads(f.readline()); s.close()
    if not r.get("ok"): raise RuntimeError(f"{c}: {r.get('error')}")
    return r

def sh(cmd, t=120): return call("shell", cmd=cmd, timeout_s=t)["out"]

done = threading.Event()
def watch():
    while not done.wait(30):
        a = subprocess.run(["powershell", "-NoProfile", "-Command", r"(Get-Counter '\Memory\Available MBytes').CounterSamples[0].CookedValue"],
                           capture_output=True, text=True).stdout.strip()
        log("host Available", a, "MB")
threading.Thread(target=watch, daemon=True).start()

def q(xs, p):
    xs = sorted(xs); return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else None

def measure(name):
    sh(f"dumpsys gfxinfo {PKG} reset >/dev/null")
    ff = []
    for fwd in ([True] * 3 + [False] * 3) * 4:
        x1, x2 = (600, 120) if fwd else (120, 600)
        ff.append(call("swipe", x1=x1, y1=150, x2=x2, y2=150, ms=150, deadline_ms=1500).get("first_frame_ms")); time.sleep(0.4)
    g = sh(f"dumpsys gfxinfo {PKG}")
    pick = lambda key: next((l.split(":")[1].strip() for l in g.splitlines() if l.strip().startswith(key)), None)
    xs = [x for x in ff if x is not None]
    r = {"first_frame_p50": q(xs, .5), "first_frame_p95": q(xs, .95), "frames": len(xs), "n": len(ff), "raw": ff,
         "hwui_p50": pick("50th percentile"), "hwui_p95": pick("95th percentile"), "janky": pick("Janky frames:"),
         "system_server": sh("pidof system_server"), "app": sh(f"pidof {PKG}")}
    open(f"{OUT}/{name}.jpg", "wb").write(base64.b64decode(call("screenshot")["frame"]["jpeg"]))
    log(name, {k: v for k, v in r.items() if k != "raw"}); return r

dmn = subprocess.Popen([EXE], env=dict(os.environ, AE_ALLOW_OTHER_CROSVM=os.environ.get("AE_ALLOW_OTHER_CROSVM", "1")),
                       stdout=open(f"{OUT}/daemon.log", "w"), stderr=subprocess.STDOUT)
res = {"cpus": os.environ.get("AE_CPUS", "2")}
try:
    time.sleep(1)
    res["ready_s"] = round(call("start", image="slim5", mem=896, net=True)["ready_s"], 1); log("ready", res["ready_s"])
    res["install"] = sh(INSTALL, 300)
    sh(f"am start -W -n {ACT}"); time.sleep(20)
    res["base"] = measure("base")
    svc = sh("getprop | grep -E 'init.svc.*ril' | head -3")
    res["rild_services"] = svc
    for name in [l.split("]")[0].strip("[").replace("init.svc.", "") for l in svc.splitlines() if "running" in l]:
        sh(f"setprop ctl.stop {name}")
    time.sleep(3); res["rild"] = measure("rild")
    sh("service call SurfaceFlinger 1008 i32 0 >/dev/null"); time.sleep(2); res["hwc"] = measure("hwc")
    sh(f"setprop debug.hwui.renderer skiavk; am force-stop {PKG}; am start -W -n {ACT} >/dev/null"); time.sleep(20)
    res["skiavk_check"] = sh(f"dumpsys gfxinfo {PKG} | grep -iE 'Pipeline|renderer' | head -3")
    res["skiavk"] = measure("skiavk")
finally:
    try: call("stop"); log("stopped d0")
    except Exception as e: log("stop failed", e)
    done.set(); dmn.terminate(); json.dump(res, open(f"{OUT}/result.json", "w"), indent=1)
