# agent-emu: operating guide (QA install)

agent-emu runs a fleet of light Android phones (Devices `d0`..`dN`) on this Windows PC to test the BoltBetz
staging app. One daemon owns them. Drive it with the `agent-emu` MCP tools (same names as the HTTP calls:
`POST http://127.0.0.1:7401/api` with `{"call":"<tool>",...}`). Every reply carries `ok`: read it; `ok:true`
proves the call ran, not that the app did the right thing. Dashboard: http://127.0.0.1:7401/

## Rules
- Leave other agents' phones and runs alone. Before `start`/`stop`, read `status`; a phone someone `claim`ed
  is theirs. `claim` the phones you use (`owner` = your name) and `unclaim` them after.
- One test run per phone at a time. A second runner on the same phone corrupts both.
- Never press Back on the Welcome/Start screen. Navigate with `deep_link` routes, tab testIDs or a close button.
- Production is read-only. Phones talk to staging only. Never install-and-launch a production build.
- Never switch the staging Synkros mode: it signs every app on every device out. Ask a human.
- Secrets (keys, sign-in codes) never go into files or chat.

## Phone lifecycle
- Phase order: spawning -> booting -> android -> setup -> browser -> `ready`. Ready means `phase == "ready"`;
  the `ready` boolean turns true earlier (during `browser`). Calls on a phone not up fail "still booting".
- Start: `start_many` `{"devices":["d0","d1","d2","d3"],"image":"slim5","screen":"iphone17promax-half","keep_data":true}`.
  Then poll `status` every 30 s until each phase is `ready` (~2 min). No progress for 3 polls = report it.
- `keep_data:true` keeps the installed app and sign-in. Refused on a fresh disk or other image: start without
  `keep_data` (disk wiped), then `install_bundled` (or `install_app` with an APK path, APK URL, expo.dev build page
  or EAS build id; `set_default_app` makes it the app every new phone installs).
- Limit 4 phones. A boot is refused under ~4 GB free host RAM: report it, never stop others' phones.
- `stop`/`stop_many` sync the disk first, so the next `keep_data` start reuses it.

## Core loop (one phone)
1. `status`: your phone in phase `ready`.
2. Open the app: `app` `{"device":"d0","launch":"com.boltbetz.staging"}`. Restart it: `shell`
   `am force-stop com.boltbetz.staging`, then `launch` again.
