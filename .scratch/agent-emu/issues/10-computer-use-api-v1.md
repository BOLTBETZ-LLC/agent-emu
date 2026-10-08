# Computer-use API v1

Type: grilling
Status: resolved
Blocked by: 06

## Question

What is the exact v1 surface of computer-use control? Cover:

- The calls: screenshot, tap, swipe, type, key, wait-until-idle.
- The frame format.
- How events are pushed instead of polled.
- Error semantics.
- How the MCP layer exposes each call to agents.

## Answer

Grilled with Aaron on 2026-10-07. Each line below is his pick. The speed and size figures come from Computer-use fast path.

- **Action reply:** every input call returns a settled screenshot. That is one round trip per step, with an estimated ~37-41 ms at 60 Hz. A per-call opt-out returns just the input ack.
- **Settled** means no new frame was posted for N ms (from the host) AND the app is idle (main thread and JS queue idle, no animations running). A small in-guest helper reports app idle.
- **Deadline:** the default maximum wait is 3 s and can be overridden per call. At the deadline, the call returns the latest frame with `settled=false` and the reason (`frames_changing` or `app_busy`). It does not error.
- **Frame:** 1080x2400 JPEG q75 by default (about 7 ms encode and about 122 KB, measured on this PC). A `size` parameter can ask for smaller frames, such as 706x1568. A `zoom(rect)` call returns part of the screen. Every frame carries a generation number, capture time and scale factor. Frames come from gfxstream `getScreenshot`, never from the lagged readback worker.
- **Inputs:** `tap`, `long_press`, `swipe`, `type_text`, `key` (back, home, enter and so on), and `gesture` for multi-finger moves such as pinch. Gestures are generated on the host as timed event streams over virtio-input with MT protocol B.
- **Coordinates:** x,y are in pixels of the frame the agent received. The server maps them to the device using the frame's scale factor.
- **UI tree:** a separate `ui_tree` call, only on request. It is not in action replies.
- **Animations:** window, transition and animator scales are 0 by default. A Device setting turns them back on.
- **MCP:** one tool per call: `screenshot`, `tap`, `long_press`, `swipe`, `type_text`, `key`, `gesture`, `ui_tree`, `zoom`. Each takes a device id. The binary API underneath has the same calls.
- **Ownership:** one agent leases a Device at a time. Any other agent's input gets a `busy` error. Watchers (the viewer, other agents) can still take screenshots.
- **Acceptance timings,** each reported as p50/p95/p99 at 1 and at 10 Devices: screenshot request to image; input request to injection ack; input request to a settled frame.

## Measured: daemon v1 (2026-10-08)

`agent-emud` (branch `daemon`, commit `52f81fc`): Rust/tokio, newline JSON on 127.0.0.1:7400, and an `mcp` stdio mode with 8 tools. The daemon owns the console pipe itself. Smoke test on 1 Device (slim3 + pmem + DAX kernel, 896 MB, 720x1080 display), with input and screenshots going over the guest console (`input` / `screencap` + base64):

| Call | p50 | p95 |
|---|---|---|
| screenshot (client round trip) | 159 ms | 174 ms |
| of which guest `screencap` + base64 | 144 ms | 159 ms |
| JPEG encode | 13 ms | 13 ms |
| tap, input ack | 37 ms | 61 ms |
| tap returning a settled screenshot (2-4 frames, settled 10/10) | 496 ms | 613 ms |
| tap with `screenshot:false` | 21 ms | 36 ms |
| ui_tree | 2.2-4.3 s | |

- **Misses the p95 < 50 ms target** (spec §8). The cost is the console transport. Next: the fast path through host-side frame capture plus virtio-input.
- Lease works: a second client got `busy`. The MCP screenshot returns an image block in 155 ms.
- **The tap's effect is not proven.** Before and after frames are identical (`assets/10-daemon-smoke/`, both seen by eye): offline, "Try Again" brings back the same dialog. The input ack is real.
- Trap: `uiautomator` from the console shell fails (`libnativeloader.so not found`). The console shell predates apexd's mount namespace and lacks `*CLASSPATH`. Fix: `nsenter` into system_server's namespace and take its env.
- Not built yet: Job Object, health check, restart, `long_press` / `gesture` / `zoom`, the app-idle helper, binary framing. Settle is "two identical frames", not the spec's "100 ms + app idle".

## Measured: fast path (2026-10-08)

crosvm `agent-emu-pmem` + daemon `daemon`: input goes into virtio-input over named pipes (`--input multi-touch[path=\.\pipe\ae-touch-N]`, `--input keyboard[path=\.\pipe\ae-kbd-N]`). Frames come from the 2D scanout: crosvm copies the scanout blob from guest RAM into `fb.bin` (shared mapping) on every flush and again on request over `\.\pipe\ae-fb-N`. The console stays as the fallback. 1 Device, 720x1080, 10 calls each:

| Call | p50 | p95 |
|---|---|---|
| screenshot (client round trip) | 12 ms | 13 ms |
| of which capture (refresh + read) | 1 ms | 1 ms |
| of which JPEG q75 encode, 720x1080 | 10 ms | 10 ms |
| tap, input ack | 0 ms | 0 ms |
| tap with `screenshot:false` (client round trip) | 0 ms | 0 ms |
| key HOME to first new frame | 18 ms | 28 ms |
| key HOME to settled (100 ms with no frame) | 398 ms | 674 ms |
| tap with no visible change to settled | 113 ms | 187 ms |

- Visible proof: a virtio tap on the drawer icon opens WebView Browser Tester (`assets/10-fast-path/before-tap-drawer*.jpg`, `after-tap-app-open*.jpg`; the fast frame matches the guest's own `screencap`). Tap to first new frame was 33 ms on that tap.
- "Settled" as defined in §8 (100 ms quiet) cannot be under 50 ms. Input to first response frame is under 50 ms at p95 for HOME.
- Trap: the guest flushes a scanout blob before it finishes drawing, so a flush-time copy is one frame old. Copy at read time.
- Trap: slim3 has no wallpaper and no SystemUI, so regions no layer covers keep old pixels in the scanout. `screencap` shows them black. Full-screen apps are unaffected.
- Trap: crosvm's Windows virtio-input worker waited on the raw pipe handle, which is always signaled. It spun and logged `ERROR_NO_DATA` (232) without end (GBs of `crosvm.log`). Fixed by waiting on the read notifier.
