# Fork design

Type: research
Status: claimed
Blocked by:

## Question

How would Fork work for a crosvm Device on WHPX, so the v1 memory layout leaves room for it? Answer these:

- Copying the guest RAM image: a copy-on-write file mapping plus lazy fill through `GpaAccessFaultExit`.
- Snapshotting device state: crosvm snapshot code on Windows.
- Cloning the disk overlay.
- Re-identifying a clone: MAC, Android ID, randomness.
- What the v1 VMM must do now, such as file-backed guest RAM, so Fork stays possible later.
