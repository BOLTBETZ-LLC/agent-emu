# GPU Device 40 measurements (gfxstream, 1320x2868): input -> first frame on carousel swipes, sustained
# scanout fps over 5 s of continuous dragging, gfxinfo frame times, host CPU/RAM/GPU memory.
# Frames are counted from fb.bin's flush counter (u64 at offset 32), written by crosvm on every scanout flush.
# usage: python measure.py <out.json>
import json, mmap, statistics, struct, subprocess, sys, time

DEV = __import__("os").environ.get("AE_ID", "40")
FB = rf"C:\dev\agent-emu-work\gpu\d{DEV}\fb.bin"
TOUCH = rf"\\.\pipe\ae-touch-{DEV}"
G = "C:/dev/agent-emu-work/gpu"
SX = int(__import__("os").environ.get("AE_W", "1320")) / 1320
Y, XL, XR = int(700 * SX), int(250 * SX), int(1100 * SX)  # carousel card band

f = open(FB, "rb")
m = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
flushes = lambda: struct.unpack_from("<Q", m, 32)[0]
pipe = open(TOUCH, "wb", buffering=0)


def ev(t, c, v):
    return struct.pack("<HHi", t, c, v)


def touch(x, y, first=False, up=False, tid=1):
    b = ev(3, 0x2F, 0)
    if up:
        b += ev(3, 0x39, -1) + ev(1, 0x14A, 0)
    else:
        if first:
            b += ev(3, 0x39, tid)
        b += ev(3, 0x35, x) + ev(3, 0x36, y)
        if first:
            b += ev(1, 0x14A, 1)
        b += ev(3, 0, x) + ev(3, 1, y)
    pipe.write(b + ev(0, 0, 0))


def sh(cmd, t=60):
    return subprocess.run(["bash", f"{G}/g.sh", cmd, str(t)], capture_output=True, text=True).stdout


def stats():
    o = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", f"{G}/procstats.ps1"],
                       capture_output=True, text=True).stdout
    return json.loads(o.strip().splitlines()[-1])


def wait_idle(quiet=0.3, limit=5.0):
    end = time.perf_counter() + limit
    last, since = flushes(), time.perf_counter()
    while time.perf_counter() < end:
        time.sleep(0.002)
        n = flushes()
        if n != last:
            last, since = n, time.perf_counter()
        elif time.perf_counter() - since >= quiet:
            return True
    return False


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))]


def swipe_first_frame(x1, x2, ms=150):
    """Timed swipe with a watcher on the flush counter; returns ms from first touch event to first new frame."""
    base = flushes()
    t0 = time.perf_counter()
    touch(x1, Y, first=True)
    steps = max(1, ms // 8)
    first = None
    for i in range(1, steps + 1):
        target = t0 + ms / 1000 * i / steps
        while time.perf_counter() < target:
            if first is None and flushes() != base:
                first = time.perf_counter()
        touch(int(x1 + (x2 - x1) * i / steps), Y)
    touch(0, 0, up=True)
    end = t0 + 1.5
    while first is None and time.perf_counter() < end:
        if flushes() != base:
            first = time.perf_counter()
    return None if first is None else (first - t0) * 1000


out = {"start": stats()}
wait_idle(1.0, 10)
i0 = stats(); idle0 = flushes(); time.sleep(10); i1 = stats()
out["idle_frames_10s"] = flushes() - idle0
out["idle_cpu_cores"] = round((i1["cpu_s_sum"] - i0["cpu_s_sum"]) / (i1["t"] - i0["t"]), 3)
print("idle", out["idle_frames_10s"], out["idle_cpu_cores"], flush=True)

# A: input -> first frame, 20 carousel swipes (alternating so the pager stays on pages 0-1).
ff = []
for i in range(22):
    wait_idle()
    x1, x2 = (XR, XL) if i % 2 == 0 else (XL, XR)
    v = swipe_first_frame(x1, x2)
    if i >= 2 and v is not None:
        ff.append(v)
    time.sleep(0.4)
out["swipe_first_frame_ms"] = {"n": len(ff), "p50": round(pct(ff, 50), 1), "p95": round(pct(ff, 95), 1),
                               "max": round(max(ff), 1), "raw": [round(x, 1) for x in ff]}
print("first frame", out["swipe_first_frame_ms"], flush=True)

# B: 5 s of continuous dragging (finger held, sweeping across the card), frames counted from the scanout.
sh("dumpsys gfxinfo com.boltbetz.staging reset >/dev/null")
wait_idle()
s0 = stats()
n0, t0 = flushes(), time.perf_counter()
times = []
touch(XR, Y, first=True)
k, last = 0, n0
while time.perf_counter() - t0 < 5.0:
    tgt = t0 + k * 0.008
    while time.perf_counter() < tgt:
        n = flushes()
        if n != last:
            times.append(time.perf_counter()); last = n
    ph = (k * 0.008) % 1.0  # one sweep right->left->right per second
    x = XR - (XR - XL) * (2 * ph if ph < 0.5 else 2 - 2 * ph)
    touch(int(x), Y)
    k += 1
touch(0, 0, up=True)
dt = time.perf_counter() - t0
n1 = flushes()
s1 = stats()
gaps = [(b - a) * 1000 for a, b in zip(times, times[1:])]
out["drag_5s"] = {"frames": n1 - n0, "seconds": round(dt, 2), "fps": round((n1 - n0) / dt, 1),
                  "gap_ms_p50": round(pct(gaps, 50), 1) if gaps else None,
                  "gap_ms_p95": round(pct(gaps, 95), 1) if gaps else None,
                  "gaps_over_20ms": sum(g > 20 for g in gaps),
                  "steady_fps": round((len(times) - 1) / (times[-1] - times[0]), 1) if len(times) > 1 else None,
                  "host_cpu_pct_of_one_core": round((s1["cpu_s_sum"] - s0["cpu_s_sum"]) / (s1["t"] - s0["t"]) * 100, 1)}
print("drag", out["drag_5s"], flush=True)
out["gfxinfo"] = sh("dumpsys gfxinfo com.boltbetz.staging | grep -E 'Total frames|Janky|percentile|Number '")
out["end"] = stats()
json.dump(out, open(sys.argv[1], "w"), indent=1)
print(json.dumps({k: v for k, v in out.items() if k != "swipe_first_frame_ms"}, indent=1))
