"""Minimal lane runner for agent-emud. Stdlib only.

python run.py [--phones d0,d1,d2,d3] [--lanes L1,L2] [cases_dir]
python run.py --phones d1,d2,d3 --lanes L2,L3,L4      # L2->d1, L3->d2, L4->d3, in parallel
python run.py --phones d3 --case L4-01                # one case (id prefix; comma list ok) on d3
python run.py --phones d0,d1,d2,d3 --matrix cases/matrix [--areas a,b] [--shard 1/2]   # work queue, see matrix_main

A step {"check": {...}} runs a check mid-case; "cleanup" steps always run after the case (pass or fail).

Lanes are dealt to phones in order (L1->first phone, L2->second, ...). Each lane runs on its own thread,
its cases serially in id order. A lane with no phone is reported as SKIP. Writes
results/<stamp>/results.json, grid.md and one PNG per screenshot check.

Talks to the daemon's HTTP mirror (POST $AE_API/api, default http://127.0.0.1:7401). Results go to
$AE_RUNS_DIR (default results/ beside this file). Port 7400 is newline-delimited JSON
over raw TCP, which urllib cannot speak; every 7400 call is also on 7401 /api (API.md).
"""
import json, os, re, sys, threading, time, urllib.request
from pathlib import Path

API = os.environ.get("AE_API", "http://127.0.0.1:7401").rstrip("/")
HERE = Path(__file__).parent


_tl = threading.local()  # .deadline: matrix mode's hard per-case timeout, enforced on every daemon call


