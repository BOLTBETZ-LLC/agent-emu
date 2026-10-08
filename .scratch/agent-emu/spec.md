# agent-emu build spec

Reader: builder agents. Terms follow `GLOSSARY.md`. Every number comes from the linked ticket. "Estimate" and "unverified" mean exactly that.

## 1. Summary

agent-emu is a slim Android emulator whose only job is letting agents test apps.
It is a fork of public crosvm running on WHPX (Windows Hypervisor Platform), with one crosvm process per Device and one Rust daemon, `agent-emud`, that owns the Fleet.
The guest is AOSP API 36 (Android 16), no GMS, headless, rendered on the host GPU through gfxstream.
The system image is one read-only file, shared by every Device through virtio-pmem DAX, so its pages count once for the Fleet.
Agents drive Devices through a binary API with MCP on top: computer-use control first, then the structured agent API.
Finish line: the proof app (EAS `183b566c-2d7c-448a-ad2e-fb1ec459374d`, v1.4.0 (22), staging) reaches its first screen, logged out, with ≤400 MB unique memory per Device, 10 Devices at once, on Aaron's PC (32 GB RAM, Core Ultra 7 265, RTX 5060 Ti 16 GB, Windows 11 Home).
Computer-use round trip target: under 50 ms.
Fork is designed in (8 seams) but not built.
Biggest risk, unproven: whether crosvm on WHPX boots API 36 at all. The spike (build step 1) tests that first.
Speed rule (Aaron, 2026-10-07): critical paths only. No broad test suites, no extra audits, no polish passes.

## 2. Decisions

| # | Decision | Ticket |
|---|---|---|
| D1 | Destination is this spec. RAM is unique memory per Device. Finish line is the proof app's first screen, logged out. 10 Devices at once. | [map](map.md) |
| D2 | Windows first; design stays portable to Linux and macOS. New code in Rust. | [map](map.md), [20](issues/20-portability-seams.md) |
| D3 | API 36, no GMS. No Metro: the JS bundle ships inside the APK. | [map](map.md), [04](issues/04-proof-app-without-gms.md) |
| D4 | Route A: fork public crosvm, run on WHPX. Fork, not build on rust-vmm crates. | [08](issues/08-lock-the-route.md), [01](issues/01-crosvm-on-whpx.md) |
| D5 | One crosvm process per Device; one Rust daemon `agent-emud` owns the Fleet. | [08](issues/08-lock-the-route.md), [18](issues/18-fleet-manager.md) |
| D6 | WHPX can share a read-only base across VMs. No KSM-style dedup: budget without dedup. | [02](issues/02-whpx-memory-capabilities.md) |
| D7 | Shared system image via virtio-pmem DAX, not virtio-fs. Needs a crosvm Windows pmem port. | [11](issues/11-shared-system-image-on-windows.md) |
| D8 | Spike guest: `aosp_cf_x86_64_only_phone` API 36 prebuilt 15581820. Slim (`aosp_cf_x86_64_slim`) stays the memory target, built later from `android-16.0.0_r4`. | [12](issues/12-guest-image-source.md), [03](issues/03-minimum-aosp-footprint.md) |
| D9 | Custom GKI android16-6.12 kernel with `ZONE_DEVICE`, `FS_DAX`, `VIRTIO_PMEM` built in, `VMGENID`. | [13](issues/13-boot-proof-spike-plan.md), [16](issues/16-fork-design.md) |
| D10 | Drawing turned on (`debug.hwui.drawing_enabled`). | [03](issues/03-minimum-aosp-footprint.md), [05](issues/05-baseline-on-stock-emulator.md) |
| D11 | Graphics: gfxstream, cross-domain blob buffers, staging buffer cut from 128 to 16 MiB. | [07](issues/07-gpu-cost-per-device.md), [09](issues/09-memory-budget.md) |
| D12 | Memory budget: 400 MB split in 7 parts; fixed cut order if over; re-grill if still over 500. | [09](issues/09-memory-budget.md) |
| D13 | Frames come from gfxstream `getScreenshot`, JPEG q75, never from the async readback worker. | [06](issues/06-computer-use-fast-path.md), [10](issues/10-computer-use-api-v1.md) |
| D14 | Computer-use API v1 surface, settled rule, leases. | [10](issues/10-computer-use-api-v1.md) |
| D15 | Structured agent API: 4 layers, all in v1, in a fixed order after computer use and the UI tree. | [15](issues/15-structured-agent-api-v1.md) |
| D16 | Networking: per-Device NAT via crosvm slirp, isolated, host at `10.0.2.2`, test switches, capture off by default. | [14](issues/14-device-networking.md) |
| D17 | Fork: no Fork code in v1, only 8 seams. Keep the cross-domain blob path; GPU snapshot solved later. | [16](issues/16-fork-design.md) |
| D18 | Spike in 2 stages; kill Route A after 3 failed fix attempts on stage 1. Linux steps in WSL2 Ubuntu on this PC. | [13](issues/13-boot-proof-spike-plan.md) |
| D19 | Viewer: local web page from `agent-emud`, MJPEG grid, watcher until a human takes the lease. | [19](issues/19-viewer.md) |
| D20 | Portability seams: crosvm hypervisor trait, one memory-trick trait, cross-platform daemon. | [20](issues/20-portability-seams.md) |
| D21 | Baseline: stock emulator costs 3.1-3.3 GB unique per Device; proof app runs with no GMS, 0 crashes. | [05](issues/05-baseline-on-stock-emulator.md) |
| D22 | Spec shape and test scope: critical checks only. | [17](issues/17-spec-document-shape.md) |

