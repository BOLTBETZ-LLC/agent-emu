# Shared system image on Windows

Type: research
Status: resolved
Blocked by:

## Question

How can a Device on WHPX map file-backed system pages (`.oat`, `.vdex`, `.jar`, `.so`, the system partition) from one host file shared across all Devices, so they count once for the Fleet? Answer these:

- Public crosvm on Windows has no virtio-pmem and no virtio-fs (see crosvm on Windows hypervisor). What would it take to add DAX-capable virtio-pmem or virtio-fs on WHPX? Do OpenVMM's Windows virtio-pmem or virtio-fs support DAX?
- Can the WHPX read-only shared base mapping (see WHPX memory capabilities) back a virtio-pmem region directly?
- How do the Linux guest and Android need to be configured to run the system partition from DAX (erofs on pmem, `dax=always`)? Does API 36 Cuttlefish support it?
- Estimate how many MB per Device this saves, with sources.

## Answer

- **Use virtio-pmem with DAX, not virtio-fs.** One immutable raw image, opened by each Device's VMM process, mapped into the guest with `WHvMapGpaRange` Read|Execute and no Write. The guest mounts uncompressed erofs on `/dev/block/pmemN` with `ro,dax=always`. Both passes agree.
- **Physical sharing across Devices is unverified.** Windows says views of one file are coherent across processes (MS CreateFileMappingW / "Sharing files and memory"). Same physical pages under WHPX is inferred, not measured. Settle with a two-process `QueryWorkingSetEx` or RAMMap probe.
- **One VMM process per Device.** OpenVMM's source says "on Windows, it is not possible to map memory from a single process into multiple partitions" (`openvmm/membacking/src/lib.rs` L84-86, main, read 2026-10-07). So a fleet manager can't hold one view for all 10 partitions.
- **crosvm needs a Windows pmem port.** `pmem` and `fs` compile for Linux/Android only. The Windows `MemoryMappingArena` is an empty stub (`mmap_platform.rs` L227-230). WHPX already maps read-only regions. The Windows file mapper hardcodes `PAGE_READWRITE` (`base/src/sys/windows/mmap.rs` L186, main), so a read-only image needs that changed. The pmem worker's `MadvisePageout`, `MsyncArena` and `MadviseRemove` requests (`pmem.rs` L130, L161, L188) need Windows handling or exclusion.
- **Skip crosvm virtio-fs on Windows.** It needs a new FUSE server plus a DAX window. Both passes agree.
- **OpenVMM is not a ready fallback for this.** Its pmem maps the file as `PAGE_EXECUTE_READ` (DAX by design), but it never offers `VIRTIO_PMEM_F_SHMEM_REGION` and config reads return 0, so Linux sees a 0-byte region. Issue #4048 is still open with 0 comments (checked 2026-10-07 via the GitHub API, opened 2026-07-27). It accepts one pmem device only (`cli_args.rs` L824 `Option<VirtioPmemArgs>`). Its virtiofs DAX works only for Windows section objects (SectionFs), not host files.
- **Custom guest kernel required.** GKI android16-6.12 x86_64 `gki_defconfig` has `MEMORY_HOTPLUG`, `MEMORY_HOTREMOVE`, `LIBNVDIMM`, `EROFS_FS`, but no `ZONE_DEVICE` and no `FS_DAX` (read 2026-10-07). `FS_DAX` is a bool, so no module can add it. `VIRTIO_PMEM=m` comes from the virtual-device fragment (L10). It must be built in or loaded from the first-stage ramdisk before `/system` mounts.
- **erofs must be uncompressed and non-inline.** The kernel sets `S_DAX` only for `FLAT_PLAIN` or `CHUNK_BASED` files (`fs/erofs/inode.c` L214-218, master). Android's default compressor is `lz4hc,9` (`core/Makefile` L2142, android-16.0.0_r4), so set `BOARD_*IMAGE_EROFS_COMPRESSOR := none`. mkfs.erofs inlines small files by default, and `-E^inline_data` is "typically useful to enable FSDAX" (mkfs.erofs man L131-132). AOSP `build_image.py` (r4) has no knob for that flag, so the image build needs a patch or a separate mkfs step.
- **Storage stack: no dm-verity, no loop.** dm-linear supports DAX. dm-verity and loop do not. Drop `avb=` and verify the image on the host before exposing it. Proposed fstab line (unverified, never booted): `/dev/block/pmem0 /system erofs ro,dax=always wait,first_stage_mount`.
- **API 36 Cuttlefish does not do this.** Its fstab mounts `/system` erofs with `logical,slotselect,avb=vbmeta_system` and no DAX option (r2 and r4 tags). Pass A found its launcher uses pmem only for hwcomposer state and `access_kregistry`.
- **APEX gap.** apexd mounts pre-installed APEX payloads (ART, bionic, ICU) through loop devices, so their pages stay private per Device. Closing it needs an apexd patch (dm-linear or a separate pmem range per payload). Design idea only, unverified.
- **Cost:** `struct page` metadata is 64 B per 4 KiB, about 16 MiB guest RAM per GiB of pmem per Device (ndctl docs; DSec arXiv 2609.22978). Local `google_atd` API 36 `system.img` is 1,898,971,136 bytes (Pass A, read 2026-10-07). A slim uncompressed image is unmeasured.
- **Savings: 0 MB verified.** Estimate (Pass A, unverified): about 70-185 MB per Device with system/vendor DAX, about 120-300 MB with the APEX patch. Pass B's worked example (estimate): 100 MiB eligible resident pages minus 16 MiB metadata gives about 84 MiB per Device. Only outside anchor: DSec on Firecracker (Linux sandboxes, not Android) reports 40.2% lower peak host memory with erofs on virtio-pmem DAX (paper title confirmed 2026-10-07; the 40.2% figure not re-read).
- **How to settle the savings:** boot the slim image on virtio-blk and on pmem+DAX. Check `STATX_ATTR_DAX` on code files, guest `Cached` and `showmap` for zygote64 and system_server, and host unique memory for two Devices (`QueryWorkingSetEx` or RAMMap).

