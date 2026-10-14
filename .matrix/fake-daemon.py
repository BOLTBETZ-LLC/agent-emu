"""Stateful fake of agent-emud's dashboard port (7401), for the ui-* checks.

Serves the REAL dashboard file (daemon/agent-emud/src/ui.html) at / and answers the
calls the page makes. State is controlled through POST /__fake and every call the page
sends is recorded at GET /__calls. Stdlib only.

    python fake-daemon.py <port>

Control:
  GET  /__fake            current state
  POST /__fake            {"reset": true}  -> defaults, clears calls
                          {"set": {...}}   -> shallow merge into state
                          {"devices": {"d0": {...}}} -> merge per device (phase, ready, health, claim, ...)
                          {"drop": ["d0"]} -> remove devices
  GET  /__calls           every POST /api body received since the last reset

Fail injection: state["fail"] = {"start": "<error>", "sleep": "...", ...} makes that call
fail (job failed or {ok:false}) until cleared. "offline": true makes /api and /events 503.
Start/wake jobs step spawning -> booting -> android -> setup -> ready, state["step_s"] each.
"""
import json
import os
import sys
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

UI = r"C:\dev\agent-emu\daemon\agent-emud\src\ui.html"
PHASES = ["spawning", "booting", "android", "setup", "ready"]

LOCK = threading.Lock()
DEFAULTS = {
    "available_mb": 9000,
    "max_phones": 4,
    "offline": False,
    "step_s": 0.4,
    "fail": {},
    "devices": {},          # id -> {phase, ready, image_name, screen, mem, cpus, net, keep_data, uptime_s, health, claim, ui_reader, own_mb, shared_mb, activity, scanout_fps}
    "phase_since": {},      # id -> epoch ms when the current phase began (internal)
    "bugs": [],
    "runs": [],
    "run_detail": {},       # stamp -> results.json body
}
STATE = json.loads(json.dumps(DEFAULTS))
CALLS = []
JOB_N = [0]
SUBS = []               # one queue of SSE payloads per open /events connection
EV_LOCK = threading.Condition()


def now_ms():
    return int(time.time() * 1000)


def dev_view(i, d):
    v = {"id": i, "ready": d.get("phase") == "ready", "phase": d.get("phase"),
         "phase_since_ms": STATE["phase_since"].get(i, now_ms()),
         "image_name": d.get("image_name", "slim5"),
         "screen": d.get("screen", {"name": "samsung-a16", "width": 656, "height": 1424}),
         "mem": d.get("mem", 768), "cpus": d.get("cpus", 2), "net": d.get("net", True),
         "keep_data": d.get("keep_data", True), "uptime_s": d.get("uptime_s", 60),
         "ui_reader": d.get("ui_reader", "daemon")}
    for k in ("health", "claim"):
        if k in d:
            v[k] = d[k]
    return v


def status_obj():
    devs = [dev_view(i, d) for i, d in sorted(STATE["devices"].items())]
    return {"ok": True, "available_mb": STATE["available_mb"], "max_phones": STATE["max_phones"], "devices": devs}


def set_phase(i, phase):
    d = STATE["devices"].setdefault(i, {})
    d["phase"] = phase
    STATE["phase_since"][i] = now_ms()


def push_event(ev):
    # Every open /events connection gets its own copy, so a stale connection from a closed
    # page can never swallow a job result meant for the live one.
    with EV_LOCK:
        for q in SUBS:
            q.append(ev)
        EV_LOCK.notify_all()


GEN = [0]  # bumped by reset: a job left running by an earlier check must not touch the new state


