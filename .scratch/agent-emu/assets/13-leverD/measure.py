# Lever D: on running Device <id>: install proof APK, launch, settle, screenshot, guest + host memory, then cap 250/16 and re-measure.
# usage: measure.py <id> <dir> <outdir> [--no-install]
import base64, ctypes, ctypes.wintypes as wt, json, os, re, socket, subprocess, sys, time, uuid
W = "C:/dev/agent-emu-work"; i, D, O = int(sys.argv[1]), sys.argv[2], sys.argv[3]; os.makedirs(O, exist_ok=True); port = 7100 + i
k32 = ctypes.WinDLL("kernel32", use_last_error=True); k32.OpenProcess.restype = wt.HANDLE
k32.SetProcessWorkingSetSizeEx.argtypes = [wt.HANDLE, ctypes.c_size_t, ctypes.c_size_t, wt.DWORD]
def ps(c): return subprocess.run(["powershell", "-NoProfile", "-Command", c], capture_output=True, text=True).stdout.strip()
def gsh(cmd, timeout=120):
    m = "__AE_" + uuid.uuid4().hex[:8] + "__"; s = socket.create_connection(("127.0.0.1", port)); s.settimeout(1)
    s.sendall(f"su 0 sh -c '{cmd}'; echo {m}\n".encode()); out, end = b"", time.time() + timeout
    while time.time() < end:
        try: d = s.recv(65536)
        except socket.timeout: continue
        if not d: break
        out += d
        if ("\n" + m).encode() in out.replace(b"\r", b""): break
    s.close(); t = out.decode(errors="replace").replace("\r", "")
    return "\n".join(l for l in t.split("\n") if m not in l and not l.startswith(("su 0", "<", "console:"))).strip()
def procs():
    rows = [l.split("|", 2) for l in ps("Get-CimInstance Win32_Process -Filter \"Name='crosvm.exe'\" | % { '{0}|{1}|{2}' -f $_.ProcessId,$_.ParentProcessId,$_.CommandLine }").splitlines() if l.count("|") >= 2]
    b = [int(p) for p, pp, cl in rows if f"ae-vm-{i} " in cl + " " and " run-mp " in f" {cl} "]
    return [(int(p), cl) for p, pp, cl in rows if b and (int(p) == b[0] or int(pp) == b[0])]
def ws(pr): return {("main" if "run-main" in cl else str(p)): int(ps(f"(Get-Process -Id {p} -EA SilentlyContinue).WorkingSet64") or 0) >> 20 for p, cl in pr}
def host(): return ps("$a=(Get-Counter '\Memory\Available MBytes').CounterSamples[0].CookedValue; '{0}|{1}' -f [int]$a,((Get-Process 'Memory Compression').WorkingSet64 -shr 20)")
def shot(name):
    t = gsh("screencap -p /data/local/tmp/s.png; echo B64START; base64 -w 0 /data/local/tmp/s.png; echo; echo B64END", 300)
    m = re.search(r"B64START\n(.*?)\nB64END", t, re.S)
    if m: open(f"{O}/{name}.png", "wb").write(base64.b64decode(re.sub(r"\s", "", m.group(1)))); return f"{O}/{name}.png"
def guest(): return {"meminfo": gsh("grep -E \"MemTotal|MemAvailable|MemFree|^Cached|SwapTotal|SwapFree\" /proc/meminfo"),
                     "used": gsh("dumpsys meminfo | grep -E \"Used RAM|Free RAM|Total RAM\""), "app_pid": gsh("pidof com.boltbetz.staging"),
                     "app_pss": gsh("dumpsys meminfo com.boltbetz.staging | grep -E \"TOTAL PSS|TOTAL:\" | head -2")}
r = {"id": i}
if "--no-install" not in sys.argv:
    n = int(open(f"{W}/run/apk.size").read()); t = time.time()
    r["install"] = gsh(f"head -c {n} /dev/block/vdb > /data/local/tmp/p.apk && chmod 644 /data/local/tmp/p.apk && pm install -r /data/local/tmp/p.apk; rm -f /data/local/tmp/p.apk", 600)
    r["install_s"] = round(time.time() - t, 1)
gsh("settings put global hide_error_dialogs 1; cmd connectivity airplane-mode enable; settings put global airplane_mode_on 1; settings put global window_animation_scale 0; settings put global transition_animation_scale 0; "
    "settings put global animator_duration_scale 0; svc power stayon true; input keyevent KEYCODE_WAKEUP; wm dismiss-keyguard; settings put secure immersive_mode_confirmations confirmed")
r["launch"] = gsh("am start -W -n com.boltbetz.staging/com.boltbetz.MainActivity | grep -E \"Status|TotalTime\"", 180)
time.sleep(int(os.environ.get("SETTLE", "40")))
r["shot_settled"] = shot("settled"); r["guest_settled"] = guest(); pr = procs()
r["host_settled"] = host(); r["ws_settled_mb"] = ws(pr); r["ws_settled_total_mb"] = sum(r["ws_settled_mb"].values())
for p, cl in pr:
    h = k32.OpenProcess(0x0100 | 0x0400, False, p); k32.SetProcessWorkingSetSizeEx(h, 1 << 20, (int(os.environ.get("CAP_MAIN", "250")) if "run-main" in cl else int(os.environ.get("CAP_HELPER", "16"))) << 20, 0x4 | 0x2); k32.CloseHandle(h)
r["host_after_cap_0s"] = host(); r["hold"] = []; end = time.time() + int(os.environ.get("HOLD", "90"))
while time.time() < end:  # keep it busy while capped: a tap every ~10 s, sample host every 30 s
    t = time.time(); gsh("input tap 360 780"); r.setdefault("tap_ms", []).append(round((time.time() - t) * 1000))
    if len(r["tap_ms"]) % 3 == 0: r["hold"].append({"t": round(time.time() - end + int(os.environ.get("HOLD", "90"))), "host": host(), "ws_mb": sum(ws(pr).values())})
    time.sleep(10)
r["host_after_cap_90s"] = host(); r["ws_capped_mb"] = ws(pr); r["ws_capped_total_mb"] = sum(r["ws_capped_mb"].values())
r["guest_capped"] = guest(); r["shot_capped"] = shot("capped")
json.dump(r, open(f"{O}/result.json", "w"), indent=1); print(json.dumps(r, indent=1))
