# Computer-use fast path

Type: research
Status: open
Blocked by:

## Question

How do frames get from a guest GPU path (gfxstream or virtio-gpu on Windows) to an agent, and how do inputs get back in, with a round trip under 50 ms? Answer these:

- Readback cost of a 1080x2400 frame from a host GPU texture. Encoding options and their latency and size: raw, PNG, WebP, JPEG, delta frames.
- Input injection via virtio-input: latency, and multitouch support.
- How fast is the stock emulator's gRPC screenshot and input path today (the published or measured baseline)?
- How do existing agent tools (mobile-mcp, Android CLI, agent-device) get frames, and what latencies do they report?
