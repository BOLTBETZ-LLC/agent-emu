# Is the ~90 ms swipe -> first frame the pipeline or the app's gesture? One Device (slim5, 896 MB, AE_CPUS
# vCPUs, uncapped, Cuttlefish RIL stopped): HOME -> first frame (system path, no app logic; the app is brought
# back with am start between rounds), then carousel swipes of 480 px lasting 60, 150 and 300 ms. If the first
# frame scales with swipe duration, the app waits for a distance threshold; if it stays flat, it is a delay.
# usage: [AE_CPUS=2] [AE_CROSVM_DIR=crosvm-diet] python gesture_smoke.py [out_dir]
import json, os, socket, subprocess, sys, threading, time

D = os.path.dirname(os.path.abspath(__file__)); EXE = f"{D}/target/release/agent-emud.exe"; W = "C:/dev/agent-emu-work"
OUT = sys.argv[1] if len(sys.argv) > 1 else f"{W}/results/gesture"; os.makedirs(OUT, exist_ok=True)
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

def stats(ff):
    xs = sorted(x for x in ff if x is not None)
    return {"p50": xs[len(xs) // 2] if xs else None, "p95": xs[min(len(xs) - 1, int(len(xs) * .95))] if xs else None,
            "min": xs[0] if xs else None, "frames": len(xs), "n": len(ff), "raw": ff}

dmn = subprocess.Popen([EXE], env=dict(os.environ, AE_ALLOW_OTHER_CROSVM=os.environ.get("AE_ALLOW_OTHER_CROSVM", "1")),
                       stdout=open(f"{OUT}/daemon.log", "w"), stderr=subprocess.STDOUT)
res = {"cpus": os.environ.get("AE_CPUS", "2")}
try:
    time.sleep(1)
    res["ready_s"] = round(call("start", image="slim5", mem=896, net=True)["ready_s"], 1); log("ready", res["ready_s"])
    res["install"] = sh(INSTALL, 300)
    sh("setprop ctl.stop vendor.ril-daemon")
    sh(f"am start -W -n {ACT}"); time.sleep(20)
    ff = []
    for _ in range(12):
        ff.append(call("key", name="home", deadline_ms=1500).get("first_frame_ms")); time.sleep(0.5)
        sh(f"am start -W -n {ACT} >/dev/null"); time.sleep(1)
    res["home"] = stats(ff); log("home", {k: v for k, v in res["home"].items() if k != "raw"})
    for ms in (60, 150, 300):
        ff = []
        for fwd in ([True] * 3 + [False] * 3) * 2:
            x1, x2 = (600, 120) if fwd else (120, 600)
            ff.append(call("swipe", x1=x1, y1=150, x2=x2, y2=150, ms=ms, deadline_ms=1500).get("first_frame_ms")); time.sleep(0.5)
        res[f"swipe_{ms}ms"] = stats(ff); log(f"swipe {ms} ms", {k: v for k, v in res[f"swipe_{ms}ms"].items() if k != "raw"})
    res["app"] = sh(f"pidof {PKG}")
finally:
    try: call("stop"); log("stopped d0")
    except Exception as e: log("stop failed", e)
    done.set(); dmn.terminate(); json.dump(res, open(f"{OUT}/result.json", "w"), indent=1)
