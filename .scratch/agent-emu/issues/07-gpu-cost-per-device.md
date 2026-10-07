# GPU cost per Device

Type: research
Status: resolved
Blocked by:

## Question

What does host-GPU rendering cost per Device when 10 Devices share one NVIDIA RTX 5060 Ti on Windows? Answer these:

- How much VRAM and host RAM does gfxstream or a virtio-gpu backend use per guest?
- What does a 1080x2400 surface plus swapchain cost?
- Known Windows/NVIDIA problems, such as gfxstream issue #198 (write-combined memory slowness under WHPX).
- Do any guest graphics buffers count against host RAM, and so against the 400 MB?
- How does it compare with software rendering (lavapipe, SwiftShader) per Device?

## Answer

Reconciled 2026-10-07. Checked against gfxstream `main` @ `07ee40e` (2026-10-02), issue #198 via `gh api`, plus `nvidia-smi` and `vulkaninfo` run again on this PC.

- VRAM is not the limit. This card has 16 GB (`nvidia-smi`: 16311 MiB, RTX 5060 Ti, driver 610.88, re-run 2026-10-07). Estimated 150-300 MiB VRAM per Device, 1.5-3 GiB for 10 (Pass A estimate, unverified).
- Host RAM is the risk. The biggest fixed item: gfxstream allocates a 128 MiB Vulkan staging buffer per `VkEmulation` (`kDefaultStagingBufferSize = 128ULL * 1048576ULL`, `vk_common_operations.h:723`). Each Device's VMM process has its own one.
- The staging buffer picks the first HOST_VISIBLE|HOST_CACHED type (`getStagingMemoryTypeIndex`, `vk_common_operations.cpp:237-272`). On this PC that is type 3 (flags 0x0e, heap 1 = system RAM, `vulkaninfo`). So it is 128 MiB of host RAM per Device, about a third of the 400 MB. It is a `constexpr`, so agent-emu can patch it smaller (correctness at a smaller size unverified).
- One 1080x2400 RGBA8 buffer = 10,368,000 B = 9.89 MiB (arithmetic, both passes).
- Headless means no host swapchain. `DisplayVk` builds one only for a bound surface (`display_vk.cpp:129-223`). An attached viewer adds about 30 MiB VRAM (3 x 9.89, image count = `minImageCount + 1`, NVIDIA count unverified).
- How guest buffers are created decides double copies. Non-blob (virgl-style 3D) resources get guest RAM plus a host `mLinear` copy plus VRAM. Cross-domain blob (HOST3D) buffers live in VRAM only (Pass A: `virtio_gpu_resource.cpp` `AttachIov`, minigbm `virtgpu_cross_domain.c`). Spec: enable the cross-domain context with blob + host-visible.
- Cuttlefish's gfxstream mode passes no `cross-domain` context, so it likely takes the triple-copy path (Pass A, `crosvm_manager.cpp` @ `762f240`; which gralloc loads is unverified).
- Guest host-visible Vulkan memory maps 1:1 to host types. Types 2/3 are system RAM and count against the 400 MB; type 4 is ReBAR VRAM (Pass A, `vk_emulated_physical_device_memory.cpp`; layout re-confirmed by `vulkaninfo`).
- Screenshots and recording cost host RAM only while on: the post callback allocates `4 * w * h` (9.89 MiB, `frame_buffer.cpp:1719`). The GL readback worker adds 4 pixel-pack buffers of the same size (`readback_worker_gl.cpp:34,64-67`, 39.55 MiB; driver placement unverified). Spec: read back on demand, free when idle.
- Issue #198 matches this host. Same memory-type layout (types 2/3/4 identical, re-checked). Moving guest uploads from write-combined type 2 to cached type 3 cut the render thread from 93% to 49.9% (reporter, RTX 4070 SUPER, WHPX). RAM cost is the same, only CPU changes. Open; maintainer jmacnak asked for the patch on 2026-10-06; no PR yet.
- NVIDIA/Windows also has a source-level warning: `createVkColorBufferLocked` notes zero readback for some non-dedicated ColorBuffers on NVIDIA/Windows (Pass B, `vk_common_operations.cpp`). Keep `prefersDedicatedAllocation` honoured.
- On a Windows host, gfxstream is the only accelerated backend. Venus needs a Linux or Android host (Pass A, docs.mesa3d.org/drivers/venus.html).
- Software rendering (lavapipe, SwiftShader) moves all buffers into host or guest RAM and the work onto CPU. VRAM is 0. llvmpipe starts one thread per core by default (20 here, `LP_NUM_THREADS`, docs.mesa3d.org/envvars.html), so cap it. Cost is estimated at 1-2 cores per animating Device (Pass A estimate, unverified). Hardware stays the default.
- Graphics host RAM per Device, estimated: about 140-160 MiB on the blob path (128 MiB staging + ring + driver baseline), about 240-260 MiB on the 3D-resource path. Both leave little room in 400 MB. Shrinking staging plus the blob path is required, not optional (estimate; measure in the spike).
- Still unmeasured: NVIDIA driver baseline per process, whether WDDM charges system-segment Vulkan memory to the VMM's unique RAM, and real buffer counts for the proof app. Measure 1, 2 and 10 Devices with `SHOW_PERF_STATS=1` and `nvidia-smi --query-compute-apps`.

