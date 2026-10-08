# Guest diet check for agent-emud: boot d0 with image slim4 (704 MB guest by default, diet kernel cmdline, 11 consoles,
# one virtio-snd), open the proof app (screenshot), 20 taps on a blank area (tap -> first frame), squeeze
# 250/16, compression-store growth over the next 60 s, memory, 20 more taps, screenshot. Host Available is logged every 30 s; the daemon stops a boot
# under 4000 MB. Stops d0.
# usage: [AE_SMOKE_MEM=704] python diet_smoke.py [out_dir]
import base64, json, os, socket, subprocess, sys, threading, time

D = os.path.dirname(os.path.abspath(__file__)); EXE = f"{D}/target/release/agent-emud.exe"; W = "C:/dev/agent-emu-work"
OUT = sys.argv[1] if len(sys.argv) > 1 else f"{W}/results/diet-daemon"; os.makedirs(OUT, exist_ok=True)
PKG = "com.boltbetz.staging"; BLANK = (360, 120)  # dimmed area above the offline dialog
INSTALL = (f"head -c {int(open(f'{W}/run-slim4/apk.size').read())} /dev/block/vdb > /data/local/tmp/p.apk && "
           "chmod 644 /data/local/tmp/p.apk && pm install -r /data/local/tmp/p.apk; rm -f /data/local/tmp/p.apk")

def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)

def call(c, **kw):
    s = socket.create_connection(("127.0.0.1", 7400)); f = s.makefile("rb")
    s.sendall((json.dumps({"id": 1, "call": c, "device": "d0", **kw}) + "\n").encode()); r = json.loads(f.readline()); s.close()
    if not r.get("ok"): raise RuntimeError(f"{c}: {r.get('error')}")
    return r

def avail():
    return int(float(subprocess.run(["powershell", "-NoProfile", "-Command", r"(Get-Counter '\Memory\Available MBytes').CounterSamples[0].CookedValue"],
                                    capture_output=True, text=True).stdout.strip() or 0))

done = threading.Event()
def watch():
    while not done.wait(30): log("host Available", avail(), "MB")
threading.Thread(target=watch, daemon=True).start()

def taps(n=20):
    ff = []
    for _ in range(n):
        r = call("tap", x=BLANK[0], y=BLANK[1], deadline_ms=1000); time.sleep(0.3)
        if r.get("first_frame_ms") is not None: ff.append(r["first_frame_ms"])
    ff.sort()
    return {"n": n, "with_frame": len(ff), "p50": ff[len(ff) // 2] if ff else None,
            "p95": ff[min(len(ff) - 1, int(len(ff) * .95))] if ff else None, "max": ff[-1] if ff else None}

def shot(name):
    f = call("screenshot")["frame"]; open(f"{OUT}/{name}.jpg", "wb").write(base64.b64decode(f["jpeg"])); return f["capture_ms"] + f["encode_ms"]

env = dict(os.environ, AE_ALLOW_OTHER_CROSVM="1")
dmn = subprocess.Popen([EXE], env=env, stdout=open(f"{OUT}/daemon.log", "w"), stderr=subprocess.STDOUT)
res = {"available_before_mb": avail()}
try:
    time.sleep(1); r = call("start", image="slim4", **({"mem": int(os.environ["AE_SMOKE_MEM"])} if os.environ.get("AE_SMOKE_MEM") else {})); res["ready_s"] = round(r["ready_s"], 1); log("ready", res["ready_s"])
    res["guest"] = call("shell", cmd="cat /proc/cmdline | tr ' ' '\n' | grep -E 'virtio_blk|transparent|kfence'; grep MemTotal /proc/meminfo; ls /dev/hvc* | wc -l")["out"]
    res["install"] = call("shell", cmd=INSTALL, timeout_s=300)["out"]
    res["launch"] = call("shell", cmd=f"am start -W -n {PKG}/com.boltbetz.MainActivity | grep -E 'Status|TotalTime'")["out"]
    time.sleep(8); shot("first-screen")
    res["memory_before"] = call("memory")["ws_mb"]; log("ws before", res["memory_before"], res["guest"])
    res["taps_before"] = taps(); log("taps before", res["taps_before"])
    res["compression_before_squeeze_mb"] = int(subprocess.run(["powershell", "-NoProfile", "-Command", "(Get-Process 'Memory Compression').WorkingSet64 / 1MB"],
                                                               capture_output=True, text=True).stdout.strip().split(".")[0] or 0)
    sq = call("squeeze", cap_main_mb=250, cap_helper_mb=16)
    res["squeeze"] = {k: sq[k] for k in ("balloon", "ws_before_mb", "ws_after_mb", "squeeze_ms")}; log("squeeze", res["squeeze"])
    # Compression store growth in the 60 s after squeeze (global store: other workers' Devices add noise).
    comp = lambda: int(subprocess.run(["powershell", "-NoProfile", "-Command", "(Get-Process 'Memory Compression').WorkingSet64 / 1MB"],
                                      capture_output=True, text=True).stdout.strip().split(".")[0] or 0)
    c0 = res["compression_before_squeeze_mb"]
    res["compression_after_mb"] = []
    for _ in range(4):
        time.sleep(15); res["compression_after_mb"].append(comp())
    res["compression_growth_60s_mb"] = res["compression_after_mb"][-1] - c0
    res["memory_after"] = call("memory"); log("ws after 60 s", res["memory_after"]["ws_mb"], "compression", c0, "->", res["compression_after_mb"])
    res["taps_after"] = taps(); log("taps after", res["taps_after"])
    res["screenshot_ms"] = shot("after-squeeze"); res["app_pid"] = call("shell", cmd=f"pidof {PKG}")["out"]
    log("app pid", res["app_pid"])
finally:
    try: call("stop"); log("stopped d0")
    except Exception as e: log("stop failed", e)
    done.set(); dmn.terminate(); json.dump(res, open(f"{OUT}/result.json", "w"), indent=1)
