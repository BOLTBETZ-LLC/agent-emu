// agent-emu desktop shell (Windows). Owns: first-run wizard, secrets (safeStorage), starting/attaching the
// agent-emud daemon (detached, outlives this app), the dashboard window (the daemon's own ui.html), tray,
// MCP registration for Claude Code + Codex, updates. Install/check logic stays in setup.ps1 / doctor.ps1 /
// agent-emu.cmd from the daemon payload (resources\agent-emu); this file only calls them.
const { app, BrowserWindow, Tray, Menu, ipcMain, dialog, safeStorage, nativeImage, shell } = require("electron");
const { execFile, spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { Readable } = require("stream");
const { pipeline } = require("stream/promises");

// Keys a user without AWS access is given. Name -> what it is (wizard label) and other env names that carry it.
const SECRETS_INFO = {
  AE_SIDECAR_KEY: { label: "Synkros emulator key (casino system: players, wallets, machines)", alias: ["SYNKROS_API_KEY"] },
  EXT_QA_TOKEN: { label: "Sidecar QA token (creates and signs in test accounts, no Gmail)", alias: [] },
  TYPESAFE_API_KEY: { label: "TypeSafe key (optional: plain-English decide/judge test steps)", alias: [] },
  AE_TEST_CARD: { label: "Deposit test card, as number|MMYY|CVV|name (optional: only the add-card case)", alias: [] },
};
const SECRET_NAMES = Object.keys(SECRETS_INFO);
const RES = app.isPackaged ? path.join(process.resourcesPath, "agent-emu") : path.join(__dirname, "..", "dist", "agent-emu");
const userData = app.getPath("userData");
const CONFIG = path.join(userData, "config.json");
const SECRETS = path.join(userData, "secrets.json");
const PKG = require("./package.json");

// Config is fixed at first run (env overrides only then), so the uninstaller sees the same root/ports/name.
function loadConfig() {
  try { return JSON.parse(fs.readFileSync(CONFIG, "utf8")); } catch {}
  const c = {
    root: process.env.AE_APP_ROOT || path.join(process.env.LOCALAPPDATA, "agent-emu", "app"),
    port: Number(process.env.AE_APP_PORT || 7401),
    agentPort: Number(process.env.AE_APP_AGENT_PORT || 7400),
    mcpName: process.env.AE_MCP_NAME || "agent-emu",
    setupDone: false,
  };
  saveConfig(c);
  return c;
}
function saveConfig(c) { fs.mkdirSync(userData, { recursive: true }); fs.writeFileSync(CONFIG, JSON.stringify(c, null, 2)); }
const cfg = loadConfig();
const ROOT = cfg.root;
const URL_UI = `http://127.0.0.1:${cfg.port}/`;
const MCP_EXE = path.join(ROOT, "bin", "agent-emud-mcp.exe");
const PY_EXE = path.join(ROOT, "python", "python.exe");
const PICT = path.join(ROOT, "bin", "pict.exe");

function log(m) {
  const line = `${new Date().toISOString()} ${m}`;
  try { fs.appendFileSync(path.join(userData, "app.log"), line + "\n"); } catch {}
  for (const w of BrowserWindow.getAllWindows()) if (w.__wizard) w.webContents.send("log", m);
}
function run(file, args, opts = {}) {
  return new Promise((resolve) => {
    const p = spawn(file, args, { windowsHide: true, stdio: ["ignore", "pipe", "pipe"], ...opts });
    let out = "";
    const onData = (d) => { const s = d.toString(); out += s; if (opts.echo !== false) s.split(/\r?\n/).filter(Boolean).forEach(log); };
    p.stdout?.on("data", onData); p.stderr?.on("data", onData);
    p.on("error", (e) => resolve({ code: -1, out: String(e) }));
    p.on("close", (code) => resolve({ code, out }));
  });
}
const ps = (file, args) => run("powershell.exe", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", file, ...args]);

async function health() {
  try { const r = await fetch(URL_UI + "health", { signal: AbortSignal.timeout(2000) }); return (await r.text()).trim() === "ok"; }
  catch { return false; }
}
// The daemon on our port is ours only when its exe lives under ROOT (never stop someone else's daemon).
async function ownDaemonRunning() {
  const r = await run("powershell.exe", ["-NoProfile", "-Command",
    `@(Get-Process agent-emud -ErrorAction SilentlyContinue | ? { $_.Path -like '${ROOT.replace(/'/g, "''")}\\*' }).Count`], { echo: false });
  return Number(r.out.trim()) > 0;
}

// ---- secrets: DPAPI via safeStorage. Values never go to logs, files in clear, or the renderer.
function readSecretFile() { try { return JSON.parse(fs.readFileSync(SECRETS, "utf8")); } catch { return {}; } }
async function setSecret(name, value) {
  const s = readSecretFile();
  s[name] = (await safeStorage.encryptStringAsync(value)).toString("base64");
  fs.writeFileSync(SECRETS, JSON.stringify(s));
  await writeRunnerKey(name, value);
}
// The test runner is started by MCP clients, not by this app, so it cannot get the keys from the daemon's env.
// Each key also goes to <root>\keys\<NAME>.dpapi, DPAPI-encrypted for this Windows user (tests\boltbetz\aekeys.py
// reads it). The value reaches PowerShell on stdin, never on a command line.
function writeRunnerKey(name, value) {
  const dir = path.join(ROOT, "keys");
  fs.mkdirSync(dir, { recursive: true });
  const script = "Add-Type -AssemblyName System.Security; $v = [Console]::In.ReadToEnd(); " +
    "$b = [Security.Cryptography.ProtectedData]::Protect([Text.Encoding]::UTF8.GetBytes($v), $null, 'CurrentUser'); " +
    "[IO.File]::WriteAllBytes($env:AE_KEYFILE, $b)";  // path via env: -Command does not fill $args
  return new Promise((resolve) => {
    const p = spawn("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", script],
      { windowsHide: true, stdio: ["pipe", "ignore", "ignore"], env: { ...process.env, AE_KEYFILE: path.join(dir, `${name}.dpapi`) } });
    p.on("close", resolve); p.on("error", resolve);
    p.stdin.end(value);
  });
}
async function secretEnv() {
  const s = readSecretFile(), env = {};
  for (const n of SECRET_NAMES) {
    if (!s[n]) continue;
    env[n] = (await safeStorage.decryptStringAsync(Buffer.from(s[n], "base64"))).result;
    for (const a of SECRETS_INFO[n].alias) env[a] = env[n];
  }
  return env;
}
// First run: take the keys already in the Windows user env (process env, else HKCU\Environment).
async function importEnvSecrets() {
  const s = readSecretFile();
  for (const n of SECRET_NAMES) {
    if (s[n]) {
      if (!fs.existsSync(path.join(ROOT, "keys", `${n}.dpapi`))) await writeRunnerKey(n, (await secretEnv())[n]);
      log(`ok    ${n} stored`); continue;
    }
    let v = [n, ...SECRETS_INFO[n].alias].map((k) => process.env[k]).find(Boolean);
    if (!v) v = (await run("powershell.exe", ["-NoProfile", "-Command", `[Environment]::GetEnvironmentVariable('${n}','User')`], { echo: false })).out.trim();
    if (v) { await setSecret(n, v); log(`ok    ${n} imported from your Windows user env, encrypted`); }
    else log(`WARN  missing: ${SECRETS_INFO[n].label}. Paste it under Secrets below.`);
  }
  return true;
}

// ---- steps
// One setup at a time: the wizard and the new-version startup path both ran it at once (two copies racing).
let setupRun = null;
function stepSetup() {
  setupRun = setupRun || stepSetupOnce().finally(() => { setupRun = null; });
  return setupRun;
}
async function stepSetupOnce() {
  const r = await ps(path.join(RES, "setup.ps1"), ["-Root", ROOT, "-Port", cfg.port, "-AgentPort", cfg.agentPort, "-NoShortcuts", "-NoMcp", "-NoStart"].map(String));
  if (r.code !== 0) return { ok: false, whp: /Hypervisor Platform is off/.test(r.out) };
  // Separate MCP exe: MCP clients hold it open; refresh only when not locked.
  try { fs.mkdirSync(path.dirname(MCP_EXE), { recursive: true }); fs.copyFileSync(path.join(ROOT, "agent-emud.exe"), MCP_EXE); }
  catch (e) { log(`skip  MCP exe in use, kept the old copy (${e.code})`); }
  fs.writeFileSync(path.join(ROOT, "app-version.txt"), app.getVersion());
  return { ok: true };
}
async function fixWhp() {
  log("Asking Windows for admin rights to turn on Hypervisor Platform...");
  const r = await run("powershell.exe", ["-NoProfile", "-Command",
    "Start-Process dism.exe -Verb RunAs -Wait -ArgumentList '/online','/enable-feature','/featurename:HypervisorPlatform','/all','/norestart'"]);
  log(r.code === 0 ? "Done. Restart Windows, then open agent-emu again. Still off after that: turn on CPU virtualization in the BIOS." : "Not changed (admin prompt refused?).");
}

const imagesPresent = () => fs.existsSync(path.join(ROOT, "images", "browser", "browser.img"));
// Windows bsdtar (reads zip); a tar.exe earlier on PATH (Git's GNU tar) cannot.
const TAR = path.join(process.env.SystemRoot || "C:\\Windows", "System32", "tar.exe");
const FEED = (PKG.agentEmu && PKG.agentEmu.feed) || "";
async function extractZip(zip, dest = path.join(ROOT, "images"), strip = 0) {
  const first = (await run(TAR, ["-tf", zip], { echo: false })).out.split(/\r?\n/)[0] || "";
  fs.mkdirSync(dest, { recursive: true });
  // The portable zip (agent-emu/images/...) or a pack (paths relative to dest).
  const args = first.startsWith("agent-emu/")
    ? ["-xf", zip, "-C", ROOT, "--strip-components=1", "agent-emu/images"]
    : ["-xf", zip, "-C", dest, ...(strip ? [`--strip-components=${strip}`] : [])];
  log(`Unpacking ${path.basename(zip)}...`);
  return (await run(TAR, args)).code === 0;
}
async function sha256(file) {
  const h = crypto.createHash("sha256");
  await pipeline(fs.createReadStream(file), h);
  return h.digest("hex");
}
// Resumable: a partial file continues with a Range request; sha256-checked before it is used.
async function fetchFile(url, dest, size, want) {
  let have = fs.existsSync(dest) ? fs.statSync(dest).size : 0;
  if (have > size) { fs.rmSync(dest); have = 0; }
  if (have < size) {
    const res = await fetch(url, { headers: have ? { Range: `bytes=${have}-` } : {} });
    if (!res.ok) throw new Error(`${path.basename(dest)}: HTTP ${res.status}`);
    if (have && res.status !== 206) have = 0;
    let got = have, lastPct = -1;
    const body = Readable.fromWeb(res.body);
    body.on("data", (c) => { got += c.length; const p = Math.floor((got * 20) / size) * 5; if (p !== lastPct) { lastPct = p; log(`download ${path.basename(dest)} ${p}%`); } });
    await pipeline(body, fs.createWriteStream(dest, { flags: have ? "a" : "w" }));
  }
  if ((await sha256(dest)) !== want) { fs.rmSync(dest); throw new Error(`${path.basename(dest)}: sha256 mismatch, deleted; run again`); }
  log(`ok    ${path.basename(dest)} sha256 matches`);
}
// Everything the installer leaves out comes from the feed manifest: Python, PyYAML, pict.exe, a JRE + bundletool
// (install_app turns .aab into APKs), phone base images.
// A component is done when <root>\.feed\<name> holds its sha256; Repair clears those marks.
async function stepDownloads() {
  if (!FEED) { log("WARN  no download feed in this build; use 'Use a local zip' for images"); return { ok: imagesPresent(), warn: true }; }
  const m = await (await fetch(FEED)).json();
  const marks = path.join(ROOT, ".feed"), dl = path.join(ROOT, "downloads");
  fs.mkdirSync(marks, { recursive: true }); fs.mkdirSync(dl, { recursive: true });
  for (const c of m.components) {
    const mark = path.join(marks, c.name);
    if (fs.existsSync(mark) && fs.readFileSync(mark, "utf8") === c.sha256) { log(`ok    ${c.name} present`); continue; }
    const file = path.join(dl, c.file);
    await fetchFile(new URL(c.file, FEED).href, file, c.size, c.sha256);
    const dest = path.join(ROOT, c.dest);
    if (c.unpack === "zip") { if (!(await extractZip(file, dest, c.strip || 0))) throw new Error(`unpack ${c.file} failed`); }
    else { fs.mkdirSync(path.dirname(dest), { recursive: true }); fs.copyFileSync(file, dest); }
    if (c.name === "python") {
      // Embeddable Python: its ._pth fixes sys.path; add vendored packages and the runner folder.
      const pth = fs.readdirSync(dest).find((f) => /^python\d+\._pth$/.test(f));
      fs.appendFileSync(path.join(dest, pth), "\r\nLib\\site-packages\r\n..\\tests\\boltbetz\r\n");
    }
    fs.rmSync(file);
    fs.writeFileSync(mark, c.sha256);
    log(`ok    ${c.name} installed`);
  }
  fs.writeFileSync(path.join(ROOT, "feed.version"), String(m.version));
  return { ok: imagesPresent() && fs.existsSync(PY_EXE) };
}
async function repair() {
  fs.rmSync(path.join(ROOT, ".feed"), { recursive: true, force: true });
  openWizard();
}

// The app under test: the phone images ship without one. Each app version pins the staging build in package.json
// (agentEmu.defaultApp); set it as the daemon's default app once per change. Phones started without keep_data install it.
async function ensureDefaultApp() {
  const want = PKG.agentEmu && PKG.agentEmu.defaultApp;
  if (!want) return true;
  if (cfg.defaultAppApplied === want) {
    // The flag alone can go stale (images dir rebuilt): trust it only while the daemon still has a default app.
    try {
      const cur = await (await fetch(URL_UI + "api", { method: "POST", body: JSON.stringify({ call: "default_app" }), signal: AbortSignal.timeout(10000) })).json();
      if (cur.ok && cur.default_app) return true;
    } catch (e) { log(`WARN  default_app: ${e.message}`); return false; }
  }
  log(`set the app under test: ${want} (downloads ~150 MB)`);
  try {
    const r = await (await fetch(URL_UI + "api", { method: "POST", body: JSON.stringify({ call: "set_default_app", source: want }), signal: AbortSignal.timeout(900000) })).json();
    if (!r.ok) { log(`WARN  set_default_app: ${r.error}`); return false; }
    cfg.defaultAppApplied = want; saveConfig(cfg);
    log(`ok    app under test: ${r.default_app.package} ${r.default_app.version_name} (${r.default_app.version_code})`);
    return true;
  } catch (e) { log(`WARN  set_default_app: ${e.message}`); return false; }
}
async function startDaemon() {
  if (await health()) { log(`ok    daemon already answers on ${URL_UI} (attached)`); return true; }
  const env = { ...process.env, ...(await secretEnv()), AE_HOME: ROOT, AE_WORK: path.join(ROOT, "images"), AE_PYTHON: PY_EXE, PICT };
  // agent-emu.cmd starts agent-emud hidden with Start-Process: not a child of this app, so it outlives it.
  // stdio ignored: the daemon would inherit our pipes and "close" would never fire.
  await run("cmd.exe", ["/d", "/c", path.join(ROOT, "agent-emu.cmd"), "--no-open"], { env, cwd: ROOT, stdio: "ignore" });
  const ok = await health();
  log(ok ? `ok    daemon answers on ${URL_UI}` : `FAIL  daemon did not answer; see ${path.join(ROOT, "logs", "agent-emud.log")}`);
  return ok;
}

// ---- tools the agents need: Node.js (Appium's MCP) and Claude Code itself, installed when missing
const NODE_DIR = path.join(ROOT, "node"), NODE_EXE = path.join(NODE_DIR, "node.exe");
const APPIUM_DIR = path.join(ROOT, "appium"), APPIUM_JS = path.join(APPIUM_DIR, "node_modules", "appium-mcp", "dist", "index.js");
const CLAUDE_NATIVE = path.join(process.env.USERPROFILE || "", ".local", "bin", "claude.exe");
async function download(url, dest) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  await pipeline(Readable.fromWeb(res.body), fs.createWriteStream(dest));
}
// Newest Node LTS from nodejs.org, checked against its SHASUMS256.txt, unpacked to <root>\node (portable, not on PATH).
async function ensureNode() {
  if (fs.existsSync(NODE_EXE)) return true;
  const rel = (await (await fetch("https://nodejs.org/dist/index.json")).json()).find((r) => r.lts && r.files.includes("win-x64-zip"));
  const name = `node-${rel.version}-win-x64.zip`, base = `https://nodejs.org/dist/${rel.version}/`, dl = path.join(ROOT, "downloads");
  fs.mkdirSync(dl, { recursive: true });
  log(`download Node.js ${rel.version} (LTS)`);
  const zip = path.join(dl, name);
  await download(base + name, zip);
  const want = (await (await fetch(base + "SHASUMS256.txt")).text()).split(/\r?\n/).find((l) => l.endsWith(`  ${name}`))?.split(" ")[0];
  if (!want || (await sha256(zip)) !== want) { fs.rmSync(zip, { force: true }); throw new Error(`${name}: sha256 mismatch, deleted`); }
  if (!(await extractZip(zip, NODE_DIR, 1))) throw new Error(`unpack ${name} failed`);
  fs.rmSync(zip, { force: true });
  log(`ok    Node.js ${rel.version} in ${NODE_DIR}`);
  return true;
}
// appium-mcp installed into <root>\appium once per app version, so Claude Code starts it at once (no npx download per session).
async function ensureAppium() {
  const stamp = path.join(APPIUM_DIR, "app-version.txt");
  if (fs.existsSync(APPIUM_JS) && fs.existsSync(stamp) && fs.readFileSync(stamp, "utf8") === app.getVersion()) return true;
  fs.mkdirSync(APPIUM_DIR, { recursive: true });
  log("install appium-mcp (Appium's official MCP server)...");
  const r = await run(path.join(NODE_DIR, "npm.cmd"), ["install", "--prefix", APPIUM_DIR, "appium-mcp@latest", "--no-audit", "--no-fund"],
    { shell: true, env: { ...process.env, PATH: `${NODE_DIR};${process.env.PATH}` }, echo: false });
  if (r.code !== 0 || !fs.existsSync(APPIUM_JS)) throw new Error(`npm install appium-mcp: ${r.out.trim().split(/\r?\n/).slice(-3).join(" | ")}`);
  fs.writeFileSync(stamp, app.getVersion());
  log("ok    appium-mcp installed");
  return true;
}
// Claude Code's official native installer (no admin): https://code.claude.com/docs/en/setup
async function ensureClaude() {
  if (await findClaude()) return true;
  log("Claude Code not found: installing it (irm https://claude.ai/install.ps1 | iex)...");
  const r = await run("powershell.exe", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "irm https://claude.ai/install.ps1 | iex"]);
  if (r.code !== 0 || !(await findClaude())) { log("WARN  Claude Code install failed; install it from https://code.claude.com/docs/en/setup, then Setup again"); return false; }
  log("ok    Claude Code installed. Sign in once: open a terminal and run `claude`.");
  return true;
}

