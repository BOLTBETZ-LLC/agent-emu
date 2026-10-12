"""Pause and resume the whole agent-emu session: every running phone, its settings, claims, the app in front, and
any live test run. Used by the desktop app around an update; agents and people can run it too.

  python session.py busy                # what is running now (JSON); exit 0 = idle, 3 = busy
  python session.py save [--snapshot]   # write session.json, stop live run.py runs, stop phones (disks synced)
  python session.py resume              # boot the same phones (keep_data), re-claim, relaunch apps, resume runs

Phones come back from their own disks: installed apps, sign-in and app data are kept. RAM is not: each phone
cold-boots and its app relaunches on its start screen. --snapshot also saves a named snapshot per phone
(`session-<stamp>`, ~2 min each, reboots it) so `restore` can roll a phone back to this moment later.
A test run is stopped and later continued with `run.py --resume <its results folder>`: cases already finished are kept,
the rest (including the one that was mid-flight) run again.

Env: AE_API (daemon HTTP, default http://127.0.0.1:7401), AE_SESSION (file, default %LOCALAPPDATA%/agent-emu/session.json).
"""
import json, os, subprocess, sys, time, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
API = os.environ.get("AE_API", "http://127.0.0.1:7401")
RUNS = Path(os.environ.get("AE_RUNS_DIR") or HERE / "results")
FILE = Path(os.environ.get("AE_SESSION") or Path(os.environ.get("LOCALAPPDATA", HERE)) / "agent-emu" / "session.json")
PKG_SKIP = ("com.android.", "com.google.android.", "org.mozilla.firefox")


def call(name, timeout=600, **args):
    req = urllib.request.Request(f"{API}/api", data=json.dumps({"call": name, **args}).encode())
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def say(m):
    print(m, flush=True)


def runner_procs():
    """Live run.py / plan.py processes: [{pid, cmd}]."""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ? { $_.CommandLine -match '(run|plan)\\.py' } | "
          "% { [pscustomobject]@{pid=$_.ProcessId; cmd=$_.CommandLine} } | ConvertTo-Json -Compress")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout.strip()
    if not out:
        return []
    got = json.loads(out)
    return [p for p in (got if isinstance(got, list) else [got]) if str(os.getpid()) != str(p["pid"])]


def live_runs():
    """Results folders whose results.json says running (or that have no results.json yet and are < 10 min old)."""
    live = []
    for d in sorted(RUNS.glob("*/"), reverse=True)[:20]:
        f = d / "results.json"
        try:
            if f.exists() and json.loads(f.read_text(encoding="utf-8")).get("running"):
                live.append(str(d))
            elif not f.exists() and time.time() - d.stat().st_mtime < 600:
                live.append(str(d))
        except (OSError, ValueError):
            pass
    return live


def busy():
    st = call("status", timeout=10)
    phones = [d["id"] for d in st["devices"] if d.get("phase") != "stopped"]
    claims = {d["id"]: d["claim"] for d in st["devices"] if d.get("claim")}
    procs = runner_procs()
    appium = []
    for p in phones:
        try:
            if "io.appium.uiautomator2.server" in call("shell", device=p, cmd="ps -A -o NAME", timeout=15).get("out", ""):
                appium.append(p)
        except Exception:
            pass
    return {"phones": phones, "claims": claims, "test_runs": procs, "live_results": live_runs() if procs else [],
            "appium_sessions": appium, "busy": bool(procs or claims or appium)}


def foreground(p):
    try:
        out = call("shell", device=p, cmd="dumpsys activity activities | grep -m 1 mResumedActivity", timeout=20).get("out", "")
        pkg = out.split("/")[0].split()[-1] if "/" in out else ""
        return "" if not pkg or pkg.startswith(PKG_SKIP) else pkg
    except Exception:
        return ""


