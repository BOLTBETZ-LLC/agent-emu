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
- **Standing rules**: use sources from the last 6 months where possible, and every claim cites a URL. Never run a full test suite or a heavy build in parallel; at most 2 heavy jobs run at once.

## Decisions so far

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
