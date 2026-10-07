# WHPX memory capabilities

Type: research
Status: open
Blocked by:

## Question

What can the Windows Hypervisor Platform (WHPX) and the Windows memory APIs do for guest memory? Answer these:

- Can guest physical memory be backed by a file mapping or by section objects shared read-only across several VMs, with copy-on-write per VM?
- Is there a Windows equivalent of KSM (page dedup across VMs) or userfaultfd (lazy, on-demand page fill)? For example WHvAdviseGpaRange, memory partitions, or Hyper-V memory features exposed to WHPX callers.
- Can a VM return unused memory to the host (balloon, free-page reporting, decommit)?
- Is there dirty-page tracking for snapshots?

Fork, the shared base image and the 400 MB per-Device budget all rest on this answer.