## 3. Architecture

### 3.1 Host processes

| Process | Count | Role |
|---|---|---|
| `agent-emud` (Rust, tokio + std only) | 1 | Owns the Fleet: start, stop, health, leases, binary API, MCP, viewer page, saved Device config files. |
| crosvm (fork) | 1 per Device | One VM. Child of `agent-emud`, inside a Windows Job Object that caps its memory and kills it when the daemon exits. |

- Why one process per Device: Windows can't map one process's memory into several partitions (OpenVMM `membacking/src/lib.rs` L84-86), and a crash stays contained. [08](issues/08-lock-the-route.md)
- Windows crosvm spawns block, net, slirp, snd and GPU as child processes (`broker.rs`). A "crosvm process" here means that process tree. [16](issues/16-fork-design.md)
- Per Device: 2 vCPUs by default. Idle Devices run at below-normal priority. [18](issues/18-fleet-manager.md)
- Health: crosvm alive, `sys.boot_completed` set, a frame posted in the last N s (N not fixed in the ticket; builder picks and records it). Crash: restart once, then report. [18](issues/18-fleet-manager.md)
- Device config (image, RAM, screen, network, plus the Fork seam fields in 3.2) is one small, serialized, versioned file the daemon saves. [16](issues/16-fork-design.md), [18](issues/18-fleet-manager.md)
- Process caps sit behind a small process-cap trait (Job Objects now, cgroups on Linux). [20](issues/20-portability-seams.md)

### 3.2 crosvm fork changes

Base: public crosvm, source anchor `1ca5899813e21674d63b7888252a2ffd3c5f2c93`. Build: `cargo build --features all-msvc64,whpx` plus the gfxstream backend (gfxstream is not in `all-msvc64`; rutabaga_gfx links an external `gfxstream_backend` you build yourself; Windows build unverified). [01](issues/01-crosvm-on-whpx.md), [13](issues/13-boot-proof-spike-plan.md)

| Change | Why | Ticket |
|---|---|---|
| Headless display backend | Windows virtio-gpu hard-wires `vec![virtio::DisplayBackend::WinApi]` and always opens a window (`devices/src/virtio/vhost_user_backend/gpu/sys/windows.rs` ~L293). | [01](issues/01-crosvm-on-whpx.md) |
| gfxstream backend on Windows | Only accelerated backend on a Windows host (Venus needs Linux/Android host). | [01](issues/01-crosvm-on-whpx.md), [07](issues/07-gpu-cost-per-device.md) |
| Cross-domain blob context, blob + host-visible | Screen buffers live in VRAM only. Without it: guest RAM + host `mLinear` + VRAM triple copy, ~240-260 MiB graphics host RAM vs ~140-160 MiB (estimates, with 128 MiB staging). | [07](issues/07-gpu-cost-per-device.md) |
| gfxstream staging buffer 128 → 16 MiB | `kDefaultStagingBufferSize` is a `constexpr` (`vk_common_operations.h:723`), allocated per `VkEmulation` in host RAM (type 3, system heap on this PC). Correctness at a smaller size is unverified. | [07](issues/07-gpu-cost-per-device.md), [09](issues/09-memory-budget.md) |
| Keep `prefersDedicatedAllocation` honoured | Source warns of zero readback for some non-dedicated ColorBuffers on NVIDIA/Windows. | [07](issues/07-gpu-cost-per-device.md) |
| Readback on demand, freed when idle | The post callback allocates 9.89 MiB while registered; GL readback worker adds up to 39.55 MiB. | [07](issues/07-gpu-cost-per-device.md) |
| Windows virtio-pmem with DAX | Missing on Windows. Map one read-only image file per Device VMM via `WHvMapGpaRange` Read\|Execute, no Write. Fix the `PAGE_READWRITE` section protection (`base/src/sys/windows/mmap.rs` L186). Fill the empty Windows `MemoryMappingArena` stub (`mmap_platform.rs` L227-230). Handle or exclude `MadvisePageout`, `MsyncArena`, `MadviseRemove` (`pmem.rs` L130, L161, L188). Port size: unresolved (estimate "a few hundred lines" vs larger). | [11](issues/11-shared-system-image-on-windows.md) |
| Drawing on | Set `debug.hwui.drawing_enabled` true before the app launches (only_phone already has it on; slim ships it off). | [03](issues/03-minimum-aosp-footprint.md), [12](issues/12-guest-image-source.md) |
| Input endpoint | Persistent Rust endpoint writes 8-byte `virtio_input_event`s into crosvm's socket event source, MT protocol B, each batch ends with `SYN_REPORT`. Multi-touch config: `new_multi_touch_config`. | [06](issues/06-computer-use-fast-path.md) |
| Boot test | A CI boot test (upstream has no Windows boot job; Windows builder off since 2025-03-13). | [01](issues/01-crosvm-on-whpx.md), [08](issues/08-lock-the-route.md) |
| Memory-trick trait | Windows: section-backed RAM, `GpaAccessFaultExit` lazy fill, `OfferVirtualMemory`. Linux later: memfd, userfaultfd, KSM. No WHPX calls outside the fork. | [20](issues/20-portability-seams.md) |

