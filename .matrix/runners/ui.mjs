// UI checks for the agent-emu dashboard (features.tsv rows ui-*).
//
// Usage: node .matrix/runners/ui.mjs            (all ui-* rows)
//        node .matrix/runners/ui.mjs ui-04 ui-10 (only these)
//
// Each check loads the REAL dashboard (daemon/agent-emud/src/ui.html) served by
// .matrix/fake-daemon.py on a free port, drives it with Playwright (headless chromium),
// and asserts the plain claim with the cheapest DOM signal. Two lanes run in parallel,
// each with its own fake daemon and its own browser context per check. A failing check
// never stops the run. Screenshots are taken only on fail.
//
// Outputs: .matrix/runs/<stamp>/<id>.png (fails), .matrix/runs/<stamp>.tsv (all results),
// and the status column of .matrix/features.tsv for the ids that ran.

import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";
import net from "node:net";
import { spawn } from "node:child_process";

const require = createRequire(import.meta.url);
const { chromium } = require("../node_modules/playwright");

const HERE = path.dirname(fileURLToPath(import.meta.url));
const MATRIX = path.resolve(HERE, "..");
const FAKE = path.join(MATRIX, "fake-daemon.py");
const FEATURES = path.join(MATRIX, "features.tsv");
const RUNS = path.join(MATRIX, "runs");
const TIMEBOX_MS = 20000;
const LANES = 2;

// ---------- helpers ----------

function freePort() {
  return new Promise((resolve, reject) => {
    const s = net.createServer();
    s.unref();
    s.on("error", reject);
    s.listen(0, "127.0.0.1", () => {
      const { port } = s.address();
      s.close(() => resolve(port));
    });
  });
}

async function waitHttp(url, ms = 8000) {
  const end = Date.now() + ms;
  for (;;) {
    try {
      const r = await fetch(url);
      if (r.ok) return;
    } catch {}
    if (Date.now() > end) throw new Error("fake daemon did not start: " + url);
    await new Promise((r) => setTimeout(r, 150));
  }
}

