"""Offline check of run.py's matrix scheduler: no daemon, no phones. python test_matrix.py"""
import json, os, tempfile, time
from pathlib import Path

os.environ["AE_RUNS_DIR"] = tempfile.mkdtemp()
os.environ["SYNKROS_API_KEY"] = "offline-test"
import run

tries = {}


def fake_case(device, case, out, lane=None, hard=False, say=True):
    case = run.for_lane(case, lane)
    tries[case["id"]] = tries.get(case["id"], 0) + 1
    time.sleep(case["t"])
    bad = case["id"] == "x-fail" or (case["id"] == "x-flaky" and tries[case["id"]] == 1)
    return {"id": case["id"], "lane": lane, "device": device, "checks": [], "shots": [], "wall_s": case["t"],
            "status": "FAIL" if bad else "PASS", "player": case.get("player")}


run.run_case, run.lane_setup, run.reset = fake_case, lambda *a, **k: None, lambda *a: []
run.claims = lambda *a, **k: None  # no daemon here
root = Path(tempfile.mkdtemp())
(root / "a").mkdir()
for i in range(8):
    (root / "a" / f"x-{i}.json").write_text(json.dumps({"id": f"x-{i}", "t": 0.05 * (i + 1), "player": "{lane.player}"}))
for cid, extra in [("x-fail", {}), ("x-flaky", {}), ("x-pin", {"lane": "L2"}), ("x-last", {"lane": "L1", "last": True}),
                   ("x-skip", {"lane": "L4"})]:
    (root / f"{cid}.json").write_text(json.dumps({"id": cid, "t": 0.05, **extra}))
assert run.main(["--phones", "d0,d1,d2", "--matrix", str(root)]) == 1  # x-fail fails
out = max(Path(os.environ["AE_RUNS_DIR"]).iterdir())
doc = json.loads((out / "results.json").read_text())
by = {r["id"]: r for r in doc["results"]}
assert len(by) == 13 and not doc["running"], doc
assert by["x-pin"]["device"] == "d1" and by["x-last"]["device"] == "d0" and by["x-skip"]["status"] == "SKIP"
assert by["x-flaky"]["flaky"] and by["x-flaky"]["device"] != by["x-flaky"]["first_attempt"]["device"]
assert by["x-fail"]["status"] == "FAIL" and tries["x-fail"] == 2 and tries["x-pin"] == 1
assert by["x-0"]["player"] == str(run.LANES[by["x-0"]["lane"]]["player"])  # {lane.x} filled per phone
time.sleep(1.1)  # results folders are per second
assert run.main(["--phones", "d0", "--matrix", str(root), "--areas", "a", "--shard", "2/2"]) == 0
assert len(json.loads((max(Path(os.environ["AE_RUNS_DIR"]).iterdir()) / "results.json").read_text())["results"]) == 4

# Smarter retry: case/setup errors and settled-screen mismatches are not retried; a signature failing in 2 cases
# of one area is repeat-fail for the rest of the run.
time.sleep(1.1)
root2 = Path(tempfile.mkdtemp())
(root2 / "b").mkdir()
errs = {"b-ph": "{lane.nope}: lanes.json has no nope for L1", "b-tid": "tap_id deposit-button: not on screen",
        "b-r1": "boom 1", "b-r2": "boom 22", "b-r3": "boom 333", "b-st": None}
for cid in errs:
    (root2 / "b" / f"{cid}.json").write_text(json.dumps({"id": cid, "t": 0.05}))


def fake_fail(device, case, out, lane=None, hard=False, say=True):
    tries[case["id"]] = tries.get(case["id"], 0) + 1
    r = {"id": case["id"], "lane": lane, "device": device, "shots": [], "wall_s": 0.05, "status": "FAIL",
         "checks": [] if errs[case["id"]] else [{"check": {"ui": "x"}, "ok": False, "detail": "x present=False"}]}
    if errs[case["id"]]:
        r["error"] = errs[case["id"]]
    else:
        r["stable_screen"] = True
    return r


run.run_case = fake_fail
assert run.main(["--phones", "d0,d1", "--matrix", str(root2)]) == 1
doc = json.loads((max(Path(os.environ["AE_RUNS_DIR"]).iterdir()) / "results.json").read_text())
by = {r["id"]: r for r in doc["results"]}
assert len(by) == 6 and not doc["running"] and "interrupted" not in doc, doc
assert tries["b-ph"] == 1 and by["b-ph"]["no_retry"] == "case/setup error"
assert tries["b-tid"] == 1 and by["b-tid"]["no_retry"] == "case/setup error"
assert tries["b-st"] == 1 and by["b-st"]["no_retry"] == "expectation mismatch on a settled screen"
boom = ["b-r1", "b-r2", "b-r3"]
assert sum(tries[c] for c in boom) == 4 and sum(bool(by[c].get("repeat_fail")) for c in boom) == 2, (tries, by)
assert sorted(doc["repeat_fail"]) == sorted(c for c in boom if by[c].get("repeat_fail"))

# wait_idle: idle after busy; None (old behaviour) when the build has no qa-idle node.
seq = [[{"resource-id": "qa-idle", "content-desc": "busy 2"}], [{"resource-id": "qa-idle", "content-desc": "idle 0"}]]
run.nodes = lambda d: seq.pop(0) if len(seq) > 1 else seq[0]
assert run.wait_idle("d0", 2)[0] is True
run.nodes = lambda d: [{"resource-id": "home-screen"}]
assert run.wait_idle("d0", 2)[0] is None
run.nodes = lambda d: [{"resource-id": "qa-idle", "content-desc": "busy 1"}]
assert run.wait_idle("d0", 0.3)[0] is False

# An interrupted run (Ctrl-Break) does not leave "running": true behind.
import subprocess, signal, sys
runs = Path(tempfile.mkdtemp())
child = r"""
import os, sys, time
sys.path.insert(0, %r)
import run
run.lane_setup, run.reset = (lambda *a, **k: None), (lambda *a: [])
def slow(device, case, out, lane=None, hard=False, say=True):
    time.sleep(60)
    return {"id": case["id"], "status": "PASS", "checks": [], "shots": []}
run.run_case = slow
run.main(["--phones", "d0", "--matrix", %r])
""" % (str(Path(__file__).parent), str(root2))
p = subprocess.Popen([sys.executable, "-c", child], env={**os.environ, "AE_RUNS_DIR": str(runs)},
                     creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
end = time.time() + 20
while not list(runs.glob("*/results.json")) and time.time() < end:
    time.sleep(0.2)
time.sleep(0.5)
p.send_signal(getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM))
p.wait(timeout=15)
doc = json.loads(next(runs.glob("*/results.json")).read_text())
assert doc["running"] is False and doc["interrupted"] is True, doc
print("ok")
