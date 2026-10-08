"""8-phone proof against a running agent-emud (default http://127.0.0.1:7401).

Boots N lean iPhone gfxstream phones one at a time (each: start, install BoltBetz, launch with auto_squeeze),
stopping at the first failure (the daemon refuses a boot under its 4000 MB Available floor). Then:
  - 60 s of /events metrics: RAM ws/own/shared per phone, host Available, and growth of the host
    "Memory Compression" process since the baseline taken before the first boot
  - per phone, one at a time: ~5 s of continuous carousel drags; frames the guest posted (scanout fps,
    daemon metrics) and frames delivered to a /mux tile
  - idle CPU per phone over 5 s (cores, summed over each phone's crosvm processes)
  - a screenshot of every phone
Writes <out>/proof8.json and <out>/proof8.md. Leaves the phones running.

  python proof8.py [--n 8] [--native] [--base http://127.0.0.1:7401] [--out DIR] [--first 0]
"""
import argparse
import json
import os
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

PKG = "com.boltbetz.staging"
CPU_PS = r"""
$all = Get-CimInstance Win32_Process -Filter "name='crosvm.exe'"
$map = @{}
foreach ($b in $all | Where-Object { $_.CommandLine -match 'run-mp' -and $_.CommandLine -match 'ae-vm-(\d+)' }) { $map[$b.ProcessId] = "d" + $matches[1] }
foreach ($p in $all) { if ($map.ContainsKey($p.ParentProcessId)) { $map[$p.ProcessId] = $map[$p.ParentProcessId] } }
$t0 = @{}; foreach ($k in $map.Keys) { $q = Get-Process -Id $k -ErrorAction SilentlyContinue; if ($q) { $t0[$k] = $q.TotalProcessorTime.TotalSeconds } }
Start-Sleep -Seconds SECS
$use = @{}
foreach ($k in $t0.Keys) { $q = Get-Process -Id $k -ErrorAction SilentlyContinue; if ($q) { $use[$map[$k]] += ($q.TotalProcessorTime.TotalSeconds - $t0[$k]) / SECS } }
$use | ConvertTo-Json -Compress
"""


class Daemon:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.events = []
        threading.Thread(target=self._listen, daemon=True).start()

    def api(self, body, timeout=900):
        req = urllib.request.Request(self.base + "/api", json.dumps(body).encode())
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())

    def _listen(self):
        while True:
            try:
                for raw in urllib.request.urlopen(self.base + "/events", timeout=3600):
                    line = raw.decode().strip()
                    if line.startswith("data: "):
                        self.events.append(json.loads(line[6:]))
            except Exception as e:  # reconnect; the proof keeps going
                print("events:", e, file=sys.stderr)
                time.sleep(1)

    def metrics(self, t0, t1, dev=None):
        rows = [(e, x) for e in self.events if e["type"] == "metrics" and t0 <= e["ts"] / 1000 <= t1 for x in e["devices"]]
        return [(e, x) for e, x in rows if dev is None or x["id"] == dev]


def compression_mb():
    """Working set of the host "Memory Compression" process (MB), or None if it cannot be read."""
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "(Get-Process -Name 'Memory Compression' -ErrorAction SilentlyContinue).WorkingSet64"],
                         capture_output=True, text=True).stdout.strip()
    return round(int(out) / 2**20) if out.isdigit() else None


def idle_cores(secs=5):
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False) as f:
        f.write(CPU_PS.replace("SECS", str(secs)))
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", f.name],
                             capture_output=True, text=True).stdout.strip()
        return {k: round(v, 3) for k, v in json.loads(out or "{}").items()}
    finally:
        os.unlink(f.name)


def boot(dm, dev, screen):
    t0 = time.time()
    body = {"call": "start", "device": dev, "image": "slim5", "auto_squeeze": True}
    if screen:
        body["screen"] = screen
    r = dm.api(body)
    if not r.get("ok"):
        return {"device": dev, "ok": False, "error": r.get("error")}
    i = dm.api({"call": "install_bundled", "device": dev})
    a = dm.api({"call": "app", "device": dev, "launch": PKG})
    sq = a.get("auto_squeeze") or {}
    return {"device": dev, "ok": bool(i.get("ok") and a.get("ok")), "ready_s": round(r["ready_s"], 1),
            "available_mb_before": r.get("available_mb_before"), "ws_before_squeeze_mb": sq.get("ws_before_mb"),
            "ws_after_squeeze_mb": sq.get("ws_after_mb"), "total_s": round(time.time() - t0, 1),
            "error": i.get("error") or a.get("error")}