// ---- MCP registration
function findClaude() {
  return new Promise((res) => execFile("where.exe", ["claude"], (e, out) => {
    const all = (out || "").split(/\r?\n/).filter(Boolean);
    // A fresh native install is not on this process's PATH yet.
    res(all.find((p) => p.toLowerCase().endsWith(".exe")) || all.find((p) => p.toLowerCase().endsWith(".cmd")) || (fs.existsSync(CLAUDE_NATIVE) ? CLAUDE_NATIVE : null));
  }));
}
async function claude(args) {
  const exe = await findClaude();
  if (!exe) return null;
  if (exe.toLowerCase().endsWith(".exe")) return run(exe, args, { echo: false });
  return run(`"${exe}"`, args.map((a) => `"${a}"`), { shell: true, echo: false });
}
const codexToml = () => path.join(process.env.CODEX_HOME || path.join(process.env.USERPROFILE, ".codex"), "config.toml");
const BEGIN = "# >>> agent-emu app", END = "# <<< agent-emu app";
function codexStrip(text) { return text.replace(new RegExp(`\\r?\\n?${BEGIN}[\\s\\S]*?${END}\\r?\\n?`, "g"), "\n"); }
async function stepMcp() {
  const addr = `127.0.0.1:${cfg.agentPort}`;
  await ensureClaude();
  let appiumOk = false;
  try { appiumOk = (await ensureNode()) && (await ensureAppium()); } catch (e) { log(`WARN  Appium tools: ${e.message}`); }
  await claude(["mcp", "remove", cfg.mcpName, "-s", "user"]);
  const add = await claude(["mcp", "add", "-s", "user", cfg.mcpName, "-e", `AE_PYTHON=${PY_EXE}`, "-e", `PICT=${PICT}`, "--", MCP_EXE, "mcp", "--addr", addr]);
  if (!add) log("skip  Claude Code not found; register later from Setup");
  else if (add.code === 0) { log(`ok    Claude Code: MCP ${cfg.mcpName} (user scope)`); log((await claude(["mcp", "get", cfg.mcpName])).out.split(/\r?\n/).slice(0, 6).join(" | ")); }
  else log(`WARN  claude mcp add failed: ${add.out.trim()}`);
  const skill = path.join(process.env.USERPROFILE, ".claude", "skills", "agent-emu");
  const skillSrc = app.isPackaged ? path.join(RES, "skill", "agent-emu") : path.join(__dirname, "qa", "skill", "agent-emu");
  try { fs.cpSync(skillSrc, skill, { recursive: true }); log(`ok    Claude Code: skill agent-emu in ${skill}`); }
  catch (e) { log(`WARN  skill copy failed: ${e.message}`); }
  // Appium's official MCP (embedded UiAutomator2) wants ANDROID_HOME\platform-tools\adb.exe: give it the daemon's own adb
  // (same version = one shared adb server) and the downloaded JRE. Phones join `adb devices` via the `appium` call.
  const sdk = path.join(ROOT, "android-sdk"), pt = path.join(sdk, "platform-tools");
  try { fs.cpSync(path.join(ROOT, "adb"), pt, { recursive: true }); } catch (e) { log(`WARN  android-sdk for Appium: ${e.message}`); }
  if (add && appiumOk) {
    await claude(["mcp", "remove", "appium-mcp", "-s", "user"]);
    const ap = await claude(["mcp", "add", "-s", "user", "appium-mcp", "-e", `ANDROID_HOME=${sdk}`, "-e", `JAVA_HOME=${path.join(ROOT, "jre")}`,
      "-e", "APPIUM_MCP_ON_CLIENT_DISCONNECT=delete_all", "--", NODE_EXE, APPIUM_JS]);
    log(ap.code === 0 ? "ok    Claude Code: MCP appium-mcp (user scope)" : `WARN  claude mcp add appium-mcp failed: ${ap.out.trim()}`);
  }
  const toml = codexToml();
  if (!fs.existsSync(path.dirname(toml))) { log("skip  Codex not found (~/.codex missing)"); return { ok: true }; }
  const lit = (s) => `'${s}'`; // TOML literal string: backslashes kept as-is
  const block = [BEGIN, `[mcp_servers.${cfg.mcpName}]`, `command = ${lit(MCP_EXE)}`, `args = ["mcp", "--addr", "${addr}"]`,
    "startup_timeout_sec = 20", "tool_timeout_sec = 900", `[mcp_servers.${cfg.mcpName}.env]`, `AE_PYTHON = ${lit(PY_EXE)}`, `PICT = ${lit(PICT)}`, END].join("\n");
  const old = fs.existsSync(toml) ? fs.readFileSync(toml, "utf8") : "";
  fs.writeFileSync(toml, codexStrip(old).replace(/\s*$/, "\n\n") + block + "\n");
  log(`ok    Codex: [mcp_servers.${cfg.mcpName}] in ${toml}`);
  return { ok: true };
}
async function unregisterMcp() {
  await claude(["mcp", "remove", cfg.mcpName, "-s", "user"]);
  await claude(["mcp", "remove", "appium-mcp", "-s", "user"]);
  const toml = codexToml();
  if (fs.existsSync(toml)) { const t = fs.readFileSync(toml, "utf8"); if (t.includes(BEGIN)) fs.writeFileSync(toml, codexStrip(t).replace(/\s*$/, "\n")); }
}

