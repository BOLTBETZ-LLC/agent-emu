# Where swipe -> first frame goes, guest side. Boots d0 (slim5, 896 MB, AE_CPUS vCPUs, uncapped), opens the
# proof app, records the renderer (ro.hardware.egl, debug.hwui.renderer, SurfaceFlinger GLES line), then runs
# 24 carousel swipes (3 pages forward, 3 back, so never against an edge) with `top -H` sampling every 0.5 s
# in the guest and `dumpsys gfxinfo <pkg> framestats` reset before and read after. Reports per-frame HWUI
# phases (UI thread, sync, RenderThread draw, swap) and the busiest threads. Stops d0.
# usage: [AE_CPUS=2] [AE_CROSVM_DIR=crosvm-diet] python prof_smoke.py [out_dir]
import json, os, socket, statistics, subprocess, sys, threading, time

D = os.path.dirname(os.path.abspath(__file__)); EXE = f"{D}/target/release/agent-emud.exe"; W = "C:/dev/agent-emu-work"
OUT = sys.argv[1] if len(sys.argv) > 1 else f"{W}/results/prof"; os.makedirs(OUT, exist_ok=True)
PKG, ACT = "com.boltbetz.staging", "com.boltbetz.staging/com.boltbetz.MainActivity"
INSTALL = (f"head -c {int(open(f'{W}/run-slim5/apk.size').read())} /dev/block/vdb > /data/local/tmp/p.apk && "
           "chmod 644 /data/local/tmp/p.apk && pm install -r /data/local/tmp/p.apk; rm -f /data/local/tmp/p.apk")

def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)

def call(c, **kw):
    s = socket.create_connection(("127.0.0.1", 7400)); f = s.makefile("rb")
    s.sendall((json.dumps({"id": 1, "call": c, "device": "d0", **kw}) + "\n").encode()); r = json.loads(f.readline()); s.close()
    if not r.get("ok"): raise RuntimeError(f"{c}: {r.get('error')}")
    return r

def sh(cmd, t=120): return call("shell", cmd=cmd, timeout_s=t)["out"]

def ps(cmd): return subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True).stdout.strip()

done = threading.Event()
def watch():
    while not done.wait(30): log("host Available", ps(r"(Get-Counter '\Memory\Available MBytes').CounterSamples[0].CookedValue"), "MB")
threading.Thread(target=watch, daemon=True).start()

def q(xs, p):
    xs = sorted(xs); return round(xs[min(len(xs) - 1, int(len(xs) * p))], 1) if xs else None

def framestats(text):
    """HWUI framestats rows -> per-frame phases in ms (Flags != 0 rows are skipped, as gfxinfo does)."""
    rows, hdr = [], None
    for line in text.splitlines():
        line = line.strip().rstrip(",")
        if line.startswith("Flags,"): hdr = line.split(","); continue
        if hdr and line and line[0].isdigit():
            v = line.split(",")
            if len(v) >= len(hdr) - 1 and v[0] == "0":
                rows.append({k: int(x) for k, x in zip(hdr, v) if x.lstrip("-").isdigit()})
    ms = lambda a, b, r: (r[b] - r[a]) / 1e6
    out = []
    for r in rows:
        if not r.get("FrameCompleted") or not r.get("IntendedVsync"): continue
        out.append({"ui_thread": ms("Vsync", "SyncQueued", r), "sync": ms("SyncQueued", "SyncStart", r) + ms("SyncStart", "IssueDrawCommandsStart", r),
                    "render_draw": ms("IssueDrawCommandsStart", "SwapBuffers", r), "swap": ms("SwapBuffers", "FrameCompleted", r),
                    "total": ms("IntendedVsync", "FrameCompleted", r), "input_handling": ms("HandleInputStart", "AnimationStart", r) if r.get("HandleInputStart") else 0})
    return out

def top_threads(text):
    """`top -H -b -o TID,%CPU,CMD,NAME` samples -> mean %CPU per (process, thread) over all samples."""
    acc, samples = {}, 0
    for line in text.splitlines():
        p = line.split()
        if p and p[0] == "TID": samples += 1; continue
        if len(p) >= 4 and p[0].isdigit():
            try: cpu = float(p[1])
            except ValueError: continue
            key = f"{p[3]}:{p[2]}"; acc[key] = acc.get(key, 0) + cpu
    return sorted(((k, round(v / max(samples, 1), 1)) for k, v in acc.items()), key=lambda x: -x[1])[:15], samples

