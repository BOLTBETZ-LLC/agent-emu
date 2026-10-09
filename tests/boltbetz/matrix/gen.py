"""PICT rows -> runner cases. Stdlib only, plus Microsoft's pict.exe (v3.7.4, official release).

  python matrix/gen.py            # regenerate cases/matrix/<area>/*.json + cases/matrix-signed-out/auth/*.json
  python matrix/gen.py --dry      # counts and time estimate only

pict.exe: $PICT, else tests/boltbetz/.venv/bin/pict.exe
(`gh release download v3.7.4 -R microsoft/pict -p pict.exe -D tests/boltbetz/.venv/bin`). pypict has no
Python 3.14 wheel and its sdist does not build, so the CLI is used; PICT output is deterministic for a model.

Each model in matrix/models/<area>.pict is one area. Every row becomes one case with:
- setup: account state seeded on the Synkros emulator for the phone's lane player ({lane.*}, run.py fills it),
  an app-side fault deep link, a Synkros error injection scoped to the lane player, or a network condition;
- the flow, with a lifecycle event (bg_resume = Home key + relaunch, kill_mid = force-stop + launch) mid-flow;
- expected checks: success ids/values, or (on a fault) an error the user can see, money unchanged on the emulator,
  app balance == emulator balance after the dust settles, no crash, no fatal JS log, no fatal-error screen;
- cleanup that restores the lane: faults cleared, card unlocked, CWA on, CWA $25, machine session ended, offers cleared.
Matrix mode's reset (run.py --matrix) also force-stops the app and puts CWA back to $25 before every case.
"""
import json, os, re, subprocess, sys, urllib.parse
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent  # tests/boltbetz
PICT = os.environ.get("PICT", str(ROOT / ".venv" / "bin" / "pict.exe"))
APP = "com.boltbetz.staging"
L = "boltbetz-staging://e2e-session/"
FATAL = "FATAL EXCEPTION|ReactNativeJS.*(Unhandled|TypeError|Invariant Violation)"
ERR_JUDGE = ("The screen tells the user something went wrong or is unavailable (an error message, a retry "
             "button or an unavailable state) and does not show the action as completed")
SOFT200_BODY = json.dumps({"transactionStatus": "Failed", "status": "Failed", "message": "QA soft-200 failure"})
PRESET = {"p500": "500", "problem": "problem", "validation": "validation", "forbidden": "forbidden",
          "timeout": "timeout", "offline": "offline", "decline": "decline", "synkros": "synkros", "sila": "sila"}


# ---------- step helpers ----------

def dl(path):
    return {"call": "deep_link", "uri": L + path}


def home():
    return [dl("hooks?on=1"), dl("route/MainFlow/Tabs/Home"), {"tap_id": "tab-home", "optional": True, "wait_s": 2},
            {"wait_id": "home-screen", "wait_s": 20}]


def fault(endpoint, kind):
    """App-side fault: the call never leaves the phone (qaFaults.ts)."""
    if kind == "soft200":
        return dl(f"fault?endpoint={endpoint}&status=200&body=" + urllib.parse.quote(SOFT200_BODY))
    return dl(f"fault?endpoint={endpoint}&preset={PRESET[kind]}")


def emu(method, path, body=None, optional=False):
    s = {"emu": "req", "method": method, "path": path}
    if body is not None:
        s["body"] = body
    if optional:
        s["optional"] = True
    return s


def synk(op, code, times=1):
    """Synkros emulator error injection scoped to the lane player (consumed by that player's calls only).
    'after-<code>': Synkros does the work, then answers the error (money moved, app told it failed)."""
    after = code.startswith("after-")
    body = {"operation": op, "errorCode": code[6:] if after else code, "times": times, "playerId": "{lane.player}"}
    if after:
        body["timing"] = "after"
    return emu("POST", "/admin/api/errors", body)


def slow(op, times=2):
    return emu("POST", "/admin/api/errors", {"operation": op, "delayMs": 8000, "times": times,
                                             "playerId": "{lane.player}"})


def mid(lifecycle):
    if lifecycle == "bg_resume":
        return [{"call": "key", "name": "home", "screenshot": False}, {"sleep_ms": 1500},
                {"call": "app", "launch": APP}, {"sleep_ms": 800}]
    if lifecycle == "kill_mid":
        return [{"call": "shell", "cmd": f"am force-stop {APP}"}, {"call": "app", "launch": APP},
                {"wait_id": ["home-screen", "tab-home"], "wait_s": 40}, dl("hooks?on=1")]
    return []


def chk(c):
    return {"check": c}


def judge(text, settle=2):
    return chk({"judge": text, "settle_s": settle})


def cwa(save=None, expect=None, wait=20):
    c = {"value": {"emu": "/admin/api/players/{lane.player}", "field": "cwaAvailable", "scale": 100000}}
    c.update({"save": save} if save else {"expect": expect, "wait_s": wait})
    return chk(c)