function startFake(port) {
  const child = spawn("python", ["-I", FAKE, String(port)], { stdio: "ignore", windowsHide: true });
  return child;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function withTimeout(p, ms, label) {
  let timer;
  const t = new Promise((_, rej) => {
    timer = setTimeout(() => rej(new Error(`timeout ${ms / 1000}s (${label})`)), ms);
  });
  return Promise.race([p, t]).finally(() => clearTimeout(timer));
}

// ---------- check context ----------

function makeT(lane) {
  const base = `http://127.0.0.1:${lane.port}`;
  const t = {
    base,
    page: null,
    // Top-level state keys replace; `devices` merges per device (see fake-daemon.py).
    async fake(set) {
      const { devices, ...rest } = set;
      if (Object.keys(rest).length) await fetch(base + "/__fake", { method: "POST", body: JSON.stringify({ set: rest }) });
      if (devices) await fetch(base + "/__fake", { method: "POST", body: JSON.stringify({ devices }) });
    },
    async dev(devices) {
      await fetch(base + "/__fake", { method: "POST", body: JSON.stringify({ devices }) });
    },
    async drop(ids) {
      await fetch(base + "/__fake", { method: "POST", body: JSON.stringify({ drop: ids }) });
    },
    async calls() {
      return (await (await fetch(base + "/__calls")).json()) || [];
    },
    async callsNamed(name) {
      return (await t.calls()).filter((c) => c.call === name);
    },
    async waitCalls(pred, ms = 8000) {
      const end = Date.now() + ms;
      for (;;) {
        const hit = pred(await t.calls());
        if (hit) return hit;
        if (Date.now() > end) return null;
        await sleep(150);
      }
    },
    // Number of rendered (not display:none) elements matching sel.
    async vis(sel) {
      return t.page.evaluate((s) => [...document.querySelectorAll(s)].filter((e) => e.getClientRects().length > 0).length, sel);
    },
    async visText(sel) {
      return t.page.evaluate((s) => {
        const e = document.querySelector(s);
        return e && e.getClientRects().length > 0 ? e.innerText.trim() : null;
      }, sel);
    },
    async waitVisText(sel, re, ms = 8000) {
      const end = Date.now() + ms;
      let last = null;
      for (;;) {
        last = await t.visText(sel);
        if (last != null && re.test(last)) return last;
        if (Date.now() > end) return last;
        await sleep(200);
      }
    },
    async click(sel) {
      await t.page.locator(sel).first().click({ timeout: 6000 });
    },
    // Tile HUD buttons only take pointer events while the phone is hovered (ui.html .hud CSS).
    async clickHud(id, act) {
      await t.page.hover(`#grid > article.tile[data-id="${id}"] .phone`);
      await t.click(`#grid > article.tile[data-id="${id}"] button[data-act="${act}"]`);
    },
    async clickNth(sel, n) {
      await t.page.locator(sel).nth(n).click({ timeout: 6000 });
    },
    async waitPill(id, label, ms = 10000) {
      return t.waitVisText(`#grid > article.tile[data-id="${id}"] .pill`, new RegExp("^" + label + "$"), ms);
    },
  };
  return t;
}

// ---------- page open ----------

async function openPage(lane, browser, o = {}) {
  const ctx = await browser.newContext({ viewport: o.viewport || { width: 1280, height: 860 } });
  const errors = [];
  const page = await ctx.newPage();
  page.on("console", (m) => { if (m.type() === "error") errors.push("console: " + m.text()); });
  page.on("pageerror", (e) => errors.push("pageerror: " + e.message));
  const kv = {};
  if (o.n !== undefined) kv["ae.n.shown"] = JSON.stringify(o.n);
  if (o.mine) kv["ae.mine"] = JSON.stringify(o.mine);
  await page.addInitScript((seed) => {
    for (const [k, v] of Object.entries(seed)) localStorage.setItem(k, v);
  }, kv);
  return { ctx, page, errors };
}

async function gotoPage(t, page, { offline = false } = {}) {
  await page.goto(t.base + "/", { waitUntil: "domcontentloaded" });
  await page.waitForSelector(offline ? "#conn.off" : "#conn.on", { timeout: 8000 });
}

// ---------- checks ----------
// Each check: async (t) => { pass: boolean, observed: string } and may call t.page.
// Setup: lane fake is reset before each check; t.open(opts) opens a fresh page.

const CHECKS = {};

CHECKS["ui-01"] = async (t, open) => {
  const out = [];
  for (let n = 1; n <= 4; n++) {
    await t.fake({ devices: {}, max_phones: 4 });
    await t.reset?.();
    const p = await open({ n: n - 1 });
    await gotoPage(t, p.page);
    await t.click(`#nseg button >> nth=${n - 1}`);
    await sleep(300);
    // N=1 is the solo stage (grid hidden): the phone frame is the stage .phone.
    const count = n === 1 ? await t.vis("#phones .phone") : await t.vis("#grid > article.tile");
    out.push(`N=${n}:${count}`);
    await p.ctx.close();
    if (n === 1 && count !== 1) return { pass: false, observed: out.join(" ") };
    if (n > 1 && count !== n) return { pass: false, observed: out.join(" ") };
  }
  return { pass: true, observed: out.join(" ") };
};

CHECKS["ui-02"] = async (t, open) => {
  await t.fake({ max_phones: 2, devices: {} });
  const p = await open({ n: 4 });
  await gotoPage(t, p.page);
  const count = await p.page.locator("#nseg button").count();
  const labels = await p.page.locator("#nseg button").allTextContents();
  return { pass: count === 2, observed: `picker buttons=${count} labels=${labels.join(",")} max_phones=2` };
};

CHECKS["ui-03"] = async (t, open) => {
  await t.fake({ available_mb: 2500, devices: {} });
  const p = await open({ n: 1 });
  await gotoPage(t, p.page);
  const b2 = p.page.locator("#nseg button").nth(1);
  const disabled = await b2.isDisabled();
  const title = await b2.getAttribute("title");
  return { pass: disabled && /fits 1 phone/.test(title || ""), observed: `btn2 disabled=${disabled} title="${title}"` };
};

CHECKS["ui-04"] = async (t, open) => {
  await t.fake({ devices: { d0: { phase: "ready", ready: true } } });
  const p = await open({ n: 1 });
  await gotoPage(t, p.page);
  await sleep(600);
  const phones = await t.vis("#phones .phone");
  const tiles = await t.vis("#grid > article.tile");
  return { pass: phones === 1, observed: `visible phone frames=${phones} visible grid tiles=${tiles}` };
};

CHECKS["ui-05"] = async (t, open) => {
  await t.fake({ devices: { d0: { phase: "android", ready: false, phase_since_ago_ms: 5000 } } });
  const p = await open({ n: 2 });
  await gotoPage(t, p.page);
  const sel = '#grid > article.tile[data-id="d0"] .say .ph';
  const a = await t.waitVisText(sel, /Starting Android/, 8000);
  await sleep(2200);
  const b = await t.visText(sel);
  const sa = (a || "").match(/(\d+) s/), sb = (b || "").match(/(\d+) s/);
  const ok = !!a && /Starting Android/.test(a) && sa && sb && +sb[1] > +sa[1];
  return { pass: !!ok, observed: `"${a}" then "${b}"` };
};

CHECKS["ui-06"] = async (t, open) => {
  await t.fake({ devices: { d0: { phase: "sleeping", ready: false } } });
  const p = await open({ n: 2 });
  await gotoPage(t, p.page);
  const pill = await t.visText('#grid > article.tile[data-id="d0"] .pill');
  await t.click('#grid > article.tile[data-id="d0"] .say button[data-v="start"]');
  const wake = await t.waitCalls((c) => c.find((x) => x.call === "wake" && x.device === "d0"));
  const after = await t.waitPill("d0", "Running", 15000);
  return { pass: pill === "Sleeping" && !!wake && after === "Running", observed: `before="${pill}" wake_call=${!!wake} after="${after}"` };
};

CHECKS["ui-07"] = async (t, open) => {
  await t.fake({ devices: { d0: { phase: "ready", ready: true } } });
  const p = await open({ n: 2 });
  await gotoPage(t, p.page);
  await t.waitPill("d0", "Running", 5000);
  await t.clickHud("d0", "sleep");
  const c = await t.waitCalls((cs) => cs.find((x) => x.call === "sleep" && x.device === "d0"));
  const pill = await t.waitPill("d0", "Sleeping", 10000);
  return { pass: !!c && pill === "Sleeping", observed: `sleep_call=${!!c} pill="${pill}"` };
};

CHECKS["ui-08"] = async (t, open) => {
  await t.fake({ devices: { d0: { phase: "ready", ready: true } } });
  const p = await open({ n: 2 });
  await gotoPage(t, p.page);
  await t.waitPill("d0", "Running", 5000);
  await t.clickHud("d0", "more");
  await t.click("#menustop");
  const c = await t.waitCalls((cs) => cs.find((x) => x.call === "stop" && x.device === "d0"));
  const pill = await t.waitPill("d0", "Off", 10000);
  return { pass: !!c && pill === "Off", observed: `stop_call=${!!c} pill="${pill}"` };
};

CHECKS["ui-09"] = async (t, open) => {
  await t.fake({ devices: { d0: { phase: "ready", ready: true } } });
  const p = await open({ n: 2 });
  await gotoPage(t, p.page);
  await t.waitPill("d0", "Running", 5000);
  await t.clickHud("d0", "more");
  await t.click("#menureset");
  const armed = await t.page.locator("#menureset").innerText();
  const before = (await t.calls()).length;
  await t.click("#menureset");
  const st = await t.waitCalls((cs) => cs.find((x) => x.call === "start" && x.device === "d0" && x.fresh === true), 10000);
  const stopped = (await t.calls()).some((x) => x.call === "stop");
  return { pass: /Wipes app/.test(armed) && !!st && stopped && before >= 0, observed: `armed_text="${armed.trim()}" stop=${stopped} start_fresh=${!!st}` };
};

CHECKS["ui-10"] = async (t, open) => {
  await t.fake({ fail: { start: "disk_mismatch: d0 disk was made for image slim4, this start needs slim5" }, devices: {} });
  const p = await open({ n: 2 });
  await gotoPage(t, p.page);
  await t.click('#grid > article.tile[data-id="d0"] .say button[data-v="start"]');
  const say = await t.waitVisText('#grid > article.tile[data-id="d0"] .say', /older image/, 8000);
  const btn = await p.page.locator('#grid > article.tile[data-id="d0"] .say button', { hasText: "Reset phone" }).count();
  return { pass: /older image/.test(say || "") && btn === 1, observed: `say="${(say || "").slice(0, 120)}" reset_button=${btn}` };
};

CHECKS["ui-11"] = async (t, open) => {
  await t.fake({ available_mb: 2000, devices: {} });
  const p = await open({ n: 2 });
  await gotoPage(t, p.page);
  await t.click('#grid > article.tile[data-id="d0"] .say button[data-v="start"]');
  const say = await t.waitVisText('#grid > article.tile[data-id="d0"] .say', /Not enough free memory/, 8000);
  const retry = await p.page.locator('#grid > article.tile[data-id="d0"] .say button', { hasText: "Retry" }).count();
  return { pass: /Not enough free memory: 2000 MB free, 4000 MB needed/.test(say || "") && retry === 1, observed: `say="${(say || "").slice(0, 120)}" retry_button=${retry}` };
};

CHECKS["ui-12"] = async (t, open) => {
  await t.fake({ devices: {} });
  const p = await open({ n: 2 });
  await gotoPage(t, p.page);
  const screen = await p.page.locator("#s_screen").inputValue();
  const image = await p.page.locator("#s_image").inputValue();
  await t.click("#startall");
  const starts = await t.waitCalls((cs) => cs.filter((x) => x.call === "start").length >= 2 ? cs.filter((x) => x.call === "start") : null);
  const ids = (starts || []).map((x) => x.device).sort();
  const okArgs = (starts || []).every((x) => x.image === "slim5" && x.screen === "samsung-a16" && x.fresh !== true);
  return {
    pass: ids.join(",") === "d0,d1" && okArgs,
    observed: `page defaults image=${image} screen=${screen}; start calls for ${ids.join(",") || "none"}; args ${JSON.stringify((starts || [])[0] || {})}. keep_data not sent by page (daemon keeps disks by default, API.md)`,
  };
};

CHECKS["ui-13"] = async (t, open) => {
  await t.fake({ offline: true, devices: {} });
  const p = await open({});
  await gotoPage(t, p.page, { offline: true });
  const hint = await t.visText("#hint");
  const startDisabled = await p.page.locator("#startall").isDisabled();
  const pickerDisabled = await p.page.locator("#nseg button").first().isDisabled().catch(() => null);
  return {
    pass: /not answering/.test(hint || "") && startDisabled && pickerDisabled === true,
    observed: `hint="${(hint || "").replace(/\n/g, " ").slice(0, 100)}" start_disabled=${startDisabled} picker1_disabled=${pickerDisabled}`,
  };
};

CHECKS["ui-14"] = async (t, open) => {
  await t.fake({ devices: {} });
  const p = await open({});
  await gotoPage(t, p.page);
  const hint = await t.waitVisText("#hint", /How many phones do you want\?/, 8000);
  return { pass: /How many phones do you want\?/.test(hint || ""), observed: `hint="${(hint || "").replace(/\n/g, " ").slice(0, 100)}"` };
};

CHECKS["ui-15"] = async (t, open) => {
  await t.fake({ devices: { d0: { phase: "ready", ready: true } } });
  const p = await open({ n: 2 });
  await gotoPage(t, p.page);
  await t.waitPill("d0", "Running", 5000);
  const seen = [];
  for (const tab of ["apps", "logs", "issues", "crashes"]) {
    await t.click(`#tabs [data-tab="${tab}"]`);
    await sleep(400);
    const v = await t.vis(`#tabbody [data-pane="${tab}"]`);
    seen.push(`${tab}=${v > 0 ? "visible" : "HIDDEN"}`);
  }
  const errs = p.errors.length;
  return { pass: seen.every((s) => s.endsWith("visible")) && errs === 0, observed: `${seen.join(" ")} console_errors=${errs}` };
};

CHECKS["ui-16"] = async (t, open) => {
  const stamp = "20261014-101500";
  await t.fake({
    runs: [{ stamp, total: 2, passed: 1, failed: 1, wall_s: 120 }],
    run_detail: {
      [stamp]: {
        total_wall_s: 120, deal: { L4: "d3" },
        results: [
          { id: "L4-01-home", status: "FAIL", device: "d3", wall_s: 4.2, checks: [{ ok: false, detail: "ui home-screen: absent", check: { ui: "home-screen" } }] },
          { id: "L4-02-rewards", status: "PASS", device: "d3", wall_s: 3.1, checks: [{ ok: true, detail: "ui rewards-screen present", check: { ui: "rewards-screen" } }] },
        ],
      },
    },
  });
  const p = await open({});
  await gotoPage(t, p.page);
  await t.click("#v-runs");
  await sleep(500);
  const listed = await t.page.locator("#runlist button").count();
  await t.click("#runlist button >> nth=0");
  const detail = await t.waitVisText("#rundetail", /L4-01-home/, 8000);
  return { pass: listed >= 1 && /L4-01-home/.test(detail || ""), observed: `runs listed=${listed} detail has L4-01-home=${/L4-01-home/.test(detail || "")}` };
};

CHECKS["ui-17"] = async (t, open) => {
  const occ = (title) => ({ run: "20261014-101500", attempt: 1, phone: "d3", lane: "L4", case: "L4-01-home", failed_step: "", failed_check: { check: "ui:home-screen:present", detail: "home-screen absent" }, error: "", shots: [], build: {}, time: "2026-10-14T10:15:00Z", title });
  const bug = (key, title, count) => ({ key, sig: key, title, kind: "unsorted", status: "open", area: "home", step: "home", endpoint: "", check: "ui:home-screen:present", first_seen: Date.now() - 3600e3, last_seen: Date.now() - 60e3, count, cases: ["L4-01-home"], phones: ["d3"], runs: ["20261014-101500"], last: occ(title), occurrences: [occ(title)] });
  await t.fake({ bugs: [bug("b1", "Home never shows home-screen", 3), bug("b2", "Pin field drops digits", 1)] });
  const p = await open({});
  await gotoPage(t, p.page);
  await t.click("#v-bugs");
  await sleep(600);
  const rows = await t.page.locator("#bugrows .bug").count();
  await t.click("#bugrows .bug >> nth=0");
  const detail = await t.waitVisText("#bugdetail", /home-screen/, 8000);
  return { pass: rows === 2 && /home-screen/.test(detail || ""), observed: `rows=${rows} detail mentions home-screen=${/home-screen/.test(detail || "")}` };
};

CHECKS["ui-18"] = async (t, open) => {
  await t.fake({ available_mb: 7000, devices: { d0: { phase: "ready", ready: true, own_mb: 620, shared_mb: 180 } } });
  const p = await open({ n: 2 });
  await gotoPage(t, p.page);
  await sleep(1200);
  await t.click("#membtn");
  await sleep(300);
  const txt = await t.visText("#memin");
  const hasMb = /\d[\d.,]*\s*(MB|GB)/.test(txt || "");
  const hasPhone = /d0/.test(txt || "");
  return { pass: hasMb && hasPhone, observed: `memory popover mb=${hasMb} phone_d0=${hasPhone} text="${(txt || "").replace(/\s+/g, " ").slice(0, 160)}"` };
};

async function columns(t) {
  return t.page.evaluate(() => {
    const xs = [...document.querySelectorAll("#grid > article.tile")].filter((e) => e.getClientRects().length > 0).map((e) => Math.round(e.getBoundingClientRect().left));
    return { cols: new Set(xs).size, tiles: xs.length, overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth };
  });
}

CHECKS["ui-19"] = async (t, open) => {
  await t.fake({ devices: {} });
  const p = await open({ n: 4, viewport: { width: 520, height: 860 } });
  await gotoPage(t, p.page);
  await sleep(400);
  const c = await columns(t);
  return { pass: c.cols === 1 && c.overflow <= 0, observed: `520px: tiles=${c.tiles} columns=${c.cols} horizontal_overflow=${c.overflow}px` };
};

CHECKS["ui-20"] = async (t, open) => {
  await t.fake({ devices: {} });
  const p = await open({ n: 4, viewport: { width: 800, height: 860 } });
  await gotoPage(t, p.page);
  await sleep(400);
  const c = await columns(t);
  return { pass: c.cols === 2, observed: `800px: tiles=${c.tiles} columns=${c.cols}` };
};

CHECKS["ui-21"] = async (t, open) => {
  await t.fake({ devices: { d0: { phase: "ready", ready: true, health: { level: "ok", phase: "ready", signed_in: false } } } });
  const p = await open({ n: 2 });
  await gotoPage(t, p.page);
  const out = await t.waitVisText('#grid > article.tile[data-id="d0"] .sig', /Signed (in|out)/, 8000);
  await t.fake({});
  await t.dev({ d0: { health: { level: "ok", phase: "ready", signed_in: true } } });
  const out2 = await t.waitVisText('#grid > article.tile[data-id="d0"] .sig', /Signed in/, 8000);
  return { pass: out === "Signed out" && out2 === "Signed in", observed: `signed_in=false -> "${out}"; signed_in=true -> "${out2}"` };
};

CHECKS["ui-22"] = async (t, open) => {
  await t.fake({ devices: { d0: { phase: "ready", ready: true, ui_reader: "yielded_appium" } } });
  const p = await open({ n: 2 });
  await gotoPage(t, p.page);
  const txt = await t.waitVisText('#grid > article.tile[data-id="d0"] .sig.ax', /Appium in control/, 8000);
  return { pass: txt === "Appium in control", observed: `appium label="${txt}"` };
};

CHECKS["ui-23"] = async (t, open) => {
  await t.fake({
    devices: { d0: { phase: "ready", ready: true }, d1: { phase: "ready", ready: true } },
    bugs: [],
    runs: [{ stamp: "20261014-101500", total: 1, passed: 1, failed: 0, wall_s: 9 }],
    run_detail: { "20261014-101500": { results: [{ id: "L4-02", status: "PASS", checks: [{ ok: true, detail: "ok" }] }] } },
  });
  const p = await open({ n: 2, mine: ["d0", "d1"] });
  await gotoPage(t, p.page);
  await t.waitPill("d0", "Running", 5000);
  const steps = [];
  const go = async (name, fn) => { try { await fn(); steps.push(name + ":ok"); } catch (e) { steps.push(name + ":ERR " + e.message.split("\n")[0]); } };
  await go("tabs", async () => { for (const tab of ["logs", "issues", "crashes", "apps"]) { await t.click(`#tabs [data-tab="${tab}"]`); await sleep(250); } });
  await go("memory", async () => { await t.click("#membtn"); await sleep(250); await t.page.keyboard.press("Escape"); });
  await go("picker", async () => { for (const k of [1, 3, 4, 2]) { await t.click(`#nseg button >> nth=${k - 1}`); await sleep(200); } });
  await go("runs", async () => { await t.click("#v-runs"); await sleep(600); await t.click("#runlist button >> nth=0"); await sleep(400); });
  await go("bugs", async () => { await t.click("#v-bugs"); await sleep(400); });
  await go("phones", async () => { await t.click("#v-phones"); await sleep(300); });
  await go("sleep-all", async () => { await t.click("#stopall"); await t.waitCalls((cs) => cs.filter((x) => x.call === "sleep").length >= 2, 6000); });
  const errs = p.errors;
  return { pass: errs.length === 0 && steps.every((s) => s.endsWith(":ok")), observed: `${steps.join(" | ")} | console_errors=${errs.length}${errs.length ? " " + errs.slice(0, 3).join(" ;; ").slice(0, 400) : ""}` };
};

CHECKS["ui-24"] = async (t, open) => {
  await t.fake({ devices: { d0: { phase: "ready", ready: true }, d1: { phase: "ready", ready: true }, d2: { phase: "ready", ready: true } } });
  const p = await open({ n: 3, mine: ["d0", "d1", "d2"] });
  await gotoPage(t, p.page);
  await t.waitPill("d2", "Running", 5000);
  await t.click("#stopall");
  const pills = [];
  for (const id of ["d0", "d1", "d2"]) pills.push(await t.waitPill(id, "Sleeping", 10000));
  const sleeps = (await t.calls()).filter((x) => x.call === "sleep").map((x) => x.device).sort();
  return { pass: sleeps.join(",") === "d0,d1,d2" && pills.every((x) => x === "Sleeping"), observed: `sleep calls=${sleeps.join(",")} pills=${pills.join(",")}` };
};

CHECKS["ui-25"] = async (t, open) => {
  await t.fake({ devices: { d0: { phase: "ready", ready: true }, d1: { phase: "ready", ready: true }, d2: { phase: "ready", ready: true } } });
  const p = await open({ n: 3, mine: ["d0", "d1"] });
  await gotoPage(t, p.page);
  await t.waitPill("d2", "Running", 5000);
  await t.click("#nseg button >> nth=0");
  await sleep(800);
  const sleeps1 = (await t.calls()).filter((x) => x.call === "sleep").map((x) => x.device);
  const askText = await t.visText("#asktext");
  const asked = /d2/.test(askText || "") && /anyway/.test(askText || "");
  await t.click("#askok");
  const c = await t.waitCalls((cs) => cs.find((x) => x.call === "sleep" && x.device === "d2"), 6000);
  const sleeps2 = (await t.calls()).filter((x) => x.call === "sleep").map((x) => x.device).sort();
  return {
    pass: sleeps1.join(",") === "d1" && asked && !!c,
    observed: `before OK: sleep calls=${sleeps1.join(",")}; ask="${askText}"; after OK: sleep calls=${sleeps2.join(",")}`,
  };
};

// ---------- runner ----------

async function main() {
  const ids = process.argv.slice(2);
  const all = Object.keys(CHECKS);
  const want = ids.length ? ids : all;
  const bad = want.filter((i) => !CHECKS[i]);
  if (bad.length) { console.error("no ui check for: " + bad.join(", ")); process.exit(2); }

  const stamp = new Date().toISOString().replace(/[:.]/g, "").slice(0, 15);
  const evDir = path.join(RUNS, stamp);
  fs.mkdirSync(evDir, { recursive: true });

  const lanes = [];
  for (let i = 0; i < LANES; i++) {
    const port = await freePort();
    const child = startFake(port);
    await waitHttp(`http://127.0.0.1:${port}/__fake`);
    lanes.push({ port, child });
  }

  const browser = await chromium.launch({ headless: true });
  const results = [];
  const queue = [...want];

  async function runLane(lane) {
    while (queue.length) {
      const id = queue.shift();
      const t = makeT(lane);
      let page = null, ctx = null, errors = [];
      const opened = [];
      const open = async (o) => {
        const r = await openPage(lane, browser, o);
        ctx = r.ctx; page = r.page; errors = r.errors; t.page = page;
        opened.push(r);
        return r;
      };
      const res = { id, result: "fail", observed: "", evidence: "" };
      try {
        // Reset fake state for this check (calls, devices, faults).
        await fetch(t.base + "/__fake", { method: "POST", body: JSON.stringify({ reset: true }) });
        const out = await withTimeout(CHECKS[id](t, open), TIMEBOX_MS, id);
        res.result = out.pass ? "pass" : "fail";
        res.observed = out.observed;
      } catch (e) {
        res.result = "fail";
        res.observed = String(e.message || e).split("\n").map((l) => l.trim()).filter(Boolean).slice(0, 5).join(" | ").slice(0, 400);
      }
      if (page && res.result === "fail") {
        const shot = path.join(evDir, id + ".png");
        try { await page.screenshot({ path: shot, timeout: 5000 }); res.evidence = path.relative(MATRIX, shot).replace(/\\/g, "/"); } catch {}
      }
      res.console = errors.length;
      for (const r of opened) { try { await r.ctx.close(); } catch {} }
      results.push(res);
      console.log(`${res.result.toUpperCase().padEnd(5)} ${id}  ${res.observed.slice(0, 220)}`);
    }
  }

  await Promise.all(lanes.map(runLane));
  await browser.close();
  for (const l of lanes) l.child.kill();

  // Results file.
  results.sort((a, b) => a.id.localeCompare(b.id));
  const tsv = ["id\tresult\tobserved\tevidence\tconsole_errors"]
    .concat(results.map((r) => [r.id, r.result, r.observed.replace(/[\t\n]/g, " "), r.evidence, r.console].join("\t")));
  const tsvPath = path.join(RUNS, stamp + ".tsv");
  fs.writeFileSync(tsvPath, tsv.join("\n") + "\n");

  // Status column in features.tsv for the ids that ran.
  const ran = new Map(results.map((r) => [r.id, r.result]));
  const feat = fs.readFileSync(FEATURES, "utf8").split("\n");
  const out = feat.map((line) => {
    const cols = line.split("\t");
    if (ran.has(cols[0]) && cols.length >= 5) { cols[4] = ran.get(cols[0]); return cols.join("\t"); }
    return line;
  });
  fs.writeFileSync(FEATURES, out.join("\n"));

  const count = (r) => results.filter((x) => x.result === r).length;
  console.log(`\npass=${count("pass")} fail=${count("fail")} blocked=${count("blocked")}  results: ${path.relative(MATRIX, tsvPath)}`);
  process.exit(0);
}

main().catch((e) => { console.error(e); process.exit(1); });