Fork seams (v1 builds these and nothing else for Fork). [16](issues/16-fork-design.md)

1. One guest-RAM backing type. v1 ships only the existing pagefile section. Balloon must not hard-wire to the pagefile kind. File-backed `open` stays `unimplemented!()`.
2. Device config is one serialized, versioned value the daemon stores: vCPUs, RAM size, GPA layout, device list and order, PCI slots, negotiated features. No random or host-dependent values in device setup.
3. No guest-visible state outside crosvm and the disk files. Drop or list any kept Cuttlefish host daemon (e.g. `secure_env`, modem simulator).
4. Writable disks are plain per-Device files the daemon owns, separate from the shared pmem image. No composite disk.
5. Per-Device isolated NAT (3.6).
6. Guest kernel: `CONFIG_VIRT_DRIVERS=y`, `CONFIG_VMGENID=y`, ACPI on (3.4).
7. Daemon treats a dropped vsock/adb as normal and reconnects.
8. GPU mode is a per-Device launch value, not hard-wired.

Not v1: VMGenID device, fault-exit handler, dirty tracking, file-backed RAM, moving devices in-process, GPU snapshot code.

### 3.3 Guest image

| Phase | Image | Notes |
|---|---|---|
| Spike (stages 1-2) | `aosp_cf_x86_64_only_phone-userdebug` API 36, build **15581820** (2026-06-06, `BP4A.251205.006`, kernel 6.12.38-android16-5), from `aosp-android-latest-release`. Pin it. | Download `img.zip`, `target_files.zip`, `otatools.zip` (~3.8 GB). Drawing already on. [12](issues/12-guest-image-source.md) |
| Later (memory cut 3) | `aosp_cf_x86_64_slim` built from `android-16.0.0_r4` | No slim prebuilt since 2023-07-06. Needs a 64 GB RAM, 400 GB disk Linux builder (cloud). Drops SystemUI, Launcher3, Settings, bundled apps; `FakeSystemApp` supplies HOME. Turn drawing back on. [12](issues/12-guest-image-source.md), [03](issues/03-minimum-aosp-footprint.md) |

- Both build `system` from the same makefiles, so the pmem/DAX/erofs work carries over. [12](issues/12-guest-image-source.md)
- Spike scope: only `system` moves to pmem. system_ext/product/vendor stay as shipped in `img.zip`. No APEX DAX patch. [12](issues/12-guest-image-source.md), [13](issues/13-boot-proof-spike-plan.md)
- No GMS, nothing faked or stubbed. Launch fresh (fresh install or `pm clear`), airplane mode. [04](issues/04-proof-app-without-gms.md), [05](issues/05-baseline-on-stock-emulator.md)

### 3.4 Kernel config

Build in WSL2 Ubuntu: `repo init -b common-android16-6.12` (pin a dated branch), then
`tools/bazel run --defconfig_fragment=//common:agent_emu_dax_defconfig //common-modules/virtual-device:virtual_device_x86_64_dist`. [12](issues/12-guest-image-source.md)

Fragment `agent_emu_dax_defconfig`:

| Option | Value | Why | Ticket |
|---|---|---|---|
| `CONFIG_ZONE_DEVICE` | y | Off in GKI today (`# CONFIG_ZONE_DEVICE is not set` in CI kernel 16542785). Bool, needs vmlinux rebuild. | [12](issues/12-guest-image-source.md) |
| `CONFIG_FS_DAX` | y | Bool, no module can add it. | [11](issues/11-shared-system-image-on-windows.md) |
| `CONFIG_VIRTIO_PMEM` | y (built in) | `/system` must mount from pmem in first stage. See trap T19 on the `=m` match check. | [13](issues/13-boot-proof-spike-plan.md) |
| `CONFIG_VIRT_DRIVERS` | y | No default; needed for VMGENID. | [16](issues/16-fork-design.md) |
| `CONFIG_VMGENID` | y | Fork seam 6. ACPI on. | [16](issues/16-fork-design.md) |

