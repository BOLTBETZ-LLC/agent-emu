# Fleet manager

Type: grilling
Status: resolved
Blocked by: 08

## Question

How are Devices started, stopped, watched and capped on the host?

## Answer

Recommended default, recorded under Aaron's standing order of 2026-10-07 ("just do everything you recommend").

- One Rust daemon, `agent-emud`, owns the Fleet: start, stop, health, leases, the binary API and MCP.
- Each Device is one crosvm child process, wrapped in a Windows Job Object. The Job Object caps that Device's memory and kills the crosvm process when the daemon exits.
- Each Device gets 2 vCPUs by default. Idle Devices run at below-normal priority so active ones get the CPU.
- Health check: the crosvm process is alive, `sys.boot_completed` is set, and a frame has posted in the last N s. A Device that crashes is restarted once, then reported.
- Device config (image, RAM, screen, network) is a small file the daemon saves. Fork needs the same file (Fork design seam).