def meter(save=None, expect=None, wait=20):
    c = {"value": {"emu": "/machine/{lane.machine}/feed", "field": "meterMillicents", "scale": 100000}}
    c.update({"save": save} if save else {"expect": expect, "wait_s": wait})
    return chk(c)


def ui_val(uid, save=None, expect=None, wait=20):
    c = {"value": {"ui": uid}}
    c.update({"save": save} if save else {"expect": expect, "wait_s": wait})
    return chk(c)


def refresh():  # pull to refresh (native-width coordinates; the runner scales them)
    return [{"call": "swipe", "x1": 660, "y1": 600, "x2": 660, "y2": 1900, "device_px": True, "screenshot": False},
            {"sleep_ms": 2500}]


FINAL = [{"no_crash": APP}, {"logs_lack": FATAL, "filter": APP}, {"ui": "fatal-error-screen", "present": False, "wait_s": 1}]


def restore(offers=False):
    s = [{"tap_id": t, "optional": True} for t in ("enter-pin-close-button", "wallet-modal-close", "voucher-modal-close-button",
                                                   "tip-close-button", "machine-tips-sheet-close-button")]
    s += [dl("fault?clear=1"),
          emu("POST", "/admin/api/cards/{lane.card}/lock", {"locked": False}, optional=True),
          emu("PUT", "/admin/api/players/{lane.player}", {"cwaEnabled": True}, optional=True),
          {"emu": "end_session", "asset": "{lane.machine}", "optional": True},
          {"emu": "set_cwa", "player": "{lane.player}", "dollars": 25}]
    if offers:
        s.append({"emu": "clear_offers", "player": "{lane.player}"})
    return s + [dl("route/MainFlow/Tabs/Home")]


def state_setup(state):
    return {"funded": [], "zero_balance": [{"emu": "set_cwa", "player": "{lane.player}", "dollars": 0}],
            "locked_card": [emu("POST", "/admin/api/cards/{lane.card}/lock", {"locked": True})],
            "cwa_disabled": [emu("PUT", "/admin/api/players/{lane.player}", {"cwaEnabled": False})]}.get(state, [])


def failing(r):
    """Does this row expect the action to fail?"""
    return (r.get("AppFault", "none") not in ("none",) or r.get("FlagsFault", "none") != "none"
            or (r.get("SynkrosErr", "none") != "none" and not r["SynkrosErr"].startswith("after-"))
            or r.get("Network") == "offline")


def server_cause(r, op, endpoint):
    """Steps that arm the row's fault, Synkros error or network condition, right before the trigger."""
    s = []
    if r.get("AppFault", "none") != "none":
        s.append(fault(r.get("FaultEndpoint", endpoint) if r.get("FaultEndpoint", "none") != "none" else endpoint,
                       r["AppFault"]))
    if r.get("SynkrosErr", "none") != "none":
        s.append(synk(op(r["SynkrosErr"]) if callable(op) else op, r["SynkrosErr"]))
    if r.get("Network") == "offline":
        s.append(dl("fault?endpoint=*&preset=offline"))
    if r.get("Network") == "slow":
        s.append(slow(op("") if callable(op) else op))
    return s


# ---------- areas ----------

def a_home(r):
    st, s = r["Step"], []
    s += home() + state_setup(r["State"]) + [cwa(save="cwa0")]
    ep = r["FaultEndpoint"] if r["FaultEndpoint"] != "none" else "getLinkedPlayerAccounts"
    s += server_cause(r, "*", ep)
    if st == "home_load":
        s += [{"tap_id": "tab-settings"}, {"wait_id": "settings-screen"}, {"tap_id": "tab-home"}, {"wait_id": "home-screen"}] + refresh()
    elif st == "pull_refresh":
        s += refresh()
    elif st == "card_list":
        # the error banner can shift the layout under the first tap: Jev retries the tap only if the list is not open
        s += refresh() + [{"tap_id": "home-all-cards-button", "wait_s": 15}, {"sleep_ms": 1500},
                          {"decide": "Open the list of all my venue cards", "until_id": "card-list-screen", "max_steps": 3}]
    else:
        s += [dl("route/MainFlow/Transactions"), {"wait_id": "transactions-screen"}]
    s += mid(r["Lifecycle"])
    if r["Lifecycle"] == "kill_mid":  # the relaunch dropped the faults; what matters is a sane Home afterwards
        s += [{"wait_id": "home-screen", "wait_s": 30}] + refresh()
        if r["State"] in ("funded", "zero_balance"):
            s += [cwa(save="cwa1"), ui_val("home-card-balance-{lane.card}", expect="cwa1")]
    elif failing(r):
        s += [chk({"screenshot": "error"}), judge(ERR_JUDGE)]
        if r["AppFault"] != "none":  # recovery: fault cleared, refresh brings the data back
            s += [dl("fault?clear=1")] + refresh() + [judge("The screen shows data and no error message", 3)]
    elif st in ("home_load", "pull_refresh"):
        if r["State"] in ("funded", "zero_balance"):
            s += [ui_val("home-card-balance-{lane.card}", expect="cwa0", wait=40 if r["Network"] == "slow" else 20)]
        elif r["State"] == "locked_card":
            s += [judge("The venue card is shown as blocked or locked, or tells the user to contact the venue")]
        else:
            s += [judge("Home shows the venue card area (a card or a message about it), not a blank or broken screen")]
    elif st == "card_list":
        s += [chk({"ui": "card-list-load-failed", "present": False, "wait_s": 3}), chk({"screenshot": "cards"})]
    else:
        s += [chk({"ui": "transactions-error-title", "present": False, "wait_s": 3}), chk({"screenshot": "transactions"})]
    return dict(steps=s, cleanup=restore(), est=14, timeout=150)


