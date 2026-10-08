# Viewer

Type: grilling
Status: resolved
Blocked by: 10

## Question

How does a human watch or click a headless Device?

## Answer

Recommended default, recorded under Aaron's standing order of 2026-10-07.

- `agent-emud` serves a local web page at `http://127.0.0.1:<port>/`. It shows a grid of every Device, live through MJPEG from the same `getScreenshot` path, plus clicks and keys. Nothing to install.
- The viewer is a watcher, not a lease holder. It acts only when a human takes the lease.
- Closing the page costs nothing: frames are only produced while someone watches.

## Built: the control panel (2026-10-08)

**How to open it:** double-click **agent-emu** on the Desktop or in the Start Menu (or run `C:\dev\agent-emu\agent-emu.cmd`). It starts `agent-emud` hidden if it is not running and opens http://127.0.0.1:7401/ in the default browser. Closing the browser leaves Devices running; **Quit** in the panel stops every Device and the daemon. Create the shortcuts again with `daemon/install-shortcuts.ps1`.

- `agent-emud` serves the page on 127.0.0.1:7401 (localhost only, `AE_UI_ADDR`) next to the binary API on 7400. One embedded page (`daemon/agent-emud/src/ui.html`), no new dependencies (`ui.rs`: small HTTP/1.1 server; `/api` reuses the daemon's call handler; `/frames` streams scanout JPEGs whenever the screen changes; `/upload` installs an APK with adb; `/screenshot.png`).
- Start defaults to **Phone (full Android)**: the stock Cuttlefish 15581820 image (`agent-emu-work/run-full`: stock super unsparsed from `stage1/img`, stock kernel and initrd, system on the super block device, no pmem), 2048 MB, network on. Its setup renames Cuttlefish's hidden `buried_eth0` to `eth0` so Android's Ethernet service takes it (DHCP from slirp gives internet; 10.0.2.15 stays on it for adb). The lean images (slim3n, slim3, slim4, slim5) are listed as "Agent (lean)" with a note that they have no system UI.
- Checked by hand in Chrome on d0 (`assets/19-panel/`): fresh page, Start, ticking progress line, Android home with status bar and nav bar after ~50-67 s, one-click "Open BoltBetz (staging)" (install from the image + launch), Try Again -> sign-in screen online, typed `test@example.com` into the field, Back closed the keyboard and went back, Home, Launch, Logs filtered to the package, crash list. Mouse click -> tap, drag -> swipe, wheel -> swipe, keyboard -> type_text / keys.
- **Click -> frame shown in the page** (timed in the page, from the click to the first new frame drawn on the canvas; lean slim3n, 2 vCPU, launcher icon tap + HOME, n=20): **p50 56 ms, p95 58 ms**.
- Not built: rotation (the button is shown disabled), long press, per-page auth.
