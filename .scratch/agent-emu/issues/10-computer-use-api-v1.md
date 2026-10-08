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

### Decision: settle rule (2026-10-08, recommended default under Aaron's standing order)

- **Settled** = the first response frame after the input, then **33 ms with no new frame** (about 2 vsyncs at 60 Hz), plus app idle once the in-guest helper exists. It replaces "100 ms quiet" (spec §8), which can never be under 50 ms.
- **Pass bar:** p95 < 50 ms for *input → first response frame* at 1 Device. The fast path already measures 28 ms p95 (key HOME). "Input → settled frame" is still reported, but it is not the pass bar.
- Visible input proof checked by eye: a fast-path tap on the drawer icon opened WebView Browser Tester (`assets/10-fast-path/before-tap-drawer.jpg` → `after-tap-app-open.jpg`).

## Measured: settle v2 (2026-10-08)

Daemon settle rule now matches the decision: wait for the first frame after the input, then 33 ms with no new frame. No frame by the 3 s deadline returns `settled=false`, `reason: no_frame`. The settle loop compares scanout seq numbers only; one final frame is captured and encoded. JPEG moved to `jpeg-encoder` with AVX2 (`simd`). Driver: `daemon/settle_smoke.py`. 1 Device, 720x1080, screen idle first (0 frames in 2 s), crash dialogs hidden (`hide_error_dialogs 1`; slim3 crash-loops Bluetooth). 10 rounds of: tap the launcher icon (opens WebView Browser Tester), wait 1 s, HOME.

| Call | p50 | p95 (= max, n=10) |
|---|---|---|
| tap → first frame | 27 ms | 56 ms |
| tap → settled | 206 ms | 239 ms |
| HOME → first frame | 36 ms | 37 ms |
| HOME → settled | 305 ms | 355 ms |
| all 20 inputs → first frame | 35 ms | 56 ms |
| screenshot (client round trip) | 6 ms | 6 ms |
| JPEG q75 encode, 720x1080 | 2 ms | 3 ms (was 10 ms with `image`) |

- Settled 20/20. Visible change seen by eye: `assets/10-settle-v2/1-tap-open.jpg` (the settled frame of tap 3 is the app) and `2-home.jpg` (launcher back; app pixels stay where no layer draws, the slim3 trap).
- **The 33 ms rule stops early on cold launches.** Taps 1 and 2 settled at 93 and 80 ms and 12 more frames came within 1 s (the app window draws after a >33 ms gap). Tap 9 had 4 late frames. Warm taps had 0. The app-idle helper is the fix; until then a cold app launch can return a mid-transition frame.
- p95 < 50 ms for input → first frame: HOME passes (37 ms). Tap misses at n=10 (56 ms, the first cold tap); taps 3-10 were 24-35 ms.
- Fixed on the way: the scanout seqlock reader gave up after 1000 spins (a few µs) while crosvm's copy takes about 1 ms, so a tap during a launch failed with `scanout mapping: no consistent frame`. It now retries for up to 200 ms and copies the raw pixels before converting.

## Measured: headless display, stale-pixel fix, dialogs (2026-10-08)

- **Headless:** crosvm fork `9ade87258`. With `AGENT_EMU_HEADLESS` set, the Windows GPU uses the stub display instead of `WinApi` and the window thread creates no GUI windows. agent-emud sets it (`3d55d6a`). `EnumWindows` over every crosvm pid: 8 processes, **0 windows** (visible or hidden). The `fb.bin` fast path is unchanged, because frames are published at flush, not by the display.
- **Stale pixels fixed:** Device setup runs `service call SurfaceFlinger 1008 i32 1` (HW overlays off), so SurfaceFlinger composes every frame on the GPU into a target it clears first, and the guest composer copies the whole target into the scanout (`226c57c`). Check (`daemon/display_smoke.py`, `assets/10-headless/`): open the proof app, HOME, then compare the fast frame with the guest's `screencap`.

| | fast frame vs `screencap` | seen by eye |
|---|---|---|
| overlays on (old) | mean diff 128.9, **67% of pixels off** | the whole app stays on screen after HOME (`before-2-home.jpg`) |
| overlays off (setup default) | mean diff **0.0, 0% off** | clean launcher (`after-2-home.jpg`) |

  Input → first frame stays in range: tap 27-40 ms with overlays off vs 26-44 ms with them on (5 rounds each, 896 MB). A wallpaper can't fix this on slim3: the wallpaper is drawn by SystemUI, which slim3 drops.
