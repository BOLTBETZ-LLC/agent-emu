# Boot proof spike plan

Type: grilling
Status: resolved
Blocked by: 12

## Question

What exactly is the first spike that proves Route A, and what result kills it? Cover:

- The steps: build crosvm `all-msvc64,whpx` on this PC, boot slim API 36 headless, reach the proof app's first screen, and take one screenshot through gfxstream.
- The pass/fail numbers to record: boot time, host unique memory, screenshot round trip.
- The time box.
- What happens if it fails (OpenVMM, or a Linux host in WSL2).

## Answer

Grilled with Aaron on 2026-10-07. Critical path only (map speed rule).

**Stage 1: boot proof (kills the biggest risk first)**
1. Start the two slow jobs at minute 0, in parallel:
   - build crosvm `--features all-msvc64,whpx` (plus the gfxstream backend) on this PC;
   - download the `aosp_cf_x86_64_only_phone` API 36 prebuilt **15581820** (`img.zip`, `target_files.zip`, `otatools.zip`, about 3.8 GB) into WSL2.
2. Boot the stock image on crosvm/WHPX, headless.
3. Exit check:
   - the proof app (EAS `183b566c`, airplane mode) reaches its first screen;
   - one screenshot is taken through gfxstream and looked at;
   - host unique memory of the crosvm process is recorded;
   - boot time is recorded.

**Stage 2: DAX (only after stage 1 passes)**
4. In WSL2 Ubuntu, Kleaf-build `common-android16-6.12` `virtual_device_x86_64_dist` with `ZONE_DEVICE`, `FS_DAX`, built-in `VIRTIO_PMEM` and `VMGENID`.
5. Repack `system` from `target_files`:
   - edit `misc_info.txt` (`erofs_default_compressor=none`, `erofs_share_dup_blocks=true`, `avb_system_hashtree_enable=false`);
   - run `add_img_to_target_files`;
   - repack `vendor_boot` with a pmem fstab (`/system` on pmem, `ro,dax=always`, no avb).
6. Add Windows virtio-pmem to the crosvm fork (read-only file view through `WHvMapGpaRange`).
7. Exit check:
   - `S_DAX` is confirmed on a `/system` file;
   - first screen again;
   - host unique memory for 2 Devices, to show the sharing works.

**Kill rule:** if stage 1 doesn't reach the first screen after **3 distinct fix attempts** (each with the real error recorded), stop and re-grill the route. The fallbacks are an OpenVMM base or a Linux host in WSL2.

**Build host:** WSL2 Ubuntu on this PC for every Linux-only step. At most 2 heavy jobs at a time. The full slim AOSP build is not on the spike path.

**Out of the spike:** building slim from source, pmem for system_ext/product/vendor, the APEX DAX patch, Fork, the agent API, and 10-Device runs.

## Stage 1 result (2026-10-07): PASS

- **Booted:** public crosvm (HEAD of `chromium.googlesource.com/crosvm/crosvm`, built `--release --features all-msvc64,whpx,composite-disk`) on WHPX booted Cuttlefish `aosp_cf_x86_64_only_phone` 15581820 (API 36) to `sys.boot_completed=1` and `ActivityManager: System now ready`. Launch to boot completed takes about 17-20 s, and SurfaceFlinger reports "Boot is finished (7831 ms)".
- **First screen:** the proof app (EAS `183b566c`) was installed with `pm install` (Success, 4 s), launched offline (`am start -W`: TotalTime 335 ms), and reached its first screen: Start with the "No Internet Connection / Try Again" dialog, the same as the stock emulator. Seen by eye in `assets/13-stage1/first-screen-crosvm-whpx.png`.
- **Memory with the app on screen** (`--mem 4096`, 4 vCPUs):
  - Host working set is about 4,254 MB across 6 crosvm processes: main 3,202, block 872 (disk cache), others ≤150.
  - Host private bytes are only 155 MB, because guest RAM is a shared section.
  - Guest used RAM 1,765 MB; app PSS 295 MB / RSS 476 MB.
  - **About 2.5 GB of resident guest RAM is cache or free inside the guest.** Returning it to the host (balloon, free-page reporting, `--mem` cut) is the first memory win.
