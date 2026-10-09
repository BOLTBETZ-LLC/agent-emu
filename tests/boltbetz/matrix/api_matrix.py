"""Backend + sidecar API matrix: no phone, staging only. Stdlib only.

  python matrix/api_matrix.py                          # unauthenticated + anonymous cases (no token needed)
  python matrix/api_matrix.py --send-code aaron+lane4@boltbetz.com      # Auth0 passwordless email code (app's client)
  BB_OTP=<code from Gmail> python matrix/api_matrix.py --login aaron+lane4@boltbetz.com
        # adds the authenticated cases. The token lives in this process only; the code is never written down.
  BB_TOKEN=... python matrix/api_matrix.py --email aaron+lane4@boltbetz.com   # a token you already hold (env only)
  python matrix/api_matrix.py --list                   # print the cases, call nothing

Every call is read-only or rejected before it can act:
- unauthenticated / bad-token calls: every v2 controller is [Authorize] (only GET account-exists is anonymous), so
  POSTs are refused by the auth middleware before model binding;
- authenticated: GETs on the caller's own ids, another user's id (must not answer 200), a bad route type, an
  unknown operator; POSTs only with a malformed JSON body to actions whose single [FromBody] model must bind first
  ([ApiController] answers 400 before the action runs); never deactivate, never create-sila-processor-token;
- Synkros errors: injected on the Synkros emulator scoped to the lane player (times=1, consumed by the call).
Never production: the base URLs are fixed to staging and checked.

Expected wire shapes (skill boltbetz-error-contract): A problem-details, B title-only, C validation, D empty body,
soft-200. Output: results/api-<stamp>/results.json + grid.md. Exit 0 = every expectation met.
"""
import base64, concurrent.futures as cf, getpass, json, os, sys, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
V2 = "https://staging.bbapp01.com/api/v2"
EXT = "https://ext-staging.bbapp01.com"
AUTH0 = "https://boltbetz-v2-staging.us.auth0.com"
CLIENT_ID = "UmZ1be9fBqFtVQZKe3lhhFTJlEgasZXQ"  # public native client id from v2-React-Native .env.staging
AUDIENCE = "https://staging.bbapp01.com/api"
for u in (V2, EXT, AUTH0):
    assert "prod" not in u and ("staging" in u), u

# v2 routes at BBManagementSystemV2 origin/staging (Controllers/*.cs). {u}=boltBetzUserId {o}=operatorId {p}=playerId {e}=email
GETS = """getlinkedoperators/{u} getallavailableoperators getoperatorinformation/{o} getlinkedplayeraccounts/{e}
boltbetzuser/{e} boltbetzuserexists/{e} playerdevicetokens/{u} boltbetz-bankaccounts/{u} error-state-plaid-items/{u}
player-notifications/{p}/{o} boltbetz-user-notifications/{u} boltbetz-appsettings/{u} transactions/{u}/01012026/12312026
total-balance/{u} transaction-totals/{u}/01012026/12312026 deposit-fees/{o} transaction-fees/{o}
cashout-voucher-barcode-images-limited/{u} cashout-voucher-barcode-images/{u} transaction-limits/{u}/{o}
responsible-gaming-settings/{u} boltbetz-user-active/{u} paymentsources/{u}
playerprofile/{o}/{p} playercardlevelautomation/{o}/{p} playercardview/{o}/{p} playeraccounts/{o}/{p} playerpoints/{o}/{p}
playerhomeview/{o}/{p} connectionview/{o}/{p} playeroffers/{o}/{p} homeplayeroffers/{o}/{p} available-tip-amount/{o}/{p}
player-account-enabled/{o}/{p}""".split()
CMS_GETS = [g for g in GETS if g.endswith("/{o}/{p}")]
# POST actions whose only input is one body model (signatures read at origin/staging). Malformed JSON only.
BODY_POSTS = """responsible-gaming-settings addnewboltbetzuser identityverification email-support update-notification-preferences
record-user-milestone refreshdevicetoken device-logout linkaccount linkaccount-multitry machinelogin machinelogout transfer-cwa
transferpointstoplay transferfreeplay transferplayeroffer acceptplayeroffer createplayeraccount createplayercard cwa-withdrawal
cwa-withdrawal-voucher validate-pincode generateplaidlinktoken generate-plaid-auth-linktoken generate-plaid-update-linktoken
process-bank-account-link bank-account-money-transfer bank-account-money-transfer-selectable-route tokenized-creditcard-deposit
tokenized-creditcard-withdrawal update-default-paymentsource boltbetz-account-capabilities remove-saved-creditcard
bank-transaction-fees card-transaction-fees card-withdrawal-support saved-card-withdrawal-support
payment-source-transaction-fees""".split()
ALL_POSTS = BODY_POSTS + ["deactivate-boltbetz-user/1", "mark-notification-read/1", "link-auth0-identities",
                          "remove-plaid-item", "new-creditcard-deposit", "generate-barcode-image/1", "findplayer/1"]
