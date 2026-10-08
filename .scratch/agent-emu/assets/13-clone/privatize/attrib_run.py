# Attribute a CoW clone's privatized pages. Restores one clone with AGENT_EMU_COW_DUMP (host per-page
# state every 30 s), drives it like the steady runs (tap every 20 s, relaunch at 300 s), and pulls guest
# state at fixed times: /proc/kpageflags (gzip+base64), meminfo, vmstat, zram mm_stat, dumpsys meminfo.
# usage: python attrib_run.py <tag> <snapdir> [--id 13] [--extra-setup "<guest cmd run right after restore>"]
import argparse, base64, gzip, json, os, re, socket, subprocess, sys, time, uuid

X = "C:/dev/agent-emu-work/clone-exp"
ap = argparse.ArgumentParser(); ap.add_argument("tag"); ap.add_argument("snap"); ap.add_argument("--id", type=int, default=13)
ap.add_argument("--extra-setup", default=""); ap.add_argument("--times", default="5,60,290,600")
a = ap.parse_args(); i = a.id; port = 7100 + i
OUT = f"{X}/out/{a.tag}"; os.makedirs(OUT, exist_ok=True)


def gsh(cmd, timeout=120):
    marker = "__AE_" + uuid.uuid4().hex[:8] + "__"
    s = socket.create_connection(("127.0.0.1", port), timeout=10); s.settimeout(1)
    s.sendall(f"su 0 sh -c '{cmd}'; echo {marker}\n".encode()); out, end = b"", time.time() + timeout
    while time.time() < end:
        try: d = s.recv(1 << 20)
        except socket.timeout: continue
        if not d: break
        out += d
        if ("\n" + marker).encode() in out.replace(b"\r", b""): break
    s.close()
    t = out.decode(errors="replace").replace("\r", "")
    return "\n".join(l for l in t.split("\n") if marker not in l and not l.startswith(("su 0", "<", "console:"))).strip()


def pull(t):
    pre = f"{OUT}/g{t:04d}"
    r = gsh("cat /proc/kpageflags | gzip -c | base64 -w 0; echo; echo KPFEND", 600)
    m = re.search(r"([A-Za-z0-9+/=]{100,})\nKPFEND", r)
    if m: open(f"{pre}-kpageflags.bin", "wb").write(gzip.decompress(base64.b64decode(m.group(1))))
    open(f"{pre}-meminfo.txt", "w").write(gsh("cat /proc/meminfo; echo ===; cat /proc/vmstat; echo ===; cat /sys/block/zram0/mm_stat; echo ===; cat /proc/loadavg"))
    open(f"{pre}-dumpsys.txt", "w").write(gsh("dumpsys meminfo -s", 180))


env = dict(os.environ, AGENT_EMU_COW_DUMP=f"{OUT}/host".replace("/", "\\"))
r = subprocess.run([sys.executable, f"{X}/clone_exp.py", "restore", str(i), a.snap, "--cow"], capture_output=True, text=True, env=env)
print(r.stdout[-300:], flush=True)
t0 = time.time()
if a.extra_setup: print("setup:", gsh(a.extra_setup)[-300:], flush=True)
times = [int(x) for x in a.times.split(",")]; nt = 0; next_tap = t0; relaunched = False
while nt < len(times):
    el = time.time() - t0
    if el >= times[nt]:
        pull(times[nt]); print("pulled", times[nt], "at", round(time.time() - t0), flush=True); nt += 1; continue
    if not relaunched and el >= 300:
        print("relaunch", gsh("am force-stop com.boltbetz.staging; am start -W -n com.boltbetz.staging/com.boltbetz.MainActivity | grep TotalTime"), flush=True)
        relaunched = True
    if time.time() >= next_tap:
        gsh("input tap 360 780", 30); next_tap += 20
    time.sleep(1)
print(json.dumps({"tag": a.tag, "id": i, "alive": gsh("pidof com.boltbetz.staging")}))
