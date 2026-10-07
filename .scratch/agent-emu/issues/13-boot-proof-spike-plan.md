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
