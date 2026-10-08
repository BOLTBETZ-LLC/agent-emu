# agent-emu

Label: wayfinder:map

## Destination

A build-ready spec for a slim Android emulator built for agents to test apps. The spec is done when nothing is left to decide before building a version where the proof app reaches its first screen with no more than 600 MB of unique RAM per Device (raised from 400 by Aaron, 2026-10-08), and 10 Devices run at once on Aaron's Windows 11 PC.

## Notes

- **Domain**: an Android emulator whose only job is letting agents test apps. Vocabulary is in `GLOSSARY.md` at the repo root.
- **Priorities**: (1) host footprint and computer-use control rank jointly first; (2) the structured agent API comes second.
- **Decided while charting (Aaron, 2026-10-07)**:
  - The destination is a spec, not a build.
  - RAM is measured as unique memory per Device.
  - The finish line is the proof app's first screen, logged out.
  - Host: Windows first, with the design kept portable to Linux and macOS.
  - 10 Devices run at once.
  - No Metro: the JS bundle ships inside the APK.
  - Route A is the working hypothesis: a slim real-kernel VM, crosvm-style, on the Windows hypervisor (WHPX).
  - Android API 36 (Android 16), no GMS.
  - Phone-resolution screen rendered on the host GPU.
  - Headless, with an optional viewer.
  - New code is in Rust.
  - Agents use a binary API, with MCP on top.
  - Computer-use round trip under 50 ms.
  - Fork is designed in but not built.
- **Proof app**: EAS build `183b566c-2d7c-448a-ad2e-fb1ec459374d`. Profile `staging`, v1.4.0 (22), APK, commit `09ef72912b9edf91410b5055aa3a321b3b577d1b` of v2-React-Native. Artifact: https://expo.dev/artifacts/eas/93pP9VDx6Y5XYamfbhF9oms5rXZd5oh9-SBMvI-xo0s.apk (expires 2027-01-05).
- **Host machine**: 32 GB RAM, 20 threads (Intel Core Ultra 7 265), NVIDIA RTX 5060 Ti plus Intel iGPU, Windows 11 Home.
- **Skills**: grilling tickets call `grilling` and `domain-modeling`. Research tickets call `research`.
- **Two-model work (Aaron, 2026-10-07)**: Claude and GPT-6.1 Sol (`codex exec -m gpt-6.1-sol`, not gpt-6-sol) work this map together. Each research ticket gets two blind passes, one Claude and one GPT-6.1 Sol (`*.codex.md` on `research/codex-*` branches). The ticket's Answer reconciles the two and names any disagreement.
- **Speed rule (Aaron, 2026-10-07)**: "best for agents to work with and move as fast as possible and not do anything unnecessary or do massive tests. critical paths only". Applies to the spec and to every build step.
- **Standing rules**: use sources from the last 6 months where possible, and every claim cites a URL. Never run a full test suite or a heavy build in parallel; at most 2 heavy jobs run at once.

## Decisions so far

