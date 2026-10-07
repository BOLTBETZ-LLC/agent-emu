# Computer-use fast path

Type: research
Status: resolved
Blocked by:

## Question

How do frames get from a guest GPU path (gfxstream or virtio-gpu on Windows) to an agent, and how do inputs get back in, with a round trip under 50 ms? Answer these:

- Readback cost of a 1080x2400 frame from a host GPU texture. Encoding options and their latency and size: raw, PNG, WebP, JPEG, delta frames.
- Input injection via virtio-input: latency, and multitouch support.
- How fast is the stock emulator's gRPC screenshot and input path today (the published or measured baseline)?
- How do existing agent tools (mobile-mcp, Android CLI, agent-device) get frames, and what latencies do they report?

## Answer

Reconciled 2026-10-07 from two blind passes. Contested claims re-checked against primary source (gh api).

- Under 50 ms is plausible but unproven. Biggest cost is the guest's own render: Android needs at least 2 vsync frames input-to-display, 33 ms at 60 Hz (AOSP https://source.android.com/docs/core/graphics/implement-vsync). Host-side work adds ~5 ms (estimate, Pass A).
- Frames: read the last posted color buffer on the host through gfxstream `FrameBuffer::getScreenshot`. It scales on the GPU (`readToBytesScaled`) and waits on the post worker synchronously. No adb, no guest round trip (https://github.com/google/gfxstream/blob/main/host/frame_buffer.cpp, checked).
- Do not hook the async readback worker (post callback) for action results. It runs a mailbox pipeline with "+1 frame of lag" by design (https://github.com/google/gfxstream/blob/main/host/gl/readback_worker_gl.cpp, checked). That path is fine for a viewer.
- Every frame carries generation, capture time and frame age, so an action result never silently reuses a pre-action frame (Pass B design point).
- Raw 1080x2400 RGBA8 = 10,368,000 bytes (calculation). Readback ms on the RTX 5060 Ti is unmeasured (estimates 0.2-1 ms, both passes).
- Local binary clients get raw pixels in shared memory, double-buffered with a sequence number so readers never tear. The stock emulator's MMAP transport warns it can tear (emulator_controller.proto).
- Model-facing default: JPEG q75. Measured by Pass A on this PC with real app screenshots: 706x1568 in 1.8 ms / 61 KB; 1080x2400 in 7.1 ms / 122 KB. PNG costs 11-45 ms with outliers to 230 ms; lossy WebP is 2-10x slower than JPEG. Not re-run here.
- Size is an API parameter, and the API returns the scale factor. Claude Opus 4.7+ takes 1080x2400 as-is (3354 tokens); older Claude needs 706x1568 (Claude computer-use docs, per Pass A). Add `zoom(rect)`: gfxstream `getScreenshot` takes a `rect` (checked).
- No delta frames for models; they need whole images. Use host-side change detection only for "settled?". Tile deltas for binary clients are deferred.
- Input: a persistent Rust endpoint writes 8-byte `virtio_input_event`s into crosvm's socket event source, MT protocol B, ending each batch with `SYN_REPORT`. Multitouch works end to end (crosvm `defaults.rs` `new_multi_touch_config`, Linux `virtio_input.c` `input_mt_init_slots`). Latency <1 ms is an estimate, unverified.
- Swipes and pinches are timed event streams generated on the host. Report injection ack separately from gesture completion (both passes).
- Stock baseline: gRPC screenshot 19.94 ms mean, sd 21.47 ms; TOUCH 410.50 µs, LIFT 412.30 µs. Measured on API 30, 1080x1920, Linux/KVM in 2024. Historical, not Windows (Mobile-Env README https://github.com/X-LANCE/Mobile-Env/blob/master/README.md, checked).
- Today's agent tools are slower. auto-mobile `observe` 440/532/543 ms p50/p95/p99 (https://github.com/kaeawc/auto-mobile/issues/8758, checked). agent-device sleeps 1000 ms before each screenshot by default (checked). mobilecli tap 4.0 ms median after PR #412, no app reaction included (checked). mobilecli JPEG screenshot ~1.5 s vs 2.4 s for screencap PNG (PR #368, checked). scrcpy streams video at 35-70 ms (README, per Pass A).
- Acceptance metric: report 3 timings with p50/p95/p99 at 1 Device and at 10 (Pass B proposal). (1) screenshot request to image; (2) input request to injection ack; (3) input request to a frame showing the app's response.

### Latency budget (tap, then screenshot of the result, 60 Hz guest, animations off)

| Step | ms | Measured or estimate | Source |
|---|---|---|---|
| Agent -> host daemon (local socket, binary API) | 0.1-0.5 | estimate | Pass A |
| Host -> guest touch (virtio-input queue + IRQ) | <1 | estimate | crosvm `event_source.rs`; stock gRPC touch is 0.41 ms measured (Mobile-Env), a different path |
| Guest input -> app -> SurfaceFlinger -> post | >=33 (2 frames @60 Hz); ~17 @120 Hz | estimate from documented floor; 120 Hz unverified | AOSP implement-vsync |
| Wait for new-frame post | 0 extra if event-driven via `getScreenshot`; +16.7 if read through the async readback worker | code-checked mechanism, ms is arithmetic | gfxstream `frame_buffer.cpp`, `readback_worker_gl.cpp` |
| GPU scale + readback to host RAM | 0.2-1 | estimate | Pass A (PCIe math), Pass B (10 GB/s math) |
| JPEG q75 encode, 706x1568 (1080x2400) | 1.8 (7.1) | measured, Pass A, this PC | Pass A Evidence E3 |
| Base64 + MCP/stdio hand-off ~61 KB | <1 | estimate | Pass A |
| **Total** | **~37-41 @60 Hz; ~21-25 @120 Hz** | estimate | sum; ~54-58 if the lagged readback path is used |

A screenshot with no input ("what is on screen now") costs ~3-5 ms: readback plus encode (estimate).

### Disagreements

- **Stock emulator latency.** A: "no published numbers". B: Mobile-Env, 19.94 ms screenshot, ~0.41 ms touch. **B wins.** The Mobile-Env README table is real (checked). It is old and on Linux, so it is a lower-bound reference only. B notes the paper says TOUCH 419.50 µs, the README 410.50 µs; README checked, paper not.
- **mobile-mcp / mobilecli latency.** A: "none published". B: tap 20.2 -> 4.0 ms median (PR #412), screenshot ~1.5 s vs 2.4 s (PR #368). **B wins.** Both PR bodies say this (checked). Taps hit a dead region, so app reaction is excluded.
- **Frame lag.** A: readback is direct, 0 extra wait. B: gfxstream adds one frame of lag. **Both right, on different paths.** `getScreenshot` reads the last posted buffer synchronously. The async readback worker's mailbox has "+1 frame of lag" (both checked). Verdict: action results use the `getScreenshot` path.
- **Encode cost and size.** A: measured (JPEG 1.8 ms / 61 KB at 706x1568). B: unverified, no numbers. **A wins.** Only A measured. Its bench script was not committed and was not re-run here.
- **agent-device 1000 ms wait.** A: on by default. B: optional. **A wins on the default.** `screenshotAndroid` sleeps `ANDROID_SCREENSHOT_SETTLE_DELAY_MS = 1_000` unless `stabilize === false` (checked). B is right that callers can turn it off.
- **GPU readback ms.** A: 0.2-0.6 ms. B: ~1.04 ms at a hypothetical 10 GB/s. **Unresolved.** Both are arithmetic, not measurement. To settle it: time `getScreenshot` at 706x1568 and 1080x2400 on the RTX 5060 Ti under WHPX in the spike.
- **Delta frames.** A: useless to models. B: tile deltas with a base generation. **A wins for model input**, because model APIs take whole images. B's deltas are only for binary or viewer clients, and B also says defer them. Deferred.

Context: Pass A (Claude) `C:/dev/worktrees/agent-emu--research-computer-use-path/.scratch/agent-emu/research/06-computer-use-fast-path.md`, branch `research/computer-use-path`. Pass B (GPT-6.1 Sol) `C:/dev/worktrees/agent-emu--codex-computer-use-fast-path/.scratch/agent-emu/research/06-computer-use-fast-path.codex.md`, branch `research/codex-computer-use-fast-path`, commit 01ed811.
