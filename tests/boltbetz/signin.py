"""Sign a phone in by emailed code, in two halves so the code can be read from Gmail in between.

  python signin.py send d0 aaron+lane1@boltbetz.com   # Login -> email -> Send code, prints the screen ids after
  python signin.py code d0 <code>                      # types the code on the EnterCode screen, prints ids after
"""
import sys, time
from run import call, nodes, wait_for, do_step

def ids(dev):
    return sorted({n.get("resource-id") for n in nodes(dev) if n.get("resource-id")})

mode, dev, val = sys.argv[1:4]
if mode == "send":
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
