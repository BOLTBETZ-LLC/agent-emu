"""A signed-in staging test account on a phone with no Gmail and no AWS login.

  python newaccount.py d7 [label]

1. Provisions a password QA account through the sidecar QA routes (POST /accounts/provision), which hands out a Plaid
   sandbox identity from its pool. Needs the sidecar QA token (EXT_QA_TOKEN, or the agent-emu app's stored key).
2. Signs the phone in through the session route (no emailed code).
3. Runs the sign-up + Plaid sandbox ID check with that identity (signup.py) until Home.
Prints the account's email and id (never its password) and appends them to accounts.json beside this file, so
`signin.py`-style session sign-ins can reuse the account later. Exit 0 = on Home.
"""
import json, os, sys, time
from pathlib import Path
import signin, signup
from run import call, wait_for

HERE = Path(__file__).resolve().parent


def main(dev, label="agent"):
    tok = signin.qa_token()
    acct = signin.qa("POST", "/accounts/provision", tok, {"label": label})
    ident = acct["identity"]
    rec = {"email": acct["email"], "accountId": acct["accountId"], "identity": ident["index"], "created": time.strftime("%Y-%m-%d %H:%M")}
    print("provisioned", rec["email"], rec["accountId"], "identity", ident["firstName"], ident["lastName"], flush=True)
    f = HERE / "accounts.json"
    known = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else []
    f.write_text(json.dumps(known + [rec], indent=1), encoding="utf-8")

    link = signin.qa("POST", f"/accounts/{acct['accountId']}/session", tok, {})["link"]
    call(dev, "shell", cmd=f"am force-stop {signin.APP}")  # the app reads the session link only at cold start
    call(dev, "deep_link", uri=link, package=signin.APP)
    hit, _ = wait_for(dev, ["home-screen", "onboarding-screen", "no-account-create", "affirmations-screen", "start-screen", "login-screen"], 60)
    where = hit.get("resource-id") if hit else "nothing"
    print("after session link:", where, flush=True)
    if where == "home-screen":
        return True
    os.environ["PLAID_SSN4"] = ident["ssn"][-4:]
    return signup.run(dev, ident["firstName"], ident["lastName"], ident["street"])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(*sys.argv[1:3]) else 1)
