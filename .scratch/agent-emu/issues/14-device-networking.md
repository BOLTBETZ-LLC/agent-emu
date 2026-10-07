# Device networking

Type: grilling
Status: resolved
Blocked by:

## Question

How does each Device reach the network? Cover:

- Internet access (for the staging backend) versus isolation.
- Whether Devices can see each other or the host's localhost (Metro is out of scope, but local mock servers matter).
- Per-Device network shaping or offline mode for tests.
- The crosvm networking backend on Windows (slirp).

## Answer

Grilled with Aaron on 2026-10-07.

- **Default: internet on, isolated.** Each Device gets its own NAT'd internet through crosvm's user-mode network (slirp on Windows), so it can reach the staging backend.
- **Isolation:** Devices can't see each other.
- **Host localhost:** reachable through a fixed alias (`10.0.2.2`, as on the stock emulator) for local mock servers.
- **Test switches** per Device and per call: offline, plus slow or lossy network shaping.
- **Traffic capture:** off unless an agent turns it on for a Device. When on, requests and responses are recorded on the host, HTTPS through a test CA the guest trusts, and the agent can mock or fail calls. Plain NAT otherwise.
- Not checked: slirp throughput and latency on Windows crosvm, and whether shaping exists in crosvm's net device. Those belong to the spike.
