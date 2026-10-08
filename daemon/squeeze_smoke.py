# Lever B check for agent-emud: boot d0 (slim3 + pmem, AE_MEM default 896), install and open the proof app,
# 20 taps (tap -> first frame), squeeze (balloon + working-set caps), memory, 20 more taps, a fast screenshot.
# Host Available is logged every 30 s; the daemon aborts a boot under 4000 MB. Stops d0.
# usage: python squeeze_smoke.py [out_dir]
import base64, json, os, socket, statistics, subprocess, sys, threading, time

D = os.path.dirname(os.path.abspath(__file__)); EXE = f"{D}/target/release/agent-emud.exe"; W = "C:/dev/agent-emu-work"
OUT = sys.argv[1] if len(sys.argv) > 1 else f"{W}/results/squeeze"; os.makedirs(OUT, exist_ok=True)
PKG = "com.boltbetz.staging"; TAP = (360, 780)  # "Try Again" on the offline first screen: redraws on every tap
INSTALL = (f"head -c {int(open(f'{W}/run/apk.size').read())} /dev/block/vdb > /data/local/tmp/p.apk && "
           "chmod 644 /data/local/tmp/p.apk && pm install -r /data/local/tmp/p.apk; rm -f /data/local/tmp/p.apk")

def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)

def call(c, **kw):
    s = socket.create_connection(("127.0.0.1", 7400)); f = s.makefile("rb")
    s.sendall((json.dumps({"id": 1, "call": c, "device": "d0", **kw}) + "\n").encode()); r = json.loads(f.readline()); s.close()
    if not r.get("ok"): raise RuntimeError(f"{c}: {r.get('error')}")
    return r

def avail():
    return int(float(subprocess.run(["powershell", "-NoProfile", "-Command", "(Get-Counter '\Memory\Available MBytes').CounterSamples[0].CookedValue"],
                                    capture_output=True, text=True).stdout.strip() or 0))

done = threading.Event(); mins = []
def watch():
    while not done.wait(30):
        a = avail(); mins.append(a); log("host Available", a, "MB")
threading.Thread(target=watch, daemon=True).start()

def taps(n=20):
    ff = []
    for _ in range(n):
        r = call("tap", x=TAP[0], y=TAP[1], deadline_ms=2000); time.sleep(0.3)
        if r.get("first_frame_ms") is not None: ff.append(r["first_frame_ms"])
    ff.sort()
    return {"n": n, "with_frame": len(ff), "p50": ff[len(ff) // 2] if ff else None, "p95": ff[min(len(ff) - 1, int(len(ff) * .95))] if ff else None,
            "max": ff[-1] if ff else None}

env = dict(os.environ, AE_ALLOW_OTHER_CROSVM="1")  # other workers' Devices may run; ours is d0 only
dmn = subprocess.Popen([EXE], env=env, stdout=open(f"{OUT}/daemon.log", "w"), stderr=subprocess.STDOUT)
res = {"available_before_mb": avail()}
try:
    time.sleep(1); r = call("start"); res["ready_s"] = round(r["ready_s"], 1); log("ready", res["ready_s"])
    res["install"] = call("shell", cmd=INSTALL, timeout_s=300)["out"]
    res["launch"] = call("shell", cmd=f"am start -W -n {PKG}/com.boltbetz.MainActivity | grep -E 'Status|TotalTime'")["out"]
    time.sleep(8)
    res["memory_before"] = call("memory"); log("ws before", res["memory_before"]["ws_mb"])
    res["taps_before"] = taps(); log("taps before", res["taps_before"])
    sq = call("squeeze"); res["squeeze"] = {k: sq[k] for k in ("opts", "balloon", "ws_before_mb", "ws_after_mb", "squeeze_ms", "capped")}
    log("squeeze", res["squeeze"])
    time.sleep(30)
    res["memory_after_30s"] = call("memory"); log("ws after 30 s", res["memory_after_30s"]["ws_mb"])
    res["taps_after"] = taps(); log("taps after", res["taps_after"])
    f = call("screenshot")["frame"]; open(f"{OUT}/after-squeeze.jpg", "wb").write(base64.b64decode(f["jpeg"]))
    res["screenshot_ms"] = f["capture_ms"] + f["encode_ms"]
    res["memory_after_taps"] = call("memory"); res["app_pid"] = call("shell", cmd=f"pidof {PKG}")["out"]
    res["guest_mem"] = call("shell", cmd="grep -E 'MemTotal|MemAvailable' /proc/meminfo")["out"]
    # the same calls over MCP
    m = subprocess.Popen([EXE, "mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    def rpc(i, method, params):
        m.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params}) + "\n"); m.stdin.flush()
        return json.loads(m.stdout.readline())["result"]
    rpc(1, "initialize", {"protocolVersion": "2025-06-18"})
    names = [t["name"] for t in rpc(2, "tools/list", {})["tools"]]
    mm = rpc(3, "tools/call", {"name": "memory", "arguments": {"device": "d0"}})
    res["mcp"] = {"has_squeeze": "squeeze" in names, "has_memory": "memory" in names, "memory_ws_mb": json.loads(mm["content"][-1]["text"]).get("ws_mb")}
    m.stdin.close(); m.wait(10); log("mcp", res["mcp"], "app pid", res["app_pid"])
finally:
    try: call("stop"); log("stopped d0")
    except Exception as e: log("stop failed", e)
    done.set(); dmn.terminate(); res["available_min_mb"] = min(mins) if mins else None
    json.dump(res, open(f"{OUT}/result.json", "w"), indent=1); print(json.dumps(res, indent=1))
