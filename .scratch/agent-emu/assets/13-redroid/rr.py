#!/usr/bin/env python3
"""redroid container lever driver (runs inside WSL Ubuntu as root).

  rr.py up K [extra androidboot args...]   start container rrK, wait boot, install proof APK, launch app
  rr.py sample                             one JSON line of per-container + host-kernel memory
  rr.py hold MIN OUT.jsonl K...            tap every 20 s, relaunch at MIN/2, sample every 30 s
  rr.py shot K OUT.png                     screencap
  rr.py down K
"""
import json, os, subprocess, sys, time

IMG = "redroid/redroid:16.0.0_64only-latest"
APK = "/mnt/c/dev/agent-emu-work/results/adb/proof.apk"
PKG = "com.boltbetz.staging"


def sh(cmd, check=False, timeout=120):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode:
        raise SystemExit(f"FAIL {cmd}: {r.stdout}{r.stderr}")
    return r.stdout.strip()


_ip = {}


def serial(k):
    # no -p: this kernel lacks the DNAT module, so talk to the container IP directly
    if k not in _ip:
        _ip[k] = sh(f"docker inspect -f '{{{{range .NetworkSettings.Networks}}}}{{{{.IPAddress}}}}{{{{end}}}}' rr{k}")
    return f"{_ip[k]}:5555"


def adb(k, args, **kw):
    return sh(f"adb -s {serial(k)} {args}", **kw)


def launch(k):
    act = adb(k, f"shell cmd package resolve-activity --brief {PKG}").splitlines()[-1]
    return adb(k, f"shell am start -W -n {act}")


def up(k, extra):
    t0 = time.time()
    sh(f"docker rm -f rr{k}")
    args = " ".join(["androidboot.redroid_gpu_mode=guest"] + extra)
    sh(f"docker run -d --privileged --name rr{k} {IMG} {args}", check=True)
    while time.time() - t0 < 300:
        sh(f"adb connect {serial(k)}")
        if adb(k, "shell getprop sys.boot_completed", timeout=10) == "1":
            break
        time.sleep(2)
    else:
        raise SystemExit(f"rr{k} no boot in 300 s")
    boot = time.time() - t0
    inst = adb(k, f"install -r -g {APK}", timeout=300)
    out = launch(k)
    time.sleep(45)
    pid = adb(k, f"shell pidof {PKG}")
    print(json.dumps({"k": k, "boot_s": round(boot, 1), "install": inst.splitlines()[-1:], "launch": out.splitlines()[-3:], "pid": pid, "total_s": round(time.time() - t0, 1)}))


def cg_dir(k):
    pid = sh(f"docker inspect -f '{{{{.State.Pid}}}}' rr{k}")
    if not pid or pid == "0":
        return None, []
    rel = open(f"/proc/{pid}/cgroup").read().strip().split("::")[-1]
    d = "/sys/fs/cgroup" + rel
    procs = []  # Android init makes child cgroups inside the container's, so walk the tree
    for root, _, files in os.walk(d):
        if "cgroup.procs" in files:
            procs += open(root + "/cgroup.procs").read().split()
    return d, procs


def rollup(pids):
    pss = uss = rss = 0
    for p in pids:
        try:
            for line in open(f"/proc/{p}/smaps_rollup"):
                f = line.split()
                if f[0] == "Pss:": pss += int(f[1])
                elif f[0] == "Rss:": rss += int(f[1])
                elif f[0] in ("Private_Clean:", "Private_Dirty:"): uss += int(f[1])
        except OSError:
            pass
    return pss // 1024, uss // 1024, rss // 1024


def ksm():
    d = "/sys/kernel/mm/ksm/"
    g = lambda n: int(open(d + n).read()) if os.path.exists(d + n) else -1
    return {"run": g("run"), "pages_shared": g("pages_shared"), "pages_sharing": g("pages_sharing"),
            "saved_mb": g("pages_sharing") * 4 // 1024, "general_profit_mb": g("general_profit") // 2**20 if g("general_profit") >= 0 else -1}


def meminfo():
    m = {}
    for line in open("/proc/meminfo"):
        k, v = line.split(":")
        m[k] = int(v.split()[0]) // 1024
    return {k: m[k] for k in ("MemTotal", "MemFree", "MemAvailable", "Cached", "AnonPages", "Shmem", "SwapTotal", "SwapFree")}


def names():
    return [n[2:] for n in sh("docker ps --format '{{.Names}}'").split() if n.startswith("rr")]


def sample():
    out = {"t": time.strftime("%H:%M:%S"), "mem": meminfo(), "ksm": ksm(), "c": {}}
    for k in sorted(names(), key=int):
        d, procs = cg_dir(k)
        if not d:
            continue
        st = dict(l.split() for l in open(d + "/memory.stat"))
        pss, uss, rss = rollup(procs)
        out["c"][k] = {"cur": int(open(d + "/memory.current").read()) // 2**20,
                       "anon": int(st["anon"]) // 2**20, "file": int(st["file"]) // 2**20, "shmem": int(st["shmem"]) // 2**20,
                       "pss": pss, "uss": uss, "rss": rss, "nproc": len(procs),
                       "app": adb(k, f"shell pidof {PKG}", timeout=15)}
    return out


def hold(minutes, path, ks):
    t0 = time.time(); nt = ns = 0; relaunched = False; f = open(path, "a")
    while (el := time.time() - t0) < minutes * 60:
        if el >= nt:
            for k in ks:
                s = time.time(); adb(k, "shell input tap 360 800", timeout=30)
                f.write(json.dumps({"tap": k, "ms": int((time.time() - s) * 1000)}) + "\n")
            nt += 20
        if not relaunched and el >= minutes * 30:
            for k in ks:
                adb(k, f"shell am force-stop {PKG}")
                f.write(json.dumps({"relaunch": k, "out": launch(k).splitlines()[-3:]}) + "\n")
            relaunched = True
        if el >= ns:
            s = sample(); s["el"] = int(el); f.write(json.dumps(s) + "\n"); f.flush()
            print(s["t"], int(el), "ksm_saved", s["ksm"]["saved_mb"], {k: (v["cur"], v["pss"], v["uss"], bool(v["app"])) for k, v in s["c"].items()}, flush=True)
            ns += 30
        time.sleep(1)


if __name__ == "__main__":
    c = sys.argv[1]
    if c == "up": up(sys.argv[2], sys.argv[3:])
    elif c == "sample": print(json.dumps(sample()))
    elif c == "hold": hold(float(sys.argv[2]), sys.argv[3], sys.argv[4:])
    elif c == "shot":
        sh(f"adb -s {serial(sys.argv[2])} exec-out screencap -p > {sys.argv[3]}")
    elif c == "down": sh(f"adb disconnect {serial(sys.argv[2])}"); sh(f"docker rm -f rr{sys.argv[2]}")
