---
version: 1
slug: "daemon-agent-emud-src-ui-html"
primary_target: "daemon/agent-emud/src/ui.html"
related_targets: []
---

# Control panel (agent-emud ui.html)

Mode: Operate. Audience: Aaron, desktop browser at 2560x1305 and 1920x1080, watching and steering up to 8 Devices.
Task: start/stop/configure Devices, see all 8 screens live, tap/swipe/type on any, install APKs, open BoltBetz, read logs and issues.
Unresolved: daemon API v2 (daemon/API.md) not written yet; panel runs on /api + /mux until it lands.
Direction chosen unattended (no question tool in this run): assigned roll, lab assay plate.

## Direction contract

THESIS: The Fleet is an assay plate: eight fixed wells, two rows of four, each well a phone at true iPhone 17 Pro Max aspect. Refuses the category default of an instance table beside one viewer.

OWN-WORLD: graphite bench ground on a strict numbered neutral ramp (raise from the exposure-record challenger: tones only from the ramp). Wells are phone-shaped recesses with a reagent rim: clear dashed = empty, amber = booting, indicator teal = running, red = failed. Labels set like lab tape in Bahnschrift condensed caps with tabular figures.

STORY: Aaron sees every Device and the host RAM budget at once, starts wells with one click, taps any screen to work in it, and reads why a Device failed in plain words.

FIRST VIEWPORT: 48 px top bar (name, host RAM budget bar segmented per Device with the 4000 MB floor marked, Start, Stop all, panel toggle). Left: the plate, 4x2 wells filling the height. Middle: focus stage with the selected Device at full height. Right: collapsible side panel (Start, Apps, Logs, Crashes sections).

FORM: assay plate, candidate 5 of 7 (slot-bank candles, phone demo table, contact sheet, split-flap board, assay plate, key rack, elevator indicator). Seed a272989c. Signature move: boot is a reagent fill rising up the well rim with boot progress, flashing teal when the Device is ready. Raise from the j-card challenger: the host RAM bar is a hard budget that must not overflow the floor. Raise from the streaming-wall challenger: the focused well lifts, others hold.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
