# adb over slirp check for agent-emud: boot 1 Device, adb connect 127.0.0.1:6520, getprop, install the
# proof APK, then stop. usage: python adb_smoke.py [out_dir]
import json, os, socket, subprocess, sys, time

D = os.path.dirname(os.path.abspath(__file__))
W = "C:/dev/agent-emu-work"
OUT = sys.argv[1] if len(sys.argv) > 1 else f"{W}/results/adb"
os.makedirs(OUT, exist_ok=True)

def call(c, **kw):
    s = socket.create_connection(("127.0.0.1", 7400)); f = s.makefile("rb")
    s.sendall((json.dumps({"id": 1, "call": c, "device": "d0", **kw}) + "\n").encode())
    r = json.loads(f.readline())
    if not r.get("ok"): raise RuntimeError(f"{c}: {r.get('error')}")
    return r

def run(*a, timeout=300):
    t = time.perf_counter()
    p = subprocess.run(a, capture_output=True, text=True, timeout=timeout)
    out = f"$ {' '.join(a)}\n{p.stdout}{p.stderr}(exit {p.returncode}, {time.perf_counter() - t:.1f} s)"
    print(out, flush=True)
    return out

apk = f"{OUT}/proof.apk"
n = int(open(f"{W}/run/apk.size").read())
open(apk, "wb").write(open(f"{W}/run/apk.img", "rb").read(n))
daemon = subprocess.Popen([f"{D}/target/release/agent-emud.exe"], stdout=open(f"{OUT}/daemon.log", "w"), stderr=subprocess.STDOUT)
log = []
try:
    time.sleep(1)
    r = call("start"); print("ready", round(r["ready_s"], 1), "s", flush=True)
    log.append(run("adb", "connect", "127.0.0.1:6520", timeout=30))
    log.append(run("adb", "-s", "127.0.0.1:6520", "shell", "getprop", "sys.boot_completed", timeout=30))
    log.append(run("adb", "-s", "127.0.0.1:6520", "install", "-r", apk))
    log.append(run("adb", "-s", "127.0.0.1:6520", "shell", "pm", "list", "packages", "com.boltbetz", timeout=30))
finally:
    subprocess.run(["adb", "disconnect", "127.0.0.1:6520"], capture_output=True)
    try:
        call("stop"); print("stopped d0")
    except Exception as e:
        print("stop failed", e)
    daemon.terminate()
    open(f"{OUT}/adb.txt", "w").write("\n\n".join(log) + "\n")
