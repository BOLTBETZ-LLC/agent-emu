# Fast-path check for agent-emud: boot 1 Device, prove virtio-input changes the screen (HOME, shade
# swipe), then time 10 screenshots, 10 taps with a settled frame and 10 tap acks. Stops the Device.
# usage: python fast_smoke.py [out_dir]
import base64, json, os, re, socket, statistics, subprocess, sys, time

D = os.path.dirname(os.path.abspath(__file__))
EXE = f"{D}/target/release/agent-emud.exe"
W = "C:/dev/agent-emu-work"

def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)

class Client:
    def __init__(s):
        s.sock = socket.create_connection(("127.0.0.1", 7400)); s.f = s.sock.makefile("rb"); s.n = 0
        s.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    def call(s, call, **kw):
        s.n += 1
        t = time.perf_counter()
        s.sock.sendall((json.dumps({"id": s.n, "call": call, "device": "d0", **kw}) + "\n").encode())
        r = json.loads(s.f.readline()); r["rt_ms"] = (time.perf_counter() - t) * 1000
        if not r.get("ok"): raise RuntimeError(f"{call}: {r.get('error')}")
        return r

def save_to(out, frame, name):
    p = f"{out}/{name}.jpg"; open(p, "wb").write(base64.b64decode(frame["jpeg"])); return p

def pct(xs):
    if not xs: return None
    xs = sorted(xs); return {"p50": round(statistics.median(xs)), "p95": round(xs[min(len(xs) - 1, int(0.95 * len(xs)))]),
                             "min": round(xs[0]), "max": round(xs[-1])}

OUT = sys.argv[1] if len(sys.argv) > 1 else f"{W}/results/daemon-fast"
os.makedirs(OUT, exist_ok=True)
save = lambda f, n: save_to(OUT, f, n)

