# Portability seams

Type: grilling
Status: resolved
Blocked by: 08

## Question

What keeps the Linux and macOS ports open while v1 ships Windows-only?

## Answer

Recommended default, recorded under Aaron's standing order of 2026-10-07.

- **Hypervisor:** use crosvm's own hypervisor trait (WHPX now; KVM and later HVF already there or planned upstream). No WHPX calls outside the crosvm fork.
- **Memory tricks:** behind one trait in the fork. On Windows: section-backed RAM, `GpaAccessFaultExit` lazy fill, `OfferVirtualMemory`. On Linux: memfd, userfaultfd, KSM.
- **Daemon:** `agent-emud` uses only cross-platform Rust (tokio, std). Job Objects sit behind a small process-cap trait, with cgroups on Linux.
- **Guest:** the image, kernel and erofs/DAX work are host-independent.
