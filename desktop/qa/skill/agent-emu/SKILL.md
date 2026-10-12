---
name: agent-emu
description: Operate agent-emu, the Android phone fleet (d0..dN) on this PC, to test the BoltBetz staging app. Use when testing BoltBetz on a phone or emulator, running test cases, lanes or the matrix, reading the bug feed, starting/stopping/checking phones, reading a phone screen (ui_tree, screenshot, logs, crashes), tapping or typing on a phone, or sending BoltBetz QA deep links (route jumps, faults, machine QR).
---

# agent-emu

Full guide: `AGENTS.md` in this skill's folder. Read the section a step names when you reach it.
Tools: the `agent-emu` MCP server (`claude mcp get agent-emu`). Every reply has `ok`; read it.

## Core loop

1. **Up?** `status` shows your phone in phase `ready` (not just `ready: true`). Not up: "Phone lifecycle".
2. **Where am I?** `ui_tree`. Match `resource-id` (the app's testID) first, then `text`.
3. **Act.** `tap` a node's bounds center with `"device_px": true`; inputs with `"screenshot": false`. Jump with
   `deep_link boltbetz-staging://e2e-session/route/...`, fail an endpoint with `.../fault?endpoint=...&preset=500`
   ("QA hooks").
4. **Verify the outcome.** Expected testID in `ui_tree`, screenshot looked at, `crash_events` after the starting
   seq empty, `logs` (level E, cursor from before) free of fatal JS errors ("Core loop").
5. **Tests.** `run_case` / `run_lanes` / `run_matrix`, then `bugs` for the bug feed ("Running tests").

## Hard lines

Leave other agents' phones and runs alone. One run per phone. Never press Back on the Start screen.
Production stays read-only. Never switch the staging Synkros mode. Secrets never go into files or chat.
