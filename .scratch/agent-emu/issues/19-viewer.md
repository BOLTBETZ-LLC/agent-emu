# Viewer

Type: grilling
Status: resolved
Blocked by: 10

## Question

How does a human watch or click a headless Device?

## Answer

Recommended default, recorded under Aaron's standing order of 2026-10-07.

- `agent-emud` serves a local web page at `http://127.0.0.1:<port>/`. It shows a grid of every Device, live through MJPEG from the same `getScreenshot` path, plus clicks and keys. Nothing to install.
- The viewer is a watcher, not a lease holder. It acts only when a human takes the lease.
- Closing the page costs nothing: frames are only produced while someone watches.
