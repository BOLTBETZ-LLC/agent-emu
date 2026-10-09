"""Sign up a new account and pass the Plaid sandbox ID check (login-v2 staging build). Worked on d1-d3, 2026-10-09.

  python signin.py send dN <email>                      # Login -> email -> Send code
  (tap code-input) python signin.py code dN <code>      # lands on "No account for <email>"
  PLAID_SSN4=<last 4 of the sandbox SSN> python signup.py dN <First> <Last>

SSN last 4: ops-dev-kyc-walkthrough.md (bb-infra/docs/70-ops). Plaid asks for the LAST 4 only; typing 9 digits keeps
the first 4, which is wrong. Names: Leslie Knope or a Plaid Dashboard sandbox identity (Ethan Hunt, Jack Reacher,
John Dutton, Tony Soprano, Jackie Chan); all share Leslie's DOB, phone, address and SSN.

Flow: Create account -> Home (no ID check yet) -> Deposit (guarded: opens the ID check, adds no money) -> agree ->
the app's licence scanner -> Type It Instead -> Plaid Link -> "Successfully verified" -> Finish -> Choose Your Venue
-> Close -> Wallet -> Home tab.

Plaid's WebView often exposes no accessibility tree, so Plaid pages are driven by coordinates (1320x2868 screen).
Masked fields drop or reorder digits when typed fast: one digit at a time, 1.5 s apart, 1 s after focusing.
Plaid with three phones at once ran fine; at 1 GB guest RAM the app can be killed mid-Plaid (d3) or the VM can die
(d0). If the app was killed: relaunch it, tap Deposit, then "Check again" (id-check-check-again-button); Plaid
resumes at the page it was on, so finish the remaining pages by hand with the same coordinates.
"""
import os, sys, time
from run import call, nodes, find, wait_for

GAP = 1.5
CONTINUE = (660, 2720)
FIELD1, FIELD2 = (660, 508), (660, 697)  # first field on a Plaid page; last name
MONTH, DAY, YEAR = (304, 508), (763, 508), (1080, 508)
CITY, STATE, ZIP = (660, 789), (400, 969), (1027, 969)


def log(dev, *a):
    print(time.strftime("%H:%M:%S"), dev, *a, flush=True)


def tap(dev, key, wait_s=30):
    n, _ = wait_for(dev, [key], wait_s)
    if not n:
        raise RuntimeError(f"{key!r} not on screen")
    call(dev, "tap", x=n["center"][0], y=n["center"][1], device_px=True, screenshot=False)


def xy(dev, p, after=1.0):
    call(dev, "tap", x=p[0], y=p[1], device_px=True, screenshot=False)
    time.sleep(after)


def text(dev, s, after=1.0):
    call(dev, "type_text", text=s, screenshot=False)
    time.sleep(after)


def digits(dev, s):
    for ch in s:
        text(dev, ch, GAP)


def key(dev, k, after=1.0):
    call(dev, "shell", cmd="input keyevent " + k)
    time.sleep(after)


def next_page(dev, wait=6):
    xy(dev, CONTINUE, wait)


def run(dev, first, last):
    ssn4 = os.environ["PLAID_SSN4"]
    call(dev, "permission", pkg="com.boltbetz.staging", perm="android.permission.CAMERA", action="grant")
    if find(nodes(dev), "no-account-create"):
        tap(dev, "no-account-create")
    tap(dev, "home-action-deposit", 40)
    log(dev, "Home reached, opening ID check")
    tap(dev, "affirmation-agree-box")
    time.sleep(1)
    tap(dev, "affirmations-continue")
    tap(dev, "license-scan-type")
    time.sleep(20)  # Plaid Link WebView load
    next_page(dev)                                   # intro
    next_page(dev)                                   # country: United States
    xy(dev, FIELD1); digits(dev, "2345678909")       # phone
    next_page(dev)
    xy(dev, FIELD1); text(dev, first)
    xy(dev, FIELD2); text(dev, last)
    next_page(dev)
    digits(dev, "11111")                             # SMS code, auto-submits
    time.sleep(6)
    xy(dev, MONTH, 2); text(dev, "Jan", 2); key(dev, "KEYCODE_ENTER")
    xy(dev, DAY); digits(dev, "18")
    xy(dev, YEAR); digits(dev, "1975")
    next_page(dev)
    xy(dev, FIELD1); text(dev, "123 Main St.", 2); key(dev, "KEYCODE_ESCAPE")  # close suggestions
    xy(dev, CITY); text(dev, "Pawnee")
    xy(dev, STATE); text(dev, "Indiana"); key(dev, "KEYCODE_ENTER")
    xy(dev, ZIP); digits(dev, "46001")
    next_page(dev, 8)
    xy(dev, FIELD1); digits(dev, ssn4)               # SSN last 4
    next_page(dev, 15)                               # -> "Successfully verified"
    log(dev, "Plaid submitted, tapping Finish")
    next_page(dev, 15)                               # Finish
    tap(dev, "link-account-close", 40)               # skip the venue picker
    tap(dev, "tab-home")
    ok = wait_for(dev, ["home-screen"], 15)[0]
    log(dev, "HOME" if ok else "NOT HOME")
    return bool(ok)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if run(*sys.argv[1:4]) else 1)