### Per-Device cost (one Device, 1080x2400, headless, gfxstream on WHPX)

| Item | VRAM | Host RAM | Counts against 400 MB? | Source |
| --- | --- | --- | --- | --- |
| Vulkan staging buffer | 0 | 128 MiB (type 3, system heap) | Yes, unless patched smaller | `vk_common_operations.h:723`, `.cpp:237-272`; `vulkaninfo` |
| One full-screen buffer, blob/cross-domain | 9.89 MiB | 0 | No | arithmetic; minigbm `virtgpu_cross_domain.c` |
| One full-screen buffer, 3D-resource path | 9.89 MiB | 9.89 MiB `mLinear` + 9.89 MiB guest RAM | Yes, both | `virtio_gpu_resource.cpp` `AttachIov`; Linux `virtgpu_object.c` |
| ~10 full-screen buffers (app, SurfaceFlinger, HWC, wallpaper) | ~100 MiB | 0 (blob) or ~200 MiB (3D path) | Only on 3D path | buffer count estimate, unverified |
| Host swapchain | 0 headless; ~29.7 MiB with viewer | 0 | No | `display_vk.cpp`; `swap_chain_state_vk.cpp` |
| ASG command transport per render context | 0 | 12 KiB ring + 1 MiB write buffer | Yes | `address_space_device.h` (Pass A) |
| Guest host-visible Vulkan allocations | 0 if type 4 | Full size if type 2/3 | Yes for type 2/3 | `vk_emulated_physical_device_memory.cpp`; #198 |
| Screenshot/record callback image | 0 | 9.89 MiB while registered | Yes | `frame_buffer.cpp:1719` |
| GL readback worker PBOs (GL path, while recording) | unknown | up to 39.55 MiB (4 x 9.89) | Likely, placement unverified | `readback_worker_gl.cpp:34,64-67` |
| virtio-gpu PCI BAR | 0 | 0 committed (8 GiB address space) | No | crosvm `parameters.rs` |
| NVIDIA driver per-process baseline | unknown | unknown, likely tens of MB | Yes | unverified; measure |
| App textures and render targets | 50-200 MiB | 0 unless host-visible | Only if type 2/3 | estimate, unverified |
| Software rendering instead (lavapipe/SwiftShader) | 0 | all buffers (~100 MiB) + JIT + threads | Yes | Mesa docs; SwiftShader `VkMemory.cpp` (Pass B); estimate |

### Disagreements

- **128 MiB staging buffer.** Pass A missed it; Pass B found it but left the heap unverified. Verdict: Pass B wins, and it is settled. Source shows the allocation per `VkEmulation`, and it picks HOST_CACHED first, which is type 3 = system heap on this PC (`vulkaninfo`).
- **Total graphics host RAM.** Pass A: ~5-20 MiB plus driver on the blob path. Pass B: ~138 MiB conditional. Verdict: Pass B. Pass A's total leaves out the 128 MiB staging buffer.
- **Swapchain per Device.** Pass B's table has a 3-image swapchain plus an output image (39.55 MiB) per Device; Pass A says 0 headless. Verdict: Pass A. `DisplayVk` creates a swapchain only for a bound surface, and Pass B marks its own row as illustrative.
- **Readback cost.** Pass A: ~9.89 MiB while recording. Pass B: callback image plus 4 GL pixel-pack buffers. Verdict: Pass B. `readback_worker_gl.cpp` creates `mBuffers(4 /* mailbox */)`, each `mBufferSize`. Where the driver puts the PBOs is unverified.
- **GPU memory size.** Pass B: 8 or 16 GB variant unknown. Pass A: 16 GB. Verdict: Pass A. `nvidia-smi` re-run here reports 16311 MiB.
- **#198 applies to this GPU.** Pass A: yes. Pass B: unverified. Verdict: Pass A on memory layout, since `vulkaninfo` here shows the same type 2/3/4 flags and heaps as the issue. **Unresolved:** the WHPX caching cause and the speedup for the proof app. A type-2 vs type-3 run of the proof app under WHPX on this PC would settle it. (Both passes are right that it is open: maintainer asked for the patch 2026-10-06, no PR.)

Context: Pass A (Claude) `C:/dev/worktrees/agent-emu--research-gpu-cost/.scratch/agent-emu/research/07-gpu-cost-per-device.md` on branch `research/gpu-cost`. Pass B (GPT-6.1 Sol) `C:/dev/worktrees/agent-emu--codex-gpu-cost-per-device/.scratch/agent-emu/research/07-gpu-cost-per-device.codex.md` on branch `research/codex-gpu-cost-per-device`, not yet committed.
