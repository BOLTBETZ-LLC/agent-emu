# Memory budget

Type: grilling
Status: resolved
Blocked by: 03, 05, 07, 08, 11

## Question

Split the 400 MB unique-memory budget per Device across its parts: VMM process, kernel, native daemons, zygote and boot image (shared or private), system_server, graphics buffers, and the proof app. Decide which cuts the spec commits to. Decide what the plan is if the measured app alone leaves too little room.

## Answer

Grilled with Aaron on 2026-10-07. Inputs: Minimum AOSP API 36 footprint, Baseline on stock emulator, GPU cost per Device, Shared system image on Windows, Lock the route.

**Target: 400 MB of unique memory per Device**, measured at the proof app's first screen.

| Part | Budget MB | Stock today | How it gets there |
|---|---|---|---|
| VMM process (crosvm + gfxstream) | 30 | ~2 GB above guest use (host 3.1-3.3 GB vs guest 1.1-1.35 GB) | Own VMM; gfxstream staging buffer cut from 128 to 16 MiB; blob/cross-domain buffers so screen buffers live in VRAM only |
| Kernel | 25 | ~243-277 MB "kernel" in dumpsys (includes page cache) | Trimmed config; free-page reporting returns cache |
| Native daemons | 25 | not split out | Slim product; code pages shared through DAX |
| SurfaceFlinger + graphics | 20 | not split out | Headless; blob buffers |
| zygote unique (incl. `boot.art`, which can't be shared) | 40 | zygote64 ~193 MB RSS | Code shared through DAX; only dirty pages stay private |
| system_server private | 90 | 372-411 MB RSS | Service trim (slim drops SystemUI and launcher; more cuts are spike work) |
| Proof app | 170 | 217-221 MB PSS, 365-368 MB RSS | App code and framework pages shared through DAX; rest is the app's own heap |
| **Total** | **400** | **3,129-3,311 host unique** | |

- Each part is a hard target. The spike measures each part against it.
- The tightest parts are system_server (90) and the app (170).
- **Over 400:** the spec commits to cutting in this order, stopping as soon as the total is under 400:
  1. Graphics: blob path plus a smaller staging buffer.
  2. Free-page reporting and balloon.
  3. system_server service trim.
  4. apexd DAX patch for APEX payloads.
  5. zram for cold pages.
- Still over 500 after all five: re-grill the budget.
- Not measured yet: every row's "budget" value. They are targets from estimates plus the stock baseline.
