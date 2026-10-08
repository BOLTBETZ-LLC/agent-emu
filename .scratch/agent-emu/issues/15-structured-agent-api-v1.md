# Structured agent API v1

Type: grilling
Status: resolved
Blocked by:

## Question

Which structured layers ship in v1 beyond computer use, and in what order? Candidates:

- UI tree
- app JS/Hermes state through CDP
- network capture and mocking
- app storage, keychain and files
- logs
- clock control
- push injection
- deep links and intents
- GPS, sensors and camera image injection

## Answer

Grilled with Aaron on 2026-10-07. Computer use (Computer-use API v1) and the UI tree ship first. All four structured layers below are in v1, built in this order:

1. **Logs and crash events:** logcat streamed and filtered to the app, plus pushed crash and ANR events.
2. **Device controls:**
   - deep links and intents;
   - GPS;
   - camera image injection (QR codes for the machine flow);
   - clock freeze and advance;
   - permission grants;
   - app install and clear-data.
3. **App JS state through CDP:** the Hermes Chrome DevTools Protocol for console, JS errors, evaluate, and reading the Redux store. It works without Metro through the in-app inspector (to be checked in the spike).
4. **Network capture and mocking:** the off-by-default capture from Device networking, exposed as calls (list requests, mock, fail). It needs the test CA plus a host proxy, so it comes last.

- Each layer is a set of calls on the binary API with matching MCP tools that take a device id, like the computer-use calls.
- Not decided: exact call names and schemas per layer. That's builder-level detail; the spec gives the list above.

## Measured: layer 2, device controls (2026-10-08)

Daemon calls plus MCP tools, each taking `device`: `deep_link`, `set_location`, `inject_camera_image`, `clock`, `permission`, `app` (`daemon/agent-emud/src/controls.rs`). Each runs as root through the guest shell: adb (`su 0`) when `AGENT_EMU_ADB_PORT` is set, else the console. Host files (APK install) need adb. Driver: `daemon/controls_smoke.py` (Device d5, `--mem 2048`, daemon 7405, adb 6525), every call through the MCP stdio server, against `com.boltbetz.staging`:

| Call | Result | Evidence |
|---|---|---|
| `app install` / `clear` | pass | `Success` |
| `permission` grant / revoke CAMERA | pass | `dumpsys package`: `granted=true`, then `granted=false` |
| `app launch` | pass | `am start -W` `Status: ok`; `topResumedActivity` is `com.boltbetz.MainActivity` |
| `deep_link boltbetz-staging://verified` | pass | `Status: ok`, `Activity: com.boltbetz.staging/com.boltbetz.MainActivity` |
| `set_location 36.1147,-115.1728` | pass | `dumpsys location`: `Location[gps 36.114700,-115.172800 hAcc=5.0 ... mock]` |
| `clock advance_ms 3600000` | pass | guest `date +%s%3N` moved 3600104 ms |
| `clock set` | pass | 127 ms from target |
| `clock freeze` | gap | turns off automatic time detection only; the guest clock still ticks (2310 ms in ~2 s). A real stop needs a hypervisor clock hook. |
| `inject_camera_image` | gap | returns an error, injects nothing. See below. |

- **Camera gap:** the guest camera is `com.google.emulated.camera.provider.hal` (`android.hardware.camera.provider@2.7-service-google`, `libgooglecamerahwl_impl.so`). Its frames come from `EmulatedScene`, a procedural scene; the only knobs are the JSON characteristics in `etc/config/emu_camera_*.json` and the sensor test pattern. No file, property or pipe feeds it an image. QR injection needs a guest change: a camera provider that reads frames from a file or a virtio pipe. It is not a crosvm change.
- Location: `cmd location set-location-enabled true` plus a `gps` test provider works as root on API 36 with no appops step.
- Clock: `cmd time_detector set_auto_detection_enabled false` then `cmd alarm set-time <ms>`. The target ms is computed on the host: the guest `sh` does 32-bit arithmetic (`$((1791433813 * 1000))` gave 432450568 and set the clock to 1970).
- Trap: a fresh Device has no proof app (userdata is recreated per boot); `app install` puts it back.
