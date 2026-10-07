# crosvm on Windows hypervisor

Type: research
Status: open
Blocked by:

## Question

Can the open-source crosvm build and run on Windows 11 with WHPX today and boot an Android x86_64 guest (API 36, Cuttlefish-style or similar)? Answer these:

- What is the state of crosvm's public Windows support: build docs, CI, last commits, and which features are missing on Windows (virtio-gpu, gfxstream, vsock, balloon, snapshot)?
- Does Google Play Games for PC's `crosvm.exe` come from public source, or from a private fork?
- If crosvm on Windows is not viable, which other Rust VMMs run on WHPX (libkrun, Cloud Hypervisor, Hyperlight, others), and what are their gaps?

The route lock depends on this answer.
