# Input -> first frame on targets that redraw with no app logic, through agent-emud on one Device:
#   net on:  the proof app's Welcome carousel, swiped left and right (the pager animates on its own);
#   net off: the app shows the offline dialog, so HOME -> first frame (the app is brought back with
#            `am start` between rounds, which is not timed).
# 20 inputs each, before and after `squeeze`. Host Available is logged every 30 s; the daemon stops a boot
# under 4000 MB. Stops d0.
# usage: [AE_SMOKE_IMAGE=slim4] [AE_SMOKE_MEM=704] [AE_SMOKE_NET=0] [AE_CAP_MAIN=200] [AE_CROSVM_DIR=crosvm-diet] python frame_smoke.py [out_dir]
import base64, json, os, socket, subprocess, sys, threading, time

D = os.path.dirname(os.path.abspath(__file__)); EXE = f"{D}/target/release/agent-emud.exe"; W = "C:/dev/agent-emu-work"
OUT = sys.argv[1] if len(sys.argv) > 1 else f"{W}/results/frame"; os.makedirs(OUT, exist_ok=True)
PKG, ACT = "com.boltbetz.staging", "com.boltbetz.staging/com.boltbetz.MainActivity"
IMAGE = os.environ.get("AE_SMOKE_IMAGE", "slim4"); NET = os.environ.get("AE_SMOKE_NET", "1") != "0"
RUN = {"slim3": "run", "slim4": "run-slim4", "slim5": "run-slim5"}[IMAGE]
INSTALL = (f"head -c {int(open(f'{W}/{RUN}/apk.size').read())} /dev/block/vdb > /data/local/tmp/p.apk && "
           "chmod 644 /data/local/tmp/p.apk && pm install -r /data/local/tmp/p.apk; rm -f /data/local/tmp/p.apk")
CAROUSEL_Y = 150  # Welcome carousel image band (720x1080 frame)

def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)

def call(c, **kw):
    s = socket.create_connection(("127.0.0.1", 7400)); f = s.makefile("rb")
    s.sendall((json.dumps({"id": 1, "call": c, "device": "d0", **kw}) + "\n").encode()); r = json.loads(f.readline()); s.close()
    if not r.get("ok"): raise RuntimeError(f"{c}: {r.get('error')}")
    return r

def avail():
    o = subprocess.run(["powershell", "-NoProfile", "-Command", r"(Get-Counter '\Memory\Available MBytes').CounterSamples[0].CookedValue"],
                       capture_output=True, text=True).stdout.strip()
    return int(float(o or 0))

done = threading.Event()
def watch():
    while not done.wait(30): log("host Available", avail(), "MB")
threading.Thread(target=watch, daemon=True).start()

def pct(xs):
    xs = sorted(x for x in xs if x is not None)
    return {"n": len(xs), "p50": xs[len(xs) // 2] if xs else None, "p95": xs[min(len(xs) - 1, int(len(xs) * .95))] if xs else None}

def rounds(n=20):
    ff, kinds = [], []
    for i in range(n):
        if NET:  # alternate left and right so the pager always has a page to go to
            x1, x2 = (600, 120) if i % 2 == 0 else (120, 600)
            r = call("swipe", x1=x1, y1=CAROUSEL_Y, x2=x2, y2=CAROUSEL_Y, ms=150, deadline_ms=1500); kinds.append("swipe")
        else:
            r = call("key", name="home", deadline_ms=1500); kinds.append("home")
            call("shell", cmd=f"am start -W -n {ACT} >/dev/null"); time.sleep(0.5)
        ff.append(r.get("first_frame_ms")); time.sleep(0.4)
    return {"input": kinds[0], **pct(ff), "no_frame": sum(x is None for x in ff), "raw": ff}

dmn = subprocess.Popen([EXE], stdout=open(f"{OUT}/daemon.log", "w"), stderr=subprocess.STDOUT)
res = {"image": IMAGE, "net": NET}
try:
    time.sleep(1)
    opts = {"image": IMAGE, "net": NET, **({"mem": int(os.environ["AE_SMOKE_MEM"])} if os.environ.get("AE_SMOKE_MEM") else {})}
    res["ready_s"] = round(call("start", **opts)["ready_s"], 1); log("ready", res["ready_s"])
    res["install"] = call("shell", cmd=INSTALL, timeout_s=300)["out"]
    call("shell", cmd=f"am start -W -n {ACT}"); time.sleep(8)
    open(f"{OUT}/first-screen.jpg", "wb").write(base64.b64decode(call("screenshot")["frame"]["jpeg"]))
    res["before"] = rounds(); log("before", {k: v for k, v in res["before"].items() if k != "raw"})
    sq = call("squeeze", cap_main_mb=int(os.environ.get("AE_CAP_MAIN", "250")), cap_helper_mb=int(os.environ.get("AE_CAP_HELPER", "16"))); res["squeeze"] = {k: sq[k] for k in ("ws_before_mb", "ws_after_mb")}
    time.sleep(30)
    res["after"] = rounds(); log("after", {k: v for k, v in res["after"].items() if k != "raw"})
    open(f"{OUT}/end.jpg", "wb").write(base64.b64decode(call("screenshot")["frame"]["jpeg"]))
    res["app_pid"] = call("shell", cmd=f"pidof {PKG}")["out"]
finally:
    try: call("stop"); log("stopped d0")
    except Exception as e: log("stop failed", e)
    done.set(); dmn.terminate(); json.dump(res, open(f"{OUT}/result.json", "w"), indent=1)
