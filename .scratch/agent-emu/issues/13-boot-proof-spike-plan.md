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

| **fleet6-896** (slim3, pmem, 896 MB, 2 vCPU) | 6 | 131 / 161 / 171 / 183 / 193 / 205 | all 6 (pids 4836, 5766, 5648, 5494, 6079, 5803). First screens seen by eye: 5 clean; **d2 had a "Bluetooth keeps stopping" system dialog over the app** | 5,413 MB (Available) / 6,716 MB (Committed) / +36 MB compression | **~902 MB** (Available), ~1,119 MB (Committed) | n/a |

- fleet6-896 guard: minimum Available was 7,747 MB, so it never came near the 3,000 MB stop. Other sessions were running throughout.
- **Trap:** slim3 crash-loops `com.android.bluetooth`, and its "keeps stopping" dialog can cover the app under test. Fix: set `settings put global hide_error_dialogs 1` in Device setup, and drop Bluetooth from the image or fix its crash.
- At 4 Devices the Available delta and the Committed delta agree within 1.5%, so **~1.2 GB per Device at `--mem 1024`** is the reliable number. The cost is guest RAM (1 GB, fully resident) plus about 200 MB of VMM, GPU and device processes. The 2-Device numbers were noise.
- **fleet8-pmem (8 Devices × 1 GB): stopped by the memory guard.** Available fell to 1,133 MB during boot (48 crosvm processes). The cause was not the Devices alone: the WSL VM (`vmmemWSL`) held **10.9 GB** from the kernel build and the repacks. Rerun after `wsl --shutdown`. Rule for the spec: the Fleet host must not run the image-build VM at the same time.
- **slim3 (slim2 minus SystemUI and its RRO; `config.disable_systemui=true`) + pmem at `--mem 768`: OOM at boot.**
  - At 25 s the kernel OOM killer took system_server; the kernel panicked at 28 s.
  - Memory at OOM: anon about 280 MB, **slab_unreclaimable 177 MB**, pagetables 24 MB, free 22 MB.
  - Android 16 boot needs more than 768 MB even without SystemUI, with DAX system and low_ram.
  - Getting lower needs kernel and userspace diet work (slab users, zram sizing, fewer boot services), not just APK removal.
- **fleet10-896 (10 Devices × 896 MB, pmem, 2 vCPU): did not complete.**
  - Claude Code's low-memory reaper killed the fleet driver and the 30 s memory guard while all 10 guests were still booting (guest uptimes 12-37 s, none had reached `sys.boot_completed`). The guard's last reading was 6,208 MB available before the reap.
  - All crosvm processes were stopped afterwards; Available recovered to 10,753 MB.
  - **Not restarted.** The PC also runs other heavy sessions (a node process at 2.4 GB, several Claude sessions). 10 × ~1.1 GB plus the boot peaks exceeds what is free with them running.
- **slim3 + pmem at `--mem 896`: PASS.** Ready at 143 s (slow, memory-bound), launch 691 ms, app alive, first screen seen by eye. Guest used 995 MB (with zram). The single-Device host delta was noise (WSL had just shut down).
- **pmem + slim2 at `--mem 768`: boot timeout.** The guest was only 26 s into boot after 10 min of wall time. This image (with SystemUI and the launcher) needs about 1 GB even with DAX. Next cut: slim3 without SystemUI (252 MB RSS), like the ATD images.
- **The next lever is guest RAM size.** DAX took system files out of the guest page cache, so try `--mem 768` and 640 with pmem. Then add a balloon per Device.

**Measurement caveats, found 2026-10-08:**
- The Available-MBytes delta is noisy on this PC. Other sessions, the WSL VM and Docker all move it.
- Windows **memory compression** also squeezes idle guest pages. Its store's working set was already 1.6 GB.
- So block coming out lower than pmem (341 vs 960) is not a real ranking; the two runs were minutes apart with different background load.
- The Windows PSS walker (`assets/13-fleet/pss.py`, QueryWorkingSetEx) is also unreliable. crosvm maps guest RAM into several of a Device's processes (main + block workers), and `ShareCount` saturates at 7, so it undercounts (~275 MB per Device even though each guest touched 1 GB).
- **Next runs record three numbers:** Available delta, `\Memory\Committed Bytes` delta, and the Memory Compression working-set delta. They use larger N so noise per Device shrinks.

## Lever B: host working-set trim and hard caps (2026-10-08, Device 8: slim3, pmem, 896 MB guest, 2 vCPU)

| Step | Device working set (all crosvm processes) | App | Latency p50 / p95 over ~3 min (console path) |
|---|---|---|---|
| Settled, untouched | 1,997 MB | alive | shell 66, screencap 280, tap 77 ms |
| `EmptyWorkingSet` once, after 30 s | 560 MB | alive | shell 106, screencap 360, tap 109 ms |
| After more actions | 655 MB | alive | |
| **Hard cap**: main 400 MB, helpers 64 MB | 466 MB p50/max | alive (pid 5819), first screen seen by eye (`assets/13-wscap/cap400.png`) | 75 / 139, 297 / 421, 78 / 202 ms |
| **Hard cap: main 330 MB, helpers 32 MB** | **364 MB p50, 365 max** | alive (pid 5819), first screen seen by eye (`assets/13-wscap/cap330.png`) | 78 / 102, 297 / 686, 78 / 124 ms |

- The cap is `SetProcessWorkingSetSizeEx(..., QUOTA_LIMITS_HARDWS_MAX_ENABLE)` on each crosvm process of the Device. Windows enforces it by moving extra pages to the standby/modified lists and the pagefile. Pagefile usage stayed at ~3.8%.
- The compression store grew only ~33 MB; trimmed pages mostly went to standby (reclaimable), not compression.
- What this proves: **one Device runs the proof app with ≤400 MB of resident host memory**, with the medians unchanged and longer latency tails.
- What it does not prove yet: that a Fleet of capped Devices costs ≤400 MB each in host RAM under real pressure, when standby pages are evicted and touched pages come back from the pagefile. Next: a capped Fleet run.

### fleetcap6: 6 capped Devices at once (2026-10-08, `fleet_cap.py`, ids 20-25, slim3, pmem, 896 MB, 2 vCPU, cap main 330 / helpers 32)

