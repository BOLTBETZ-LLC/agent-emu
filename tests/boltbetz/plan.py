"""Test plan in, chunks dealt across N phones out. Stdlib + PyYAML.

  python plan.py run plans/example.yaml --phones 2 --dry     # cases, chunks, phones, est. wall time; starts nothing
  python plan.py run plans/example.yaml --phones 2           # boot phones, run, stop the phones it booted
  python plan.py run plans/example.yaml --phones 2 --keep    # leave the phones it booted running

Plan YAML (tests/boltbetz/plans/<name>.yaml; paths inside are relative to tests/boltbetz):
  name: x
  items:
    - pict: matrix/models/home.pict     # PICT rows -> cases via matrix/gen.py (limit: N = first N rows)
    - cases: cases/L4-0*                # existing case files by glob
    - goal: "See my rewards and offers" # Jev decide taps toward the goal, then a judge check (until_id, judge optional)
    - flow: machine                     # gen.py area (model) x states x faults; step: picks the model's Step
      step: connect
      states: [funded, zero_balance]    # a model State value is set by the case itself; any other name = requires_state
      faults: [none, "synkros:31-001", p500, "p500@getHomeOffers"]
  Every item may carry priority (P0 first, default P1), requires_state, area, timeout_s, limit.

Expansion -> cases with requires_state, est seconds (results history, else gen's est, else timeout_s/4), portable.
Chunks = cases grouped by (requires_state, pinned lane), cut to ~5-8 min. Each phone (lanes.json order: d0=L1,
d1=L2, ...) pulls the next chunk it may run (P0 first, longest first), calls the emulator lane_state op for the
chunk's state, then runs each case with run.py's per-case reset. Results: results/<stamp>/ like matrix mode.
"""
import itertools, json, math, sys, threading, time, urllib.error, urllib.request
from fnmatch import fnmatch
from pathlib import Path

import yaml

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE), str(HERE / "matrix")]
import gen, run  # noqa: E402

CHUNK_MAX_S = 480      # 8 min of estimated work; groups longer than this are cut into equal ~5-8 min chunks
RESET_S = 4            # run.reset per case (measured ~3.8 s)
STATE_S = 20           # one lane_state call (unmeasured: the op does not exist yet)
BOOT_S = 110           # one keep_data boot; 2 boot at a time
PHONE_BUSY_MB, RAM_FLOOR_MB = 750, 4000
IMAGE, SCREEN = "slim5", "iphone17promax-half"


# ---------- expansion ----------

def prio(p):
    return int(str(p or "P1").lstrip("Pp"))


def model_values(model):
    """'Col: a, b' header lines of a .pict file -> {col: [values]}."""
    out = {}
    for line in Path(model).read_text(encoding="utf-8").splitlines():
        if line.strip() == "" and out:
            break
        if ":" in line and not line.startswith("#") and not line.lstrip().startswith(("{", "IF")):
            k, v = line.split(":", 1)
            out[k.strip()] = [x.strip() for x in v.split(",")]
    return out


def flow_rows(item):
    """flow x states x faults -> (PICT-shaped row, requires_state) pairs, built on a real row of the model."""
    area = item["flow"]
    model = HERE / "matrix" / "models" / f"{area}.pict"
    vals, rows = model_values(model), gen.pict_rows(model)
    if item.get("step"):
        rows = [r for r in rows if r.get("Step") == item["step"]]
        if not rows:
            raise ValueError(f"flow {area}: no PICT row with Step={item['step']}")
    base = next((r for r in rows if r.get("AppFault", "none") == "none" and r.get("SynkrosErr", "none") == "none"), rows[0])
    out = []
    for state, fault in itertools.product(item.get("states") or [None], item.get("faults") or ["none"]):
        r, need = dict(base), item.get("requires_state")
        if state is not None:
            if state in vals.get("State", []):
                r["State"] = state  # the case seeds it itself (gen.state_setup)
            else:
                need = state        # a catalog state: lane_state sets it per chunk
        for k, v in (("AppFault", "none"), ("SynkrosErr", "none"), ("FaultEndpoint", "none"),
                     ("Network", "normal"), ("Lifecycle", "warm")):  # only the asked state x fault varies
            if k in r and v in vals.get(k, []):
                r[k] = v
        if fault.startswith("synkros:"):
            r["SynkrosErr"] = fault.split(":", 1)[1]
        elif fault != "none":
            r["AppFault"], _, ep = fault.partition("@")
            if "FaultEndpoint" in vals:
                r["FaultEndpoint"] = ep or next(v for v in vals["FaultEndpoint"] if v != "none")
        for k, v in r.items():
            if k in vals and v not in vals[k]:
                raise ValueError(f"flow {area}: {k}={v} is not in {model.name} ({', '.join(vals[k])})")
        out.append((r, need))
    return out