def animate(dm, dev, w, h, secs=5.0):
    """Continuous carousel drags on `dev` for `secs`; returns posted (scanout) and delivered (tile) fps."""
    stop, tile = threading.Event(), []

    def drive():
        i = 0
        while not stop.is_set():
            x1, x2 = (int(w * .85), int(w * .15)) if i % 2 == 0 else (int(w * .15), int(w * .85))
            dm.api({"call": "swipe", "device": dev, "x1": x1, "y1": int(h * .35), "x2": x2, "y2": int(h * .35),
                    "ms": 250, "device_px": True, "screenshot": False}, timeout=30)
            i += 1

    def mux():
        r = urllib.request.urlopen(f"{dm.base}/mux?devices={dev}&max_width=220&quality=60", timeout=30)
        while not stop.is_set():
            head = r.read(24)
            if len(head) < 24:
                return
            r.read(struct.unpack("<IQIII", head)[0])
            tile.append(time.time())

    for f in (drive, mux):
        threading.Thread(target=f, daemon=True).start()
    time.sleep(2)  # animation running before the window opens
    t0 = time.time()
    time.sleep(secs)
    t1 = time.time()
    stop.set()
    rows = [x for _, x in dm.metrics(t0, t1, dev)]
    return {"posted_fps": round(sum(x["scanout_fps"] for x in rows) / max(len(rows), 1), 1),
            "delivered_fps": round(len([t for t in tile if t0 <= t <= t1]) / (t1 - t0), 1)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:7401")
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--first", type=int, default=0, help="first device id (d<first> .. d<first+n-1>)")
    ap.add_argument("--native", action="store_true", help="iPhone 17 Pro Max native 1320x2868 instead of lean 656x1424")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "proof8-" + time.strftime("%Y%m%d-%H%M%S")))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    dm = Daemon(a.base)
    screen = "iphone17promax-native" if a.native else ""
    base = {"available_mb": dm.api({"call": "status"})["available_mb"], "compression_mb": compression_mb(), "t": time.time()}
    print("baseline", base, flush=True)

    boots = []
    for i in range(a.first, a.first + a.n):
        b = boot(dm, f"d{i}", screen)
        boots.append(b)
        print(b, flush=True)
        if not b["ok"]:
            break
    up = [b["device"] for b in boots if b["ok"]]

    print("60 s of metrics", flush=True)
    t0 = time.time()
    time.sleep(60)
    t1 = time.time()
    ram = {}
    for d in up:
        rows = [x for _, x in dm.metrics(t0, t1, d) if x.get("ws_mb") is not None]
        last = rows[-1] if rows else {}
        ram[d] = {k: last.get(k) for k in ("ws_mb", "own_mb", "shared_mb")}
    avail = [e["available_mb"] for e, _ in dm.metrics(t0, t1)] or [None]
    comp = compression_mb()

    status = {x["id"]: x for x in dm.api({"call": "status"})["devices"]}
    anim = {}
    for d in up:
        s = status[d]["screen"]
        anim[d] = animate(dm, d, s["width"], s["height"])
        print(d, anim[d], flush=True)
    time.sleep(3)  # let the last animation finish before the idle sample
    cpu = idle_cores()
    for d in up:
        urllib.request.urlretrieve(f"{a.base}/screenshot.png?device={d}", os.path.join(a.out, f"{d}.png"))

    res = {"baseline": base, "native": a.native, "boots": boots, "ram": ram, "anim": anim, "idle_cores": cpu,
           "host": {"available_mb_min_60s": min(x for x in avail if x is not None) if avail[0] is not None else None,
                    "compression_mb": comp,
                    "compression_growth_mb": comp - base["compression_mb"] if comp is not None and base["compression_mb"] is not None else None}}
    with open(os.path.join(a.out, "proof8.json"), "w") as f:
        json.dump(res, f, indent=1)
    lines = [f"# {len(up)} of {a.n} phones, {'native 1320x2868' if a.native else 'lean 656x1424'}, gfxstream", "",
             f"Host: Available before {base['available_mb']} MB, lowest over the 60 s {res['host']['available_mb_min_60s']} MB; "
             f"Memory Compression {base['compression_mb']} -> {comp} MB (growth {res['host']['compression_growth_mb']} MB).", "",
             "| Phone | Ready s | RAM ws MB | own MB | shared MB | Posted fps (drag) | Delivered tile fps | Idle cores |",
             "|---|---|---|---|---|---|---|---|"]
    for b in boots:
        d = b["device"]
        if not b["ok"]:
            lines.append(f"| {d} | failed: {b['error']} | | | | | | |")
            continue
        r, m = ram.get(d, {}), anim.get(d, {})
        lines.append(f"| {d} | {b['ready_s']} | {r.get('ws_mb')} | {r.get('own_mb')} | {r.get('shared_mb')} | "
                     f"{m.get('posted_fps')} | {m.get('delivered_fps')} | {cpu.get(d)} |")
    with open(os.path.join(a.out, "proof8.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print("wrote", a.out)


if __name__ == "__main__":
    main()