def run_job(job, call, dev, after_ready=None):
    """Background job: moves the phone through boot phases, then reports the job result."""
    g = GEN[0]
    err = STATE["fail"].get(call)
    if err is None and call == "wake":
        err = STATE["fail"].get("start")
    if err is None and call in ("start", "wake") and STATE["available_mb"] < 4000:
        err = "Available %d MB < 4000 MB needed to boot" % STATE["available_mb"]
    if err:
        if call == "start":
            STATE["devices"].pop(dev, None)  # a failed boot leaves the phone off
        elif call == "wake":
            set_phase(dev, "sleeping")  # a failed wake leaves it asleep
        push_event({"type": "job", "job": job, "state": "failed", "error": err})
        return
    if call == "start":
        STATE["devices"][dev] = {}
        for ph in PHASES[:]:  # spawning first, then boot phases
            if GEN[0] != g:
                return
            set_phase(dev, ph)
            if ph != "ready":
                time.sleep(STATE["step_s"])
    elif call == "wake":
        for ph in PHASES:
            if GEN[0] != g:
                return
            set_phase(dev, ph)
            if ph != "ready":
                time.sleep(STATE["step_s"])
    elif call == "stop":
        STATE["devices"].pop(dev, None)
        STATE["phase_since"].pop(dev, None)
    elif call == "sleep":
        set_phase(dev, "sleeping")
    push_event({"type": "job", "job": job, "state": "done", "result": {"ok": True, "ready_s": STATE["step_s"] * 4}})


def job_call(call, body):
    dev = body.get("device")
    if dev is None:
        return {"ok": False, "error": "no device"}
    if call in ("start", "wake") and dev in STATE["devices"] and call == "start" and STATE["devices"][dev].get("phase") not in (None, "stopped"):
        return {"ok": False, "error": "%s is already running" % dev}
    if call == "start" and STATE["fail"].get("start") and STATE["fail"]["start"].startswith("disk_mismatch"):
        pass
    if call == "start" and sum(1 for d in STATE["devices"].values()) >= STATE["max_phones"]:
        return {"ok": False, "error": "at most %d phones run (AE_MAX_PHONES); stop one first" % STATE["max_phones"]}
    if call in ("sleep", "wake", "stop") and dev not in STATE["devices"]:
        return {"ok": False, "error": "no Device %s" % dev}
    if call == "sleep":
        cl = STATE["devices"].get(dev, {}).get("claim")
        if cl and str(cl.get("owner", "")).startswith("run.py"):
            return {"ok": False, "error": "%s is claimed by %s: a test run is using it" % (dev, cl["owner"])}
    if call == "wake" and STATE["devices"].get(dev, {}).get("phase") != "sleeping":
        return {"ok": False, "error": "%s is not asleep" % dev}
    if body.get("async"):
        JOB_N[0] += 1
        job = "j%d" % JOB_N[0]
        threading.Thread(target=run_job, args=(job, call, dev), daemon=True).start()
        return {"ok": True, "job": job}
    # Synchronous path (not used by the page for these calls, kept for completeness).
    run_job("sync", call, dev)
    return {"ok": True}