AMT = {"zero": "0", "below_min": "4", "min_5": "5", "typical_10": "10", "over_daily": "6000",
       "all_balance": "35", "over_balance": "100", "typical_5": "5"}


def money_tail(r, amt, op_name):
    """After PIN: what the user and the ledgers must show."""
    s = mid(r["Lifecycle"])
    a = float(amt)
    if r["Lifecycle"] == "kill_mid":
        return s + [dl("route/MainFlow/Tabs/Wallet/WalletMain"), {"wait_id": "wallet-main-screen", "wait_s": 20},
                    {"sleep_ms": 8000}, cwa(save="cwa1")] + refresh() + [
            ui_val("wallet-balance-amount", expect="cwa1", wait=30)]
    s += [{"wait_id": ["wallet-modal-title", "wallet-modal", "enter-pin-title"], "wait_s": 60}, chk({"screenshot": "result"})]
    sign = 1 if op_name == "deposit" else -1
    if failing(r):
        s += [judge(ERR_JUDGE), cwa(expect="cwa0")]
    elif r.get("SynkrosErr", "").startswith("after-"):  # Synkros moved the money, then answered an error
        s += [cwa(expect=f"cwa0 + {sign * a}")]
    else:
        s += [chk({"ui_text": "wallet-modal-title", "contains": "Complete" if op_name == "deposit" else ""}),
              cwa(expect=f"cwa0 + {sign * a}")]
    # whatever happened, the app must end up agreeing with the casino ledger
    s += [{"tap_id": "wallet-modal-secondary-button", "optional": True}, {"tap_id": "wallet-modal-close", "optional": True},
          dl("route/MainFlow/Tabs/Wallet/WalletMain"), {"wait_id": "wallet-main-screen", "wait_s": 20},
          dl("fault?clear=1"), cwa(save="cwa1")] + refresh() + [ui_val("wallet-balance-amount", expect="cwa1", wait=30)]
    return s


def a_deposit(r):
    amt = AMT[r["Amount"]]
    s = home() + state_setup(r.get("State", "funded"))
    if r["FaultEndpoint"] == "getDepositFees":
        s.append(fault("getDepositFees", r["AppFault"]))
    s += [dl("route/MainFlow/Tabs/Wallet/WalletMain"), {"wait_id": "wallet-main-screen", "wait_s": 20}, cwa(save="cwa0"),
          {"tap_id": "wallet-action-deposit"}, {"wait_id": "wallet-deposit-screen"},
          {"fill": "deposit-screen-amount-input", "text": amt}, {"tap_id": "deposit-amount-title"}]
    if r["Step"] == "form_only":
        s += mid(r["Lifecycle"])
        s += [judge(ERR_JUDGE) if r["FaultEndpoint"] == "getDepositFees" else chk({"ui": "deposit-fee-text"}),
              chk({"ui": "enter-pin-title", "present": False, "wait_s": 2}), cwa(expect="cwa0")]
        return dict(steps=s, cleanup=restore(), est=14, timeout=120)
    s += [{"tap_id": "deposit-agreement-checkbox"}]
    if r["Amount"] in ("zero", "below_min", "over_daily"):
        want = {"zero": "greater than 0", "below_min": "minimum deposit is $5.00", "over_daily": "more than $5,000.00 per day"}
        s += [{"tap_id": "wallet-deposit-submit-button", "optional": True},
              chk({"ui_text": "deposit-screen-amount-input-error", "contains": want[r["Amount"]]}),
              chk({"ui": "enter-pin-title", "present": False, "wait_s": 2}), cwa(expect="cwa0")]
        return dict(steps=s, cleanup=restore(), est=16, timeout=120)
    if r["FaultEndpoint"] == "tokenizedCreditCardDeposit" or r["SynkrosErr"] != "none" or r["Network"] != "normal":
        s += server_cause(r, "deposits", "tokenizedCreditCardDeposit")
    s += [{"tap_id": "wallet-deposit-submit-button"}, {"emu": "pin", "player": "{lane.player}"}]
    s += money_tail(r, amt, "deposit")
    return dict(steps=s, cleanup=restore(), est=40, timeout=210)


