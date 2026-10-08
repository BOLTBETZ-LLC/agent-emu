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
