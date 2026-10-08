# Experiment B2: hard-cap a Device's crosvm working sets (Windows enforces the max by paging out),
# then drive the guest for a few minutes and record working set, latency and app health.
# usage: python wscap_exp.py <device id> <console port> <cap MB for main process> [minutes]
import ctypes, ctypes.wintypes as wt, json, os, re, socket, subprocess, sys, time, uuid, base64

DEV, PORT, CAP_MB = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
MINUTES = float(sys.argv[4]) if len(sys.argv) > 4 else 3
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.OpenProcess.restype = wt.HANDLE
k32.SetProcessWorkingSetSizeEx.argtypes = [wt.HANDLE, ctypes.c_size_t, ctypes.c_size_t, wt.DWORD]
QUOTA_LIMITS_HARDWS_MAX_ENABLE = 0x4
QUOTA_LIMITS_HARDWS_MIN_DISABLE = 0x2

def ps(cmd):
    return subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True).stdout.strip()

def device_procs():
    out = ps("Get-CimInstance Win32_Process -Filter \"Name='crosvm.exe'\" | ForEach-Object { '{0}|{1}|{2}' -f $_.ProcessId,$_.ParentProcessId,$_.CommandLine }")
    rows = [l.split("|", 2) for l in out.splitlines() if l.count("|") >= 2]
    broker = [int(p) for p, pp, cl in rows if f"ae-vm-{DEV}" in cl and " run-mp " in f" {cl} "]
    return {int(p): cl for p, pp, cl in rows if broker and (int(p) == broker[0] or int(pp) == broker[0])}

def ws_mb(pids):
    return {p: int(ps(f"(Get-Process -Id {p}).WorkingSet64") or 0) >> 20 for p in pids}

def gsh(cmd, timeout=60):
    marker = "__AE_" + uuid.uuid4().hex[:8] + "__"
    s = socket.create_connection(("127.0.0.1", PORT)); s.settimeout(1)
    t0 = time.perf_counter(); s.sendall(f"{cmd}; echo {marker}\n".encode()); out = b""
    while time.perf_counter() - t0 < timeout:
        try: d = s.recv(65536)
        except socket.timeout: continue
        if not d: break
        out += d
        if ("\n" + marker).encode() in out.replace(b"\r", b""): break
    s.close()
    return round((time.perf_counter() - t0) * 1000), out.decode(errors="replace").replace("\r", "")

procs = device_procs()
main = [p for p, cl in procs.items() if "run-main" in cl]
res = {"procs": {p: cl.split('"')[-1].strip()[:30] for p, cl in procs.items()}, "cap_mb": CAP_MB, "ws_before": ws_mb(procs)}
for p, cl in procs.items():
    cap = CAP_MB if p in main else int(os.environ.get("AE_HELPER_CAP", "64"))  # helpers: block, gpu, broker, metrics
    h = k32.OpenProcess(0x0100 | 0x0400, False, p)
    ok = k32.SetProcessWorkingSetSizeEx(h, 1 << 20, cap << 20, QUOTA_LIMITS_HARDWS_MAX_ENABLE | QUOTA_LIMITS_HARDWS_MIN_DISABLE)
    res.setdefault("cap_set", {})[p] = bool(ok) or ctypes.get_last_error()
    k32.CloseHandle(h)
samples, end = [], time.time() + MINUTES * 60
while time.time() < end:
    t_shell, _ = gsh("su 0 sh -c 'pidof com.boltbetz.staging'")
    t_shot, _ = gsh("su 0 sh -c 'screencap -p /data/local/tmp/t.png'")
    t_tap, _ = gsh("su 0 input tap 360 780")
    samples.append({"t": round(time.time()), "shell_ms": t_shell, "screencap_ms": t_shot, "tap_ms": t_tap,
                    "ws_mb": sum(ws_mb(procs).values())})
    time.sleep(5)
res["samples"] = samples
res["ws_after"] = ws_mb(procs)
res["app_alive"] = gsh("su 0 sh -c 'pidof com.boltbetz.staging'")[1].split("\n")[1:2]
res["available_mb"] = int(float(ps("(Get-Counter '\\Memory\\Available MBytes').CounterSamples[0].CookedValue")))
res["pagefile_usage_pct"] = ps("(Get-Counter '\\Paging File(_total)\\% Usage').CounterSamples[0].CookedValue")
t = gsh("su 0 sh -c 'screencap -p /data/local/tmp/s.png; echo B64START; base64 -w 0 /data/local/tmp/s.png; echo; echo B64END'", 300)[1]
m = re.search(r"B64START\n(.*?)\nB64END", t, re.S)
if m:
    os.makedirs("C:/dev/agent-emu-work/results/wscap", exist_ok=True)
    open(f"C:/dev/agent-emu-work/results/wscap/cap{CAP_MB}.png", "wb").write(base64.b64decode(re.sub(r"\s", "", m.group(1))))
def pct(v, q): v = sorted(v); return v[min(len(v) - 1, int(len(v) * q))]
for k in ("shell_ms", "screencap_ms", "tap_ms", "ws_mb"):
    vals = [s[k] for s in samples]
    res[f"{k}_p50"], res[f"{k}_p95"], res[f"{k}_max"] = pct(vals, .5), pct(vals, .95), max(vals)
res.pop("samples")
print(json.dumps(res, indent=1))