def a_withdraw(r):
    amt = AMT[r["Amount"]]
    st0 = r.get("State", "funded")
    seed = 0 if st0 == "zero_balance" else 35
    s = home() + state_setup(st0)
    if r["FaultEndpoint"] == "getPaymentSources":
        s.append(fault("getPaymentSources", r["AppFault"]))
    s += [{"emu": "set_cwa", "player": "{lane.player}", "dollars": seed},
          dl("route/MainFlow/Tabs/Wallet/WalletMain"), {"wait_id": "wallet-main-screen", "wait_s": 20}] + refresh() + [
          cwa(save="cwa0"), {"tap_id": "wallet-action-withdraw"},
          {"wait_id": ["withdraw-amount-input", "withdraw-no-card"], "wait_s": 15}]
    if r["FaultEndpoint"] == "getPaymentSources":
        s += mid(r["Lifecycle"]) + [judge(ERR_JUDGE), cwa(expect="cwa0")]
        return dict(steps=s, cleanup=restore(), est=16, timeout=120)
    s += [{"fill": "withdraw-amount-input", "text": amt}, {"tap_id": "wallet-withdraw-balance-hero"}]
    if r["Step"] == "form_only":
        s += mid(r["Lifecycle"]) + [chk({"ui": "withdraw-fee-text"}), chk({"ui": "enter-pin-title", "present": False, "wait_s": 2})]
        return dict(steps=s, cleanup=restore(), est=16, timeout=120)
    if r["Amount"] in ("zero", "over_balance") or st0 == "zero_balance":
        s += [{"tap_id": "withdraw-submit-button", "optional": True},
              chk({"ui": "enter-pin-title", "present": False, "wait_s": 3}),
              judge("The withdraw screen shows a problem with the amount or balance and did not start a withdrawal"),
              cwa(expect="cwa0")]
        return dict(steps=s, cleanup=restore(), est=18, timeout=120)
    s += server_cause(r, "withdrawals", "tokenizedCreditCardWithdrawal")
    s += [{"tap_id": "withdraw-submit-button"}, {"emu": "pin", "player": "{lane.player}"}]
    s += money_tail(r, amt, "withdraw")
    return dict(steps=s, cleanup=restore(), est=42, timeout=210)


def connect(r=None, cause=()):
    return [{"emu": "end_session", "asset": "{lane.machine}", "optional": True}, {"tap_id": "tab-boltbetz"},
            {"wait_id": "qa-machine-scan-input", "wait_s": 12}, *cause, {"tap_id": "qa-machine-scan-input"},
            {"emu": "type_qr", "asset": "{lane.machine}"}, {"tap_id": "qa-machine-scan-submit"}]


