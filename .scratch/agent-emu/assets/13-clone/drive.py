# Drive CoW clones for a while and log memory every 30 s. Never touches Devices outside --ids.
# usage: python drive.py <tag> --ids 13,14,15 --minutes 10 [--cap-main 300 --cap-helper 16] [--relaunch-at 5]
# Per clone, every 30 s: privatized guest pages (from the clone's crosvm.log), private and total
# working set of its crosvm processes. Host: Available, Memory Compression WS, pagefile %.
# Guard: Available < 3000 MB stops the newest of these clones.
import argparse, ctypes, ctypes.wintypes as wt, json, os, re, socket, subprocess, sys, time, uuid

X = "C:/dev/agent-emu-work/clone-exp"
ap = argparse.ArgumentParser(); ap.add_argument("tag"); ap.add_argument("--ids", default="13,14,15")
ap.add_argument("--minutes", type=float, default=10); ap.add_argument("--relaunch-at", type=float, default=5)
ap.add_argument("--cap-main", type=int, default=0); ap.add_argument("--cap-helper", type=int, default=16)
a = ap.parse_args(); IDS = [int(i) for i in a.ids.split(",")]
OUT = f"{X}/out/{a.tag}"; os.makedirs(OUT, exist_ok=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True); k32.OpenProcess.restype = wt.HANDLE
k32.SetProcessWorkingSetSizeEx.argtypes = [wt.HANDLE, ctypes.c_size_t, ctypes.c_size_t, wt.DWORD]

PS = r"""
$all = Get-CimInstance Win32_Process -Filter "Name='crosvm.exe'"
$perf = Get-CimInstance Win32_PerfFormattedData_PerfProc_Process | Where-Object { $_.Name -like 'crosvm*' }
$c = (Get-Counter '\Memory\Available MBytes','\Paging File(_total)\% Usage').CounterSamples
$o = @{ available_mb = [int]$c[0].CookedValue; pagefile_pct = [math]::Round($c[1].CookedValue, 2);
        memcomp_ws_mb = [int]((Get-Process 'Memory Compression').WorkingSet64 / 1MB); dev = @{} }
foreach ($b in ($all | Where-Object { $_.CommandLine -match 'ae-vm-\d+ ' -and $_.CommandLine -match 'crosvm-clone' })) {
  $id = [regex]::Match($b.CommandLine, 'ae-vm-(\d+) ').Groups[1].Value
  $kids = @($all | Where-Object { $_.ParentProcessId -eq $b.ProcessId })
  $procs = @(@{ pid = $b.ProcessId; main = $false }) + @($kids | ForEach-Object { @{ pid = $_.ProcessId; main = ($_.CommandLine -match 'run-main') } })
  $ws = 0; $priv = 0
  foreach ($p in $perf | Where-Object { ($procs | ForEach-Object { $_.pid }) -contains $_.IDProcess }) { $ws += $p.WorkingSet; $priv += $p.WorkingSetPrivate }
  $o.dev[$id] = @{ ws_mb = [int]($ws / 1MB); private_ws_mb = [int]($priv / 1MB); procs = $procs }
}
$o | ConvertTo-Json -Depth 5 -Compress
"""
open(f"{OUT}/sample.ps1", "w").write(PS)


def sample():
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", f"{OUT}/sample.ps1"], capture_output=True, text=True)
    return json.loads(r.stdout)


def privatized_mb(i):
    log = subprocess.run(["iconv", "-f", "UTF-16LE", "-t", "UTF-8", f"{X}/d{i}/crosvm.log"], capture_output=True, text=True).stdout
    m = re.findall(r"cow pages: (\d+) private", log)
    return int(m[-1]) // 256 if m else None