def save(snapshot=False):
    st = call("status", timeout=10)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    procs = runner_procs()
    # Only with a live runner: a crashed old run's results.json can still say "running".
    runs = [r for r in live_runs() if (Path(r) / "argv.json").exists()] if procs else []
    phones = []
    for d in st["devices"]:
        if d.get("phase") == "stopped":
            continue
        phones.append({"id": d["id"], "image": d.get("image_name") or None, "screen": (d.get("screen") or {}).get("name"),
                       "net": d.get("net", True), "claim": d.get("claim"), "app": foreground(d["id"])})
    sess = {"saved": stamp, "phones": phones, "runs": runs, "runner_cmds": [p["cmd"] for p in procs], "snapshots": {}}
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(sess, indent=2))  # first, so a crash below still leaves a resumable record
    for p in procs:
        subprocess.run(["taskkill", "/PID", str(p["pid"]), "/T", "/F"], capture_output=True)
        say(f"stopped test run pid {p['pid']}")
    if snapshot:
        for ph in phones:
            r = call("snapshot", device=ph["id"], name=f"session-{stamp}", timeout=900)
            sess["snapshots"][ph["id"]] = f"session-{stamp}" if r.get("ok") else f"FAILED: {r.get('error')}"
            say(f"snapshot {ph['id']}: {sess['snapshots'][ph['id']]}")
        FILE.write_text(json.dumps(sess, indent=2))
    if phones:
        r = call("stop_many", devices=[ph["id"] for ph in phones], timeout=600)
        say(f"stopped phones {[ph['id'] for ph in phones]} ok={r.get('ok')}")
    say(f"saved {FILE}: {len(phones)} phones, {len(runs)} test runs")
    return sess


def resume():
    if not FILE.exists():
        say("no saved session"); return 0
    sess = json.loads(FILE.read_text(encoding="utf-8"))
    groups = {}
    for ph in sess["phones"]:
        groups.setdefault((ph["image"], ph["screen"]), []).append(ph["id"])
    for (image, screen), ids in groups.items():
        args = {"devices": ids, "keep_data": True, "async": True}
        if image: args["image"] = image
        if screen: args["screen"] = screen
        r = call("start_many", timeout=60, **args)
        say(f"starting {ids} ({image}, {screen}): ok={r.get('ok')} {r.get('error', '')}")
    want = {ph["id"] for ph in sess["phones"]}
    for i in range(40):  # 20 min, checked every 30 s
        st = {d["id"]: d.get("phase") for d in call("status", timeout=10)["devices"]}
        say(f"  {i * 30}s: " + ", ".join(f"{p}={st.get(p, 'none')}" for p in sorted(want)))
        if all(st.get(p) == "ready" for p in want):
            break
        if any(st.get(p) in ("stopped", None) for p in want) and i > 2:
            say("a phone failed to boot; see the daemon log"); break
        time.sleep(30)
    for ph in sess["phones"]:
        c = ph.get("claim")
        if c and c.get("owner") and not str(c["owner"]).startswith("run.py-"):
            call("claim", device=ph["id"], owner=c["owner"], note=c.get("note", ""), timeout=15)
        if ph.get("app"):
            r = call("app", device=ph["id"], launch=ph["app"], timeout=60)
            say(f"{ph['id']}: relaunched {ph['app']} ok={r.get('ok')}")
    py = sys.executable
    for run in sess["runs"]:
        log = open(Path(run) / "resume.log", "a")
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        p = subprocess.Popen([py, str(HERE / "run.py"), "--resume", run], cwd=HERE, stdout=log, stderr=log, creationflags=flags)
        say(f"resumed test run {Path(run).name} (pid {p.pid}, log {run}\\resume.log)")
    FILE.rename(FILE.with_name(f"session-resumed-{sess['saved']}.json"))
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "busy":
        b = busy(); print(json.dumps(b, indent=2)); sys.exit(3 if b["busy"] else 0)
    if cmd == "save":
        save("--snapshot" in sys.argv); sys.exit(0)
    if cmd == "resume":
        sys.exit(resume())
    print(__doc__); sys.exit(2)