def a_machine(r):
    st = r["Step"]
    s = home() + state_setup(r["State"])
    if r["State"] == "zero_balance":
        s.append({"emu": "set_cwa", "player": "{lane.player}", "dollars": 0})
    s.append(cwa(save="cwa0"))
    if st in ("connect_link_text", "connect_garbage"):
        text = "https://example.com/not-a-machine" if st == "connect_link_text" else "QA-NOT-A-TOKEN-0000"
        s += [{"tap_id": "tab-boltbetz"}, {"wait_id": "qa-machine-scan-input", "wait_s": 12}, {"tap_id": "qa-machine-scan-input"},
              {"call": "type_text", "text": text, "screenshot": False}, {"tap_id": "qa-machine-scan-submit"}]
        s += mid(r["Lifecycle"]) + [chk({"ui": "machine-connected-screen", "present": False, "wait_s": 8}),
                                     chk({"screenshot": "rejected"}), meter(expect="0"), cwa(expect="cwa0")]
        if st == "connect_garbage":
            s.append(judge(ERR_JUDGE, 3))
        return dict(steps=s, cleanup=restore(), est=16, timeout=120)
    ops = lambda code: {"connect": "egm-logins", "transfer": "funds-transfers", "cashout": "egm-logouts"}[st]
    ep = {"connect": "machineLogin", "transfer": "transferCwa", "cashout": "machineLogout"}[st]
    if st == "connect":
        s += connect(cause=server_cause(r, ops, ep)) + mid(r["Lifecycle"])
        if failing(r) or r["State"] == "locked_card":
            s += [chk({"ui": "machine-connected-screen", "present": False, "wait_s": 10}), judge(ERR_JUDGE), meter(expect="0"),
                  cwa(expect="cwa0")]
        else:
            s += [{"wait_id": "machine-connected-screen", "wait_s": 40 if r["Network"] == "slow" else 25}, meter(expect="0"),
                  ui_val("machine-play-option-value-cwa", expect="cwa0")]
        s += [{"emu": "end_session", "asset": "{lane.machine}", "optional": True}]
        return dict(steps=s, cleanup=restore(), est=24, timeout=150)
    # transfer and cashout start from a clean connection
    s += connect() + [{"wait_id": "machine-connected-screen", "wait_s": 25}]
    amt = AMT.get(r["Amount"], "5")
    if st == "transfer":
        s += server_cause(r, ops, ep)
        s += [{"tap_id": "machine-play-option-info-button-cwa"}, {"wait_id": "machine-transfer-sheet"},
              {"fill": "machine-transfer-amount", "text": amt}, {"tap_id": "machine-transfer-title"},
              {"tap_id": "machine-transfer-submit"}]
        if r["Amount"] in ("zero", "over_balance") or r["State"] == "zero_balance":
            s += [chk({"ui": "pin-cell-0", "present": False, "wait_s": 3}), judge(ERR_JUDGE), meter(expect="0"), cwa(expect="cwa0")]
            return dict(steps=s, cleanup=restore(), est=34, timeout=180)
        s += [{"emu": "pin", "player": "{lane.player}"}] + mid(r["Lifecycle"]) + [{"sleep_ms": 3000}]
        if r["Lifecycle"] == "kill_mid":  # money is conserved whatever the app shows: meter + CWA == start
            s += [{"sleep_ms": 8000}, meter(save="m1"), cwa(expect="cwa0 - m1")]
        elif failing(r):
            s += [judge(ERR_JUDGE), meter(expect="0"), cwa(expect="cwa0")]
        elif r["SynkrosErr"].startswith("after-"):
            s += [meter(expect=amt), cwa(expect=f"cwa0 - {amt}"), ui_val("machine-play-option-value-cwa", expect=f"cwa0 - {amt}", wait=30)]
        else:
            s += [meter(expect=amt), cwa(expect=f"cwa0 - {amt}"), ui_val("machine-play-option-value-cwa", expect=f"cwa0 - {amt}")]
        return dict(steps=s, cleanup=restore(), est=45, timeout=210)
    # cashout: put $5 on the machine first, then disconnect with the row's cause armed
    s += [{"tap_id": "machine-play-option-info-button-cwa"}, {"wait_id": "machine-transfer-sheet"},
          {"fill": "machine-transfer-amount", "text": "5"}, {"tap_id": "machine-transfer-title"},
          {"tap_id": "machine-transfer-submit"}, {"emu": "pin", "player": "{lane.player}"}, meter(expect="5")]
    s += server_cause(r, ops, ep)
    s += [{"tap_id": "machine-session-disconnect-button", "wait_s": 8},
          {"tap_id": "machine-session-disconnect-confirm-button", "wait_s": 8}] + mid(r["Lifecycle"]) + [{"sleep_ms": 3000}]
    if failing(r) or r["Lifecycle"] == "kill_mid" or r["SynkrosErr"].startswith("after-"):
        if failing(r) and r["Lifecycle"] != "kill_mid":
            s.append(judge(ERR_JUDGE))
        s += [{"sleep_ms": 5000}, meter(save="m1"), cwa(expect="cwa0 - m1")]  # nothing lost between machine and wallet
    else:
        s += [{"wait_id": "machine-tips-sheet", "wait_s": 25}, meter(expect="0"), cwa(expect="cwa0"),
              chk({"ui_text": "machine-tips-balance-text", "contains": "$"})]
    return dict(steps=s, cleanup=restore(), est=50, timeout=240)


TIP_AMOUNTS = ["1", "2", "3", "4", "1.5", "2.5", "3.5"]  # a repeat of the same amount within ~1 min is refused


