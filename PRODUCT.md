# Product

<!-- impeccable:product-schema 1 -->

> Written by the UI worker on 2026-10-08 from Aaron's brief and the repo. No interview round ran (subagent without a question tool); lines marked (inferred) are not confirmed.

## Platform

web

## Users
Aaron, a developer running many virtual Android phones (Devices) on one Windows host, watching and steering them while AI agents test the BoltBetz app on them. He opens the control panel in a desktop browser (window 2560x1305, also 1920x1080).

## Product Purpose
agent-emu runs a Fleet of up to 8 lightweight Android Devices on one host at low Unique memory, so agents can drive them by computer-use control and the Agent API. The control panel lets a person start, stop and configure Devices, see every screen live, and interact with any of them directly.

## Positioning
Many phones per host at a few hundred MB unique RAM each, with every screen live in one window and click-to-tap on any of them.

## Operating Context
- The daemon `agent-emud` serves the panel on http://127.0.0.1:7401 (localhost only, no auth); agents use a binary API on 7400.
- A Device boots in about 1-2 minutes; host free RAM must stay above ~4000 MB per boot.
- Images: full phone (~2 GB, home screen) and lean agent phones (no home screen, blank until an app opens).
- Screen profile: iPhone 17 Pro Max shape (1320x2868 aspect, 440 dp layout); legacy small 720x1080.
- Typical use: start 2-8 Devices, open BoltBetz staging, watch them, tap through flows, read logs and issues (app vs system), install APKs.

## Capabilities and Constraints
- Per Device: start (image, screen, RAM, CPUs, network, save RAM), stop, tap/swipe/keys/text, install APK, launch app, logs, grouped issues, crash events, RAM own/shared, screenshot.
- Fleet start/stop. Quit stops the daemon.
- Single embedded HTML page; no build step, no external network dependencies (inferred).

## Brand Commitments
Terminology from GLOSSARY.md: Device (not instance/emulator), Fleet, Host, Unique memory, Lease, Settled. Copy is plain and functional.

## Product Principles
- Every action is acknowledged at once; slow work shows progress inline, never a blocking spinner.
- All Devices visible at once; one click to focus and interact.
- Errors say what happened and what to do next.
- Memory is the scarce resource: show it plainly.

## Accessibility & Inclusion
Keyboard reachable controls, visible focus, reduced motion respected.