EXT_AUTHED = [("GET", "/api/ext/onboarding/me"), ("POST", "/api/ext/stepup/email/start"),
              ("POST", "/api/ext/account/close-request"), ("POST", "/api/ext/onboarding/idv/start"),
              ("POST", "/api/ext/owner/confirm")]
SYNKROS_CODES = ["01-001", "10-002", "11-004", "02-001"]
EXPIRED = ".".join(base64.urlsafe_b64encode(json.dumps(x).encode()).decode().rstrip("=") for x in
                   ({"alg": "RS256", "typ": "JWT"}, {"sub": "qa", "aud": AUDIENCE, "exp": 1})) + ".c2ln"


def http(method, url, token=None, body=None, raw=None, timeout=30):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace"), round(time.time() - t0, 2)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), round(time.time() - t0, 2)
    except Exception as e:  # network error: status 0
        return 0, f"{type(e).__name__}: {e}", round(time.time() - t0, 2)


def shape(status, text):
    if not text.strip():
        return "D-empty"
    try:
        j = json.loads(text)
    except ValueError:
        return "text"
    if not isinstance(j, dict):
        return "json"
    if "errors" in j:
        return "C-validation"
    if j.get("detail") and ("title" in j or "reason" in j or "status" in j):
        return "A-problem"  # staging also sends {status, detail, reason} with no title (seen 2026-10-09)
    if "title" in j:
        return "B-title"
    if status >= 400:
        return "E-envelope:" + ",".join(sorted(j)[:4])  # a JSON error body that is none of A/B/C
    if status == 200 and any(str(j.get(k, "")).lower() in ("failed", "failure", "error", "false")
                             for k in ("transactionStatus", "status", "success", "isSuccess")):
        return "soft-200"
    return "json"


def case(cid, method, url, expect, token=None, body=None, raw=None, before=None):
    return {"id": cid, "method": method, "url": url, "expect": expect, "token": token, "body": body, "raw": raw,
            "before": before}


def unauth_cases():
    out = []
    for path in GETS + ALL_POSTS:
        p = path.format(u=1, o=1, p=1, e="nobody%40example.com")
        m = "GET" if path in GETS else "POST"
        for tag, tok in (("none", None), ("garbage", "not-a-jwt"), ("expired", EXPIRED)):
            out.append(case(f"v2 {m} {path} auth={tag}", m, f"{V2}/{p}", {"status": [401]}, tok, body={} if m == "POST" else None))
    out.append(case("v2 GET account-exists anonymous bad phone", "GET", f"{V2}/account-exists?phoneNumber=abc%27",
                    {"status": [400], "shape": ["A-problem", "C-validation"]}))
    for m, path in EXT_AUTHED:
        for tag, tok in (("none", None), ("garbage", "not-a-jwt")):
            out.append(case(f"ext {m} {path} auth={tag}", m, EXT + path, {"status": [401, 403]}, tok, body={} if m == "POST" else None))
    out.append(case("ext GET /health", "GET", EXT + "/health", {"status": [200]}))
    out.append(case("ext GET /api/ext/flags no auth", "GET", EXT + "/api/ext/flags", {"status": [200, 401]}))
    out.append(case("v2 GET unknown route", "GET", f"{V2}/no-such-route", {"status": [401, 404]}))
    return out


def find(obj, *names):
    """First value of any key named like one of names (case-insensitive), searching nested JSON."""
    names = {n.lower() for n in names}
    stack = [obj]
    while stack:
        o = stack.pop(0)
        if isinstance(o, dict):
            for k, v in o.items():
                if k.lower() in names and not isinstance(v, (dict, list)):
                    return v
            stack += list(o.values())
        elif isinstance(o, list):
            stack += o
    return None


def authed_cases(token, email):
    e = urllib.parse.quote(email)
    s, t, _ = http("GET", f"{V2}/boltbetzuser/{e}", token)
    if s != 200:
        raise SystemExit(f"boltbetzuser/{email}: HTTP {s} {t[:200]}")
    u = find(json.loads(t), "boltBetzUserId", "id")
    s, t, _ = http("GET", f"{V2}/getlinkedplayeraccounts/{e}", token)
    linked = json.loads(t) if s == 200 and t.strip() else {}
    p, o = find(linked, "playerId"), find(linked, "operatorId")
    if not (u and p and o):
        raise SystemExit(f"could not resolve ids for {email}: user={u} player={p} operator={o}")
    ids = dict(u=u, o=o, p=p, e=e)
    other = dict(ids, u=int(u) + 1, p=int(p) + 1)
    out = []
    for path in GETS:
        out.append(case(f"own GET {path}", "GET", f"{V2}/{path.format(**ids)}", {"status": [200, 204], "not_shape": ["soft-200"]}, token))
        if "{u}" in path or "{p}" in path:
            out.append(case(f"other-user GET {path}", "GET", f"{V2}/{path.format(**other)}", {"status_not": [200]}, token))
        if "{u}" in path:
            out.append(case(f"bad-type GET {path}", "GET", f"{V2}/{path.format(**dict(ids, u='abc'))}", {"status": [400, 404]}, token))
        if "{o}" in path:
            out.append(case(f"unknown-operator GET {path}", "GET", f"{V2}/{path.format(**dict(ids, o=999))}",
                            {"status": [400, 403, 404], "shape": ["A-problem", "B-title", "C-validation", "D-empty"]}, token))
    for path in CMS_GETS:
        for code in SYNKROS_CODES:
            inj = {"operation": "*", "errorCode": code, "times": 1, "playerId": int(p)}
            # 02-001 (Synkros "not authorized"): the backend re-authenticates and retries, so the call succeeds
            ex = {"status": [200]} if code == "02-001" else {"status": [400, 404, 409, 500, 502, 503], "shape": ["A-problem", "B-title"]}
            out.append(case(f"synkros {code} GET {path}", "GET", f"{V2}/{path.format(**ids)}", ex, token, before=inj))
    for path in BODY_POSTS:
        out.append(case(f"malformed-json POST {path}", "POST", f"{V2}/{path}", {"status": [400, 415], "shape": ["C-validation", "A-problem", "B-title"]},
                        token, raw=b'{"amount": '))
    out.append(case("ext GET /api/ext/onboarding/me", "GET", EXT + "/api/ext/onboarding/me", {"status": [200]}, token))
    out.append(case("ext GET /api/ext/flags", "GET", EXT + "/api/ext/flags", {"status": [200]}, token))
    return out


