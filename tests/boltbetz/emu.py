"""Synkros emulator admin client. Stdlib only. The key comes from env SYNKROS_API_KEY or, when unset,
from asm-exec at ASM_EXEC (default C:/dev/dev-harness/tools/asm-exec.ps1) ({{resolve:secretsmanager:boltbetz/v2/sandbox/synkros-emulator-qa:SecretString:ApiKey}}). Never printed.

  python emu.py overview                 # machines + player count (no secrets)
  python emu.py seed L1 <pin>            # player "Lane1 QA" with a card, PIN, $25 CWA + machine EMU-L1; prints ids
  python emu.py player <id>              # player summary (cards, CWA)
  python emu.py feed EMU-L3              # current QR token text (single-use, not a secret)
  python emu.py end EMU-L3               # end the machine session
"""
import json, os, subprocess, sys, urllib.request, urllib.error

BASE = os.environ.get("SYNKROS_URL", "https://synkros-emu.bbapp01.com")


ASM_EXEC = os.environ.get("ASM_EXEC", r"C:/dev/dev-harness/tools/asm-exec.ps1")
SECRET = "{{resolve:secretsmanager:boltbetz/v2/sandbox/synkros-emulator-qa:SecretString:ApiKey}}"
_cached = []


def _key():
    """SYNKROS_API_KEY if set, else resolved once through asm-exec (by full path) into this process only.
    Raises RuntimeError (never SystemExit) so the runner reports a FAIL with the reason."""
    k = os.environ.get("SYNKROS_API_KEY", "") or (_cached[0] if _cached else "")
    if not k:
        if not os.path.exists(ASM_EXEC):
            raise RuntimeError(f"asm-exec not found at {ASM_EXEC} (set ASM_EXEC)")
        # ponytail: secret rides a child python's argv for ~1 s (asm-exec only resolves arguments); never printed.
        # Child is this file's "_echo", not python -c: PowerShell would bind -c to asm-exec's -Command.
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ASM_EXEC,
                            sys.executable, os.path.abspath(__file__), "_echo", SECRET],
                           capture_output=True, text=True, timeout=90)
        if r.returncode or not r.stdout.strip():
            raise RuntimeError(f"asm-exec exit {r.returncode}: {r.stderr.strip()[-200:]}")
        k = r.stdout.strip()
        _cached.append(k)
    if k.startswith("{"):
        k = json.loads(k).get("ApiKey", "")
    if not k:
        raise RuntimeError("Synkros emulator key empty")
    return k


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"X-Api-Key": _key(), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            txt = resp.read().decode()
            return json.loads(txt) if txt else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path}: {e.code} {e.read().decode()[:300]}")


def feed(asset):
    return req("GET", f"/machine/{asset}/feed")


def end_session(asset):
    return req("POST", f"/admin/api/machines/{asset}/end-session")


def summary(p):
    cards = [{k: c.get(k) for k in ("cardId", "id", "status", "cardType") if k in c} for c in p.get("cards", [])]
    keep = {k: p.get(k) for k in ("playerId", "firstName", "lastName", "cwaAvailable", "cwaEnabled", "cardLevel")}
    return {**keep, "cards": cards}


if __name__ == "__main__":
    cmd, *a = sys.argv[1:]
    if cmd == "_echo":  # asm-exec child: hands the resolved key back to _key() over a pipe
        sys.stdout.write(a[0]); sys.exit()
    if cmd == "overview":
        o = req("GET", "/admin/api/overview")
        print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in o.items()
                          if not isinstance(v, dict)}, default=str)[:1500])
        for m in o.get("machines", []):
            print("machine", m.get("assetId"), "session", m.get("sessionCardId"))
        for p in o.get("players", []):
            if str(p.get("firstName", "")).startswith("Lane"):
                print("player", json.dumps(summary(p)))
    elif cmd == "seed":
        lane, pin = a
        n = lane[1:]
        p = req("POST", "/admin/api/players", {"firstName": f"Lane{n}", "lastName": "QA", "pin": pin,
                                                 "cwaAvailable": 2_500_000})  # $25.00 in millicents
        try:
            m = req("POST", "/admin/api/machines", {"assetId": f"EMU-L{n}"})
            print("machine", m.get("assetId"))
        except RuntimeError as e:
            print("machine", f"EMU-L{n}", "exists" if "409" in str(e) else e)
        print("player", json.dumps(summary(p)))
    elif cmd == "player":
        print(json.dumps(summary(req("GET", f"/admin/api/players/{a[0]}"))))
    elif cmd == "feed":
        print(json.dumps(feed(a[0])))
    elif cmd == "end":
        print(json.dumps(end_session(a[0]))[:400])
