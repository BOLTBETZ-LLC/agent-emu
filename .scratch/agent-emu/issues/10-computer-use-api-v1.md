# Computer-use API v1

Type: grilling
Status: resolved
Blocked by: 06

## Question

What is the exact v1 surface of computer-use control? Cover:

- The calls: screenshot, tap, swipe, type, key, wait-until-idle.
- The frame format.
- How events are pushed instead of polled.
- Error semantics.
- How the MCP layer exposes each call to agents.

## Answer

Grilled with Aaron on 2026-10-07. Each line below is his pick. The speed and size figures come from Computer-use fast path.

- **Action reply:** every input call returns a settled screenshot. That is one round trip per step, with an estimated ~37-41 ms at 60 Hz. A per-call opt-out returns just the input ack.
- **Settled** means no new frame was posted for N ms (from the host) AND the app is idle (main thread and JS queue idle, no animations running). A small in-guest helper reports app idle.
- **Deadline:** the default maximum wait is 3 s and can be overridden per call. At the deadline, the call returns the latest frame with `settled=false` and the reason (`frames_changing` or `app_busy`). It does not error.
- **Frame:** 1080x2400 JPEG q75 by default (about 7 ms encode and about 122 KB, measured on this PC). A `size` parameter can ask for smaller frames, such as 706x1568. A `zoom(rect)` call returns part of the screen. Every frame carries a generation number, capture time and scale factor. Frames come from gfxstream `getScreenshot`, never from the lagged readback worker.
- **Inputs:** `tap`, `long_press`, `swipe`, `type_text`, `key` (back, home, enter and so on), and `gesture` for multi-finger moves such as pinch. Gestures are generated on the host as timed event streams over virtio-input with MT protocol B.
- **Coordinates:** x,y are in pixels of the frame the agent received. The server maps them to the device using the frame's scale factor.
- **UI tree:** a separate `ui_tree` call, only on request. It is not in action replies.
- **Animations:** window, transition and animator scales are 0 by default. A Device setting turns them back on.
- **MCP:** one tool per call: `screenshot`, `tap`, `long_press`, `swipe`, `type_text`, `key`, `gesture`, `ui_tree`, `zoom`. Each takes a device id. The binary API underneath has the same calls.
- **Ownership:** one agent leases a Device at a time. Any other agent's input gets a `busy` error. Watchers (the viewer, other agents) can still take screenshots.
- **Acceptance timings,** each reported as p50/p95/p99 at 1 and at 10 Devices: screenshot request to image; input request to injection ack; input request to a settled frame.
