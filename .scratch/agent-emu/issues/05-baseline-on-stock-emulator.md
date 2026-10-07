# Baseline on stock emulator

Type: task
Status: open
Blocked by: 04

## Question

Measure the proof app on the stock emulator to set the baseline. Run it on a stock API 36 x86_64 image, with an ATD (Automated Test Device) image as the closest to slim:

- Unique memory of the emulator process on the host at the app's first screen.
- Guest per-process memory (`dumpsys meminfo`, RSS/PSS) for the kernel, system_server, zygote, SystemUI and the app.
- Time from cold boot to the first screen.

Use the existing streams tooling under its locks (`streams.py`). Record the exact commands and numbers. This shows how far the stock setup is from 400 MB and where the memory goes.
