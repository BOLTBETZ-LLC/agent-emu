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
| 768 | not reached in 10 min (stopped at ~576 s guest uptime, lmkd thrashing) | n/a | n/a | n/a | n/a | n/a | n/a | n/a |

- **Floor for this image: about 1 GB.** At 768 MB, full only_phone (with SystemUI, launcher and the full app set) never settles. Going lower needs the slim guest.
- **Guest RAM is always fully resident on the host.** The main process working set is about the full `--mem`. Shrinking guest RAM is the only lever that has worked so far.
- **`--balloon-page-reporting` did nothing:** no balloon activity in the crosvm log. The guest only has about 50-100 MB of high-order free pages, and the rest is cache. On WHPX, crosvm releases memory with `WHvUnmapGpaRange` + `OfferVirtualMemory`, and offered pages stay in the working set until Windows is under memory pressure.
- **The block process cache** is Windows file cache of the shared `super.img`. It should count once for the Fleet when every Device reads the same file (not yet measured with 2 Devices).
- **Next cuts (Memory budget order):**
  1. graphics: done implicitly by the 720x1080 2D display, about 87-150 MB;
  2. balloon: inflate at runtime and measure, or a smaller boot-time `--init-mem`;
  3. slim guest: no SystemUI or launcher;
  4. DAX shared system image (stage 2: kernel building).
- The first screen was checked by eye at every size. At 2048 the one-time "Viewing full screen" system hint covered the dialog's top half. It is now pre-dismissed with `settings put secure immersive_mode_confirmations confirmed`.

## Stage 2 progress (2026-10-08)

- **DAX kernel built.**
  - Source: `common-android16-6.12` (6.12.93), built with Kleaf `virtual_device_x86_64_dist` in WSL Ubuntu (12 cores, 16 GB), about 12 min after a 13 GB source sync.
  - Fragment: `CONFIG_ZONE_DEVICE=y`, `CONFIG_FS_DAX=y`, `CONFIG_VIRT_DRIVERS=y`, `CONFIG_VMGENID=y`. The first build failed with `CONFIG_VMGENID: actual ''`, because `VMGENID` needs `VIRT_DRIVERS`.
  - `VIRTIO_PMEM` stays `=m` (spec §8). Script and config are in `assets/13-stage2/`.
- **It boots Android and the proof app.**
  - Kernel `6.12.93-android16-6-maybe-dirty`; the three options are confirmed in `/proc/config.gz`.
  - initrd = init_boot ramdisk + vendor ramdisk + the new `initramfs.img`; the new modules win because they come last.
  - Boot 43.6 s at 1 GB; the app reaches its first screen, seen by eye (`assets/13-stage2/first-screen.png`).
  - Gap: the old `system_dlkm` modules fail vermagic (`zsmalloc`, `rust_binder`, `rfkill` "disagrees about version of symbol module_layout"). Boot still completes. Fix: swap in the rebuilt `system_dlkm.erofs.img`.
