---
name: agent-emu
description: Operate agent-emu, the Android phone fleet (d0..dN) on this PC, to test the BoltBetz app. Use when testing BoltBetz on a phone or emulator, running or adding a test case or lane, starting/stopping/checking phones, reading a phone screen (ui_tree, screenshot, logs, crashes), tapping or typing on a phone, sending BoltBetz QA deep links (route jumps, faults, machine QR), or signing a lane account in.
---

# agent-emu

Full guide: `C:\dev\agent-emu\AGENTS.md`. Read the section a step names when you reach it.

## Core loop

1. **Up?** `curl -s -m 2 http://127.0.0.1:7401/health` = `ok`; `status` shows your phone in phase `ready`.
   Not up: AGENTS.md "60-second start". Starting or stopping a phone someone else uses is off limits: AGENTS.md "Rules".
2. **Where am I?** `ui_tree` (~80 ms). Match `resource-id` (the RN testID) first, then `text`. Screenshot when you
   need to see it.
3. **Act.** Tap a node's bounds center with `"device_px": true`; inputs with `"screenshot": false`. Jump with
   `deep_link boltbetz-staging://e2e-session/route/...`, fail an endpoint with `.../fault?endpoint=...&preset=500`.
   AGENTS.md "Act", "BoltBetz QA hooks".
4. **Verify the outcome.** Expected testID present in `ui_tree`, screenshot looked at, `crash_events` after the
   starting seq empty, `logs` (level E, cursor from before) free of fatal JS errors. AGENTS.md "Verify".
5. **Repeatable?** Write it as a case and run it alone:
   `cd C:/dev/agent-emu/.scratch/test-matrix/runner && python run.py --phones d3 --case <id>`.
   Check for a live run first. AGENTS.md "Test runner".

Stuck: AGENTS.md "Gotchas", then "Troubleshooting".

## Calling the daemon

- MCP tools `agent-emu` (`status`, `start`, `ui_tree`, `tap`, `deep_link`, `logs`, ...): AGENTS.md "MCP setup".
- Or HTTP: `curl -s localhost:7401/api -d '{"call":"ui_tree","device":"d3"}'`. Every reply has `ok`; read it.

## Hard lines

Leave every process and other agents' phones alone. One run per phone. Never press Back on the Start screen.
Sign-in codes go from the Gmail connector straight into the phone, never into a file. Staging Synkros mode changes
only with Aaron. Production stays read-only.
