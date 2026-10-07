# Boot proof spike plan

Type: grilling
Status: claimed
Blocked by: 12

## Question

What exactly is the first spike that proves Route A, and what result kills it? Cover:

- The steps: build crosvm `all-msvc64,whpx` on this PC, boot slim API 36 headless, reach the proof app's first screen, and take one screenshot through gfxstream.
- The pass/fail numbers to record: boot time, host unique memory, screenshot round trip.
- The time box.
- What happens if it fails (OpenVMM, or a Linux host in WSL2).
