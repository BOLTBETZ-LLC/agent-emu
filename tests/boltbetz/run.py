"""Minimal lane runner for agent-emud. Stdlib only.

python run.py [--phones d0,d1,d2,d3] [--lanes L1,L2] [cases_dir]
python run.py --phones d1,d2,d3 --lanes L2,L3,L4      # L2->d1, L3->d2, L4->d3, in parallel
python run.py --phones d3 --case L4-01                # one case (id prefix; comma list ok) on d3

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


def call(device, call_name, **args):
    body = json.dumps({"call": call_name, "device": device, **args}).encode()
    req = urllib.request.Request(API + "/api", data=body, headers={"Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=180))
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


def lane_setup(device, lane):
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
    if lane == "L3":  # scanner lane runs camera-off (a live camera got the app LMK-killed on 1 GB phones); user-fixed
        # deny, or the system camera prompt covers the scanner. A restart dropped USER_FIXED (2026-10-08).
        call(device, "permission", pkg=APP, perm="android.permission.CAMERA", action="revoke")
        call(device, "shell", cmd=f"pm set-permission-flags {APP} android.permission.CAMERA user-fixed")
    call(device, "app", launch=APP)
    wait_for(device, ["home-screen", "tab-home"], 60)


def run_case(device, case, shot_dir):
    t0 = time.time()
    ctx = {"vars": {}, "shots": [], "decisions": [], "crash_seq": call(device, "crash_events")["last"],
           "log_cursor": call(device, "logs", max_lines=0)["cursor"]}
    res = {"id": case["id"], "lane": case["lane"], "device": device, "checks": [], "shots": ctx["shots"]}
    try:
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
        res["status"], res["error"] = "FAIL", str(e)
    if res["status"] == "FAIL":  # what was on screen when it failed
        try:
            do_check(device, {"screenshot": "FAIL"}, ctx, shot_dir, case["id"])
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
    print(f"{res['status']} {case['id']} on {device} {res['wall_s']}s {res.get('error', '')}", flush=True)
    return res


def main(argv):
    phones, only, pick, cases_dir = ["d0"], None, None, HERE / "cases"
    it = iter(argv)
    for a in it:
        if a == "--phones": phones = next(it).split(",")
        elif a == "--lanes": only = set(next(it).split(","))
        elif a == "--case": pick = next(it).split(",")
        else: cases_dir = Path(a)
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