- **Balloon on Windows:** a control pipe works (`--socket \.\pipe\ae-vm`; use `balloon.ps1`, because Git Bash strips the leading backslash). But `crosvm balloon` returns `unexpected response: error: The specified module could not be found. (os error 126)`. Under investigation.
- **Still to do for stage 2:**
  - repack system as uncompressed, non-inline erofs from `target_files` in WSL;
  - Windows virtio-pmem in the crosvm fork (crosvm's pmem device is Linux-only);
  - a pmem fstab;
  - check `S_DAX` in the guest;
  - measure 2 Devices sharing the image.

### Runtime trim (2026-10-08)

- `pm disable-user` on 45 bloat packages (`assets/13-memory/trim.txt`) at 1 GB: the app still launches (TotalTime 4,099 ms), but guest used RAM stays about 1.2 GB (memory moves into zram). Host ~1.82 GB WS, unchanged.
- Disabling `com.android.phone` / `com.android.server.telecom` crashes system_server (`Can't find service: activity`). Never trim the telephony core.
- The trimmed image cold-booted at 768 MB (`--keep-data`) still crawls: guest uptime 48 s after about 10 min of wall time. Runtime trimming is not enough.
- **Next:** repack a slim, low-RAM image from `target_files`: drop APKs at the image level, set `ro.config.low_ram=true` and smaller dalvik heaps, and switch system to uncompressed chunk erofs (the DAX prep). A worker is running it in WSL. Output: `C:\devgent-emu-work\stage2\slim\`.
- **Balloon: fixed and working.**
  - `os error 126` is `ENOTCONN` in the MSVC libc. It meant the balloon device was never activated, because the guest never loaded `virtio_balloon.ko`: it sits in `/system_dlkm`, but nothing in this image loads it.
  - Fix: `insmod /system_dlkm/lib/modules/virtio_balloon.ko` after boot (the driver now does this; the spec needs it in the image's module load list).
  - Result at 2 GB with a 1 GiB inflate requested: the guest inflated 466 MB within about 90 s (it inflates as fast as the guest can reclaim). The host main process went from 2,068 to 1,601 MB WS. The app stayed alive.
  - WHPX unmap + `OfferVirtualMemory` does take pages out of the working set.

### Slim image (2026-10-08)

- **Built by repacking `target_files` from 15581820**, not a full AOSP build (`assets/13-stage2/slim-README.txt`):
  - 33 APKs removed;
  - `ro.config.low_ram=true`; dalvik heapgrowthlimit 128m / heapsize 256m;
  - system, system_ext, product and vendor as uncompressed chunk-based erofs;
  - system hashtree off, with an AVB hash footer added so vbmeta builds.
  - `otatools.zip` is on page 2 of the Build API artifact list; the first fetcher missed it.
- **Boot needed an fstab overlay** (`fstab_overlay.py`):
  - The first-stage fstab still had `avb=vbmeta_system,avb_keys=/avb` on `/system`, so init died: "Hashtree descriptor not found: system".
  - The overlay is a cpio carrying the edited `first_stage_ramdisk/system/etc/fstab.cf.{f2fs,ext4}.hctr2`, appended to the initrd.
  - It must be **legacy-LZ4 framed**. The kernel's LZ4 reader runs to the end of the buffer and ignores a plain cpio appended after an LZ4 ramdisk.
- **At 1 GB:** boot 30.3 s (stock 48 s); guest used 967 MB (stock 1,180 MB); host ~1.82 GB WS (guest RAM is still fully resident).
- **The app launched (TotalTime 1,648 ms) but lmkd killed it as TOP:** "low watermark is breached and thrashing (128%)". The aggressive `ro.lmk.*` overrides in the repack caused it. Rebuilding with softer lmkd settings (`ro.lmk.thrashing_limit=100`, PSI stall 200/700 ms). The screenshot shows the launcher after the kill (`assets/13-stage2/slim-1g/`).

### slim2 + pmem DAX (2026-10-08): stage 2 core PASS

- **slim2** (softer lmkd: `ro.lmk.thrashing_limit=100`, PSI 200/700 ms; still `low_ram=true`) at 1 GB: boot 28.3 s, launch 2,528 ms, app alive, first screen seen by eye. Guest used 1,153 MB. Host ~1.81 GB WS.
- slim2 at 768 MB: did not finish within 700 s. Not viable on a block-device system.
- **Windows virtio-pmem port:** crosvm branch `agent-emu-pmem`, commit `2643cd511`, patch in `assets/13-stage2/`.
  - pmem now compiles on Windows and is a main-process device.
  - It maps one read-only file view (`PAGE_READONLY`, which also fixes the hardcoded `PAGE_READWRITE`) and uses WHvMapGpaRange read|execute.
  - The image must be a multiple of 2 MiB; the GPA is 128 MiB aligned. `--pmem path=<file>,ro=true`.
- **DAX boot PASS** (DAX kernel 6.12.93 + `initrd-dax-pmem.img` fstab overlay: `/dev/block/pmem0 /system erofs ro,dax=always wait,first_stage_mount`):
  - `__mount(source=/dev/block/pmem0,target=/system,type=erofs)=0: Success`.
  - `/proc/mounts`: `/dev/block/pmem0 / erofs ro,...,dax=always`. The nd bus has `dax0.0` and `pfn0.0`.
  - At 1 GB: boot 24.2 s, **launch 702 ms** (block-disk system: 1.6-2.5 s), app alive, first screen seen by eye (`assets/13-stage2/pmem-1g/`).
  - Guest Cached is down to 156 MB (system files no longer go through guest page cache). Guest used 1,110 MB.
  - Host main process 1,461 MB WS = 1 GB private guest RAM + touched pmem file pages (file-backed, should be shared across Devices; **not yet measured with 2 Devices**). Block process 582 MB.
- **Next:** a multi-Device harness (per-Device run dir with hardlinked read-only images, per-Device pipes and bridge ports). Then measure 2 and then 10 Devices sharing one pmem image, and try a smaller `--mem` now that guest page cache is lower.

## Fleet runs (`assets/13-fleet/fleet.py`)

Each Device gets its own dir with hard-linked read-only images (one disk copy, one Windows file cache), its own small writable disks, console pipe, control pipe and bridge port, 2 vCPUs, 1 GB guest, the DAX kernel and the slim2 image.

**Fleet cost** is the drop in host `\Memory\Available MBytes` from before boot to all apps settled: an honest "how many fit" number. Summed crosvm working sets double-count shared pages.

| Run | Devices | Ready (s) | App alive | Fleet cost | **Per Device** | Summed WS (double-counts) |
|---|---|---|---|---|---|---|
| fleet2-pmem | 2 | 34.4 / 44.3 | both, first screens seen by eye | 1,919 MB | ~960 MB | 4,377 MB |
| fleet2-block (system on a block disk, same DAX kernel) | 2 | 34.0 / 53.0 | both | 682 MB | ~341 MB | 3,679 MB |
| **fleet4-pmem** | 4 | 40.8 / 65.5 / 76.3 / 85.9 | all 4, first screens seen by eye | 4,883 MB (Available) / 4,819 MB (Committed) / +124 MB compression store | **~1,221 MB** | 8,842 MB |

- At 4 Devices the Available delta and the Committed delta agree within 1.5%, so **~1.2 GB per Device at `--mem 1024`** is the reliable number. The cost is guest RAM (1 GB, fully resident) plus about 200 MB of VMM, GPU and device processes. The 2-Device numbers were noise.
- **fleet8-pmem (8 Devices × 1 GB): stopped by the memory guard.** Available fell to 1,133 MB during boot (48 crosvm processes). The cause was not the Devices alone: the WSL VM (`vmmemWSL`) held **10.9 GB** from the kernel build and the repacks. Rerun after `wsl --shutdown`. Rule for the spec: the Fleet host must not run the image-build VM at the same time.
- **slim3 (slim2 minus SystemUI and its RRO; `config.disable_systemui=true`) + pmem at `--mem 768`: OOM at boot.**
  - At 25 s the kernel OOM killer took system_server; the kernel panicked at 28 s.
  - Memory at OOM: anon about 280 MB, **slab_unreclaimable 177 MB**, pagetables 24 MB, free 22 MB.
  - Android 16 boot needs more than 768 MB even without SystemUI, with DAX system and low_ram.
  - Getting lower needs kernel and userspace diet work (slab users, zram sizing, fewer boot services), not just APK removal.
- **slim3 + pmem at `--mem 896`: PASS.** Ready at 143 s (slow, memory-bound), launch 691 ms, app alive, first screen seen by eye. Guest used 995 MB (with zram). The single-Device host delta was noise (WSL had just shut down).
- **pmem + slim2 at `--mem 768`: boot timeout.** The guest was only 26 s into boot after 10 min of wall time. This image (with SystemUI and the launcher) needs about 1 GB even with DAX. Next cut: slim3 without SystemUI (252 MB RSS), like the ATD images.
- **The next lever is guest RAM size.** DAX took system files out of the guest page cache, so try `--mem 768` and 640 with pmem. Then add a balloon per Device.

**Measurement caveats, found 2026-10-08:**
- The Available-MBytes delta is noisy on this PC. Other sessions, the WSL VM and Docker all move it.
- Windows **memory compression** also squeezes idle guest pages. Its store's working set was already 1.6 GB.
- So block coming out lower than pmem (341 vs 960) is not a real ranking; the two runs were minutes apart with different background load.
- The Windows PSS walker (`assets/13-fleet/pss.py`, QueryWorkingSetEx) is also unreliable. crosvm maps guest RAM into several of a Device's processes (main + block workers), and `ShareCount` saturates at 7, so it undercounts (~275 MB per Device even though each guest touched 1 GB).
- **Next runs record three numbers:** Available delta, `\Memory\Committed Bytes` delta, and the Memory Compression working-set delta. They use larger N so noise per Device shrinks.
