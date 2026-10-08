# Headless display + stale-pixel check for agent-emud: boot 1 Device, list crosvm's desktop windows
# (EnumWindows), install the proof app, then with HW overlays on (old behaviour) and off (Device setup
# default): open the app, HOME, and save the fast frame and the guest's own `screencap`. Stops the Device.
# usage: python display_smoke.py [out_dir]
import base64, ctypes, ctypes.wintypes as wt, io, json, os, socket, subprocess, sys, time
from PIL import Image, ImageChops, ImageStat

D = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1] if len(sys.argv) > 1 else "C:/dev/agent-emu-work/results/display"
os.makedirs(OUT, exist_ok=True)
W = "C:/dev/agent-emu-work"
APP = "com.boltbetz.staging/com.boltbetz.MainActivity"
INSTALL = (f"head -c {int(open(f'{W}/run/apk.size').read())} /dev/block/vdb > /data/local/tmp/p.apk && "
           "chmod 644 /data/local/tmp/p.apk && pm install -r /data/local/tmp/p.apk; rm -f /data/local/tmp/p.apk")

class Client:
    def __init__(s):
        s.sock = socket.create_connection(("127.0.0.1", 7400)); s.f = s.sock.makefile("rb")
    def call(s, call, **kw):
        s.sock.sendall((json.dumps({"id": 1, "call": call, "device": "d0", **kw}) + "\n").encode())
        r = json.loads(s.f.readline())
        if not r.get("ok"): raise RuntimeError(f"{call}: {r.get('error')}")
        return r

def crosvm_windows():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq crosvm.exe", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
    pids = {int(l.split(",")[1].strip('"')) for l in out.splitlines() if l.startswith('"crosvm')}
    u, rows = ctypes.windll.user32, []
    def cb(h, _):
        pid = wt.DWORD(); u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pid.value in pids:
            t = ctypes.create_unicode_buffer(256); u.GetWindowTextW(h, t, 256)
            rows.append({"pid": pid.value, "visible": bool(u.IsWindowVisible(h)), "title": t.value})
        return True
    u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(cb), 0)
    return {"crosvm_processes": len(pids), "windows": rows}

def img(frame, name):
    b = base64.b64decode(frame["jpeg"]); open(f"{OUT}/{name}.jpg", "wb").write(b)
    return Image.open(io.BytesIO(b)).convert("RGB")

def diff(a, b):
    """Mean abs difference per channel (0-255) and % of pixels off by more than 40 (JPEG noise is lower)."""
    d = ImageChops.difference(a, b)
    big = sum(1 for p in d.convert("L").tobytes() if p > 40)
    return {"mean": round(sum(ImageStat.Stat(d).mean) / 3, 2), "pct_px_off": round(100 * big / (a.width * a.height), 2)}

daemon = subprocess.Popen([f"{D}/target/release/agent-emud.exe"], stdout=open(f"{OUT}/daemon.log", "w"), stderr=subprocess.STDOUT)
res = {}
try:
    time.sleep(1); c = Client()
    r = c.call("start"); res["ready_s"] = round(r["ready_s"], 1)
    res["status"] = c.call("status")["devices"]
    res["windows"] = crosvm_windows()
    print("windows", res["windows"], flush=True)
    res["install"] = c.call("shell", cmd=INSTALL, timeout_s=300)["out"]
    for name, overlays in (("before", 0), ("after", 1)):
        c.call("shell", cmd=f"service call SurfaceFlinger 1008 i32 {overlays}")
        c.call("key", name="home"); time.sleep(1)
        c.call("shell", cmd=f"am start -W -n {APP}"); time.sleep(4)
        img(c.call("screenshot")["frame"], f"{name}-1-app")
        h = c.call("key", name="home"); time.sleep(2)
        fast = img(c.call("screenshot")["frame"], f"{name}-2-home")
        cap = img(c.call("screenshot", via="console")["frame"], f"{name}-2-home-screencap")
        res[name] = {"home_first_frame_ms": h["first_frame_ms"],
                     "fast_vs_screencap": diff(fast, cap)}
        print(name, res[name], flush=True)
finally:
    try:
        Client().call("stop"); print("stopped d0")
    except Exception as e:
        print("stop failed", e)
    daemon.terminate()
    json.dump(res, open(f"{OUT}/result.json", "w"), indent=1)
    print(json.dumps(res, indent=1))