// ---- windows, tray
let dash = null, wizard = null, tray = null;
function openDashboard() {
  if (dash) { dash.show(); dash.focus(); return; }
  dash = new BrowserWindow({ width: 1400, height: 900, title: "agent-emu", autoHideMenuBar: true });
  dash.loadURL(URL_UI);
  dash.on("close", (e) => { if (!app.__quitting) { e.preventDefault(); dash.hide(); } });
  if (process.env.AE_APP_CAPTURE) dash.webContents.once("did-finish-load", () => setTimeout(async () => {
    fs.writeFileSync(process.env.AE_APP_CAPTURE, (await dash.webContents.capturePage()).toPNG());
  }, 4000));
}
function openWizard() {
  if (wizard) { wizard.show(); wizard.focus(); return; }
  wizard = new BrowserWindow({ width: 760, height: 720, title: "agent-emu setup", autoHideMenuBar: true,
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, sandbox: true } });
  wizard.__wizard = true;
  wizard.loadFile(path.join(__dirname, "wizard.html"));
  wizard.on("closed", () => { wizard = null; });
}
// ---- session pause/resume (tests\boltbetz\session.py) and updates (electron-updater, feed from electron-builder publish)
const SESSION_FILE = path.join(ROOT, "session.json");
const session = (cmd) => run(PY_EXE, [path.join(ROOT, "tests", "boltbetz", "session.py"), cmd],
  { env: { ...process.env, AE_API: URL_UI.replace(/\/$/, ""), AE_SESSION: SESSION_FILE } });
