# Input -> first frame on the fast path: a virtio-input HOME key over \\.\pipe\ae-kbd-<id>, then the
# first new flush in the Device's fb.bin (flush count, u64 at header offset 32), polled every ~1 ms.
# After each HOME the app is brought back over the console and left to settle (not timed).
# usage: python latency.py <tag> --ids 13,14,15 --n 20
import argparse, json, mmap, os, socket, struct, time, uuid

X = "C:/dev/agent-emu-work/clone-exp"
ap = argparse.ArgumentParser(); ap.add_argument("tag"); ap.add_argument("--ids", default="13,14,15"); ap.add_argument("--n", type=int, default=20); ap.add_argument("--warmup", type=int, default=0)
a = ap.parse_args(); IDS = [int(i) for i in a.ids.split(",")]
EV_SYN, EV_KEY, KEY_HOME = 0, 1, 172


def ev(ty, code, val): return struct.pack("<HHi", ty, code, val)


def gsh(port, cmd, timeout=60):
    marker = "__AE_" + uuid.uuid4().hex[:8] + "__"
    s = socket.create_connection(("127.0.0.1", port), timeout=10); s.settimeout(1)
    s.sendall(f"su 0 sh -c '{cmd}'; echo {marker}\n".encode()); out, end = b"", time.time() + timeout
    while time.time() < end:
        try: d = s.recv(1 << 16)
        except socket.timeout: continue
        if not d: break
        out += d
        if ("\n" + marker).encode() in out.replace(b"\r", b""): break
    s.close()


def pct(v, q): v = sorted(v); return v[min(len(v) - 1, int(len(v) * q))]


res = {"ids": IDS, "n": a.n}
for i in IDS:
    f = open(f"{X}/d{i}/fb.bin", "rb"); fb = mmap.mmap(f.fileno(), 64, access=mmap.ACCESS_READ)
    flushes = lambda: struct.unpack_from("<Q", fb, 32)[0]
    kbd = open(rf"\\.\pipe\ae-kbd-{i}", "wb", buffering=0)
    ms, misses = [], 0
    for k in range(a.warmup):  # untimed HOME + relaunch: first-touch costs land here, not in the sample
        gsh(7100 + i, "am start -W -n com.boltbetz.staging/com.boltbetz.MainActivity >/dev/null", 60); time.sleep(1.5)
        kbd.write(ev(EV_KEY, KEY_HOME, 1) + ev(EV_SYN, 0, 0) + ev(EV_KEY, KEY_HOME, 0) + ev(EV_SYN, 0, 0)); time.sleep(1.5)
    for k in range(a.n):
        gsh(7100 + i, "am start -W -n com.boltbetz.staging/com.boltbetz.MainActivity >/dev/null", 60); time.sleep(1.5)
        s0 = flushes(); t0 = time.perf_counter()
        kbd.write(ev(EV_KEY, KEY_HOME, 1) + ev(EV_SYN, 0, 0) + ev(EV_KEY, KEY_HOME, 0) + ev(EV_SYN, 0, 0))
        while time.perf_counter() - t0 < 2.0:
            if flushes() != s0:
                ms.append(round((time.perf_counter() - t0) * 1000, 1)); break
            time.sleep(0.001)
        else:
            misses += 1
    gsh(7100 + i, "am start -W -n com.boltbetz.staging/com.boltbetz.MainActivity >/dev/null", 60)
    res[f"d{i}"] = {"ms": ms, "misses": misses, "p50": pct(ms, .5) if ms else None, "p95": pct(ms, .95) if ms else None}
    print(i, res[f"d{i}"], flush=True)
allms = [m for i in IDS for m in res[f"d{i}"]["ms"]]
res["all_p50_p95"] = [pct(allms, .5), pct(allms, .95)] if allms else None
os.makedirs(f"{X}/out/{a.tag}", exist_ok=True); json.dump(res, open(f"{X}/out/{a.tag}/latency.json", "w"), indent=1)
print(json.dumps({"all_p50_p95": res["all_p50_p95"]}))
