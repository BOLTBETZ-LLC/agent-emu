# Guest image source

Type: research
Status: resolved
Blocked by:

## Question

Where do Cuttlefish `aosp_cf_x86_64_slim` API 36 images come from for this project? Answer these:

- Does ci.android.com (or another Google channel) publish prebuilt slim images: `img.zip`, kernel and `cvd-host_package`? Which branch, how recent, and what is in them?
- Building from AOSP source: what does the build need (disk, RAM, OS)? Can it run on this PC (32 GB RAM, Windows 11) inside WSL2, or does it need a cloud Linux builder?
- How is a custom kernel (GKI android16-6.12 plus `FS_DAX`/`ZONE_DEVICE`) built and swapped into a prebuilt image?
- How is the image rebuilt as uncompressed, non-inline erofs without dm-verity?

## Answer

Reconciled 2026-10-07 from two blind passes. Disagreements were checked against primary sources on the same day (Android Build API v4, googlesource `?format=TEXT`).

- **No slim prebuilt exists.** Android Build API v4 shows `aosp_cf_x86_64_slim-userdebug` last built 2023-07-06 (`aosp-master-throttled`, 10448730), and `aosp_cf_x86_64_slim-trunk_staging-userdebug` has never been built. Slim has to be built from source.
- **Public Cuttlefish prebuilts:** `aosp-android-latest-release` builds `aosp_cf_x86_64_only_phone-userdebug` monthly. It moved to API 37 on 2026-06-16 (15660610). The last **API 36** build is **15581820** (2026-06-06, `BP4A.251205.006`, kernel 6.12.38-android16-5). Pin it. It ships `img.zip`, `target_files.zip`, `otatools.zip` and `cvd-host_package.tar.gz` (the host package holds Linux tools only).
- **only_phone vs slim:** both build `system` from the same `core_64_bit_only.mk` + `generic_system.mk` on `vsoc_x86_64_only`. They differ in system_ext, product and vendor (slim drops SystemUI/Launcher/Settings and turns drawing off). The DAX work on `system` carries over.
- **Prebuilt erofs is not DAX-ready:** `lz4hc,9` compression plus a hashtree (`avb_system_hashtree_enable=true`). `mkfs.erofs` rebuild mode rejects compressed source inodes, so repack from `target_files`, not from `system.img`.
- **erofs repack, no AOSP build needed:** in `META/misc_info.txt` set `erofs_default_compressor=none`, `erofs_share_dup_blocks=true` (adds `--chunksize 4096`, so every regular file is CHUNK_BASED and never inline), `avb_system_hashtree_enable=false`, and drop `erofs_sparse_flag`. Delete `IMAGES/system.img` and run `add_img_to_target_files`. Fallback: run `mkfs.erofs -b 4096 -E noinline_data` (no `-z`) directly with the `META` fs_config and file_contexts.
- **Kernel 6.12 sets `S_DAX` for both FLAT_PLAIN and CHUNK_BASED** regular files (android16-6.12 `fs/erofs/inode.c` L183-186), so the chunk-based route is DAX-eligible. A `dax=always` mount can still quietly fall back to no DAX. Check that DAX is actually active on real files.
- **Kernel:** the daily `kernel_virt_x86_64` CI kernel (16542785, 6.12.93) has `# CONFIG_ZONE_DEVICE is not set` and no `FS_DAX`. Both are bool options inside vmlinux, so the kernel must be rebuilt: `repo init -b common-android16-6.12` (pin a dated branch), then `tools/bazel run --defconfig_fragment=//common:agent_emu_dax_defconfig //common-modules/virtual-device:virtual_device_x86_64_dist`, with a fragment containing `CONFIG_ZONE_DEVICE=y` and `CONFIG_FS_DAX=y`.
- **Keep `CONFIG_VIRTIO_PMEM=m`.** `virtio_pmem.ko` is in the `virtual_device_x86_64` module set, and the dist initramfs installs it, so first-stage init loads it.
- **Kernel swap:** new `bzImage` + `initramfs.img`, plus new vendor_dlkm/system_dlkm, because the old modules do not match the new kernel. Cuttlefish does this with `-kernel_path`/`-initramfs_path` (`kernel_ramdisk_repacker.cpp`). The crosvm fork must do the same repack itself with `otatools` (`mkbootimg`, `lpmake`) or boot the kernel directly (ticket 13).
- **dm-verity off:** turn the image hashtree off (above), and in the first-stage fstab in `vendor_boot` remove **both** `avb=vbmeta_system` and `avb_keys=/avb` (fs_mgr still sets up a hashtree with only `avb_keys`). Point `/system` at the pmem device with `ro,dax=always`, then regenerate or disable vbmeta.
- **Building slim from source:** Google requires 64 GB RAM, 400 GB disk and 64-bit Linux. This PC has 32 GB of RAM, and WSL2 has only `docker-desktop`. A build inside WSL2 with swap is unverified. The safe path is one run on a 64 GB+ cloud Linux builder. The kernel (Kleaf) build is far lighter and plausible in WSL2 (unverified).
- **API 36 source tags:** `android-16.0.0_r4` (tag 2025-12-02, `BP4A`, matches 15581820) and `android-security-16.0.0_r9` (tag 2026-10-05). Use r4 so source and prebuilt line up. Use r9 only if a newer patch level matters.