async function saveSession() {
  if (!(await health())) return false;
  const r = await session("save");
  log(r.code === 0 ? "ok    session saved, phones stopped" : `WARN  session save exit ${r.code}`);
  refreshTray();
  return r.code === 0;
}
async function stopAll() {
  const { response } = await dialog.showMessageBox({ type: "question", buttons: ["Save and stop", "Cancel"], cancelId: 1,
    message: "Save the session and stop every phone?", detail: "Live test runs stop and continue on Resume." });
  if (response === 0) await saveSession();
}
async function resumeSession() {
  if (!fs.existsSync(SESSION_FILE) || !(await startDaemon())) return;
  const r = await session("resume");
  log(r.code === 0 ? "ok    session resumed" : `WARN  session resume exit ${r.code}`);
  refreshTray();
}
let updater = null, updateReady = null;
function setupUpdates() {
  if (!app.isPackaged || updater) return;
  try { updater = require("electron-updater").autoUpdater; } catch (e) { log(`WARN  updates off: ${e.message}`); return; }
  updater.autoInstallOnAppQuit = false;
  updater.on("update-downloaded", (i) => { updateReady = i.version; log(`ok    update ${i.version} ready`); refreshTray(); });
  updater.on("error", (e) => log(`WARN  update: ${e.message}`));
  updater.checkForUpdates().catch((e) => log(`WARN  update check: ${e.message}`));
}
async function installUpdate() {
  // Phones and runs come back after the new version starts (cfg.resumeAfterUpdate, read at startup).
  // Phones keep running through the update (quit keep_devices; the new daemon adopts them). Only when that is
  // refused (a phone still booting) fall back to saving the session, which stops the phones.
  let kept = false;
  if (await ownDaemonRunning()) {
    try { kept = !!(await (await fetch(URL_UI + "api", { method: "POST", body: JSON.stringify({ call: "quit", keep_devices: true }) })).json()).ok; } catch {}
  }
  if (!kept) {
    cfg.resumeAfterUpdate = await saveSession(); saveConfig(cfg);
    if (await ownDaemonRunning()) { try { await fetch(URL_UI + "api", { method: "POST", body: JSON.stringify({ call: "quit" }) }); } catch {} }
  }
  app.__quitting = true; updater.quitAndInstall();
}
async function quitAll() {
  await saveSession();
  if (await ownDaemonRunning()) { try { await fetch(URL_UI + "api", { method: "POST", body: JSON.stringify({ call: "quit" }) }); } catch {} }
  app.__quitting = true; app.quit();
}
async function makeTray() {
  tray = new Tray(await app.getFileIcon(process.execPath, { size: "small" }).catch(() => nativeImage.createEmpty()));
  tray.setToolTip("agent-emu");
  tray.on("click", openDashboard);
  refreshTray();
}
function refreshTray() {
  if (!tray) return;
  const saved = fs.existsSync(SESSION_FILE);
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: "Open dashboard", click: openDashboard },
    ...(updateReady ? [{ label: `Install update ${updateReady}...`, click: installUpdate }] : []),
    { label: "Check for updates", enabled: !!updater, click: () => updater.checkForUpdates().catch((e) => log(`update check: ${e.message}`)) },
    { type: "separator" },
    { label: "Save session and stop phones...", click: stopAll },
    ...(saved ? [{ label: "Resume last session", click: resumeSession }] : []),
    { type: "separator" },
    { label: "Setup and doctor", click: openWizard },
    { label: "Repair / re-download", click: repair },
    { label: "Logs folder", click: () => shell.openPath(path.join(ROOT, "logs")) },
    { type: "separator" },
    { label: "Quit (saves the session, stops phones and daemon)...", click: quitAll },
  ]));
}

