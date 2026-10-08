# Device-controls smoke for agent-emud (issue 15, layer 2): boot 1 Device, call every control once through
# the MCP stdio server against the proof app (com.boltbetz.staging), print PASS/FAIL/GAP with evidence per
# call, then stop the Device. Defaults keep clear of a second worker: Device d5, daemon 127.0.0.1:7405,
# adb 127.0.0.1:6525, --mem 2048.
# usage: python controls_smoke.py [out_dir]
import json, os, socket, subprocess, sys, time

D = os.path.dirname(os.path.abspath(__file__))
W = "C:/dev/agent-emu-work"
EXE = f"{D}/target/release/agent-emud.exe"
DEV, ADDR, ADB = os.environ.get("AE_DEV", "d5"), os.environ.get("AE_ADDR", "127.0.0.1:7405"), os.environ.get("AE_ADB", "6525")
PKG = "com.boltbetz.staging"
OUT = sys.argv[1] if len(sys.argv) > 1 else f"{W}/results/controls"
os.makedirs(OUT, exist_ok=True)

def daemon_call(call, **kw):
    h, p = ADDR.split(":"); s = socket.create_connection((h, int(p))); f = s.makefile("rb")
    s.sendall((json.dumps({"id": 1, "call": call, "device": DEV, **kw}) + "\n").encode())
    r = json.loads(f.readline()); s.close()
    if not r.get("ok"): raise RuntimeError(f"{call}: {r.get('error')}")
    return r

class Mcp:
    def __init__(s):
        s.p = subprocess.Popen([EXE, "mcp", "--addr", ADDR], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        s.n = 0; s.rpc("initialize", {"protocolVersion": "2025-06-18"})
    def rpc(s, method, params):
        s.n += 1
        s.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": s.n, "method": method, "params": params}) + "\n"); s.p.stdin.flush()
        return json.loads(s.p.stdout.readline())["result"]
    def tool(s, name, **args):
        r = s.rpc("tools/call", {"name": name, "arguments": {"device": DEV, **args}})
        text = r["content"][-1]["text"]
        if r.get("isError"): return False, text
        return True, json.loads(text)

results = []
def report(status, call, evidence):
    line = f"{status} {call}: {evidence}"
    print(line, flush=True); results.append(line)

def check(call, ok, rep, cond, evidence):
    if not ok: return report("FAIL", call, rep)
    report("PASS" if cond(rep) else "FAIL", call, evidence(rep))

env = {**os.environ, "AE_MEM": "2048", "AE_ALLOW_OTHER_CROSVM": "1", "AGENT_EMU_ADB_PORT": ADB}
daemon = subprocess.Popen([EXE, "--addr", ADDR], env=env, stdout=open(f"{OUT}/daemon.log", "w"), stderr=subprocess.STDOUT)
try:
    time.sleep(1)
    r = daemon_call("start"); print(f"boot {DEV}: ready {r['ready_s']:.0f} s, host Available before {r['available_mb_before']} MB", flush=True)
    m = Mcp()
    names = [t["name"] for t in m.rpc("tools/list", {})["tools"]]
    report("PASS" if all(c in names for c in ["deep_link", "set_location", "inject_camera_image", "clock", "permission", "app"]) else "FAIL",
           "tools/list", f"{len(names)} tools: {', '.join(names)}")

    apk = f"{OUT}/proof.apk"
    open(apk, "wb").write(open(f"{W}/run/apk.img", "rb").read(int(open(f"{W}/run/apk.size").read())))
    ok, rep = m.tool("app", install=apk)
    check("app install", ok, rep, lambda r: "Success" in r["out"], lambda r: r["out"].splitlines()[-1])
    ok, rep = m.tool("app", clear=PKG)
    check("app clear", ok, rep, lambda r: r["out"] == "Success", lambda r: r["out"])
    ok, rep = m.tool("permission", pkg=PKG, perm="android.permission.CAMERA", action="grant")
    check("permission grant", ok, rep, lambda r: r["granted"] is True, lambda r: r["verified"])
    ok, rep = m.tool("permission", pkg=PKG, perm="android.permission.CAMERA", action="revoke")
    check("permission revoke", ok, rep, lambda r: r["granted"] is False, lambda r: r["verified"])

    ok, rep = m.tool("app", launch=PKG)
    def resumed():
        o = daemon_call("shell", cmd="dumpsys activity activities | grep -m 1 topResumedActivity 2>/dev/null")["out"].strip()
        return o
    check("app launch", ok, rep, lambda r: "Status: ok" in r["out"] and PKG in resumed(),
          lambda r: " | ".join(l for l in r["out"].splitlines() if l.startswith(("Status", "Activity"))) + " | " + resumed())

    # The proof app registers boltbetz-staging://verified and ://e2e-session (aapt2 dump xmltree).
    uri = "boltbetz-staging://verified"
    daemon_call("key", name="home", screenshot=False)
    ok, rep = m.tool("deep_link", uri=uri)
    check("deep_link", ok, rep, lambda r: "Status: ok" in r["out"] and PKG in r["out"] and PKG in resumed(),
          lambda r: f"{uri} -> " + " | ".join(l for l in r["out"].splitlines() if l.startswith(("Status", "Activity"))))

    ok, rep = m.tool("set_location", lat=36.1147, lon=-115.1728)
    check("set_location", ok, rep, lambda r: "36.114700,-115.172800" in r["verified"], lambda r: r["verified"])

    host_ms = int(time.time() * 1000)
    ok, rep = m.tool("clock", advance_ms=3600000)
    check("clock advance_ms", ok, rep, lambda r: abs(r["after_ms"] - r["before_ms"] - 3600000) < 5000,
          lambda r: f"before {r['before_ms']} after {r['after_ms']} delta {r['after_ms'] - r['before_ms']} ms")
    ok, rep = m.tool("clock", set=host_ms)
    check("clock set", ok, rep, lambda r: abs(r["error_ms"]) < 5000,
          lambda r: f"target {r['target_ms']} after {r['after_ms']} (error {r['error_ms']} ms)")
    ok, rep = m.tool("clock", freeze=True)
    auto = daemon_call("shell", cmd="cmd time_detector is_auto_detection_enabled")["out"].strip()
    time.sleep(2)
    ok2, rep2 = m.tool("clock", freeze=True)
    if ok and ok2:
        report("GAP", "clock freeze", f"auto time detection now {auto}; guest clock still moved "
               f"{rep2['after_ms'] - rep['after_ms']} ms in ~2 s (no true stop)")
    else:
        report("FAIL", "clock freeze", rep if not ok else rep2)
    m.tool("clock", freeze=False)

    img = f"{OUT}/qr.png"
    open(img, "wb").write(bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000d49444154789c63000100000500010d0a2db40000000049454e44ae426082"))
    ok, rep = m.tool("inject_camera_image", path=img)
    report("GAP" if not ok and rep.startswith("unsupported") else "FAIL", "inject_camera_image", rep)

    shot = daemon_call("screenshot")["frame"]
    import base64; open(f"{OUT}/last.jpg", "wb").write(base64.b64decode(shot["jpeg"]))
    m.p.stdin.close(); m.p.wait(10)
finally:
    try:
        daemon_call("stop"); print(f"stopped {DEV}")
    except Exception as e:
        print("stop failed", e)
    subprocess.run(["adb", "disconnect", f"127.0.0.1:{ADB}"], capture_output=True)
    daemon.terminate()
    open(f"{OUT}/controls.txt", "w").write("\n".join(results) + "\n")
