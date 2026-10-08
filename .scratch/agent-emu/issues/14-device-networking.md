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

## Measured: slirp + adb (2026-10-08)

- **Root cause of the slirp crash** ("Failed to start queue 0: worker not found when stopping queue", then "Backend device disconnected early"): `devices/device_virtio_net/src/vhost_user/sys/windows.rs` `start_queue` checked `backend.workers.get(idx).is_some()`. `workers` is `[Option<_>; 3]`, so that is true for every valid idx; it called `stop_queue` on an empty slot, which returns `WorkerNotFound`. linux.rs checks `workers[idx].is_some()`. Fixed in crosvm `agent-emu-pmem` `cdf42c9d9`.
- **adb:** `AGENT_EMU_ADB_PORT=<p>` makes slirp forward `127.0.0.1:<p>` to `10.0.2.15:5555` (`bae510ca1`). agent-emud sets `6520+N` and, in its post-boot setup, brings up `buried_eth0` with `10.0.2.15/24` plus the on-link route in table `legacy_system` (the guest runs no DHCP client on that NIC, and its policy rules end in `unreachable`). adbd already listens on 5555 (`persist.adb.tcp.port=5555`). `boot-stage1.ps1` turns net on only when `AGENT_EMU_ADB_PORT` is set.
- Proof on a fresh boot (`daemon/adb_smoke.py`, output in `assets/14-adb/adb.txt`): `adb connect 127.0.0.1:6520` connected, `getprop sys.boot_completed` printed `1`, `adb install -r` of the proof APK (145 MB) `Success` in 13.3 s.
- Internet from the guest is not set up (no default route, airplane mode stays on). Not needed for adb.
