# agent-emu

Label: wayfinder:map

## Destination

A build-ready spec for a slim Android emulator built for agents to test apps. The spec is done when nothing is left to decide before building a version where the proof app reaches its first screen with no more than 400 MB of unique RAM per Device, and 10 Devices run at once on Aaron's Windows 11 PC.

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

## Not yet specified

- **Fork and snapshot design**: memory layout, a shared read-only base image plus a per-Device overlay, and how resume works on WHPX without userfaultfd or KSM. Depends on what WHPX can do and on the route lock.
- **Image build pipeline**: how a trimmed AOSP API 36 x86_64 image is produced and kept reproducible. Which services are dropped depends on the footprint research.
- **Device lifecycle and fleet manager**: start, stop, health, resource caps across 10 Devices, and per-Device CPU pinning or shares.
- **Networking per Device**: NAT, reaching the staging backend, and isolating Devices from each other.
- **Viewer design**: how a human attaches to a headless Device.
- **MCP layer shape**: tool names and granularity over the binary API.
- **Structured agent API layers beyond computer use**: UI tree, Hermes/JS state, network capture, storage, logs, clock.
- **Portability seams**: what keeps the Linux and macOS ports open.
- **Spec document structure**: where the final spec lives and its sections.

## Out of scope

- **Full BoltBetz flow parity** (login, Plaid, wallet, QR, push). Aaron, 2026-10-07: get the app to its first screen first and work this out afterwards. It belongs to a later effort.
- **GMS, FCM and Play-dependent features.** The image is no-GMS by decision.
- **Metro and live reload.** Ruled out by Aaron.
- **Building the emulator.** This map ends at a spec.
