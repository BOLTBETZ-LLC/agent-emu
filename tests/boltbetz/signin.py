"""Sign a phone in to a lane account.

Default, no Gmail: the sidecar QA session route mints tokens for the lane's password QA account and the staging app
takes them from an e2e-session deep link at cold start.

  python signin.py session d0 L1        # lane email from the emulator lane registry; ends on home-screen or fails

Needs a provisioned (password) QA account in the lane registry that has finished sign-up (backend account). An
emailed-code account answers 409 not_password_account; one without a backend account lands on onboarding-screen
(exit 1). Lanes L1-L4 are emailed-code accounts today, so they still sign in with the fallback below. QA token: env EXT_QA_TOKEN, else asm-exec resolves the staging
ext-admin-token (the token the sidecar accepts). Tokens and the link go straight to the phone; nothing is printed.

Fallback, emailed code (Gmail), in two halves so the code can be read from Gmail in between:

  python signin.py send d0 aaron+lane1@boltbetz.com   # Login -> email -> Send code, prints the screen ids after
  python signin.py code d0 <code>                      # types the code on the EnterCode screen, prints ids after
"""
import json, os, subprocess, sys, time, urllib.error, urllib.request
import emu
from run import call, nodes, wait_for, do_step

APP = "com.boltbetz.staging"
QA_BASE = os.environ.get("EXT_QA_URL", "https://ext-staging.bbapp01.com/api/ext/qa")
QA_SECRET = "{{resolve:secretsmanager:boltbetz/v2/staging/ext-admin-token}}"


def ids(dev):
    return sorted({n.get("resource-id") for n in nodes(dev) if n.get("resource-id")})


def qa_token():
    t = os.environ.get("EXT_QA_TOKEN", "")
    if t:
        return t
    if not os.path.exists(emu.ASM_EXEC):
        raise RuntimeError("no sidecar QA token: enter it in the agent-emu app (Setup > Secrets) or set EXT_QA_TOKEN")
    # Same asm-exec child as emu._key(): the value comes back over a pipe, never printed.
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", emu.ASM_EXEC,
                        sys.executable, os.path.abspath(emu.__file__), "_echo", QA_SECRET],
                       capture_output=True, text=True, timeout=90)
    t = r.stdout.strip()
    if r.returncode or not t:
        raise RuntimeError(f"asm-exec exit {r.returncode}: {r.stderr.strip()[-200:]}")
    if t.startswith("{"):
        t = next(iter(json.loads(t).values()))
    return t


def qa(method, path, tok, body=None):
    req = urllib.request.Request(QA_BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"x-qa-token": tok, "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        b = json.loads(e.read() or b"{}")
        raise RuntimeError(f"{method} {path}: {e.code} {b.get('reason') or b.get('title')}")


def lane_email(lane):
    try:
        return emu.req("GET", "/admin/api/lanes")[lane]["email"]
    except (RuntimeError, KeyError):
        return json.load(open(os.path.join(os.path.dirname(__file__), "lanes.json")))[lane]["email"]


def session(dev, lane):
    email = lane_email(lane).lower()
    tok = qa_token()
    acct = next((a for a in qa("GET", "/accounts", tok) if a.get("email") == email and not a.get("deletedAt")), None)
    if not acct:
        raise RuntimeError(f"{lane}: {email} is not a sidecar QA account (run lane_state {lane} fresh)")
    link = qa("POST", f"/accounts/{acct['accountId']}/session", tok, {})["link"]
    call(dev, "shell", cmd=f"am force-stop {APP}")  # the app reads the session link only at cold start
    call(dev, "deep_link", uri=link, package=APP)
    hit, _ = wait_for(dev, ["home-screen", "onboarding-screen", "start-screen", "login-screen"], 45)
    where = hit.get("resource-id") if hit else "nothing"
    print("signed in" if where == "home-screen" else "NOT on home", lane, email, "on", dev, "->", where)
    return where == "home-screen"


if __name__ == "__main__":
    mode, dev, val = sys.argv[1:4]
    if mode == "session":
        sys.exit(0 if session(dev, val) else 1)
    elif mode == "send":
        for s in [{"tap_id": "start-login", "optional": True, "wait_s": 3}, {"wait_id": "login-screen"},
                  {"tap_id": "login-email-toggle", "optional": True, "wait_s": 2},
                  {"tap_id": "login-identifier-input"},
                  {"call": "shell", "cmd": "input keyevent KEYCODE_MOVE_END " + "67 " * 80},  # clear leftover text
                  {"call": "type_text", "text": val, "screenshot": False},
                  {"sleep_ms": 800}, {"tap_id": "login-send-code"}, {"sleep_ms": 4000}]:
            do_step(dev, s)
        print("sent at", time.strftime("%H:%M:%S"), ids(dev))
    else:
        call(dev, "type_text", text=val, screenshot=False)
        time.sleep(6)
        print(ids(dev))