3. Where am I: `ui_tree` first (~80 ms). Match `resource-id` (the app's testID), then `text`. Bounds are device
   pixels. Screen ids: `start-screen`, `login-screen`, `home-screen`, `wallet-main-screen`, `settings-screen`,
   `rewards-screen`, `responsible-gaming-screen`, `notifications-screen`. Tabs: `tab-home`, `tab-wallet`,
   `tab-boltbetz` (scanner), `tab-rewards`, `tab-settings`.
4. Act: `tap` the center of a node's `bounds` with `"device_px": true`. Pass `"screenshot": false` on every input
   (`tap`, `swipe`, `type_text`, `key`) to keep replies small. Text: tap the field, then `type_text`. Scroll: `swipe`
   from high y to low y.
5. Verify the outcome, not the call:
   - the expected testID is in `ui_tree`, and a `screenshot` (pass `size` like `360x780`) you actually looked at;
   - no crash: note `crash_events.last` before acting, then `crash_events` `{"after":<seq>,"filter":"com.boltbetz.staging"}`
     returns nothing;
   - no JS errors: `logs` `{"max_lines":1}` gives a `cursor` before acting; after, `logs` with that `cursor`,
     `filter` `com.boltbetz.staging`, `level` `E`; look for `FATAL EXCEPTION` or `ReactNativeJS` TypeError/Unhandled.
   - `issues` summarizes E/F log lines; `health` checks adb, app version, signed in, last crash.

## QA hooks (staging app deep links)
Send with `deep_link` and `"package":"com.boltbetz.staging"`, then wait ~1 s.
- `boltbetz-staging://e2e-session/hooks?on=1` hook mode on (QA overlay, test paste boxes).
- `.../route/MainFlow/Tabs/Home` jump to a screen (also `Tabs/Settings`, `Tabs/Wallet/WalletMain`,
  `MainFlow/ResponsibleGaming`, `MainFlow/Notifications`). Signed out, route jumps are silently dropped.
- `.../fault?endpoint=getLinkedPlayerAccounts&preset=500` make the next calls to that endpoint fail on the phone.
  Presets: 500, problem, validation, forbidden, timeout, offline, decline, synkros, sila. `.../fault?clear=1` clears.
  Reads are cached 60 s: fault before the screen first opens, or pull to refresh.
- Machine QR (no camera): with hooks on, on `tab-boltbetz` tap `qa-machine-scan-input`, `type_text` the token, tap
  `qa-machine-scan-submit`.

## Sign-in
Signed in = `home-screen` in `ui_tree`. A fresh phone is signed out. Make a new signed-in test account with no
email: run `%LOCALAPPDATA%\agent-emu\app\python\python.exe newaccount.py d0` from
`%LOCALAPPDATA%\agent-emu\app\tests\boltbetz` (needs the Sidecar QA token entered in the app's Setup). It prints the
account's email and ends on Home. Then `snapshot` `{"device":"d0","name":"d0-golden"}` saves that signed-in state;
`restore` puts it back when a test leaves the phone broken.

## Running tests (parallel)
- Check no run is live on your phones first (`status` claims; a `run.py-<pid>` owner = a run holds it).
- `run_case` `{"device":"d0","case":"L4-01"}` one case. `run_lanes` `{"phones":["d0","d1","d2","d3"]}` every lane, one
  phone per lane, in parallel (~60 s). `run_matrix` `{"phones":["d0","d1","d2","d3"],"matrix":"cases/matrix"}` deals
  every matrix case across the phones, longest first; `areas` (e.g. `["wallet"]`) and `shard` (`"1/2"`) narrow it.
- Replies: a PASS/FAIL grid plus `results` = the run folder (`results.json`, `grid.md`, a PNG per screenshot check,
  `<case>-FAIL.png`). Open the FAIL PNG before calling anything an app bug; a case that fails in a 4-phone run but
  passes alone is a load flake.
- Lane cases (L1..L4) expect the lane accounts in `tests\boltbetz\lanes.json` on their phones (d0..d3).
- Bug feed: every final FAIL is folded into a bug (same area, step, endpoint and first failed check = one bug,
  `count` goes up). Read it with `bugs` (`{}` = all, `{"area":"home","q":"pin"}` filters, `"full":true` = every
  occurrence with screenshots and app error lines). Report bugs from this feed: title, count, cases, phones,
  failed check, screenshot path. Nothing is filed anywhere automatically.

## Appium (appium-mcp tools)
- Hand the phone over first: `appium` `{"device":"d0","package":"com.boltbetz.staging"}` stops agent-emu's screen
  reader on that phone, adds it to `adb devices` and returns `udid` + `capabilities`.
- Then appium-mcp `appium_session_management` `{"action":"create","platform":"android","capabilities":"<that JSON>"}`.
  Skip `select_device`: the udid is already known.
- testIDs are bare resource-ids: find with strategy `-android uiautomator` and `new UiSelector().resourceId("home-screen")`,
  or xpath `//*[@resource-id='home-screen']`. Strategy `id` adds the package prefix and finds nothing.
- During the session agent-emu `ui_tree` on that phone errors ("an Appium session holds this phone"). Read the screen
  through Appium there. `action: delete` ends the session and gives the phone back. Call `appium` again before the
  next session: any `ui_tree` in between restarts agent-emu's reader and Appium then fails with "UiAutomation not connected".
- Split the fleet: some phones on Appium, the rest on agent-emu tools or the test runner.

## Pause and resume (updates, stopping work)
From `%LOCALAPPDATA%\agent-emu\app\tests\boltbetz` with `..\..\python\python.exe`:
- `session.py busy`: JSON of running phones, live test runs, claims, Appium sessions; exit 3 = busy.
- `session.py save [--snapshot]`: records phones and live runs, stops the runs and phones (disks kept).
- `session.py resume`: boots the same phones, restores claims, reopens the app in front, continues each run with
  `run.py --resume <results folder>` (finished cases kept). Phones cold-boot: apps open on their start screen.
The desktop app does save/resume around updates. Never pause someone else's work without asking the person.

## Gotchas
- Tap lands wrong: you used screenshot coordinates. Use `ui_tree` bounds with `"device_px": true`.
- `ui_tree` lacks an id the screenshot shows: it raced a transition (retry), or it is a WebView (Plaid): use coordinates.
- First tap after a minute idle takes up to ~0.5 s (idle memory caps lift). Normal.
- "We couldn't load your rewards." stays after the backend recovered: restart the app.
- Tip sheet over Home after a machine disconnect: tap `machine-tips-sheet-close-button`.
- App killed with the scanner open: low memory from the live camera. Keep CAMERA denied (`permission` revoke).
- No push notifications, camera image injection or real money on these phones.