### Disagreements

- **Default erofs compressor: Pass B wins.** A said `lz4`; B said `lz4hc`. `core/Makefile` (android-16.0.0_r4) L2139-2142 sets `lz4` only when `BOARD_EROFS_USE_LEGACY_COMPRESSION=true`, otherwise `lz4hc,9`. source.android.com's EROFS page also says the default is lz4hc. The fix is the same either way: `none`.
- **Is `COMPRESSOR := none` enough for DAX? Pass B wins.** A treated it as enough. B said inline data must be off too. `inode.c` gives `S_DAX` only to `FLAT_PLAIN` and `CHUNK_BASED`, and the mkfs.erofs man page says `-E^inline_data` is what enables FSDAX. `build_image.py` (r4) passes only `-E legacy-compress`, never `^inline_data`.
- **Is crosvm's Windows file mapping ready for a read-only image? Pass B wins.** A said Windows `MemoryMapping` already takes a protection argument. True, but `MemoryMappingBuilder::build` creates the file section with `PAGE_READWRITE` hardcoded (`mmap.rs` L186, main). That section protection needs changing.
- **How much the crosvm port costs: unresolved.** A estimated "a few hundred lines". B listed more scope: image mapping plus the Msync, MadviseRemove and MadvisePageout requests, which the source confirms exist in `pmem.rs`. Both are estimates. Only writing the port settles it.
- **Savings figure: unresolved.** A gave a 70-185 MB range (estimate). B refused to give a range and showed about 84 MiB at an assumed 100 MiB. Neither has an API 36 measurement. The two-boot comparison above settles it.
- **Where `VIRTIO_PMEM` comes from: both right, A more precise.** B said the GKI x86 config does not enable it. A said the virtual-device fragment sets it to `=m`. Both are confirmed (fragment L10). B's point that it must load before the first-stage `/system` mount still holds.

Context: Pass A (Claude) is `C:/dev/worktrees/agent-emu--research-shared-image/.scratch/agent-emu/research/11-shared-system-image-on-windows.md` on branch `research/shared-image`. Pass B (GPT-6.1 Sol) is `C:/dev/worktrees/agent-emu--codex-shared-system-image-on-windows/.scratch/agent-emu/research/11-shared-system-image-on-windows.codex.md` on branch `research/codex-shared-system-image-on-windows`.