- Kernel swap: new `bzImage` + `initramfs.img`, plus rebuilt `vendor_dlkm`/`system_dlkm` (old modules don't match). Repack with `otatools` (`mkbootimg`, `lpmake`) or boot the kernel directly. [12](issues/12-guest-image-source.md)
- Unresolved: whether GKI KMI/ABI checks pass with `ZONE_DEVICE` on. The first Kleaf build settles it. [12](issues/12-guest-image-source.md)
- Cost: `struct page` metadata is ~16 MiB guest RAM per GiB of pmem per Device. [11](issues/11-shared-system-image-on-windows.md)

### 3.5 erofs and storage stack

Repack `system` from `target_files` (not from `system.img`). Edit `META/misc_info.txt`, delete `IMAGES/system.img`, run `add_img_to_target_files`. [12](issues/12-guest-image-source.md)

| Setting | Value | Why |
|---|---|---|
| `erofs_default_compressor` | `none` | Default is `lz4hc,9`. Only FLAT_PLAIN or CHUNK_BASED files get `S_DAX`. |
| `erofs_share_dup_blocks` | `true` | Adds `--chunksize 4096`: every regular file is CHUNK_BASED, never inline. |
| `avb_system_hashtree_enable` | `false` | dm-verity has no DAX. |
| `erofs_sparse_flag` | removed | Raw image for pmem. |

- Fallback if the above fails: `mkfs.erofs -b 4096 -E noinline_data` (no `-z`) with the `META` fs_config and file_contexts. [12](issues/12-guest-image-source.md)
- `vendor_boot` first-stage fstab: `/system` on pmem, `ro,dax=always`, remove **both** `avb=vbmeta_system` and `avb_keys=/avb`. Regenerate or disable vbmeta. Proposed line (unverified, never booted): `/dev/block/pmem0 /system erofs ro,dax=always wait,first_stage_mount`. [11](issues/11-shared-system-image-on-windows.md), [12](issues/12-guest-image-source.md)
- No dm-verity, no loop. Verify the image on the host before exposing it. [11](issues/11-shared-system-image-on-windows.md)
- Unresolved: whether deduped chunks behave under DAX. A boot-time `S_DAX` check plus reading a shared-chunk file settles it. [12](issues/12-guest-image-source.md)

### 3.6 Networking

[14](issues/14-device-networking.md)

- Default: internet on, NAT per Device through crosvm slirp. Devices can't see each other.
- Host localhost at `10.0.2.2` (for local mock servers).
- Per Device and per call: offline switch, slow or lossy shaping.
- Traffic capture: off unless an agent turns it on for a Device. Then requests/responses are recorded on the host, HTTPS via a test CA the guest trusts, and the agent can mock or fail calls.
- Clones later keep MAC and IP; per-Device NAT stops collisions. [16](issues/16-fork-design.md)
- Unverified: slirp throughput/latency on Windows crosvm, and whether crosvm's net device can shape. Check in the spike.
- Proof-app runs use airplane mode (trap T11).

### 3.7 Computer-use API v1

[10](issues/10-computer-use-api-v1.md), [06](issues/06-computer-use-fast-path.md)

| Item | Rule |
|---|---|
| Calls (binary API = MCP tools, each takes a device id) | `screenshot`, `tap`, `long_press`, `swipe`, `type_text`, `key` (back, home, enter, ...), `gesture` (multi-finger, e.g. pinch), `ui_tree`, `zoom(rect)` |
| Action reply | Every input call returns a settled screenshot. Per-call opt-out returns only the input ack. |
| Settled | No new frame posted for N ms (host side) AND app idle (main thread and JS queue idle, no animations). A small in-guest helper reports app idle. N is not fixed in the ticket; builder picks and records it. |
| Deadline | Default 3 s, overridable per call. At deadline: latest frame, `settled=false`, reason `frames_changing` or `app_busy`. Not an error. |
| Frame | 1080x2400 JPEG q75 default (~7.1 ms encode, ~122 KB, measured on this PC). `size` param (e.g. 706x1568: 1.8 ms / 61 KB). Each frame carries generation, capture time, scale factor. Source: gfxstream `FrameBuffer::getScreenshot` (synchronous, GPU-scaled). Never the async readback worker. |
| Coordinates | Pixels of the frame the agent received. Server maps via the frame's scale factor. |
| Gestures | Host-generated timed event streams over virtio-input, MT protocol B. Injection ack reported separately from gesture completion. |
| UI tree | Separate `ui_tree` call only; not in action replies. |
| Animations | Window, transition and animator scales 0 by default. A Device setting turns them on. |
| Lease | One agent per Device. Other agents' input gets `busy`. Watchers can still screenshot. |
| Local binary clients | Raw pixels in shared memory, double-buffered with a sequence number (no tearing). |
| Deltas | None for models. Host-side change detection only for "settled?". Tile deltas deferred. |

Latency budget (tap then screenshot, 60 Hz, animations off; all estimates except encode): agent→daemon 0.1-0.5, host→guest touch <1, guest input→post ≥33 (2 vsync frames), wait 0 via `getScreenshot`, GPU scale + readback 0.2-1 (unmeasured), JPEG 1.8 (7.1 at full size), hand-off <1. Total ~37-41 ms. Using the lagged readback path gives ~54-58 ms. [06](issues/06-computer-use-fast-path.md)

### 3.8 Structured agent API layers

Built after computer use and `ui_tree`, in this order. Each layer = binary API calls + matching MCP tools taking a device id. Exact call names and schemas are builder-level. [15](issues/15-structured-agent-api-v1.md)

1. Logs and crash events: logcat streamed and filtered to the app; pushed crash and ANR events.
2. Device controls: deep links and intents; GPS; camera image injection (QR codes); clock freeze and advance; permission grants; app install and clear-data.
3. App JS state via Hermes CDP: console, JS errors, evaluate, read the Redux store. Without Metro, through the in-app inspector (to be checked).
4. Network capture and mocking: list requests, mock, fail. Needs the test CA plus a host proxy (3.6).

### 3.9 Viewer

[19](issues/19-viewer.md)

- `agent-emud` serves `http://127.0.0.1:<port>/`: grid of every Device, live MJPEG from the same `getScreenshot` path, plus clicks and keys. Nothing to install.
- Watcher only; acts only when a human takes the lease.
- Frames are produced only while someone watches.
- An attached host swapchain would cost ~29.7 MiB VRAM per Device; headless costs 0. [07](issues/07-gpu-cost-per-device.md)

### 3.10 Memory budget

Target: 400 MB unique memory per Device at the proof app's first screen. Each row is a hard target; every value is a target from estimates plus the stock baseline, not a measurement. [09](issues/09-memory-budget.md)

| Part | Budget MB | Stock today | How |
|---|---|---|---|
| VMM process (crosvm + gfxstream) | 30 | ~2 GB above guest use | Own VMM; staging 128 → 16 MiB; blob/cross-domain buffers |
| Kernel | 25 | ~243-277 MB "kernel" in dumpsys (incl. page cache) | Trimmed config; free-page reporting returns cache |
| Native daemons | 25 | not split out | Slim product; code pages shared via DAX |
| SurfaceFlinger + graphics | 20 | not split out | Headless; blob buffers |
| zygote unique (incl. `boot.art`) | 40 | zygote64 ~193 MB RSS | Code via DAX; only dirty pages private |
| system_server private | 90 | 372-411 MB RSS | Service trim |
| Proof app | 170 | 217-221 MB PSS, 365-368 MB RSS | App and framework code via DAX |
| **Total** | **400** | **3,129-3,311 host unique** | |

Over 400: cut in this order, stop once under 400.
1. Graphics: blob path plus smaller staging buffer.
2. Free-page reporting and balloon.
3. system_server service trim.
4. apexd DAX patch for APEX payloads.
5. zram for cold pages.

Still over 500 after all five: stop and re-grill the budget.

Known gaps behind the rows: NVIDIA per-process driver baseline is unknown ("likely tens of MB") and lands in the VMM row [07](issues/07-gpu-cost-per-device.md). WHPX has no free-page reporting API; crosvm has no WHPX reporting path (VMM job, unverified) [02](issues/02-whpx-memory-capabilities.md). DAX savings are 0 MB verified; estimate 70-185 MB per Device (120-300 MB with the APEX patch) [11](issues/11-shared-system-image-on-windows.md).

How to measure unique memory: host working-set private of the Device's whole crosvm process tree, as in the baseline (`assets/05-baseline/measure.sh`) [05](issues/05-baseline-on-stock-emulator.md). For the shared pmem image, confirm pages are shared with `QueryWorkingSetEx` or RAMMap across 2 Devices [11](issues/11-shared-system-image-on-windows.md). Guest split: `dumpsys meminfo`, `showmap` for zygote64 and system_server, `/proc/meminfo` [03](issues/03-minimum-aosp-footprint.md).

## 4. Build order

Critical path only. Each step stops at its exit check. At most 2 heavy jobs at once. Linux-only steps run in WSL2 Ubuntu on this PC (add the distro first; WSL2 today has only `docker-desktop`).

| Step | Work | Exit check |
|---|---|---|
| 1. Spike stage 1: boot proof | At minute 0, in parallel: (a) build the crosvm fork `--features all-msvc64,whpx` + gfxstream backend + headless display on this PC; (b) download 15581820 `img.zip`, `target_files.zip`, `otatools.zip` into WSL2. Boot the stock only_phone image on crosvm/WHPX, headless. Install the proof APK, airplane mode, launch fresh. | Proof app first screen seen in a gfxstream screenshot that was looked at. Host unique memory of the crosvm process tree recorded. Boot time recorded. **Kill rule:** 3 distinct fix attempts fail (real error recorded each time) → stop, re-grill the route (fallbacks: OpenVMM base, or a Linux host in WSL2). [13](issues/13-boot-proof-spike-plan.md) |
| 2. Spike stage 2: DAX | Only after step 1 passes. Kleaf-build the kernel (3.4). Repack `system` (3.5) and `vendor_boot` with the pmem fstab. Rebuild dlkm images. Add Windows virtio-pmem to the fork (3.2). | `S_DAX` confirmed on a `/system` file. First screen again. Host unique memory for 2 Devices shows the image pages shared. [13](issues/13-boot-proof-spike-plan.md) |
| 3. Daemon + computer-use API v1 | `agent-emud` (3.1) with Job Object per Device, leases, binary API, MCP tools (3.7), input endpoint, settled detection, animations off, Fork seams 2, 4, 7, 8 in the config/launch path. | An agent, via MCP, gets a settled first-screen frame and does one tap that returns a settled frame. Round trip at 1 Device: 3 timings with p50/p95/p99 recorded (section 5, check C). |
| 4. Memory cuts | Measure each budget row. If over 400, apply cuts in the fixed order (3.10), re-measure after each, stop when under. Cut 3 may need the slim build on a 64 GB+ cloud builder. | ≤400 MB unique for one Device at first screen (section 5, check B). Over 500 after cut 5 → re-grill. |
| 5. 10 Devices at once | Start 10 Devices from `agent-emud`. | All 10 at first screen at once, each ≤400 MB unique; round trip timings at 10 Devices (section 5, checks C and D). |
| 6. Structured layer 1: logs and crash events | 3.8 item 1. | Agent receives the app's filtered logcat and one pushed crash event. |
| 7. Structured layer 2: device controls | 3.8 item 2. | Each control called once via MCP; result visible in a settled frame or the call reply. |
| 8. Structured layer 3: Hermes CDP | 3.8 item 3. | Agent evaluates JS and reads the Redux store from the proof app without Metro. |
| 9. Structured layer 4: network capture and mocking | 3.8 item 4 (test CA + host proxy). | With capture on, agent lists one proof-app request and fails one call. |

Off the critical path (build beside, never blocking): the viewer (3.9), network shaping/offline switches (3.6), the CI boot test.

## 5. Acceptance checks

Only these. No broad suites.

| Check | Pass | How | Step |
|---|---|---|---|
| A. First screen | Proof app shows login v2 `Start`, logged out, seen in a screenshot that someone looked at. In airplane mode the "No Internet Connection" / "Try Again" dialog sits on top; that counts (as in the baseline). | gfxstream `getScreenshot` frame; UI tree text as backup. [05](issues/05-baseline-on-stock-emulator.md), [04](issues/04-proof-app-without-gms.md) | 1, 2, 4, 5 |
| B. Memory | ≤400 MB unique memory per Device at first screen. | 3.10 method. | 4, 5 |
| C. Round trip | Under 50 ms. Report p50/p95/p99 at 1 and at 10 Devices for: (1) screenshot request → image; (2) input request → injection ack; (3) input request → settled frame. | Timestamps at the agent side of the binary API. [10](issues/10-computer-use-api-v1.md) | 3, 5 |
| D. 10 at once | 10 Devices running at once, each passing A and B. | Same host, same run. | 5 |

Not fixed by the tickets: which percentile must be under 50 ms, and the settle window N (which adds to timing 3). Record both with the numbers.

## 6. Known traps

| # | Trap | Fix | Ticket |
|---|---|---|---|
| T1 | ATD and slim ship `debug.hwui.drawing_enabled=0`: apps draw nothing, `screencap` is all black. | `setprop debug.hwui.drawing_enabled true` before launch. Discard any numbers taken with drawing off. | [05](issues/05-baseline-on-stock-emulator.md), [03](issues/03-minimum-aosp-footprint.md) |
| T2 | Git Bash rewrites `/sdcard/...` and other `/`-leading args to adb. | `MSYS_NO_PATHCONV=1`. | [05](issues/05-baseline-on-stock-emulator.md) |
| T3 | crosvm Windows file mapper hardcodes `PAGE_READWRITE` for the file section (`mmap.rs` L186). | Change the section protection for the read-only image. | [11](issues/11-shared-system-image-on-windows.md) |
| T4 | crosvm virtio-gpu on Windows always opens a window (`DisplayBackend::WinApi` hard-wired). | Add a headless backend. | [01](issues/01-crosvm-on-whpx.md) |
| T5 | gfxstream issue #198: guest uploads in write-combined type 2 memory are slow under WHPX. Moving to cached type 3 cut the render thread from 93% to 49.9% (RTX 4070 SUPER). Same RAM cost. This PC has the same memory-type layout. Open, no PR (maintainer asked for the patch 2026-10-06). Speedup for the proof app unverified. | A type-2 vs type-3 run on this PC settles it. | [07](issues/07-gpu-cost-per-device.md) |
| T6 | Android's default erofs compressor is `lz4hc,9`, and mkfs.erofs inlines small files by default. Both block DAX (`S_DAX` only for FLAT_PLAIN/CHUNK_BASED). | 3.5 settings. | [11](issues/11-shared-system-image-on-windows.md), [12](issues/12-guest-image-source.md) |
| T7 | dm-verity and loop have no DAX (dm-linear does). Cuttlefish fstab mounts `/system` with `avb=vbmeta_system`. | No verity, no loop; verify on host. | [11](issues/11-shared-system-image-on-windows.md) |
| T8 | fs_mgr still sets up a hashtree with only `avb_keys`. | Remove both `avb=` and `avb_keys=`. | [12](issues/12-guest-image-source.md) |
| T9 | `dax=always` can quietly fall back to no DAX. | Check `S_DAX`/`STATX_ATTR_DAX` on real `/system` files. | [12](issues/12-guest-image-source.md), [11](issues/11-shared-system-image-on-windows.md) |
| T10 | One VM per process for a shared mapping on Windows: memory from one process can't map into several partitions (OpenVMM L84-86); a second partition in one process fails `WHvMapGpaRange` with `0xC0370008`; 512 handle limit for `WHvMapGpaRange2` (Hyperlight, unverified in MS docs). | One crosvm process per Device. | [08](issues/08-lock-the-route.md), [02](issues/02-whpx-memory-capabilities.md), [11](issues/11-shared-system-image-on-windows.md) |
| T11 | Online, expo-updates may fetch a newer `staging` OTA and replace the bundled JS; the version check can swap in UpdateGate. | Airplane mode (or block `u.expo.dev`) for proof runs. | [04](issues/04-proof-app-without-gms.md), [05](issues/05-baseline-on-stock-emulator.md), [13](issues/13-boot-proof-spike-plan.md) |
| T12 | Offline, the splash holds up to 10 s and the watchdog fires at 15 s. | Expect it in boot-to-first-screen time. | [04](issues/04-proof-app-without-gms.md) |
| T13 | If AsyncStorage fails in `hydrateLocale`, the splash stays up forever. | A trimmed image must keep normal app storage working. | [04](issues/04-proof-app-without-gms.md) |
| T14 | Stock emulator charges 3.1-3.3 GB unique per Device while the guest uses 1.1-1.35 GB. It never returns guest pages. | The host side is where the waste is; don't compare guest numbers to the 400 MB. | [05](issues/05-baseline-on-stock-emulator.md) |
| T15 | Out-of-process devices break copy-on-write Fork: block, net, slirp, snd and GPU run as child processes and would see the template, not the clone's pages. Eager restore is unaffected. | Fork later needs in-process devices or eager fill. Not v1. Also: count these children in unique memory. | [16](issues/16-fork-design.md) |
| T16 | GPU snapshot is blocked: Windows GPU is always vhost-user and snapshots `()`; virtio-gpu snapshot is 2D only; cross-domain returns `Unsupported`; gfxstream snapshot builds only under Bazel/Android.bp. | Accepted for v1 (D17). | [16](issues/16-fork-design.md) |
| T17 | crosvm snapshot is "highly experimental" / "100% not supported"; WHPX "Tested upstream: no"; Windows CI off since 2025-03-13. | Treat as starting code; own the boot test. | [01](issues/01-crosvm-on-whpx.md) |
| T18 | Staging buffer is 128 MiB of host RAM per Device by default (a third of the budget). | Patch to 16 MiB; correctness at smaller size unverified. | [07](issues/07-gpu-cost-per-device.md) |
| T19 | `VIRTIO_PMEM=y` may clash with the `virtual_device.fragment` `=m` match check (ticket 12 says keep `=m`; `virtio_pmem.ko` is in the dist initramfs, so first-stage init loads it). | If the Kleaf build fails the match check, use `=m` from the first-stage ramdisk. | [12](issues/12-guest-image-source.md), [13](issues/13-boot-proof-spike-plan.md) |
| T20 | New kernel, old modules don't match. | Rebuild `vendor_dlkm`/`system_dlkm`. | [12](issues/12-guest-image-source.md) |
| T21 | `mkfs.erofs` rebuild mode rejects compressed source inodes. | Repack from `target_files`, not `system.img`. | [12](issues/12-guest-image-source.md) |
| T22 | APEX payloads (ART, bionic, ICU) mount through loop, so they stay private per Device. | Memory cut 4 (apexd patch, design idea only). | [11](issues/11-shared-system-image-on-windows.md) |
| T23 | Async readback worker has "+1 frame of lag" by design. | Action results use `getScreenshot` only. | [06](issues/06-computer-use-fast-path.md) |
| T24 | Cuttlefish's gfxstream mode passes no `cross-domain` context, so it likely takes the triple-copy path. | Enable cross-domain blob + host-visible explicitly. | [07](issues/07-gpu-cost-per-device.md) |
| T25 | Committed pages of a pagefile `SEC_RESERVE` section can't be decommitted with `VirtualFree`. Offer is not a guaranteed discard. | Section-backed RAM reclaims via Offer/Reclaim (crosvm balloon); shared base and reclaimable private RAM need separate backing. | [02](issues/02-whpx-memory-capabilities.md) |
| T26 | No KSM on WHPX. Windows page combining's reach into guest RAM is unproven. | Budget with no dedup. | [02](issues/02-whpx-memory-capabilities.md) |
| T27 | Block worker logs a failed flush and still acks it. | Fork (later) must fail on a failed flush. | [16](issues/16-fork-design.md) |
| T28 | llvmpipe starts one thread per core (20 here) if software rendering is ever used. | Cap `LP_NUM_THREADS`. Hardware stays default. | [07](issues/07-gpu-cost-per-device.md) |
| T29 | OpenVMM pmem gives Linux a 0-byte region (issue #4048, open); no virtio-gpu, no balloon. | Matters only if the kill rule fires. | [11](issues/11-shared-system-image-on-windows.md), [08](issues/08-lock-the-route.md) |
| T30 | Full slim AOSP build needs 64 GB RAM; this PC has 32 GB. | Cloud Linux builder, only when cut 3 needs it. | [12](issues/12-guest-image-source.md) |

## 7. Out of scope

From [map](map.md):

- **Full BoltBetz flow parity** (login, Plaid, wallet, QR, push). Aaron, 2026-10-07: get the app to its first screen first and work this out afterwards. It belongs to a later effort.
- **GMS, FCM and Play-dependent features.** The image is no-GMS by decision.
- **Metro and live reload.** Ruled out by Aaron.
- **Building the emulator.** The map ends at a spec. (This spec is what a build works from.)

Also not v1 (from [16](issues/16-fork-design.md)): building Fork.

## 8. Defaults set after spec review (2026-10-07, Aaron's standing order: take the recommended option)

These settle the contradictions and gaps found while compiling this spec. Where an earlier section disagrees, they win.

| Item | Decision | Why |
|---|---|---|
| `VIRTIO_PMEM` | `=m`, loaded from the first-stage ramdisk before `/system` mounts | Keeps the GKI `virtual_device.fragment` match check passing (Guest image source). Built-in is the fallback only if loading from first stage fails. |
| `misc_info.txt` edits | All four: `erofs_default_compressor=none`, `erofs_share_dup_blocks=true`, `avb_system_hashtree_enable=false`, drop `erofs_sparse_flag` | Guest image source |
| fstab | Remove both `avb=` and `avb_keys=` | `avb_keys` alone still sets up a hashtree (Guest image source) |
| Spike guest | `aosp_cf_x86_64_only_phone` 15581820; slim only when the budget needs it | No slim prebuilt since 2023 (Guest image source) |
| Free-page reporting | Stays as cut 2, marked **unverified**: WHPX has no API for it. If it can't be built, `OfferVirtualMemory` on balloon-freed pages is the substitute (WHPX memory capabilities) | Memory budget plus WHPX memory capabilities |
| VMM 30 MB row | Measure the NVIDIA driver's per-process baseline in spike stage 1. If over 15 MB, move the excess into cut 1 (graphics) | GPU cost per Device |
| Settle window N | First response frame, then 33 ms with no new frame, plus app idle (replaces 100 ms; issue 10 decision 2026-10-08) | Computer-use API v1 |
| Health window N | No frame and no adb reply for 30 s means unhealthy | Fleet manager |
| Round-trip pass bar | **p95 < 50 ms** for "input to first response frame" at 1 Device (measured 28 ms); settled time reported separately; p95 < 100 ms at 10 Devices | Computer-use fast path |
