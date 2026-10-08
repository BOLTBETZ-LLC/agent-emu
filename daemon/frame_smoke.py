# Input -> first frame on targets that redraw with no app logic, through agent-emud on one Device:
#   net on:  the proof app's Welcome carousel, stepped one page at a time and back (the page is read from
#            the dot indicator in each returned frame, so a swipe never pushes against an edge);
#   net off: the app shows the offline dialog, so HOME -> first frame (the app is brought back with
#            `am start` between rounds, which is not timed).
# 20 inputs each, uncapped and then capped (`squeeze` once 45 s have passed since launch, the proven
# settle). Idle frames over 5 s are counted first: a pager that animates on its own would fake first frames. Host Available is logged every 30 s; the daemon stops a boot
# under 4000 MB. Stops d0.
# usage: [AE_SMOKE_IMAGE=slim4] [AE_SMOKE_MEM=704] [AE_SMOKE_NET=0] [AE_CAP_MAIN=200] [AE_CROSVM_DIR=crosvm-diet] python frame_smoke.py [out_dir]
import base64, io, json, os, socket, subprocess, sys, threading, time
from PIL import Image

D = os.path.dirname(os.path.abspath(__file__)); EXE = f"{D}/target/release/agent-emud.exe"; W = "C:/dev/agent-emu-work"
OUT = sys.argv[1] if len(sys.argv) > 1 else f"{W}/results/frame"; os.makedirs(OUT, exist_ok=True)
PKG, ACT = "com.boltbetz.staging", "com.boltbetz.staging/com.boltbetz.MainActivity"
IMAGE = os.environ.get("AE_SMOKE_IMAGE", "slim4"); NET = os.environ.get("AE_SMOKE_NET", "1") != "0"
RUN = {"slim3": "run", "slim4": "run-slim4", "slim5": "run-slim5"}[IMAGE]
INSTALL = (f"head -c {int(open(f'{W}/{RUN}/apk.size').read())} /dev/block/vdb > /data/local/tmp/p.apk && "
           "chmod 644 /data/local/tmp/p.apk && pm install -r /data/local/tmp/p.apk; rm -f /data/local/tmp/p.apk")
CAROUSEL_Y = 150  # Welcome carousel image band (720x1080 frame)
DOTS_Y = 318      # page indicator row: the current page is a dark pill, the others grey dots
PAGES = 4

def page_of(frame):
    """Carousel page from the indicator: the dark pill's centre is at 312 + 32 * page (720 px wide frame)."""
    img = Image.open(io.BytesIO(base64.b64decode(frame["jpeg"]))).convert("L")
    sx = img.width / 720; y = int(DOTS_Y * img.height / 1080)
    xs = [x for x in range(int(250 * sx), int(470 * sx)) if img.getpixel((x, y)) < 80]
    if not xs: return None
    return max(0, min(PAGES - 1, round((sum(xs) / len(xs) / sx - 312) / 32)))

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
    ff, kinds, pages = [], [], []
    page = page_of(call("screenshot")["frame"]) if NET else None
    for i in range(n):
        if NET:  # one page forward or back, turning around at either end
            fwd = page is None or page < PAGES - 1 if i == 0 or page in (0, PAGES - 1) else fwd
            x1, x2 = (600, 120) if fwd else (120, 600)
            r = call("swipe", x1=x1, y1=CAROUSEL_Y, x2=x2, y2=CAROUSEL_Y, ms=150, deadline_ms=1500); kinds.append("swipe")
            page = page_of(r["frame"]); pages.append(page)
        else:
            r = call("key", name="home", deadline_ms=1500); kinds.append("home")
            call("shell", cmd=f"am start -W -n {ACT} >/dev/null"); time.sleep(0.5)
        ff.append(r.get("first_frame_ms")); time.sleep(0.4)
    return {"input": kinds[0], **pct(ff), "no_frame": sum(x is None for x in ff), "raw": ff, "pages": pages}

def idle_frames(secs=5):
    g0 = call("screenshot")["frame"]["generation"]; time.sleep(secs)
    return call("screenshot")["frame"]["generation"] - g0

dmn = subprocess.Popen([EXE], stdout=open(f"{OUT}/daemon.log", "w"), stderr=subprocess.STDOUT)
res = {"image": IMAGE, "net": NET}
try:
    time.sleep(1)
    opts = {"image": IMAGE, "net": NET, **({"mem": int(os.environ["AE_SMOKE_MEM"])} if os.environ.get("AE_SMOKE_MEM") else {})}
    res["ready_s"] = round(call("start", **opts)["ready_s"], 1); log("ready", res["ready_s"])
    res["install"] = call("shell", cmd=INSTALL, timeout_s=300)["out"]
    call("shell", cmd=f"am start -W -n {ACT}"); t_launch = time.time(); time.sleep(8)
    open(f"{OUT}/first-screen.jpg", "wb").write(base64.b64decode(call("screenshot")["frame"]["jpeg"]))
    res["idle_frames_5s_uncapped"] = idle_frames()
    res["before"] = rounds(); log("before", {k: v for k, v in res["before"].items() if k != "raw"}, "idle frames/5 s", res["idle_frames_5s_uncapped"])
    time.sleep(max(0, 45 - (time.time() - t_launch)))
    sq = call("squeeze", cap_main_mb=int(os.environ.get("AE_CAP_MAIN", "250")), cap_helper_mb=int(os.environ.get("AE_CAP_HELPER", "16"))); res["squeeze"] = {k: sq[k] for k in ("ws_before_mb", "ws_after_mb")}
    time.sleep(30)
    res["idle_frames_5s_capped"] = idle_frames()
    res["after"] = rounds(); log("after", {k: v for k, v in res["after"].items() if k != "raw"}, "idle frames/5 s", res["idle_frames_5s_capped"])
    open(f"{OUT}/end.jpg", "wb").write(base64.b64decode(call("screenshot")["frame"]["jpeg"]))
    res["app_pid"] = call("shell", cmd=f"pidof {PKG}")["out"]
finally:
    try: call("stop"); log("stopped d0")
    except Exception as e: log("stop failed", e)
    done.set(); dmn.terminate(); json.dump(res, open(f"{OUT}/result.json", "w"), indent=1)