def goal_case(item, n):
    goal = item["goal"]
    dec = {"decide": goal, "max_steps": item.get("max_steps", 4)}
    if item.get("until_id"):
        dec["until_id"] = item["until_id"]
    slug = "-".join(goal.lower().split())[:40]
    return {"id": f"G-{n:03d}-{''.join(ch for ch in slug if ch.isalnum() or ch == '-')}", "lane": "any", "portable": True,
            "timeout_s": item.get("timeout_s", 120), "preconditions": {"signed_in": True},
            "steps": gen.home() + [dec, {"check": {"judge": item.get("judge") or f"The screen shows this is done: {goal}"}},
                                   {"check": {"screenshot": "goal"}}],
            "checks": list(gen.FINAL), "cleanup": gen.home(), "note": f"plan goal: {goal}"}


def expand(plan, history=None):
    """Plan dict -> flat case list. Each case gets _area, _prio, _state, _est (seconds), _portable."""
    history = run.wall_history() if history is None else history
    cases, seen, goals = [], set(), 0
    for item in plan.get("items", []):
        got = []  # (case, requires_state)
        if "pict" in item:
            area = Path(item["pict"]).stem
            for n, r in enumerate(gen.pict_rows(HERE / item["pict"])[:item.get("limit")], 1):
                got.append((gen.build(area, r, n)[0], item.get("requires_state")))
        elif "cases" in item:
            files = sorted(p for p in HERE.glob(item["cases"]) if p.suffix == ".json") or \
                sorted(HERE.glob(item["cases"] + ".json"))
            if not files:
                raise ValueError(f"cases {item['cases']}: no case files")
            got = [(json.loads(p.read_text(encoding="utf-8")), item.get("requires_state")) for p in files[:item.get("limit")]]
        elif "goal" in item:
            goals += 1
            got = [(goal_case(item, goals), item.get("requires_state"))]
        elif "flow" in item:
            rows = flow_rows(item)[:item.get("limit")]
            got = [(gen.build(item["flow"], r, n)[0], need) for n, (r, need) in enumerate(rows, 1)]
            for c, _ in got:
                c["id"] = "F-" + c["id"][2:]  # M-<area>-... -> F-<area>-...: never collides with a pict case
        else:
            raise ValueError(f"plan item has none of pict/cases/goal/flow: {item}")
        for c, need in got:
            if c["id"] in seen:
                print(f"note: {c['id']} listed twice in the plan; kept the first", flush=True)
                continue
            seen.add(c["id"])
            if "timeout_s" in item:
                c["timeout_s"] = item["timeout_s"]
            c["_area"] = item.get("area") or c.get("matrix", {}).get("area") or c.get("lane") or "any"
            c["_prio"] = prio(item.get("priority"))
            c["_state"] = need or c.get("requires_state")
            c["_est"] = history.get(c["id"]) or c.get("matrix", {}).get("est_s") or c.get("timeout_s", 120) / 4
            c["_portable"] = run.pinned_lane(c) is None
            cases.append(c)
    return cases