def a_tips(r, n):
    s = home() + state_setup(r["State"])
    if r["State"] == "zero_balance":
        s.append({"emu": "set_cwa", "player": "{lane.player}", "dollars": 0})
    if r["FaultEndpoint"] in ("getAvailableTipAmount", "getVoucherBarcodeImages"):
        s.append(fault(r["FaultEndpoint"], r["AppFault"]))
    s += [cwa(save="cwa0"), {"tap_id": "home-action-tip"}, {"wait_id": "tip-title", "wait_s": 15}]
    if r["Entry"] == "voucher_view":
        s += mid(r["Lifecycle"])
        if r["Lifecycle"] == "kill_mid":
            s += [{"tap_id": "home-action-tip"}, {"wait_id": "tip-title", "wait_s": 15}]
        s += [{"tap_id": "tip-voucher-row-0", "optional": True, "wait_s": 8}, {"sleep_ms": 2000}, chk({"screenshot": "voucher"})]
        s.append(judge(ERR_JUDGE) if failing(r) else
                 judge("A voucher barcode is shown, or the tip screen shows its list of previous tips or an empty list"))
        return dict(steps=s, cleanup=restore(), est=18, timeout=120)
    if r["FaultEndpoint"] == "getAvailableTipAmount":
        s += mid(r["Lifecycle"]) + [judge(ERR_JUDGE), cwa(expect="cwa0")]
        return dict(steps=s, cleanup=restore(), est=16, timeout=120)
    amt = {"zero": "0", "over_balance": "100"}.get(r["Amount"], TIP_AMOUNTS[n % len(TIP_AMOUNTS)])
    s += [{"fill": "tip-amount-input", "text": amt}, {"tap_id": "tip-title"}]
    if r["Amount"] in ("zero", "over_balance") or r["State"] == "zero_balance":
        s += [{"tap_id": "tip-submit-button", "optional": True}, chk({"ui": "pin-cell-0", "present": False, "wait_s": 3}),
              judge(ERR_JUDGE), cwa(expect="cwa0")]
        return dict(steps=s, cleanup=restore(), est=18, timeout=120)
    op = lambda code: {"22-012": "cashout-vouchers", "after-01-001": "cashout-vouchers"}.get(code, "withdrawals")
    s += server_cause(r, op, "tipWithdrawal")
    s += [{"tap_id": "tip-submit-button"}, {"emu": "pin", "player": "{lane.player}"}] + mid(r["Lifecycle"])
    if r["Lifecycle"] == "kill_mid":
        s += [{"sleep_ms": 8000}, cwa(save="cwa1"), dl("route/MainFlow/Tabs/Wallet/WalletMain"),
              {"wait_id": "wallet-main-screen", "wait_s": 20}] + refresh() + [ui_val("wallet-balance-amount", expect="cwa1", wait=30)]
    elif failing(r):
        s += [{"sleep_ms": 3000}, judge(ERR_JUDGE), cwa(expect="cwa0")]
    else:
        s += [{"wait_id": "wallet-modal-title", "wait_s": 45}, cwa(expect=f"cwa0 - {amt}")]
        if not r["SynkrosErr"].startswith("after-"):
            s.append(chk({"ui_text": "wallet-modal-title", "contains": "Tip Sent"}))
    return dict(steps=s, cleanup=restore(), est=30, timeout=180)


def a_rewards(r):
    s = [{"emu": "clear_offers", "player": "{lane.player}"}]
    names = {"none": [], "one_freeplay": ["QA {lane.id} Offer A"], "two_offers": ["QA {lane.id} Offer A", "QA {lane.id} Offer B"]}[r["Offers"]]
    s += [{"emu": "offer", "player": "{lane.player}", "name": nm, "freeplay": 5} for nm in names]
    s += home()
    if r["FaultEndpoint"] == "getPlayerRewards":
        s.append(fault("getPlayerRewards", r["AppFault"]))
    if r["Step"] == "list" or r["FaultEndpoint"] == "getPlayerRewards":
        op = lambda code: "offer-notifications"
        s += [x for x in server_cause({**r, "AppFault": "none"}, op, "getPlayerRewards")]
    s += [dl("route/MainFlow/Tabs/Rewards"), {"wait_id": "rewards-screen"}] + refresh() + mid(r["Lifecycle"])
    if r["Lifecycle"] == "kill_mid":
        s += [dl("route/MainFlow/Tabs/Rewards"), {"wait_id": "rewards-screen"}] + refresh()
    list_fails = r["Step"] == "list" and failing(r) and r["Lifecycle"] != "kill_mid"
    if list_fails or (r["FaultEndpoint"] == "getPlayerRewards" and r["Lifecycle"] != "kill_mid"):
        s += [chk({"screenshot": "rewards-error"}), judge(ERR_JUDGE)]
        return dict(steps=s, cleanup=restore(offers=True), est=18, timeout=120)
    if not names:
        s += [judge("Rewards shows no offers (an empty state), not an error", 3)]
        return dict(steps=s, cleanup=restore(offers=True), est=16, timeout=120)
    s += [{"wait_id": names[0], "wait_s": 40 if r["Network"] == "slow" else 20}]
    if r["Step"] == "list":
        if len(names) > 1:
            s.append(chk({"ui": names[1]}))
        return dict(steps=s, cleanup=restore(offers=True), est=18, timeout=120)
    if r["FaultEndpoint"] == "acceptPlayerOffer":
        s.append(fault("acceptPlayerOffer", r["AppFault"]))
    elif r["SynkrosErr"] != "none":
        s.append(synk("award-selections" if r["SynkrosErr"] != "35-002" else "offer-notifications", r["SynkrosErr"]))
    if r["Network"] == "slow":
        s.append(slow("award-selections"))
    s += [{"tap_id": "Redeem Now"}, {"wait_id": "reward-redeem-button"}, {"tap_id": "reward-redeem-button"}]
    if failing(r):
        s += [{"sleep_ms": 3000}, chk({"ui": "rewards-redeemed-title", "present": False, "wait_s": 3}), judge(ERR_JUDGE)]
    else:
        s += [{"wait_id": "rewards-redeemed-title", "wait_s": 40}, chk({"ui_text": "rewards-redeemed-message", "contains": "QA"}),
              {"tap_id": "rewards-redeemed-back-button", "optional": True}]
    return dict(steps=s, cleanup=restore(offers=True), est=24, timeout=150)