daemon = subprocess.Popen([EXE], stdout=open(f"{OUT}/daemon.log", "w"), stderr=subprocess.STDOUT)
res = {}
try:
    time.sleep(1); c = Client()
    r = c.call("start"); res["boot"] = {"ready_s": round(r["ready_s"], 1), "available_mb_before": r["available_mb_before"]}
    res["status"] = c.call("status")["devices"]; log("ready", res["boot"], res["status"])
    c.call("lease")
    time.sleep(5)
    res["input_devices"] = c.call("shell", cmd="grep Name= /proc/bus/input/devices; wm size; getevent -lp | grep -A1 -E \"POSITION_(X|Y)\"")["out"]
    n_apk = int(open(f"{W}/run/apk.size").read())
    c.call("shell", cmd=f"head -c {n_apk} /dev/block/vdb > /data/local/tmp/p.apk && chmod 644 /data/local/tmp/p.apk && "
                        "pm install -r /data/local/tmp/p.apk; rm -f /data/local/tmp/p.apk", timeout_s=300)
    APP = "am start -W -n com.boltbetz.staging/com.boltbetz.MainActivity"
    c.call("shell", cmd=APP)
    btn, t_end = None, time.time() + 90
    while time.time() < t_end and not btn:
        r = c.call("ui_tree")
        m = re.search(r'<node[^>]*text="Try Again"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', r["xml"], re.I)
        if m:
            x1, y1, x2, y2 = map(int, m.groups()); btn = ((x1 + x2) // 2, (y1 + y2) // 2)
        else:
            time.sleep(2)
    if not btn:
        log("Try Again not found in ui_tree; using its last known spot"); btn = (390, 779)
    res["try_again_at"] = btn
    c.call("shell", cmd="nohup getevent -lt > /data/local/tmp/ge.txt 2>&1 &")
    res["shots"] = {"0-app": save(c.call("screenshot")["frame"], "0-app")}
    for name, call, kw in [("1-home", "key", {"name": "home"}),
                           ("2-swipe-up", "swipe", {"x1": 360, "y1": 1000, "x2": 360, "y2": 300, "ms": 200}),
                           ("3-back", "key", {"name": "back"}),
                           ("4-tap-icon", "tap", {"x": 446, "y": 925})]:
        r = c.call(call, **kw)
        res["shots"][name] = save(r["frame"], name)
        res["shots"][name + "-screencap"] = save(c.call("screenshot", via="console")["frame"], name + "-screencap")
        res.setdefault("proof", {})[name] = {k: r[k] for k in ("input_via", "input_ms", "first_frame_ms", "settled_ms", "settled", "frames")}
        log(name, res["proof"][name])
    time.sleep(3)
    res["shots"]["4b-tap-icon-3s"] = save(c.call("screenshot")["frame"], "4b-tap-icon-3s")
    res["shots"]["4b-tap-icon-3s-screencap"] = save(c.call("screenshot", via="console")["frame"], "4b-tap-icon-3s-screencap")
    res["getevent"] = c.call("shell", cmd="head -c 3000 /data/local/tmp/ge.txt")["out"]
    c.call("shell", cmd=APP); time.sleep(2)
    res["shots"]["5-app"] = save(c.call("screenshot")["frame"], "5-app")
    shots = [c.call("screenshot") for _ in range(10)]
    res["screenshot_rt_ms"] = pct([s["rt_ms"] for s in shots])
    res["screenshot_capture_ms"] = pct([s["frame"]["capture_ms"] for s in shots])
    res["screenshot_encode_ms"] = pct([s["frame"]["encode_ms"] for s in shots])
    res["frame_meta"] = {k: v for k, v in shots[-1]["frame"].items() if k != "jpeg"}
    taps = [c.call("tap", x=btn[0], y=btn[1]) for _ in range(10)]
    res["tap_input_ack_ms"] = pct([t["input_ms"] for t in taps])
    res["tap_first_frame_ms"] = pct([t["first_frame_ms"] for t in taps if t["first_frame_ms"] is not None])
    res["tap_first_frame_n"] = sum(t["first_frame_ms"] is not None for t in taps)
    res["tap_settled_rt_ms"] = pct([t["rt_ms"] for t in taps])
    res["tap_settled"] = sum(t["settled"] for t in taps); res["tap_frames"] = [t["frames"] for t in taps]
    res["shots"]["6-after-tap"] = save(taps[-1]["frame"], "6-after-tap")
    acks = [c.call("tap", x=btn[0], y=btn[1], screenshot=False) for _ in range(10)]
    res["tap_ack_rt_ms"] = pct([a["rt_ms"] for a in acks])
    homes = [c.call("key", name="home") if i % 2 == 0 else c.call("shell", cmd="am start -n com.boltbetz.staging/com.boltbetz.MainActivity") for i in range(20)]
    hk = [h for h in homes if "first_frame_ms" in h]
    res["home_first_frame_ms"] = pct([h["first_frame_ms"] for h in hk if h["first_frame_ms"] is not None])
    res["home_settled_rt_ms"] = pct([h["rt_ms"] for h in hk])
    # Taps with a visible response: from the app drawer, tap the app icon (opens the app), then go back.
    opens = []
    for i in range(10):
        c.call("key", name="home"); c.call("swipe", x1=360, y1=1000, x2=360, y2=300, ms=150); time.sleep(0.5)
        opens.append(c.call("tap", x=446, y=925))
        if i == 0: res["shots"]["7-tap-open"] = save(opens[0]["frame"], "7-tap-open")
    res["tap_open_first_frame_ms"] = pct([o["first_frame_ms"] for o in opens if o["first_frame_ms"] is not None])
    res["tap_open_first_frame_n"] = sum(o["first_frame_ms"] is not None for o in opens)
    res["tap_open_settled_ms"] = pct([o["settled_ms"] for o in opens])
    res["tap_open_rt_ms"] = pct([o["rt_ms"] for o in opens])
    res["tap_open_settled"] = sum(o["settled"] for o in opens)
finally:
    try:
        Client().call("stop"); log("stopped d0")
    except Exception as e:
        log("stop failed", e)
    daemon.terminate()
    json.dump(res, open(f"{OUT}/result.json", "w"), indent=1)
    print(json.dumps(res, indent=1))
