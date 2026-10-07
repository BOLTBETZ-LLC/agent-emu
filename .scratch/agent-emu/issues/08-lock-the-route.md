# Lock the route

Type: grilling
Status: resolved
Blocked by: 01, 02, 03, 05

## Question

With the research in hand, confirm or kill Route A (a slim real-kernel VM, crosvm-style, on WHPX). If Route A is killed, choose among:

- A Linux host inside WSL2 (KVM, KSM and userfaultfd available, with GPU passthrough risk).
- Route B, a no-VM simulator (Starnix-style syscall layer).
- Another option the research surfaces.

Also decide whether to fork an existing VMM or build one on rust-vmm crates.

## Answer

Grilled with Aaron on 2026-10-07. Route A is confirmed.

- **VMM:** fork public crosvm and run it on WHPX. The fork adds a headless display, the gfxstream backend, Windows virtio-pmem (DAX), drawing turned on, and a boot test in CI. Evidence: crosvm on Windows hypervisor, WHPX memory capabilities. Google's Play Games ships a private crosvm fork on Windows.
- **Why not the alternatives:**
  - OpenVMM has no virtio-gpu and no balloon, and its pmem is broken for Linux guests (issue #4048).
  - A Linux host inside WSL2 risks nested virtualization and GPU passthrough to guests, and adds one more VM's overhead.
  - Route B (simulator) has no Windows base at all.
- **Why the VMM matters most:** the stock emulator charges 3.1-3.3 GB of host unique memory per Device while the guest uses 1.1-1.35 GB (Baseline on stock emulator). The host side is where most of the waste is.
- **Guest:** Cuttlefish `aosp_cf_x86_64_slim`, API 36, no GMS. It is already built for crosvm. The kernel config adds `FS_DAX` and `ZONE_DEVICE`. The system image is erofs, uncompressed, with no inline data and no dm-verity. Drawing is turned back on (slim and ATD ship `debug.hwui.drawing_enabled=0`).
- **Process layout:** one crosvm process per Device. Windows can't map one process's memory into several VMs (OpenVMM `membacking/src/lib.rs` L84-86), and a crash stays contained. One Rust fleet daemon owns leases, the binary API and MCP.
- **Build vs fork:** fork. Reuse crosvm's device models, and write new code only where Windows is missing pieces.
- **Biggest risk, still unproven:** whether crosvm on WHPX boots API 36 slim at all. No public evidence exists, and Windows CI has been off since 2025-03-13.
