# agent-emud HTTP API (dashboard)

The daemon serves two ports on 127.0.0.1:

- **7400**: the agent API, newline-delimited JSON over TCP (`{"id":1,"call":"tap",...}`). The MCP server forwards to it.
- **7401** (`AE_UI_ADDR`): HTTP for the control panel and dashboards. This file covers this port.

Every call the agent API has is also available through `POST /api` on 7401. Localhost only, with no auth.
Pages served from another localhost port (a dev server on `http://localhost:5173`, for example) get CORS
headers. Other origins get none.

Device ids are `d0` .. `dN`. Each id has its own pipes and adb port (6520+N). Eight or more Devices can run
at once. Each boot is refused while host Available memory, minus the guest RAM of Devices still booting,
is under 4000 MB (`AE_MIN_AVAIL_MB`).

## Endpoints

| Method | Path | What |
| --- | --- | --- |
| GET | `/` | the panel page (`AE_UI_FILE=<path>` serves it from disk on each load) |
| GET | `/health` | `ok` |
| POST | `/api` | JSON call, see below. `"async": true` makes it return `202 {"ok":true,"job":"j7"}` at once |
| GET | `/events` | Server-Sent Events: device phases, jobs, metrics once a second, crashes, errors, bugs |
| GET | `/frames?device=d0` | frame stream of one Device |
| GET | `/mux?devices=d0,d1,d2&full=d1` | frame streams of several Devices on one connection |
| GET | `/screenshot.png?device=d0` | one full-size PNG |
| POST | `/upload?device=d0` | body = APK, installed with adb |
| GET | `/runs` | test runner results, newest 30: `{runs: [{stamp, total, passed, failed, wall_s}]}` (`AE_RUNS_DIR`, else `tests/boltbetz/results` above the exe, plus the old `.scratch/test-matrix/runner/results`) |
| GET | `/runs/<stamp>/<file>` | one file of a run: `results.json`, `grid.md` or a `.png`. Read-only, plain names, that folder only |
| POST | `/bugs` | body = one bug occurrence (the `bug` call's arguments, no `call`); reply `{ok, key, new, count}` |
| GET | `/bugs?status=open&area=home&full=1` | the bugs, as the `bugs` call (query values are its arguments) |

A browser opens at most 6 HTTP/1.1 connections per host. An 8-tile dashboard should use one `/mux` and one
`/events`. Separate `/frames` streams per tile would run out of connections.

## Calls (`POST /api`)

The body is `{"call": "<name>", "device": "d0", ...}`. The reply is `{"ok": true, ...}` or
`{"ok": false, "error": "..."}` (HTTP 200 either way), plus `ms`.

| Call | Arguments | Notes |
| --- | --- | --- |
| `start` | `force` (boot past the phone limit: at most `AE_MAX_PHONES`, default 4, phones run; also `start_many`, `fleet`), `image` (`phone`, `phone-n`, `slim5`, `slim4`, `slim3n`, ...), `screen`, `render` (`gfxstream` default, `software`), `refresh_hz` (default 120, 1-240), `boot_cap_mb` (default 600, 0 = none), `idle_cap_mb` (default 250, 0 = keep the boot cap), `mem`, `cpus`, `net`, `auto_squeeze`, `keep_data` | boots and waits for Android plus setup. Use `async`. `keep_data: true` reuses the Device's disks from its last run (installed apps, sign-ins, files); refused if there is none or it was made for another Device, image or super.img. Without it the disks are wiped. `stop` runs `sync` in the guest first |
| `start_many` | `devices: ["d0","d1",...]`, `parallel` (default 4, max 8), plus the `start` arguments | boots them, up to `parallel` at once. Reply `devices: [{device, ok, ready_s / error}]` |
| `stop` | `device` | |
| `stop_many` | `devices` (default: all) | stops them all at once |
| `status` | | every Device: `id, ready, phase, streams, image, mem, cpus, net, screen{name,width,height,dpi}, uptime_s, frames_via, input_via, claim, health` (last check, see `health`), plus `available_mb` and `claims` (every claim, running or not) |
| `claim` | `device`, `owner`, `note`, `force` | marks the phone as `owner`'s (in memory; a daemon restart clears it). Refused while someone else holds it, unless `force`. Any later non-read call on it from another `owner` (or none) still runs and gets a `warning` field |
| `unclaim` | `device`, `owner`, `force` | drops the claim; another owner's only with `force` |
| `health` | `device` | fresh check, reply `health`: `level` (`ok` / `warn` / `bad`), `summary`, `problems`, `warnings`, `adb {ok,port,error}` (host `adb -s 127.0.0.1:<6520+N> shell echo ok`), `network {ip_10_0_2_15, dns_ok, dns, host}` (`AE_HEALTH_HOST`, default `staging.bbapp01.com`), `foreground`, `signed_in` (home vs start/login screen; looked at only while the app is in front), `last_crash`, `stuck` (not ready after `AE_BOOT_STUCK_S`, default 600), and `build` (below). The daemon also checks each idle or booting phone every 60 s (`health` event); phones in use keep their last result (`checked_ms`) |
| `snapshot` | `device`, `name` (letters, digits, `-_.`) | saves the phone's writable disks (userdata, metadata, misc, frp) to `<work>/snapshots/<name>/`: stop, copy only the data in use (sparse), start it again with `keep_data` (crosvm locks the disks while it runs, so the phone reboots, ~2 min). Stamped with the disks' `data.id` (Device, image, system image) |
| `restore` | `device`, `name` | puts it back: stop (if up), copy the disks in (seconds), start with `keep_data` and the snapshot's image and screen. Refused for another Device's snapshot or when the image or system image changed since. Reply `disks_s`, `ready_s` |
| `app` | exactly one of `install` (host APK path), `uninstall`, `clear`, `launch` (package) | `launch` runs auto-squeeze when the Device was started with `auto_squeeze` |
| `install_bundled` | | installs the image's own APK (BoltBetz staging) over the console, no adb needed |
| `tap` | `x, y`, `device_px` | `device_px: true` = device pixels (what a dashboard maps clicks to) |
| `swipe` | `x1, y1, x2, y2, ms`, `device_px` | |
| `key` | `name` (`back`, `home`, `enter`, ...) | |
| `type_text` | `text` | |
| | input options: `screenshot: false` (reply right after the input), `settle_ms`, `deadline_ms`, `size: "WxH"` | input replies carry `first_frame_ms` (input to first new frame) |
| `screenshot` | `size: "WxH"` | base64 JPEG in `frame.jpeg` |
| `logs` | `filter` (package or tag), `level` (`V D I W E F`: that level and up), `max_lines` (500), `cursor`, `wait_ms` | one read, or a long poll with `wait_ms` |
| `issues` | `filter` (package), `top` (40) | E and F lines since boot, grouped by (app or system, level, tag, message with numbers as `#`), most frequent first: `[{side, count, level, tag, message, last}]` |
| `crash_events` | `after` (seq), `wait_ms`, `filter` | crashes, ANRs and native crashes |
| `memory` | | the Device's crosvm processes: `ws_mb, own_mb, shared_mb`, per-process caps |
| `squeeze` | `balloon_mb, cap_main_mb, cap_helper_mb` | balloon plus working-set caps |
| `deep_link`, `set_location`, `clock`, `permission`, `shell`, `ui_tree`, `lease`, `release`, `fleet`, `fleet_stop` | | as on the agent API |
| `bug` | `case` (required), `area`, `step`, `endpoint`, `check`, plus any evidence fields (below) | records one test FAIL. Also `POST /bugs`. Reply `key`, `new` (first time this key was seen), `count` |
| `bugs` | `key`, `status`, `kind`, `area`, `case` (id prefix of any of its cases), `q` (text in title or signature), `since` (ms, last seen), `limit` (200), `full` | bugs, newest `last_seen` first: `{key, sig, title, kind, status, area, step, endpoint, check, first_seen, last_seen, count, cases, phones, runs}` plus `last` (newest occurrence), or `occurrences` (newest 30) with `full: true`. Reply also `total`, `open`, `matched`, `file`. Also `GET /bugs` |
| `quit` | | stops every Device and exits the daemon |
| `quit` + `keep_devices: true` | | exits, phones keep running; the next daemon on the same agent address adopts them. Refused while a Device is still booting |
| `restart` | `exe` (optional: the build to run next, default this exe) | saves every Device, starts the next daemon with the same arguments, exits. The next one waits for this one to exit, binds the ports and adopts the phones: same boot, `phase` `ready`, fast input and frames, logs and `crash_events` cursors still valid. Refused while a Device is still booting |

**Phones outlive the daemon.** Each Device's crosvm runs detached (own process group, out of the daemon's job),
and the daemon parks a copy of its console, touch and keyboard pipe handles in the Device's boot powershell, so
crosvm's pipes stay connected while no daemon runs. `fleet\dNdopt.json` records the boot process (pid + start
time), those handles, the agent address and the `start` options. At startup a daemon adopts every phone recorded
for its own agent address (a crashed daemon's too); a record whose boot process is gone or whose pid was reused is
deleted, one owned by another live daemon or address is left alone. `stop`, plain `quit` and failed boots delete it.

`render`: `gfxstream` (default) draws on the host GPU: crosvm from `crosvm-gpu` (`AE_CROSVM_GPU`) with the Android
SDK emulator's `lib64\libgfxstream_backend.dll` (`AE_SDK_EMULATOR`, default `%LOCALAPPDATA%\Android\Sdk\emulator`),
on a PATH of only the SDK's `lib64\gles_angle`, `lib64` and Windows dirs, and a guest bootconfig set to ANGLE plus
Vulkan over virtio-gpu-asg. If either file is missing, `start` fails and names it. `software` is crosvm's 2D
renderer on the CPU. `status` shows which one a Device runs as `render`.

`refresh_hz` is the guest display's refresh rate (crosvm `refresh-rate`). On gfxstream at 120 Hz the GPU
worker measured 117.6 fps at native size.

`boot_cap_mb` caps the main crosvm process's working set from launch (helpers at 32 MB), checked every 2 s
until the Device is ready. Without it a gfxstream boot took about 2.1 GB of host RAM; with 600 MB one
peaked at 746 MB and boot time stayed at 123 s. The caps stay after boot; `squeeze` (or `auto_squeeze`
after `app launch`) lowers them to the settled size (about 360 MB).

`idle_cap_mb` replaces the main boot cap once the Device is ready: it applies after a minute with no input
(taps, swipes, keys, text, `app`, `deep_link` and the other control calls, focused streams). Any input lifts every
cap at once and the idle cap comes back after the next quiet minute. Exp G (2026-10-09, one phone): idle 250 MB took the Device from about 794 to 430 MB on the host; the first tap after idle took 156-191 ms (133 ms at 600), 0 app kills.

To change a running Device's settings (image, screen, RAM), stop it and start it again with the new options.

`health.build` (app build and OTA): `version_name`, `version_code`, `apk_bytes`, `apk_sha256` (guest sha256sum of the
installed APK), `bundled` (same size as the image's bundled APK) with `bundled_apk` and `eas_build` (from the
`apk/<name>.img` it links), `runtime` (manifest meta-data `expo.modules.updates.EXPO_RUNTIME_VERSION`; this build's is a
resource reference to `file:fingerprint`, so the APK's `assets/fingerprint`), `channel` (the `expo-channel-name`
request header), `update_url`, `embedded_update` (the APK's `assets/app.manifest` id), and the running update from the
app's `databases/updates.db` (latest `last_accessed`): `update_id`, `update_runtime`, `update_commit_ms`,
`update_embedded`. `expected_runtime` = `AE_EXPECTED_RUNTIME`, else the first word of `<work>/expected-runtime.txt`
(the staging channel's current runtime; set it when staging moves). When set and different, `runtime_match: false`
and a warning: the phone never takes the staging OTA.

### Bug feed (`bug`, `bugs`)

The test runner (`tests/boltbetz/run.py`, also `plan.py`) sends every final FAIL as an occurrence. The daemon folds
occurrences into bugs by **dedupe key** = 12 hex of FNV-1a 64 over `ui|area|step|endpoint|check`: `step` and
`endpoint` lowercased with only letters and digits kept (`home_load` = `homeload`), `endpoint` `none` when empty,
`check` = the first failed check as `kind:target` (`ui:<id>:present|absent`, `ui_text:<id>`, `judge:<goal>`,
`value:<source>`) or `error:<error, digits as #>`. The variant (fault preset, Synkros code, lifecycle, amount, lane,
phone, attempt, build) is not in the key, so the same bug on another phone, retry or build only bumps `count`. On the
real run 20261009-132949: 99 FAILs -> 33 bugs.

Every occurrence is appended to `AE_BUGS_FILE` (default `%LOCALAPPDATA%\agent-emu\bugs.jsonl`), one JSON per line
with its `key`, `sig` and `ts`; the daemon rebuilds the bug list from it on start and keeps the newest 30 occurrences
per bug in memory. Each one is also a `bug` event on `/events`.

Occurrence fields the runner sends: `case`, `area`, `step`, `endpoint`, `check`, `title`, `kind` (`unsorted`, or
`runner` for reset/setup/retired-phone errors), `run` (results folder stamp), `attempt`, `phone`, `lane`, `variant`
(the matrix row), `failed_step {n, step}` (the step that threw), `failed_check {check, ok, detail}`, `error`, `shots`
(PNG paths in the run folder, served by `/runs/<run>/<file>`), `logs` (app E lines), `decisions` (Jev), `flaky_first`,
`build` (`version_name, version_code, eas_build, runtime, update_id, runtime_match, health` from `health`).
Not yet: `PATCH` of kind/status/jira (every bug is `open`), ui_tree, network.

### Screens (`start.screen`)

The iPhone 17 Pro Max is 6.9", "2868-by-1320-pixel resolution at 460 ppi" (support.apple.com/en-us/125091),
so 440 x 956 points. Every iPhone profile lays Android out at the same 440 dp width.

| `screen` | Pixels | dpi | Default for |
| --- | --- | --- | --- |
| `iphone17promax-native` | 1320 x 2868 | 480 | `phone`, `phone-n` (`screen` omitted) |
| `iphone17promax-3q` | 984 x 2140 | 358 | |
| `iphone17promax-half` (lean) | 656 x 1424 | 238 | lean images (`screen` omitted) |
| `legacy` (or `small`) | 720 x 1080 | 320 | |

The lean width is 656, not 660, because crosvm rounds the display width down to a multiple of 8.

## Async jobs

Add `"async": true` to any call except `quit`:

```
POST /api {"call":"start","device":"d0","image":"slim5","auto_squeeze":true,"async":true}
-> 202 {"ok":true,"job":"j1"}
```

The result arrives on `/events` as a `job` event (`running`, then `done` or `failed`). Without `async`, the
same call blocks and returns the result.

## Events (`GET /events`)

The stream is `text/event-stream`. Each message is one `data:` line holding a JSON object with a `type`
and `ts` (epoch ms). A `: ping` comment is sent every 15 s when nothing else happens.

```js
const es = new EventSource("/events");
es.onmessage = (m) => { const e = JSON.parse(m.data); /* switch (e.type) */ };
```

| `type` | Fields |
| --- | --- |
| `snapshot` | first message: the `status` reply (`devices`, `available_mb`) |
| `device` | `device, phase, t_s` (seconds since start). Phases: `spawning` (with `info`: image, mem, cpus, screen) -> `booting` (crosvm and kernel) -> `android` (the guest shell answers) -> `setup` (boot completed, settings applied) -> `ready`; `stopped` after a stop or a failed boot |
| `job` | `job, call, device, state` (`running`, `done`, `failed`), `result` or `error`, `ms` |
| `metrics` | once a second: `available_mb`, `devices: [{id, phase, ready, uptime_s, activity, scanout_fps, sent_fps, streams, input_p50_ms, input_n, ws_mb, own_mb, shared_mb}]` |
| `crash` | `device, event` (as in `crash_events`: `kind` crash, anr or native, `process`, `seq`, ...) |
| `error` | `device, call, error` (a failed start, for example) |
| `health` | `device, health` (as the `health` call) |
| `claim` | `device, claim` (null after `unclaim`) |
| `bug` | `key, new, count, bug` (the bug without occurrences), `occurrence` (the one just recorded) |
| `lagged` | `missed`: the client fell behind and that many events were dropped |

Metrics fields:

- `activity`: `idle` when the guest posted no frame in the last second (nothing changed on screen), else `rendering`. Show "idle", not "0 fps", for a still screen.
- `scanout_fps`: frames the guest posted in the last second (the rendering rate while `activity` is `rendering`).
- `sent_fps`: frames sent to all stream clients of that Device in the last second.
- `streams`: open `/frames` and `/mux` feeds.
- `input_p50_ms`: median time from input to the first new frame, over the last 50 taps and swipes (`input_n`).
- `own_mb`: RAM only this Device uses (guest RAM, heaps). `shared_mb`: file-backed pages other Devices reuse (system image, DLLs). `ws_mb` = own + shared. These refresh every 5 s and are null until the first refresh.

Example:

```
data: {"type":"device","device":"d0","phase":"android","t_s":14.2,"ts":1791489708000}
data: {"type":"metrics","available_mb":8906,"devices":[{"id":"d0","phase":"ready","ready":true,"uptime_s":61,"activity":"rendering","scanout_fps":58.9,"sent_fps":30.0,"streams":1,"input_p50_ms":21,"input_n":12,"ws_mb":624,"own_mb":373,"shared_mb":251}],"ts":1791489760000}
data: {"type":"job","job":"j1","call":"start","device":"d0","state":"done","result":{"ok":true,"device":"d0","ready_s":48.1},"ms":48120,"ts":1791489742000}
```

## Frames

Query parameters for `/frames` and `/mux`:

| Param | Default | What |
| --- | --- | --- |
| `max_width` | | fit the width to this many px (aspect kept) |
| `scale` | | 0-1 of the device size (ignored when `max_width` is set) |
| | half size for screens wider than 1000 px, else full | when neither is given |
| `fps` | unlimited | most frames per second sent (0-120). The stream sends on each new scanout frame and once every 2 s when the screen is still |
| `quality` | 75 | JPEG quality 1-100 |
| `format` | `raw` | `/frames` only: `raw` records, or `mjpeg` (`multipart/x-mixed-replace`, works as `<img src>`) |
| `full` | | `/mux` only: this Device goes out unscaled (the focused phone) |

Each phone encodes on its own blocking thread. On `/mux`, when the connection's queue is full, that phone's
frame is dropped (its next frame replaces it), so one slow phone or reader never holds back the others.

Record layout (little endian), repeated:

- `/frames`: `[u32 jpeg_len][u64 seq][u32 device_w][u32 device_h][jpeg]`
- `/mux`: `[u32 jpeg_len][u64 seq][u32 device_w][u32 device_h][u32 device_n][jpeg]` (`device_n` 3 = `d3`)

`device_w`/`device_h` are the Device's pixels, not the JPEG's. Map a click on the drawn image to device pixels
and send `tap` with `device_px: true`.

Tiles and one focused phone on one connection:

```
GET /mux?devices=d0,d1,d2,d3,d4,d5,d6,d7&max_width=220&fps=15&quality=60&full=d2
```

When the focused phone changes, open a new `/mux` with the new `full` and close the old one. A `/mux`
waits for Devices that are not up yet and picks them up when they become ready.

`/frames` ends when its Device stops. With `format=mjpeg`, `<img src="/frames?device=d0&max_width=220&fps=10&format=mjpeg">`
needs no script. It does use one connection per tile.

## Examples

```sh
# boot four lean iPhones, at most four at once, without waiting
curl -s localhost:7401/api -d '{"call":"start_many","devices":["d0","d1","d2","d3"],"image":"slim5","auto_squeeze":true,"async":true}'
# watch
curl -sN localhost:7401/events
# install and open BoltBetz on d0
curl -s localhost:7401/api -d '{"call":"install_bundled","device":"d0","async":true}'
curl -s localhost:7401/api -d '{"call":"app","device":"d0","launch":"com.boltbetz.staging","async":true}'
# tap at device pixels without waiting for a frame
curl -s localhost:7401/api -d '{"call":"tap","device":"d0","x":328,"y":700,"device_px":true,"screenshot":false,"async":true}'
# app warnings and errors, then grouped issues
curl -s localhost:7401/api -d '{"call":"logs","device":"d0","filter":"com.boltbetz.staging","level":"W","max_lines":500}'
curl -s localhost:7401/api -d '{"call":"issues","device":"d0","filter":"com.boltbetz.staging"}'
# stop everything
curl -s localhost:7401/api -d '{"call":"stop_many"}'
```
