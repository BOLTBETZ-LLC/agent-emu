# Device setup hygiene + structured API layer 1 check for agent-emud. Boots 1 Device, installs and opens
# the proof app (first screen saved; no system dialog over it), then:
# logs (one-shot, filtered to the app), logs follow (streamed), crash_events long poll across
# `am crash`, an ANR (app SIGSTOPped, then taps), and the same calls through `agent-emud mcp`. Stops the Device.
# usage: python logs_smoke.py [out_dir]
import base64, json, os, socket, subprocess, sys, threading, time

D = os.path.dirname(os.path.abspath(__file__))
EXE = f"{D}/target/release/agent-emud.exe"
OUT = sys.argv[1] if len(sys.argv) > 1 else "C:/dev/agent-emu-work/results/logs"
os.makedirs(OUT, exist_ok=True)
W = "C:/dev/agent-emu-work"
PKG = "com.boltbetz.staging"
INSTALL = (f"head -c {int(open(f'{W}/run/apk.size').read())} /dev/block/vdb > /data/local/tmp/p.apk && "
           "chmod 644 /data/local/tmp/p.apk && pm install -r /data/local/tmp/p.apk; rm -f /data/local/tmp/p.apk")

def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)

class Client:
    def __init__(s):
        s.sock = socket.create_connection(("127.0.0.1", 7400)); s.f = s.sock.makefile("rb")
    def send(s, call, **kw):
        s.sock.sendall((json.dumps({"id": 1, "call": call, "device": "d0", **kw}) + "\n").encode())
    def recv(s):
        return json.loads(s.f.readline())
    def call(s, call, **kw):
        s.send(call, **kw); r = s.recv()
        if not r.get("ok"): raise RuntimeError(f"{call}: {r.get('error')}")
        return r

def shell(c, cmd, t=120):
    return c.call("shell", cmd=cmd, timeout_s=t)["out"]

def bg(fn):
    box = {}
    th = threading.Thread(target=lambda: box.update(r=fn(), t=time.time())); th.start()
    return th, box

def brief(e):
    return {k: e.get(k) for k in ("seq", "type", "process", "pid", "exception", "message", "reason", "signal")} | {"stack_lines": len(e.get("stack", []))}

