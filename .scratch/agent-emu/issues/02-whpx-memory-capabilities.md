# WHPX memory capabilities

Type: research
Status: resolved
Blocked by:

## Question

What can the Windows Hypervisor Platform (WHPX) and the Windows memory APIs do for guest memory? Answer these:

- Can guest physical memory be backed by a file mapping or by section objects shared read-only across several VMs, with copy-on-write per VM?
- Is there a Windows equivalent of KSM (page dedup across VMs) or userfaultfd (lazy, on-demand page fill)? For example WHvAdviseGpaRange, memory partitions, or Hyper-V memory features exposed to WHPX callers.
- Can a VM return unused memory to the host (balloon, free-page reporting, decommit)?
- Is there dirty-page tracking for snapshots?

Fork, the shared base image and the 400 MB per-Device budget all rest on this answer.

## Answer

- **Shared read-only base: yes.** `WHvMapGpaRange` backs GPA with any page-aligned process VA, including a `MapViewOfFile` section view; Hyperlight maps one read-only file section into many partitions via `WHvMapGpaRange2`. (MS WHvMapGpaRange, MS overview, Hyperlight `surrogate_process.rs`; both passes.)
- **CoW per VM: build it in the VMM.** Map the base Read|Execute, take the write as a memory-access exit, copy the page to private memory, remap that 4 KB writable (mapping "replaces any previous mappings"). Documented pieces; never run end to end. (MS WHvMapGpaRange, MS MemoryAccess; both passes.)
- **Transparent `PAGE_WRITECOPY`/`FILE_MAP_COPY` CoW under a writable GPA mapping: unverified.** No doc or code found doing it. `FILE_MAP_COPY` charges commit for the whole view. (MS MapViewOfFile; both passes.) A two-partition probe settles it.
- **userfaultfd equivalent: yes in function, x64 only.** With `GpaAccessFaultExit` on, a mapped GPA with no SLAT entry exits to the VMM; the VMM fills/commits, then `WHvAdviseGpaRange(Populate)`. OpenVMM ships this for lazy commit and soft large pages; aarch64 WHP "does not deliver these faults". (OpenVMM `virt_whp/src/lib.rs` `supports_memory_fault_resolution`, `vp.rs` memory-access handler, checked 2026-10-07.) Unmapped-GPA exits give a second lazy-fill path (MS WHvUnmapGpaRange).
- **KSM equivalent: none in WHPX.** No dedup export in `WinHvPlatform.h` 10.0.26100.0 (Pass A). Windows page combining exists (`Enable-MMAgent -PageCombining`) but targets "process private page-able memory" (MS 2012 blog) and its reach into guest RAM is unproven. Budget with no dedup.
- **Memory partitions: not usable.** `NtCreatePartition` is undocumented and not exposed by WHPX (Geoff Chappell, Pass A); `MemExtendedParameterPartitionHandle` has no documented semantics (MS MEM_EXTENDED_PARAMETER_TYPE, Pass B).
- **Return memory to host: yes.** crosvm WHPX balloon: inflate = `WHvUnmapGpaRange` + `OfferVirtualMemory`; deflate = `ReclaimVirtualMemory` + zero + remap (crosvm `hypervisor/src/whpx/vm.rs` lines ~341-430). Offer is not a guaranteed discard (MS OfferVirtualMemory).
- **Strict decommit needs private `VirtualAlloc` backing.** `VirtualFree(MEM_DECOMMIT)` releases pages (OpenVMM `sparse_mmap` decommit), but committed pages of a pagefile `SEC_RESERVE` section "cannot be freed or decommitted with the VirtualFree function" (MS CreateFileMappingW). Shared base and reclaimable private RAM need separate backing.
- **Free-page reporting: no WHPX API; VMM job, unverified.** Virtio reporting lets the guest reuse a page after ack with no deflate message, so the host needs a recommit-on-access path (virtio 1.3 §5.5.6.7, Pass B). No crosvm WHPX reporting path found (Pass A).
- **Dirty tracking: yes.** Map with `WHvMapGpaRangeFlagTrackDirtyPages`, read with `WHvQueryGpaRangeDirtyBitmap`; size 0 + NULL clears without reading; unregistered range returns `WHV_E_GPA_RANGE_NOT_FOUND` (MS WHvQueryGpaRangeDirtyBitmap). QEMU WHPX marks all RAM dirty instead (QEMU `whpx-common.c`, Pass A).
- **Dirty tracking gaps.** It covers guest writes; host/device writes need separate tracking (Pass B, inference). crosvm's balloon deflate remaps with `track dirty pages = false` (crosvm vm.rs, checked), so copying it unchanged loses tracking on those pages.
- **This PC supports both key features.** `WHvGetCapability` re-run 2026-10-07: HypervisorPresent=1, Features=`0x2ff` (incl. DirtyPageTracking, PartialUnmap), ExtendedVmExits=`0x7fff` (bit 14 GpaAccessFaultExit). Page-combining state unread (needs elevation).
- **Hyperlight limits (unverified in MS docs):** a second partition in one process fails `WHvMapGpaRange` with `0xC0370008`; 512 process handles per caller for `WHvMapGpaRange2` (Hyperlight `surrogate_process_manager.rs`, Pass A). One Device per VMM process avoids both.
- **Fork primitive candidate:** `WHvStartPartitionMigration`/`Accept`/`Complete` move partition state to another process; memory semantics undocumented (MS WHvStartPartitionMigration, Pass A).
- **Net:** shared base, lazy fill, reclaim and dirty tracking all rest on documented APIs plus shipping Microsoft/Google code. Open costs to probe: one exit per first-write page, many 4 KB GPA mappings, and real unique RAM per Device (no measurement yet, both passes).

