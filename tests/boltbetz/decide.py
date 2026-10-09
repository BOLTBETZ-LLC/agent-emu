"""Model-backed decisions for the runner: pick the next tap for a goal, or judge a yes/no claim about the screen.

next_tap(goal, ns, log) -> (node | "scroll_down" | None, entry)   None = unsure: the case fails, an agent takes over
judge(claim, ns, log)   -> (True | False | None, entry)

Model: Jev (TypeSafe System One, hosted), one typed `choice` for a tap, one `noul` for a judge.
Key: env TYPESAFE_API_KEY, else the Windows USER var of that name. Never logged. No key, an error, or confidence
below the bar -> unsure.
Confidence bar AE_DECIDE_MIN_CONF (default 0.5). Tap confidence = the chosen option's; judge confidence = |2p - 1|
(0.5 -> p between 0.25 and 0.75 is unsure). Every decision is appended to `log` (it lands in results.json).
Measured 2026-10-09 (.scratch/jev-exp/results.md): Jev 29/30 taps, 28/30 judge, ~0.18 s, ~$0.00004 per decision.
"""
import json, os, subprocess, time, urllib.request
import aekeys  # noqa: F401  keys the installed app stored

JEV_URL = "https://api.typesafe.ai/v1/systemone"
MIN_CONF = float(os.environ.get("AE_DECIDE_MIN_CONF", "0.5"))
JEV_PRICE_IN = 0.042 / 1e6  # $ per input token; output is free
APP = "BoltBetz casino wallet app (Android phone)"
_key = []


def jev_key():
    if not _key:
        k = os.environ.get("TYPESAFE_API_KEY") or subprocess.run(
            ["powershell", "-NoProfile", "-Command", "[Environment]::GetEnvironmentVariable('TYPESAFE_API_KEY','User')"],
            capture_output=True, text=True).stdout.strip()
        _key.append(k)
    return _key[0]


def label(n, ns):
    return n.get("text") or next((m["text"] for m in ns if m.get("text") and inside(m, n)), "")


def inside(m, n):
    a, b = m["b"], n["b"]
    return a[0] >= b[0] and a[1] >= b[1] and a[2] <= b[2] and a[3] <= b[3]


def candidates(ns):
    """option key -> (node, description). Keys are testIDs; blank or repeated ids get el<i>."""
    h = max((n["b"][3] for n in ns), default=1) or 1
    out = {}
    for i, n in enumerate(ns):
        rid = n.get("resource-id", "")
        if n.get("clickable") != "true" or rid.startswith("qa-force"):  # QA debug overlay buttons are noise
            continue
        desc, text = n.get("content-desc", ""), label(n, ns)
        words = f"{desc} / {text}" if desc and text and desc != text else desc or text or "(no label)"
        y = n["center"][1] / h
        where = "bottom tab bar" if y > 0.92 else "top of screen" if y < 0.14 else "middle of screen"
        out[rid if rid and rid not in out else f"el{i}"] = (n, f"button '{words}', {where}")
    return out


def screen_text(ns):
    """Compact screen for the judge: every node that carries an id, text or description."""
    lines = []
    for n in ns:
        rid, text, desc = n.get("resource-id", ""), n.get("text", ""), n.get("content-desc", "")
        if rid.startswith(("qa-force", "android", "action_bar")) or not (rid or text or desc):
            continue
        cls = n.get("class", "").split(".")[-1]
        lines.append(" ".join([cls] + [f'{k}="{v}"' for k, v in (("id", rid), ("text", text), ("desc", desc)) if v]))
    return "\n".join(lines)


def ask(url, model, key, body):
    req = urllib.request.Request(url, data=json.dumps({"model": model, **body}).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    return json.load(urllib.request.urlopen(req, timeout=30))


def decide(body, read, entry, log):
    """Ask Jev; read(answers) -> (value, confidence). Returns the value at or above MIN_CONF, else None (unsure)."""
    entry.update(value=None, model="jev-latest")
    t = time.time()
    try:
        if not jev_key():
            raise RuntimeError("TYPESAFE_API_KEY not set")
        r = ask(JEV_URL, "jev-latest", jev_key(), body)
        value, conf = read(r["answers"])
        entry.update(choice=value, confidence=round(conf, 3),
                     cost_usd=round(((r.get("usage") or {}).get("input_tokens") or 0) * JEV_PRICE_IN, 7))
        if conf >= MIN_CONF:
            entry["value"] = value
        else:
            entry["reason"] = f"confidence {conf:.2f} < {MIN_CONF}"
    except Exception as e:
        entry["reason"] = str(e)[:120]
    entry["latency_ms"] = round((time.time() - t) * 1000)
    log.append(entry)
    return entry["value"]


def next_tap(goal, ns, log, scroll=True):
    opts = candidates(ns)
    crit = {k: d for k, (_, d) in opts.items()}
    if scroll:
        crit["scroll_down"] = "swipe up to scroll the screen and reveal options further down"
    body = {"state": {"app": APP, "goal": goal, "tappable_elements": [f"{k}: {v}" for k, v in crit.items()]},
            "questions": {"next_tap": {"type": "choice", "criteria": crit,
                                       "instructions": "Which element should the tester tap next to make progress on `goal`?"}}}
    entry = {"kind": "tap", "goal": goal, "candidates": len(crit)}

    def read(a):
        return a["next_tap"]["choice"], a["next_tap"].get("confidence") or 0

    k = decide(body, read, entry, log)
    return ("scroll_down" if k == "scroll_down" else opts[k][0] if k in opts else None), entry


def judge(claim, ns, log):
    body = {"state": {"app": APP, "screen_elements": screen_text(ns)},
            "questions": {"verdict": {"type": "noul", "instructions": f"Is this true of the current screen: {claim}",
                                      "criteria": {"true": "The screen elements show this.",
                                                   "false": "The screen elements do not show this."}}}}
    entry = {"kind": "judge", "goal": claim, "candidates": 2}

    def read(a):
        p = a["verdict"]["noul"]
        entry["p_true"] = round(p, 3)
        return p >= 0.5, abs(2 * p - 1)

    return decide(body, read, entry, log), entry
