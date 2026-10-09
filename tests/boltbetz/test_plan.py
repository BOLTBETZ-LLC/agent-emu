"""Offline check of plan.py: expansion, chunking, dealing, RAM math. No daemon, no phones. python test_plan.py"""
import os, tempfile

os.environ["AE_RUNS_DIR"] = tempfile.mkdtemp()
import plan
plan.run.report_bug = lambda *a: None  # offline: no bug feed posts to a daemon

# RAM: (available - 4000) / 750, never negative
assert plan.ram_max_new(3000) == 0 and plan.ram_max_new(4749) == 0 and plan.ram_max_new(4750) == 1
assert plan.ram_max_new(7000) == 4 and plan.boot_s(0) == 0 and plan.boot_s(1) == 110 and plan.boot_s(3) == 220

# expansion: every item kind, with history overriding the estimate
p = {"items": [
    {"cases": "cases/L4-01-*", "priority": "P0"},
    {"goal": "See my rewards and offers", "until_id": "rewards-screen", "requires_state": "funded-25"},
    {"pict": "matrix/models/home.pict", "limit": 2},
    {"flow": "machine", "step": "transfer", "states": ["funded", "funded-0"], "faults": ["none", "synkros:12-001", "p500"]},
    {"cases": "cases/L2-1*"},
]}
cs = plan.expand(p, history={"L4-01-home-renders": 7.5})
ids = [c["id"] for c in cs]
assert ids[0] == "L4-01-home-renders" and cs[0]["_est"] == 7.5 and cs[0]["_prio"] == 0
g = cs[1]
assert g["id"].startswith("G-001-") and g["_state"] == "funded-25" and g["_portable"]
assert any("decide" in s and s["until_id"] == "rewards-screen" for s in g["steps"])
assert sum(i.startswith("M-home-") for i in ids) == 2
flows = [c for c in cs if c["id"].startswith("F-machine-")]
assert len(flows) == 6, len(flows)
rows = [c["matrix"]["row"] for c in flows]
assert all(r["Step"] == "transfer" for r in rows)
assert {c["_state"] for c in flows} == {None, "funded-0"}  # funded = model State value; funded-0 = catalog state
assert sum(r["SynkrosErr"] == "12-001" for r in rows) == 2 and sum(r["AppFault"] == "p500" for r in rows) == 2
pinned = [c for c in cs if c["id"].startswith("L2-1")]
assert pinned and all(not c["_portable"] for c in pinned)
try:
    plan.expand({"items": [{"flow": "machine", "faults": ["synkros:E20"]}]}, history={})
    raise AssertionError("unknown fault value accepted")
except ValueError as e:
    assert "SynkrosErr=E20" in str(e)

# chunking: by (state, pinned lane); long groups cut into equal chunks <= 8 min; P0 first
fake = lambda i, est, state=None, lane=None, pr=1: {"id": f"c{i:02d}", "lane": lane or "any", "_est": est,
                                                    "_state": state, "_prio": pr, "_area": "a"}
many = [fake(i, 60) for i in range(20)] + [fake(30 + i, 30, "funded-0") for i in range(3)] + \
       [fake(40, 50, lane="L2"), fake(41, 50, lane="L2", pr=0)]
chunks = plan.chunk(many)
plain = [ch for ch in chunks if ch["state"] is None and ch["lane"] is None]
assert len(plain) == 3 and all(len(ch["cases"]) in (6, 7) for ch in plain)  # 1200 s -> 3 x 400 s
assert all(ch["est_s"] <= plan.CHUNK_MAX_S + 7 * plan.RESET_S for ch in plain)
st = next(ch for ch in chunks if ch["state"] == "funded-0")
assert len(st["cases"]) == 3 and st["est_s"] == 3 * (30 + plan.RESET_S) + plan.STATE_S
l2 = next(ch for ch in chunks if ch["lane"] == "L2")
assert chunks[0] is l2 and l2["cases"][0]["id"] == "c41"  # P0 chunk first; P0 case first inside it
assert [ch["id"] for ch in chunks] == [f"C{i:02d}" for i in range(1, len(chunks) + 1)]
assert sorted(c["id"] for ch in chunks for c in ch["cases"]) == sorted(c["id"] for c in many)

# dealing: pinned chunk only on its lane's phone; no phone for a lane = skipped; load balanced
sched, load, crit, skipped = plan.deal(chunks, {"d0": "L1", "d1": "L2"})
assert "C01" in sched["d1"] and not skipped and crit and crit[2] == max(load.values())
assert abs(load["d0"] - load["d1"]) <= max(ch["est_s"] for ch in chunks)
_, _, _, skipped = plan.deal(chunks, {"d0": "L1"})
assert skipped == ["C01"]
print("test_plan: all asserts passed")
