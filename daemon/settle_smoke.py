# Settle v2 check for agent-emud: boot 1 Device, wait until the screen is idle, then 10 times: tap the
# launcher icon (opens the app), HOME (back to the launcher). Times input -> first frame and
# input -> settled (first frame, then 33 ms quiet). Also times screenshots (JPEG encode). Stops the Device.
# usage: python settle_smoke.py [out_dir]
import base64, json, os, socket, statistics, subprocess, sys, time

D = os.path.dirname(os.path.abspath(__file__))
EXE = f"{D}/target/release/agent-emud.exe"
OUT = sys.argv[1] if len(sys.argv) > 1 else "C:/dev/agent-emu-work/results/settle-v2"
os.makedirs(OUT, exist_ok=True)

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

def save(frame, name):
    p = f"{OUT}/{name}.jpg"; open(p, "wb").write(base64.b64decode(frame["jpeg"])); return p

def pct(xs):
    if not xs: return None
    xs = sorted(xs); return {"p50": round(statistics.median(xs)), "p95": round(xs[min(len(xs) - 1, int(0.95 * len(xs)))]),
                             "min": round(xs[0]), "max": round(xs[-1]), "n": len(xs)}

daemon = subprocess.Popen([EXE], stdout=open(f"{OUT}/daemon.log", "w"), stderr=subprocess.STDOUT)
res = {}
try:
    time.sleep(1); c = Client()
    r = c.call("start"); res["boot"] = {"ready_s": round(r["ready_s"], 1), "available_mb_before": r["available_mb_before"]}
    res["status"] = c.call("status")["devices"]; log("ready", res["boot"], res["status"])
    c.call("lease"); time.sleep(5)
    c.call("key", name="home"); time.sleep(1)
    shots = [c.call("screenshot") for _ in range(10)]
    res["screenshot_rt_ms"] = pct([s["rt_ms"] for s in shots])
    res["screenshot_encode_ms"] = pct([s["frame"]["encode_ms"] for s in shots])
    res["frame_meta"] = {k: v for k, v in shots[-1]["frame"].items() if k != "jpeg"}
    res["shots"] = {"0-home": save(shots[-1]["frame"], "0-home")}
    # slim3 crash-loops Bluetooth; its "keeps stopping" dialog covers the launcher and redraws on its own.
    c.call("shell", cmd="settings put global hide_error_dialogs 1; am broadcast -a android.intent.action.CLOSE_SYSTEM_DIALOGS")
    c.call("key", name="back"); time.sleep(1)
    # "Phone is starting..." animates after boot; wait until no frame for 3 s so frames come from inputs.
    last, still, t_end = -1, 0, time.time() + 180
    while time.time() < t_end and still < 3:
        g = c.call("screenshot")["frame"]["generation"]; still = still + 1 if g == last else 0; last = g; time.sleep(1)
    res["idle_wait_s"] = round(180 - (t_end - time.time())); res["idle_frames_3s"] = 0 if still >= 3 else None
    g0 = c.call("screenshot")["frame"]; time.sleep(2); g1 = c.call("screenshot")["frame"]
    res["idle_frames_2s"] = g1["generation"] - g0["generation"]
    res["shots"]["0-idle"] = save(g1, "0-idle")
    rows = []
    for i in range(10):
        o = c.call("tap", x=446, y=925)
        time.sleep(1)
        b = c.call("key", name="home")
        time.sleep(0.5)
        for kind, r in (("tap_open", o), ("home", b)):
            rows.append({"kind": kind, **{k: r.get(k) for k in ("input_via", "input_ms", "first_frame_ms", "settled_ms", "settled", "frames", "reason")},
                         "encode_ms": r["frame"]["encode_ms"], "generation": r["frame"]["generation"], "rt_ms": round(r["rt_ms"])})
        if i == 2:
            res["shots"]["1-tap-open"] = save(o["frame"], "1-tap-open")
            res["shots"]["2-home"] = save(b["frame"], "2-home")
        log(i, rows[-2], rows[-1])
    res["rows"] = rows
    for kind in ("tap_open", "home", None):
        rs = [r for r in rows if kind in (None, r["kind"])]
        res[f"summary_{kind or 'all'}"] = {
            "first_frame_ms": pct([r["first_frame_ms"] for r in rs if r["first_frame_ms"] is not None]),
            "settled_ms": pct([r["settled_ms"] for r in rs]),
            "rt_ms": pct([r["rt_ms"] for r in rs]),
            "settled": sum(bool(r["settled"]) for r in rs), "n": len(rs)}
finally:
    try:
        Client().call("stop"); log("stopped d0")
    except Exception as e:
        log("stop failed", e)
    daemon.terminate()
    json.dump(res, open(f"{OUT}/result.json", "w"), indent=1)
    print(json.dumps({k: v for k, v in res.items() if k != "rows"}, indent=1))
