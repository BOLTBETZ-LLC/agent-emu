# Experiment B: trim a settled Device's working set so Windows compresses idle guest pages.
# Measures host memory before/after the trim and how slow the next guest actions get.
# usage: python trim_exp.py <device id> <console port>
import ctypes, ctypes.wintypes as wt, json, socket, subprocess, sys, time, uuid

DEV, PORT = sys.argv[1], int(sys.argv[2])
psapi = ctypes.WinDLL("psapi"); k32 = ctypes.WinDLL("kernel32")
k32.OpenProcess.restype = wt.HANDLE

def ps(cmd):
    return subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True).stdout.strip()

def device_pids():
    out = ps("Get-CimInstance Win32_Process -Filter \"Name='crosvm.exe'\" | ForEach-Object { '{0}|{1}|{2}' -f $_.ProcessId,$_.ParentProcessId,$_.CommandLine }")
    rows = [l.split("|", 2) for l in out.splitlines() if l.count("|") >= 2]
    broker = [int(p) for p, pp, cl in rows if f"ae-vm-{DEV}" in cl and " run-mp " in f" {cl} "]
    return broker + [int(p) for p, pp, cl in rows if broker and int(pp) == broker[0]]

def host(pids):
    av = int(float(ps("(Get-Counter '\\Memory\\Available MBytes').CounterSamples[0].CookedValue")))
    mc = int(ps("(Get-Process 'Memory Compression').WorkingSet64") or 0) >> 20
    ws = sum(int(ps(f"(Get-Process -Id {p}).WorkingSet64") or 0) for p in pids) >> 20
    return {"available_mb": av, "compression_store_mb": mc, "device_ws_mb": ws}

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
    return round((time.perf_counter() - t0) * 1000), out.decode(errors="replace")

def actions():
    t_ps, _ = gsh("su 0 sh -c 'pidof com.boltbetz.staging'")
    t_shot, _ = gsh("su 0 sh -c 'screencap -p /data/local/tmp/t.png; ls -l /data/local/tmp/t.png'")
    t_tap, _ = gsh("su 0 input tap 360 780")
    return {"shell_ms": t_ps, "screencap_ms": t_shot, "tap_ms": t_tap}

pids = device_pids()
res = {"pids": pids, "before": host(pids), "actions_before": actions()}
for p in pids:  # PROCESS_SET_QUOTA | PROCESS_QUERY_INFORMATION
    h = k32.OpenProcess(0x0100 | 0x0400, False, p)
    psapi.EmptyWorkingSet(h); k32.CloseHandle(h)
time.sleep(30)
res["after_trim_30s"] = host(pids)
res["actions_after_trim"] = actions()   # first actions after trim pay the page-in cost
res["actions_after_trim_2nd"] = actions()
time.sleep(10)
res["after_actions"] = host(pids)
res["app_alive"] = "pidof" and gsh("su 0 sh -c 'pidof com.boltbetz.staging'")[1][-40:]
print(json.dumps(res, indent=1))