def run_one(c):
    if c["before"]:
        import emu
        emu.req("POST", "/admin/api/errors", c["before"])
    s, t, secs = http(c["method"], c["url"], c["token"], c["body"], c["raw"])
    sh, ex, why = shape(s, t), c["expect"], []
    if "status" in ex and s not in ex["status"]:
        why.append(f"status {s} not in {ex['status']}")
    if "status_not" in ex and s in ex["status_not"]:
        why.append(f"status {s} (must not be)")
    if "shape" in ex and sh not in ex["shape"]:
        why.append(f"shape {sh} not in {ex['shape']}")
    if sh in ex.get("not_shape", []):
        why.append(f"shape {sh}")
    return {"id": c["id"], "status": s, "shape": sh, "secs": secs, "ok": not why, "why": "; ".join(why),
            "detail": t[:200] if why else ""}


def send_code(email):
    s, t, _ = http("POST", f"{AUTH0}/passwordless/start", body={"client_id": CLIENT_ID, "connection": "email",
                                                               "email": email, "send": "code"})
    if s != 200:
        raise SystemExit(f"passwordless/start: HTTP {s} {t[:200]}")


def login(email):
    """Code from env BB_OTP (after --send-code), else sends one now and reads it from stdin."""
    code = os.environ.get("BB_OTP", "")
    if not code:
        send_code(email)
        code = getpass.getpass(f"code sent to {email}: ") if sys.stdin.isatty() else sys.stdin.readline().strip()
    s, t, _ = http("POST", f"{AUTH0}/oauth/token", body={
        "grant_type": "http://auth0.com/oauth/grant-type/passwordless/otp", "client_id": CLIENT_ID, "username": email,
        "otp": code, "realm": "email", "audience": AUDIENCE, "scope": "openid profile email"})
    if s != 200:
        raise SystemExit(f"oauth/token: HTTP {s} {t[:200]}")
    return json.loads(t)["access_token"]


def main(argv):
    if "--send-code" in argv:  # step 1 of a two-step login: then BB_OTP=<code> ... --login <email>
        send_code(argv[argv.index("--send-code") + 1])
        print("code sent")
        return 0
    email = argv[argv.index("--login") + 1] if "--login" in argv else (argv[argv.index("--email") + 1] if "--email" in argv else None)
    token = login(email) if "--login" in argv else os.environ.get("BB_TOKEN")
    cases = unauth_cases() + (authed_cases(token, email) if token and email else [])
    if "--list" in argv:
        print("\n".join(c["id"] for c in cases), f"\n{len(cases)} cases")
        return 0
    plain = [c for c in cases if not c["before"]]
    with cf.ThreadPoolExecutor(8) as ex:
        res = list(ex.map(run_one, plain))
    res += [run_one(c) for c in cases if c["before"]]  # one injection at a time: each must meet its own call
    out = HERE.parent / "results" / ("api-" + time.strftime("%Y%m%d-%H%M%S"))
    out.mkdir(parents=True)
    (out / "results.json").write_text(json.dumps({"authenticated": bool(token), "results": res}, indent=1))
    bad = [r for r in res if not r["ok"]]
    lines = [f"API matrix: {len(res)} calls, {len(res) - len(bad)} ok, {len(bad)} unexpected "
             f"({'with' if token else 'no'} token)", "", "| case | status | shape | why |", "|---|---|---|---|"]
    lines += [f"| {r['id']} | {r['status']} | {r['shape']} | {r['why']} |" for r in sorted(res, key=lambda r: (r["ok"], r["id"]))]
    (out / "grid.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:1] + [f"  {r['id']}: {r['why']}" for r in bad[:40]]), f"\n{out}")
    return 1 if bad else 0


if __name__ == "__main__":
    import urllib.parse
    sys.exit(main(sys.argv[1:]))
