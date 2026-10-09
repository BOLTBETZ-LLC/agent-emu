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
print("ok")