# ---------- chunking and dealing ----------

def chunk(cases, max_s=CHUNK_MAX_S):
    """Group by (requires_state, pinned lane); cut a group into ceil(total/max_s) near-equal chunks.
    Inside a chunk: priority first, "last" cases at the end. Chunks come back P0 first, then longest first."""
    groups = {}
    for c in cases:
        groups.setdefault((c["_state"], run.pinned_lane(c)), []).append(c)
    chunks = []
    for (state, lane), cs in groups.items():
        cs.sort(key=lambda c: (bool(c.get("last")), c["_prio"], c["id"]))
        total = sum(c["_est"] for c in cs)
        target, cur = total / max(1, math.ceil(total / max_s)), []
        for c in cs:
            cur.append(c)
            if sum(x["_est"] for x in cur) >= target - 1e-9 and c is not cs[-1]:
                chunks.append(cur)
                cur = []
        chunks.append(cur)
    out = [{"cases": cs, "state": cs[0]["_state"], "lane": run.pinned_lane(cs[0]), "prio": min(c["_prio"] for c in cs),
            "est_s": sum(c["_est"] + RESET_S for c in cs) + (STATE_S if cs[0]["_state"] else 0)} for cs in chunks]
    out.sort(key=lambda ch: (ch["prio"], -ch["est_s"]))
    for i, ch in enumerate(out, 1):
        ch["id"] = f"C{i:02d}"
    return out


def can_run(ch, lane):
    return ch["lane"] in (None, lane)


def deal(chunks, phone_lane):
    """Simulate the run: each chunk (in order) goes to the eligible phone that frees up first.
    Returns ({phone: [chunk ids]}, {phone: busy s}, critical (chunk id, phone, end s) or None, skipped chunk ids)."""
    load, plan, crit, skipped = dict.fromkeys(phone_lane, 0.0), {p: [] for p in phone_lane}, None, []
    for ch in chunks:
        ok = [p for p, l in phone_lane.items() if can_run(ch, l)]
        if not ok:
            skipped.append(ch["id"])
            continue
        p = min(ok, key=load.get)
        load[p] += ch["est_s"]
        plan[p].append(ch["id"])
        if crit is None or load[p] > crit[2]:
            crit = (ch["id"], p, load[p])
    return plan, load, crit, skipped


