# agent-emu: operating guide for coding agents

agent-emu runs a **fleet** of light Android phones (**Devices**, ids `d0`..`dN`) on this Windows PC, so agents can
test the BoltBetz app on them. One daemon, `agent-emud`, owns the Devices. You drive it three ways:

| Way | Address | Use it for |
| --- | --- | --- |
| MCP server (`agent-emud mcp`) | stdio, forwards to 7400 | Claude Code / Codex tool calls |
| HTTP | `POST http://127.0.0.1:7401/api` | curl, Python, the test runner |
| Raw TCP | `127.0.0.1:7400`, one JSON per line | the MCP server itself |

Every call has the same name and arguments on all three. The full call reference is `daemon/API.md`; this file is
the how-to. Words: `GLOSSARY.md`. Dashboard: `http://127.0.0.1:7401/`.

**Core loop** for any phone task (Claude Code also has it as skill `.claude/skills/agent-emu`):
health + `status` phase `ready` -> `ui_tree` to see where you are -> act (`tap` with `device_px`, `deep_link` route
or fault) -> verify (testID present, screenshot looked at, no `crash_events`, no fatal `logs`) -> repeatable work
becomes a case run alone with `run.py --case` (or MCP `run_case`).

Contents: [Rules](#rules) · [60-second start](#60-second-start) · [Calls](#calls) · [Phone lifecycle](#phone-lifecycle) ·
[Read the screen](#read-the-screen) · [Act](#act) · [BoltBetz QA hooks](#boltbetz-qa-hooks) · [Verify](#verify) ·
[Test runner](#test-runner) · [Accounts and sign-in](#accounts-and-sign-in) · [Staging and Synkros](#staging-and-synkros) ·
[MCP setup](#mcp-setup) · [Gotchas](#gotchas) · [Troubleshooting](#troubleshooting)

## Rules

These hold for every agent, every task.

- **Leave processes alone.** `agent-emud`, `crosvm`, test daemons on other ports (for example
  `target-expK` on 7440) and other agents' `python run.py` belong to someone. Report a stuck one; Aaron decides.
- **Leave other agents' phones running.** Before `start`/`stop`, read `status` and the newest results folder
  (see [Test runner](#test-runner)). If a phone is in use, run on the phone you were given, or ask.
- **One test run per phone at a time.** A second runner on the same phone corrupts both runs.
- **Never press Back on the Welcome/Start screen** (Aaron's rule). Navigate with `deep_link` routes, tab
  testIDs or the screen's own close button; use `key back` only inside a flow you opened.
- **Secrets stay out of files and chat.** The Synkros emulator key resolves through asm-exec at run time
  (`emu.py` does it). Sign-in codes go straight from the Gmail connector into the phone; never write one down.
- **Production is read-only.** Phones talk to staging. Never point anything at production.
- **Staging Synkros mode belongs to Aaron.** Switching it signs every app out ([details](#staging-and-synkros)).
- `python`, not `python3`. Git Bash rewrites args that start with `/`: prefix `MSYS_NO_PATHCONV=1` when a
  URL path is a bare argument.

## 60-second start

Each step ends on the check that proves it.

1. **Daemon up.** `curl -s -m 2 http://127.0.0.1:7401/health` prints `ok`. If not, run `C:\dev\agent-emu\agent-emu.cmd`
   (starts the daemon hidden, opens the panel; log in `%LOCALAPPDATA%\agent-emu\agent-emud.log`) and check again.
2. **Phones ready.** `status` lists each phone with `"phase":"ready"`.
   ```sh
   curl -s localhost:7401/api -d '{"call":"status"}'
   ```
   Missing phones: start them the BoltBetz way (keeps installed app and sign-in, ~2 min):
   ```sh
   curl -s localhost:7401/api -d '{"call":"start_many","devices":["d0","d1","d2","d3"],"image":"slim5","screen":"iphone17promax-half","keep_data":true}'
   ```
   Poll `status` every 30 s until every phase is `ready`. `ready: true` alone is not enough (see `browser` phase below).
3. **App installed.** Only when `status` shows `"keep_data": false` for the phone (disks wiped at start):
   `{"call":"install_bundled","device":"d3"}` replies `ok:true` with `Success`.
4. **App on screen, signed in.**
   ```sh
   curl -s localhost:7401/api -d '{"call":"app","device":"d3","launch":"com.boltbetz.staging"}'
   curl -s localhost:7401/api -d '{"call":"ui_tree","device":"d3"}' | grep -o 'home-screen'
   ```
   `home-screen` printed = signed in on Home. `start-screen` or `login-screen` = signed out: do
   [Accounts and sign-in](#accounts-and-sign-in).
5. **One case passes.**
   ```sh
   cd C:/dev/agent-emu
   python tests/boltbetz/run.py --phones d3 --case L4-01
   ```
   Prints `PASS L4-01-home-renders on d3 ...` and exits 0. Suite and lanes: [Test runner](#test-runner).

## Calls

One example per call, as the HTTP body (`curl -s localhost:7401/api -d '<body>'`). Through MCP, drop `"call"` and
use the tool of that name with the same arguments. Replies are `{"ok":true,...}` or `{"ok":false,"error":"..."}`,
HTTP 200 either way: always read `ok`.

| Call | Example body | Reply / note |
| --- | --- | --- |
| `status` | `{"call":"status"}` | `devices[{id,ready,phase,image_name,screen{width,height},mem,keep_data,uptime_s}]`, `available_mb` |
| `start` | `{"call":"start","device":"d3","image":"slim5","screen":"iphone17promax-half","keep_data":true}` | blocks until ready (~110 s); add `"async":true` on HTTP to return at once |
| `start_many` | `{"call":"start_many","devices":["d0","d1"],"image":"slim5","screen":"iphone17promax-half","keep_data":true}` | up to 4 boot at once |
| `stop` | `{"call":"stop","device":"d3"}` | syncs the guest first, so `keep_data` can reuse the disks |
| `stop_many` | `{"call":"stop_many","devices":["d0","d1"]}` | no `devices` = every phone |
| `install_bundled` | `{"call":"install_bundled","device":"d3"}` | the image's default app (on this PC the BoltBetz staging APK) |
| `install_app` | `{"call":"install_app","devices":["d3"],"source":"https://expo.dev/artifacts/eas/<x>.apk"}` | any build, see [The app under test](#the-app-under-test) |
| `set_default_app` | `{"call":"set_default_app","source":"C:/builds/app.apk"}` | the app new phones install at boot |
| `app` | `{"call":"app","device":"d3","launch":"com.boltbetz.staging"}` | one of `install` (host APK path), `uninstall`, `clear`, `launch` |
| `ui_tree` | `{"call":"ui_tree","device":"d3"}` | `xml`: uiautomator dump, ~80 ms |
| `screenshot` | `{"call":"screenshot","device":"d3","size":"360x780"}` | `frame.jpeg` base64; MCP returns an image block |
| `tap` | `{"call":"tap","device":"d3","x":328,"y":700,"device_px":true,"screenshot":false}` | see [Act](#act) for coordinates |
| `swipe` | `{"call":"swipe","device":"d3","x1":328,"y1":1150,"x2":328,"y2":350,"ms":300,"device_px":true,"screenshot":false}` | |
| `type_text` | `{"call":"type_text","device":"d3","text":"hello","screenshot":false}` | types into the focused field |
| `key` | `{"call":"key","device":"d3","name":"enter","screenshot":false}` | `back`, `home`, `enter`, `del`, `app_switch` |
| `deep_link` | `{"call":"deep_link","device":"d3","uri":"boltbetz-staging://e2e-session/hooks?on=1","package":"com.boltbetz.staging"}` | QA hooks, below |
| `permission` | `{"call":"permission","device":"d3","pkg":"com.boltbetz.staging","perm":"android.permission.CAMERA","action":"revoke"}` | verified from dumpsys |
| `clock` | `{"call":"clock","device":"d3","advance_ms":3600000}` | or `set` (epoch ms), `freeze` |
| `set_location` | `{"call":"set_location","device":"d3","lat":36.1147,"lon":-115.1728}` | BoltBetz uses no location |
| `shell` | `{"call":"shell","device":"d3","cmd":"am force-stop com.boltbetz.staging"}` | root shell; `out`, `code` |
| `logs` | `{"call":"logs","device":"d3","filter":"com.boltbetz.staging","level":"W","max_lines":200}` | `lines`, `cursor`; pass `cursor` back for newer lines only |
| `issues` | `{"call":"issues","device":"d3","filter":"com.boltbetz.staging"}` | E/F lines grouped and counted |
| `crash_events` | `{"call":"crash_events","device":"d3","after":0,"filter":"com.boltbetz.staging"}` | crashes, ANRs, native crashes; `last` = newest seq. Without `filter`, boot-time system crashes (`dlkm_loader`) show too |
| `memory` | `{"call":"memory","device":"d3"}` | host RAM of the phone's crosvm processes |
| `squeeze` | `{"call":"squeeze","device":"d3"}` | balloon + RAM caps now (the daemon also does this on idle) |
| `lease` / `release` | `{"call":"lease","device":"d3"}` | exclusive input for this connection (MCP holds it until the MCP process exits) |
| `inject_camera_image` | | always errors: the guest camera takes no image. Use the QA paste box |
| `claim` / `unclaim` | `{"call":"claim","device":"d2","owner":"me","note":"lane 3 work"}` | marks the phone yours (dashboard tag, `status`); others' input calls get a `warning`. Pass your `owner` on your own calls |
| `health` | `{"call":"health","device":"d2"}` | adb, network, app version, runtime vs staging, update id, foreground, signed in, last crash, stuck boot; `level` ok/warn/bad. Dashboard: dot by the phone name, hover for detail |
| `snapshot` | `{"call":"snapshot","device":"d1","name":"L2-golden"}` | save a signed-in phone's disks (reboots it, ~2 min) |
| `restore` | `{"call":"restore","device":"d1","name":"L2-golden"}` | put them back: disks in seconds, then a keep_data boot (~2 min); only that Device's snapshot, same image |
| `bugs` | `{"call":"bugs","area":"home","q":"pin"}` | the live bug feed: test FAILs folded by dedupe key, newest first, each with `count`, `cases`, `phones` and its newest occurrence (`last`); `full: true` = every kept occurrence. See [Bug feed](#bug-feed) |

## Phone lifecycle

Phases, in order (`status.devices[].phase`, also `device` events on `/events`):

`spawning` -> `booting` (crosvm + kernel) -> `android` (guest shell answers) -> `setup` (boot done, settings applied)
-> `browser` (Firefox first run, only on wiped disks; can relaunch Firefox over the app for 2-4 min) -> `ready`.
`stopped` after a stop or a failed boot.

**Ready means `phase == "ready"`.** The `ready` boolean turns true before the `browser` phase ends. Calls other
than `status`/`stop` on a phone that is not up fail with `Device dN is still booting`.

- **`keep_data: true`** reuses the phone's disks: installed app, sign-in, Firefox setup. It is refused when the disk
  was made for another Device, image or system image (`keep_data: d3's disk is ..., this start needs ...`). Then
  start with the same `image`/`screen` as the last run (BoltBetz phones: `slim5`, `iphone17promax-half`), or start
  without `keep_data` to wipe it and redo install + sign-in.
- **Without `keep_data`** the disks are wiped: `install_bundled`, then sign in.
- **RAM.** slim5 at half screen boots with 896 MB guest RAM (640 + 256 browser headroom). A boot is refused while
  host available memory (minus phones still booting) is under 4000 MB (`AE_MIN_AVAIL_MB`).
- **Idle caps and balloon.** After 60 s with no input, the daemon caps each phone's host working set (idle cap
  200-250 MB) and pushes cold pages into the guest balloon/zram. Any input (tap, swipe, key, text, `app`,
  `deep_link`) lifts every cap at once; the first tap after idle can take 150-450 ms instead of ~130 ms. Normal,
  not a hang.
- **Settings of a running phone** (image, screen, RAM) change only by `stop` then `start`.
- **Phone limit.** `start` refuses a phone past `AE_MAX_PHONES` (default 4, Aaron's rule) unless `"force": true`.
- **Golden snapshots.** Each lane phone has `L<n>-golden` (signed in, Home): `snapshot` makes one, `restore` puts a
  broken phone (signed out, bad data) back. Expected staging runtime for the health warning: `C:\dev\agent-emu-work\expected-runtime.txt`.

### The app under test

Any Android build plugs in. `install_app` puts it on running phones now; `set_default_app` makes it the app every phone
started later without `keep_data` installs at boot (and what `install_bundled`/`fleet` install). Both take one
`source`, and anything that names a build works:

| Source | Example |
| --- | --- |
| local APK | `C:\builds\app-staging.apk` |
| any https APK URL | `https://expo.dev/artifacts/eas/PrDreXFiALCuSSTALkhmPQvdrc-OwQNJ9stUadTzgN0.apk` |
| expo.dev build page | `https://expo.dev/accounts/boltbetz/projects/<project>/builds/4cd7f3dc-7570-4c66-b85f-349fe5b5e3a9` |
| EAS build id | `4cd7f3dc-7570-4c66-b85f-349fe5b5e3a9` |

- Build pages and ids go through the logged-in `eas` CLI (`eas build:view <id> --json`), which must run inside some Expo
  project folder: pass `eas_project` or set `AE_EAS_PROJECT_DIR` (on this PC `C:\dev\v2-React-Native`). Not logged in = a
  clear error; the artifact URL works without eas.
- The reply names `package`, `version_name`, `version_code`, Expo `runtime` and `channel`, read from the APK itself.
- `.aab` (store builds) is refused: phones need an APK (EAS profile with `android.buildType: "apk"`).
- Installer builds of agent-emu ship base images with no app at all; until `set_default_app` runs, `install_bundled`
  errors and wiped phones boot without an app.

**Bundled APK on this PC** (the default app here; what `install_bundled` and `fleet` install): EAS staging build `4cd7f3dc` (1.4.0, versionCode 22),
kept at `C:\dev\agent-emu-work\apk\boltbetz-staging-4cd7f3dc.apk`. Previous one (2026-10-07) beside it as
`boltbetz-staging-prev-2026-10-07.apk`.
- **Rule: the bundled build's fingerprint must match today's staging OTA fingerprint.** Otherwise the app never
  takes the staging OTA and the phone tests old JS. Check after an install + two launches:
  `/data/data/com.boltbetz.staging/files/.expo-internal` is non-empty (the OTA was downloaded).
- **Swap it:** put the new APK in `agent-emu-work\apk\`, then
  `powershell -NoProfile -ExecutionPolicy Bypass -File daemon\set-bundled-apk.ps1 C:\dev\agent-emu-work\apk\<name>.apk`.
  It writes `apk\<name>.img` (APK padded to 4 KiB) + `.size` and hard-links them as `apk.img`/`apk.size` into every
  `run*` dir. Running phones keep their old APK (their Device dir holds its own links) and get the new one on their
  next start. Then run `daemon\package.ps1` so the portable zip carries it.

## Read the screen

1. **`ui_tree` first.** It is ~80 ms and gives exact ids. Each `<node>` carries `resource-id` (= the React Native
   `testID`), `text`, `content-desc`, `enabled`, `package` and `bounds="[x1,y1][x2,y2]"` in **device pixels**.
   The BoltBetz app sets testIDs on every screen and button: match on `resource-id` first, then visible `text`.
2. **Screenshot to look.** `screenshot` (MCP: image block) or `GET http://127.0.0.1:7401/screenshot.png?device=d3`
   (full-size PNG). Pass `size` to save tokens. Look at it before you say something is visible.
3. **Phone size.** BoltBetz phones are 656 x 1424 (`iphone17promax-half`, 440 dp wide like an iPhone 17 Pro Max).
   Native is 1320 x 2868. Read `status.devices[].screen` rather than assuming.

Screen ids that mark where you are: `start-screen`, `login-screen`, `home-screen`, `wallet-main-screen`,
`settings-screen`, `rewards-screen`, `machine-connected-screen`, `responsible-gaming-screen`,
`notifications-screen`. Tabs: `tab-home`, `tab-wallet`, `tab-boltbetz` (scanner), `tab-rewards`, `tab-settings`.
More ids: `tests/boltbetz/cases/*.json`.

Plaid's ID-check WebView often has no accessibility tree. Drive it by coordinates (`tests/boltbetz/signup.py` has them).

## Act

- **Tap a node:** center of its `bounds`, with `"device_px": true`. Without `device_px`, x/y are pixels of the
  last screenshot you received (scaled if you passed `size`).
- **`"screenshot": false`** on inputs returns right after the input (fast). Leave it on to get a settled frame back
  (`settled`, `first_frame_ms`), which costs an image in MCP.
- **Text:** tap the field first, then `type_text`. Masked fields (Plaid) drop digits typed fast: one character
  per call, ~1.5 s apart. Clear a field: `shell` `input keyevent KEYCODE_MOVE_END 67 67 67 ...`.
- **Scroll:** `swipe` from low to high y (content moves up). Pull to refresh: swipe from y 300 to 950 on a
  656 x 1424 phone.
- **Restart the app:** `shell` `am force-stop com.boltbetz.staging`, then `app` `launch`. `launch` alone only brings
  a running app forward.
- **Machine QR (no camera):** with hook mode on, the scanner (`tab-boltbetz`) mounts an invisible paste box:
  tap `qa-machine-scan-input`, `type_text` the QR token, tap `qa-machine-scan-submit`. The token comes from the
  Synkros emulator: `python emu.py feed EMU-L3` (single use). The runner step `{"emu":"type_qr","asset":"EMU-L3"}` does it.

## BoltBetz QA hooks

The staging app (`com.boltbetz.staging`) obeys deep links on `boltbetz-staging://e2e-session/...`. Send each with
`deep_link` and `"package":"com.boltbetz.staging"`, then wait ~0.8 s: the app acts on them asynchronously.

| Link | Effect |
| --- | --- |
| `hooks?on=1` / `hooks?on=0` | hook mode on / off (stored across restarts; off also clears faults). Any other link turns it on |
| `route/MainFlow/Tabs/Home` | jump to a screen; path = navigator names. Used: `MainFlow/Tabs/{Home,Settings}`, `MainFlow/Tabs/Wallet/{WalletMain,WalletDeposit,WalletWithdrawal}`, `MainFlow/{Notifications,OperatorLimits,ResponsibleGaming}`. `?param=value` passes params |
| `fault?endpoint=getLinkedPlayerAccounts&preset=500` | the next calls to that RTK endpoint fail on the phone (never reach the server). Presets: `500`, `problem`, `validation`, `forbidden`, `timeout`, `offline`, `decline`, `synkros`, `sila`. Or `&status=<code>&body=<json>` |
| `fault?clear=1` (`&endpoint=<name>`) | clear all faults (or one) |

- Route jumps work only signed in; signed out they are dropped silently.
- RTK caches a read for 60 s after its screen was last open: a fault shows only on a cold cache or after a refresh.
- Hook mode on shows QA overlay buttons (`qa-force-ota`, ...) and the `qa-state` readout.
- Faulted endpoints used by cases: `getLinkedPlayerAccounts`, `getResponsibleGamingSettings`, `generatePlaidLinkToken`.
  Source of the link grammar: `v2-React-Native` `src/features/qa/hookLinks.ts`, presets `qaFaults.ts`.

## Verify

Name the outcome, then observe it directly. A reply with `ok:true` proves the call ran, not that the app did the
right thing.

- **Screen state:** the expected testID is in `ui_tree` (and absent ids are absent), plus a screenshot you looked at.
- **No crash:** take `crash_events.last` before the action; after it, `crash_events` with `after` = that seq and
  `filter` = `com.boltbetz.staging` returns no events.
- **No JS errors:** take the `cursor` before with `logs` `{"max_lines":1}` (`max_lines: 0` returns the whole
  logcat, 100k+ lines); after, read `logs` with that `cursor`,
  `filter` `com.boltbetz.staging`, `level` `E`, and search for
  `FATAL EXCEPTION|ReactNativeJS.*(Unhandled|TypeError|Invariant Violation)`.
- **What went wrong overall:** `issues` with the package filter.
- **The dashboard itself** (when you change `ui.html`/`ui.rs`, not when testing the app): open
  `http://127.0.0.1:7401/` in a browser (no Reticle snippet: Aaron removed it 2026-10-09, it asked him to sign up).
  Live streams: `/mux?devices=d0,d1,d2,d3`, events `/events` (`daemon/API.md`).

## Test runner

`tests/boltbetz/run.py` in this repo (stdlib Python; it finds its cases and results beside itself). Cases: one
JSON per case in `tests/boltbetz/cases/` (top level only; `cases/signed-out/` and `cases/no-card/` are parked and run only when passed as
the cases dir).

**Lanes.** Each case has a lane; each lane owns one account, Synkros player and machine, so lanes never share state.
Lanes are dealt to `--phones` in order. The standard deal:

| Lane | Phone | Account | Covers |
| --- | --- | --- | --- |
| L1 identity | d0 | aaron+lane1@boltbetz.com | settings profile, limits, notifications, limits fault |
| L2 money | d1 | aaron+lane2@boltbetz.com | wallet, deposit form, withdraw, bank-link fault (never submits money) |
| L3 machine | d2 | aaron+lane3@boltbetz.com | scanner, QA paste box, invalid QR, real connect to `EMU-L3` |
| L4 browse | d3 | aaron+lane4@boltbetz.com | home, rewards, settings, responsible gaming, home cards fault |

**Before you run:** no other run may be using your phones.
```sh
ls -lt --time-style=+%H:%M C:/dev/agent-emu/tests/boltbetz/results | sed -n 2p   # newest folder + time
ls C:/dev/agent-emu/tests/boltbetz/results/<that folder>                       # results.json there = finished
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ? CommandLine -match 'run.py' | select ProcessId,CommandLine"
```
A newest folder under 10 minutes old without `results.json` (or whose `results.json` says `"running": true`, matrix
mode), or any `run.py` process, means a run is live: wait, or use only phones it does not hold.

**Run** (from `tests/boltbetz/`):
```sh
python run.py --phones d0,d1,d2,d3                 # all lanes in parallel, ~60 s
python run.py --phones d3 --lanes L4               # one lane on one phone
python run.py --phones d3 --case L4-01             # one case (id prefix; comma list allowed)
python run.py --phones d2 --case L3-01,L3-03       # two cases
python run.py --phones d0 cases/signed-out         # another cases dir
```
Before the first case of a lane the runner waits for phase `ready`, grants POST_NOTIFICATIONS, on L3 sets CAMERA to
denied (user-fixed: a live camera gets the app low-memory-killed), launches the app and waits for Home.
Exit code 0 = no FAIL.

**Snapshots and claims in a run.** `--restore {lane}-golden` restores every phone first; `--golden {lane}-golden` restores a
phone that a case left signed out, then the lane goes on (lane mode: after the FAIL; matrix: when the reset finds it
signed out, then the reset runs again). `{phone}` works in names too. The runner claims its phones as `run.py-<pid>`
and drops the claims at the end.

**Lane data.** Cases use `{lane.player}`, `{lane.card}`, `{lane.machine}`, `{lane.email}`, `{lane.id}` instead of a
lane's literal ids; `tests/boltbetz/lanes.json` fills them (in every mode) and names each lane's phone. A new lane = one
entry there (`phone`, `email`, `player`, `card`, `machine`).

**Matrix mode** (a work queue for many cases on many phones):
```sh
python run.py --phones d0,d1,d2,d3 --matrix cases/matrix                    # every case under cases/matrix
python run.py --phones d0,d1,d2,d3 --matrix cases/matrix --areas wallet,rewards
python run.py --phones d0,d1,d2,d3 --matrix cases/matrix --shard 1/2        # every 2nd case from the 1st (split runs)
python run.py --phones d0,d1,d2,d3 --matrix cases --areas L1,L2,L3,L4       # the lane cases as a matrix
```
- Area = the case's subfolder under the matrix dir (files directly in it: their lane). `--case` filters ids here too.
- **Portable vs pinned.** A case with no `lane` (or `"lane": "any"`, or `"portable": true`) runs on any phone, with that
  phone's lane account (lanes.json `phone`). A case with a lane and no `portable` runs only on that lane's phone, in id
  order (L2-10 before L2-12). `"last": true` (L1-99 signs out) runs after everything else on its phone; the phone then
  takes no more work. Matrix cases must use `{lane.*}`, never a literal player, card or machine.
- **Scheduling.** Cases are dealt longest first (wall time from earlier runs in `results/`, else `timeout_s / 4`)
  onto per-phone queues; a phone whose queue empties steals the longest portable case from the busiest phone.
- **Reset before every case** (~3 s): end the lane machine's session, put the lane player's CWA back to $25 if it is
  off, force-stop + launch the app, hooks on, faults cleared, route Home, wait for `home-screen` (signed out = FAIL).
  CAMERA is denied on every phone. Three failed resets in a row retire that phone for the run.
- `timeout_s` is a hard limit: every daemon call fails once it passes (cleanup gets 30 s more).
- **Retry:** a failed portable case runs once more on another phone; PASS then = `flaky: true`. Pinned cases are not
  retried (no other phone has their account).
- **Live:** `results.json` is rewritten after every case (`running`, `progress`, results so far), so the dashboard's
  Test runs view fills in as it goes; stdout prints `[n/N] PASS|FLAKY|FAIL <id> on <phone> ...`.
- **Bug feed:** every final FAIL (lane, matrix and plan mode) goes to the daemon at once, see [Bug feed](#bug-feed).
- **Output:** `grid.md` = area x PASS/FLAKY/FAIL/SKIP, total wall time, per-phone busy time and utilization, flaky
  list, failures with error, FAIL screenshot and app error log lines (also `logs` in results.json).
- Offline self-check of the scheduler (no phones): `python test_matrix.py`.

Env: `AE_API` (daemon HTTP, default `http://127.0.0.1:7401`), `AE_RUNS_DIR` (results root, default
`tests/boltbetz/results`, gitignored), `ASM_EXEC` (asm-exec script, default `C:/dev/dev-harness/tools/asm-exec.ps1`),
`AE_SANDBOX_NOTE` (test-card note for card cases, default `C:/dev/boltbetz-docs/70-ops/ops-sandbox-test-account.md`).
Runs before 2026-10-09 stay in `.scratch/test-matrix/runner/results`.

### Plans

A test plan in, chunks dealt across N phones out: `tests/boltbetz/plan.py` (needs PyYAML, installed).
```sh
python plan.py run plans/example.yaml --phones 2 --dry   # cases, chunks, deal, est. wall time, critical chunk; starts nothing
python plan.py run plans/example.yaml --phones 2         # boots missing phones, runs, stops the phones IT booted
python plan.py run plans/example.yaml --phones 2 --keep  # leaves them running
```
- **Plan YAML** (`plans/<name>.yaml`, paths relative to `tests/boltbetz`): `items`, each one of
  `pict: matrix/models/home.pict` (rows via `matrix/gen.py`), `cases: cases/L4-0*` (glob),
  `goal: "plain words"` (Jev `decide` toward it, optional `until_id`, then a `judge` check; taps only, no typing),
  `flow: <gen area>` + `step`, `states`, `faults` (cross product on the plainest real row of that model; fault `none`,
  `synkros:<code>`, `<appfault>` or `<appfault>@<endpoint>`; values are checked against the model). Any item:
  `priority` (P0 first, default P1), `requires_state`, `area`, `timeout_s`, `limit` (first N cases). A flow state that
  is a model `State` value is seeded by the case itself; any other name becomes `requires_state`.
  Free text or a PR diff is turned into this YAML by an agent first; plan.py never runs free text.
- **Phones:** `--phones N` = the first N lanes.json phones (d0=L1, d1=L2, ...); N above the lane count is refused.
  `--phones d0,d2` picks exact lane phones (when another agent holds one of the first N).
  Missing phones boot 2 at a time (`slim5`, `iphone17promax-half`, `keep_data`). Refused when the boots need more than
  (`available_mb` - 4000) / 750 MB; the message names the max N. Phones already up are used and never stopped.
- **Chunks:** cases grouped by (`requires_state`, pinned lane), each group cut into equal chunks of at most ~8 min
  (estimate = newest wall time in `results/`, else gen's estimate, else `timeout_s / 4`, plus ~4 s reset per case).
  Phones pull chunks P0 first, longest first; a pinned chunk runs only on its lane's phone (no phone = SKIP).
- **State per chunk:** before a chunk with a `requires_state`, the runner calls the emulator
  `POST /admin/api/lanes/{lane}/state` (ticket 06). Until it exists (404/405) it logs `state op unavailable` once,
  runs the cases on the per-case reset and marks each result `state_note: requires_state X not set`. Another error
  FAILs the chunk's cases. Per case: run.py's reset + `run_case` (no retry in plan mode).
- **Results:** same folder and `results.json`/`grid.md` as matrix mode, plus `plan.json` (chunks and deal);
  each result carries `chunk`. Offline check: `python test_plan.py`.

### Bug feed

Each final FAIL becomes a bug **occurrence**: the runner sends it to the daemon (`POST $AE_API/bugs`, on its own
thread, so a slow or absent daemon never holds up or fails a case) and appends it to `results/<stamp>/bugs.jsonl`.
The daemon folds occurrences into **bugs** by dedupe key `area|step|endpoint|first failed check`, appends each to
`%LOCALAPPDATA%\agent-emu\bugs.jsonl` (`AE_BUGS_FILE`), and pushes a `bug` event on `/events`.
- **Key:** step = the matrix id's step (`M-home-005-transactions-...` -> `transactions`), else the case id; endpoint
  = the matrix row's `FaultEndpoint`, or a lane case's `fault?endpoint=` link; check = first failed check as
  `ui:<id>:present|absent`, `judge:<goal>`, `value:...`, or the error with digits folded. Fault preset, Synkros code,
  lifecycle, amount, phone, lane, attempt and build are dropped: the same bug elsewhere only bumps `count`.
- **Evidence per occurrence:** case, run, attempt, phone, lane, variant (matrix row), failing step, failed check with
  detail, error, screenshot paths, app E log lines, Jev decisions, build/runtime/OTA update id from `health` (cached 5 min per phone).
- **Read it:** dashboard **Bugs** tab (fills live, groups occurrences under each bug, shows screenshots and logs;
  runner-side bugs hidden unless **Runner** is ticked), the `bugs` call / MCP tool, or `GET /bugs?full=1`.
- **Backfill a recorded run:** `python run.py --post-bugs results/<stamp>` (matrix rows from the case files; ids whose
  case file is gone use the id's step and endpoint). 20261009-132949: 99 FAILs -> 33 bugs.
- `kind` is `unsorted`, or `runner` for reset/setup/retired-phone errors. Every bug is `open`: no triage edits yet,
  and nothing is filed to Jira (a draft-ticket button comes later; never auto-file).

**Through MCP:** `run_case` `{"device":"d3","case":"L4-01"}` and `run_lanes` `{"phones":["d0","d1","d2","d3"],"lanes":["L4"]}`
(`lanes` optional) run `run.py` from the MCP process and block until done. Reply: the PASS/FAIL grid, then
`{"exit","passed","results","stderr"}` with `results` = the run's folder. The MCP finds `tests/boltbetz/run.py`
above its exe (`AE_TESTS_DIR` overrides; `AE_PYTHON` picks the interpreter, default `python`). Same rules as the CLI:
check for a live run first, one run per phone. `run_matrix` `{"phones":["d0","d1","d2","d3"],"matrix":"cases/matrix",
"areas":["wallet"],"shard":"1/2"}` runs matrix mode (`matrix` default `cases/matrix`; `areas`, `shard` optional).

**Results:** `tests/boltbetz/results/<yyyymmdd-hhmmss>/`: `results.json` (deal, total wall time, every check with detail,
errors), `grid.md` (case x lane PASS/FAIL/SKIP + wall s), one PNG per screenshot check, `<case>-FAIL.png` on failure.
Open the PNGs of anything that failed before you call it an app bug.

**Case format.** Annotated (comments are not valid JSON; drop them in a real file):
```jsonc
{
  "id": "L4-06-wallet-from-home",        // <lane>-<nn>-<slug>; file name = id + ".json"; cases run in id order
  "lane": "L4",
  "timeout_s": 90,                        // whole-case budget; over it = FAIL
  "preconditions": {"signed_in": true, "kyc": true, "venue_card": true},   // labels only, except app_launched
  "steps": [
    {"call": "deep_link", "uri": "boltbetz-staging://e2e-session/hooks?on=1"},             // any daemon call, as-is
    {"call": "deep_link", "uri": "boltbetz-staging://e2e-session/route/MainFlow/Tabs/Home"},
    {"tap_id": "tab-home", "optional": true, "wait_s": 2},   // tap a node by resource-id / text / content-desc
    {"wait_id": "home-screen", "wait_s": 15},                // wait for one id, or a list (any of)
    {"tap_id": "tab-wallet"},
    {"call": "swipe", "x1": 660, "y1": 2300, "x2": 660, "y2": 700, "device_px": true, "screenshot": false},
                                              // raw x/y are written for the 1320 px native width; the runner scales them
    {"sleep_ms": 600},                        // capped at 300 ms unless AE_FULL_SLEEPS=1; prefer wait_id
    {"check": {"ui": "wallet-balance-amount"}},              // mid-case check, same forms as "checks"
    {"check": {"screenshot": "wallet"}},                     // saves <id>-wallet.png
    {"emu": "type_qr", "asset": "EMU-L4"}                     // Synkros helper: type_qr | end_session
  ],
  "checks": [                                 // run after the steps; all must pass
    {"ui": "home-screen"},                    // present (wait_s default 10); add "present": false or "enabled": true
    {"ui_any": ["wallet-balance-amount", "wallet-balance-unknown"]},
    {"foreground": "com.boltbetz.staging"},
    {"no_crash": "com.boltbetz.staging"},     // no crash_events since the case started
    {"logs_lack": "FATAL EXCEPTION|ReactNativeJS.*(Unhandled|TypeError|Invariant Violation)", "filter": "com.boltbetz.staging"}
  ],
  "cleanup": [                                // always runs, pass or fail: leave the next case a clean Home
    {"call": "deep_link", "uri": "boltbetz-staging://e2e-session/fault?clear=1"},
    {"call": "deep_link", "uri": "boltbetz-staging://e2e-session/route/MainFlow/Tabs/Home"}
  ],
  "note": "What the case proves, and why any odd step is there."
}
```

**Decide and judge steps (Jev, optional).** A case can name a goal instead of a testID, and check a plain-English
claim instead of an id. Both send the compact `ui_tree` (testIDs, labels, text) to Jev, TypeSafe's hosted System One
typed-question API (`tests/boltbetz/decide.py`):
```jsonc
{"decide": "Open my responsible gaming limits", "until_id": "responsible-gaming-screen", "max_steps": 6}
    // Jev picks one tappable testID (or "scroll_down") per step; the runner taps it until until_id shows.
    // Not reached in max_steps (default 5; 1 tap without until_id) = FAIL.
{"check": {"judge": "Home shows a $25.00 card balance"}}
    // Jev yes/no on the screen after settle_s (default 1). PASS only on a confident yes.
```
- **Unsure = FAIL with the reason** (`decide ...: unsure (confidence 0.31 < 0.5, picked X), agent takes over`). No
  fallback model: the agent reading the result takes over that step. Tap confidence is Jev's for the chosen option;
  judge confidence is `|2p - 1|`, so at the default bar 0.5 a `p_true` between 0.25 and 0.75 is unsure.
- **Env:** `TYPESAFE_API_KEY` (read from the process env, else the Windows USER variable; never logged or written),
  `AE_DECIDE_MIN_CONF` (default `0.5`). No key = every decide/judge step is unsure.
- **Log:** each case with such steps gets `decisions` in `results.json`: kind, goal, candidates count, choice,
  confidence (`p_true` for judges), model, latency_ms, cost_usd, and `reason` when unsure.
- **Cost and speed:** ~$0.00004 per decision, ~0.14-0.3 s each from this PC. Offline eval (60 decisions,
  `.scratch/jev-exp/results.md`): 29/30 taps, 28/30 judges; every tap at confidence >= 0.5 was right.
- The ui_tree text of the test accounts goes to TypeSafe's service. Typed values (amounts, emails) never come from
  Jev: keep `fill` steps. Decide picks any tappable testID on screen, so keep goals away from money-moving buttons.
- Demo cases: `L4-20-decide-rewards`, `L4-21-decide-responsible-gaming`, `L4-22-judge-home`.

**Add a case:** copy the closest case in the same lane, change id/steps/checks, keep the hooks-on + route-Home
opening and the cleanup, then run it alone (`--case <id>`) twice; both PASS and the screenshots show what the
note claims. Cases read but never change money or limits.

Other runner tools: `signin.py` (sign-in: QA session route for password QA accounts, emailed code for lanes 1-4), `signup.py` (new account + Plaid sandbox ID check),
`emu.py` (Synkros emulator admin: `overview`, `feed EMU-L3`, `end EMU-L3`, `player <id>`).

## Accounts and sign-in

Phones d0-d3 run on `keep_data` disks and are already signed in (lanes 1-4, ID check passed, venue card linked,
$25.00 card balance). Sign in only when `ui_tree` shows `start-screen` or `login-screen`.

**No-Gmail sign-in (default for password QA accounts).** `python signin.py session d3 L4` (from `tests/boltbetz/`).
It reads the lane's email from the emulator lane registry, asks the sidecar QA session route
(`POST /api/ext/qa/accounts/:id/session`) for tokens, force-stops the app and sends the
`boltbetz-staging://e2e-session?accessToken=...` link (the app takes it only at cold start, in hook mode). Prints
`signed in L4 <email> on d3 -> home-screen` and exits 0, else exits 1. Verified 2026-10-09 on d0: route, link and
in-app sign-in work.
- Works only for a **password** QA account the sidecar provisioned (`aaronjlilla+qa-<label>-<hex>@gmail.com`) that has
  a backend account. **Lanes L1-L4 are still emailed-code `aaron+laneN@boltbetz.com` accounts: sign them in with the
  Gmail fallback below.** The sidecar refuses BoltBetz addresses on purpose, and emailed-code accounts answer 409
  `not_password_account`.
- **Do not run `lane_state <lane> fresh` on a working lane.** It retires the lane's backend account (anonymized,
  inactive; the card stays tied to it) and the new password account cannot reach Home: creating its backend account
  needs a real Plaid ID check (the backend checks the session with Plaid), and `kyc-passed` only sets the sidecar state.
  The app also sends a session-injected account without a backend account to the old phone-code sign-up.
- QA token: env `EXT_QA_TOKEN`, else asm-exec resolves `boltbetz/v2/staging/ext-admin-token`. Never printed. Each call
  resets the account's password, which can end that account's earlier sessions.

**Fallback: emailed code (Gmail).** Codes land in Aaron's inbox (`aaron@boltbetz.com`, plus-addressed),
are valid 3 minutes, and only the newest code works.

1. `python signin.py send d3 aaron+lane4@boltbetz.com` (Start -> Log In -> email -> Send code). Prints the screen ids.
2. Read the code with the Gmail connector (Claude Code: `mcp__claude_ai_Gmail__search_threads`, query
   `to:aaron+lane4@boltbetz.com newer_than:10m`, then read the newest message). Pass the digits straight on.
3. Tap the code field (testID `code-input`, label "6-digit code"), then `python signin.py code d3 <code>`.
   Done when `ui_tree` shows `home-screen`.

Codex has no Gmail connector: stop at step 1's screen and ask Aaron, or hand step 2 to a Claude session.
New account + ID check: `signup.py` docstring (Plaid sandbox identities; SSN last 4 from
`C:\dev\bb-infra\docs\70-ops\ops-dev-kyc-walkthrough.md`, passed as env `PLAID_SSN4`). Background:
`.scratch/test-matrix/issues/02-test-accounts-and-sign-in.md`.

## Staging and Synkros

- Backend: staging (`staging.bbapp01.com`, sidecar `ext-staging.bbapp01.com`, Auth0 `boltbetz-v2-staging`).
- Staging's Synkros (casino system) is the **Synkros emulator** at `https://synkros-emu.bbapp01.com` (mode
  `emulator`, set 2026-10-09). Lane players `Lane1..Lane4 QA` (L1 9000012 / card 97000012 since 2026-10-09; L2-L4 9000004..9000006 / cards 97000004..97000006),
  machines `EMU-L1..EMU-L4`.
- **Never switch the staging Synkros mode without asking Aaron.** The switch bumps a flag epoch that signs every
  open app out, on every phone and every tester's device, and parks player links.
- Emulator admin key: Secrets Manager `boltbetz/v2/sandbox/synkros-emulator-qa`, resolved by `emu.py` through
  `C:/dev/dev-harness/tools/asm-exec.ps1`. QR tokens are single use; a machine holds one session
  (`python emu.py end EMU-L3` frees it).
- Out of reach on these phones: push notifications (no Google Play services), camera image injection, real money.

## MCP setup

The MCP server is the daemon binary run as `agent-emud mcp --addr 127.0.0.1:7400`. It needs the daemon running.
Both configs below are committed and point at `daemon\target-mcp\release\agent-emud.exe`, a build kept apart from
the live daemon's exe so the daemon can be swapped while MCP clients hold theirs open. Rebuild it after `mcp.rs`
changes:
```sh
cd C:/dev/agent-emu/daemon && CARGO_TARGET_DIR=target-mcp cargo build --release -p agent-emud
```

**Claude Code**: `.mcp.json` (project scope) holds:
```json
{"mcpServers": {"agent-emu": {"type": "stdio",
  "command": "C:\\dev\\agent-emu\\daemon\\target-mcp\\release\\agent-emud.exe",
  "args": ["mcp", "--addr", "127.0.0.1:7400"]}}}
```
An interactive `claude` started in this folder asks once to approve it; `claude -p` loads it without asking.
Other folders: `claude mcp add -s user agent-emu -- C:\dev\agent-emu\daemon\target-mcp\release\agent-emud.exe mcp --addr 127.0.0.1:7400`.
Check: `claude mcp get agent-emu`.

**Codex**: `.codex/config.toml` (project scope, this project is trusted):
```toml
[mcp_servers.agent-emu]
command = 'C:\dev\agent-emu\daemon\target-mcp\release\agent-emud.exe'
args = ["mcp", "--addr", "127.0.0.1:7400"]
startup_timeout_sec = 20
tool_timeout_sec = 900   # start blocks ~2 min; Codex's default is 60
```
Other folders: `codex mcp add agent-emu -- C:\dev\agent-emu\daemon\target-mcp\release\agent-emud.exe mcp --addr 127.0.0.1:7400`
(then raise `tool_timeout_sec` in `~/.codex/config.toml`). Check: `codex mcp get agent-emu`.

**Tools** (40): `install_app`, `set_default_app`, `default_app`, `bugs`, `claim`, `unclaim`, `health`, `snapshot`, `restore`, `status`, `start`, `stop`, `start_many`, `stop_many`, `install_bundled`, `app`, `ui_tree`,
`screenshot`, `tap`, `swipe`, `type_text`, `key`, `deep_link`, `permission`, `clock`, `set_location`, `shell`,
`logs`, `issues`, `crash_events`, `memory`, `squeeze`, `lease`, `release`, `inject_camera_image`, `fleet`,
`fleet_stop`, `run_case`, `run_lanes`, `run_matrix` (the last three run the test runner: [Test runner](#test-runner)).
MCP replies with a frame become an image block: pass `"screenshot": false` on inputs to keep context small.

## Gotchas

| Gotcha | What happens | Fix |
| --- | --- | --- |
| Phone shows `ready: true` but phase `browser` | Firefox first run relaunches Firefox over the app for 2-4 min | wait for phase `ready` |
| Boot stuck in `android` 10+ min, `logcat.log` grows to 100s of MB | an old trim disabled Settings (FallbackHome, the only home app): "No home screen found", boot never completes, the radio floods logcat | daemon from commit f6c7f0c on re-enables it at boot; an older live daemon needs Aaron's redeploy. Do not stop the phone yourself |
| Home shows "We couldn't load your rewards." after the backend recovered | the app keeps the failed read; L4-02 fails on it | restart the app: `shell am force-stop com.boltbetz.staging`, `app launch` |
| `logs` slow or huge | logcat floods (`C:\dev\agent-emu-work\fleet\dN\logcat.log`) | always pass `filter`, `level` and a `cursor`; `issues` for a summary |
| `keep_data` refused, "disk is ..., this start needs ..." | disk belongs to another Device/image/system image | same `image`/`screen` as last time, or start without `keep_data` (then install + sign in) |
| Waiting for `Get-Process agent-emud` to exit never ends (daemon swap scripts) | test daemons (`daemon\target-expX\...`, other ports) share the process name | match the exe path `daemon\target\release\agent-emud.exe` or the owner of port 7400, never the bare name |
| Tip sheet stays over Home after Disconnect | it blocks the tab bar for later steps | tap `machine-tips-sheet-close-button` before navigating |
| App killed with the scanner open | live camera on a ~1 GB phone trips the low-memory killer | keep CAMERA denied on the scanner lane (runner does it on L3) |
| Fault shows no error | RTK served a cached read (60 s) | fault before the screen's first open, or pull to refresh |
| Route jump does nothing | signed out: only the auth navigator exists | sign in first |
| Tap lands in the wrong place | x/y were scaled to the last screenshot | `"device_px": true` with ui_tree bounds |
| `start` through Codex MCP times out | tool timeout 60 s by default | `tool_timeout_sec = 900` (committed config has it) |

## Troubleshooting

| Symptom | Check | Fix |
| --- | --- | --- |
| `curl` to 7401 refused | `curl -s -m 2 127.0.0.1:7401/health` | run `agent-emu.cmd`; read `%LOCALAPPDATA%\agent-emu\agent-emud.log` |
| MCP tool error `daemon 127.0.0.1:7400: ...` | daemon health as above | start the daemon; the MCP reconnects on the next call |
| `no Device dN` | `status` | the phone is not running: `start` it (with `keep_data` for BoltBetz phones), if no one else owns that id |
| `Device dN is still booting` | `status` phase | poll every 30 s; no progress for 3 checks = report it |
| `start` refused for memory | `status.available_mb` | free RAM is under 4000 MB: report it; never stop other agents' phones |
| `busy: Device is leased by another client` | someone holds the lease | use another phone or wait; `release` only your own |
| `ui_tree` lacks an id the screenshot shows | dump raced a transition, or a WebView (Plaid) | retry after a `wait_id`; drive WebViews by coordinates |
| Case FAIL `tap_id X: not on screen` | the case's FAIL PNG | wrong screen (sheet, dialog, signed out) or a renamed testID: fix the step or the starting state |
| Case passes alone, fails in a 4-phone run | rerun alone on the same commit | load flake (seen on L1-12, L2-04, L4-01): note it in one line and move on |
| App on Start/Login unexpectedly | was the Synkros mode switched, or the app data cleared? | [sign in](#accounts-and-sign-in); ask Aaron before touching the mode |
| Screen black or frozen | `screenshot` twice, `crash_events`, `issues` | restart the app; still black = report the phone, leave it running |
| `crash_events` shows the app killed (`lowmemorykiller`) | `memory`, `status.available_mb` | relaunch the app; camera off on scanner lanes |
| `inject_camera_image` error | expected | use the QA paste box |
