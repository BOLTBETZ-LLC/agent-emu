# Fleet check for agent-emud: one `fleet` call boots N Devices one after another (each to the proof app's
# first screen, then squeezed), then a screenshot and `memory` per Device, the MCP tool list, `fleet_stop`.
# The daemon refuses the next boot under 4000 MB Available; host Available is also logged every 30 s here.
# usage: [AE_FLEET_N=2] [AE_FLEET_BASE=0] [AE_SMOKE_IMAGE=slim5] [AE_SMOKE_MEM=640] [AE_SMOKE_NET=0]
#        [AE_CROSVM_DIR=crosvm-diet] [AE_ALLOW_OTHER_CROSVM=1 when other workers run VMs] python fleet_smoke.py [out_dir]
import base64, json, os, socket, subprocess, sys, threading, time

D = os.path.dirname(os.path.abspath(__file__)); EXE = f"{D}/target/release/agent-emud.exe"
OUT = sys.argv[1] if len(sys.argv) > 1 else "C:/dev/agent-emu-work/results/fleet-daemon"; os.makedirs(OUT, exist_ok=True)
N, BASE = int(os.environ.get("AE_FLEET_N", "2")), int(os.environ.get("AE_FLEET_BASE", "0"))
OPTS = {"n": N, "base": BASE, "image": os.environ.get("AE_SMOKE_IMAGE", "slim4"), "net": os.environ.get("AE_SMOKE_NET", "1") != "0",
        "auto_squeeze": True, "cap_main_mb": 250, "cap_helper_mb": 16}
if os.environ.get("AE_SMOKE_MEM"): OPTS["mem"] = int(os.environ["AE_SMOKE_MEM"])

def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)

def call(c, dev=None, **kw):
    s = socket.create_connection(("127.0.0.1", 7400)); f = s.makefile("rb")
    s.sendall((json.dumps({"id": 1, "call": c, **({"device": dev} if dev else {}), **kw}) + "\n").encode())
    r = json.loads(f.readline()); s.close()
    if not r.get("ok"): raise RuntimeError(f"{c}: {r.get('error')}")
    return r

def avail():
    o = subprocess.run(["powershell", "-NoProfile", "-Command", r"(Get-Counter '\Memory\Available MBytes').CounterSamples[0].CookedValue"],
                       capture_output=True, text=True).stdout.strip()
    return int(float(o or 0))

def comp():
    o = subprocess.run(["powershell", "-NoProfile", "-Command", "(Get-Process 'Memory Compression').WorkingSet64 / 1MB"],
                       capture_output=True, text=True).stdout.strip()
    return int(float(o or 0))

done = threading.Event()
def watch():
    while not done.wait(30): log("host Available", avail(), "MB")
threading.Thread(target=watch, daemon=True).start()

dmn = subprocess.Popen([EXE], stdout=open(f"{OUT}/daemon.log", "w"), stderr=subprocess.STDOUT)
res = {"opts": OPTS, "available_before_mb": avail(), "compression_before_mb": comp()}
try:
    time.sleep(1)
    m = subprocess.Popen([EXE, "mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    m.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}) + "\n"); m.stdin.flush()
    res["mcp_has_fleet"] = {"fleet", "fleet_stop"} <= {t["name"] for t in json.loads(m.stdout.readline())["result"]["tools"]}
    m.stdin.close(); m.wait(10)
    log("fleet", OPTS)
    f = call("fleet", **OPTS)
    res["fleet"] = f; log("fleet total", f["total"], "stopped:", f["stopped"])
    for row in f["devices"]:
        log(row.get("id"), {k: row.get(k) for k in ("ready_s", "ws_before_squeeze_mb", "ws_after_squeeze_mb", "app_pid", "error")})
    time.sleep(60)  # let the caps push pages out before the memory reading
    res["compression_after_60s_mb"] = comp()
    res["per_device"] = {}
    for row in f["devices"]:
        if row.get("error"): continue
        dev = row["id"]; fr = call("screenshot", dev)["frame"]
        open(f"{OUT}/{dev}.jpg", "wb").write(base64.b64decode(fr["jpeg"]))
        res["per_device"][dev] = {"ws_mb": call("memory", dev)["ws_mb"], "app_pid": call("shell", dev, cmd="pidof com.boltbetz.staging")["out"]}
    res["ws_total_mb"] = sum(v["ws_mb"] for v in res["per_device"].values())
    res["compression_growth_mb"] = res["compression_after_60s_mb"] - res["compression_before_mb"]
    log("per device", res["per_device"], "ws total", res["ws_total_mb"], "compression +", res["compression_growth_mb"], "(global store)")
finally:
    try: log("fleet_stop", call("fleet_stop")["stopped"])
    except Exception as e: log("fleet_stop failed", e)
    done.set(); dmn.terminate(); res["available_after_mb"] = avail()
    json.dump(res, open(f"{OUT}/result.json", "w"), indent=1)