def ram_max_new(available_mb, busy_mb=PHONE_BUSY_MB, floor_mb=RAM_FLOOR_MB):
    """How many more phones host RAM allows: (available - floor) / busy-per-phone."""
    return max(0, int((available_mb - floor_mb) // busy_mb))


def boot_s(n_new):
    return BOOT_S * math.ceil(n_new / 2)


# ---------- phones and the emulator state op ----------

def api(body, timeout=600):
    req = urllib.request.Request(run.API + "/api", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def boot(phones, started):
    """Start the phones not running, 2 at a time (keep_data: lane phones keep app + sign-in). Appends each phone it
    booted to `started` (so the caller stops them even when a later boot fails)."""
    st = api({"call": "status"})
    up = {d["id"] for d in st["devices"] if d.get("phase") != "stopped"}
    new = [p for p in phones if p not in up]
    if len(new) > (room := ram_max_new(st.get("available_mb", 0))):
        raise SystemExit(f"refused: {len(new)} phone(s) to boot, RAM allows {room} more "
                         f"(available {st.get('available_mb')} MB, ~{PHONE_BUSY_MB} MB each + {RAM_FLOOR_MB} MB floor); "
                         f"max --phones now: {len(phones) - len(new) + room}")
    for i in range(0, len(new), 2):
        pair = new[i:i + 2]
        print(f"booting {','.join(pair)} ({IMAGE}, {SCREEN}, keep_data)", flush=True)
        r = api({"call": "start_many", "devices": pair, "image": IMAGE, "screen": SCREEN, "keep_data": True})
        started += [d["device"] for d in r.get("devices", []) if d.get("ok")]
        if not r.get("ok"):
            bad = "; ".join(f"{d['device']}: {d.get('error')}" for d in r.get("devices", []) if not d.get("ok"))
            raise SystemExit(f"start_many {pair}: {bad or r.get('error')}")


_state_op = {}  # "unavailable" once the emulator answered 404/405: the rest of the run falls back without asking again


def lane_state(lane, state):
    """Put the lane's account into `state` via the emulator. Returns a note when it could not (never fakes success);
    raises RuntimeError when the op exists and failed."""
    if not state:
        return None
    if _state_op.get("unavailable"):
        return f"requires_state {state} not set: state op unavailable"
    import emu
    try:
        emu.req("POST", f"/admin/api/lanes/{lane}/state", {"state": state})
        return None
    except RuntimeError as e:
        if any(f": {code} " in str(e) for code in (404, 405)):
            _state_op["unavailable"] = True
            print(f"state op unavailable ({str(e)[:80]}); falling back to the per-case reset", flush=True)
            return f"requires_state {state} not set: state op unavailable"
        raise RuntimeError(f"lane_state {lane} {state}: {e}")


# ---------- run ----------

def execute(chunks, phones, phone_lane, out):
    m = run.Matrix(phones, phone_lane, [], {}, out)
    m.total = sum(len(ch["cases"]) for ch in chunks)
    queue, lock = list(chunks), threading.Lock()
    emu_ok = True
    try:
        import emu
        emu._key()
    except Exception as e:
        emu_ok = False
        print(f"WARNING: Synkros emulator unreachable ({e}); resets skip the session end and $25 restore", flush=True)
    with m.cv:
        for ch in [ch for ch in queue if ch["lane"] and ch["lane"] not in phone_lane.values()]:
            queue.remove(ch)
            for c in ch["cases"]:
                m.final(run.Matrix.stub(c, ch["lane"], "SKIP", f"no phone for {ch['lane']} in this run"))

    def fail_rest(cs, phone, lane, why):
        with m.cv:
            for c in cs:
                m.final(run.Matrix.stub(c, lane, "FAIL", why, phone))

    def worker(phone):
        lane = phone_lane[phone]
        try:
            run.lane_setup(phone, lane, camera_off=True)
        except Exception as e:
            print(f"{phone} setup: {e}", flush=True)
            return
        bad = 0
        while True:
            with lock:
                ch = next((ch for ch in queue if can_run(ch, lane)), None)
                if ch is None:
                    return
                queue.remove(ch)
            print(f"{phone}: chunk {ch['id']} ({len(ch['cases'])} cases, state {ch['state'] or '-'}, ~{ch['est_s'] / 60:.1f} min)", flush=True)
            try:
                note = lane_state(lane, ch["state"]) if emu_ok else (f"requires_state {ch['state']} not set: emulator unreachable" if ch["state"] else None)
            except RuntimeError as e:
                fail_rest(ch["cases"], phone, lane, str(e))
                continue
            for i, c in enumerate(ch["cases"]):
                t = time.time()
                m.busy[phone] = c["id"]
                try:
                    notes = run.reset(phone, lane, emu_ok)
                    rs = round(time.time() - t, 1)
                    res = run.run_case(phone, c, out, lane=lane, hard=True, say=False)
                    res["reset_s"] = rs
                    if notes:
                        res["reset_fixed"] = notes
                    bad = 0
                except Exception as e:
                    res = run.Matrix.stub(c, lane, "FAIL", str(e), phone)
                    res["wall_s"] = round(time.time() - t, 1)
                    bad += 1
                res.update(area=c["_area"], attempt=1, chunk=ch["id"])
                if note:
                    res["state_note"] = note
                m.util[phone] += time.time() - t
                with m.cv:
                    m.busy.pop(phone, None)
                    m.final(res)
                if bad >= 3:
                    print(f"{phone}: 3 resets failed in a row, phone retired from this run", flush=True)
                    fail_rest(ch["cases"][i + 1:], phone, lane, "phone retired: 3 resets failed in a row")
                    return

    ts = [threading.Thread(target=worker, args=(p,), daemon=True) for p in phones]
    for t in ts: t.start()
    for t in ts: t.join()
    for ch in queue:  # nobody could take it (its phone died): a FAIL, never a gap
        fail_rest(ch["cases"], None, ch["lane"], "never ran (no phone left)")
    with m.cv:
        m.save(finished=True)
    return run.matrix_report(m, out)


def main(argv):
    if len(argv) < 2 or argv[0] != "run":
        print(__doc__)
        return 2
    path = Path(argv[1])
    path = path if path.exists() else HERE / argv[1]
    want = argv[argv.index("--phones") + 1] if "--phones" in argv else "4"
    dry, keep = "--dry" in argv, "--keep" in argv
    lane_order = [(v["phone"], k) for k, v in run.LANES.items() if "phone" in v]
    if want.isdigit():  # N = the first N lanes.json phones
        if int(want) > len(lane_order):
            print(f"refused: --phones {want}, but lanes.json has {len(lane_order)} lane accounts (one per phone)")
            return 2
        phone_lane = dict(lane_order[:int(want)])
    else:  # d0,d2 = exactly these lane phones (when another agent holds one of the first N)
        phone_lane = {p: l for p, l in lane_order if p in want.split(",")}
        if bad := set(want.split(",")) - set(phone_lane):
            print(f"refused: {','.join(sorted(bad))} not a lanes.json phone")
            return 2
    n = len(phone_lane)
    plan = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = expand(plan)
    chunks = chunk(cases)
    sched, load, crit, skipped = deal(chunks, phone_lane)
    try:
        st = api({"call": "status"}, timeout=10)
        up = {d["id"] for d in st["devices"] if d.get("phase") != "stopped"}
        need = [p for p in phone_lane if p not in up]
        ram = f"{len(need)} to boot, RAM allows {ram_max_new(st.get('available_mb', 0))} more (available {st.get('available_mb')} MB)"
    except Exception as e:
        need, ram = list(phone_lane), f"daemon unreachable ({e})"
    print(f"plan {plan.get('name', path.stem)}: {len(cases)} cases -> {len(chunks)} chunks on {n} phones ({ram})")
    for ch in chunks:
        print(f"  {ch['id']} P{ch['prio']} state={ch['state'] or '-'} lane={ch['lane'] or 'any'} "
              f"{len(ch['cases'])} cases ~{ch['est_s'] / 60:.1f} min: {', '.join(c['id'] for c in ch['cases'])[:150]}")
    for p, ids in sched.items():
        print(f"  {p} ({phone_lane[p]}): {', '.join(ids) or '-'} ~{load[p] / 60:.1f} min")
    wall = boot_s(len(need)) + max(load.values(), default=0)
    print(f"est. wall time ~{wall / 60:.1f} min (boot ~{boot_s(len(need))} s)"
          + (f"; critical chunk {crit[0]} on {crit[1]}" if crit else "")
          + (f"; SKIP (no phone for their lane): {', '.join(skipped)}" if skipped else ""), flush=True)
    if dry:
        return 0
    out = Path(run.os.environ.get("AE_RUNS_DIR") or HERE / "results") / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True)
    (out / "plan.json").write_text(json.dumps({"plan": str(path), "phones": phone_lane, "chunks": [
        {k: v for k, v in ch.items() if k != "cases"} | {"cases": [c["id"] for c in ch["cases"]]} for ch in chunks]}, indent=1))
    started = []
    try:
        boot(list(phone_lane), started)
        return execute(chunks, list(phone_lane), phone_lane, out)
    finally:
        if started and not keep:
            print(f"stopping {','.join(started)} (booted by this run; --keep leaves them)", flush=True)
            api({"call": "stop_many", "devices": started})


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