ipcMain.handle("info", () => ({ root: ROOT, url: URL_UI, mcpName: cfg.mcpName, version: app.getVersion() }));
ipcMain.handle("step", async (_e, name) => {
  log(`--- ${name}`);
  try {
    switch (name) {
      case "setup": return await stepSetup();
      case "downloads": return await stepDownloads();
      case "secrets": return { ok: await importEnvSecrets() };
      case "daemon": return { ok: (await startDaemon()) && (await ensureDefaultApp()) };
      case "mcp": return await stepMcp();
      case "finish": cfg.setupDone = true; saveConfig(cfg); openDashboard(); return { ok: true };
      case "fix-whp": await fixWhp(); return { ok: true };
      case "doctor": { const r = await ps(path.join(ROOT, "doctor.ps1"), ["-Port", String(cfg.port)]); return { ok: r.code === 0 }; }
      case "local-zip": {
        const pick = await dialog.showOpenDialog(wizard, { filters: [{ name: "zip", extensions: ["zip"] }], properties: ["openFile"] });
        if (pick.canceled) return { ok: false };
        return { ok: (await extractZip(pick.filePaths[0])) && imagesPresent() };
      }
    }
  } catch (e) { log(`FAIL  ${e.message}`); return { ok: false }; }
  return { ok: false };
});
ipcMain.handle("secrets-status", () => { const s = readSecretFile(); return SECRET_NAMES.map((n) => ({ name: n, label: SECRETS_INFO[n].label, has: !!s[n] })); });
ipcMain.handle("secret-save", async (_e, name, value) => {
  if (!SECRET_NAMES.includes(name) || !value) return false;
  await setSecret(name, value); log(`ok    ${name} saved (encrypted). Restart the daemon to use it.`); return true;
});

