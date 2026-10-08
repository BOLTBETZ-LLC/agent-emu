# Windows PSS for crosvm Devices: walk each process's working set with QueryWorkingSetEx.
# Private pages count fully; shared pages count 1/ShareCount (ShareCount saturates at 7, so the
# shared share is an upper bound). Groups processes by Device via the broker (run-mp) process tree.
# usage: python pss.py            -> JSON {device_pid: {...}, "fleet": {...}}
import ctypes, ctypes.wintypes as wt, json, subprocess, sys

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)
PROCESS_QUERY_INFORMATION, PROCESS_VM_READ = 0x400, 0x10
MEM_COMMIT = 0x1000
PAGE = 4096

class MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p), ("AllocationProtect", wt.DWORD),
                ("PartitionId", wt.WORD), ("RegionSize", ctypes.c_size_t), ("State", wt.DWORD), ("Protect", wt.DWORD),
                ("Type", wt.DWORD)]

class WSEX(ctypes.Structure):
    _fields_ = [("VirtualAddress", ctypes.c_void_p), ("Flags", ctypes.c_size_t)]

k32.OpenProcess.restype = wt.HANDLE
k32.VirtualQueryEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]
k32.VirtualQueryEx.restype = ctypes.c_size_t
psapi.QueryWorkingSetEx.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD]

def pss_of(pid):
    h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        return None
    priv = shared_pss = shared_rss = 0
    addr, mbi = 0, MBI()
    while k32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
        base, size = mbi.BaseAddress or 0, mbi.RegionSize
        if mbi.State == MEM_COMMIT:
            n = size // PAGE
            for off in range(0, n, 65536):
                cnt = min(65536, n - off)
                arr = (WSEX * cnt)()
                for i in range(cnt):
                    arr[i].VirtualAddress = base + (off + i) * PAGE
                if psapi.QueryWorkingSetEx(h, arr, ctypes.sizeof(arr)):
                    for e in arr:
                        f = e.Flags
                        if not f & 1:            # Valid
                            continue
                        share_count = (f >> 1) & 0x7
                        shared = (f >> 15) & 1
                        if shared and share_count > 1:
                            shared_pss += PAGE / share_count; shared_rss += PAGE
                        else:
                            priv += PAGE
        addr = base + size
        if addr >= 0x7FFFFFFF0000:
            break
    k32.CloseHandle(h)
    return {"private_mb": round(priv / 2**20), "shared_pss_mb": round(shared_pss / 2**20), "shared_rss_mb": round(shared_rss / 2**20),
            "pss_mb": round((priv + shared_pss) / 2**20)}

def crosvm_tree():
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
        "Get-CimInstance Win32_Process -Filter \"Name='crosvm.exe'\" | ForEach-Object { '{0}|{1}|{2}' -f $_.ProcessId,$_.ParentProcessId,($_.CommandLine -replace '\\|','/') }"],
        capture_output=True, text=True).stdout
    procs = {}
    for l in out.splitlines():
        pid, ppid, cl = l.split("|", 2)
        procs[int(pid)] = (int(ppid), cl)
    brokers = [p for p, (pp, cl) in procs.items() if " run-mp " in f" {cl} "]
    groups = {b: [b] + [p for p, (pp, _) in procs.items() if pp == b] for b in brokers}
    return groups, procs

if __name__ == "__main__":
    groups, procs = crosvm_tree()
    res, fleet = {}, {"private_mb": 0, "pss_mb": 0}
    for b, pids in groups.items():
        dev = {"processes": {}}
        for p in pids:
            r = pss_of(p)
            if r:
                role = "broker" if p == b else procs[p][1].split('"')[-1].strip()[:40] or "child"
                dev["processes"][p] = {"role": role, **r}
        dev["private_mb"] = sum(v["private_mb"] for v in dev["processes"].values())
        dev["pss_mb"] = sum(v["pss_mb"] for v in dev["processes"].values())
        fleet["private_mb"] += dev["private_mb"]; fleet["pss_mb"] += dev["pss_mb"]
        res[b] = dev
    res["fleet"] = fleet
    print(json.dumps(res, indent=1))
