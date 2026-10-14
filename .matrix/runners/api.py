#!/usr/bin/env python
"""Matrix runner for the api-* rows of .matrix/features.tsv.

  python .matrix/runners/api.py                 # every api-* row
  python .matrix/runners/api.py api-02 api-06   # only these

Tier 1 (api-01..07, 17) probes the LIVE daemon (7401): read-only calls, plus sleep/wake/claim on d2 (Aaron's
phone, proven safe; always left ready and unclaimed). Never starts, stops or wipes a live phone.
Tier 2 (api-08..16, 18) runs a TEST daemon built from main on ports 7450+ with its own home (junctions to the
installed images/tools) and phone id d7, so nothing it does can touch the live phones. Boots only when the live
daemon shows available_mb >= 5000, else those rows are `blocked: RAM`.
api-19 is manual (blocked).

Results: .matrix/runs/<timestamp>-api.tsv (id, result, observed, evidence) and the status column of features.tsv.
Everything started here (test daemons, d7, junctions, window watcher) is stopped and removed at the end.
"""
import concurrent.futures as cf
import ctypes
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MATRIX = os.path.join(ROOT, ".matrix")
FEATURES = os.path.join(MATRIX, "features.tsv")
LIVE = "http://127.0.0.1:7401"
INSTALLED = os.path.join(os.environ.get("LOCALAPPDATA", ""), "agent-emu", "app")
BUILT_EXE = os.path.join(ROOT, "dist", "cargo-target", "release", "agent-emud.exe")
WINWATCH = os.path.join(ROOT, "_work", "2026-10-14-haiping", "winwatch.ps1")
WORK = os.path.join(ROOT, "_work", "2026-10-14-mx-api")
RAM_GATE_MB = 3000
DEV = "d7"
NOWIN = 0x08000000  # CREATE_NO_WINDOW

RESULTS = {}  # id -> (result, observed, evidence)
RLOCK = threading.Lock()


def rec(i, result, observed, evidence=""):
    with RLOCK:
        RESULTS[i] = (result, str(observed), str(evidence))
    print(f"{i}: {result}: {observed}", flush=True)


