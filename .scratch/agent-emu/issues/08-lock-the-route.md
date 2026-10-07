# Lock the route

Type: grilling
Status: open
Blocked by: 01, 02, 03, 05

## Question

With the research in hand, confirm or kill Route A (a slim real-kernel VM, crosvm-style, on WHPX). If Route A is killed, choose among:

- A Linux host inside WSL2 (KVM, KSM and userfaultfd available, with GPU passthrough risk).
- Route B, a no-VM simulator (Starnix-style syscall layer).
- Another option the research surfaces.

Also decide whether to fork an existing VMM or build one on rust-vmm crates.
