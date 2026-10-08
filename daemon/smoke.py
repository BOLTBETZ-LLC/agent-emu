# Smoke test for agent-emud: boot 1 Device, install + launch the proof app, screenshot, tap "Try Again"
# (found via ui_tree), screenshot again, time 10 screenshots and 10 taps, check the MCP server, stop.
# usage: python smoke.py [out_dir]
import base64, json, os, re, socket, statistics, subprocess, sys, time

D = os.path.dirname(os.path.abspath(__file__))
EXE = f"{D}/target/release/agent-emud.exe"
W = "C:/dev/agent-emu-work"
OUT = sys.argv[1] if len(sys.argv) > 1 else f"{W}/results/daemon-smoke"
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
    xs = sorted(xs); return {"p50": round(statistics.median(xs)), "p95": round(xs[min(len(xs) - 1, int(0.95 * len(xs)))]),
                             "min": round(xs[0]), "max": round(xs[-1])}

daemon = subprocess.Popen([EXE], stdout=open(f"{OUT}/daemon.log", "w"), stderr=subprocess.STDOUT)
res = {}
try:
    time.sleep(1); c = Client()
    log("start d0 ...")
    r = c.call("start"); res["boot"] = {"ready_s": round(r["ready_s"], 1), "available_mb_before": r["available_mb_before"]}
    log("ready", res["boot"])
    c.call("lease")
    n_apk = int(open(f"{W}/run/apk.size").read())
    r = c.call("shell", cmd=f"head -c {n_apk} /dev/block/vdb > /data/local/tmp/p.apk && chmod 644 /data/local/tmp/p.apk && "
                            "pm install -r /data/local/tmp/p.apk; rm -f /data/local/tmp/p.apk", timeout_s=300)
    res["install"] = r["out"]; log("install", r["out"])
    r = c.call("shell", cmd="am start -W -n com.boltbetz.staging/com.boltbetz.MainActivity | grep -E 'Status|TotalTime'")
    res["launch"] = r["out"]; log("launch", r["out"])
    btn, t_end = None, time.time() + 90
    while time.time() < t_end and not btn:
        r = c.call("ui_tree"); res.setdefault("ui_tree_ms", []).append(round(r["rt_ms"]))
        m = re.search(r'<node[^>]*text="Try Again"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', r["xml"], re.I)
        if m:
            x1, y1, x2, y2 = map(int, m.groups()); btn = ((x1 + x2) // 2, (y1 + y2) // 2)
        else:
            time.sleep(2)
    if not btn:
        open(f"{OUT}/ui.xml", "w", encoding="utf-8").write(r["xml"]); raise RuntimeError("Try Again not found in ui_tree")
    res["try_again_at"] = btn; log("Try Again at", btn)
    r = c.call("screenshot"); res["before"] = save(r["frame"], "before-tap")
    res["frame_meta"] = {k: v for k, v in r["frame"].items() if k != "jpeg"}
    r = c.call("tap", x=btn[0], y=btn[1]); res["after"] = save(r["frame"], "after-tap")
    res["first_tap"] = {"settled": r["settled"], "frames": r["frames"], "input_ms": r["input_ms"], "rt_ms": round(r["rt_ms"])}
    log("tapped", res["first_tap"])
    shots = [c.call("screenshot") for _ in range(10)]
    res["screenshot_rt_ms"] = pct([s["rt_ms"] for s in shots])
    res["screenshot_capture_ms"] = pct([s["frame"]["capture_ms"] for s in shots])
    res["screenshot_encode_ms"] = pct([s["frame"]["encode_ms"] for s in shots])
    taps = [c.call("tap", x=btn[0], y=btn[1]) for _ in range(10)]
    res["tap_settled_rt_ms"] = pct([t["rt_ms"] for t in taps])
    res["tap_input_ack_ms"] = pct([t["input_ms"] for t in taps])
    res["tap_settled_count"] = sum(t["settled"] for t in taps); res["tap_frames"] = [t["frames"] for t in taps]
    acks = [c.call("tap", x=btn[0], y=btn[1], screenshot=False) for _ in range(10)]
    res["tap_no_screenshot_rt_ms"] = pct([a["rt_ms"] for a in acks])
    # busy check: a second client may screenshot but not tap while the lease is held
    c2 = Client(); c2.call("screenshot")
    try: c2.call("tap", x=1, y=1); res["lease_busy"] = "FAIL: second client tapped"
    except RuntimeError as e: res["lease_busy"] = str(e)
    # MCP: initialize, tools/list, tools/call screenshot (MCP holds its own connection, so release first)
    c.call("release")
    m = subprocess.Popen([EXE, "mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    def rpc(i, method, params): m.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params}) + "\n"); m.stdin.flush(); return json.loads(m.stdout.readline())
    rpc(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "0"}})
    tl = rpc(2, "tools/list", {})["result"]["tools"]
    t = time.perf_counter(); sc = rpc(3, "tools/call", {"name": "screenshot", "arguments": {"device": "d0", "size": "360x640"}})["result"]
    res["mcp"] = {"tools": [x["name"] for x in tl], "screenshot_types": [x["type"] for x in sc["content"]],
                  "screenshot_ms": round((time.perf_counter() - t) * 1000), "is_error": sc.get("isError", False)}
    open(f"{OUT}/mcp-360x640.jpg", "wb").write(base64.b64decode(sc["content"][0]["data"]))
    m.stdin.close(); m.wait(10)
finally:
    try:
        Client().call("stop"); log("stopped d0")
    except Exception as e:
        log("stop failed", e)
    daemon.terminate()
    json.dump(res, open(f"{OUT}/result.json", "w"), indent=1)
    print(json.dumps(res, indent=1))