- [crosvm on Windows hypervisor](issues/01-crosvm-on-whpx.md): public crosvm runs on WHPX but is untested there, and its display opens a desktop window. Fork it to add a headless display, gfxstream and a boot test. OpenVMM is the fallback. An API 36 boot is not proven yet.
- [WHPX memory capabilities](issues/02-whpx-memory-capabilities.md): WHPX can share a read-only base image across VMs. The VMM can build copy-on-write, lazy page fill (x64 `GpaAccessFaultExit`), balloon reclaim and dirty tracking; this PC supports both needed features. There is no KSM-style dedup. Still open: whether Windows page combining reaches guest RAM.
- [Proof app without GMS](issues/04-proof-app-without-gms.md): the app likely reaches its login v2 Start screen with no GMS and no fakes. The APK has x86_64 and embedded JS, FCM runs only after login, and Amplitude is off. Still open: whether native Firebase providers fail before JS starts.
- [GPU cost per Device](issues/07-gpu-cost-per-device.md): VRAM is fine on the 16 GB card. Host RAM is the risk, mainly gfxstream's fixed 128 MiB staging buffer per Device, so the spec needs a smaller staging buffer plus the cross-domain blob path. Still open: whether the issue #198 fix speeds up the proof app.
- [Minimum AOSP API 36 footprint](issues/03-minimum-aosp-footprint.md): no published RAM number exists. Start from Cuttlefish `aosp_cf_x86_64_slim` with drawing turned back on (it ships with `debug.hwui.drawing_enabled=0`). `boot.art` is anonymous dirty memory and can't be shared across Devices; file-backed .oat/.so pages can, but only through DAX. 400 MB unique is plausible but unmeasured.
- [Computer-use fast path](issues/06-computer-use-fast-path.md): under 50 ms looks reachable, an estimated 37-41 ms at 60 Hz. Screenshots come from the host via gfxstream `getScreenshot` encoded as JPEG q75, and input goes through crosvm virtio-input. Most of the time is Android's own two-frame input-to-display. Nothing is measured on this PC yet; GPU readback time is still open.
- [Shared system image on Windows](issues/11-shared-system-image-on-windows.md): share the system image with virtio-pmem DAX, using one read-only file mapped into each Device's VMM. This needs a crosvm Windows pmem port, a custom guest kernel (`FS_DAX`, `ZONE_DEVICE`) and an uncompressed, non-inline erofs image with no dm-verity. APEX payloads stay private unless apexd is patched. Savings are unmeasured; the estimate is about 70-185 MB per Device.
- [Baseline on stock emulator](issues/05-baseline-on-stock-emulator.md): measured. The proof app reaches its first screen on all three stock API 36 images, including both no-GMS ones, with 0 crashes. The stock emulator costs 3.1-3.3 GB of host unique memory per Device while the guest uses only 1.1-1.35 GB. The app alone is about 220 MB PSS, leaving about 180 MB for everything else. ATD images disable drawing (`debug.hwui.drawing_enabled=0`).
- [Computer-use API v1](issues/10-computer-use-api-v1.md): every action returns a settled 1080x2400 JPEG; settled means frames stopped AND the app is idle, with a 3 s deadline flagged as not settled. Inputs are tap, long_press, swipe, type_text, key and gesture, with coordinates in frame pixels. The UI tree is on request, animations are off, MCP has one tool per call, and one agent leases a Device at a time.
- [Lock the route](issues/08-lock-the-route.md): Route A is confirmed. Fork crosvm on WHPX (adding a headless display, gfxstream, Windows pmem DAX and a boot test), with a Cuttlefish `aosp_cf_x86_64_slim` API 36 guest, one crosvm process per Device and one Rust fleet daemon. The biggest risk, an API 36 boot on crosvm/WHPX, is unproven.
- [Memory budget](issues/09-memory-budget.md): 400 MB per Device split as VMM 30, kernel 25, daemons 25, SurfaceFlinger 20, zygote 40, system_server 90, app 170. Over budget means cutting in a fixed order: graphics, balloon, system_server trim, APEX DAX, zram. Still over 500 after that means re-grill.
- [Device networking](issues/14-device-networking.md): internet is on and isolated per Device (NAT through crosvm slirp). Host localhost is reachable at 10.0.2.2. There are offline and shaping switches for tests. Traffic capture (HTTPS via a test CA) is off unless an agent asks.
- [Structured agent API v1](issues/15-structured-agent-api-v1.md): all four layers are in v1, after computer use and the UI tree, in this order: logs and crash events, device controls (deep link, GPS, QR image, clock, permissions, install/clear), Hermes CDP app state, then network capture and mocking. Each is binary API calls plus matching MCP tools.
- [Fork design](issues/16-fork-design.md): Fork means pause, checkpoint and restore into a new crosvm process, copying RAM eagerly for now. v1 builds no Fork code but keeps 8 small seams. GPU snapshot is the blocker: the cross-domain blob path (needed for the memory budget) can't snapshot today. Aaron kept the blob path; GPU snapshot is solved later.
- [Guest image source](issues/12-guest-image-source.md): no slim prebuilt exists (last built 2023-07-06). The spike starts from the API 36 `aosp_cf_x86_64_only_phone` prebuilt 15581820, repacks system as uncompressed chunk-based erofs, and rebuilds the GKI kernel with `ZONE_DEVICE`/`FS_DAX`. The full slim build needs a 64 GB+ Linux builder. Route and slim target stand.
- [Boot proof spike plan](issues/13-boot-proof-spike-plan.md): stage 1 is crosvm/WHPX booting stock only_phone 15581820 to the first screen, with a gfxstream screenshot and host memory. Stage 2 is the DAX kernel plus erofs repack plus Windows pmem. Kill Route A after 3 failed fix attempts. Linux steps run in WSL2 Ubuntu on this PC.
- [Fleet manager](issues/18-fleet-manager.md): recommended default. The Rust daemon `agent-emud` runs one crosvm child per Device inside a Windows Job Object (memory cap, kill on exit). Each Device gets 2 vCPUs, idle Devices run at low priority, and a crashed Device is restarted once.
- [Viewer](issues/19-viewer.md): recommended default. A local web page served by `agent-emud` shows every Device live (MJPEG) and passes clicks and keys. It's a watcher until a human takes the lease.
- [Portability seams](issues/20-portability-seams.md): recommended default. crosvm's own hypervisor trait, one memory-trick trait (WHPX now, userfaultfd/KSM on Linux), and a cross-platform daemon.
- [Headless display](issues/10-computer-use-api-v1.md): done. `AGENT_EMU_HEADLESS` puts the crosvm GPU on the stub display; 0 windows, fast frames unchanged.
- [Stale-pixel fix](issues/10-computer-use-api-v1.md): done. HW overlays off (SurfaceFlinger 1008) at Device setup; fast frame equals `screencap` (67% of pixels off before).
- [Dialogs](issues/10-computer-use-api-v1.md): `hide_error_dialogs 1` at Device setup. Bluetooth stays enabled: disabling it crash-loops system_server.
- [Logs and crash events](issues/15-structured-agent-api-v1.md): `logs` and `crash_events` built from the host-side logcat stream; crash event 238 ms after `am crash`, ANR event seen.