SCREENS = {  # route, screen id, ok id
    "settings": ("Tabs/Settings", "settings-screen", "settings-email-value"),
    "responsible_gaming": ("ResponsibleGaming", "responsible-gaming-screen", "responsible-gaming-deposit-value"),
    "operator_limits": ("OperatorLimits", "operator-limits-screen", "operator-limits-dep_card-value"),
    "notifications": ("Notifications", "notifications-screen", "notifications-list"),
    "email_toggle": ("Tabs/Settings", "settings-screen", "settings-email-notifications"),
    "card_ids": ("CardIDs", "card-ids-screen", "card-ids-heading"),
}


def a_settings(r):
    route, sid, ok = SCREENS[r["Screen"]]
    s = home()
    if r["FaultEndpoint"] != "none":
        s.append(fault(r["FaultEndpoint"], r["AppFault"]))
    s += [dl("route/MainFlow/" + route), {"wait_id": sid, "wait_s": 15}] + mid(r["Lifecycle"])
    if r["Lifecycle"] != "warm":
        s += [dl("route/MainFlow/" + route), {"wait_id": sid, "wait_s": 15}]
    if r["Screen"] == "email_toggle":  # tap twice: ends where it started (the setting is restored in-case)
        s += [{"call": "swipe", "x1": 660, "y1": 2300, "x2": 660, "y2": 700, "device_px": True, "screenshot": False},
              {"tap_id": "settings-email-notifications"}, {"sleep_ms": 2500}, chk({"screenshot": "toggled"})]
        if failing(r) and r["Lifecycle"] != "kill_mid":
            s.append(judge("The app says the setting could not be saved, or the switch went back to where it was"))
        s += [dl("fault?clear=1"), {"tap_id": "settings-email-notifications"}, {"sleep_ms": 2500}]
        return dict(steps=s, cleanup=restore(), est=16, timeout=120)
    if failing(r) and r["Lifecycle"] != "kill_mid":
        s += [chk({"screenshot": "error"}), judge(ERR_JUDGE)]
    else:
        s += [chk({"ui": ok, "wait_s": 15}), chk({"screenshot": r["Screen"]})]
    return dict(steps=s, cleanup=restore(), est=13, timeout=120)


def a_link(r):
    s = home()
    if r["AppFault"] != "none":
        s.append(fault("getAllAvailableOperators", r["AppFault"]))
    s += [dl("route/MainFlow/LinkAccount"), {"wait_id": "link-account-screen", "wait_s": 15}] + mid(r["Lifecycle"])
    if r["Lifecycle"] != "warm":
        s += [dl("route/MainFlow/LinkAccount"), {"wait_id": "link-account-screen", "wait_s": 15}]
    op = "link-account-operator-" + r["Operator"][-1]
    if failing(r):
        s += [chk({"ui_any": ["link-account-venue-error", "link-account-retry"]}), judge(ERR_JUDGE)]
    elif r["Step"] == "venue_list":
        s += [chk({"ui": op}), chk({"screenshot": "venues"})]
    else:  # opens the venue's page; never submits a link
        s += [{"tap_id": op}, {"sleep_ms": 2500}, chk({"screenshot": "operator"}),
              judge("The screen offers to link an existing player card or to create a new player account at this venue")]
    s += [{"tap_id": "link-account-close", "optional": True}]
    return dict(steps=s, cleanup=restore(), est=13, timeout=120)


EMAILS = {"valid": "lane.agent@example.com", "plus_tag": "lane.agent+qa1@example.com",
          "long_254": "a" * 64 + "@" + ".".join(["b" * 63, "c" * 63, "d" * 57]) + ".com",
          "no_at": "lane.agent.example.com", "empty": "", "spaces": "lane agent@example.com"}


