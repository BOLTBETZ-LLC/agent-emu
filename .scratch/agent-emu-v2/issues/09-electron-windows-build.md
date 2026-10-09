# Build the Windows Electron app (unsigned NSIS installer, first-run wizard, MCP hookup)

Label: wayfinder:task
Parent: [map](../map.md)
Blocked by: none (design: [08](08-electron-shell.md))

## Plan

Code lives in `C:\dev\agent-emu\desktop\`. Electron 44.7.0, electron-builder 26.15.3, electron-updater 6.8.9
(npm, 2026-10-09). The app wraps the existing scripts; it does not rewrite them.

| Piece | File | What it does |
| --- | --- | --- |
| Payload | `scripts/prepare.ps1` | runs `daemon\package.ps1 -NoZip` (unchanged), then stages the official embeddable Python 3.14.7 (sha256 pinned to python.org's sigstore digest) with PyYAML vendored, `._pth` adds `Lib\site-packages` and `..\tests\boltbetz` |
| Installer | `electron-builder.yml` | NSIS, per-user forced (`build/installer.nsh` customInstallMode), no images; `extraResources` = `dist\agent-emu` minus `images\`, the Python payload, and runner data package.ps1 does not stage (`lanes.json`, `matrix\`, `plans\`) |
| Release | `electron-builder.release.yml` | extends the base: `azureSignOptions` (Artifact Signing, `signExts: .exe` so the daemon, crosvm, adb, python get signed too) + the CloudFront update URL. Values are REPLACE placeholders |
| Images | `scripts/pack-images.ps1` | zips `dist\agent-emu\images` + `manifest.json` (version, name, size, sha256) for the S3/CloudFront feed. The app reads the URL from `package.json` `agentEmu.imageManifest` |
| Main | `main.js` | wizard on first run, then attach-or-start the daemon, dashboard window, tray, updates, `--uninstall` |
| Wizard | `wizard.html` + `preload.js` | runs the chain by itself; buttons for retry, WHP fix, local zip, doctor, secrets |

### First run (all automatic, stops at the first failure)

1. **Check and install**: `setup.ps1 -Root <root> -Port -AgentPort -NoShortcuts -NoMcp -NoStart` from the installed
   resources. Its checks are the preflight (WHP, GPU, Vulkan, RAM, disk, ports); it copies the payload to the root
   (`%LOCALAPPDATA%\agent-emu\app` by default, apart from the dev log folder) and writes `config.cmd`. Then the app
   copies `agent-emud.exe` to `bin\agent-emud-mcp.exe` (the MCP copy; skipped when locked) and writes `app-version.txt`.
   WHP off: a button runs `dism /enable-feature /featurename:HypervisorPlatform` elevated (one UAC), then says restart.
2. **Phone images**: present = ok. Feed set = resumable download (HTTP Range), sha256 check, `tar -xf` into
   `<root>\images`, `images.version`. No feed yet = warning plus "Use a local zip" (image pack or the portable zip).
3. **Secrets**: `AE_SIDECAR_KEY` and `TYPESAFE_API_KEY` from the process env, else the Windows user env, encrypted with
   `safeStorage.encryptStringAsync` into `userData\secrets.json`. Paste fields replace them. Values never reach the
   renderer, logs, or any plain file; they are decrypted only into the daemon's env.
4. **Daemon**: `/health` answers = attach. Else `agent-emu.cmd --no-open` (its Start-Process detaches the daemon) with
   `AE_HOME`, `AE_WORK=<root>\images`, `AE_PYTHON=<root>\python\python.exe` and the secrets in env.
5. **MCP**: `claude mcp remove` + `claude mcp add -s user <name> -e AE_PYTHON=... -- <mcp exe> mcp --addr ...`;
   Codex gets a marked `[mcp_servers.<name>]` block in `~/.codex/config.toml` with `startup_timeout_sec = 20` and
   `tool_timeout_sec = 900`. Missing CLI = skipped, never a failure.
6. **Finish**: dashboard window on `http://127.0.0.1:<port>/` (the daemon's own ui.html). Doctor stays a button: it boots a phone.

Later starts: daemon attach/start, dashboard, `checkForUpdatesAndNotify`. Close = hide to tray. Tray Quit stops the
daemon only when its exe lives under the root (never someone else's daemon). Uninstall (not on update,
`${isUpdated}`): `agent-emu.exe --uninstall` stops that daemon, removes both MCP entries, deletes the root, then NSIS
deletes `%APPDATA%\agent-emu`.

Test-only overrides, read once at first run into `userData\config.json`: `AE_APP_ROOT`, `AE_APP_PORT`,
`AE_APP_AGENT_PORT`, `AE_MCP_NAME`, `AE_APP_CAPTURE` (PNG of the dashboard 4 s after load).

### Build

```sh
cd C:/dev/agent-emu/desktop
npm ci && node node_modules/electron/install.js     # npm 11 skips electron's install script
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/prepare.ps1   # -SkipPackage reuses dist\agent-emu
npx electron-builder --win nsis --publish never      # out\agent-emu-Setup-0.1.0.exe
```

## Proof (2026-10-09, this PC)

Installer 163 MB, unsigned. Installed with `/S` to `%LOCALAPPDATA%\Programs\agent-emu`: HKCU uninstall key, no UAC.
Launched with root in the session scratchpad, ports 7411/7410, MCP name `agent-emu-test` (the live daemon on 7400/7401
and its phones were not touched):
- Wizard: setup checks ok (WHP on, GPU, RAM, disk), images warned (no feed), both secrets imported encrypted, daemon
  answered on 7411, `claude mcp` registered and `Connected`, Codex block written.