// ---- entry
if (process.argv.includes("--uninstall")) {
  // Run by the NSIS uninstaller (not on updates): stop our daemon, drop MCP entries, delete ROOT.
  (async () => {
    if (await ownDaemonRunning()) {
      try { await fetch(URL_UI + "api", { method: "POST", body: JSON.stringify({ call: "quit" }), signal: AbortSignal.timeout(60000) }); } catch {}
      for (let i = 0; i < 20 && (await ownDaemonRunning()); i++) await new Promise((r) => setTimeout(r, 1000));
    }
    await unregisterMcp();
    try { fs.rmSync(ROOT, { recursive: true, force: true, maxRetries: 5, retryDelay: 1000 }); } catch {}
    app.exit(0);
  })();
} else if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => (cfg.setupDone ? openDashboard() : openWizard()));
  app.on("window-all-closed", () => {}); // stay in the tray
  app.whenReady().then(async () => {
    await makeTray();
    const stale = (() => { try { return fs.readFileSync(path.join(ROOT, "app-version.txt"), "utf8") !== app.getVersion(); } catch { return true; } })();
    if (!cfg.setupDone) return openWizard();
    // New version: refresh the payload, then tools + MCP registration (appium-mcp, skill) for this version.
    // setup.ps1 asks a running daemon to exit with its phones kept, so a new version always gets its own daemon
    // (it used to attach to the old one and run old code until a reboot).
    if (stale) { const r = await stepSetup(); if (!r.ok) return openWizard(); await stepMcp().catch((e) => log(`WARN  mcp: ${e.message}`)); }
    if (await startDaemon()) { openDashboard(); ensureDefaultApp(); } else openWizard();
    // After an update: boot the saved phones and continue the paused test runs.
    if (cfg.resumeAfterUpdate) { cfg.resumeAfterUpdate = false; saveConfig(cfg); await resumeSession(); }
    setupUpdates(); refreshTray();
  }).catch((e) => { log(`FAIL  startup: ${e.stack || e.message}`); openWizard(); }); // never start with no window
}