def a_auth(r):
    """Signed-out phone only. Never taps a send button: a send emails a real inbox."""
    s = [{"call": "shell", "cmd": f"am force-stop {APP}"}, {"call": "app", "launch": APP},
         {"wait_id": ["start-screen", "login-screen"], "wait_s": 30}]
    if r["FlagsFault"] != "none":
        s += [fault("getFlags", r["FlagsFault"])]
    s += mid(r["Lifecycle"]) if r["Lifecycle"] == "bg_resume" else []
    valid = r["Input"] in ("valid", "plus_tag", "long_254")
    if r["Screen"] == "login_email":
        s += [{"tap_id": "start-login", "optional": True, "wait_s": 5}, {"wait_id": "login-screen"},
              {"tap_id": "login-email-toggle", "optional": True, "wait_s": 2}, {"tap_id": "login-identifier-input"}]
        if EMAILS[r["Input"]]:
            s.append({"call": "type_text", "text": EMAILS[r["Input"]], "screenshot": False})
        s += [{"sleep_ms": 1000}, chk({"screenshot": "typed"})]
        s += [chk({"ui": "login-send-code", "enabled": True})] if valid else [
            judge("The send code button is disabled, or the screen says the email is not valid", 1)]
    else:
        s += [{"tap_id": "start-create", "wait_s": 5}, {"tap_id": "login-signup-email", "optional": True, "wait_s": 5},
              {"wait_id": "create-email-screen", "wait_s": 10}, {"tap_id": "create-email-input"}]
        if EMAILS[r["Input"]]:
            s.append({"call": "type_text", "text": EMAILS[r["Input"]], "screenshot": False})
        s += [{"sleep_ms": 1000}, chk({"screenshot": "typed"})]
        s += [chk({"ui": "create-email-send"})] if valid else [
            judge("The send button is disabled, or the screen says the email is not valid", 1)]
    return dict(steps=s, cleanup=[dl("fault?clear=1")], est=14, timeout=90, signed_out=True)


AREAS = {  # area: (builder, pinned lane or None = portable, extra preconditions)
    "home": (a_home, None, {}), "deposit": (a_deposit, "L2", {"saved_card": True}),
    "withdraw": (a_withdraw, "L2", {"saved_card": True}), "machine": (a_machine, None, {}),
    "tips": (a_tips, None, {}), "rewards": (a_rewards, None, {}), "settings": (a_settings, None, {}),
    "link": (a_link, None, {}), "auth": (a_auth, "L1", {"signed_in": False}),
}


def pict_rows(model):
    out = subprocess.run([PICT, str(model)], capture_output=True, text=True, check=True).stdout.strip().splitlines()
    head = out[0].split("\t")
    return [dict(zip(head, line.split("\t"))) for line in out[1:]]


def slug(r):
    return "-".join(re.sub(r"[^a-z0-9]+", "", v.lower()) for k, v in r.items() if v not in ("none", "na", "warm", "normal", "funded"))[:70]


def build(area, r, n):
    fn, lane, pre = AREAS[area]
    b = fn(r, n) if area == "tips" else fn(r)
    est = b["est"] + 3 + {"bg_resume": 4, "kill_mid": 12}.get(r.get("Lifecycle"), 0) + (8 if r.get("Network") == "slow" else 0)
    case = {"id": f"M-{area}-{n:03d}-{slug(r)}".rstrip("-"), "lane": lane or "any", "timeout_s": b["timeout"],
            "preconditions": {"signed_in": not b.get("signed_out"), "kyc": True, "venue_card": True, **pre},
            "matrix": {"area": area, "row": r, "expect": "error" if failing(r) else "success", "est_s": est},
            "steps": b["steps"], "checks": list(FINAL),
            "cleanup": b["cleanup"],
            "note": f"PICT row {n} of matrix/models/{area}.pict: " + ", ".join(f"{k}={v}" for k, v in r.items())}
    if b.get("signed_out"):
        case["preconditions"]["app_launched"] = APP
        case["checks"] = FINAL[:2]
    return case, est


def main(argv):
    dry = "--dry" in argv
    totals, pinned = {}, {}
    for model in sorted((HERE / "models").glob("*.pict")):
        area = model.stem
        rows = pict_rows(model)
        out = ROOT / "cases" / ("matrix-signed-out" if area == "auth" else "matrix") / area
        if not dry:
            out.mkdir(parents=True, exist_ok=True)
            for old in out.glob("M-*.json"):
                old.unlink()
        secs = 0
        for n, r in enumerate(rows, 1):
            case, est = build(area, r, n)
            secs += est
            if not dry:
                (out / f"{case['id']}.json").write_text(json.dumps(case, indent=1) + "\n", encoding="utf-8")
        totals[area] = (len(rows), secs)
        if AREAS[area][1] and area != "auth":
            pinned[AREAS[area][1]] = pinned.get(AREAS[area][1], 0) + secs
    signed_in = {a: v for a, v in totals.items() if a != "auth"}
    n_all, s_all = sum(v[0] for v in signed_in.values()), sum(v[1] for v in signed_in.values())
    for a, (n, s) in totals.items():
        print(f"{a:9} {n:4} cases  ~{s / 60:5.1f} phone-min" + ("  (signed-out phone, run apart)" if a == "auth" else ""))
    phones = int(os.environ.get("AE_PHONES", "4"))
    wall = max(s_all / phones, max(pinned.values(), default=0))
    print(f"signed-in total {n_all} cases, ~{s_all / 60:.0f} phone-min; on {phones} phones ~{wall / 60:.0f} min wall "
          f"(pinned L2 alone ~{pinned.get('L2', 0) / 60:.0f} min)")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