### Disagreements

- **userfaultfd equivalent.** A: yes (x64, `GpaAccessFaultExit` + Populate). B: none identified, only unmapped-GPA exits. **A wins.** OpenVMM `lib.rs` enables `GpaAccessFaultExit` and states WHP "forwards guest memory-access faults back to the VMM ... (soft large pages, lazy commit)"; `vp.rs` handles "a mapped GPA that wasn't mapped in the SLAT". B is right only that there is no named pager API.
- **Page combining as KSM.** A: no KSM equivalent. B: page combining is "KSM-like", eligibility unverified. **Unresolved** on whether it reaches WHPX guest RAM. Both agree: no WHPX dedup API, budget without it. Settles with: elevated `Enable-MMAgent -PageCombining`, two partitions with identical guest pages, then `QueryWorkingSetEx` share counts or commit totals.
- **Host DirtyPageTracking support.** A: present (live query). B: not queried, unverified. **A wins.** Re-ran `WHvGetCapability` read-only this turn: Features `0x2ff`, ExtendedVmExits `0x7fff`.
- **Decommit as a reclaim path.** A: `VirtualFree(MEM_DECOMMIT)` works. B: not for `SEC_RESERVE` pagefile sections. **B wins (scoping).** MS CreateFileMappingW: committed `SEC_RESERVE` pages "cannot be freed or decommitted with the VirtualFree function". A's OpenVMM decommit is on `VirtualAlloc`-style sparse memory. Section-backed RAM uses Offer/Reclaim (crosvm).
- **Does a dirty-bitmap query reset the bits?** A: undocumented, crosvm implies read-and-clear. B: the overview says "since the previous query". **B wins.** MS hypervisor-platform overview: "pages the guest has modified since the last query of the range"; the function page adds the size-0 clear-only mode.

Context: Pass A (Claude) `C:/dev/worktrees/agent-emu--research-whpx-memory/.scratch/agent-emu/research/02-whpx-memory-capabilities.md`, branch `research/whpx-memory`. Pass B (GPT-6.1 Sol) `C:/dev/worktrees/agent-emu--codex-whpx-memory-capabilities/.scratch/agent-emu/research/02-whpx-memory-capabilities.codex.md`, branch `research/codex-whpx-memory-capabilities` (not yet committed). Reconciled 2026-10-07.