- All 6 booted (ready at 149-246 s, staggered) and launched the app (`Status: ok`, TotalTime 2,370-2,796 ms).
- Screens seen by eye (`results/fleetcap6/first-screen-d2*.png`): all 6 show the proof app's first screen ("No Internet Connection", airplane mode). **d24 and d25 have a "Bluetooth keeps stopping" dialog over the app**; `fleet_cap.py` does not apply the daemon's dialog suppression.
- Working set after the cap: **435 MB per Device** (all crosvm processes, every Device the same).
- **But the compression store grew from 409 to 1,493 MB** in the 60 s after the cap (other workers' Devices were steady). That is ~180 MB per Device of guest pages that left the working set only to be compressed in RAM.
- **Honest host RAM per capped Device: ~615 MB** (435 WS + ~180 compressed). Not ≤400 yet.
- The Available delta is unusable here: two other workers started and stopped Devices during the run.
- Next: inflate the balloon before capping, so pages the guest gives back are unmapped and discarded instead of compressed, and tighten the caps (`fleet_squeeze.py`).

### Balloon on a running app guest, and squeeze in agent-emud (2026-10-08)

- **A 350 MB balloon on 6 running 896 MB guests starved them:** guest MemAvailable 8.7 MB, then 0; shell calls hung; the run (squeeze6) gave no numbers. A 150 MB one-step inflate on d0 got the app killed by lmkd.
- **With the app up, the 896 MB guest has nothing to balloon:** MemAvailable is 13 MB after `drop_caches`. The balloon now grows 50 MB at a time and stops before guest MemAvailable drops under 100 MB, so here it stays at 0.
- **agent-emud `squeeze` / `memory`** (`e28ce65`, `12078ba`): d0 went from 1,999 MB to 349 MB working set (cap 250 main / 16 helpers), app alive, first screen seen by eye (`assets/13-squeeze-daemon/after-squeeze.jpg`). Tap to first frame on the offline "Try Again" button: p50/p95 188/1,392 ms before, 200/694 ms after.
- Still not counted: what the cap pushed into the compression store. A worker is measuring that now.

### squeeze2: 2 Devices capped at 250 main / 16 helpers under real host pressure (2026-10-08, ids 20-21)

- Balloon stayed at 0 on both (stepped grow refused: guest MemAvailable already under 150 MB).
- Working set: 435 MB each after the first cap (330/32), **320 / 319 MB after 250/16**, held through a 3 min hold with a tap every ~10 s per Device.
- During the hold another session's jest run took host Available down to ~470 MB. Windows emptied the compression store to the pagefile (store 1,164 to 281 MB, pagefile 5.0% to 11.6%). **Under that pressure the Devices kept 320 MB each resident and the app stayed alive** (pids 5247, 5531). First screens seen by eye (`results/squeeze2/d20.png`, `d21.png`).
- Latency: shell p50/p95 124/295 ms; tap on the offline "Try Again" button p50/p95 124/1,551 ms (that button re-checks the network before redrawing).
- Bound on real RAM per Device: 320 MB WS + at most half of the 281 MB store left (shared with every other process) = **≤460 MB, likely close to 320**. Not yet a clean ≤400 proof: the compression share is not attributed per process.

## Guest diet (2026-10-08)

Goal: get the guest RAM floor well below 896 MB, with the proof app's first screen as the pass bar. All runs use 2 vCPU, pmem DAX system, DAX kernel 6.12.93 (no kernel rebuild) and Device 16. Driver: `C:/dev/agent-emu-work/fleet_diet.py` with `boot-diet.ps1`. Results: `C:/dev/agent-emu-work/results/diet/<tag>/` (meminfo, slabinfo, allocinfo, dumpsys meminfo, services, screenshot).

**Result: the floor is 640 MB (was 896).** Ready 120.5 s, launch TotalTime 1,314 ms, app alive, first screen checked by eye (`results/diet/s4-640/first-screen-d16.png`). Guest `Used RAM: 801,063K (534,723K used pss + 266,340K kernel)`; 387 MB is swapped into 114 MB of zram. MemTotal 595 MB.

| Guest RAM | Image | Result |
|---|---|---|
| 896 | slim3, stock settings | PASS (ready 159 s, launch 2,351 ms). Used RAM 1,015,692K (631,216K pss + 384,476K kernel) |
| 896 | slim4 + all cuts | PASS (ready 126 s). Used RAM 897,336K (600,604K pss + 296,732K kernel) |
| **640** | slim4 + all cuts | **PASS** (above) |
| 576 | slim4 + all cuts | FAIL. The app launched (1,552 ms), then lmkd killed it as TOP during the settle: `low watermark is breached and thrashing (406%)`. Screen: "Phone is starting…" |
| 512 | slim4 + all cuts | FAIL. lmkd killed the app as TOP 3 s after launch (`thrashing (392%)`) |
| 448 | slim4 + all cuts | FAIL. Boot never completed. The kernel log stops at 106 s uptime (`rust_binder: Failed to allocate buffer`), with no OOM kill and no panic |

### Where memory goes (slim3, 896 MB, app on screen)

Resident, from `/proc/meminfo` and `dumpsys meminfo`:

| Rank | User | MB |
|---|---|---|
| 1 | Kernel slab (SUnreclaim 211) | 236 |
| 2 | Proof app PSS | 198 |
| 3 | zram store (holds 508 MB of swapped anon) | 155 |
| 4 | system_server PSS | 76 |
| 5 | VmallocUsed | 57 |
| 6 | Kept free by watermarks (`min_free_kbytes` 22528, from THP; `watermark_scale_factor` 174) | 52 |
| 7 | PageTables (~400 processes) | 42 |
| 8 | Shmem | 38 |
| 9 | surfaceflinger | 35 |
| 10 | mediaprovider 27, phone 19, permissioncontroller 15, LatinIME 14, android.process.media 14, devicelock 12, nfc 12, networkstack 12, gceservice 12, ext.services 11, se 10, satellite 10 | 188 |
| 11 | KernelStack (1,567 threads) | 21 |

**Slab hog: virtio, not debug options.** With `sysctl.vm.mem_profiling=1`, slim3 at 896 MB OOMed at 30 s. The OOM report's top allocations:

```
59.2 MiB  15143 drivers/virtio/virtio_ring.c:319 func:vring_alloc_queue
36.2 MiB    122 drivers/virtio/virtio_ring.c:1897 func:vring_alloc_desc_extra
36.2 MiB    122 drivers/virtio/virtio_ring.c:1058 func:vring_alloc_state_extra_split
32.4 MiB    523 block/blk-mq.c:3695 func:blk_mq_alloc_rqs
20.0 MiB   5120 drivers/char/virtio_console.c:442 [virtio_console] func:alloc_buf
```

- Cause: on Windows, crosvm creates block, gpu and snd as vhost-user frontends with `max_queue_size: None`, so every queue gets `Queue::MAX_SIZE` = 32768 entries (`src/sys/windows.rs` `create_vhost_user_{block,gpu,snd}_device`).
- The block backend offers 16 queues per disk.
- The GPU's default `audio-device-mode=per-surface` creates one virtio-snd device per display slot.
- About 70 queues × 32768 entries comes to ~180 MB.
- KFENCE (63 objects), page_owner, slub_debug and kmemleak are off or tiny in this config. `MEM_ALLOC_PROFILING` is compiled in but off by default.

**Boot ran with no swap.** The Cuttlefish vendor rc runs `swapon_all` only on `sys-boot-completed-set`. The 896 OOM report shows `Total swap = 0kB`. That is why 768 OOMed at boot.

### Cuts and measured savings

| Cut | How | Measured |
|---|---|---|
| Block queues 16 → 1 per disk | cmdline `virtio_blk.num_request_queues=1` | together with the next row (allocinfo, same 896 boot with profiling): vring 59.2 → 8.1 MiB, desc_extra 36.2 → 4.9, state_extra 36.2 → 4.9, blk_mq 32.4 → 20.6. **About −125 MB.** SUnreclaim 211 → 151, Lost RAM 69 → 27, dumpsys kernel 384 → 326 MB |
| One virtio-snd instead of one per display | crosvm `--gpu ...,audio-device-mode=one-global` | included in the row above |
| Block tag depth | cmdline `virtio_blk.queue_depth=64` | not isolated. Combined with slim4 and the console cut: SUnreclaim 151 → 125 MB |
| Consoles 20 → 11 | drop the sinks for `num=12..20` | 4 KB × 256 rx buffers per console, so about −9 MB. **Do not drop `num=11` (hvc10)**: the oemlock HAL needs it. Without it, system_server waits forever on `IOemLock` |
| THP off | cmdline `transparent_hugepage=never` | `min_free_kbytes` 22528 → 3603 (−18.5 MB kept free); `watermark_scale_factor` 174 → 117 |
| KFENCE off | cmdline `kfence.sample_interval=0` | pool is 63 objects, ≤1 MB, not measured on its own |
| zram on before zygote, 100% size | `init.cutf_cvm.rc`: `swapon_all` moved to `on post-fs-data`; fstab `zramsize=75%` → `100%` | this is what lets boot pass below 896 MB. Not measured as MB |
| Userspace (slim4) | image edits below | at 896 MB: used pss 631 → 601 MB, services 337 → 296 |
| Total kernel side | all of the above | dumpsys kernel 384 → 297 MB at 896 MB; 266 MB at 640 MB |

Not tested:
- `swiotlb=noforce` and `init_on_alloc=0`: `init_on_alloc` costs CPU, not memory.
- `page_poison=0`: page poisoning is not enabled.
- A 32-bit-less build: only_phone is already 64-bit only.
- `persist.sys.dalvik.vm.lib.2`: it is already `libart.so`, so there is nothing to gain.

### slim4 image

The source is `/root/slim/tf4`, a copy of the slim3 target_files tree. Script `stage2/slim4/edits.sh`, build `stage2/slim4/build.sh`, log `edits.log`/`build.log`. Edits:

- **zram:** `swapon_all` moved to `on post-fs-data`, and `zramsize=100%`.
- **Props:** `dalvik.vm.heapstartsize` 8m → 2m, `heapmaxfree` 8m → 2m, `ro.zygote.disable_gl_preload=true`, `ro.config.max_starting_bg=2`.
- **Features removed:**
  - feature files: wifi (+direct, passpoint), uwb, face, fingerprint, camera (all 5), live_wallpaper, window_magnification, credentials;
  - from `handheld_core_hardware.xml`: bluetooth (this stops the slim3 bluetooth crash-loop dialog), camera, print, backup, companion_device_setup, app_widgets, voice_recognizers, controls, credentials, picture_in_picture.
- **Vendor APEXes removed:** cf.bt, cf.nfc, uwb, threadnetwork, wifi, cf.wifi, cf.wpa_supplicant, gnss, cf.ir, contexthub, cas, drm.clearkey, neuralnetworks, tetheroffload. The face/fingerprint HAL rc, VINTF and binaries are removed too.
- **APKs removed:**
  - Browser2, LatinIME, PhotoTable, SettingsIntelligence;
  - BasicDreams, BluetoothMidiService, BookmarkProvider, CameraExtensionsProxy, CarrierDefaultApp, PartnerBookmarksProvider, PrintRecommendationService, SecureElement, SimAppDialog, Stk;
  - DeviceAsWebcam, Tag, E2eeContactKeysProvider, PrivateSpace, ONS;
  - AvatarPicker, CFSatelliteService, ThemePicker, ThreadNetworkDemoApp, Launcher3QuickStep, AccessibilityMenu;
  - vendor CuttlefishService (gceservice).
- **Kept on purpose:** telephony core and rild, audio, camera provider (bootconfig selects it), secure_element apex (bootconfig selects it), oemlock, sensors, lights.
- **Trap:** with Launcher3 gone, Settings' FallbackHome stays as HOME and costs 33-40 MB PSS. A tiny HOME stub would win that back.

Images (`C:/dev/agent-emu-work/stage2/slim4/`):
- `super.img` sha256 `ac45a8c0515e8729f0aaa4800e2278b399454e136d00267bf29d897aea8d727c`;
- `system-pmem.img` (padded to 2 MiB) sha256 `428768961b167aa57babf64b55244b4c9167b6e3f26fe69d2d8bfbbba4a54edf`;
- `vbmeta.img`, `vbmeta_system.img`, `vbmeta_system_dlkm.img`, `vbmeta_vendor_dlkm.img`.

Run dir `C:/dev/agent-emu-work/run-slim4` (hard links). Boot flags:

```
python fleet_diet.py <tag> --run run-slim4 --mem 640 --sinks 11 \
  --params "virtio_blk.num_request_queues=1 virtio_blk.queue_depth=64 kfence.sample_interval=0 transparent_hugepage=never" \
  --gpu "audio-device-mode=one-global"
```

### What blocks going lower

- **The app itself:** 198-230 MB PSS. It renders through ANGLE on SwiftShader (CPU Vulkan). At 576/512, lmkd kills it as TOP under thrashing.
- **Kernel at 640:** ~266 MB, made up of:
  - Slab 136 (SUnreclaim 112);
  - zram store ~114;
  - vmalloc 53;
  - PageTables 29;
  - KernelStack 17.
- **Still 9 queues × 32768 entries:** 3 block, 2 gpu, 4 snd. The fix is in the crosvm fork, not the guest: pass `Some(256)` as `max_queue_size` in the three Windows vhost-user frontend constructors. Estimate ≥10 MB more, plus shorter blk-mq tags. Not built, because the shared crosvm-pmem binary is in use by other workers.
- **system_server** ~110 MB PSS, plus ~400 processes and 1,567 threads.
- **Next cuts, not tried:**
  - the crosvm queue fix;
  - a HOME stub in place of FallbackHome;
  - dropping devicelock, adservices, ondevicepersonalization, federatedcompute and cellbroadcast (APEX jars: need care);
  - `webview_zygote` off;
  - lmkd tuned to spare TOP (`ro.lmk.thrashing_limit`).

Host note: four of the boots were cut short by the 3,000 MB during-boot host guard, because other sessions (jest runs) drove host Available to 66-1,883 MB. The coordinator raised the boot floor to 6,000 MB during those windows.

## Lever A: template clones with copy-on-write guest RAM (2026-10-08, crosvm-clone `a2eb51492`)

- Template d12 (896 MB, 2 vCPU, DAX kernel, pmem, slim3, app on first screen, `AGENT_EMU_INPROC=1`) snapshotted in 1.44 s. Restore with `AGENT_EMU_COW_RAM=1` maps guest RAM as a `FILE_MAP_COPY` view of the snapshot `mem` file; WHPX accepts it through `WHvMapGpaRange`.
- 1 eager restore + 3 CoW clones (d13-d15): app alive (pid 5273) in all, same activity on top. All screenshots seen by eye: proof app first screen (`clone-exp/out/cow-d1[345]-screencap.png`).
- Restore: eager 1,291 ms; **CoW 148 ms** (memory part 49 ms). Console answers 3.2-5.9 s after launch.
- **Unique memory per CoW clone: 323-354 MB private working set** about 45 s in (privatized pages 312-344 MB), against ~900 MB-1.1 GB per booted Device. Still growing; steady state not measured yet. Available/Committed deltas are noise (jest runs moved Available 509 MB to 17 GB during the run).
- Costs: `FILE_MAP_COPY` charges commit for the whole guest per clone (pagefile must cover N x guest RAM). Fixes needed: IA32_XSS/CET/PAT/TSC_AUX/TSC_DEADLINE in the WHPX vCPU snapshot, re-arming the TSC-deadline timer on restore, GPU 2D state kept across stop/snapshot, block in-process for CoW.
- Open: host scanout `fb.bin` blank after restore (guest screencap works); TSC jumps by wall time since snapshot.

### Lever A steady state, caps, slim4 (2026-10-08, crosvm-clone `95048aabc`, driver `assets/13-clone/`)

Each run: 3 CoW clones (d13-d15) of one template, tap "Try Again" every 10 s, one app relaunch at minute 5, sample every 30 s. Per clone: private WS and total WS of its crosvm processes, privatized guest pages (crosvm's own QueryWorkingSetEx count). Host: Memory Compression WS, pagefile %. Raw samples: `assets/13-clone/<run>.json`.

| Run | Plateau private WS per clone | Total WS | Compression store | Relaunch | Taps p50/p95/max |
|---|---|---|---|---|---|
| slim3 896, uncapped, 10 min (`steady896`) | **484 / 486 / 483 MB** (flat 5.5-10 min; ~420 before the relaunch) | 681 / 688 / 677 | 1,169 → 802 MB, no growth | 1,791 / 1,157 / 1,135 ms | 77 / 139 / 514 ms |
| same clones, cap 300 main / 16 helpers, 5 min (`cap300`) | 241-256 MB | 316-329 | grew; not attributable from samples | 2.2-2.3 s | 77 / 110 / 841 ms |
| same clones, cap 250 / 16, 5 min (`cap250`) | 196-214 MB | 268-278 | grew; see stop test | 2.5-2.9 s | 78 / 141 / 1,930 ms |
| slim4 640 + diet cuts, uncapped, 10 min (`steady640b`) | **344 / 343 / 341 MB** (flat 6-10 min; ~280-289 before the relaunch) | 520 / 529 / 527 | 1,974 → 2,055 MB (≤27 MB per clone) | 2,066 / 1,405 / 1,289 ms | 77 / 141 / 2,029 ms |
| slim5 576 + queue fix (`a45835a2e`), cap 200 main / 16 helpers, 10 min (`slim5cap200`) | 158-171 MB | 218-229 | stop test: 2,302 → 2,075 MB (−227, ~76 MB per clone) | 4,557-5,939 ms | 203 / 438 / 2,154 ms |

- **Unique per clone at plateau, slim3 896 uncapped: ~485 MB** (private WS; the compression store shrank, so nothing to add). Under the 600 MB target.
- **Capped at 250 MB: ~315 MB unique per clone.** Pagefile stayed at 2.6%, so evicted private pages went to the compression store. Stopping the 3 capped clones dropped the store 1,923 → 1,577 MB (−346 MB, ~115 MB per clone): 200 private WS + 115 compressed. Shared template pages are the rest of the WS (~70 MB) and are file-backed.
- App alive in every clone through all runs, 0 tap errors. Screens seen by eye: `clone-exp/out/steady896-d1[345]-*.png`, `cap250-d1[35]-*.png`, `steady640-d1[345]-*.png`, `templateE-d12-scanout.png`.
- slim4 640 (`snapE`): the first run lost its samples at 2.5 min (the diet worker's name-pattern cleanup killed my brokers; now fixed). The rerun `steady640b` gives **~345 MB unique per clone uncapped** (private WS, compression flat).
- **slim5 576 capped 200/16 (`snapF`, crosvm-clone with the cherry-picked 256-entry queue fix): ~240 MB unique per clone** (~165 private WS + ~76 compressed). App alive, 0 tap errors, screens are the offline dialog. Cost: taps p50 203 ms (77 uncapped), relaunch 4.6-5.9 s.
- **Host scanout after restore: fixed** (`95048aabc`). Blob resources now keep their guest ranges, so restore re-attaches them (rutabaga drops iovecs on restore), and restore publishes the scanout once. A restored clone's fb.bin carries the template's last frame. With `AGENT_EMU_FB_PIPE` (on-demand copy) the frame is current: app screen in `templateE-d12-scanout.png`, `steady640-d13-scanout.png`.
- Open: restored clones log `ErrRutabaga ... TransferToHost2d/ResourceFlush ... resource_w 0` (60 times in slim4 d13, 0 in the template). Screens are unaffected.
- Open: `FILE_MAP_COPY` commit charge is the full guest RAM per clone.

### 10-clone proof (2026-10-08, slim4 640, uncapped, crosvm-clone `a45835a2e`)

10 CoW clones (d13-d22) of one slim4 640 template (`snapG`: diet cmdline cuts, 11 consoles, one virtio-snd, 256-entry queues, `AGENT_EMU_INPROC`). Restored one after another, then held for 10 min: a tap every ~20 s on each clone, one relaunch per clone at minute 5, samples every 30 s. Raw data: `assets/13-clone/clone10.json`, `clone10-latency.json`, `clone10-restore.log`.

- **Baseline:** Available 14,584 MB, Committed 33,245 MB, compression store 2,011 MB, pagefile 2.20%. 8 other crosvm processes were running (another worker); during the hold that rose to 14.
- **Restore:** all 10 came up, 0.95-1.58 s each, console answering after 2.5-3.8 s. The 4,000 MB boot floor and the 3,000 MB stop guard never fired.
- **Plateau private WS per clone: 332-343 MB**, flat from 5.5 to 10 min (~260-289 before the relaunch). Total WS 496-516 MB. Privatized guest pages 324-335 MB.
- **App alive in all 10 at every sample** (20 samples × 10). Relaunch 1.2-2.6 s. Console taps p50/p95/max 92/249/1,449 ms, 0 errors.
- Final app pids 3740, 3737, 3735, 3733, 3732, 3726, 3722, 3722, 3716, 3713: **9 distinct values; d19 and d20 both report 3722.** The clones start from one snapshot and relaunched in lockstep, so a pid collision is expected. They are 10 separate crosvm processes on separate console ports.
- **Screens, all 20 seen by eye:** every screencap and every host scanout (`fb.bin`) shows the proof app's offline dialog ("No Internet Connection", Try Again). Contact sheets: `assets/13-clone/clone10-sheet-screencap.png`, `clone10-sheet-scanout.png`.
- **Input to first frame, fast path** (virtio-input HOME key over `ae-kbd-<id>`, then the first new flush in `fb.bin`, polled every 1 ms; d13-d15, 20 each, 0 misses):
  - p50 **36.4 ms**, p95 **75.6 ms**. That misses the 50 ms p95 target.
  - The first input of each series is the outlier (75.6 / 81.2 / 77.4 ms). Without those three, the 57 inputs give p50 34.9 ms and p95 49.0 ms.

**Unique RAM per clone:**

| Measure | Per clone |
|---|---|
| Private WS at plateau | 332-343 MB |
| + compression growth / 10 (worst case) | +0: store went 2,011 → 1,868 MB during the hold, and did not drop when the clones stopped (1,869 → 1,988) |
| Template share | resident shared template pages 48-61 MB per clone (crosvm's own count). The whole 640 MB mem file / 10 = 64 MB at worst |
| Available freed by stopping all 10 (clean: other crosvm count steady at 14) | 5,976 MB / 10 = **598 MB** |
| Available drop baseline → end of hold (noisy: the other worker added 6 crosvm processes) | 6,864 MB / 10 = 686 MB |
| Committed freed on stop | 8,127 MB / 10 = 813 MB (`FILE_MAP_COPY` charges the whole 640 MB view) |

- The gap between private WS (~338) and Available freed (~598) is ~260 MB per clone outside the process working sets. It is likely hypervisor and kernel cost per partition (WHPX/VID, page tables) plus the template share counted once. Not attributed yet.
- Pass against the 600 MB target by the Available measure (598 MB). It does not pass the 400 MB target.

### Where the ~260 MB per clone outside the working sets goes (2026-10-08, slim4 640 clones from `snapG`)

**Answer: it is not a hidden cost per partition.** A clone's marginal cost is its private working set plus ~6 MB of hypervisor pages. The rest is shared file-backed pages (template `mem` file, pmem system image, crosvm image) that the whole set of clones holds once. Those pages return to Available only when the last clone that maps them exits. Raw data: `assets/13-clone/attrib*.json`, `stopone.ps1`.

- **The 0 / 1 / 3 clone counter sweep was too noisy to use.** During it WSL started (`vmmemWSL` 5,346 MB) and the other worker's crosvm count moved 14 → 8 → 14. The only visible `Hyper-V Hypervisor Partition` / `VM Vid Partition` instance was WSL's (1,572,864 physical pages = 6 GB). Per-partition instances for WHPX partitions in other processes were not visible without elevation.
- **Clean test: stop one clone at a time**, Available sampled every second just before and after (`stopone.ps1`; other crosvm steady at 14):

| Stop | Clone private WS | Available freed | Hypervisor Total Pages | Nonpaged pool | Committed |
|---|---|---|---|---|---|
| d15 (2 still running) | 277 MB | ~280 MB (6,880 → 7,160) | −1,536 (6 MB) | +1 MB | −860 MB |
| d14 (1 still running) | 270 MB | ~225 MB (6,540 → 6,765) | −1,536 (6 MB) | +6 MB | −790 MB |
| d13 (last) | 277 MB | ~650 MB (6,720 → 7,400) | −1,536 (6 MB) | +6 MB | −970 MB |

- So the cost per clone ≈ private WS + 6 MB of hypervisor pages: 1,536 pages per partition, the same as `\Hyper-V Hypervisor\Total Pages` moving 62,685 → 64,221 when one more partition existed. Nonpaged pool, paged pool and System Driver Resident do not move per clone. Committed moves ~0.8-1 GB per clone (the `FILE_MAP_COPY` view charge); that is commit, not RAM.
- **Shared, once per host: ~370 MB** with 3 clones (the extra freed when the last clone stopped). In the 10-clone stop it looked larger: 5,976 − 10 × ~338 − 60 ≈ 2.5 GB. Some of that is noise, and some is more template/pmem pages touched over a 16-minute run with relaunches. Not separated.
- **Fleet model:** host RAM ≈ shared once (0.4-2.5 GB) + N × (private WS + 6 MB). At the slim4 640 uncapped plateau that is ~345 MB per extra clone.
- **Step 2 (WHvMapGpaRange flags, file-backed vs private): not applicable.** There are no locked/VID pages per partition outside our working set to remove. The docs only say a mapping "sets a region in the caller's process as the backing memory". `WHvAdviseGpaRange` Pin is the call that "pins the backing memory ... so that it remains resident". crosvm never calls it.
- **First-input outlier = first touch after restore or idle:**
  - fresh clone, no warm-up: first HOME 123.9 ms, p95 123.9 ms;
  - fresh clones with 2 untimed warm-up inputs: first 68.2 / 39.2 ms, p95 60.6 ms;
  - **3 warmed clones: p50 36.2 ms, p95 50.5 ms, max 53.3 ms** (60 inputs, 0 misses).
  - Warm samples spread 1.5-53 ms, bunched like frame pacing (a few ~16.7 ms vsync periods). Getting p95 under 50 likely needs a higher display refresh rate or a vsync-independent flush. Not tried.

### 10-clone unique proof (2026-10-08, slim4 640 `snapG`, uncapped, crosvm-clone `a45835a2e`)

Run: 10 CoW clones (d13-d22) restored one after another (0.96-5.82 s each). Held 10 min: a tap every ~20 s on each, one relaunch per clone at minute 5. Then stopped one at a time, d22 down to d13, ~25 s apart. Available, hypervisor pages, standby/modified/free lists and the other workers' crosvm pid set were sampled every ~1 s, 3 samples before and 6 after each stop. Freed = mean of after-samples 2-4 minus mean of before-samples. Raw data: `assets/13-clone/u10.json`, `u10-stopseq.json`, `stopseq.ps1`, `u10-run.log`.

- **Hold:** app alive in all 10 at all 20 samples. Plateau private WS 340-348 MB. Relaunch 1.3-5.4 s; console taps p50/p95/max 78/202/1,043 ms, 0 errors. Compression store 2,102 → 2,503 MB (+401, i.e. ≤40 MB per clone).
- **Screens:** the sheet (`assets/13-clone/u10-sheet.png`) was seen by eye. All 10 show the proof app's offline dialog. Final pids: 9 distinct; d19 and d20 both 3762 (same lockstep effect as before).

| Stop | Private WS | WS | Available freed | Hypervisor pages | Standby Δ | Note |
|---|---|---|---|---|---|---|
| d22 | 342 | 539 | 378 | −1,536 | −21 | |
| d21 | 347 | 542 | 383 | −1,536 | +67 | |
| d20 | 348 | 542 | (1,831) | −1,536 | +1,479 | rejected: the standby list jumped 1.5 GB in the window, an outside event the crosvm-pid check did not catch |
| d19 | 340 | 526 | 414 | −1,536 | +58 | |
| d18 | 343 | 539 | 405 | −1,536 | +20 | |
| d17 | 342 | 538 | 441 | −1,536 | −71 | |
| d16 | 343 | 539 | 418 | −1,536 | +45 | |
| d15 | 341 | 527 | (1,503) | −1,536 | +164 | rejected: another process started or stopped in the window |
| d14 | 343 | 532 | 482 | −1,536 | +184 | |
| d13 (last) | 346 | 533 | 634 | −1,536 | +232 | |

- **Marginal (unique) cost per extra clone:**
  - **median 416 MB, max 482 MB** over the 7 clean non-last stops;
  - that is private WS (~343) + 6 MB hypervisor + ~65 MB of pages mapped by that clone alone (file pages that go to standby when it exits);
  - **+ compression worst case 40 → ~456 MB.**
- **Shared once:** the last clone freed 634 − 346 private = **~288 MB**.
- **Average per Device at N=10, shared included:**
  - 9 marginal (the 2 rejected stops taken at the median) = 3,753;
  - + last 634 = 4,387 MB / 10 = **~439 MB**;
  - **+ compression ≤40 → ~479 MB**.
- **Verdict:** meets 600 MB at N=10. Misses the original 400 MB, by ~16 MB at the clean marginal median before compression and by ~56-79 MB once compression and the shared part are counted.

### 10-clone light-cap proof (2026-10-08, slim4 640 `snapG`, cap 300 main / 16 helpers after the plateau)

Started after the Android 17 10-Device run (ids 30-39) had finished, once 0 `ae-vm-3x` crosvm processes and Available > 9,000 MB held for 2 minutes. Then:
1. 10 CoW clones restored (1.11-1.15 s each).
2. 10 min uncapped (tap ~20 s, relaunch at minute 5).
3. Cap 300 / 16 on every clone process, then 5 more min with taps.
4. Fast-path latency on d13-d15 under the cap.
5. Stops one at a time, same method as the unique proof.

Raw data: `assets/13-clone/lc10-*.json`, `lc10-run.log`, `lc10-sheet.png`.

- **Uncapped plateau** (same as before): private WS 340-346 MB, WS 506-530 MB, compression flat (1,951 → 1,950). Relaunch 1.1-2.0 s, taps p50/p95 78/156 ms.
- **Under the cap:** private WS 243-249 MB, WS 320-329 MB. App alive in all 10 at every sample (20 uncapped + 10 capped). Taps p50/p95/max 78/188/282 ms, 0 errors. The compression store grew 1,954 → 2,522 MB from before the restores to before the stops (+568, ~57 MB per clone).
- **Screens:** the sheet (all 10 capped screencaps) was seen by eye. All show the offline dialog. Final pids: 8 distinct values (3764 and 3760 each twice: lockstep clones).
- **Latency under the cap** (HOME to first frame, 2 warm-ups, 20 each): **p50 44.3 ms, p95 104.3 ms**. That is worse than warm uncapped (36.2 / 50.5).

| Stop | Private WS | Available freed | Hypervisor pages | Standby Δ | Note |
|---|---|---|---|---|---|
| d22 | 240 | 476 | −1,536 | +66 | |
| d21 | 226 | 334 | −1,536 | +6 | |
| d20 | 239 | 386 | −1,536 | +48 | |
| d19 | 237 | 368 | −1,536 | +35 | |
| d18 | 226 | 459 | −1,536 | +65 | |
| d17 | 242 | 473 | −1,536 | +480 | standby jump; kept |
| d16 | 245 | (−582) | +9,373 | −1,354 | rejected: another partition started in the window |
| d15 | 243 | 461 | −1,536 | +68 | |
| d14 | 258 | 592 | −2,048 | −49 | |
| d13 (last) | 258 | 495 | −1,536 | +75 | |

- **Marginal per clone with the cap: median 460 MB, worst clean 592 MB** (8 clean non-last stops). Freeing a capped clone also frees its compressed pages, so this already includes the compression share. Do not add the +57 on top (the uncapped proof's "+40" was likewise already inside its freed figure).
- **Shared once:** last freed 495 − 258 private = **≤237 MB** (part of that is d13's own compressed pages).
- **Average at N=10:** the 8 clean stops + d16 taken at the median + last 495 → **~450 MB per Device**.
- **Verdict: the light cap does not cross 400 MB.** It cuts the working set by ~100 MB per clone, but those pages move into the compression store at about the same real cost (marginal 460 capped vs 416 uncapped, inside noise). It also doubles input p95. Uncapped slim4 640 clones remain the better config. Getting under 400 needs fewer privatized guest pages (guest diet, or balloon/free-page reporting so guest-free pages return to the template), not host caps.

### slim5 at 640, 3 clones (2026-10-08, `snapH`, uncapped): no gain over slim4; 10-clone run skipped

Setup: slim5 image (`run-slim5`: HomeStub, tuned lmkd) at `--mem 640`, queue-fix crosvm-clone, diet cmdline, 11 consoles, one virtio-snd. Template setup now also runs `setprop log.tag.RIL S` and renices every `libcuttlefish-rild` thread to 19, as the daemon does. Readback in the template: 3 threads at nice 19, 1 still at 0. Gate: no other worker Devices, Available 14,495 MB. Raw data: `assets/13-clone/s5-*`.

- Template: ready 122.8 s, launch 694 ms. Clones restored in 1.11-1.32 s.
- **Plateau private WS 373-375 MB** (≈300 before the minute-5 relaunch). That is higher than slim4 640 (340-348). App alive in all 3 at all 20 samples. Relaunch 1.3-2.5 s, taps p50/p95 77/250 ms. Compression store flat (1,934 → 1,935).
- **Stops:**

| Stop | Private WS | Available freed | Hypervisor pages | Standby Δ |
|---|---|---|---|---|
| d15 | 378 | 469 | −1,536 | +37 |
| d14 | 376 | 516 | −1,536 | +47 |
| d13 (last) | 377 | 689 | −1,536 | +195 |

- **Marginal median ~492 MB** (469 / 516). That is above the 390 MB bar, so the 10-clone run was not done. Shared once: 689 − 377 = 312 MB.
- **Input to first frame (HOME, 2 warm-ups, 20 each on d13-d15): p50 34.9 ms, p95 41.1 ms, max 49.7, 0 misses.** This meets the 50 ms target. The spread is much tighter than slim4 (p95 50.5), consistent with the RIL no longer spinning a vCPU.
- Screens seen by eye (`assets/13-clone/s5-sheet.png`): the template scanout and all 3 clone screencaps show the offline dialog. Final pids 3826 / 3826 / 3827 (lockstep).
- **Conclusion:** at the same guest size, the slim5 image does not lower per-clone privatized memory. Best clone config stays slim4 640 uncapped: marginal median 416 MB, ~439 MB average at N=10. Reaching 400 needs the guest to privatize less: smaller `--mem` with clones (slim5 at 576), or balloon/free-page reporting that hands guest-free pages back to the template.

### What privatizes a clone's pages (2026-10-08, crosvm-clone `16f9e958e`, data `assets/13-clone/privatize/`)

Method: `AGENT_EMU_COW_DUMP` makes crosvm write each clone page's state (not resident / shared template page / private copy) every 30 s. `attrib_run.py` drives one clone like the steady runs (tap every 20 s, relaunch at 300 s) and pulls guest `/proc/kpageflags`, meminfo, vmstat and zram stats at 5 / 60 / 290 / 600 s. `attrib_analyze.py` joins the two: what each privatized guest frame is used for now.

**Step 1: where the pages go (slim4 640, `snapG`, writable mapping):**
- **291 MB privatized in the first 30 s after restore**, +1 MB by 60 s, +8 MB by 300 s, **+42 MB at the relaunch**, then flat (342 MB at 600 s).
- At 30 s, by current guest use:

| Guest use | Privatized / total in guest (MB) |
|---|---|
| Mapped anon | 72 / 78 |
| Slab | 57 / 117 |
| Kernel other (zsmalloc, vmalloc, stacks) | 47 / 156 |
| Mapped file cache, mostly read-only code | 31 / 91 |
| Other flags | 27 / 106 |
| Unmapped anon / shmem | 21 / 37 |
| Free (buddy) | 15 / 18 |
| Page tables | 15 / 30 |
| Unmapped page cache | 7 / 8 |

- The relaunch's 42 MB is mostly new app anon (16 MB), kernel other (7) and pages freed again (5).
- **The guest is under constant memory pressure even idle.** Over 10 min: `pswpin` +325k pages and `pswpout` +329k (≈1.3 GB through zram each way), `pgsteal_kswapd` +753k, file refaults +258k, anon refaults +143k, compaction migrated +16k pages. Each swap-in, refault or migration lands in a fresh frame, which is a write. That rotates almost every non-pinned frame within minutes.
- **Hypothesis "reads privatize": half right.** With `AGENT_EMU_COW_RO=1` (clone RAM mapped read+execute; a page becomes writable on its first write fault), only 149 MB is privatized at 30 s instead of 291. But churn then climbs ~25 MB per 30 s to 340 MB by 240 s, and ends at **349 MB** (vs 342). Read-only mapping only delays the copies. It also slows the clone (console 9 s, relaunch 4.2 s), so it stays off by default.

**Step 2: cuts, one clone each, privatized MB at 600 s (relaunch included):**

| Config | Privatized @30 s | @600 s | App alive | Note |
|---|---|---|---|---|
| slim4 640, baseline | 291 | 342 | yes | |
| slim4 640, read-only RAM | 149 | 349 | yes | slower |
| (a) slim4 640, `vm.swappiness=0` in the clone | 315 | 326 | **no** (app gone by 600 s) | rejected |
| (b) slim4 640, `am kill-all` + app standby + deviceidle force-idle | 286 | 338 | yes | no gain |
| (d) drop_caches before snapshot | already in every template | | | the snapshot step runs `sync; echo 3 > drop_caches` |
| slim4 **1024**, read-only RAM | 150 | 505 | yes | more RAM = more fresh frames touched |
| slim4 **1024** | 372 | 567 | yes | relaunch alone +146 MB |
| **slim5 576**, RIL reniced | 269 | **315** | yes | lowest |

- (c) ART: CMC/uffd GC is selected by the read-only build prop `ro.dalvik.vm.enable_uffd_gc` (image change), not tried.
- (e) Re-sharing free pages: at 600 s only ~20 MB of privatized frames were free (buddy) in the guest. That is the ceiling of free-page reporting. Doing it also needs per-2 MB placeholder views in crosvm. Not built, since the gain is too small to reach 400 MB.
- **slim5 576, 3 clones, stop one at a time** (RIL 4/4 threads at nice 19):
  - plateau private WS 312-314 MB, compression flat, app alive in all 3 at all 20 samples;
  - stops freed 442 / 478 MB on the two non-last clones, **marginal median 460 MB**; last 623 (shared once ≈ 304);
  - input HOME→first frame p50/p95 48.8 / 73.8 ms (worse than slim4 640, 36 / 50);
  - screens seen by eye (`privatize/s576-sheet.png`): all 3 show the offline dialog.
- **Result: no config got the marginal median ≤ 390 MB**, so there was no 10-clone run. The best stays slim4 640 uncapped (416 marginal median, 10-clone proof).
- The relaunch at minute 5 costs ~42 MB per clone in every config. Without it, slim4 640 sits at ~300 MB privatized (≈ 375 MB marginal by the same +73 offset). That is a protocol choice, not a fix.
- The rest is the guest rotating its frames under memory pressure. Under 400 needs a guest that fits its working set without swap churn at a small `--mem`: diet lever C, smaller app heap, or zram tuned to churn less. Host-side mapping tricks can't fix it.

### Free-page reporting back to the template (2026-10-08, crosvm-clone `378cea2fd`)

- **Windows cannot drop one private page of a mapped view.** `privatize/revert_probe.py`: on a private page of a `FILE_MAP_COPY` view, `VirtualAlloc(MEM_RESET)`, `DiscardVirtualMemory` and `OfferVirtualMemory` fail with 1224 (`ERROR_USER_MAPPED_FILE`), `VirtualFree(DECOMMIT)` fails with 87, and `VirtualUnlock` only trims; the page stays private.
- **Built instead:**
  - clone RAM = 64 KiB copy-on-write views inside a placeholder reservation (`VirtualAlloc2` + `MapViewOfFile3`, resolved from kernelbase);
  - a reported free range on a clone gets every whole, aligned 64 KiB chunk unmapped from the partition, its view replaced (`UnmapViewOfFile2` + `MapViewOfFile3`), and mapped again;
  - unit test: a reverted chunk reads template bytes again and its private page count drops;
  - guest: `page_reporting.page_reporting_order=4` (64 KiB; the param exists in 6.12 `mm/page_reporting.c`) and `--balloon-page-reporting`.
- **Stock Windows crosvm page reporting crashes the guest.** The WHPX inflate path unmaps the reported GPA, the guest reuses the page, and crosvm main exits 0xe0800005 at ~3 s of boot. That is why `--balloon-page-reporting` "did nothing" in the earlier memory runs. Templates now use `AGENT_EMU_BALLOON_NOOP`, which only acks reports.
- **It works mechanically, but the guest takes the pages straight back** (1 clone each, 10 min, relaunch at 5 min):

| Template | Chunks handed back | Privatized @300 s | @600 s | Note |
|---|---|---|---|---|
| (i) slim4 640, zram on | 1,383 (86 MB) | 292 | **342** | same as without reporting (342) |
| (ii) slim4 768, swapoff before snapshot | — | — | — | `swapoff` OOM-killed (MemAvailable 0), swap stayed on, variant invalid |
| (iii) slim4 896, swapoff before snapshot | — | — | — | same, `swapoff` killed |
| (M) slim4 1024, swap off right after boot, before the app | 1,252 (78 MB) | 327 | 460 | relaunch +135 MB on fresh frames |

- A reported block is free only until the guest allocates again, and the page allocator reuses it within seconds under this load. The net privatized set does not shrink.
- No variant reached ≤ 390 MB, so I ran no 3-clone or 10-clone run. Reporting stays available but is not a lever here. The guest's write footprint over 10 min (~300-340 MB at 576-640) is the floor for this app and protocol.

### Same-content dedup of clone pages (2026-10-08, crosvm-clone `dda1c35a5`, `privatize/dedup_analyze.py`)

Run: 3 slim4 640 clones of `snapG`, 10 min (tap every 20 s, relaunch at 5 min). `AGENT_EMU_COW_PAGEDUMP_AT=600` dumps each clone's private pages (gpa + 4 KiB). They are hashed against the snapshot `mem` file, against each other, and against zero.

| Per clone (MB / % of private) | d13 (337 MB) | d14 (334) | d15 (337) |
|---|---|---|---|
| (a) identical to the template at the same GPA | 27.8 / 8% | 27.2 / 8% | 28.0 / 8% |
| ... of that, in whole 64 KiB chunks (revertable now) | 1.7 | 1.7 | 2.0 |
| (b) content in the template at another offset (4 KiB level) | 56.4 / 17% | 52.1 / 16% | 58.7 / 17% |
| ... whole chunk equal to another 64 KiB-aligned template chunk | 3 chunks | 2 | 2 |
| (c) same content in another clone, not in the template | 60.6 / 18% | 46.3 / 14% | 58.6 / 17% |
| (d) zero pages | 6.1 / 2% | 7.3 / 2% | 8.5 / 3% |

- (a) is under the 30 MB bar. Windows can only revert whole 64 KiB views (single-page revert fails, see above), so only ~2 MB per clone is recoverable today. Revert-if-identical was not built.
- (b) A view maps a contiguous file range at a 64 KiB-aligned offset, so `MapViewOfFile3` at another offset only helps when a whole 64 KiB chunk equals an aligned template chunk: 2-3 chunks per clone, about nothing. Mapping single GPA pages to other template VAs (`WHvMapGpaRange` per 4 KiB) would split what the guest and the in-process devices see, so it is not an option.
- (c) ~46-61 MB per clone would need a section shared between clones: KSM-like work with no Windows API.
- (d) zero pages are 6-9 MB per clone.
- **Ceiling of all same-content tricks together: ~(a)+(b)+(c) ≈ 140 MB per clone at page granularity.** None of it is reachable with Windows file-view granularity except ~2 MB. 400 MB per clone stays out of reach on the host side. The ~73% remainder is unique guest data.

### slim4ns (no zram/swap) clones at 896 (2026-10-08, diet worker's `run-slim4ns`, READY = 896)

The diet worker's smallest no-swap size where the app survives is 896 MB, so only 896 was run. Template `snapN`: ready 117.3 s, launch 901 ms, SwapTotal 0, MemAvailable 37 MB with the app up. 3 uncapped CoW clones, 10 min (tap every 20 s, relaunch at 5 min), then stop one at a time. Raw data: `assets/13-clone/privatize/s4ns896-*`.

- **Privatized timeline (MB, from crosvm's 30 s log):**

| Clone | 30 s | 60 s | 120 s | 300 s | 330 s | 600 s |
|---|---|---|---|---|---|---|
| d13 | 121 | 193 | 223 | 241 | 242 | 399 |
| d14 | 175 | 196 | 229 | 243 | 393 | 410 |
| d15 | 174 | 215 | 232 | 240 | 388 | 392 |

  - Without zram churn the clone privatizes less before the relaunch (~240 MB at 300 s vs ~300 at slim4 640).
  - But the relaunch alone adds ~150 MB: the guest has free frames, so the new app process lands on fresh ones.
- Plateau private WS 400-418 MB. Compression flat (2,053 → 2,027). App alive in all 3 at all 20 samples. Relaunch 1.0-1.6 s, taps p50/p95 78/265 ms.
- **Stops:** d15 rejected (the standby list jumped +3,264 MB in its window, an outside event); **d14 freed 440 MB** (the only clean non-last stop); last d13 freed 683 (shared once ≈ 263).
- **Marginal ≈ 440 MB.** That is worse than slim4 640 with zram (416), so no 10-clone run.
- **Input HOME→first frame (2 warm-ups, 60 inputs): p50 34.5 ms, p95 50.5 ms**, 0 misses.
- Screens seen by eye (`privatize/s4ns896-sheet.png`): the template scanout and d13 and d14 show the offline dialog. d15 shows the proof app's Welcome/Start screen ("Welcome to BOLTBETZ", Log In / Create Account) after its Try Again taps. The app was alive in all 3.

### slim4dax (app code on DAX pmem) clones at 768 (2026-10-08, diet worker's `run-slim4dax`, READY = 768)

Setup: second read-only pmem `app-pmem.img`. After install, the template binds base.apk from `/dev/block/pmem1` (`dax=always`), the same step as the diet worker's `soakrun.sh`. zram stays on. Template `snap-slim4dax`: ready 116.5 s, launch 550 ms, MemAvailable 77 MB. 3 uncapped CoW clones, 10 min (tap every 20 s, relaunch at 5 min). Page dumps written this time (the dump path is now forward-slash). Raw data: `assets/13-clone/privatize/slim4dax768-*`.

- **Privatized timeline (MB, page-state dumps):**

| Clone | 30 s | 60 s | 120 s | 300 s | 330 s | 600 s |
|---|---|---|---|---|---|---|
| d13 | 126 | 211 | 244 | 272 | 272 | 378 |
| d14 | 198 | 234 | 251 | 268 | 269 | 383 |
| d15 | 214 | 235 | 251 | 271 | 374 | 380 |

  End of run 388-395 MB.
- Plateau private WS 387-404 MB. App alive in all 3 at all 20 samples. Relaunch 1.2-2.9 s, taps p50/p95 78/171 ms. The compression store moved 2,115 → 457 MB during the hold: another session's activity. Pagefile flat.
- **Stops:** d15 freed 541, d14 freed 403 (non-last; no other crosvm started or stopped). Last d13 freed 271, with Available drifting down in its window, so that one is noisy.
- **Marginal median ≈ 472 MB** (403 / 541). Not ≤390, so no 10-clone run.
- **Input HOME→first frame (2 warm-ups, 60 inputs): p50 31.7 ms, p95 41.0 ms**, 0 misses.
- Screens seen by eye (`privatize/slim4dax768-sheet.png`): the template scanout and all 3 clones show the offline dialog.
- App code on DAX does not lower clone privatization. The app's code was already shared template file pages in a clone. The 768 MB guest, which the image needs, writes more fresh frames than slim4 at 640.

### slim4nsdax (no swap + app code on DAX) clones at 896 (2026-10-08, `run-slim4nsdax`, READY = 896)

- First attempt: the template's first `am start` timed out, and the snapshot was taken with Settings' FallbackHome on top, no app. Run discarded. The driver now retries the launch once if the app isn't running 30 s later. Second template: launch 573 ms on the first try, app pid 3398, SwapTotal 0, MemAvailable 24 MB.
- **Privatized timeline (MB, page-state dumps):**

| Clone | 30 s | 60 s | 120 s | 300 s | 330 s | 600 s |
|---|---|---|---|---|---|---|
| d13 | 172 | 191 | 214 | 239 | 384 | 388 |
| d14 | 166 | 193 | 219 | 240 | 388 | 390 |
| d15 | 183 | 194 | 215 | 234 | 391 | 406 |

  End 399-414 MB. Same shape as slim4ns: ~240 MB before the relaunch, then +145-155 MB from the relaunch on free frames.
- Plateau private WS 397-415 MB. Compression flat (832 → 829). App alive in all 3 at all 20 samples. Relaunch 0.9-1.4 s, taps p50/p95 73/170 ms.
- **Stops (no other crosvm activity in any window):** d15 freed 541, d14 freed 516, so **marginal median ≈ 528 MB**. Last d13 freed 698 (shared once ≈ 290). Not ≤390, so no 10-clone run.
- **Input HOME→first frame (2 warm-ups, 60 inputs): p50 31.9 ms, p95 39.5 ms**, 0 misses.
- Screens seen by eye (`privatize/slim4nsdax896-sheet.png`): the template scanout and all 3 clones show the offline dialog.

**Where the guest-side images landed (marginal per clone, stop-one median):** slim4 640 + zram 416 (10-clone proof); slim5 576 460; slim4ns 896 ~440; slim4dax 768 ~472; slim4nsdax 896 ~528. No-swap images need 896 MB to keep the app alive, and their relaunch lands on free frames, so they cost more as clones. App code on DAX does not help clones, because code pages were already shared template pages. Best clone config stays slim4 640 with zram, uncapped.

### slim4dax2 (system_ext/product/vendor + app on DAX) clones at 640 (2026-10-08, `run-slim4dax2`, READY = 640)

Boot: five read-only pmems in the diet worker's order (system, app, system_ext, product, vendor). App bound from pmem1 after install. zram on. Template `snap-slim4dax2`: ready 111.2 s, launch 474 ms, MemAvailable 49 MB. 3 uncapped CoW clones, 10 min (tap every 20 s, relaunch at 5 min). Raw data: `assets/13-clone/privatize/slim4dax2-640-*`.

- **Privatized timeline (MB, page-state dumps):**

| Clone | 30 s | 60 s | 120 s | 300 s | 330 s | 600 s |
|---|---|---|---|---|---|---|
| d13 | 193 | 253 | 260 | 271 | 271 | 333 |
| d14 | 221 | 237 | 240 | 255 | 329 | 331 |
| d15 | 233 | 239 | 244 | 256 | 329 | 331 |

  End 337-339 MB, the same as slim4 640 with partitions in super (342).
- Plateau private WS 339-342 MB. App alive in all 3 at all 20 samples. Relaunch 0.8-1.3 s, taps p50/p95 77/234 ms.
- **Stops: all three windows were contaminated by other workers:**
  - d15: hypervisor pages +8,868, i.e. another partition started (freed −458);
  - d14: standby +357 MB (freed 680);
  - d13: flagged noisy (freed 1,555).
  - No usable marginal. With privatized and private WS equal to slim4 640, the expected marginal is ~410-416, so a clean re-measure could not reach ≤390. Not rerun.
- **Input HOME→first frame (2 warm-ups, 60 inputs): p50 33.4 ms, p95 48.8 ms**, 0 misses.
- Screens seen by eye (`privatize/slim4dax2-640-sheet.png`): the template scanout and all 3 clones show the offline dialog.
- Moving system_ext/product/vendor to DAX does not change clone privatization at the same guest size. Those were already read-only template pages in a clone.

### slim4dax3 (dax2 + low-churn tuning) clones at 640 (2026-10-08, `run-slim4dax3`, READY = 640)

The image sets `watermark_scale_factor` 10, `min_free_kbytes` 2048 and zram zstd in an init rc. Boot is the same as dax2 (five pmems, app bound from pmem1). Template: ready 110.4 s, launch 575 ms, MemAvailable 89 MB. 3 uncapped CoW clones, 10 min (tap every 20 s, relaunch at 5 min). Raw data: `assets/13-clone/privatize/slim4dax3-640-*`.

- **Privatized timeline (MB, page-state dumps):**

| Clone | 30 s | 60 s | 120 s | 300 s | 330 s | 600 s | end |
|---|---|---|---|---|---|---|---|
| d13 | 110 | 190 | 206 | 230 | 230 | 339 | 374 |
| d14 | 163 | 194 | 212 | 238 | 311 | 340 | 370 |
| d15 | 160 | 195 | 211 | 234 | 326 | 333 | 349 |

  Lower before the relaunch (~230-238 MB at 300 s vs ~255-271 for dax2), about the same at 600 s.
- **The app did not survive the relaunch in 2 of 3 clones.** d13 and d14 lost the app right after the minute-5 relaunch: logcat `Process com.boltbetz.staging (pid 3754) has died: fg TOP`, then lmkd kills with "min watermark is breached". Their end screens show "Phone is starting…". d15 kept it (pid 3747); its screen and the template scanout show the offline dialog. All seen by eye in `privatize/slim4dax3-640-sheet.png`.
- **Stops are unusable:** non-last stops freed 186 / 298 MB, less than those clones' private WS (363 / 379). Host Available was being consumed by other sessions during the windows; d13 was flagged noisy. Private WS at stop time was 363-383 MB, above slim4 640 (343).
- Input HOME→first frame (2 warm-ups, 60 inputs): p50 35.4 ms, p95 39.9 ms.
- **Verdict:** the low-churn tuning lowers early privatization a little, but at 640 MB the relaunched app gets killed. Not a valid clone config. No 10-clone run.

### Honest RAM of one squeezed Device (2026-10-08, `assets/13-compress/`)

`compress_probe.py`: d0 (896 MB, slim3 pmem), proof app on its first screen, then the daemon `squeeze` with balloon 0 and caps 250/16. Phase A was 6 min with caps only; phase B was 6 min with caps plus `MEMORY_PRIORITY_VERY_LOW` and EcoQoS on all 8 crosvm processes. One tap every 10 s, one sample every 30 s. Host Available stayed between 5.4 and 11.3 GB in the window (no emergency), but other workers' Devices booted and stopped throughout (`other_brokers` in `result.json`), so every global number below carries their noise.

| Time | Phase | Device WS | Compression store WS | Pagefile used | Other Devices |
|---|---|---|---|---|---|
| 22:37:10 | app open | 1976 MB | 878 MB | 3126 MB | 2 |
| 22:37:15 | caps on (+5 s) | 331 MB | **1038 MB (+160)** | 3123 MB | 2 |
| 22:40:00 | A | 330 MB | 1362 MB | 2705 MB | 0 (3 stopped) |
| 22:43:16 | A end | 330 MB | 1192 MB | 2534 MB | 2 (new) |
| 22:46:00 | B | 329 MB | 1113 MB | 2364 MB | 3 |
| 22:49:25 | B end | 329 MB | 1202 MB | 2295 MB | 4 |

- **Per-Device RAM = ~330 MB WS + ~160-310 MB in the compression store = ~490-640 MB.** The +160 MB jump came within 5 s of the caps, before any other Device changed. +310 MB is the store at the end of A against the "app open" sample. 3 other Devices stopped during A, which should have shrunk the store, so +310 may still undercount.
- About 1,645 MB left the WS. The pagefile did not grow (it fell 3126 → 2295 MB), so nothing went to disk. The rest was clean pmem/DAX file pages (the shared system image goes to the standby list for free) plus zero pages.
- Private commit of the Device's processes is only ~71 MB. Guest RAM is a pagefile-backed section, so `PageFileUsage`/private bytes does not show it.
- **Memory priority VERY_LOW + EcoQoS changed nothing measurable.** Both set successfully (read back as 1). The store stayed at 1110-1210 MB and the pagefile did not grow. Per the docs, memory priority only orders trimming ([SetProcessInformation](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setprocessinformation)).
- **No per-process way to send pages to the pagefile instead of compression was found.** The only switch is system-wide `Disable-MMAgent -MemoryCompression` ([docs](https://learn.microsoft.com/en-us/powershell/module/mmagent/disable-mmagent)); not changed. `OfferVirtualMemory` pages "will not be written to the paging file" and are discarded ([docs](https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-offervirtualmemory)). That only fits guest pages the guest has freed (the balloon), and this 896 MB guest has ~13 MB free with the app up.
- The app stayed alive throughout (pid 5241, `end.jpg`).

## Lever D: older low-RAM image, Android 11 / API 30 (2026-10-08)

Hypothesis: the proof app's minSdk is 24, so an older Cuttlefish image with `ro.config.low_ram=true` may run it in much less guest RAM than API 36. Scripts, cmdline and evidence: `assets/13-leverD/`.

**Builds available (Build API v4, same key as the 15581820 fetcher):**

| Branch | Target | Newest build | Artifacts |
|---|---|---|---|
| `aosp-android10-gsi` | `aosp_cf_x86_64_phone-userdebug` | 11690951 (2024-04-09) | listed, but every download 404s (purged) |
| `aosp-android11-gsi` | `aosp_cf_x86_64_only_phone-userdebug` | **16396005** (2026-09-21) | `aosp_cf_x86_64_only_phone-img-16396005.zip` (616 MB), `ramdisk-debug.img`, `otatools.zip` |
| `aosp-android12-gsi` | `aosp_cf_x86_64_only_phone-userdebug` | 16532920 (2026-10-07) | not fetched |

No `*_go_phone` or other low-RAM Cuttlefish target exists on these branches. Chosen: **Android 11 only_phone 16396005** (oldest that downloads).

**Boot recipe on crosvm-pmem / WHPX (direct kernel boot, its own 5.4 kernel), 4 fixes:**
1. androidboot.* go on the kernel cmdline (no bootconfig on 5.4 / Android 11). Plain `ro.*` props are first-set-wins, so the vendor `fstab_suffix=f2fs` is rewritten in place, not appended.
2. vbmeta and vbmeta_system padded to 64 KiB (libfs_avb reads 64 KiB: "Failed to read 65536 bytes from vbmeta_a").
3. userdata: `fstab.f2fs` uses metadata encryption, and first boot fails (`vdc cryptfs mountFstab ... Failed: Status(-8)`), so /data never mounts. Fix: `fstab_suffix=ext4` and userdata formatted once from the guest with `mke2fs -t ext4 -b 4096 -O encrypt,verity`.
4. Graphics: same set as API 36 (`gralloc=minigbm`, `hwcomposer=ranchu`, `egl=angle`, `vulkan=pastel`). The ranchu HWC crashes in `cuttlefish::GetDeviceConfig()` (no host config server) unless `ro.vendor.hwcomposer.display_finder_mode=drm`. Android 11 has no cmdline path for that prop, so the initrd uses `ramdisk-debug.img` (`force_debuggable`) plus a legacy-LZ4 cpio overlay of `first_stage_ramdisk/adb_debug.prop` that carries it, plus `ro.config.low_ram=true` (`prop_overlay.py`). Confirmed in the guest: `ro.config.low_ram=true`, `ro.force.debuggable=1`.

**Results (2 vCPU, Device 30, low_ram, stock app set):**

| `--mem` | Boot to completed | Launch | App alive | Guest MemAvailable (app up) | Guest Used RAM | App PSS | Host WS settled (all crosvm) | Host WS after cap 250/16 |
|---|---|---|---|---|---|---|---|---|
| **768** | **22 s** | 1,328 ms (`Status: ok`) | yes, after 90 s capped with taps | **170 MB** (vs 13 MB on API 36 slim3 at 896) | 779 MB incl. ~360 MB in zram | 203 MB (+146 MB swapped) | 1,461 MB (main 798, block 535, gpu 88) | **319 MB** (main 249) |
| 512 | 65-108 s | `Can't find service: activity`, retry landed on launcher | **no** | 63-112 MB | 490 MB | n/a | 913-919 MB | 303 MB |

- **768 first screen seen by eye:** "No Internet Connection / Try Again", before and after the cap (`a11-768-first-screen.png`, `a11-768-capped.png`).
- **Compression store** went from 1,455 to 1,641 / 1,623 MB around the 768 cap (+168-186 MB). Other workers' Devices were running, so this is an upper bound for this Device. **Estimate per Device ≈ 319 WS + ≤186 compressed ≈ ≤505 MB**, under the 600 MB target but not a clean per-process attribution.
- **512 blocker:** after boot_completed the kernel OOM killer (not lmkd) kills `com.android.phone` / NetworkStack. system_server then aborts on purpose (`IllegalStateException: Lost network stack`), restarts, and lmkd thrash-kills launcher and app. It happened the same way with softer lmkd (`ro.lmk.thrashing_limit=100`, PSI 200/700) and 13 apps disabled (`a11-512-settled.png` shows the lock screen after the restart).
- **Next for 512:** remove telephony and NetworkStack pressure at image level (repack `target_files` with no `com.android.phone`, no launcher3 / QuickSearch, SystemUI kept), or try 640. Also the DAX/pmem path for this image: it needs a Kleaf 5.4 kernel with `FS_DAX` and an erofs system, because Android 11 system is ext4.
- **Spec impact:** API 30 at 768 MB has ~157 MB more guest headroom than API 36 slim3 at 896 and boots in 22 s, which makes it a candidate base guest. It changes the API 36 decision in the map; that is Aaron's call.

### Guest diet round 2 (2026-10-08): 576 MB floor, squeezed to ≤551 MB host

**Result:** slim5 + the queue-fixed crosvm passes at **576 MB**. The squeeze test ran on that Device: the app stayed alive for 5 min, and the estimated host cost is **551 MB at most**, under the 600 MB target. That figure is working set plus every MB the compression store grew.

**crosvm queue fix:**
- Worktree `C:/dev/agent-emu-work/crosvm-diet`, branch `agent-emu-diet`, commit `9858134a6`. It does not touch the shared crosvm-pmem binary.
- Change: `max_queue_size: Some(256)` in the Windows vhost-user frontend constructors for block, gpu and snd (`src/sys/windows.rs`).
- Built with its own target dir: `cargo build --release -j 4 --features all-msvc64,whpx,composite-disk`, 2m 37s incremental.
- Boots use `fleet_diet.py --crosvm crosvm-diet`.
- Its own MB saving is not isolated (no profiled boot). The estimate is 9 queues × ~1.8 MB.

**slim5 = slim4 plus:**
- **HOME stub:** `stage2/slim5/homestub/HomeStub.apk`, package `dev.agentemu.home`. It has no code; its HOME activity is the framework's own `android.app.Activity`. It is signed with a throwaway key and placed in `SYSTEM/app/HomeStub`. Logcat: `Adding package as default/fallback role holder, package: dev.agentemu.home, role: android.app.role.HOME`, and FallbackHome's task is removed after boot.
- **lmkd:** `ro.lmk.thrashing_limit` 100 → 1000, `ro.lmk.critical_upgrade=false`, `ro.lmk.swap_free_low_percentage` 5 → 2.
- **Trap:** `ro.lmk.psi_complete_stall_ms=2000` makes lmkd exit at start (`Kernel does not support memory pressure events`), because the stall must be shorter than the 1 s PSI window. The first slim5 build had it, and its 576 pass ran with no lmkd. It was reverted to 700 and rebuilt.
- **`webview_zygote` off: not done.** The only image-level way is removing the WebView package, and the app depends on `react-native-webview`. That would cost ~10 MB zygote + ~8 MB `webview_service`.
- Images in `C:/dev/agent-emu-work/stage2/slim5/`:
  - `super.img` sha256 `6950832a9492afc029ac42cdb863c8009f7d3f26d412211c0636735854e8e699`;
  - `system-pmem.img` sha256 `649aa8d2a0c936a29fbb1ce14936825476708cee8f2cb49e25874d60abbbcc9e`;
  - `vbmeta*`.
- Edits are recorded in `edits.sh`, the build in `build.sh`; run dir `run-slim5`.

**Floor:**

| Guest RAM | Result |
|---|---|
| 576 (slim5, lmkd working, 90 s settle) | **PASS**: ready 120.3 s, launch 1,576 ms, app alive, offline dialog seen by eye (`results/diet/s5-576keep/first-screen-d16.png`). Used RAM 739,087K (473,455K pss + 265,632K kernel; 444 MB swapped into 133 MB zram) |
| 512 (slim5) | FAIL. Alive at the 30 s settle once (Start screen only, dialog not yet drawn, launch 4,043 ms). With a 90 s settle lmkd killed it as TOP: `min watermark is breached even after kill` |

**Squeeze at 576** (`diet_squeeze.py`, no balloon):
- Caps: run-main capped to 250 MB for 150 s, then 200 MB for 150 s; helpers capped to 16 MB.
- A tap every 10 s on the dialog's Try Again button.
- Results: `results/diet/squeeze-576/result.json`.

| Measure | Value |
|---|---|
| Device WS before caps | 1,484 MB |
| Device WS, run-main cap 250 | 312 MB (flat) |
| Device WS, run-main cap 200 | 262 MB (flat) |
| Memory Compression WS | 1,571 → 1,860 MB (+289 MB over the hold) |
| Pagefile % | 2.57 → 2.58 |
| Host Available (min during hold) | 10,874 MB |
| App alive | 30 of 30 samples; same pid 3525 at the end |
| Tap round trip | p50 165 ms, max 460 ms |
| End screenshot | `results/diet/squeeze-576/end-d16.png`, offline dialog, seen by eye |
| **Unique host RAM** | **≤ 551 MB** = 262 WS + 289 store growth |

The 289 MB store growth is all counted against this Device. That is an upper bound: the store is shared by the whole host, and other sessions were running (store steps at +142 s and +232 s). The Windows PSS limits from the earlier fleet notes still apply.

### Lever D: Android 17 / API 37 build (2026-10-08, search and download only, no boot)

- **Newest Android 17 x86_64 Cuttlefish phone:** branch `aosp-android-latest-release`, target `aosp_cf_x86_64_only_phone-userdebug`, build **16373615** (2026-09-17). Its `build.prop` says `ro.build.version.sdk=37`, `ro.build.version.release=17`, `ro.build.id=CP2A.260605.016`, `ro.product.first_api_level=37`. Our 15581820 is the same branch at API 36 (`BP4A.251205.006`).
- Other branches checked: `aosp-main` stopped publishing Cuttlefish x86_64 builds (newest 13281750, 2025-03-27). `aosp-android17-release`, `aosp-android17` and `aosp-android16-qpr2-release` return no builds.
- Downloaded to `C:\dev\agent-emu-work\leverD\aosp-android-latest-release-16373615\`: `aosp_cf_x86_64_only_phone-img-16373615.zip` (1,163,638,742 B), `aosp_cf_x86_64_only_phone-target_files-16373615.zip` (2,324,666,307 B), `otatools.zip` (531,408,470 B). boot, init_boot and vendor_boot are unpacked in `unpack/`.
- **The DAX kernel carries over.** A17 Cuttlefish still ships the android16-6.12 GKI: the vendor ramdisk module vermagic is `6.12.74-android16-6-g3ec022196c4e-ab15076761`, and our DAX kernel is 6.12.93 on the same `common-android16-6.12` branch. Boot images are header v4, and the vendor cmdline (`... binder.impl=rust cma=0 ... init=/init bootconfig`) is byte-identical to 15581820. So the stage 1/2 initrd recipe (init_boot ramdisk + vendor ramdisk + bootconfig + DAX `initramfs.img` + pmem fstab overlay) applies as is. The same `system_dlkm` vermagic gap is expected until `system_dlkm` is swapped.
- **The slim repack carries over.** `META/misc_info.txt` has the same keys the slim recipe edits (`erofs_default_compressor=lz4hc,9`, `erofs_sparse_flag=-s`, `avb_system_hashtree_enable=true`, every partition erofs). One addition: `erofs_default_compress_hints` should be cleared as well. Every slim4 removal target checked is present (Browser2, LatinIME, Launcher3QuickStep, SystemUI, CFSatelliteService, ThemePicker, AvatarPicker, PrivateSpace, DeviceAsWebcam, Stk). `stage2/slim4/edits.sh` + `build.sh` should run against this target_files with only the path and build id changed.
- **Not done:** the repack needs the WSL Ubuntu VM, and it was held during the coordinator's 10-Device proof (the fleet host must not run the image-build VM at the same time). No A17 boot yet. Next: repack slim4-A17 in WSL after the proof, then boot at 640 MB with the diet flags and the DAX kernel.

### Lever D: Android 17 slim5 repack and boot kit (prepared 2026-10-08; not run, held for the 10-Device proof)

Scripts are in `assets/13-leverD/a17/`; working copies are in `C:\dev\agent-emu-work\leverD\a17\`.
- `prep-wsl.sh`: unzips otatools and the 16373615 target_files into `/root/slim17/{ota,tf}`.
- `edits.sh`: all of slim → slim5 in one pass on stock A17. Each removal logs `rm` or `MISSING`, so A17 renames show up.
  - misc_info: uncompressed chunk erofs, no sparse flag, compress hints cleared, system hashtree off;
  - final vendor props: low_ram, heaps 128m/256m/2m/2m, the slim5 lmkd set (`thrashing_limit=1000`, `critical_upgrade=false`, `swap_free_low_percentage=2`, PSI 200/700), `disable_gl_preload`, `max_starting_bg=2`, `config.disable_systemui=true`;
  - the 33 slim APKs, SystemUI and its overlays, all slim4 cuts (zram before zygote at 100%, features, 14 vendor HAL APEXes, APKs), and the slim5 HomeStub.
- slim1-3 were never saved as scripts (they lived in WSL `/root/slim`), so `edits.sh` rebuilds them from `stage2/slim/README.txt` and this issue. The slim2 lmkd props are taken from what slim5's sed expects.
- `build.sh`: the slim5 build recipe pointed at `/root/slim17` and output `leverD/a17/out/`.
- `prep_run_a17.py`: builds `run-a17/`.
  - `initrd-dax-pmem.img` = A17 init_boot ramdisk + A17 `vendor_ramdisk00` + DAX `initramfs.img` + pmem fstab overlay (`fstab_overlay_a17.py`, A17 first-stage fstabs) + bootconfig (A17 vendor bootconfig + stage1 EXTRA);
  - plus `kernel-dax`, the A17 boot images, and the repack outputs (hard links).
  - Built and checked already: 32,446,759 B, overlay carries `/dev/block/pmem0 /system erofs ro,dax=always wait,first_stage_mount`.
  - The A17 bootconfig differs from 36 by one line only: no `emulated.camera.provider.hal` key.
- `fleet_a17.py`: `fleet_diet.py` with ids 30+, ports 7130+, dirs under `leverD/a17/fleet`, default `crosvm-diet`. Its kill step only stops ae-vm-3x and its bridges; the original matched every `fleet-diet` / `boot-diet` process.
- `measure.py`: cap via `CAP_MAIN` / `CAP_HELPER`.

Run order after go:
```
wsl -d Ubuntu -u root bash /mnt/c/dev/agent-emu-work/leverD/a17/prep-wsl.sh
wsl -d Ubuntu -u root bash /mnt/c/dev/agent-emu-work/leverD/a17/edits.sh
wsl -d Ubuntu -u root bash /mnt/c/dev/agent-emu-work/leverD/a17/build.sh    # log: leverD/a17/out/build.log
wsl --shutdown                                                               # never run the build VM beside Devices
python leverD/a17/prep_run_a17.py
python leverD/a17/fleet_a17.py a17-640 --run C:/dev/agent-emu-work/leverD/a17/run-a17 --mem 640 --sinks 11 --keep \
  --params "virtio_blk.num_request_queues=1 virtio_blk.queue_depth=64 kfence.sample_interval=0 transparent_hugepage=never" \
  --gpu "audio-device-mode=one-global"
CAP_MAIN=200 CAP_HELPER=16 python leverD/tools/measure.py 30 <fleet dir d30> leverD/results/a17-640-cap --no-install
```
Then the same at 576.

### 10-Device proof (2026-10-08): 10 Devices, all alive, 445 MB per Device

**Setup:**
- slim5, `--mem 576`, 2 vCPU, crosvm-diet binary (`9858134a6`), cmdline cuts, 11 consoles, one virtio-snd, pmem DAX system, no balloon.
- Caps: run-main 200 MB, helpers 16 MB, applied right after each Device's first screen.
- Driver: `C:/dev/agent-emu-work/fleet10_diet.py`. Results: `results/diet/fleet10-576/` (`result.json`, `progress.log`, `first-d*.png`, `end-d*.png`).

**Baseline (23:19:06):**
- Available 12,629 MB, Committed 37,173 MB, pagefile 2.57 %, Memory Compression WS 1,744 MB.
- Other crosvm: 14 processes, 1,482 MB WS. They were gone by the end (lever A's Devices 13-15, see the incident below), so the Available drop understates this run's cost.

**Boot, one at a time:**
- Every Device: install Success, `Status: ok`, app pid alive after a 45 s settle, then capped.
- Ready 133.6-149.0 s, launch TotalTime 1,399-3,282 ms, boot-to-capped 195-217 s.
- Devices 16 → 25 took 23:19 → 23:54. Available stayed ≥ 5,882 MB throughout; no abort.

**Hold:** 5 min with all 10 up and capped; a tap on each Device every 20 s; a sample every 30 s (10 samples).

| Measure | Value |
|---|---|
| Per-Device WS | 257-263 MB, flat for the whole hold |
| All 10 Devices WS | 2,612 MB |
| Memory Compression WS | 1,744 → 3,580 MB (**+1,836 MB**) |
| Committed | 37,173 → 41,901 MB (+4,728 MB) |
| Pagefile % | 2.57 → 2.34 |
| Available | 12,629 → 8,803 MB (−3,826 MB) |
| App alive | 10 of 10 Devices in every sample |

**Per-Device unique RAM:**
- WS + compression growth / 10 = 261 + 184 = **445 MB**. This already puts all of the compression growth on these 10 Devices, so it is also the attributable worst case.
- Available drop / 10 = **383 MB**. This is low: lever A's 1,482 MB of crosvm WS was freed during the run. Corrected for that it is about 531 MB.
- Available recovered 8,803 → 13,763 MB (+4,960) when the 10 were stopped, which is **496 MB** per Device.
- All three measures are under 600 MB.

**End screenshots, all 10 looked at:** `end-d16.png` … `end-d25.png`. Each shows the proof app's first screen: the "No Internet Connection / Try Again" dialog over the blurred Start screen. No system dialogs, no launcher, no "Phone is starting…".

All 10 were stopped at the end by their own `ae-vm-<id>` pipes. 0 crosvm processes were left.

**Incident: lever A's Devices 13-15 were killed around the start of this run. My cleanup is the likely cause.**
- At ~23:15, before this run, I stopped my 576 squeeze Device by hand. The command matched any powershell running `*boot-diet.ps1*` and also killed each match's crosvm children.
- It stopped 5 powershell processes, not the expected 2. The 3 extra are consistent with Devices 13-15, if they were booted through `boot-diet.ps1`. The daemon's `boot-device.ps1` notes that other workers' cleanup matched `boot-diet.ps1` by name.
- The same name match was in `fleet_diet.py` `kill_mine()`.
- The baseline at 23:19 still counted 14 other crosvm processes, so the timing is not certain.
- `fleet10_diet.py` itself never stopped anything during this run (no abort), and its caps only touch its own Devices' processes.
- **Fix:** `kill_mine()` in `fleet_diet.py`, and `stop()`/`device_procs()` in `fleet10_diet.py`, now match only a `run-mp` broker whose command line has `pipe?ae-vm-<id> ` for this run's ids. They stop only that broker's crosvm children, its parent powershell and the bridge `console_bridge.ps1 -Id <id> -Port <7100+id>`. A read-only check matched only the right broker for ids 16 and 19, and nothing for 13 or 1.
- A second bug was in `fleet_diet.py`: the pattern `'*\ae-vm-{i} *'` contained `\a`, which Python reads as a BEL character, so it would never have matched. It now uses the `?` wildcard.

### Coordinator check of the 10-Device proof (2026-10-08)

- All 10 end screenshots are byte-identical (md5 `b1cb85e5...`), and identical to the single-Device `squeeze-576/end-d16.png`. Each was captured over its own console port (7100+id), and the last sample shows 10 different app pids (3382-3514). So these are 10 separate live guests. Software rendering of the same static screen gives the same bytes every time.
- **Goal met (target ≤600 MB, raised from 400 on 2026-10-08):** 10 Android 16 Devices at once, proof app on its first screen, **~445 MB unique host RAM per Device** (worst case, all compression growth charged to them). The Available-recovery figure (496 MB) is also under 600.
- Not met: the original 400 MB target.

### Shared binary gets the queue fix; system_server prop trims (2026-10-08)

**Queue fix on the main line:**
- `crosvm-pmem` branch `agent-emu-pmem` fast-forwarded to `9858134a6` (the 256-entry queues for the Windows block, gpu and snd frontends).
- The shared `crosvm-pmem/target/release/crosvm.exe` was rebuilt once: incremental, 34.5 s, no crosvm running from it at the time.
- The previous binary is kept at `C:/dev/agent-emu-work/crosvm-pmem-prev-9ade87258.exe`.
- Confirm boot, slim5 at 576 MB on the shared binary (`results/diet/shared-576/`):
  - ready 127.7 s, launch 1,868 ms, app alive after a 90 s settle;
  - offline dialog seen by eye;
  - Used RAM 729,626K (449,330K pss + 280,296K kernel).

**system_server trims (slim6 = slim5 + props):**
- There are only three `config.disable_*` props in this build's `services.jar` dex strings: cameraservice, networktime and systemtextclassifier.
- Added to `VENDOR/build.prop`:
  - `config.disable_cameraservice=true`
  - `config.disable_networktime=true`
  - `config.disable_systemtextclassifier=true`
  - `ro.backup.disable=true`
  - `ro.system_settings.service.odp_enabled=false`
  - `ro.system_settings.service.backgound_install_control_enabled=false`
  - `ro.lockscreen.disable.default=true`
- Script: `stage2/slim6/edits.sh` and `build.sh`. Images: `stage2/slim6/`. Run dir: `run-slim6`.
- Result at 576 MB (`results/diet/s6-576/`): ready 126.5 s, launch 1,667 ms, app alive, offline dialog seen by eye.
- `dumpsys -l` went from 296 to 291 services. Gone: `background_install_control`, `media.camera.proxy`, `network_time_update_service`, `textclassification`, `artd`. `backup` is still listed.
- **Memory saving: none measurable.**

| Run (576 MB) | system_server PSS | Used RAM |
|---|---|---|
| slim5 (s5-576keep) | 65,883K | 747,599K |
| slim5 on the shared binary (shared-576) | 64,156K | 729,626K |
| slim6 (s6-576) | 78,779K | 743,885K |

- Two slim5 runs differ by 18 MB of Used RAM, so the noise is larger than any effect of these props. The five services are small binder stubs.
- **What would actually shrink system_server:** a framework-res overlay that turns off resource-gated services, or building `services.jar` from source with the unused services cut. Neither can be done by repacking target_files.
- **Bigger wins for the same effort are separate processes:**
  - devicelock 12.6 MB, cellbroadcast 9.2, rkpd 8.6, adservices 7.6, odp 6.1, federatedcompute 6.1, calendar provider 6.8, acore 10.2 PSS at 896 MB;
  - each needs its APEX or APK disabled with care, because some carry boot or system-server jars.
- slim5 stays the reference image. slim6 is kept, but it buys nothing measurable.

### Lever D: Android 17 slim5 on crosvm/WHPX: PASS at 640 and 576 MB, 3 Devices at ~440 MB each (2026-10-08)

Image: `aosp-android-latest-release` / `aosp_cf_x86_64_only_phone-userdebug` **16373615** (Android 17, API 37), repacked with `assets/13-leverD/a17/edits.sh` + `edits-sensors.sh` + `build.sh` (WSL, ~2 min). super sha256 `2868068f...` before the sensors fix. Kernel: our DAX `kernel-dax` (6.12.93), pmem `/system` with `dax=always`, `crosvm-diet` binary, 2 vCPU. Diet cmdline `virtio_blk.num_request_queues=1 virtio_blk.queue_depth=64 kfence.sample_interval=0 transparent_hugepage=never`, gpu `audio-device-mode=one-global`. Evidence: `assets/13-leverD/a17/results/`.

**Two Android 17 fixes:**
1. apexd-bootstrap aborted (`reboot,bootloader,bootstrap-apexd-failed`): the A17 vendor bootconfig no longer selects one of the two camera provider APEXes. Fix: `androidboot.vendor.apex.com.google.emulated.camera.provider.hal=com.google.emulated.camera.provider.hal`, added in `prep_run_a17.py`.
2. system_server restarted every ~3.5 min, because `ISensors/default` never registered: the Cuttlefish sensors sub-HAL waits for the host sensor simulator. With 11 consoles it aborted on `Could not connect to sensors control: No such device`; with 20 it hangs. Fix: drop the sensors multi-HAL (rc, VINTF fragment, binary, sub-HAL .so, sensor feature xmls) in `edits-sensors.sh`. Boot runs with 20 console sinks; 11 is untested after the fix.

All 33 slim APKs, all slim4 targets and SystemUI were present in A17 (0 `MISSING` in `edits.log`). zram moved to post-fs-data at 100%.

| Run | Ready (boot_completed) | Launch | App alive after 5 min capped with taps | Host WS settled | Host WS capped 200/16 (5 min, flat) | Compression store | Tap shell p50/max |
|---|---|---|---|---|---|---|---|
| **640 MB, 1 Device** | 113 s | 954 ms | yes (pid 3372), first screen seen by eye before and after | 1,605 MB (main 1,149) | **258 MB** | 2,076 → 2,250 at cap → 2,046 end (net −30; others running) | 172 / 571 ms |
| **576 MB, 1 Device** | 120 s | 1,808 ms | yes (pid 3375), seen by eye | 1,526 MB | **270 MB** | 1,878 → 2,107 (+229 upper bound) | 239 / 1,002 ms |
| **640 MB, 3 Devices at once (30-32)** | 117 / 125 / 130 s | 913 / 1,038 / 1,384 ms | all 3 (pids 3355, 3302, 3251), all 3 seen by eye after the hold | 1,594-1,614 MB each | **258 / 268 / 268 MB** | 1,971 before cap → 2,449-2,495 end: **+478-524 MB for 3 = ~160-175 per Device** | 160-197 / 771-838 ms |

- **Honest RAM per Device (3-Device run): ~265 MB WS + ~170 MB compression ≈ 435-440 MB**, under the 600 MB target. The compression delta also includes other workers' activity, so it is an upper bound. The host stayed above 5,400 MB Available the whole time.
- At 640 the guest shows `Used RAM 779,140K (479,744K pss + 299,396K kernel)`, with 490 MB swapped into 148 MB of zram. At 576, guest SwapFree is down to 37 MB after the hold: it works, but with little margin. Use 640 as the default.
- Android 16 slim4 failed at 576 (lmkd killed TOP). Android 17 slim5-a17 passes at 576. That is likely the slim5 lmkd set and the HOME stub, not Android 17 itself; not isolated.
- Trap: `su 0 sh -c 'am ...'` logs `app_process` aborts (`BOOTCLASSPATH and DEX2OATBOOTCLASSPATH must not be empty`). That is the shell environment under su, not the app.
- Not done: 10 Devices on Android 17, console sinks trimmed back to 11, and `system_dlkm` swapped to the DAX kernel's modules.

### Lever D: 10 Android 17 Devices at once, 640 MB, ~445 MB host RAM each (2026-10-08, ids 30-39)

Driver: `assets/13-leverD/a17/fleet10_a17.py` (slim5-a17 + sensors fix, DAX kernel, pmem, `crosvm-diet`, diet cmdline, 20 console sinks, 2 vCPU). Evidence: `assets/13-leverD/a17/results/x10/`. Gate before the start: 0 lever A crosvm (`ae-vm-1[2-9]|2[0-2]`) on two 60 s checks, and Available 13,225-15,125 MB.

- **All 10 booted one at a time:** ready 112-118 s each, launch 766-991 ms, all `Status: ok`; 22.6 min in total.
- **All 10 apps were alive after a 5 min capped hold with a tap per Device every ~10 s.** 10 distinct pids (3415, 3331, 3389, 3382, 3345, 3339, 3334, 3358, 3513, 3346).
- **All 10 screenshots seen by eye** (`montage.png`): first screen, "No Internet Connection / Try Again". The 10 PNGs are byte-identical (same deterministic render). Each was read through its own console port, and the pids differ.
- **Caps:** 200 main / 16 helpers, applied to each Device 45 s after its own launch (`cap_up.py`). They were not applied after a common settle as briefed: 10 uncapped Devices at ~1.1 GB each would have crossed the 3,000 MB guard while the rest booted.
- **Working set:** 273 MB per Device, flat over the whole hold (2,730 MB total for 10).
- **Compression store:** 1,954 MB before the first boot → 3,672 MB after the hold = **+1,718 MB for 10, ~172 per Device**. Measured only from the cap is +347, but that misses the growth while booting, because the caps were already on.
- **Honest RAM per Device:**
  - 273 WS + 172 compression = **~445 MB**;
  - cross-check by Available: 13,237 before → 8,771 after the hold = **4,466 MB for 10 = ~447 MB**;
  - stopping all 10 brought Available back to 13,808 (+5,037, ~504 per Device, including standby refill).
- **Marginal cost per stop** (Available freed 15 s after each stop): 613, 352, 454, −379, −87, 443, 697, 477, 492, 592 MB. Too noisy per stop (other sessions, standby refill); use the totals above. Compression freed per stop: 129-379 MB (first stop 379, then ~130-180).
- **Guest per Device:** Used RAM 750-780 MB (456-488 MB pss + 271-300 MB kernel), 358-497 MB swapped into 104-146 MB of zram.
- **Latency:** tap shell round trip p50/p95/max 157 / 295 / 481 ms with 10 Devices.
- **Verdict:** Android 17 meets the 600 MB per Device target with 10 Devices at once: ~445 MB host RAM each, apps alive, screens checked. It is the same as the Android 16 proof (~445).

### slim4ns: no swap at all (2026-10-08)

Why: CoW clones privatize ~340 MB because the guest churns memory through zram. slim4ns is slim4 with swap never enabled, plus lmkd tuned for no swap.

**Edits** (`stage2/slim4ns/edits.sh`, tree `/root/slim/tf4ns`, a copy of slim4's `tf4`):
- `init.cutf_cvm.rc`: removed `swapon_all` (slim4 had it at post-fs-data), the `zram.ko` modprobe, and the `comp_algorithm lz4` write. The script fails if any non-comment `swapon` or `zram` line is left.
- `fstab.cf.*`: the `/dev/block/zram0 ... swap` line is removed.
- `VENDOR/build.prop`:
  - `persist.sys.zram_enabled` 1 → 0;
  - `ro.lmk.use_minfree_levels=false`, `ro.lmk.use_psi=true`;
  - `ro.lmk.thrashing_limit=1000`, `ro.lmk.critical_upgrade=false`, `ro.lmk.swap_free_low_percentage=0`.
- Images in `stage2/slim4ns/` (system-pmem sha256 `4266a5ba09277a06893be6605ae9eb05a5d1c423a51948ae150510437361ca26`). Run dir `C:/dev/agent-emu-work/run-slim4ns`.

**Boot setup:** shared crosvm-pmem binary (queue fix), cmdline cuts, 11 consoles, one snd. The soak is `diet_soak.py`: 10 min, a tap every 20 s, `am force-stop` + `am start -W` at minute 5, a sample every 60 s.

| `--mem` | Result |
|---|---|
| **896** | **PASS.** Ready 116.8 s, launch 1,133 ms. The app survived 10 min, including the relaunch (pid 3403 → 3847, relaunch TotalTime 794 ms); no lmkd kill of the app. `SwapTotal` 0, **pswpin/pswpout delta 0/0**. MemAvailable 16-32 MB (89 right after the relaunch). End screenshot `results/diet/ns-896-soak/end-d16.png`: offline dialog, seen by eye |
| 768 | FAIL. lmkd killed the app as TOP 30 s after launch: `low watermark is breached and thrashing (3324%)` with the limit at 1000 |
| 768, thrashing kill off at runtime (`persist.device_config.lmkd_native.thrashing_limit=100000` + `lmkd.reinit`) | FAIL. The app launched (2,897 ms), then file refaults ran at ~4.7M pages in the first minute. The result was `ANR in com.boltbetz.staging`, and the app was gone by the 60 s sample |

**kswapd at 896 over 10 min:**
- `pgsteal_kswapd` +1,055,425 pages (~4.1 GB); `pgsteal_direct` +705; `workingset_refault_file` +893,366 (~3.5 GB).
- Almost all of it is in the first 6 min plus the relaunch. From 423 s to 544 s it was only +8,867 steals.
- The churn is file pages refaulting, mainly the app's own APK/oat/dex on `/data` (not DAX). Swap is gone, but page-cache refill still writes guest pages.

**Smallest size where the app survives 10 min with no swap: 896 MB.** `run-slim4ns/READY` contains `896`.

To go lower without swap, the app's code pages should be DAX-backed like `/system`: install it into the pmem image, or put `/data/app` on DAX. Otherwise 768 thrashes on refaults.

### App code on DAX: appdax (2026-10-08)

Goal: stop the app's own code refaulting from non-DAX `/data`, while it stays a normal user app.

**What the app reads, and from where** (slim4ns, 896 MB, `dumpsys package` + `/proc/<pid>/smaps`):
- `extractNativeLibs=false`: `lib/x86_64/*.so` are STORED in the APK and mapped from `base.apk`; `lib/x86_64/` on disk is empty.
- The Hermes bundle `assets/index.android.bundle` (8.9 MB) is STORED (uncompressed), so it can be mapped from the APK.
- `classes*.dex` (6 files) are DEFLATED. dexopt is `[status=verify] [reason=install]`, so ART reads dex from `oat/x86_64/base.vdex` (51 MB; `base.odex` is 394 KB), not from the APK.
- `flags=[ HAS_CODE ALLOW_CLEAR_USER_DATA ]`: a plain user app.
- The installed `base.apk` is byte-identical to the host APK (both sha256 `74b606bb186d...`).
- Resident file-backed pages of the app: `/data/app` 11.2 MB, `/apex/com.android.art` 9.5, `/system/lib64` 4.8 (pmem0, DAX), `/product/app` 4.3.
- The app process is mostly anonymous: Pss_Anon 179 MB of a 200 MB Pss.

**Method (preferred route, it works; the system-app fallback was not needed):**
- `stage2/appdax/app-pmem.img` (197 MB, sha256 `e69e4067d07b31c89436aa740c3f3c3d6528f05b1d6f260a1098d6c6655e922c`):
  - an uncompressed chunk-based erofs like system-pmem (`mkfs.erofs -b 4096 --chunksize 4096 -E noinline_data`, uid/gid 1000, padded to 2 MiB);
  - it holds `/com.boltbetz.staging/base.apk` and `oat/x86_64/base.{odex,vdex}`, copied from a real install. The oat came out through the `out` disk with `tar`.
- It is attached as a second `--pmem ...,ro=true`, so it appears in the guest as `/dev/block/pmem1`.
- In the image (`stage2/appdax/vendor/`, added to a tree by `add-to-tree.sh`):
  - `/vendor/bin/appdax.sh` mounts pmem1 at `/mnt/appdax` (`erofs ro,dax=always`).
  - For each package dir in the image it finds the install with `pm path` and checks that the `base.apk` size matches.
  - It then bind-mounts `base.apk` and `oat/<isa>/*` read-only over the installed files.
  - `/vendor/etc/init/appdax.rc` runs it on `sys.boot_completed=1` (for clones, where the app is already installed) and on `sys.agentemu.appdax=1` (the harness sets this after `pm install`).
- The app is still a normal user app: no FLAG_SYSTEM, same install dir, same permissions.
- Proof (`/proc/mounts`): `/dev/block/pmem1 /data/app/~~.../base.apk erofs ...,dax=always`, plus `base.odex` and `base.vdex`; logcat `appdax: com.boltbetz.staging code bound from pmem1 into /data/app/~~...`.
- App smaps afterwards: its `/data/app` mappings are on device `103:05` (pmem1) with ~1 MB counted RSS, down from 11.2 MB on `fe:59`. ART used the bound odex/vdex: there was no dex2oat at launch.
- SELinux is permissive on these images; an enforcing image would need labels for the bind sources (not done).
- Images:
  - **slim4dax** = slim4 (zram on) + appdax, run dir `run-slim4dax`;
  - **slim4nsdax** = slim4ns (no swap) + appdax, run dir `run-slim4nsdax`;
  - both hold `app-pmem.img`. Harness: `soakrun.sh <run-dir> <mem> <tag>`.

**10-min soaks** (`diet_soak.py`: a tap every 20 s, force-stop + relaunch at minute 5). Every end screenshot below was seen by eye and shows the offline dialog.

| Image | `--mem` | Survives 10 min | MemAvailable min | pswpin / pswpout (pages) | kswapd steals | file refaults |
|---|---|---|---|---|---|---|
| slim4ns (reference) | 896 | yes | 16 MB | 0 / 0 | 1,055,425 | 893,366 |
| **slim4nsdax** | 896 | yes | 21 MB | 0 / 0 | 114,433 (−89 %) | **60,627 (−93 %)** |
| slim4nsdax | 768 | **no**: lmkd killed TOP 30 s after launch, `thrashing (17610%)` | n/a | 0 / 0 | n/a | n/a |
| **slim4dax (zram on)** | **768** | **yes** (relaunch 778 ms) | 41 MB | 31,506 / 38,292 | 88,809 | **12,871** |
| slim4dax (zram on) | 640 | yes (relaunch 888 ms) | 14 MB | 255,764 / 272,526 | 478,698 | 113,973 |

- **Smallest size that survives 10 min:**
  - with zram: **640 MB**, but with heavy churn (~1 GB swapped each way, 114k refaults);
  - without swap: **896 MB**.
- **Refault rate:**
  - slim4dax at 768: 12,871 per 10 min, **1.4 % of slim4ns at 896** (893,366);
  - slim4nsdax at 896: 60,627, 6.8 %.
- `run-slim4dax/READY` = `768`. That is the lowest size with low churn: 123 MB swapped in, 150 MB out, 50 MB of refaults over 10 min. `run-slim4nsdax/READY` = `896`.
- zram off at 640 was not run, because 768 already fails without swap.
- **Why no-swap still fails at 768:** the remaining file pages are not DAX. In the 768 app's smaps the top file mappings are `/apex/com.google.cf.vulkan` 18 MB (loop on `/data`), `/product/app` 11 MB, and `/apex/com.android.art` 9.7 MB (decompressed apex on `/data`). system_server maps 53 MB from `/system_ext/priv-app`.
- **Next lever:** put `system_ext`, `product` and `vendor` on pmem too. They are already uncompressed erofs, but are dm-linear on super with hashtree; the change is a first-stage fstab like `/system`. The APEXes are harder, because their loop devices are never DAX.

### slim4dax2: system_ext, product, vendor on pmem DAX; APEXes uncompressed (2026-10-08)

**Partitions on pmem:**
- `fstab_overlay2.py` (`AE_PMEM_MAP=system=0,system_ext=2,product=3,vendor=4`) rewrites each partition's first-stage erofs line to `/dev/block/pmemN /<part> erofs ro,dax=always wait,first_stage_mount`, dropping `logical` and avb. pmem1 stays the appdax image.
- The images are the tree's own `IMAGES/{system_ext,product,vendor}.img`, already uncompressed chunk-based erofs; the trailing hashtree is ignored. Padded to 2 MiB: `stage2/slim4dax2/{system_ext,product,vendor}-pmem.img` (203 / 296 / 180 MB).
- crosvm order: `--pmem` system, app, system_ext, product, vendor.
- Kernel log: `__mount(source=/dev/block/pmem3,target=/product,type=erofs)=0: Success`, and the same for pmem2 `/system_ext`, pmem4 `/vendor` and pmem0 `/system`.

**ART APEX:**
- Android 16 apexd has no flattened-APEX mode: no `flattened` / `ro.apex.updatable` strings in `/system/bin/apexd`. So an APEX payload cannot be DAX-mapped; it is always a loop-mounted image.
- What was done: every `/system/apex/*.capex` (26) is replaced by its `original_apex` entry (the signed `.apex`, extracted with `unzip`), so apexd loop-mounts from `/system` on pmem. Nothing is decompressed into `/data/apex/decompressed` any more (0 `apexd ... decompress` lines in the boot log). system grew 925 → 1,086 MB (shared pmem).
- In the app's smaps, `/apex/com.android.art` is now on a loop device backed by `/system` (`07:238`, 7 MB RSS) instead of dm over `/data` (`fe:4c`). These pages still sit in the loop device's page cache, not DAX.

**Build:**
- slim4dax2 = slim4dax (zram on + appdax) + uncompressed APEXes; `stage2/slim4dax2/edits.sh`, `build.sh`, `initrd-dax-pmem2.img`; run dir `run-slim4dax2`.
- No-swap variant: the same run dir with `vendor-pmem.img` = slim4nsdax's vendor (no zram, no-swap lmkd props, appdax; `vendor-ns.sh`). Run dir `run-slim4nsdax2`. Because `/vendor` now comes from its own pmem image, only that image differs.
- Soak runner: `soakrun.sh` with `MORE_PMEM` for the three partition images.

**10-min soaks** (a tap every 20 s, force-stop + relaunch at minute 5). Every end screenshot was seen by eye and shows the offline dialog.

| Image | `--mem` | Survives | MemAvailable min | pswpin / pswpout | kswapd steals | file refaults |
|---|---|---|---|---|---|---|
| slim4dax (app DAX only) | 640 | yes | 14 MB | 255,764 / 272,526 | 478,698 | 113,973 |
| **slim4dax2** | **640** | **yes** (relaunch 845 ms) | 11 MB | 101,819 / 118,293 | 192,897 (−60 %) | **32,175 (−72 %)** |
| slim4dax2 | 576 | yes (relaunch 664 ms) | 10 MB | 316,228 / 326,344 | 533,121 | 103,932 |
| slim4nsdax2 (no swap) | 768 | **no**: lmkd killed TOP right after launch, `thrashing (5407%)` (slim4nsdax: 17610 %) | n/a | 0 / 0 | n/a | n/a |
| slim4ns (reference) | 896 | yes | 16 MB | 0 / 0 | 1,055,425 | 893,366 |

- `run-slim4dax2/READY` = `640`: the smallest size with low churn.
  - Refaults are 3.6 % of slim4ns at 896.
  - Swap traffic is ~400 MB each way per 10 min, versus ~1.2 GB at 576.
- 576 survives, but swaps ~1.2 GB each way.
- **No swap still fails at 768.** The file side is now mostly DAX, but anon does not fit: the app is ~218 MB RSS and mostly anon, plus system_server and the kernel, in 724 MB with nothing to reclaim anon into. Without swap the floor stays at 896.
- What is left in page cache: the APEX loop devices (ART 7 MB in the app), `/data` app data, and dalvik-cache.

### Swap-churn tuning at 640 MB; slim4dax3 (2026-10-08)

Goal: fewer guest page rewrites (clone cost tracks them). Base: slim4dax2, 640 MB, zram on.
- Protocol: `diet_soak.py`, 10 min, a tap every 20 s, relaunch at minute 5.
- Runtime knobs are applied by `soakrun.sh` `KNOBS` right after install (`tune640.sh` lists every set).
- Guest defaults: `swappiness` 60, `page-cluster` 3, MGLRU on (`0x0007`), zram lz4 (zstd available). `watermark_scale_factor` is computed by `extra_free_kbytes.sh` (163 at 640 MB), and `min_free_kbytes` 3603.
- One run per set. Two baseline runs (dax2-640 and sw60, which equals the default) differ by ~1 %.

| Set (runtime unless noted) | Survives | MemAvail min | pswpin | pswpout | kswapd | direct | refaults |
|---|---|---|---|---|---|---|---|
| baseline dax2-640 | yes | 11 MB | 101,819 | 118,293 | 192,897 | 880 | 32,175 |
| sw60 (= default, repeat) | yes | 8 | 103,004 | 117,918 | 196,963 | 1,922 | 30,246 |
| wm: `watermark_scale_factor` 10, `min_free_kbytes` 2048 | yes | 49 | 60,613 | 81,486 | 103,288 | 25,654 | 17,339 |
| sw160 | yes | 10 | 128,934 | 145,719 | 220,553 | 430 | 30,068 |
| pc0: `page-cluster` 0 | yes | 13 | 98,161 | 118,025 | 194,574 | 964 | 33,730 |
| zstd (swapoff, reset, zstd, same disksize, swapon) | yes | 9 | 74,343 | 91,186 | 155,138 | 666 | 31,216 |
| mglru0: `lru_gen/enabled` n | yes | 13 | 62,419 | 80,032 | 191,851 | 652 | **87,301** |
| psi: lmkd partial 70 / complete 300 ms | yes | 9 | 128,611 | 144,896 | 241,882 | 196 | 42,125 |
| combo at runtime (wm then zstd) | no: `swapoff` was killed and zram stayed lz4; the app was later killed (`device is not responding`). Invalid as a runtime test | | | | | | |
| baked v1: zstd + `min_free_kbytes` 2048 (wsf overwritten to 163) | yes | 16 | 58,026 | 76,095 | 128,395 | 4,918 | 20,656 |
| **baked slim4dax3: zstd + `min_free_kbytes` 2048 + `watermark_scale_factor` 10** | **yes** (relaunch 758 ms) | **75** | **38,219** | **49,079** | **58,708** | 20,561 | **8,829** |
| slim4dax3 at 576 | no: lmkd killed TOP, `min watermark is breached and swap is low` | | | | | | |

**Winner: slim4dax3 at 640 MB.** Compared with dax2-640:
- swap-in −62 %, swap-out −59 %, kswapd steals −70 %, refaults −73 %, MemAvailable floor 75 MB;
- direct reclaim goes up (880 → 20,561 pages): the low watermarks push some reclaim into allocations;
- the app survived, with relaunch 758 ms. End screenshot `results/diet/dax3b-640-soak/end-d16.png` shows the offline dialog, seen by eye.

**Findings per knob:**
- **Watermarks** are the biggest lever.
- **zstd** next.
- `page-cluster` 0 makes no difference.
- **Higher swappiness** makes it worse.
- **MGLRU off** trades swap for 3× refaults.
- **More aggressive lmkd PSI** makes it worse: killed background apps restart.

**Trap:** `watermark_scale_factor` cannot just be written at boot. ActivityManager sets `sys.sysctl.extra_free_kbytes`, and `init.rc` runs `/system/bin/extra_free_kbytes.sh`, which rewrites it (163). slim4dax3's vendor rc re-applies 10 after that trigger, and again on `sys.boot_completed`.

**Image:**
- slim4dax3 = slim4dax2 with only the vendor changed (`stage2/slim4dax3/build.sh`; vendor is its own pmem image). In `init.cutf_cvm.rc`:
  - `write /sys/block/zram0/comp_algorithm zstd`;
  - `on post-fs-data`: `write /proc/sys/vm/watermark_scale_factor 10` and `write /proc/sys/vm/min_free_kbytes 2048` before `swapon_all`;
  - the two re-apply triggers above.
- Checked in the guest: `[zstd]`, `watermark_scale_factor=10`, `min_free_kbytes=2048`.
- Run dir `run-slim4dax3` (slim4dax2 files plus the new `vendor-pmem.img`). `run-slim4dax3/READY` = `640`.

Not tried: `watermark_scale_factor` 1 (10 already moved reclaim into direct reclaim), a smaller zram disksize, a zram writeback device.

## Container lever (redroid) (2026-10-08): no gain, ~800 MB per instance

Question: do Android containers on one shared Linux kernel (redroid in WSL2) cost less unique RAM per Device than our crosvm clones (416 MB marginal, 445 MB per Device in the 10-Device proof)? **No. About 800 MB per instance with KSM on, about 950 without.** Data and scripts: `assets/13-redroid/`.

**Setup:**
- Image `redroid/redroid:16.0.0_64only-latest` (Android 16, `BP2A.250605.031.A3`, x86_64 only), Docker 29.1.3 in WSL Ubuntu, `--privileged`, `androidboot.redroid_gpu_mode=guest` (SwiftShader/ANGLE). 720x1280.
- WSL kernel `linux-msft-wsl-6.18.33.2` (the running version) plus `ANDROID_BINDER_IPC=y`, `ANDROID_BINDERFS=y`, `ANDROID_BINDER_DEVICES=""`, KSM already on (`build-wslk.sh`). Modules built and installed (`build-mods.sh`), because Docker needs `xt_addrtype` etc.
- Android 16 needs **ashmem** even with `androidboot.use_memfd=true`: without it surfaceflinger aborts in a loop with `output buffer not gpu writeable` (redroid-doc #934). Mainline dropped ashmem, so `ashmem_linux` comes from redroid-modules PR #23 (`skunpoj/redroid-modules@fix/modern-kernel-6.17-compat`).
- That module **crashed the WSL VM** on first mmap: `kernel BUG at arch/x86/kernel/cet.c:133` in `shmem_zero_setup` from `ashmem_mmap` (kernel IBT, `dmesg-ibt-crash.txt`). The crash also zeroed recently written ext4 files in the distro. Fix: `kernelCommandLine=ibt=off` in `.wslconfig`.
- KSM: `ksmwrap` sets `PR_SET_MEMORY_MERGE` on containerd, which every container process inherits (`ksm_merge_any: yes` checked in system_server and the app). Toggle with `/sys/kernel/mm/ksm/run`, scan 5000 pages / 20 ms.
- No `-p` port maps (no DNAT module loaded), so adb goes to the container IP directly. WSL `memory=6GB` unchanged.
- Protocol as in the clone runs: proof APK (`results/adb/proof.apk`, same bytes as `run/apk.img`) installed per container, launched, tap every 20 s, relaunch at the half-way point, sample every 30 s. Host guard every 30 s (`hostguard.ps1`) never fired.

**Boot and app:** container to `sys.boot_completed` 9.7-11.9 s. Install + launch + 45 s settle ≈ 57 s. `am start -W` TotalTime 266-432 ms. Containers have network, so the app shows the real Welcome screen (carousel, Log In, Create Account), not the offline dialog the VMs show. That makes it a little heavier. All screens looked at: `up-rr1.png`, `up-rr2.png`, `up-rr3.png`, `end-rr4-lowram.png`. App alive in every sample of every run. adb taps p50 18-20 ms, p95 30-159 ms.

**Per-container memory at plateau** (cgroup tree, `smaps_rollup` summed over ~85 processes):

| Run | anon | USS | PSS | KSM saved total |
|---|---|---|---|---|
| 1 instance, KSM off, 10 min (`hold1`) | 745 | 1,063 | 1,357 | 0 |
| 3 instances, KSM off, 10 min (`hold3`) | 730-767 | 848-873 | 1,100-1,121 | 0 |
| same 3, KSM on, 6 min (`hold3ksm`) | 562-599 | 646-658 | 932-948 | 846 MB |
| 2 instances, `ro.config.low_ram=true` + `heapgrowthlimit=128m`, KSM on, 6 min (`hold-lowram`) | 542-598 | 652-654 | — | 486 MB |

**Marginal cost per instance:**

| Measure | KSM off | KSM on |
|---|---|---|
| WSL `MemAvailable` drop, 3 instances / 3 | (5,211 − 2,344) / 3 = **956 MB** | (5,211 − 2,703) / 3 = **836 MB** |
| WSL `MemAvailable` freed, stop one (3→2, 2→1) | — | **801, 787 MB** |
| same, low_ram (2→1) | — | **799 MB** |
| vmmemWSL working-set growth, add one (1→2, 2→3) | +2,002, +1,243 MB (the second one hit the 6 GB WSL cap) | — |
| Host Available freed, stop one (3→2, 2→1) | — | 1,190, 760 MB |

- KSM saves about 300 MB per added instance (saved 241 → 542 → 853 MB at 1/2/3 instances) and ~240 MB inside a single instance. That is real, but it lands at ~800 MB, not under 416.
- `ro.config.low_ram=true` was accepted (`getprop` true) and changed almost nothing (799 MB stop-one).
- The first instance also costs ~2.5 GB of vmmemWSL for image page cache, shared by the rest.
- No 10-instance run: 3 did not look good, and 10 × ~800 MB does not fit the 6 GB WSL cap or the 3,000 MB host floor next to the 8 other crosvm processes.

**Why it loses:** the VM clone shares the whole booted guest (zygote heap, system_server, every service) copy-on-write from one snapshot, so a clone only pays for pages it writes (~340 MB). A container shares the kernel and the read-only image files, but every container boots its own Android userspace: its own zygote, system_server, SystemUI, launcher and ~85 processes, each with private anon heap (~730-770 MB per container). KSM finds only part of that as identical. Also, this redroid image is a full Android 16, not our slim4/slim5 diet.

**Downsides for the product, even if RAM were equal:**
- Needs a custom WSL kernel (binder, binderfs), an out-of-tree ashmem module, `ibt=off`, and `--privileged` containers. One bad module mmap takes down the whole WSL VM, and with it every Device and every other WSL user (Docker Desktop, image builds).
- No WHPX: it runs inside the single WSL VM, so the 6 GB `.wslconfig` cap (shared with Docker Desktop and builds) bounds the whole fleet, and per-Device host accounting is only possible from inside Linux.
- Screen and input go through adb/scrcpy (`screencap`, `input tap`), not our virtio-gpu scanout and virtio-input path (36 ms p50 input to frame).
- Android runs on the host Linux kernel (6.18 WSL), not a GKI Android kernel. Kernel-side Android features (ashmem, binder variants, SELinux policy, vendor modules) depend on what WSL's kernel supports.
- GPU: guest mode is SwiftShader in software. Android 16 surfaceflinger already depends on the ashmem workaround; host GPU mode needs `/dev/dri`, which WSL exposes only as `dxg`.

**State left:** `.wslconfig` restored from `.wslconfig.bak-redroid-2026-10-08` (stock kernel back, `uname -r` = `6.18.33.2-microsoft-standard-WSL2`). Docker and containerd in Ubuntu are disabled (installed, not started). The redroid kernel, modules and ashmem build stay at `C:/dev/agent-emu-work/redroid/` and `/root/wslk`, `/lib/modules/6.18.33.2-redroid+` in Ubuntu, if anyone wants to rerun this. No follow-up needs them.