### Recommended guest image path for the spike

Critical path: get the kernel build and the target_files download going at once, then repack, then boot. Nothing else is on the path.

1. Add an Ubuntu WSL2 distro. Two jobs then run side by side: (a) download 15581820 `aosp_cf_x86_64_only_phone` `target_files.zip`, `img.zip` and `otatools.zip` (about 3.8 GB); (b) Kleaf-build `common-android16-6.12` `virtual_device_x86_64_dist` with the DAX fragment.
2. Repack `system` from `target_files` with the four `misc_info.txt` edits and `add_img_to_target_files`. The result is raw, uncompressed, CHUNK_BASED erofs with no hashtree.
3. Repack `vendor_boot` with the new `initramfs.img` and a pmem fstab (`/system` on pmem, `ro,dax=always`, no `avb`/`avb_keys`), and disable vbmeta verification. Leave every other partition from `img.zip` untouched.
4. Boot it on the crosvm fork. Pass = the proof app's first screen, with `S_DAX` confirmed on a `/system` file. Turn drawing on if it is off (only_phone has it on).

Skip for the spike: building slim from source, converting system_ext/product/vendor to pmem, and an APEX DAX patch.

**Effect on Lock the route:** ticket 08's route stands. Slim stays the target for memory, but the spike should start from `aosp_cf_x86_64_only_phone` API 36 prebuilt **15581820**. No slim prebuilt has existed since 2023, and slim needs a full AOSP build on a 64 GB+ builder. The `system` partition is built from the same makefiles, so the pmem/DAX/erofs work carries over. Build slim (from `android-16.0.0_r4`) only when the memory budget (ticket 09) needs the smaller system_ext/product/vendor.

### Disagreements

1. **Is a slim prebuilt published?** A: none since 2023-07-06. B: unverified (its CI reader saw nothing). **A wins.** A v4 API query on 2026-10-07 returned 10448730 (2023-07-06) as the newest slim build, and nothing for the trunk_staging target.
2. **API 36 pin.** A: prebuilt 15581820 and source tag `android-16.0.0_r4`. B: source tag `android-security-16.0.0_r9`. **A wins for the spike**, because r4 matches the prebuilt (BP4A). B's r9 date (2026-10-05) is correct, and r9 is a fine choice when patch level matters. Both are API 36.
3. **How to get FS_DAX/ZONE_DEVICE into vmlinux.** A: a virtual-device fragment is not enough; edit `gki_defconfig` or use `base_kernel = None`. B: the `--defconfig_fragment` flag reaches the base kernel too. **B wins.** `kleaf/impl/kernel_build.bzl` L980-990 appends `//build/kernel/kleaf:defconfig_fragment` to every `kernel_build`'s inherited post fragments, including `//common:kernel_x86_64`. Whether GKI KMI/ABI checks pass with ZONE_DEVICE on is **unresolved**. A first Kleaf build settles it.
4. **`CONFIG_VIRTIO_PMEM` =y or =m.** A: =y, so `/system` mounts in first stage. B: keep =m. **B wins.** `virtio_pmem.ko` is in `virtual_device_x86_64`'s module set (BUILD.bazel L194/L212), and the dist initramfs installs that set. A =y override would also clash with the `virtual_device.fragment` `=m` match check.
5. **Non-inline erofs without patching the build.** A: `compressor none` + `share_dup_blocks` (CHUNK_BASED) needs no `build_image.py` change. B: `build_image.py` needs an explicit `-E noinline_data` change. **A wins.** erofs-utils r4 `lib/inode.c` L560-569 and L763-765 make uncompressed files chunk-based and skip inlining. Kernel `inode.c` L183-186 gives CHUNK_BASED `S_DAX`. B's direct `mkfs.erofs -E noinline_data` stays the fallback. Whether deduped chunks behave under DAX is **unresolved**. A boot-time `S_DAX` check plus reading a shared-chunk file settles it.
6. **Are ZONE_DEVICE/FS_DAX off in GKI today?** A: off (read from the CI `.config`). B: don't trust an absent defconfig line. **A wins.** A read the real built `.config` of 16542785, which is the inspection B asked for.

Found by one pass only: the fstab `avb_keys` trap and the `dax=always` silent fallback (B). Artifact sizes and contents, the full build list, the `misc_info.txt` settings, the rebuild-mode rejection and the Cuttlefish repacker steps (A).

**Context:** [Pass A (Claude)](../research/12-guest-image-source.md), branch `research/guest-image` (`C:/dev/worktrees/agent-emu--research-guest-image/.scratch/agent-emu/research/12-guest-image-source.md`). [Pass B (GPT-6.1 Sol)](../research/12-guest-image-source.codex.md), branch `research/codex-guest-image-source` (`C:/dev/worktrees/agent-emu--codex-guest-image-source/.scratch/agent-emu/research/12-guest-image-source.codex.md`).
