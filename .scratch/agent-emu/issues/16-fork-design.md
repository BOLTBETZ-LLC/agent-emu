# Fork design

Type: research
Status: resolved
Blocked by:

## Question

How would Fork work for a crosvm Device on WHPX, so the v1 memory layout leaves room for it? Answer these:

- Copying the guest RAM image: a copy-on-write file mapping plus lazy fill through `GpaAccessFaultExit`.
- Snapshotting device state: crosvm snapshot code on Windows.
- Cloning the disk overlay.
- Re-identifying a clone: MAC, Android ID, randomness.
- What the v1 VMM must do now, such as file-backed guest RAM, so Fork stays possible later.

## Answer

Reconciled 2026-10-07. Disputed points re-checked against crosvm `1ca5899813` (2026-10-07) via `gh api`, rutabaga_gfx `HEAD` `ced6af5b11` (2026-10-05), Cuttlefish `182ab1830a`, and the android16-6.12 kernel tree (python urllib). Nothing built or run.

- **Fork = pause, checkpoint, restore into a fresh crosvm process.** Checkpoint = RAM + vCPU + IRQ chip + devices + writable disks, all from one pause. crosvm already has the Windows pieces: suspended-start restore (`do_restore`), WHPX XSAVE/interrupt/TSC state, and a freeze-vCPUs-then-devices transaction. Snapshot is "100% not supported" upstream, so treat it as starting code, not a feature (both passes; crosvm `docs/book/.../snapshotting.md`).
- **RAM today is eager.** crosvm `GuestMemory::snapshot` writes every byte (on Windows the whole mapping), and `restore` reads it all back (`guest_memory.rs`). That works without file-backed RAM. Clones then cost full private RAM each.
- **Lazy CoW RAM is the later optimization.** Two candidate mechanisms, both unverified under WHPX: (1) clone RAM is a `FILE_MAP_COPY` view of the template file, filled on `GpaAccessFaultExit` + `WHvAdviseGpaRange(Populate)` (Pass A); (2) explicit ownership: map template pages read-only, copy into a private page on write fault, remap writable (Pass B; Pass A's fallback). OpenVMM's WHP backend already does fault-driven populate (`virt_whp/src/vp.rs`), so the exit path is real.
- **`FILE_MAP_COPY` charges commit for the whole view.** 10 clones of a 2 GiB guest add ~20 GiB commit charge, not RAM (both passes, MS `MapViewOfFile`; Pass A estimate). The pagefile must be big enough.
- **Out-of-process devices break lazy CoW.** Windows crosvm spawns block, net, slirp, snd and GPU as child processes (`broker.rs` `spawn_child` L1260, L1516, L1552, L1847, verified). CoW pages are private to one process, so those children would see the template, not the clone's pages. Cloud Hypervisor rejects lazy restore with vhost-user for the same reason (2026-10-07 commits). Eager restore is unaffected.
- **GPU is the real blocker, three layers deep (all verified).** (1) On Windows the GPU is "always vhost-user, even if running in the main process" (`src/sys/windows.rs` L714), and that backend's `snapshot` serializes `()` with a POC TODO (`vhost_user_backend/gpu.rs` L292-301). (2) crosvm's virtio-gpu snapshot is "only 2d mode". (3) rutabaga's cross-domain context has no `snapshot` override, so the default returns `Unsupported` (`rutabaga_core.rs` L251-252, no snapshot in `src/cross_domain/*` at HEAD); gfxstream snapshot is compiled only in Bazel/Android.bp builds, not meson (Pass A).
- **Cuttlefish snapshots only `guest_swiftshader`.** "Only 2D guest_swiftshader is supported for snapshot" (`assemble_cvd/flags.cc` L345-351, verified).
- **Disk: copy the per-Device writable overlay at the pause point,** after devices sleep and a flush. Plain file copy is the correctness path; ReFS block clone (Dev Drive, Win 11 24H2+) is an optional speedup only if a Dev Drive exists. The read-only pmem system image is shared, never copied.
- **Flush errors are swallowed.** The block worker logs a failed flush and still acks it (`device_virtio_block/src/asynchronous.rs` L602-608, verified; Pass B). Fork must fail on a failed flush.
- **Network: clones keep MAC and IP; each Device has its own NAT** (Firecracker network-for-clones; matches ticket 14). Live TCP through slirp and vsock connections drop on restore (Windows vsock sets `needs_transport_reset`).
- **Randomness: VMGenID.** Linux `vmgenid` reseeds the kernel RNG via `add_vmfork_randomness`. crosvm has no VMGenID device (full tree, not truncated, verified). GKI android16-6.12 x86_64 `gki_defconfig` does not set `CONFIG_VIRT_DRIVERS`, and that symbol has no default, so `VMGENID` is off (verified). Userspace PRNGs and app-cached IDs stay cloned; no generic fix exists (Firecracker `random-for-clones.md`).
- **ANDROID_ID stays the same across clones by default.** It is `HmacSHA256(per-user key, app signature)`, and the key is in cloned guest state (both passes, AOSP `SettingsProvider.java`). A template taken before any app asks for ANDROID_ID gives each clone its own key (Pass A).
- **Not a fork primitive:** `WHvStartPartitionMigration` moves a partition, it does not copy one (Pass A, MS doc).

### v1 must-do (only what keeps Fork possible; nothing here builds Fork)

1. **One guest-RAM backing seam in the crosvm fork.** All guest RAM goes through one backing type; v1 ships only the existing pagefile section. Balloon must not hard-wire to the pagefile kind. File-backed `open` (today `unimplemented!()`) waits for Fork.
2. **Device config is one serialized, versioned value the fleet daemon stores.** vCPUs, RAM size, GPA layout, device list and order, PCI slots, negotiated features. No random or host-dependent values in device setup. Restore needs "the right shape".
3. **No guest-visible state outside crosvm and the disk files.** Drop or list any kept Cuttlefish host daemon (e.g. `secure_env`, modem simulator).
4. **Writable disks are plain per-Device files the daemon owns,** separate from the shared read-only pmem image. No composite-disk.
5. **Per-Device isolated NAT** (already ticket 14), so identical clones never collide.
6. **Guest kernel: `CONFIG_VIRT_DRIVERS=y`, `CONFIG_VMGENID=y`, ACPI on.** Two lines in the custom kernel ticket 11 already needs.
7. **Fleet daemon treats a dropped vsock/adb as normal and reconnects.**
8. **GPU mode is a per-Device launch value, not hard-wired,** so a forkable mode can be added later (see GPU conflict).

Not v1: VMGenID device, fault-exit handler, dirty tracking, file-backed RAM, moving devices in-process, GPU snapshot code.

### GPU conflict

Ticket 07 makes the cross-domain blob path required to fit 400 MB (no double copy of ~10 full-screen buffers, ~140-160 MiB vs ~240-260 MiB graphics host RAM). Cross-domain contexts cannot be snapshotted (rutabaga returns `Unsupported`), and a live one fails the whole GPU snapshot. Options:

- **(a) Keep 07's path in v1; build GPU snapshot when Fork is built.** v1 cost 0. Fork cost: write cross-domain snapshot in rutabaga, get gfxstream's snapshot frontend building outside Bazel/Android.bp (Windows build unknown), turn on `VulkanSnapshots`, add crosvm 3D virtio-gpu snapshot, and replace the Windows vhost-user GPU `()` snapshot. All new code, feasibility unproven.
- **(b) Forkable Devices run `guest_swiftshader` (2D virtio-gpu, guest CPU rendering).** The only mode Cuttlefish snapshots today. Cost: CPU rendering (07 estimate 1-2 cores per animating Device), buffers in guest RAM, likely slower settle times against the 50 ms target; still needs the Windows vhost-user GPU `()` snapshot fixed.
- **(c) gfxstream without cross-domain (3D-resource path) plus gfxstream snapshot.** Cost: ~100 MiB more host RAM per Device (breaks the 400 MB budget per 07), and still needs the gfxstream snapshot build work.
- **(d) Fork without GPU state: recreate the GPU in the clone and force a redraw.** Cost: not the exact state the glossary's Fork promises; SurfaceFlinger/app GPU contexts would need a reset path that does not exist. Unverified.

Default: (a), since Fork is designed in but not built; v1 must-do 8 keeps (b) open. Re-grill when Fork is scheduled.

### Disagreements

- **Lazy RAM mechanism.** A: `FILE_MAP_COPY` view + Populate. B: explicit read-only map + copy-on-write-fault + remap; `FILE_MAP_COPY` alone not proven. **Unresolved.** A two-partition probe on one template file (guest write in one, read in the other and in the file; `QueryWorkingSetEx` share flags) settles it.
- **File-backed RAM in v1.** A: port `FileBackedMappingParameters::open` now. B: not needed, eager restore copies bytes. **B wins** for v1: crosvm restore is eager `read_exact` (`guest_memory.rs`), so v1 needs only the seam.
- **Disk clone method.** A: copy the overlay at the pause (plain or block clone). B: freeze overlay as qcow backing, new overlays for parent and child. **A wins:** crosvm's only `DiskControlCommand` is `Resize` (`vm_control/src/lib.rs` L225-228, verified), so B's chain needs a live disk swap that does not exist, and qcow backing tests are ignored on Windows (B's own finding). B is right that a Dev Drive cannot be assumed on this PC; plain copy is the fallback.
- **Where the GPU snapshot breaks.** A: 2D-only virtio-gpu, cross-domain `Unsupported`, gfxstream Bazel-only. B: Windows vhost-user GPU backend snapshots `()`. **Both right,** verified; the gaps stack.
- **Out-of-process devices in v1.** A: move block, net and GPU into the crosvm process now. B: keep them, rebind backend views later. **B wins for v1:** the split only breaks lazy CoW, eager restore works with it, and moving them is real v1 work off the critical path. A is right that lazy CoW later needs in-process devices or eager fill.
- **VMGenID support.** A: crosvm has none and GKI leaves `VIRT_DRIVERS` off. B: unverified. **A wins,** verified (crosvm tree; `gki_defconfig`; `drivers/virt/Kconfig` has no default for `VIRT_DRIVERS`).
- **Resetting ANDROID_ID in a clone.** A: delete ssaid entries and restart the app (idea). B: needs a SettingsProvider key-rotation op; raw XML edits are not established. **Unresolved;** both agree preserve by default. A guest test (rotate, then read ANDROID_ID from a second app) settles it.

Context: Pass A (Claude) `C:/dev/worktrees/agent-emu--research-fork-design/.scratch/agent-emu/research/16-fork-design.md` on branch `research/fork-design`. Pass B (GPT-6.1 Sol) `C:/dev/worktrees/agent-emu--codex-fork-design/.scratch/agent-emu/research/16-fork-design.codex.md` on branch `research/codex-fork-design`.