def handle_call(body):
    c = body.get("call")
    if STATE["offline"]:
        return None
    if c == "status":
        return status_obj()
    if c in ("start", "wake", "stop", "sleep"):
        return job_call(c, body)
    if c in ("sleep_many", "wake_many"):
        out = []
        for dev in body.get("devices", []):
            out.append(job_call("sleep" if c == "sleep_many" else "wake", {"device": dev, "async": True}))
        return {"ok": True, "results": out}
    if c == "health":
        return {"ok": True, "health": {"level": "ok"}}
    if c == "default_app":
        return {"ok": True, "default_app": None}
    if c == "bugs":
        return {"ok": True, "bugs": STATE["bugs"], "total": len(STATE["bugs"])}
    if c == "crash_events":
        return {"ok": True, "events": [], "last": 0}
    if c == "logs":
        return {"ok": True, "lines": [], "cursor": 0}
    if c == "issues":
        return {"ok": True, "issues": []}
    if c == "memory":
        return {"ok": True, "procs": []}
    if c == "claim" or c == "unclaim":
        return {"ok": True}
    if c == "ui_tree":
        return {"ok": True, "xml": "<hierarchy/>"}
    if c in ("app", "install_bundled", "deep_link", "tap", "swipe", "key", "type_text", "clock", "permission", "shell", "set_location", "lease", "release", "squeeze", "snapshot", "restore", "screenshot"):
        return {"ok": True}
    return {"ok": False, "error": "unknown call: %s" % c}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ct="application/json", extra=None):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(b)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj))

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n).decode() if n else ""
        return raw

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/" or p == "/index.html":
            with open(UI, "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif p == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
        elif p == "/health":
            self._send(200, "ok", "text/plain")
        elif p == "/events":
            if STATE["offline"]:
                return self._json(503, {"ok": False, "error": "daemon not reachable"})
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            last = None
            mine = []
            with EV_LOCK:
                SUBS.append(mine)
            try:
                while True:
                    with EV_LOCK:
                        pending = list(mine)
                        mine.clear()
                    for ev in pending:
                        self.wfile.write(("data: " + json.dumps(ev) + "\n\n").encode())
                    cur = json.dumps(status_obj(), sort_keys=True)
                    if cur != last:
                        last = cur
                        snap = status_obj()
                        snap["type"] = "snapshot"
                        self.wfile.write(("data: " + json.dumps(snap) + "\n\n").encode())
                    m = {"type": "metrics", "available_mb": STATE["available_mb"], "devices": []}
                    for i, d in sorted(STATE["devices"].items()):
                        m["devices"].append({"id": i, "ready": d.get("phase") == "ready", "phase": d.get("phase"),
                                             "uptime_s": d.get("uptime_s", 60), "own_mb": d.get("own_mb", 620),
                                             "shared_mb": d.get("shared_mb", 180), "activity": d.get("activity", "busy"),
                                             "scanout_fps": d.get("scanout_fps", 30), "memory_mode": d.get("memory_mode", "normal")})
                    self.wfile.write(("data: " + json.dumps(m) + "\n\n").encode())
                    self.wfile.flush()
                    with EV_LOCK:
                        EV_LOCK.wait(1.0)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return
            finally:
                with EV_LOCK:
                    SUBS.remove(mine)
        elif p in ("/mux", "/frames"):
            self._send(204, b"", "application/octet-stream")
        elif p == "/screenshot.png":
            self._send(204, b"", "image/png")
        elif p == "/runs":
            self._json(200, {"ok": True, "runs": STATE["runs"]})
        elif p.startswith("/runs/"):
            stamp = p.split("/")[2]
            body = STATE["run_detail"].get(stamp)
            if body is None:
                return self._json(404, {"ok": False})
            self._json(200, body)
        elif p == "/bugs":
            self._json(200, {"ok": True, "bugs": STATE["bugs"], "total": len(STATE["bugs"])})
        elif p == "/__fake":
            self._json(200, STATE)
        elif p == "/__calls":
            self._json(200, CALLS)
        else:
            self._json(404, {"ok": False, "error": "no route"})

    def do_POST(self):
        p = self.path.split("?")[0]
        raw = self._body()
        if p == "/__fake":
            req = json.loads(raw or "{}")
            with LOCK:
                if req.get("reset"):
                    GEN[0] += 1
                    STATE.clear()
                    STATE.update(json.loads(json.dumps(DEFAULTS)))
                    del CALLS[:]
                for k, v in (req.get("set") or {}).items():
                    STATE[k] = v
                for i, patch in (req.get("devices") or {}).items():
                    d = STATE["devices"].setdefault(i, {})
                    d.update(patch)
                    if "phase_since_ago_ms" in d:
                        STATE["phase_since"][i] = now_ms() - int(d.pop("phase_since_ago_ms"))
                    elif "phase" in patch:
                        STATE["phase_since"][i] = now_ms()
                for i in req.get("drop") or []:
                    STATE["devices"].pop(i, None)
            return self._json(200, {"ok": True})
        if p == "/api":
            try:
                body = json.loads(raw or "{}")
            except ValueError:
                return self._json(200, {"ok": False, "error": "bad json"})
            with LOCK:
                CALLS.append(body)
                if STATE["offline"]:
                    return self._json(503, {"ok": False, "error": "daemon not reachable"})
                out = handle_call(body)
            return self._json(200, out)
        if p == "/bugs":
            return self._json(200, {"ok": True, "new": True, "count": 1})
        self._json(404, {"ok": False})


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 7499
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