- **Fix attempts that moved the boot forward (none counted as a failure):**
  1. `create_composite` / `--block`: Windows refuses `set_sparse_file` on read-only handles. Patched `disk/src/disk.rs`.
  2. `run` panics on block devices. Windows needs the multi-process broker, so use `run-mp`.
  3. The slirp vhost-user net backend dies when the guest starts its queue. Patched the broker to skip it when `AGENT_EMU_NO_NET` is set.
  4. system_server hung on the lights HAL and the threadnetwork HAL. Added bootconfig `vsock_lights_port`, `vsock_lights_cid`, `openthread_node_id` and `vsock_tombstone_port`.
  5. `PersistentDataBlockService init timeout`. Added a 1 MiB `frp` partition to the composite.
- **Tooling:**
  - The guest console (hvc1) runs as a crosvm named pipe. It accepts one client only, so `console_bridge.ps1` holds it with async I/O and serves 127.0.0.1:7000, and `gsh.py` runs commands through it.
  - The APK goes in through a raw read-only disk (`head -c` from `vdb`). Screenshots come out as base64 over the console, because crosvm locks writable disks.
- **Not done in stage 1:**
  - networking/adb (the slirp crash, then the hostfwd patch);
  - headless display (the 2D GPU opens a window);
  - a screenshot taken through gfxstream (the guest `screencap` was used instead);
  - boot from the 1.5 GB sparse super without unsparsing.
- crosvm fork: branch `agent-emu` in `C:\dev\agent-emu-work\crosvm`, commit `6704542b7`. The patch, scripts and screenshot are in `assets/13-stage1/`.

## Memory runs (2026-10-07, crosvm/WHPX, only_phone 15581820, 4 vCPU, proof app on its first screen)

Driver: `assets/13-memory/measure.py`. Each run is a fresh boot, then install, offline launch, a 30-40 s settle, a screenshot checked by eye, then memory. Host numbers are working set per crosvm process: main = guest RAM, block = disk cache, gpu, others.

| Guest `--mem` | Boot to completed | App launch | Host main (guest RAM) | Host block (disk cache) | Host gpu | Unique per Device (main + gpu + small) | Guest used | App PSS |
|---|---|---|---|---|---|---|---|---|
| 4096 | ~17-20 s | 335 ms | 3,202 MB | 872 MB | 150 MB | ~3.4 GB | 1,765 MB | 295 MB |
| 2048 (+`--balloon-page-reporting`) | 23 s | 414 ms | 2,067 MB | 1,228 MB | 149 MB | ~2.25 GB | 1,536 MB | 305 MB |
| 1024 | 48 s | 2,946 ms | 1,043 MB | 658 MB | 87 MB | **~1.15 GB** | 1,180 MB (zram swap 183 MB) | 196 MB |

- **Guest RAM is always fully resident on the host.** The main process working set is about the full `--mem`. Shrinking guest RAM is the only lever that has worked so far.
- **`--balloon-page-reporting` did nothing:** no balloon activity in the crosvm log. The guest only has about 50-100 MB of high-order free pages, and the rest is cache. On WHPX, crosvm releases memory with `WHvUnmapGpaRange` + `OfferVirtualMemory`, and offered pages stay in the working set until Windows is under memory pressure.
- **The block process cache** is Windows file cache of the shared `super.img`. It should count once for the Fleet when every Device reads the same file (not yet measured with 2 Devices).
- **Next cuts (Memory budget order):**
  1. graphics: done implicitly by the 720x1080 2D display, about 87-150 MB;
  2. balloon: inflate at runtime and measure, or a smaller boot-time `--init-mem`;
  3. slim guest: no SystemUI or launcher;
  4. DAX shared system image (stage 2: kernel building).
- The first screen was checked by eye at every size. At 2048 the one-time "Viewing full screen" system hint covered the dialog's top half. It is now pre-dismissed with `settings put secure immersive_mode_confirmations confirmed`.