daemon = subprocess.Popen([EXE], stdout=open(f"{OUT}/daemon.log", "w"), stderr=subprocess.STDOUT)
res = {}
try:
    time.sleep(1); c = Client()
    res["ready_s"] = round(c.call("start")["ready_s"], 1); log("ready", res["ready_s"])
    boot = c.call("crash_events")
    res["boot_events"] = [brief(e) for e in boot["events"]]
    res["hygiene"] = {"hide_error_dialogs": shell(c, "settings get global hide_error_dialogs"),
                      "log.tag.RIL": shell(c, "getprop log.tag.RIL")}
    res["install"] = shell(c, INSTALL, 300)
    res["launch"] = shell(c, f"am start -W -n {PKG}/com.boltbetz.MainActivity | grep -E 'Status|TotalTime'")
    time.sleep(6)
    f = c.call("screenshot")["frame"]; open(f"{OUT}/app-first-screen.jpg", "wb").write(base64.b64decode(f["jpeg"]))
    res["focus"] = shell(c, "dumpsys window | grep mCurrentFocus")
    res["dialog_windows"] = shell(c, "dumpsys window windows | grep -cE 'Application Error|Application Not Responding|keeps stopping|isn.t responding'")
    log("hygiene", res["hygiene"], res["focus"], "dialogs", res["dialog_windows"])

    # logs: one-shot, filtered to the app
    r = c.call("logs", filter=PKG, max_lines=20)
    res["logs_oneshot"] = {"lines": len(r["lines"]), "ms": r["ms"], "cursor": r["cursor"], "sample": r["lines"][-3:]}
    tagged = c.call("logs", filter="ActivityManager", max_lines=5)
    res["logs_tag"] = {"lines": len(tagged["lines"]), "sample": tagged["lines"][-1:]}

    # follow (streamed) and crash_events long poll, both open while the app is crashed
    s = Client(); s.send("logs", filter=PKG, follow=True, duration_ms=6000, cursor=r["cursor"])
    def follow():
        batches = []
        while True:
            m = s.recv(); batches.append({"t": time.time(), "n": len(m.get("lines", [])), "more": m.get("more"), "ok": m.get("ok"),
                                          "crash_lines": sum("FATAL EXCEPTION" in l or "CrashedByAdb" in l for l in m.get("lines", []))})
            if not m.get("more"): return batches
    fth, fbox = bg(follow)
    last = boot["last"]
    eth, ebox = bg(lambda: Client().call("crash_events", after=last, filter=PKG, wait_ms=20000))
    time.sleep(1); t_crash = time.time()
    res["am_crash"] = shell(c, f"am crash {PKG}; echo rc=$?")
    eth.join(); fth.join()
    ev = ebox["r"]
    res["crash_event"] = {"events": [brief(e) for e in ev["events"]], "stack_head": ev["events"][0]["stack"][:3] if ev["events"] else None,
                          "after_am_crash_ms": round((ebox["t"] - t_crash) * 1000)}
    res["follow"] = {"batches": len(fbox["r"]), "lines": sum(b["n"] for b in fbox["r"]), "crash_lines": sum(b["crash_lines"] for b in fbox["r"]),
                     "last_more": fbox["r"][-1]["more"]}
    log("crash", res["crash_event"]["events"], res["crash_event"]["after_am_crash_ms"], "ms; follow", res["follow"])
    last = ev["last"]

    # ANR: stop the app's process, then send it input; ActivityManager reports it after the input timeout
    shell(c, f"am start -W -n {PKG}/com.boltbetz.MainActivity"); time.sleep(5)
    eth, ebox = bg(lambda: Client().call("crash_events", after=last, filter=PKG, wait_ms=60000))
    t_stop = time.time()
    shell(c, f"kill -STOP $(pidof {PKG})")
    for _ in range(6):
        if not eth.is_alive(): break
        c.call("tap", x=360, y=900, screenshot=False); time.sleep(5)
    eth.join()
    res["anr_event"] = {"events": [brief(e) for e in ebox["r"]["events"]], "after_sigstop_ms": round((ebox["t"] - t_stop) * 1000)}
    shell(c, f"kill -9 $(pidof {PKG})")
    log("anr", res["anr_event"])

    # MCP: tools/list plus crash_events and logs through the stdio server
    m = subprocess.Popen([EXE, "mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    def rpc(i, method, params):
        m.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params}) + "\n"); m.stdin.flush()
        return json.loads(m.stdout.readline())
    rpc(1, "initialize", {"protocolVersion": "2025-06-18"})
    tools = [t["name"] for t in rpc(2, "tools/list", {})["result"]["tools"]]
    ce = rpc(3, "tools/call", {"name": "crash_events", "arguments": {"device": "d0", "filter": PKG}})["result"]
    lg = rpc(4, "tools/call", {"name": "logs", "arguments": {"device": "d0", "filter": PKG, "max_lines": 5}})["result"]
    m.stdin.close(); m.wait(10)
    ce_json = json.loads(ce["content"][-1]["text"])
    res["mcp"] = {"tools": tools, "crash_events_types": [e["type"] for e in ce_json["events"]],
                  "logs_text_lines": len(lg["content"][0]["text"].splitlines()), "isError": ce.get("isError") or lg.get("isError")}
    log("mcp", res["mcp"])
    allev = c.call("crash_events")["events"]
    res["events_by_process"] = {p: sum(e.get("process") == p for e in allev) for p in {e.get("process") for e in allev}}
finally:
    try:
        Client().call("stop"); log("stopped d0")
    except Exception as e:
        log("stop failed", e)
    daemon.terminate()
    json.dump(res, open(f"{OUT}/result.json", "w"), indent=1)
    print(json.dumps(res, indent=1))
