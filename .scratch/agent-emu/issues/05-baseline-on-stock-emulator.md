# Baseline on stock emulator

Type: task
Status: resolved
Blocked by: 04

## Question

Measure the proof app on the stock emulator to set the baseline. Run it on a stock API 36 x86_64 image, with an ATD (Automated Test Device) image as the closest to slim:

- Unique memory of the emulator process on the host at the app's first screen.
- Guest per-process memory (`dumpsys meminfo`, RSS/PSS) for the kernel, system_server, zygote, SystemUI and the app.
- Time from cold boot to the first screen.

Also settle what Proof app without GMS left open: launch the APK fresh on a stock **no-GMS** (AOSP "default") API 36 x86_64 image, block `u.expo.dev`, and check logcat for crashes from native Firebase or FCM providers before JS starts.

Use the existing streams tooling under its locks (`streams.py`). Record the exact commands and numbers. This shows how far the stock setup is from 400 MB and where the memory goes.

## Answer

Measured 2026-10-07 on this PC with emulator 37.1.11.0. Three stock API 36 x86_64 images, each on a fresh `pixel_7` AVD: 2 GB guest RAM, 1080x2400 at 420 dpi, headless, `-no-snapshot`, `-gpu host`. The proof APK (EAS `183b566c`) was launched in airplane mode so no OTA could replace the bundled JS. Every value is from one run per image.

| Image | GMS | Cold boot | Host unique (WS private) | Host private commit | Guest used RAM | App PSS / RSS | system_server RSS | Crashes |
|---|---|---|---|---|---|---|---|---|
| `google_atd` | yes | 46 s (89 s first boot) | **3,259 MB** | 4,057 MB | 1,353 MB | 218 / 365 MB | 411 MB | 0 |
| `aosp_atd` | no | 45 s | **3,129 MB** | 3,943 MB | 1,126 MB | 221 / 367 MB | 372 MB | 0 |
| `default` | no | 43 s | **3,311 MB** | 4,225 MB | 1,307 MB | 217 / 368 MB | 400 MB | 0 |

- **First screen verified by eye on all three.** The screen shows login v2 Start with the offline dialog on top: "No Internet Connection", "Try Again". The UI tree dump has the same text. The activity was displayed 349-567 ms after launch, then `Running "BoltBetz"` appeared in logcat.
- **No GMS works.** Both no-GMS images reach the first screen with 0 lines in the crash buffer and 0 `FATAL EXCEPTION`. This settles what Proof app without GMS left open: native Firebase providers do not crash on start.
- **The gap is mostly on the host side.** The guest uses only 1.1-1.35 GB, but the host is charged 3.1-3.3 GB of unique memory per Device. The stock emulator never returns guest pages and adds roughly 1+ GB of its own (qemu plus gfxstream). Returning free guest pages to the host and slimming the VMM are worth more than trimming Android.
- **The app alone is about 220 MB PSS.** That leaves about 180 MB of the 400 MB budget for the kernel, the native daemons, system_server and the VMM together. That is tight.
- **What dropping things saves (guest RSS):** GMS is 3 processes of 281 + 280 + 204 MB (`google_atd`). SystemUI is 270 MB and Launcher3 197 MB (`default`). ATD images already drop SystemUI and the launcher.
- **Trap 1:** ATD images ship `debug.hwui.drawing_enabled=0`, so apps draw nothing and `screencap` returns all black. Turning it on (`setprop debug.hwui.drawing_enabled true` before launch) fixed it. Run 1's numbers were taken with drawing off and are discarded.
- **Trap 2:** Git Bash rewrites `/sdcard/...` arguments to adb. Set `MSYS_NO_PATHCONV=1`.
- **Not measured here:** an exact per-part guest split (kernel vs daemons vs zygote; the full `dumpsys meminfo` files are kept), a warm or snapshot boot, more than 1 Device at a time, and an online launch (the app was offline).
- Evidence: `.scratch/agent-emu/assets/05-baseline/` (per-image `run.log`, `host-mem.txt`, `config.txt`, `dumpsys-meminfo*.txt`, `proc-meminfo.txt`, `first-screen.png`, plus `measure.sh`).