- Dashboard window screenshot (`capturePage`): agent-emu panel, d0-d7 Off, "6.7 GB free".
- `claude mcp list`: `agent-emu-test: ...\root\bin\agent-emud-mcp.exe mcp --addr 127.0.0.1:7410 - Connected`.
  `codex mcp get agent-emu-test`: command, args, `tool_timeout_sec: 900`.
- `status` on HTTP 7411 and raw TCP 7410: `ok:true`, no devices. Live 7401 still `ok`.
- Bundled Python 3.14.7 imports `yaml` 6.0.3, `run` and `plan` from `<root>\tests\boltbetz`.
- No secret value found in app data, logs, `config.cmd`, `~/.codex/config.toml` or `~/.claude.json`.
- Uninstall `/S`: test daemon gone, root, `%APPDATA%\agent-emu`, install dir and uninstall key gone, MCP entries gone,
  `~/.codex/config.toml` byte-identical to the backup.

Not proven: doctor (boots a phone; not run), image download (no feed yet), a phone booting from the installed root,
secrets reaching a runner case, auto-update, signing.

## Left

- `daemon\package.ps1` should stage `lanes.json`, `matrix\`, `plans\` itself (the builder config covers it for now).
- The packaged daemon is the 13:33 `dist` build, before "Phones survive a daemon restart": run `prepare.ps1` without
  `-SkipPackage` on a clean tree before a release.
- `agent-emud preflight --json` (08) is not built; setup.ps1's checks stand in.
- Image feed: run `pack-images.ps1`, upload pack + manifest to the bucket behind CloudFront, set
  `agentEmu.imageManifest`. Then the update feed URL and Azure values in `electron-builder.release.yml`.
- App icon (default Electron icon now).

## Round 2 (2026-10-09): downloads in the app, pluggable app under test

Aaron, 2026-10-09: "bake the download and install parts of all of the dependencies a part of it ... as for the image,
you need to be able to plug in whatever you want".

- **Feed**: stack `agent-emu-dist` (`desktop/infra/dist-cdn.yml`, CloudFormation with the caller's own credentials, no
  role): private bucket `agent-emu-dist-670246014949` + CloudFront `d2pe4tj8fdcb6a.cloudfront.net` via OAC. S3 direct = 403.
  `https://d2pe4tj8fdcb6a.cloudfront.net/feed/v1/manifest.json` lists Python 3.14.7 embeddable, PyYAML 6.0.3 wheel,
  pict.exe 3.7.4 (each pinned to its publisher's sha256) and the base image pack (1.49 GB). Built by
  `scripts/pack-images.ps1` + `scripts/publish-feed.ps1`.
- **Base images hold no app**: `apk.img` is a 4 KiB zero placeholder, `apk.size` 0. The pack is scanned for "boltbetz"
  (raw bytes) before zipping: none.
- **App first run**: setup -> downloads (resumable Range, sha256, progress per 5%, `.feed\<name>` marks) -> secrets ->
  daemon -> MCP. Tray "Repair / re-download" clears the marks and reruns. Installer 153 MB (no Python now).
- **Daemon** (`daemon/agent-emud/src/apps.rs`): `install_app {source, device|devices, eas_project}`,
  `set_default_app {source}`, `default_app`. Source = local .apk, any https APK URL, expo.dev build page, EAS build id
  (`eas build:view`, needs any Expo project folder: `eas_project` / `AE_EAS_PROJECT_DIR`). `.aab` refused with how
  to get an APK. Wiped phones install the default app at boot (phase `app`). MCP tools carry the source list.

Proof on a clean app-data install from the rebuilt installer (main eb6fe87 + this branch), ports 7411/7410:
- First run downloaded all 4 components from CloudFront (4 sha256 matches), daemon up, Claude Code `Connected`,
  Codex entry written.
- One phone (d7) booted from the downloaded images: `pm list packages | grep -c boltbetz` = 0, `install_bundled`
  refused (no default app).
- `install_app` local path: `com.boltbetz.staging` 1.4.0 (22), runtime `36af5d1c...`, channel staging, Success, 40 s.
- `install_app` `https://expo.dev/artifacts/eas/PrDreXFiALCuSSTALkhmPQvdrc-OwQNJ9stUadTzgN0.apk` (build 4cd7f3dc),
  after uninstalling the app: downloaded 145 MB, Success, 51 s; launched to the Welcome screen.
- Before packaging, on a preview daemon: `set_default_app` + a wiped boot installed 1.4.0 at boot; the expo.dev build
  page URL resolved through `eas build:view`.
- Uninstall: phone and daemon stopped, root, app data and MCP entries gone, `~/.codex/config.toml` identical, live 7401 ok.

## Round 3 (2026-10-09): .aab

- Feed adds Temurin 21 JRE (`OpenJDK21U-jre_x64_windows_hotspot_21.0.12.1_1.zip`, Adoptium API sha256) and
  bundletool 1.18.3 (GitHub release digest). The app unpacks them to `<root>\jre` and `<root>\bin\bundletool.jar`.
- `install_app` turns an `.aab` into a universal APK (`bundletool build-apks --mode universal`), signed with a
  per-machine debug keystore made by keytool on first use at `%LOCALAPPDATA%\agent-emu\keystore\debug.keystore`.
- Proof, clean install, ports 7411/7410, phone d7: first run installed jre + bundletool from CloudFront;
  `install_app` `C:\Users\aaron\Downloads\boltbetz-1.2.0-vc20.aab` -> `com.boltbetz` 1.2.0 (20), Success, 63 s.
  That bundle is a production build (`channel: production`): uninstalled at once, never launched.