def api(base, body, timeout=30):
    req = urllib.request.Request(base + "/api", data=json.dumps(body).encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:  # connection refused, timeout, bad json
        return {"ok": False, "error": f"transport: {e!r}"}


def status(base=LIVE):
    return api(base, {"call": "status"}, 20)


def dev_of(st, d):
    for x in st.get("devices", []):
        if x.get("id") == d:
            return x
    return None


def wait_phase(base, d, want, timeout, poll=0.5):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        x = dev_of(status(base), d)
        last = x.get("phase") if x else None
        if last in want:
            return last, time.time() - t0
        time.sleep(poll)
    return last, time.time() - t0


def sh(base, cmd, timeout=40):
    r = api(base, {"call": "shell", "device": DEV, "cmd": cmd}, timeout)
    return r.get("out", "") if r.get("ok") else "ERR " + str(r.get("error"))


# ---------------------------------------------------------------- tier 1 (live)

STATUS_DEV_FIELDS = ["id", "ready", "phase", "phase_since_ms", "ui_reader", "streams", "image", "mem", "cpus", "net",
                     "screen", "uptime_s", "frames_via", "input_via", "claim", "health"]
STATUS_TOP = ["available_mb", "max_phones", "claims"]
SCREEN_FIELDS = ["name", "width", "height", "dpi"]


def c_api01():
    st = status()
    if not st.get("ok", True) or "devices" not in st:
        return rec("api-01", "fail", "status failed", st)
    miss_top = [k for k in STATUS_TOP if k not in st]
    miss = {}
    live = [x for x in st["devices"] if x.get("phase") not in ("stopped", None)]
    for x in live:
        m = [k for k in STATUS_DEV_FIELDS if k not in x]
        m += [f"screen.{k}" for k in SCREEN_FIELDS if isinstance(x.get("screen"), dict) and k not in x["screen"]]
        if m:
            miss[x["id"]] = m
    stopped = [x["id"] for x in st["devices"] if x not in live]
    ev = f"devices checked={[x['id'] for x in live]} stopped(not field-checked)={stopped} top={sorted(k for k in st if k != 'devices')}"
    if not live:
        return rec("api-01", "blocked", "no running phone to check device fields", ev)
    if miss_top or miss:
        return rec("api-01", "fail", f"missing top={miss_top} per-device={miss}", ev)
    rec("api-01", "pass", f"all {len(STATUS_DEV_FIELDS)} device fields + {STATUS_TOP} present on {len(live)} running phones", ev)


def c_api05():
    st = status()
    off = [x["id"] for x in st.get("devices", []) if x.get("phase") in ("stopped", None)]
    if not off:
        return rec("api-05", "blocked", "no listed phone is off right now (others are using d0/d1)", "")
    d = off[0]
    r = api(LIVE, {"call": "wake", "device": d})
    e = str(r.get("error", ""))
    if r.get("ok") is False and re.search(r"start", e):
        rec("api-05", "pass", f"wake on stopped {d}: refused, error names start", e)
    else:
        rec("api-05", "fail", f"wake on a stopped phone ({d}) did not say to use start", json.dumps(r)[:300])


def c_api06():
    r1 = api(LIVE, {"call": "foo"})
    r2 = api(LIVE, {"call": "foo", "device": "d7"})  # device that does not exist: must still be 'unknown call'
    e1, e2 = str(r1.get("error", "")), str(r2.get("error", ""))
    ok = all(r.get("ok") is False for r in (r1, r2)) and "unknown call" in e1.lower() and "unknown call" in e2.lower()
    rec("api-06", "pass" if ok else "fail", "unknown call named with and without a (missing) device" if ok else "wrong error",
        f"no-device: {e1} | with d7: {e2}")


def c_api07():
    r = api(LIVE, {"call": "ui_tree", "device": "d7"})
    e = str(r.get("error", ""))
    ok = r.get("ok") is False and "d7" in e
    rec("api-07", "pass" if ok else "fail", "error names d7" if ok else "error does not name the device", e or json.dumps(r)[:200])


def tree_ok(r):
    return r.get("ok") and "<node" in str(r.get("xml", ""))


def d2_chain():
    """api-02, 03, 04, 17: serial on d2 (never two lifecycle ops on the phone at once). Ends ready and unclaimed."""
    st = status()
    d2 = dev_of(st, "d2")
    if not d2 or d2.get("phase") != "ready":
        for i in ("api-02", "api-03", "api-04", "api-17"):
            rec(i, "blocked", f"d2 not ready (phase={d2.get('phase') if d2 else None})", "")
        return
    try:
        # api-02
        t = time.time(); rs = api(LIVE, {"call": "sleep", "device": "d2"}, 30); ts = time.time() - t
        t = time.time(); rw = api(LIVE, {"call": "wake", "device": "d2"}, 30); tw = time.time() - t
        tree = api(LIVE, {"call": "ui_tree", "device": "d2"})
        ev = f"sleep ok={rs.get('ok')} {ts:.2f}s ms={rs.get('ms')}; wake ok={rw.get('ok')} {tw:.2f}s woke_ms={rw.get('woke_ms')}; ui_tree ok={tree.get('ok')}"
        if rs.get("ok") and rw.get("ok") and ts < 2 and tw < 2 and tree_ok(tree):
            rec("api-02", "pass", f"sleep {ts:.2f}s, wake {tw:.2f}s, ui_tree works", ev)
        else:
            rec("api-02", "fail", f"sleep {ts:.2f}s wake {tw:.2f}s (limit 2 s) or ui_tree failed", ev + " " + str(rs.get("error", "")) + str(rw.get("error", "")) + str(tree.get("error", "")))
        # api-03: an input call (tap in the very top-left corner, no button there) on a sleeping phone
        rs = api(LIVE, {"call": "sleep", "device": "d2"}, 30)
        ph = dev_of(status(), "d2").get("phase")
        rt = api(LIVE, {"call": "tap", "device": "d2", "x": 2, "y": 2, "device_px": True, "screenshot": False}, 30)
        ph2 = dev_of(status(), "d2").get("phase")
        tree = api(LIVE, {"call": "ui_tree", "device": "d2"})
        ev = f"sleep ok={rs.get('ok')} phase={ph}; tap ok={rt.get('ok')} err={rt.get('error')}; phase after={ph2}; ui_tree ok={tree.get('ok')}"
        if ph == "sleeping" and rt.get("ok") and ph2 == "ready" and tree_ok(tree):
            rec("api-03", "pass", "tap on a sleeping phone woke it (phase ready) and succeeded", ev)
        else:
            rec("api-03", "fail", f"phase before={ph} after={ph2}, tap ok={rt.get('ok')}", ev)
        if ph2 == "sleeping" or ph == "sleeping" and ph2 != "ready":
            api(LIVE, {"call": "wake", "device": "d2"}, 30)
        # api-04
        if (dev_of(status(), "d2") or {}).get("claim") or status().get("claims", {}).get("d2"):
            rec("api-04", "blocked", "d2 already has a claim; not touching it", "")
        else:
            rc = api(LIVE, {"call": "claim", "device": "d2", "owner": "run.py-1", "note": "api matrix probe"})
            rs = api(LIVE, {"call": "sleep", "device": "d2"}, 30)
            ph = dev_of(status(), "d2").get("phase")
            ru = api(LIVE, {"call": "unclaim", "device": "d2", "owner": "run.py-1"})
            after = dev_of(status(), "d2")
            e = str(rs.get("error", ""))
            ev = f"claim ok={rc.get('ok')}; sleep ok={rs.get('ok')} err={e}; phase={ph}; unclaim ok={ru.get('ok')}; claim after={after.get('claim')}"
            if rc.get("ok") and rs.get("ok") is False and "run.py-1" in e and ph == "ready" and after.get("claim") is None:
                rec("api-04", "pass", "sleep refused naming run.py-1; phone stayed ready; claim cleared", ev)
            else:
                rec("api-04", "fail", "sleep not refused with the owner named, or claim left behind", ev)
        # api-17 (live d2): health while ui_tree is hammered from three threads
        stop = threading.Event()

        def hammer():
            while not stop.is_set():
                api(LIVE, {"call": "ui_tree", "device": "d2"}, 20)
        ths = [threading.Thread(target=hammer, daemon=True) for _ in range(3)]
        [th.start() for th in ths]
        time.sleep(0.5)
        t = time.time(); h = api(LIVE, {"call": "health", "device": "d2"}, 70); dt = time.time() - t
        stop.set(); [th.join(15) for th in ths]
        hh = h.get("health", {}) if isinstance(h.get("health"), dict) else {}
        ev = f"health ok={h.get('ok')} {dt:.1f}s level={hh.get('level')} busy={hh.get('busy')} summary={hh.get('summary')} err={h.get('error')}"
        if h.get("ok") and "level" in hh and isinstance(hh.get("busy"), bool):
            rec("api-17", "pass", f"health ok under ui_tree load, busy field={hh.get('busy')} (busy=true path: see d7 note if run)", ev)
        else:
            rec("api-17", "fail", "health failed or has no busy field under load", ev)
    finally:
        x = dev_of(status(), "d2")
        if x and x.get("phase") == "sleeping":
            api(LIVE, {"call": "wake", "device": "d2"}, 30)
        api(LIVE, {"call": "unclaim", "device": "d2", "owner": "run.py-1"})


# ---------------------------------------------------------------- tier 2 (test daemon)

class Daemon:
    def __init__(self, name, home, port, env_extra, affinity_cpus=None):
        self.name, self.home, self.port = name, home, port
        self.addr = f"127.0.0.1:{port}"
        self.ui = port + 1
        self.base = f"http://127.0.0.1:{self.ui}"
        self.env_extra, self.affinity_cpus = env_extra, affinity_cpus
        self.proc = None
        self.log = os.path.join(WORK, f"daemon-{name}.log")

    def start(self):
        env = dict(os.environ)
        for k in [k for k in env if k.startswith("AE_")]:
            del env[k]
        env.update({"AE_HOME": self.home, "AE_UI_ADDR": f"127.0.0.1:{self.ui}", "AE_BUGS_FILE": os.path.join(WORK, "bugs.jsonl")})
        env.update(self.env_extra)
        out = open(self.log, "ab")
        k32 = ctypes.windll.kernel32
        me = k32.GetCurrentProcess()
        old = ctypes.c_size_t(); sysm = ctypes.c_size_t()
        k32.GetProcessAffinityMask(me, ctypes.byref(old), ctypes.byref(sysm))
        if self.affinity_cpus:
            k32.SetProcessAffinityMask(me, ctypes.c_size_t((1 << self.affinity_cpus) - 1))
        try:  # a child inherits the affinity mask, so rust's available_parallelism sees only these cpus
            self.proc = subprocess.Popen([os.path.join(self.home, "agent-emud.exe"), "serve", "--addr", self.addr], cwd=WORK, env=env,
                                         stdout=out, stderr=out, stdin=subprocess.DEVNULL, creationflags=NOWIN | 0x200)
        finally:
            if self.affinity_cpus:
                k32.SetProcessAffinityMask(me, old)
        for _ in range(60):
            try:
                with urllib.request.urlopen(self.base + "/health", timeout=2) as r:
                    if r.read().strip() == b"ok":
                        return True
            except Exception:
                time.sleep(0.5)
        return False

    def call(self, body, timeout=30):
        return api(self.base, body, timeout)

    def stop(self):
        """Stop d7 if up, quit, and kill only the process this object started."""
        if self.proc and self.proc.poll() is None:
            try:
                x = dev_of(self.call({"call": "status"}, 10), DEV)
                if x and x.get("phase") not in ("stopped", None):
                    self.call({"call": "stop", "device": DEV}, 120)
                self.call({"call": "quit"}, 10)
            except Exception:
                pass
            try:
                self.proc.wait(10)
            except Exception:
                self.proc.kill()


def junction(link, target):
    r = subprocess.run(["cmd", "/c", "mklink", "/J", link, target], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"mklink {link}: {r.stdout}{r.stderr}")


def unlink_junction(p):
    if os.path.isdir(p):
        subprocess.run(["cmd", "/c", "rmdir", p], capture_output=True)  # removes the link only, never the target


def build_home():
    home = os.path.join(WORK, "home")
    os.makedirs(os.path.join(home, "images", "fleet"), exist_ok=True)
    shutil.copy2(BUILT_EXE, os.path.join(home, "agent-emud.exe"))
    for n in os.listdir(INSTALLED):
        s, d = os.path.join(INSTALLED, n), os.path.join(home, n)
        if n in ("images", "agent-emud.exe", "logs", "keys"):
            continue
        if os.path.isdir(s):
            junction(d, s)
        else:
            shutil.copy2(s, d)
    for n in ("run-slim5", "browser", "gpu", "stage1", "apk"):
        s = os.path.join(INSTALLED, "images", n)
        if os.path.isdir(s):
            junction(os.path.join(home, "images", n), s)
    shutil.copy2(os.path.join(INSTALLED, "images", "default-app.json"), os.path.join(home, "images", "default-app.json"))
    return home


def teardown_home(home):
    if not os.path.isdir(home):
        return
    for root in (home, os.path.join(home, "images")):
        for n in os.listdir(root):
            p = os.path.join(root, n)
            if os.path.isdir(p) and n != "fleet" and (os.path.islink(p) or n in ("run-slim5", "browser", "gpu", "stage1", "apk") or root == home):
                unlink_junction(p)
    left = [n for n in os.listdir(home) if os.path.isdir(os.path.join(home, n)) and n != "images"]
    if left:
        print("WARNING junctions left, not deleting home:", left)
        return
    imgs = os.path.join(home, "images")
    still = [n for n in os.listdir(imgs) if n != "fleet" and os.path.isdir(os.path.join(imgs, n))]
    if still:
        print("WARNING junctions left in images:", still)
        return
    shutil.rmtree(home, ignore_errors=True)


def crosvm_for_d7():
    """Pids of crosvm.exe whose command line carries pipe ae-vm-7 (verified by name AND command line)."""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='crosvm.exe'\" | ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    out = []
    for ln in r.stdout.splitlines():
        if "\t" in ln:
            pid, cl = ln.split("\t", 1)
            if re.search(r"ae-vm-7(?!\d)", cl):
                out.append((int(pid), cl))
    return out


def tier2(ids, wanted):
    """wanted = set of ids to run. Returns after cleaning up."""
    t2 = {"api-08", "api-09", "api-10", "api-11", "api-12", "api-13", "api-14", "api-15", "api-16", "api-18"}
    todo = t2 & wanted
    if "api-06" in wanted:
        todo = todo | {"api-08"}  # api-06 is re-checked on the built main through the api-08 test daemon
    if not todo:
        return
    if not os.path.exists(BUILT_EXE):
        for i in sorted(todo):
            rec(i, "blocked", f"no built daemon at {BUILT_EXE}", "")
        return
    os.makedirs(WORK, exist_ok=True)
    daemons, watcher, home = [], None, None
    try:
        home = build_home()
        st = status()
        live_running = [x["id"] for x in st.get("devices", []) if x.get("phase") not in ("stopped", None)]
        # ---- api-08: cap == number of phones already running on the PC, so the next start is past the cap
        if "api-08" in todo:
            cap = max(1, len(live_running))
            A = Daemon("cap", home, 7450, {"AE_MAX_PHONES": str(cap), "AE_MIN_AVAIL_MB": "1"}); daemons.append(A)
            if not A.start():
                rec("api-08", "fail", "test daemon did not come up", open(A.log, errors="replace").read()[-300:])
            else:
                r = A.call({"call": "start", "device": DEV, "image": "slim5", "screen": "iphone17promax-half"}, 60)
                e = str(r.get("error", ""))
                s2 = dev_of(A.call({"call": "status"}), DEV)
                ok = r.get("ok") is False and f"max {cap}" in e and (not s2 or s2.get("phase") in ("stopped", None))
                rec("api-08", "pass" if ok else "fail", f"cap={cap} (live phones running {live_running}); start refused: {e}" if ok else f"not refused with cap named (cap={cap})",
                    f"{e} | reply={json.dumps(r)[:200]} | d7 phase={s2.get('phase') if s2 else None}")
                if "api-06" in todo or "api-06" in wanted:
                    r1, r2 = A.call({"call": "foo"}), A.call({"call": "foo", "device": "d7"})
                    e1, e2 = str(r1.get("error", "")), str(r2.get("error", ""))
                    ok6 = "unknown call" in e1.lower() and "unknown call" in e2.lower()
                    rec("api-06-main", "pass" if ok6 else "fail", "unknown call named" if ok6 else "wrong error", f"no-device: {e1} | with d7: {e2}")
                A.stop()
        # ---- api-09
        if "api-09" in todo:
            B = Daemon("ram", home, 7452, {"AE_MIN_AVAIL_MB": "999999", "AE_MAX_PHONES": "99"}); daemons.append(B)
            if not B.start():
                rec("api-09", "fail", "test daemon did not come up", open(B.log, errors="replace").read()[-300:])
            else:
                r = B.call({"call": "start", "device": DEV, "image": "slim5", "screen": "iphone17promax-half"}, 60)
                e = str(r.get("error", ""))
                ok = r.get("ok") is False and re.search(r"Available \d+ MB", e) and "999999" in e
                rec("api-09", "pass" if ok else "fail", f"refused: {e}" if ok else "start not refused with free and needed MB", f"{e} | {json.dumps(r)[:200]}")
                B.stop()
        boot_ids = todo - {"api-08", "api-09"}
        # api-12 needs no boot (the disk check fails before booting) but needs a disk made by a boot: with the others
        if not boot_ids:
            return
        # ---- RAM gate
        avail = status().get("available_mb", 0)
        t0 = time.time()
        while avail < RAM_GATE_MB and time.time() - t0 < 180:
            time.sleep(15); avail = status().get("available_mb", 0)
        if avail < RAM_GATE_MB:
            for i in sorted(boot_ids):
                rec(i, "blocked", f"blocked: RAM (live available_mb={avail} < {RAM_GATE_MB} after 3 min)", "")
            return
        C = Daemon("main", home, 7454, {"AE_MAX_PHONES": "99", "AE_ALLOW_OTHER_CROSVM": "1", "AE_MIN_AVAIL_MB": "2500"}); daemons.append(C)
        if not C.start():
            for i in sorted(boot_ids):
                rec(i, "fail", "test daemon did not come up", open(C.log, errors="replace").read()[-300:])
            return
        if "api-16" in boot_ids:
            wlog = os.path.join(WORK, "winwatch.log")
            watcher = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", WINWATCH, "-Seconds", "1500", "-Out", wlog],
                                       creationflags=NOWIN, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(3)
        START = {"call": "start", "device": DEV, "image": "slim5", "screen": "iphone17promax-half"}
        # ---- boot 1: no disk -> fresh provision
        r = C.call(START, 420)
        if not r.get("ok"):
            for i in sorted(boot_ids):
                rec(i, "fail", f"first start failed: {r.get('error')}", json.dumps(r)[:300])
            return
        ph, _ = wait_phase(C.base, DEV, ("ready",), 240)
        if ph != "ready":
            for i in sorted(boot_ids):
                rec(i, "fail", f"d7 never reached ready (phase={ph})", "")
            return
        x = dev_of(C.call({"call": "status"}), DEV)
        # api-18
        if "api-18" in boot_ids:
            nproc = int(os.environ.get("NUMBER_OF_PROCESSORS", "0"))
            want = "2" if nproc <= 8 else "4"
            cv = crosvm_for_d7()
            cvcpus = [re.findall(r"--cpus\s+(?:num-cores=)?(\d+)", cl) for _, cl in cv]
            ev = f"NUMBER_OF_PROCESSORS={nproc}; status cpus={x.get('cpus')}; crosvm cmdline --cpus={cvcpus}; live d2 cpus={(dev_of(status(), 'd2') or {}).get('cpus')}"
            if str(x.get("cpus")) == want:
                rec("api-18", "pass", f"{nproc} threads -> default cpus {x.get('cpus')} (want {want}) [>8-thread branch only; <=8 branch checked on the affinity-limited daemon]", ev)
            else:
                rec("api-18", "fail", f"{nproc} threads -> default cpus {x.get('cpus')}, want {want}", ev)
        # app present + marker
        pk = "com.boltbetz.staging"
        if pk not in sh(C.base, f"pm list packages {pk}"):
            C.call({"call": "install_bundled", "device": DEV}, 240)
        pk1 = pk in sh(C.base, f"pm list packages {pk}")
        sh(C.base, "echo mx > /data/local/tmp/mx-marker && sync")
        # api-17 under load on d7
        if "api-17" not in RESULTS or True:
            sh(C.base, "for i in 1 2 3 4 5 6; do timeout 25 sh -c 'while :; do :; done' >/dev/null 2>&1 & done; echo started", 20)
            time.sleep(2)
            t = time.time(); h = C.call({"call": "health", "device": DEV}, 80); dt = time.time() - t
            hh = h.get("health", {}) if isinstance(h.get("health"), dict) else {}
            rec("api-17-d7", "pass" if (h.get("ok") and isinstance(hh.get("busy"), bool)) else "fail",
                f"d7 under 6 spinning guest procs: health ok={h.get('ok')} {dt:.1f}s level={hh.get('level')} busy={hh.get('busy')}", str(hh.get("summary")) + str(h.get("error")))
            time.sleep(25)
        r = C.call({"call": "stop", "device": DEV}, 180)
        ph, _ = wait_phase(C.base, DEV, ("stopped",), 60)
        # ---- boot 2: default keep
        r = C.call(START, 420)
        ph, _ = wait_phase(C.base, DEV, ("ready",), 240)
        if "api-10" in boot_ids:
            mk = sh(C.base, "cat /data/local/tmp/mx-marker").strip()
            pk2 = pk in sh(C.base, f"pm list packages {pk}")
            ev = f"start ok={r.get('ok')} err={r.get('error')} phase={ph}; marker={mk!r}; package before stop={pk1}, after={pk2}"
            if r.get("ok") and ph == "ready" and mk == "mx" and pk2:
                rec("api-10", "pass", "start with no flags kept the disk: marker file and app present", ev)
            else:
                rec("api-10", "fail", "disk or app not kept by a default start", ev)
        # sleep / wake / ui_tree on d7 (part of the api-16 cycle)
        C.call({"call": "sleep", "device": DEV}, 30); time.sleep(2)
        C.call({"call": "wake", "device": DEV}, 30); C.call({"call": "ui_tree", "device": DEV}, 20)
        r = C.call({"call": "stop", "device": DEV}, 180)
        wait_phase(C.base, DEV, ("stopped",), 60)
        if "api-16" in boot_ids:
            time.sleep(3)
            if watcher:
                watcher.terminate(); watcher = None
            txt = open(os.path.join(WORK, "winwatch.log"), errors="replace").read() if os.path.exists(os.path.join(WORK, "winwatch.log")) else ""
            ents = re.split(r"\n(?=\d\d:\d\d:\d\d\.\d{3} class=)", txt)
            hits = [e for e in ents if "class=" in e and re.search(r"agent-emud|crosvm", e, re.I)]
            allwin = [e for e in ents if "class=" in e]
            ev = f"window log {len(allwin)} new console windows total, {len(hits)} with agent-emud/crosvm in the chain" + ("".join("\n" + h[:400] for h in hits[:3]))
            rec("api-16", "pass" if txt and not hits else ("blocked" if not txt else "fail"),
                f"start/sleep/wake/stop cycle: {len(hits)} new console windows from the agent-emu chain" if txt else "no watcher log", ev)
        # ---- api-12: a disk made for another image (no boot needed: the disk check fails first)
        if "api-12" in boot_ids:
            s_ = os.path.join(home, "images", "run-slim4")
            junction(s_, os.path.join(home, "images", "run-slim5"))
            try:
                r = C.call({"call": "start", "device": DEV, "image": "slim4", "screen": "iphone17promax-half", "keep_data": True}, 60)
            finally:
                unlink_junction(s_)
            e = str(r.get("error", ""))
            ok = r.get("ok") is False and "disk_mismatch:" in e
            rec("api-12", "pass" if ok else "fail", "refused with disk_mismatch:" if ok else "other image on the same id did not fail with disk_mismatch:", f"{e[:300]} | {json.dumps(r)[:200]}")
        # ---- boot 3: fresh wipes
        r = C.call(dict(START, fresh=True), 420)
        ph, _ = wait_phase(C.base, DEV, ("ready",), 240)
        if "api-11" in boot_ids:
            mk = sh(C.base, "cat /data/local/tmp/mx-marker 2>&1").strip()
            gone = "No such file" in mk or "ERR" in mk or mk == ""
            ev = f"start fresh ok={r.get('ok')} err={r.get('error')} phase={ph}; marker read={mk!r}; package present={pk in sh(C.base, f'pm list packages {pk}')} (boot re-provisions the default app)"
            rec("api-11", "pass" if (r.get("ok") and ph == "ready" and gone) else "fail",
                "fresh:true wiped the disk (marker gone)" if gone else "marker file survived fresh:true", ev)
        # ---- api-15: Appium stand-in. Daemon restarts with AE_APPIUM_PROC=fakeappium via the adopt path (phones keep running)
        if "api-15" in boot_ids or "api-13" in boot_ids or "api-14" in boot_ids:
            C.call({"call": "quit", "keep_devices": True}, 30)
            try:
                C.proc.wait(20)
            except Exception:
                pass
            C.env_extra = dict(C.env_extra, AE_APPIUM_PROC="fakeappium")
            up = C.start()
            t0 = time.time()
            ph, adopt_s = wait_phase(C.base, DEV, ("ready",), 60)
            tree = C.call({"call": "ui_tree", "device": DEV}, 20)
            if "api-13" in boot_ids:
                x = dev_of(C.call({"call": "status"}), DEV)
                ev = f"daemon up={up}; phase={ph} after {adopt_s:.1f}s; ui_tree ok={tree.get('ok')}; ui_reader={x.get('ui_reader') if x else None}"
                rec("api-13", "pass" if (up and ph == "ready" and tree_ok(tree)) else "fail",
                    "quit keep_devices then a new daemon adopted d7 ready with a working ui_tree" if (up and ph == "ready" and tree_ok(tree)) else "d7 not adopted ready", ev)
            if "api-15" in boot_ids:
                sh(C.base, "cp /system/bin/sleep /data/local/tmp/fakeappium && chmod 755 /data/local/tmp/fakeappium")
                sh(C.base, "/data/local/tmp/fakeappium 3000 >/dev/null 2>&1 &", 10)
                time.sleep(1)
                seen = sh(C.base, "ps -A -o NAME | grep -c fakeappium").strip()
                deadline = time.time() + 40
                yielded = None; err = None
                while time.time() < deadline:
                    x = dev_of(C.call({"call": "status"}), DEV)
                    if x and x.get("ui_reader") == "yielded_appium":
                        yielded = True
                        break
                    time.sleep(1)
                tr = C.call({"call": "ui_tree", "device": DEV}, 20)
                err = str(tr.get("error", ""))
                sh(C.base, "pkill -f /data/local/tmp/fakeappium; pkill fakeappium; killall fakeappium 2>/dev/null; echo done", 10)
                resumed = False; tr2 = {}
                for _ in range(30):
                    time.sleep(1)
                    x = dev_of(C.call({"call": "status"}), DEV)
                    tr2 = C.call({"call": "ui_tree", "device": DEV}, 20)
                    if x and x.get("ui_reader") == "on" and tree_ok(tr2):
                        resumed = True
                        break
                ev = f"stand-in procs in guest={seen}; yielded_appium={yielded}; ui_tree while yielded: ok={tr.get('ok')} err={err[:160]}; resumed={resumed} (ui_reader={x.get('ui_reader') if x else None}, tree ok={tr2.get('ok')})"
                if yielded and tr.get("ok") is False and resumed:
                    rec("api-15", "pass", "ui_reader yielded_appium + ui_tree error while the stand-in ran; resumed after it was killed", ev)
                else:
                    rec("api-15", "fail", "did not yield while the Appium stand-in ran, or did not resume after", ev)
        # ---- api-14: kill d7's VM
        if "api-14" in boot_ids:
            cv = crosvm_for_d7()
            if not cv:
                rec("api-14", "blocked", "could not find a crosvm.exe with pipe ae-vm-7 to kill", "")
            else:
                for pid, cl in cv:
                    subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"], capture_output=True)
                t0 = time.time(); ph = None
                while time.time() - t0 < 20:
                    x = dev_of(C.call({"call": "status"}), DEV)
                    ph = x.get("phase") if x else None
                    if ph in ("stopped", "off", None):
                        break
                    time.sleep(0.5)
                dt = time.time() - t0
                rec("api-14", "pass" if (ph in ("stopped", "off", None) and dt <= 10) else "fail",
                    f"after killing crosvm pid(s) {[p for p, _ in cv]} (cmdline had ae-vm-7) phase={ph} after {dt:.1f}s (limit 10 s)",
                    cv[0][1][:200])
        C.stop()
        # ---- api-18 second branch: limit the daemon to 8 threads, start async, read cpus, stop
        if "api-18" in boot_ids:
            avail = status().get("available_mb", 0)
            if avail >= RAM_GATE_MB:
                D = Daemon("aff8", home, 7456, {"AE_MAX_PHONES": "99", "AE_ALLOW_OTHER_CROSVM": "1", "AE_MIN_AVAIL_MB": "2500"}, affinity_cpus=8); daemons.append(D)
                if D.start():
                    r = D.call(dict(START, **{"async": True}), 30)
                    cpus = None
                    for _ in range(30):
                        x = dev_of(D.call({"call": "status"}), DEV)
                        if x and x.get("cpus") is not None:
                            cpus = x.get("cpus"); break
                        time.sleep(1)
                    cv = crosvm_for_d7()
                    D.call({"call": "stop", "device": DEV}, 120)
                    wait_phase(D.base, DEV, ("stopped",), 90)
                    D.stop()
                    prev = RESULTS.get("api-18")
                    ok8 = str(cpus) == "2"
                    msg = f"; 8-thread daemon default cpus={cpus} (want 2)"
                    if prev:
                        rec("api-18", "pass" if (prev[0] == "pass" and ok8) else "fail", prev[1] + msg, prev[2] + f" | aff8 crosvm --cpus={[re.findall(r'--cpus\\s+(?:num-cores=)?(\\d+)', c) for _, c in cv]}")
                else:
                    prev = RESULTS.get("api-18")
                    rec("api-18", "blocked", (prev[1] if prev else "") + "; 8-thread branch not run: affinity daemon did not come up", "")
            else:
                prev = RESULTS.get("api-18")
                rec("api-18", prev[0] if prev else "blocked", (prev[1] if prev else "") + f"; 8-thread branch not run: RAM {avail} MB", prev[2] if prev else "")
    finally:
        if watcher:
            watcher.terminate()
        for d in daemons:
            d.stop()
        # d7 crosvm leftover (only ones carrying our pipe name)
        for pid, cl in crosvm_for_d7():
            if any(d.proc and d.proc.poll() is None for d in daemons):
                continue
            subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"], capture_output=True)
        time.sleep(2)
        if home:
            teardown_home(home)


# ---------------------------------------------------------------- output

def write_outputs(ids_run):
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    os.makedirs(os.path.join(MATRIX, "runs"), exist_ok=True)
    path = os.path.join(MATRIX, "runs", f"{ts}-api.tsv")
    clean = lambda s: re.sub(r"[\t\r\n]+", " ", s)
    with open(path, "w", encoding="utf-8") as f:
        f.write("id\tresult\tobserved\tevidence\n")
        for i in sorted(RESULTS):
            r, o, e = RESULTS[i]
            f.write(f"{i}\t{r}\t{clean(o)}\t{clean(e)}\n")
    # logs of the test daemons beside the run
    ld = os.path.join(MATRIX, "runs", f"{ts}-api-logs")
    if os.path.isdir(WORK):
        os.makedirs(ld, exist_ok=True)
        for n in os.listdir(WORK):
            p = os.path.join(WORK, n)
            if os.path.isfile(p):
                shutil.copy2(p, ld)
    # status column: re-read right before writing (emu-mx-ui edits other rows)
    for _ in range(5):
        with open(FEATURES, encoding="utf-8", newline="") as f:
            lines = f.read().split("\n")
        out = []
        for ln in lines:
            p = ln.split("\t")
            if len(p) >= 5 and p[0] in RESULTS and p[0] in ids_run:
                r, o, _e = RESULTS[p[0]]
                p[4] = r if r != "blocked" else "blocked: " + re.sub(r"^blocked: ", "", clean(o))[:120]
                ln = "\t".join(p)
            out.append(ln)
        tmp = FEATURES + ".api.tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write("\n".join(out))
        try:
            os.replace(tmp, FEATURES)
            break
        except OSError:
            time.sleep(1)
    return path


def main():
    all_ids = []
    with open(FEATURES, encoding="utf-8") as f:
        for ln in f.read().splitlines()[1:]:
            p = ln.split("\t")
            if p and p[0].startswith("api-"):
                all_ids.append(p[0])
    ids = [a for a in sys.argv[1:] if a in all_ids] or all_ids
    wanted = set(ids)
    if "api-19" in wanted:
        rec("api-19", "blocked", "manual: the lead runs the installer-over-running-app update flow", "")
    try:
        # tier 1: read-only probes in parallel, d2 chain beside them (its own lifecycle ops are serial)
        jobs = []
        with cf.ThreadPoolExecutor(8) as ex:
            for i, fn in (("api-01", c_api01), ("api-05", c_api05), ("api-06", c_api06), ("api-07", c_api07)):
                if i in wanted:
                    jobs.append((i, ex.submit(fn)))
            if wanted & {"api-02", "api-03", "api-04", "api-17"}:
                jobs.append(("d2", ex.submit(d2_chain)))
            for i, j in jobs:
                try:
                    j.result(timeout=240)
                except Exception as e:
                    rec(i if i != "d2" else "api-02", "fail", f"check crashed or timed out: {e!r}", "")
        tier2(ids, wanted)
    finally:
        # fold build-under-test results (suffix -d7 / -main, from the test daemon) into their row
        for k in [k for k in RESULTS if k.endswith("-d7") or k.endswith("-main")]:
            extra = RESULTS.pop(k)
            base, tag = k.rsplit("-", 1)
            label = "test daemon (built main)"
            if base in RESULTS:
                r, o, e = RESULTS[base]
                RESULTS[base] = ("fail" if "fail" in (r, extra[0]) else r, f"installed 0.2.6 live: {r}: {o} | {label}: {extra[0]}: {extra[1]}", e + " || " + extra[2])
            elif base in wanted:
                RESULTS[base] = (extra[0], f"{label}: {extra[1]}", extra[2])
        for k in [k for k in RESULTS if k not in wanted]:
            RESULTS.pop(k)
        for i in wanted:
            if i not in RESULTS:
                RESULTS[i] = ("blocked", "check did not run (earlier step failed or timed out)", "")
        p = write_outputs(wanted)
        print("results:", p)
        c = {k: sum(1 for v in RESULTS.values() if v[0] == k) for k in ("pass", "fail", "blocked")}
        print(c)
        if os.path.isdir(WORK) and not os.path.exists(os.path.join(WORK, "home")):
            shutil.rmtree(WORK, ignore_errors=True)


if __name__ == "__main__":
    main()