- **Dialogs:** setup sets `hide_error_dialogs 1` and broadcasts `CLOSE_SYSTEM_DIALOGS` (`b6ab7cd`). The proof app's first screen had 0 dialog windows and kept focus (`assets/15-logs/app-first-screen.jpg`). The Bluetooth crash loop goes on (no HCI peer), but its dialogs stay hidden.
- **Trap: never `pm disable-user com.android.bluetooth`.** On the next airplane-mode change, `BluetoothManagerService.bleTurningOnToOff` unbinds a service that was never bound (`IllegalArgumentException: Service not registered`). system_server dies and then crash-loops every ~5 s with `No service can handle intent ... android.bluetooth.IAdapter`.
- Setup also sets `log.tag.RIL S`: the RIL logged `Can't connect to port:9600d` (no modem simulator) at about 40 MB a minute.

## Measured: where swipe -> first frame goes (2026-10-08, slim5, 896 MB, uncapped, crosvm-diet)

First frames are timed by a watcher started before the input, so a frame drawn during a long swipe counts from the swipe's start. Swipes step the proof app's Welcome carousel one page at a time (`daemon/frame_smoke.py`, `prof_smoke.py`, `lever_smoke.py`, `gesture_smoke.py`; results in `assets/10-*`).

**vCPUs** (20 swipes, uncapped; other workers' Devices were running, host CPU 20-54%):

| vCPUs | p50 | p95 | frames drawn |
|---|---|---|---|
| 2 | 102 ms | 180 ms | 19/20 |
| 4 | 87 ms | 149 ms | 20/20 |
| 6 | 86 ms | 212 ms | 20/20 (host CPU 54%, 13 other Devices) |

**Renderer:** `ro.hardware.egl=angle`, `ro.hardware.vulkan=pastel`: GL over ANGLE on the guest's software Vulkan, all CPU. gfxinfo's GPU percentile is 1 ms because the "GPU" work runs on CPU inside `swap`.

**HWUI per frame, base, 2 vCPU** (framestats, 120 frames, ms p50/p95): UI thread 8.1/22.7, sync incl. texture upload 21.4/31.5, issue draw commands 2.0/7.8, swap (where the software raster runs) 24.6/33.8, total 68.2/91.0. gfxinfo: 50th 44 ms, 95th 65 ms, 55% janky.

**Threads** (`top -H`, 0.5 s samples during swipes): `libcuttlefish-rild` 32% of a vCPU (the RIL retrying a modem that isn't there); the app's two software-raster workers 11.6% each; app UI thread 4.5%; RenderThread 4.1%; surfaceflinger's two raster workers ~2.9% each.

**Levers, applied one after another on one boot (2 vCPU, 24 swipes each):**

| Step | swipe first frame p50/p95 | HWUI frame p50/p95 | janky |
|---|---|---|---|
| base | 106 / 125 ms | 40 / 65 ms | 50% |
| + `setprop ctl.stop vendor.ril-daemon` | 91 / 95 ms | 23 / 30 ms | 7% |
| + SurfaceFlinger 1008 off (no client composition) | 90 / 108 ms | 15 / 22 ms | 0.4% |
| + `debug.hwui.renderer skiavk` | app died at start (no frames) | | |

system_server kept its pid through all steps.

**The ~90 ms floor is the app's gesture, not the pipeline** (rild stopped, 2 vCPU):

| Input | p50 | p95 | min |
|---|---|---|---|
| HOME key (system path, no app logic) | 35 ms | 52 ms | 27 ms |
| carousel swipe, 60 ms | 92 ms | 159 ms | 72 ms |
| carousel swipe, 150 ms | 90 ms | 95 ms | 74 ms |
| carousel swipe, 300 ms | 91 ms | 107 ms | 74 ms |

The swipe's first frame does not move with the swipe's speed, so it is a fixed ~55 ms inside the app (React Native gesture and JS work before the carousel draws), on top of a ~35 ms input-to-scanout pipeline. Our scanout copy is ~1 ms, and the daemon polls the frame sequence every 0.5 ms.

**Cheapest levers for p95 < 50 ms on the pipeline** (HOME-type input), without gfxstream:
1. Stop `vendor.ril-daemon` in Device setup: free, frees ~1/3 of a vCPU, HWUI frame 40 -> 23 ms p50. Not yet in the daemon.
2. 4 vCPUs: about 15 ms off the swipe p50; 6 adds nothing.
3. Client composition off saves another ~8 ms per HWUI frame, but brings back the slim3 stale pixels (issue 10, headless section); it needs a different stale-pixel fix first.
4. skiavk does not work on this guest (the app dies).

App-gesture-bound inputs (this carousel) stay near 90 ms: for those, p95 < 50 ms needs the app, or the host GPU (gfxstream), not the emulator's input or copy path.

**Decision (2026-10-08):** the ~90 ms on app gestures (the Welcome carousel) is the proof app's own handling, not the emulator: it is the same at every swipe speed while HOME is at ~35 ms. Getting it under 50 ms needs host GPU rendering (gfxstream) or app changes. No gfxstream work now. `start`/`fleet` take `cpus` (default stays 2).

### Device setup: RIL at lowest priority, soak (2026-10-08, slim3 896 MB defaults, `daemon/soak_smoke.py`, `assets/10-soak-2cpu`, `10-home-4cpu`, `10-rilstop/`)

- **Do not stop the RIL.** With `setprop ctl.stop vendor.ril-daemon` (killed at uptime 124 s), `com.android.phone` hung on BOOT_COMPLETED (ANR at +3 min), then failed to start every ~45 s (`failed to complete startup`, 19 ANRs in 15 min). Each restart and ANR dump costs CPU. system_server kept its pid. HOME stayed p50 36-37 ms, but one HOME took 1080 ms during the loop.
- **Device setup now renices every RIL thread to 19** (`for t in /proc/$(pidof libcuttlefish-rild)/task/*: renice -n 19`). The RIL still spins (~33% of a vCPU when the CPU is idle) but yields to the UI. 15-minute soak at 2 vCPU with a HOME + relaunch every minute: system_server pid unchanged, app alive, no phone loop.

| vCPUs | HOME -> first frame p50 / p95 (2 warm-ups, 20 timed) |
|---|---|
| 2, start of soak | 34 / 41 ms |
| 2, after 15 min | 35 / 46 ms |
| 4, start | 34 / 39 ms |
| 4, after 1 min | 34 / 38 ms |

2 vCPUs meet p95 < 50 ms on HOME, so the default stays 2.
- **Pre-existing loop, not from this change:** `com.android.nfc` fails to start every ~45-60 s (`failed to complete startup`, 21 ANRs in 15 min). It began at 08:35:40, before the RIL change, and shows up in `crash_events`. It is a CPU sink on every slim3 Device. Not fixed yet (disabling it needs the same check as Bluetooth, issue 10 trap).

### slim3n: NFC HAL removed, now the daemon's default image (2026-10-08, `assets/10-soak-slim3n`)

- Cause of the `com.android.nfc` loop: the Cuttlefish NFC HAL (`vendor/apex/com.google.cf.nfc.apex`) panics at boot (`CfNfc: main: panicked at device/google/cuttlefish/guest/hals/nfc/src/nfc.rs:153`, no host NFC simulator), so `INfc/default` never registers and the NFC app's startup waits on it until ActivityManager kills it.
- Fix, same pattern as the A17 sensors fix: slim3n = slim3 minus that apex (and its `vendor_filesystem_config` line). The NFC app apex (`com.android.nfcservices`) stays: it adds framework jars to BOOTCLASSPATH. Built from `/root/slim/tf` -> `tf3n` in WSL (`agent-emu-work/stage2/slim3n/edits.sh`, `build.sh`, ~1 min), then `wsl --shutdown`. Run dir `agent-emu-work/run-slim3n` (new super, vbmeta, vbmeta_system, system-pmem; the rest hard-linked from `run`).
- 15 min soak at 2 vCPU (`soak_smoke.py`, HOME + relaunch every minute): **0 ANRs** in the whole logcat (slim3: 21 nfc ANRs in 15 min), system_server pid unchanged, app alive. Only crash events: the known boot-time Bluetooth crashes (dialogs hidden).
- HOME -> first frame p50/p95: 34/41 ms at the start, 34/38 ms after 15 min.
- The daemon's default image (`image` empty) is now slim3n; `image: "slim3"` still boots the old one.
