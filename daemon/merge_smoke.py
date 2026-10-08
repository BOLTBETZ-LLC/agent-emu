# usage: python merge_smoke.py <out_dir>
# Post-merge smoke: boot d0 at 2048 MB, then over MCP: app install + app launch + set_location (controls),
# am crash, crash_events (logs layer). Stops d0.
import json, os, socket, subprocess, sys, time
D = "C:/dev/agent-emu/daemon"; EXE = f"{D}/target/release/agent-emud.exe"; W = "C:/dev/agent-emu-work"; PKG = "com.boltbetz.staging"
OUT = sys.argv[1]; os.makedirs(OUT, exist_ok=True)
def call(c, **kw):
    s = socket.create_connection(("127.0.0.1", 7400)); f = s.makefile("rb")
    s.sendall((json.dumps({"id": 1, "call": c, "device": "d0", **kw}) + "\n").encode()); r = json.loads(f.readline()); s.close()
    if not r.get("ok"): raise RuntimeError(f"{c}: {r.get('error')}")
    return r
apk = f"{OUT}/proof.apk"; n = int(open(f"{W}/run/apk.size").read()); open(apk, "wb").write(open(f"{W}/run/apk.img", "rb").read(n))
env = dict(os.environ, AE_MEM="2048", AE_ALLOW_OTHER_CROSVM="1", AGENT_EMU_ADB_PORT="6520")
dmn = subprocess.Popen([EXE], env=env, stdout=open(f"{OUT}/daemon.log", "w"), stderr=subprocess.STDOUT)
res = {}
try:
    time.sleep(1); res["ready_s"] = round(call("start")["ready_s"], 1); print("ready", res["ready_s"], flush=True)
    m = subprocess.Popen([EXE, "mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True); n = [0]
    def tool(name, **a):
        n[0] += 1
        m.stdin.write(json.dumps({"jsonrpc": "2.0", "id": n[0], "method": "tools/call", "params": {"name": name, "arguments": {"device": "d0", **a}}}) + "\n"); m.stdin.flush()
        r = json.loads(m.stdout.readline())["result"]; t = r["content"][-1]["text"]
        out = {"isError": bool(r.get("isError")), "text": t[:400]}; print(name, out, flush=True); return out, r
    m.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}) + "\n"); m.stdin.flush(); m.stdout.readline()
    res["app_install"] = tool("app", install=apk)[0]
    res["app_launch"] = tool("app", launch=PKG)[0]
    res["set_location"] = tool("set_location", lat=36.1147, lon=-115.1728)[0]
    last = json.loads(tool("crash_events", filter=PKG)[1]["content"][-1]["text"])["last"]
    t = time.time(); call("shell", cmd=f"am crash {PKG}")
    o, r = tool("crash_events", filter=PKG, after=last, wait_ms=10000)
    ev = json.loads(r["content"][-1]["text"])["events"]
    res["crash_events"] = {"isError": o["isError"], "ms_after_am_crash": round((time.time() - t) * 1000),
                           "events": [{k: e.get(k) for k in ("seq", "type", "process", "exception", "message")} for e in ev]}
    print("crash_events", res["crash_events"], flush=True)
    m.stdin.close(); m.wait(10)
finally:
    try: call("stop"); print("stopped d0")
    except Exception as e: print("stop failed", e)
    dmn.terminate(); os.remove(apk) if os.path.exists(apk) else None
    json.dump(res, open(f"{OUT}/result.json", "w"), indent=1)
