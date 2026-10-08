# Cap every Lever D Device (ids given) whose app launched >= 45 s ago (per the run's result.json), 200/16.
# Keeps uncapped boots from stacking up while the 10-Device run boots one at a time. Loops until all ids capped.
import ctypes, ctypes.wintypes as wt, json, subprocess, sys, time
R, IDS = sys.argv[1], [int(x) for x in sys.argv[2].split(",")]
k32 = ctypes.WinDLL("kernel32", use_last_error=True); k32.OpenProcess.restype = wt.HANDLE
k32.SetProcessWorkingSetSizeEx.argtypes = [wt.HANDLE, ctypes.c_size_t, ctypes.c_size_t, wt.DWORD]
def ps(c): return subprocess.run(["powershell", "-NoProfile", "-Command", c], capture_output=True, text=True).stdout
seen, done = {}, set()
while len(done) < len(IDS):
    try: devs = json.load(open(R)).get("devices", {})
    except Exception: devs = {}
    rows = [l.split("|", 2) for l in ps("Get-CimInstance Win32_Process -Filter \"Name='crosvm.exe'\" | % { '{0}|{1}|{2}' -f $_.ProcessId,$_.ParentProcessId,$_.CommandLine }").splitlines() if l.count("|") >= 2]
    for i in IDS:
        if i in done or str(i) not in devs or "launch" not in devs[str(i)]: continue
        seen.setdefault(i, time.time())
        if time.time() - seen[i] < 45: continue
        b = [int(p) for p, pp, cl in rows if f"ae-vm-{i} " in cl + " " and " run-mp " in f" {cl} "]
        for p, pp, cl in rows:
            if b and (int(p) == b[0] or int(pp) == b[0]):
                h = k32.OpenProcess(0x0100 | 0x0400, False, int(p))
                k32.SetProcessWorkingSetSizeEx(h, 1 << 20, (200 if "run-main" in cl else 16) << 20, 0x4 | 0x2); k32.CloseHandle(h)
        done.add(i); print(time.strftime("%T"), "capped", i, flush=True)
    time.sleep(10)