## Destination reached

- [spec.md](spec.md): the build-ready spec, written for builder agents. It has a decisions table, architecture, a 9-step critical-path build order with exit checks, acceptance checks, 30 known traps, and §8 defaults that settle the contradictions between tickets. Execution has started: spike stage 1 (Boot proof spike plan).

## Execution so far (details in issues/13 and issues/10)

- **Stage 1 PASS:** crosvm fork boots API 36 on WHPX, and the proof app reaches its first screen (fork patches: sparse flag, run-mp, net skip; bootconfig vsock keys; frp partition).
- **Stage 2 PASS:** the DAX kernel (6.12.93: ZONE_DEVICE, FS_DAX, VMGENID) plus our Windows virtio-pmem port mounts `/system` from one shared read-only image with `dax=always`.
- **Slim images by target_files repack:** slim2 (low_ram, softer lmkd) and slim3 (no SystemUI). Smallest working guest: **896 MB**. 768 MB OOMs at boot (177 MB of kernel slab).
- **Fleet:** 4 Devices ran at once, ~1.2 GB each (Available and Committed agree). The 8- and 10-Device runs were stopped by memory pressure from other sessions' jest runs and the WSL VM. **400 MB per Device is not reached:** the realistic floor with this Android 16 guest is ~1.1 GB, because WHPX keeps guest RAM resident and Windows has no cross-VM page dedup.
- **Agent API:** the `agent-emud` daemon and MCP mode work. The fast path (virtio-input pipes, scanout frames) gives a 12 ms screenshot, a 0 ms tap ack, and **28 ms p95 from input to first frame**. Settle rule = first frame + 33 ms quiet.
- **Settle v2:** tap → first frame p50 27 / p95 56 ms (one cold tap; warm 24-43); HOME → first frame 36/37 ms; settled 206-355 ms. JPEG encode is 2 ms (`jpeg-encoder` SIMD). The 33 ms quiet window ends early on cold app launches, so the app-idle helper is needed.
- **crosvm fixes:** vsock log spam (GB per boot down to ~220 KB); net `start_queue` root cause (`workers.get(idx).is_some()` was always true); slirp TCP hostfwd. **adb works:** `adb connect 127.0.0.1:6520`, `getprop sys.boot_completed` = 1, `adb install` Success (13.3 s).
- **Trap:** slim3 has no wallpaper or SystemUI, so stale pixels stay in the scanout where no window draws (seen by eye in `assets/10-settle-v2/2-home.jpg`). Needs an opaque base layer or a scanout clear.
- **6 Devices at 896 MB: all apps alive, ~902 MB per Device** (Available; Committed ~1.1 GB). One screen had a Bluetooth crash dialog over the app.
- **Open:** app-idle helper; structured API layers 3-4; the `system_dlkm` swap.

## Not yet specified

Nothing. All fog has been graduated and decided.

## Out of scope

- **Full BoltBetz flow parity** (login, Plaid, wallet, QR, push). Aaron, 2026-10-07: get the app to its first screen first and work this out afterwards. It belongs to a later effort.
- **GMS, FCM and Play-dependent features.** The image is no-GMS by decision.
- **Metro and live reload.** Ruled out by Aaron.
- **Building the emulator.** This map ends at a spec.