def call(device, call_name, **args):
    wait = 180
    if getattr(_tl, "deadline", None):
        wait = min(wait, _tl.deadline - time.time())
        if wait <= 0:
            raise TimeoutError(f"{call_name}: case hit its hard timeout")
    body = json.dumps({"call": call_name, "device": device, **args}).encode()
    req = urllib.request.Request(API + "/api", data=body, headers={"Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=max(wait, 1)))
    if not r.get("ok"):
        raise RuntimeError(f"{call_name}: {r.get('error')}")
    return r


def nodes(device):
    xml = call(device, "ui_tree")["xml"]
    out = []
    for m in re.finditer(r"<node [^>]*>", xml):
        a = dict(re.findall(r'([\w-]+)="([^"]*)"', m.group(0)))
        x1, y1, x2, y2 = map(int, re.findall(r"\d+", a.get("bounds", "[0,0][0,0]")))
        a["center"], a["b"] = ((x1 + x2) // 2, (y1 + y2) // 2), [x1, y1, x2, y2]
        out.append(a)
    return out


def find(ns, key):
    """key matches resource-id (RN testID), text or content-desc exactly."""
    return next((n for n in ns if key in (n.get("resource-id"), n.get("text"), n.get("content-desc"))), None)


def wait_for(device, keys, timeout_s=15):
    end = time.time() + timeout_s
    while True:
        ns = nodes(device)  # ~0.1 s with the daemon's persistent dumper, so poll back to back
        hit = next(filter(None, (find(ns, k) for k in keys)), None)
        if hit or time.time() > end:
            return hit, ns


_scale = {}


def screen_scale(device):
    """device width / 1320 (the native iPhone 17 Pro Max width the cases' raw px were written for)."""
    if device not in _scale:
        devs = call(device, "status").get("devices", [])
        w = next((d["screen"]["width"] for d in devs if d["id"] == device), 1320)
        _scale[device] = w / 1320
    return _scale[device]


def do_decide(device, step, ctx):
    """{"decide": goal, "until_id": id, "max_steps": 5}: model picks a tap (decide.py) until until_id shows up.
    Without until_id it makes max_steps (default 1) taps. Unsure = FAIL with the reason: an agent takes over."""
    import decide
    until, goal = step.get("until_id"), step["decide"]
    for _ in range(step.get("max_steps", 5 if until else 1)):
        ns = nodes(device)
        if until and find(ns, until):
            return
        pick, entry = decide.next_tap(goal, ns, ctx["decisions"])
        if pick is None:
            raise RuntimeError(f"decide {goal!r}: unsure ({entry.get('reason')}, picked {entry.get('choice')}), agent takes over")
        if pick == "scroll_down":
            w = screen_scale(device) * 1320
            call(device, "swipe", x1=w // 2, y1=round(w * 1.74), x2=w // 2, y2=round(w * 0.53),
                 device_px=True, screenshot=False)
        else:
            call(device, "tap", x=pick["center"][0], y=pick["center"][1], device_px=True, screenshot=False)
        if until:
            wait_for(device, [until], 2)
        else:
            time.sleep(0.5)
    if until and not wait_for(device, [until], 3)[0]:
        raise RuntimeError(f"decide {goal!r}: {until} not reached in {step.get('max_steps', 5)} steps")


def do_step(device, step, ctx=None):
    step = dict(step)
    if "decide" in step:
        do_decide(device, step, ctx)
    elif "tap_id" in step:  # runner helper: tap the center of a ui_tree node
        # ui_tree is ~80 ms now: an optional tap only needs a short look before giving up.
        wait = min(step.get("wait_s", 10), 0.5) if step.get("optional") else step.get("wait_s", 10)
        n, _ = wait_for(device, [step["tap_id"]], wait)
        if not n:
            if step.get("optional"):
                return
            raise RuntimeError(f"tap_id {step['tap_id']}: not on screen")
        x, y = n["center"]
        call(device, "tap", x=x, y=y, device_px=True, screenshot=False)
    elif "wait_id" in step:
        if not wait_for(device, step["wait_id"] if isinstance(step["wait_id"], list) else [step["wait_id"]],
                        step.get("wait_s", 15))[0]:
            raise RuntimeError(f"wait_id {step['wait_id']}: timed out")
    elif "sleep_ms" in step:
        # ponytail: fixed pauses capped at 300 ms (the next wait_id/tap_id polls anyway); AE_FULL_SLEEPS=1 restores them.
        time.sleep(step["sleep_ms"] / 1000 if os.environ.get("AE_FULL_SLEEPS") else min(step["sleep_ms"], 300) / 1000)
    elif "fill" in step:  # tap a text field, clear it, type: {"fill": id, "text": "10"} or {"fill": id, "secret": "card.pan"}
        n, _ = wait_for(device, [step["fill"]], step.get("wait_s", 10))
        if not n:
            raise RuntimeError(f"fill {step['fill']}: not on screen")
        call(device, "tap", x=n["center"][0], y=n["center"][1], device_px=True, screenshot=False)
        time.sleep(0.3)
        call(device, "shell", cmd="input keycombination KEYCODE_CTRL_LEFT KEYCODE_A; input keyevent KEYCODE_DEL")
        txt = secret(step["secret"]) if "secret" in step else str(step.get("text", ""))
        if txt:
            call(device, "type_text", text=txt, screenshot=False)
        time.sleep(0.3)
    elif "emu" in step:  # Synkros emulator admin API; key via asm-exec env (see withkey.py)
        import emu
        if step["emu"] == "pin":  # PIN sheet: set a fresh random PIN on the player, type it, Continue. Never stored.
            import random
            pin = "".join(random.choice("123456789") for _ in range(4))
            emu.req("PUT", f"/admin/api/players/{step['player']}", {"pin": pin})
            n, _ = wait_for(device, ["pin-cell-0"], step.get("wait_s", 10))
            if not n:
                raise RuntimeError("pin sheet not on screen")
            call(device, "tap", x=n["center"][0], y=n["center"][1], device_px=True, screenshot=False)
            time.sleep(0.4)
            for ch in pin:  # one cell per digit; a 4-char type_text lands in cell 0 only
                call(device, "type_text", text=ch, screenshot=False)
                time.sleep(0.3)
            n, _ = wait_for(device, ["enter-pin-submit-button"], 2)
            if n:
                call(device, "tap", x=n["center"][0], y=n["center"][1], device_px=True, screenshot=False)
            return
        if step["emu"] == "set_cwa":  # restore the lane's CWA: {"emu": "set_cwa", "player": id, "dollars": 25}
            emu.req("PUT", f"/admin/api/players/{step['player']}", {"cwaAvailable": round(step["dollars"] * 100_000)})
            return
        if step["emu"] == "offer":  # seed an offer: {"emu": "offer", "player": id, "name": ..., "freeplay": 5}
            emu.req("POST", f"/admin/api/players/{step['player']}/offers",
                    {"offerName": step["name"], "description": step.get("description", step["name"]),
                     "awardAmount": step.get("freeplay", 5), "awardType": step.get("award_type", "freePlay"),
                     "maxPick": 1, "expiresInDays": 7})
            return
        if step["emu"] == "clear_offers":
            for o in emu.req("GET", f"/admin/api/players/{step['player']}").get("offers", []):
                oid = o.get("notificationId") or o.get("id")
                if oid is not None:
                    emu.req("DELETE", f"/admin/api/players/{step['player']}/offers/{oid}")
            return
        if step["emu"] == "type_qr":
            call(device, "type_text", text=emu.feed(step["asset"])["qrToken"], screenshot=False)
        else:
            try:
                emu.end_session(step["asset"])
            except RuntimeError:
                if not step.get("optional"):
                    raise
    else:  # anything else is a raw daemon call: {"call": "deep_link", "uri": ...}
        name = step.pop("call")
        if step.get("device_px"):  # case coordinates are written for the native 1320 px wide screen
            f = screen_scale(device)
            step.update({k: round(v * f) for k, v in step.items() if k in ("x", "y", "x1", "y1", "x2", "y2")})
        call(device, name, **step)
        if name == "deep_link":  # the intent lands asynchronously; give the app time to act on it
            time.sleep(0.8)


def do_check(device, chk, ctx, shot_dir, case_id):
    """Returns (ok, detail)."""
    if "screenshot" in chk:
        p = shot_dir / f"{case_id}-{chk['screenshot']}.png"
        p.write_bytes(urllib.request.urlopen(f"{API}/screenshot.png?device={device}", timeout=60).read())
        ctx["shots"].append(str(p))
        return True, str(p)
    if "value" in chk:  # {"value": {"ui": id} | {"emu": path, "field": f, "scale": 100000}, "save": name | "expect": expr}
        want, end = chk.get("expect"), time.time() + chk.get("wait_s", 15 if chk.get("expect") else 5)
        while True:
            try:
                v = read_value(device, chk["value"])
            except Exception as e:
                v = None
                err = str(e)
            if v is not None and "save" in chk:
                ctx["vars"][chk["save"]] = v
                return True, f"{chk['save']}={v}"
            if v is not None and want is not None:
                target = eval(want, {"__builtins__": {}}, dict(ctx["vars"]))
                if abs(v - target) < 0.005:
                    return True, f"{v} == {want}"
            if time.time() > end:
                return False, f"value {v} (want {want})" if v is not None else f"no value: {err if v is None else ''}"
            time.sleep(0.5)
    if "ui_text" in chk:  # {"ui_text": id, "contains": "..."}: node text contains a substring
        end = time.time() + chk.get("wait_s", 10)
        while True:
            n = find(nodes(device), chk["ui_text"])
            t = (n.get("text") or n.get("content-desc") or "") if n else None
            if t is not None and chk["contains"] in t:
                return True, f"{chk['ui_text']}: {t[:80]}"
            if time.time() > end:
                return False, f"{chk['ui_text']}: {t!r}"
            time.sleep(0.3)
    if "ui_any" in chk:
        hit, _ = wait_for(device, chk["ui_any"], chk.get("wait_s", 10))
        return bool(hit), f"found {hit.get('resource-id') or hit.get('text')}" if hit else f"none of {chk['ui_any']}"
    if "ui" in chk:
        want = chk.get("present", True)
        if want:
            hit, _ = wait_for(device, [chk["ui"]], chk.get("wait_s", 10))
        else:  # wait for it to go away, not just one look
            end = time.time() + chk.get("wait_s", 5)
            while (hit := find(nodes(device), chk["ui"])) and time.time() < end:
                time.sleep(0.1)
        if bool(hit) != want:
            return False, f"{chk['ui']} present={bool(hit)}"
        if hit and "enabled" in chk and (hit.get("enabled") == "true") != chk["enabled"]:
            return False, f"{chk['ui']} enabled={hit.get('enabled')}"
        return True, f"{chk['ui']} ok"
    if "judge" in chk:  # {"judge": "Home shows a $25.00 card balance"}: model verdict on the ui_tree (decide.py)
        import decide
        time.sleep(chk.get("settle_s", 1))  # let the screen finish loading before one look
        v, e = decide.judge(chk["judge"], nodes(device), ctx["decisions"])
        if v is None:
            return False, f"judge unsure ({e.get('reason')}, p_true={e.get('p_true')}), agent takes over"
        return v, f"judge {v} p_true={e.get('p_true')} {e['model']} {e['latency_ms']} ms"
    if "foreground" in chk:
        ns = nodes(device)
        pkg = ns[0].get("package") if ns else None
        return pkg == chk["foreground"], f"foreground {pkg}"
    if "no_crash" in chk:
        ev = call(device, "crash_events", after=ctx["crash_seq"], filter=chk["no_crash"])["events"]
        return not ev, f"{len(ev)} crash events" + (f": {ev[:2]}" if ev else "")
    if "logs_lack" in chk:
        lines = call(device, "logs", filter=chk.get("filter", ""), level=chk.get("level", "E"),
                     cursor=ctx["log_cursor"], max_lines=500)["lines"]
        bad = [l for l in lines if re.search(chk["logs_lack"], l)]
        return not bad, f"{len(bad)} matching log lines" + (f": {bad[0][:160]}" if bad else "")
    return False, f"unknown check {chk}"


APP = "com.boltbetz.staging"
# Sandbox test-account note (holds the test card); AE_SANDBOX_NOTE overrides.
NOTE = Path(os.environ.get("AE_SANDBOX_NOTE", "C:/dev/boltbetz-docs/70-ops/ops-sandbox-test-account.md"))


def secret(key):
    """Test card from the sandbox note, read at run time; never written to a case, result or log."""
    if key.startswith("card."):
        blk = NOTE.read_text(encoding="utf-8").split("## Deposit card", 1)[1].split("```")[1]
        mm, yy = re.search(r"Exp\s+(\d\d)/(\d\d)", blk).groups()
        return {"pan": re.search(r"\b(\d{4} ?\d{4} ?\d{4} ?\d{4})\b", blk).group(1).replace(" ", ""),
                "cvv": re.search(r"CVV\s+(\d{3,4})", blk).group(1), "exp": mm + yy,
                "name": [l for l in blk.strip().splitlines() if l.strip()][-1].strip()}[key[5:]]
    raise KeyError(key)


def money(t):
    m = re.search(r"-?\$?\s*(-?[\d,]+(?:\.\d+)?)", t or "")
    return float(m.group(1).replace(",", "")) if m else None


def read_value(device, src):
    if "ui" in src:
        n = find(nodes(device), src["ui"])
        return money(n.get("text") or n.get("content-desc")) if n else None
    import emu
    v = emu.req("GET", src["emu"])
    for k in src["field"].split("."):
        v = v[int(k)] if isinstance(v, list) else v[k]
    return len(v) if isinstance(v, list) else v / src.get("scale", 1)


def lane_setup(device, lane, camera_off=None):
    """Once per lane, before its first case. A phone can report ready=true while the daemon is still in phase
    "browser" (Firefox first run relaunches Firefox over the app for ~2-4 min on a keep_data boot): wait for
    phase "ready". Grant POST_NOTIFICATIONS so the Android 13+ prompt cannot cover the app, then put the app in
    front and give a fresh boot up to 60 s to reach Home."""
    end = time.time() + 300
    while next(d for d in call(device, "status")["devices"] if d["id"] == device)["phase"] != "ready":
        if time.time() > end:
            raise RuntimeError(f"{device}: phase not ready after 300 s")
        time.sleep(5)
    call(device, "permission", pkg=APP, perm="android.permission.POST_NOTIFICATIONS", action="grant")
    if camera_off or (camera_off is None and lane == "L3"):  # scanner lane runs camera-off (a live camera got the app LMK-killed on 1 GB phones); user-fixed
        # deny, or the system camera prompt covers the scanner. A restart dropped USER_FIXED (2026-10-08).
        call(device, "permission", pkg=APP, perm="android.permission.CAMERA", action="revoke")
        call(device, "shell", cmd=f"pm set-permission-flags {APP} android.permission.CAMERA user-fixed")
    call(device, "app", launch=APP)
    wait_for(device, ["home-screen", "tab-home"], 60)


LANES = json.loads((HERE / "lanes.json").read_text(encoding="utf-8"))  # per-lane account data; "phone" = its Device


def for_lane(case, lane):
    """Resolve {lane.player}, {lane.machine}, {lane.card}, {lane.email}, {lane.id} from lanes.json for this lane."""
    data = {"id": lane, **LANES.get(lane, {})}

    def sub(m):
        if m.group(1) not in data:
            raise KeyError(f"{{lane.{m.group(1)}}}: lanes.json has no {m.group(1)} for {lane}")
        return str(data[m.group(1)])
    return json.loads(re.sub(r"\{lane\.(\w+)\}", sub, json.dumps(case)))


def run_case(device, case, shot_dir, lane=None, hard=False, say=True):
    """lane: whose account data fills {lane.x} (default the case's lane). hard: case timeout_s is a hard limit."""
    t0 = time.time()
    if hard:
        _tl.deadline = t0 + case.get("timeout_s", 120)
    ctx = {"vars": {}, "shots": [], "decisions": [], "crash_seq": call(device, "crash_events")["last"],
           "log_cursor": call(device, "logs", max_lines=0)["cursor"]}
    res = {"id": case["id"], "lane": lane or case.get("lane"), "device": device, "checks": [], "shots": ctx["shots"]}
    try:
        case = for_lane(case, res["lane"])
        pre = case.get("preconditions", {})
        if pre.get("app_launched"):
            call(device, "app", launch=pre["app_launched"])
        for s in case.get("steps", []):
            if time.time() - t0 > case.get("timeout_s", 120):
                raise RuntimeError("timeout")
            if "check" in s:  # mid-case check: same forms as "checks", taken on the screen under test
                ok, detail = do_check(device, s["check"], ctx, shot_dir, case["id"])
                res["checks"].append({"check": s["check"], "ok": ok, "detail": detail})
            else:
                do_step(device, s, ctx)
        for c in case.get("checks", []):
            ok, detail = do_check(device, c, ctx, shot_dir, case["id"])
            res["checks"].append({"check": c, "ok": ok, "detail": detail})
        res["status"] = "PASS" if all(c["ok"] for c in res["checks"]) else "FAIL"
    except (Exception, SystemExit) as e:  # a step error is a FAIL, not a crash of the runner (SystemExit once killed a lane silently)
        res["status"], res["error"] = "FAIL", str(e) or type(e).__name__
    if hard:  # screenshot, logs and cleanup get their own 30 s past the case's limit
        _tl.deadline = time.time() + 30
    if res["status"] == "FAIL":  # what was on screen when it failed, and the app's error log lines
        try:
            do_check(device, {"screenshot": "FAIL"}, ctx, shot_dir, case["id"])
        except Exception:
            pass
        try:
            lines = call(device, "logs", filter=APP, level="E", cursor=ctx["log_cursor"], max_lines=200)["lines"]
            if lines:
                res["logs"] = [l[:200] for l in lines[-15:]]
        except Exception:
            pass
    for s in case.get("cleanup", []):  # always runs, so a failed case still hands the next one a clean screen
        try:
            do_step(device, s)
        except Exception as e:
            res.setdefault("cleanup_errors", []).append(str(e))
    if time.time() - t0 > case.get("timeout_s", 120):
        res["status"], res["error"] = "FAIL", res.get("error") or "timeout"
    if ctx["decisions"]:
        res["decisions"] = ctx["decisions"]
    res["wall_s"] = round(time.time() - t0, 1)
    _tl.deadline = None
    if say:
        print(f"{res['status']} {case['id']} on {device} {res['wall_s']}s {res.get('error', '')}", flush=True)
    return res


# ---------- matrix mode: a work queue over N phones ----------

FULL_CWA = 2_500_000  # $25.00 in millicents: every lane player starts each case with this


def pinned_lane(c):
    """The lane a case must run on, or None when any phone (any lane account) can run it."""
    return None if c.get("portable") or c.get("lane") in (None, "", "any") else c["lane"]


def matrix_cases(root, areas, shard, pick):
    """Every *.json under root. area = first subfolder; a file directly in root takes its lane as area."""
    root = Path(root) if Path(root).exists() else HERE / root
    cases = []
    for p in sorted(root.rglob("*.json")):
        rel = p.relative_to(root).parts
        c = json.loads(p.read_text(encoding="utf-8"))
        c["_area"] = rel[0] if len(rel) > 1 else c.get("lane") or "any"
        if (not areas or c["_area"] in areas) and (not pick or any(c["id"].startswith(k) for k in pick)):
            cases.append(c)
    cases.sort(key=lambda c: c["id"])
    if shard:  # i/n, 1-based: every n-th case, so shards stay balanced across areas
        i, n = map(int, shard.split("/"))
        cases = cases[i - 1::n]
    return cases


def wall_history():
    """case id -> newest wall_s seen in any earlier run (results folders, oldest first so newer wins)."""
    est = {}
    for f in sorted(Path(os.environ.get("AE_RUNS_DIR") or HERE / "results").glob("*/results.json")):
        try:
            for r in json.loads(f.read_text(encoding="utf-8")).get("results", []):
                if r.get("wall_s"):
                    est[r["id"]] = r["wall_s"]
        except Exception:
            pass
    return est


def reset(device, lane, emu_ok):
    """Known state before every case: lane's machine session ended, CWA back to $25, app cold-started, hooks on,
    faults cleared, on Home. Returns notes on what it had to fix."""
    notes = []
    L = LANES.get(lane, {})
    if emu_ok:
        import emu
        if L.get("machine"):
            try:
                emu.end_session(L["machine"])
            except RuntimeError:
                pass  # no open session
        if L.get("player"):
            cwa = emu.req("GET", f"/admin/api/players/{L['player']}").get("cwaAvailable")
            if cwa != FULL_CWA:
                emu.req("PUT", f"/admin/api/players/{L['player']}", {"cwaAvailable": FULL_CWA})
                notes.append(f"CWA {cwa} -> {FULL_CWA}")
    call(device, "shell", cmd=f"am force-stop {APP}")
    call(device, "app", launch=APP)
    hit, _ = wait_for(device, ["home-screen", "start-screen", "login-screen"], 45)
    if not hit or hit.get("resource-id") != "home-screen":
        raise RuntimeError(f"reset: app not on Home after launch ({hit.get('resource-id') if hit else 'nothing'}; signed out?)")
    for link in ("hooks?on=1", "fault?clear=1", "route/MainFlow/Tabs/Home"):
        call(device, "deep_link", uri="boltbetz-staging://e2e-session/" + link, package=APP)
    time.sleep(0.3)
    if not wait_for(device, ["home-screen"], 15)[0]:
        raise RuntimeError("reset: Home gone after the hook links")
    return notes


class Matrix:
    """Per-phone queues dealt longest-first (pinned cases keep id order on their lane's phone), work stealing
    when a queue runs dry, one retry of a failed portable case on another phone."""

    def __init__(self, phones, phone_lane, cases, est, out):
        self.phones, self.phone_lane, self.out, self.t0 = phones, phone_lane, out, time.time()
        self.est = lambda c: est.get(c["id"]) or c.get("timeout_s", 120) / 4
        self.cv = threading.Condition()
        self.q = {p: [] for p in phones}
        self.last = {p: [] for p in phones}  # "last": true cases (sign-out): run once nothing else is left
        self.retry, self.busy, self.retired, self.results = [], {}, set(), []
        self.util = {p: 0.0 for p in phones}
        self.total = len(cases)
        lane_phone = {l: p for p, l in phone_lane.items()}
        load = dict.fromkeys(phones, 0.0)
        for c in cases:
            lane = pinned_lane(c)
            if lane is None:
                continue
            p = lane_phone.get(lane)
            if p is None:
                self.results.append(self.stub(c, lane, "SKIP", f"no phone for {lane} in --phones"))
                continue
            (self.last if c.get("last") else self.q)[p].append(c)
            load[p] += self.est(c)
        for c in sorted((c for c in cases if pinned_lane(c) is None), key=self.est, reverse=True):
            p = min(phones, key=load.get)
            self.q[p].append(c)
            load[p] += self.est(c)

    @staticmethod
    def stub(c, lane, status, error, device=None):
        return {"id": c["id"], "area": c["_area"], "lane": lane, "device": device, "status": status, "error": error,
                "checks": [], "shots": []}

    def next(self, phone):
        with self.cv:
            while phone not in self.retired:
                job = None
                # a phone still holding a "last" case leaves retries to the others (it would serialize the tail),
                # unless no other phone could take them
                free = [p for p in self.phones if p not in self.retired and not self.last[p]]
                mine = [r for r in self.retry if r[2]["device"] != phone
                        and (not self.last[phone] or not [p for p in free if p != r[2]["device"]])]
                if mine:
                    job = max(mine, key=lambda r: self.est(r[0]))
                    self.retry.remove(job)
                elif self.q[phone]:
                    job = (self.q[phone].pop(0), 1, None)
                elif self.last[phone]:
                    job = (self.last[phone].pop(0), 1, None)
                    if not self.last[phone]:
                        self.retired.add(phone)  # e.g. signed out now: takes no more work, no retries
                else:
                    victims = [p for p in self.phones if p != phone and any(pinned_lane(c) is None for c in self.q[p])]
                    if victims:
                        v = max(victims, key=lambda p: sum(map(self.est, self.q[p])))
                        c = max((c for c in self.q[v] if pinned_lane(c) is None), key=self.est)
                        self.q[v].remove(c)
                        job = (c, 1, None)
                if job:
                    self.busy[phone] = job[0]["id"]
                    return job
                if not self.busy:
                    return None
                self.cv.wait()

    def retire(self, phone, why):
        """Phone unusable: its pinned and last cases FAIL, its portable ones stay in its queue to be stolen."""
        with self.cv:
            self.retired.add(phone)
            for c in [c for c in self.q[phone] if pinned_lane(c)] + self.last[phone]:
                self.q[phone] = [x for x in self.q[phone] if x is not c]
                self.final(self.stub(c, self.phone_lane[phone], "FAIL", why, phone))
            self.last[phone] = []
            self.cv.notify_all()

    def done(self, phone, job, res):
        with self.cv:
            self.busy.pop(phone, None)
            c, attempt, first = job
            others = [p for p in self.phones if p != phone and p not in self.retired]
            if res["status"] == "FAIL" and attempt == 1 and pinned_lane(c) is None and others:
                self.retry.append((c, 2, res))
                print(f"RETRY {c['id']} failed on {phone} ({res.get('error', 'checks failed')[:100]}); retrying on another phone", flush=True)
            else:
                if first:
                    res["first_attempt"] = {k: first.get(k) for k in ("device", "error", "wall_s", "shots", "logs")}
                    res["flaky"] = res["status"] == "PASS"
                self.final(res)
            self.cv.notify_all()

    def final(self, res):  # caller holds the lock
        self.results.append(res)
        tag = "FLAKY" if res.get("flaky") else res["status"]
        el = round(time.time() - self.t0)
        print(f"[{len(self.results)}/{self.total}] {tag} {res['id']} on {res.get('device')} {round(res.get('wall_s') or 0, 1)}s"
              f"{' reset ' + str(res['reset_s']) + 's' if 'reset_s' in res else ''} | {el}s elapsed"
              f"{' | ' + res['error'][:120] if res.get('error') else ''}", flush=True)
        self.save()

    def save(self, finished=False):  # caller holds the lock (or threads are done)
        wall = round(time.time() - self.t0, 1)
        doc = {"mode": "matrix", "running": not finished, "deal": {self.phone_lane[p]: p for p in self.phones},
               "total_wall_s": wall, "progress": {"done": len(self.results), "total": self.total, "busy": dict(self.busy)},
               "utilization": {p: {"lane": self.phone_lane[p], "busy_s": round(self.util[p], 1),
                                   "pct": round(100 * self.util[p] / wall) if wall else 0} for p in self.phones},
               "flaky": [r["id"] for r in self.results if r.get("flaky")],
               "results": sorted(self.results, key=lambda r: r["id"])}
        tmp = self.out / "results.json.tmp"
        tmp.write_text(json.dumps(doc, indent=2))
        for _ in range(20):  # the dashboard may be reading it
            try:
                return os.replace(tmp, self.out / "results.json")
            except PermissionError:
                time.sleep(0.05)


def matrix_main(phones, root, areas, shard, pick):
    phone_lane = {v["phone"]: k for k, v in LANES.items() if "phone" in v}
    if missing := [p for p in phones if p not in phone_lane]:
        print(f"{','.join(missing)}: no lane in lanes.json (add {{\"phone\": ...}} to a lane)", flush=True)
        return 2
    cases = matrix_cases(root, areas, shard, pick)
    if not cases:
        print(f"no cases under {root}" + (f" for areas {sorted(areas)}" if areas else ""), flush=True)
        return 2
    out = Path(os.environ.get("AE_RUNS_DIR") or HERE / "results") / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True)
    emu_ok = True
    try:
        import emu
        emu._key()  # resolve the emulator key once, before the threads
    except Exception as e:
        emu_ok = False
        print(f"WARNING: Synkros emulator unreachable ({e}); resets skip the session end and $25 restore", flush=True)
    m = Matrix(phones, {p: phone_lane[p] for p in phones}, cases, wall_history(), out)
    with m.cv:
        m.save()
    print(f"matrix: {len(cases)} cases on {len(phones)} phones -> {out}", flush=True)

    def worker(phone):
        lane = phone_lane[phone]
        try:
            lane_setup(phone, lane, camera_off=True)  # any phone may get a scanner case: camera off on all
        except Exception as e:
            print(f"{phone} setup: {e}", flush=True)
            return m.retire(phone, f"setup: {e}")
        bad_resets = 0
        while job := m.next(phone):
            c, t = job[0], time.time()
            try:
                notes = reset(phone, lane, emu_ok)
                rs = round(time.time() - t, 1)
                res = run_case(phone, c, out, lane=lane, hard=True, say=False)
                res["reset_s"] = rs
                if notes:
                    res["reset_fixed"] = notes
                bad_resets = 0
            except Exception as e:
                res = Matrix.stub(c, lane, "FAIL", str(e), phone)
                res["wall_s"] = round(time.time() - t, 1)
                bad_resets += 1
            res["area"], res["attempt"] = c["_area"], job[1]
            m.util[phone] += time.time() - t
            m.done(phone, job, res)
            if bad_resets >= 3:
                print(f"{phone}: 3 resets failed in a row, phone retired from this run", flush=True)
                m.retire(phone, "phone retired: 3 resets failed in a row")
                break

    ts = [threading.Thread(target=worker, args=(p,)) for p in phones]
    for t in ts: t.start()
    for t in ts: t.join()
    with m.cv:  # anything never run (retry with no phone left, queue of a dead phone) is a FAIL, never a gap
        for c, _, first in m.retry:
            first.setdefault("error", "failed; no other phone left to retry on")
            m.final(first)
        m.retry = []
        for p in phones:
            for c in m.q[p] + m.last[p]:
                m.final(Matrix.stub(c, phone_lane[p], "FAIL", "never ran (no phone left)"))
            m.q[p], m.last[p] = [], []
        m.save(finished=True)
    return matrix_report(m, out)


def matrix_report(m, out):
    rs = sorted(m.results, key=lambda r: r["id"])
    outcome = lambda r: "FLAKY" if r.get("flaky") else r["status"]
    kinds = ["PASS", "FLAKY", "FAIL", "SKIP"]
    areas = sorted({r.get("area", "?") for r in rs})
    wall = round(time.time() - m.t0, 1)
    g = ["| area | " + " | ".join(kinds) + " | total |", "|---" * (len(kinds) + 2) + "|"]
    for a in areas + ["all"]:
        sel = [r for r in rs if a == "all" or r.get("area") == a]
        g.append(f"| {a} | " + " | ".join(str(sum(outcome(r) == k for r in sel)) for k in kinds) + f" | {len(sel)} |")
    g += [f"\nTotal wall time: {wall} s; {len(rs)} cases on {len(m.phones)} phones",
          "\n| phone | lane | busy s | utilization |", "|---|---|---|---|"]
    g += [f"| {p} | {m.phone_lane[p]} | {round(m.util[p], 1)} | {round(100 * m.util[p] / wall) if wall else 0}% |" for p in m.phones]
    if flaky := [r for r in rs if r.get("flaky")]:
        g.append("\n## Flaky (failed once, passed on retry)")
        g += [f"- {r['id']}: failed on {r['first_attempt']['device']} ({(r['first_attempt'].get('error') or 'checks failed')[:120]}), "
              f"passed on {r['device']}" for r in flaky]
    if fails := [r for r in rs if r["status"] == "FAIL"]:
        g.append("\n## Failures")
        for r in fails:
            bad = [c["detail"] for c in r.get("checks", []) if not c["ok"]]
            g.append(f"- **{r['id']}** on {r.get('device')}: {(r.get('error') or '; '.join(map(str, bad)) or '?')[:200]}")
            shot = next((s for s in r.get("shots", []) if s.endswith("-FAIL.png")), None)
            if shot:
                g.append(f"  - screenshot: {Path(shot).name}")
            for l in r.get("logs", [])[-5:]:
                g.append(f"  - log: `{l[:160]}`")
    (out / "grid.md").write_text("\n".join(g) + "\n", encoding="utf-8")
    print("\n".join(g), f"\n{out}", sep="")
    return 0 if all(r["status"] != "FAIL" for r in rs) else 1


def main(argv):
    phones, only, pick, cases_dir = ["d0"], None, None, HERE / "cases"
    matrix, areas, shard = None, None, None
    it = iter(argv)
    for a in it:
        if a == "--phones": phones = next(it).split(",")
        elif a == "--lanes": only = set(next(it).split(","))
        elif a == "--case": pick = next(it).split(",")
        elif a == "--matrix": matrix = next(it)
        elif a == "--areas": areas = set(next(it).split(","))
        elif a == "--shard": shard = next(it)
        else: cases_dir = Path(a)
    if matrix:
        return matrix_main(phones, matrix, areas, shard, pick)
    cases = sorted((json.loads(p.read_text()) for p in cases_dir.glob("*.json")), key=lambda c: c["id"])
    if pick:
        cases = [c for c in cases if any(c["id"].startswith(k) for k in pick)]
    lanes = sorted({c["lane"] for c in cases if not only or c["lane"] in only})
    deal = dict(zip(lanes, phones))
    out = Path(os.environ.get("AE_RUNS_DIR") or HERE / "results") / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True)
    results, t0 = [], time.time()

    def lane_worker(lane):
        if lane in deal:
            try:
                lane_setup(deal[lane], lane)
            except Exception as e:
                print(f"{lane} setup on {deal[lane]}: {e}", flush=True)
        for c in (c for c in cases if c["lane"] == lane):
            results.append(run_case(deal[lane], c, out) if lane in deal else
                           {"id": c["id"], "lane": lane, "status": "SKIP", "error": "no phone"})

    ts = [threading.Thread(target=lane_worker, args=(l,)) for l in lanes]
    for t in ts: t.start()
    for t in ts: t.join()
    done = {r["id"] for r in results}  # a case that never reported is a FAIL, never a silent gap
    results += [{"id": c["id"], "lane": c["lane"], "status": "FAIL", "error": "lane thread died before this case"}
                for c in cases if c["lane"] in lanes and c["id"] not in done]
    results.sort(key=lambda r: r["id"])
    total = round(time.time() - t0, 1)
    (out / "results.json").write_text(json.dumps({"deal": deal, "total_wall_s": total, "results": results}, indent=2))
    grid = ["| case | " + " | ".join(lanes) + " | wall s |", "|---" * (len(lanes) + 2) + "|"]
    for r in results:
        grid.append(f"| {r['id']} | " + " | ".join(r["status"] if r["lane"] == l else "" for l in lanes)
                    + f" | {r.get('wall_s', '')} |")
    grid.append(f"\nTotal wall time: {total} s; deal: {deal}")
    (out / "grid.md").write_text("\n".join(grid) + "\n")
    print("\n".join(grid), f"\n{out}", sep="")
    return 0 if all(r["status"] != "FAIL" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