def gsh(port, cmd, timeout=60):
    marker = "__AE_" + uuid.uuid4().hex[:8] + "__"
    s = socket.create_connection(("127.0.0.1", port), timeout=10); s.settimeout(1)
    t0 = time.perf_counter(); s.sendall(f"su 0 sh -c '{cmd}'; echo {marker}\n".encode()); out = b""
    while time.perf_counter() - t0 < timeout:
        try: d = s.recv(1 << 16)
        except socket.timeout: continue
        if not d: break
        out += d
        if ("\n" + marker).encode() in out.replace(b"\r", b""): break
    s.close()
    t = out.decode(errors="replace").replace("\r", "")
    body = "\n".join(l for l in t.split("\n") if marker not in l and not l.startswith(("su 0", "<", "console:"))).strip()
    return round((time.perf_counter() - t0) * 1000), body


def cap(procs, main, helper):
    for p in procs:
        h = k32.OpenProcess(0x0100 | 0x0400, False, p["pid"])
        ok = k32.SetProcessWorkingSetSizeEx(h, 1 << 20, (main if p["main"] else helper) << 20, 0x4 | 0x2)  # hard max, min disabled
        k32.CloseHandle(h)
        if not ok: print("cap failed", p, ctypes.get_last_error(), flush=True)


res = {"args": vars(a), "samples": [], "taps": []}
s0 = sample(); res["host_before"] = {k: s0[k] for k in ("available_mb", "pagefile_pct", "memcomp_ws_mb")}
if a.cap_main:
    for i in IDS: cap(s0["dev"][str(i)]["procs"], a.cap_main, a.cap_helper)
    res["capped"] = {"main_mb": a.cap_main, "helper_mb": a.cap_helper}
t0 = time.time(); live = list(IDS); relaunched = False; next_sample = t0
while time.time() - t0 < a.minutes * 60:
    if not relaunched and time.time() - t0 >= a.relaunch_at * 60:
        for i in live:
            ms, o = gsh(7100 + i, "am force-stop com.boltbetz.staging; am start -W -n com.boltbetz.staging/com.boltbetz.MainActivity | grep -E \"Status|TotalTime\"", 120)
            res.setdefault("relaunch", {})[i] = o
        relaunched = True
    for i in live:
        try:
            ms, _ = gsh(7100 + i, "input tap 360 780", 30); res["taps"].append({"id": i, "t": round(time.time() - t0), "ms": ms})
        except OSError as e:
            res["taps"].append({"id": i, "t": round(time.time() - t0), "err": str(e)})
    if time.time() >= next_sample:
        s = sample(); row = {"t": round(time.time() - t0), "available_mb": s["available_mb"], "pagefile_pct": s["pagefile_pct"],
                             "memcomp_ws_mb": s["memcomp_ws_mb"]}
        for i in live:
            d = s["dev"].get(str(i), {})
            row[f"d{i}"] = {"privatized_mb": privatized_mb(i), "private_ws_mb": d.get("private_ws_mb"), "ws_mb": d.get("ws_mb")}
        res["samples"].append(row); print(json.dumps(row), flush=True)
        if s["available_mb"] < 3000 and live:  # stop the newest of mine
            n = live.pop(); subprocess.run([sys.executable, f"{X}/clone_exp.py", "stop", str(n)], capture_output=True)
            res.setdefault("guard_stopped", []).append({"id": n, "t": row["t"], "available_mb": s["available_mb"]})
        next_sample += 30
    time.sleep(10)
res["alive"] = {i: gsh(7100 + i, "pidof com.boltbetz.staging")[1] for i in live}
for i in live:
    r = subprocess.run([sys.executable, f"{X}/clone_exp.py", "check", str(i), f"{a.tag}-d{i}"], capture_output=True, text=True, timeout=400)
    res[f"check{i}"] = json.loads(r.stdout[r.stdout.index("{"):]) if "{" in r.stdout else r.stdout[-500:] + r.stderr[-500:]
taps = [t["ms"] for t in res["taps"] if "ms" in t]
if taps:
    taps.sort(); res["tap_ms_p50_p95_max"] = [taps[len(taps) // 2], taps[int(len(taps) * .95)], taps[-1]]
json.dump(res, open(f"{OUT}/result.json", "w"), indent=1)
print(json.dumps({k: v for k, v in res.items() if k not in ("samples", "taps")}, indent=1))
