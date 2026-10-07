# Shared system image on Windows

Type: research
Status: claimed
Blocked by:

## Question

How can a Device on WHPX map file-backed system pages (`.oat`, `.vdex`, `.jar`, `.so`, the system partition) from one host file shared across all Devices, so they count once for the Fleet? Answer these:

- Public crosvm on Windows has no virtio-pmem and no virtio-fs (see crosvm on Windows hypervisor). What would it take to add DAX-capable virtio-pmem or virtio-fs on WHPX? Do OpenVMM's Windows virtio-pmem or virtio-fs support DAX?
- Can the WHPX read-only shared base mapping (see WHPX memory capabilities) back a virtio-pmem region directly?
- How do the Linux guest and Android need to be configured to run the system partition from DAX (erofs on pmem, `dax=always`)? Does API 36 Cuttlefish support it?
- Estimate how many MB per Device this saves, with sources.