dmn = subprocess.Popen([EXE], env=dict(os.environ, AE_ALLOW_OTHER_CROSVM=os.environ.get("AE_ALLOW_OTHER_CROSVM", "1")),
                       stdout=open(f"{OUT}/daemon.log", "w"), stderr=subprocess.STDOUT)
res = {"cpus": os.environ.get("AE_CPUS", "2"), "host_cpu_pct_before": ps(r"[int](Get-Counter '\Processor(_Total)\% Processor Time').CounterSamples[0].CookedValue")}
try:
    time.sleep(1)
    res["ready_s"] = round(call("start", image="slim5", mem=896, net=True)["ready_s"], 1); log("ready", res["ready_s"])
    res["install"] = sh(INSTALL, 300)
    sh(f"am start -W -n {ACT}"); time.sleep(20)
    res["renderer"] = sh("echo egl=$(getprop ro.hardware.egl) vulkan=$(getprop ro.hardware.vulkan) hwui=$(getprop debug.hwui.renderer) "
                         "sf_re=$(getprop debug.renderengine.backend) gpu_mode=$(getprop ro.boot.hardware.gltransport); "
                         "dumpsys SurfaceFlinger | grep -m 3 -iE 'GLES:|RenderEngine|Vulkan'")
    res["cpuinfo"] = sh("nproc; grep -m1 'model name' /proc/cpuinfo")
    log("renderer", res["renderer"])
    sh(f"dumpsys gfxinfo {PKG} reset >/dev/null")
    sh("rm -f /data/local/tmp/top.txt; (top -H -b -d 0.5 -n 40 -o TID,%CPU,CMD,NAME > /data/local/tmp/top.txt 2>&1 &)")
    ff, steps = [], ([True] * 3 + [False] * 3) * 4
    for fwd in steps:
        x1, x2 = (600, 120) if fwd else (120, 600)
        r = call("swipe", x1=x1, y1=150, x2=x2, y2=150, ms=150, deadline_ms=1500)
        ff.append(r.get("first_frame_ms")); time.sleep(0.4)
    xs = [x for x in ff if x is not None]
    res["swipe_first_frame"] = {"p50": q(xs, .5), "p95": q(xs, .95), "frames": len(xs), "n": len(ff), "raw": ff}
    time.sleep(2)
    fs = sh(f"dumpsys gfxinfo {PKG} framestats", 60); open(f"{OUT}/framestats.txt", "w").write(fs)
    top = sh("cat /data/local/tmp/top.txt", 60); open(f"{OUT}/top.txt", "w").write(top)
    frames = framestats(fs)
    res["hwui_frames"] = len(frames)
    res["hwui_ms"] = {k: {"p50": q([f[k] for f in frames], .5), "p95": q([f[k] for f in frames], .95)} for k in (frames[0] if frames else {})}
    res["gfxinfo_summary"] = "\n".join(l.strip() for l in fs.splitlines() if any(t in l for t in ("Total frames", "Janky", "50th", "90th", "95th", "99th", "Slow UI", "Slow bitmap", "Slow issue", "Frame deadline")))
    res["top_threads"], res["top_samples"] = top_threads(top)
    res["host_cpu_pct_after"] = ps(r"[int](Get-Counter '\Processor(_Total)\% Processor Time').CounterSamples[0].CookedValue")
    log("swipe first frame", {k: v for k, v in res["swipe_first_frame"].items() if k != "raw"})
    log("hwui", res["hwui_frames"], "frames", res["hwui_ms"]); log("gfxinfo", res["gfxinfo_summary"].replace("\n", " | "))
    log("top threads", res["top_threads"][:10], "samples", res["top_samples"])
finally:
    try: call("stop"); log("stopped d0")
    except Exception as e: log("stop failed", e)
    done.set(); dmn.terminate(); json.dump(res, open(f"{OUT}/result.json", "w"), indent=1)
