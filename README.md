# agent-emu

A fleet of light Android phones on your Windows PC, driven by Claude Code, for testing the BoltBetz staging app.

## Install

1. Download `agent-emu-Setup-<version>.exe` from this repo's GitHub **Releases** page.
2. Run it. Windows SmartScreen warns because the installer is unsigned: click **More info**, then **Run anyway**.
   It installs for your user only (no admin prompt).
3. The setup window downloads the phone images and asks for keys. Paste what you were given:
   - **Synkros emulator key**: the fake casino system (players, wallets, machines).
   - **Sidecar QA token**: creates and signs in test accounts, no email needed.
   - **TypeSafe key** (optional): plain-English "decide" and "judge" test steps.

   Keys are stored encrypted for your Windows user. Change them later in the app under Setup.
4. Setup registers the `agent-emu` MCP server and the `agent-emu` skill with Claude Code
   (`%USERPROFILE%\.claude\skills\agent-emu`). Install Claude Code first; if it was missing, run Setup again from the app.

## Use it from Claude Code

Open Claude Code in any folder and check the server is there:

```
claude mcp get agent-emu
```

Then just ask, for example:

- "start 4 phones and run the matrix, report every bug"
- "start d0, make a new test account on it and save a snapshot"
- "on d0, open Wallet, make the bank-link call fail with a 500 and show me what the app does"
- "run lane L4 on d3 and show me the screenshots of anything that failed"
- "list the open bugs in the home area"

The agent gets its operating guide from the MCP server and the skill. The same guide is
`%USERPROFILE%\.claude\skills\agent-emu\AGENTS.md`. Dashboard with live phone screens: http://127.0.0.1:7401/

## Updates, stopping and resuming

- **Updates** download by themselves from this repo's Releases (checked at start and every 4 hours). Nothing installs
  until you choose **Install update** in the tray menu. Before installing, agent-emu shows what is running (phones,
  test runs, agent claims, Appium sessions) and asks first.
- **Pause, update and resume:** agent-emu saves the session, stops the test runs and phones (their disks are kept),
  installs the update, then boots the same phones, reopens the app on each and continues every test run from the
  first case it had not finished.
- **Stop nicely:** tray, **Save session and stop phones**. Later: **Resume last session**. **Quit** saves the same way.
- The same from a terminal (or an agent), in `%LOCALAPPDATA%\agent-emu\app\tests\boltbetz`:
  `..\..\python\python.exe session.py busy | save [--snapshot] | resume`. `--snapshot` also keeps a restore point per
  phone (`session-<time>`, about 2 minutes per phone).
- What comes back: installed apps, sign-in, app data, claims, the app in front, unfinished test cases. What does not:
  each phone's memory. Phones cold-boot (about 2 minutes) and apps open on their start screen.

## Setup installs

Setup installs whatever is missing: Claude Code (Anthropic's official installer), Node.js LTS (inside agent-emu's own
folder), Appium's MCP server, Python and the test tools, the phone images. After Claude Code installs, open a terminal
once and run `claude` to sign in.

## Appium

Setup also installs and registers Appium's official MCP server (`appium-mcp`). It uses the Node.js, adb and Java
that agent-emu installs; no Android Studio needed.

Ask Claude Code, for example: "hand d0 to Appium and use Appium to check the Start screen shows". The agent:

1. calls agent-emu `appium` `{"device":"d0","package":"com.boltbetz.staging"}`, which returns the phone's `udid`
   (`127.0.0.1:6520` for d0, `6521` for d1, ...) and ready-made UiAutomator2 capabilities;
2. creates the session with appium-mcp `appium_session_management` (`action: create`, `platform: android`,
   those capabilities as a JSON string);
3. ends it with `action: delete`, which gives the phone back to agent-emu.

While an Appium session is open on a phone, agent-emu's `ui_tree` on that phone answers with an error (Android allows
one UI-automation client per phone). Other phones are unaffected, so Appium and agent-emu can each own different phones
at once. Tested on d0 with appium-mcp 1.97.0.
