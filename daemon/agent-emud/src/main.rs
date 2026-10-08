// agent-emud: owns the Devices (one, or a fleet) and serves the computer-use API v1 as newline-delimited JSON on 127.0.0.1:7400.
//   agent-emud [serve] [--addr 127.0.0.1:7400]   daemon
//   agent-emud mcp     [--addr 127.0.0.1:7400]   MCP stdio server forwarding to the daemon
// Request:  {"id":1,"call":"tap","device":"d0","x":100,"y":200}
// Reply:    {"id":1,"ok":true,"ms":12,...} or {"id":1,"ok":false,"error":"..."}
mod controls;
mod device;
mod fast;
mod logs;
mod mcp;
mod squeeze;
mod ui;

use base64::Engine;
use device::{Cfg, Device, R};
use serde_json::{json, Value};
use std::sync::atomic::{AtomicU64, Ordering};
use std::collections::BTreeMap;
use std::sync::{Arc, Mutex as StdMutex};
use std::time::{Duration, Instant};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::TcpListener;
use tokio::sync::Mutex;

/// One running Device and what the daemon keeps for it.
struct Slot {
    dev: Arc<Device>,
    /// Squeeze defaults from `start` (lever B).
    opts: squeeze::Opts,
    /// Set by `start {auto_squeeze: true}`; the first successful `app launch` consumes it.
    auto_squeeze: bool,
    /// Connection holding the input lease.
    lease: Option<u64>,
    /// What `start` asked for, for `status` (image name, guest MB, vCPUs, net) and when.
    info: Value,
    started: Instant,
    /// "spawning" | "booting" | "android" | "setup" | "ready" (pushed as `device` events).
    phase: &'static str,
    /// Recent input -> first new frame times (ms), for the metrics p50.
    lat: std::collections::VecDeque<u64>,
    /// Frames sent on all /frames streams of this Device, and how many streams are open.
    sent: u64,
    streams: u32,
}

struct State {
    cfg: Cfg,
    devs: StdMutex<BTreeMap<String, Slot>>,
    lifecycle: Mutex<()>, // serializes starts and stops
    /// Set by `fleet_stop`; a running `fleet` checks it before each next boot.
    fleet_stop: std::sync::atomic::AtomicBool,
    /// Event bus for GET /events (ui.rs): device phases, jobs, metrics, crashes, errors.
    events: tokio::sync::broadcast::Sender<Value>,
    jobs: AtomicU64,
}

/// Push one event to every /events subscriber (dropped when nobody listens).
fn emit(st: &State, mut v: Value) {
    v["ts"] = json!(device::now_ms());
    let _ = st.events.send(v);
}

fn new_state(cfg: Cfg) -> State {
    State { cfg, devs: StdMutex::new(BTreeMap::new()), lifecycle: Mutex::new(()), fleet_stop: Default::default(),
        events: tokio::sync::broadcast::channel(1024).0, jobs: AtomicU64::new(1) }
}

impl State {
    fn dev(&self, id: &str) -> Option<Arc<Device>> {
        self.devs.lock().unwrap().get(id).map(|s| s.dev.clone())
    }
    /// Another connection holds this Device's lease.
    fn busy(&self, id: &str, conn: u64) -> bool {
        matches!(self.devs.lock().unwrap().get(id).and_then(|s| s.lease), Some(c) if c != conn)
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let addr = args.iter().position(|a| a == "--addr").and_then(|i| args.get(i + 1)).cloned().unwrap_or("127.0.0.1:7400".into());
    if args.get(1).map(String::as_str) == Some("mcp") {
        return mcp::run(&addr);
    }
    tokio::runtime::Runtime::new().unwrap().block_on(serve(addr));
}

async fn serve(addr: String) {
    let st = Arc::new(new_state(Cfg::from_env()));
    tokio::spawn(metrics(st.clone()));
    let ui_addr = std::env::var("AE_UI_ADDR").unwrap_or_else(|_| "127.0.0.1:7401".into());
    tokio::spawn(ui::serve(st.clone(), ui_addr));
    let l = TcpListener::bind(&addr).await.expect("bind");
    eprintln!("agent-emud listening on {addr}");
    let conns = AtomicU64::new(1);
    loop {
        tokio::select! {
            a = l.accept() => {
                let Ok((sock, _)) = a else { continue };
                let _ = sock.set_nodelay(true);
                let (st, conn) = (st.clone(), conns.fetch_add(1, Ordering::Relaxed));
                tokio::spawn(async move {
                    let (r, mut w) = sock.into_split();
                    let mut lines = BufReader::new(r).lines();
                    while let Ok(Some(line)) = lines.next_line().await {
                        let t0 = Instant::now();
                        let req: Value = serde_json::from_str(&line).unwrap_or(Value::Null);
                        if req["call"] == json!("logs") && req["follow"] == json!(true) {
                            if let Err(e) = follow_logs(&st, &req, &mut w).await {
                                let out = format!("{}\n", json!({"id": req["id"], "ok": false, "error": e}));
                                if w.write_all(out.as_bytes()).await.is_err() { break; }
                            }
                            continue;
                        }
                        let mut rep = match handle(&st, conn, &req).await {
                            Ok(v) => v,
                            Err(e) => json!({"ok": false, "error": e}),
                        };
                        rep["id"] = req["id"].clone();
                        rep["ms"] = json!(t0.elapsed().as_millis() as u64);
                        let mut out = rep.to_string();
                        out.push('\n');
                        if w.write_all(out.as_bytes()).await.is_err() { break; }
                    }
                    for s in st.devs.lock().unwrap().values_mut() {
                        if s.lease == Some(conn) { s.lease = None; }
                    }
                });
            }
            _ = tokio::signal::ctrl_c() => {
                stop_all(&st).await;
                return;
            }
        }
    }
}

enum Input {
    Tap(i64, i64),
    Swipe(i64, i64, i64, i64, i64),
    Key(String),
    Shell(String),
}

/// Fast path (virtio-input pipes) when open, else the guest console. Returns which one ran.
async fn inject(d: &Device, input: Input) -> R<&'static str> {
    let cmd = match (d.input.get(), input) {
        (Some(i), Input::Tap(x, y)) => return i.tap(x as i32, y as i32).await.map(|_| "fast"),
        (Some(i), Input::Swipe(x1, y1, x2, y2, ms)) => {
            return i.swipe((x1 as i32, y1 as i32), (x2 as i32, y2 as i32), ms as u64).await.map(|_| "fast")
        }
        (Some(i), Input::Key(k)) if fast::linux_key(&k).is_some() => return i.key(fast::linux_key(&k).unwrap()).await.map(|_| "fast"),
        (_, Input::Tap(x, y)) => format!("input tap {x} {y}"),
        (_, Input::Swipe(x1, y1, x2, y2, ms)) => format!("input swipe {x1} {y1} {x2} {y2} {ms}"),
        (_, Input::Key(k)) => format!("input keyevent {k}"),
        (_, Input::Shell(c)) => c,
    };
    let (o, code) = d.con.exec(&cmd, Duration::from_secs(30)).await?;
    if code != 0 {
        return Err(format!("{cmd} exit {code}: {o}"));
    }
    Ok("console")
}

fn arg_i(req: &Value, k: &str) -> R<i64> {
    req[k].as_f64().map(|v| v.round() as i64).ok_or(format!("missing number `{k}`"))
}

fn frame_json(f: &device::Frame, size: Option<(u32, u32)>) -> R<Value> {
    let t = Instant::now();
    let (jpg, w, h, dw, dh, scale) = device::encode(&f.rgb, size)?;
    Ok(json!({
        "jpeg": base64::engine::general_purpose::STANDARD.encode(&jpg), "bytes": jpg.len(), "width": w, "height": h,
        "device_width": dw, "device_height": dh, "scale": scale, "captured_ms": f.captured_ms,
        "generation": f.generation, "capture_ms": f.capture_ms, "age_ms": f.age_ms, "encode_ms": t.elapsed().as_millis() as u64,
    }))
}

/// Package (process name) or tag filter for `logs`. A package's current pids come from `pidof`; pids
/// of later starts are learned from the log itself.
async fn log_filter(d: &Device, req: &Value) -> logs::Filter {
    let name = req["filter"].as_str().filter(|s| !s.is_empty()).map(str::to_string);
    let mut pids = std::collections::HashSet::new();
    if let Some(n) = name.as_deref().filter(|n| n.chars().all(|c| c.is_ascii_alphanumeric() || "._:".contains(c))) {
        if let Ok((o, _)) = d.con.exec(&format!("pidof {n}"), Duration::from_secs(10)).await {
            pids.extend(o.split_whitespace().filter_map(|p| p.parse::<u32>().ok()));
        }
    }
    let min_level = req["level"].as_str().filter(|l| !l.is_empty()).map(logs::rank);
    logs::Filter { name, pids, since_ms: req["since"].as_u64(), year: logs::this_year(), min_level }
}

/// After the app's launch: hide system dialogs again, let the app settle for `o.settle_s` (the cold
/// start keeps paging in for tens of seconds), then squeeze with the Device's start options.
async fn auto_squeeze(cfg: &Cfg, d: &Device, o: squeeze::Opts) -> R<Value> {
    d.con.exec("settings put global hide_error_dialogs 1; am broadcast -a android.intent.action.CLOSE_SYSTEM_DIALOGS >/dev/null",
        Duration::from_secs(20)).await?;
    tokio::time::sleep(Duration::from_secs(o.settle_s)).await;
    let quiet = quiet_for(|| d.frame_seq(), Duration::from_secs(2), Duration::from_secs(10)).await;
    let pid = d.boot_pid().await.ok_or("Device has no boot process")?;
    let mut r = squeeze::squeeze(d, &cfg.crosvm(), pid, o).await?;
    r["screen_quiet"] = json!(quiet);
    Ok(r)
}

/// True once `seq` has not changed for `quiet`; false if it is still changing at `max`.
async fn quiet_for(seq: impl Fn() -> u64, quiet: Duration, max: Duration) -> bool {
    let end = Instant::now() + max;
    let (mut last, mut since) = (seq(), Instant::now());
    while Instant::now() < end {
        tokio::time::sleep(Duration::from_millis(100)).await;
        let s = seq();
        if s != last {
            (last, since) = (s, Instant::now());
        } else if since.elapsed() >= quiet {
            return true;
        }
    }
    false
}

fn ready_device(st: &State, devid: &str) -> R<Arc<Device>> {
    let d = st.dev(devid).ok_or(format!("no Device `{devid}`"))?;
    if !d.ready.load(Ordering::SeqCst) {
        return Err(format!("Device `{devid}` is still booting"));
    }
    Ok(d)
}

/// Boots Device `idx` with the `start` options in `req` and waits for boot + setup. The Device is
/// listed (as booting) while it boots, and removed again if the boot fails. Only the spawn is serialized,
/// so several Devices can boot at once; RAM of Devices still booting counts as taken for the floor check.
async fn start_device(st: &State, idx: u32, req: &Value) -> R<(Arc<Device>, f64)> {
    let id = format!("d{idx}");
    let t0 = Instant::now();
    let fail = |e: String| { emit(st, json!({"type": "error", "device": id, "call": "start", "error": e})); e };
    let mem = req["mem"].as_u64().map(|m| m.to_string());
    let net = req["net"].as_bool().unwrap_or(true);
    let cpus = req["cpus"].as_u64().map(|c| c.to_string());
    let image_name = req["image"].as_str().unwrap_or("");
    let shown_mem = mem.clone().or(device::image(image_name).map_err(fail)?.1.map(str::to_string)).unwrap_or(st.cfg.mem.clone());
    let screen = req["screen"].as_str().unwrap_or("");
    let (sw, sh, dpi) = device::screen(screen, device::is_phone(image_name)).map_err(fail)?;
    let info = json!({"image": if image_name.is_empty() { "slim3n (default)" } else { image_name }, "mem": shown_mem,
        "cpus": cpus.clone().unwrap_or(st.cfg.cpus.clone()), "net": net, "image_name": image_name,
        "screen": {"name": if screen.is_empty() { "iphone17promax" } else { screen }, "width": sw, "height": sh, "dpi": dpi}});
    let d = {
        let _g = st.lifecycle.lock().await;
        let booting: u64 = {
            let devs = st.devs.lock().unwrap();
            if devs.contains_key(&id) {
                return Err(fail(format!("Device `{id}` is already running")));
            }
            devs.values().filter(|s| !s.dev.ready.load(Ordering::SeqCst)).filter_map(|s| s.info["mem"].as_str()?.parse::<u64>().ok()).sum()
        };
        let avail = device::available_mb();
        if avail.saturating_sub(booting) < st.cfg.min_avail_mb {
            return Err(fail(format!("host Available {avail} MB minus {booting} MB for Devices still booting < {} MB; not booting {id}", st.cfg.min_avail_mb)));
        }
        // Other crosvm processes are fine once this daemon runs a Device itself (a fleet).
        let others_ok = !st.devs.lock().unwrap().is_empty();
        emit(st, json!({"type": "device", "device": id, "phase": "spawning", "t_s": 0.0, "info": info}));
        let d = Device::spawn(&st.cfg, idx, image_name, screen, mem.as_deref(), cpus.as_deref(), net, others_ok).await.map_err(fail)?;
        st.devs.lock().unwrap().insert(id.clone(), Slot { dev: d.clone(), opts: squeeze::Opts::from(req, squeeze::Opts::DEFAULT),
            auto_squeeze: req["auto_squeeze"] == json!(true), lease: None, info, started: Instant::now(), phase: "booting",
            lat: Default::default(), sent: 0, streams: 0 });
        d
    };
    let phase = |p: &'static str| {
        if let Some(s) = st.devs.lock().unwrap().get_mut(&id) { s.phase = p; }
        emit(st, json!({"type": "device", "device": id, "phase": p, "t_s": t0.elapsed().as_secs_f64()}));
    };
    phase("booting");
    if let Err(e) = d.wait_boot(Duration::from_secs(900), st.cfg.min_avail_mb, &phase).await {
        d.stop().await;
        st.devs.lock().unwrap().remove(&id);
        emit(st, json!({"type": "device", "device": id, "phase": "stopped"}));
        return Err(fail(e));
    }
    phase("ready");
    Ok((d, t0.elapsed().as_secs_f64()))
}

fn idx_of(id: &str) -> R<u32> {
    id.strip_prefix('d').and_then(|s| s.parse().ok()).ok_or(format!("device id must be d<N>, not `{id}`"))
}

/// `devices: ["d0", ...]` of a batch call; None when absent.
fn ids(req: &Value) -> R<Option<Vec<String>>> {
    let Some(a) = req["devices"].as_array() else { return Ok(None) };
    a.iter().map(|v| v.as_str().map(str::to_string).ok_or("`devices` must be a list of ids".to_string())).collect::<R<Vec<_>>>().map(Some)
}

/// `start_many`: boots `devices` with the same start options, at most `parallel` (default 4) at once.
async fn start_many(st: &Arc<State>, req: &Value) -> R<Value> {
    let list = ids(req)?.ok_or("missing `devices`")?;
    for id in &list {
        idx_of(id)?;
    }
    let sem = Arc::new(tokio::sync::Semaphore::new(req["parallel"].as_u64().unwrap_or(4).clamp(1, 8) as usize));
    let hs: Vec<_> = list.into_iter().map(|id| {
        let (st, req, sem) = (st.clone(), req.clone(), sem.clone());
        (id.clone(), tokio::spawn(async move {
            let _p = sem.acquire().await;
            start_device(&st, idx_of(&id)?, &req).await.map(|(_, s)| s)
        }))
    }).collect();
    let mut out = vec![];
    for (id, h) in hs {
        out.push(match h.await.map_err(|e| e.to_string()).and_then(|r| r) {
            Ok(s) => json!({"device": id, "ok": true, "ready_s": s}),
            Err(e) => json!({"device": id, "ok": false, "error": e}),
        });
    }
    Ok(json!({"ok": out.iter().all(|r| r["ok"] == json!(true)), "devices": out}))
}

async fn stop_one(st: &State, id: &str) -> R<()> {
    let d = st.dev(id).ok_or(format!("no Device `{id}`"))?;
    d.stop().await;
    st.devs.lock().unwrap().remove(id);
    emit(st, json!({"type": "device", "device": id, "phase": "stopped"}));
    Ok(())
}

fn p50(v: &std::collections::VecDeque<u64>) -> Option<u64> {
    let mut s: Vec<u64> = v.iter().copied().collect();
    s.sort_unstable();
    s.get(s.len() / 2).copied()
}

/// While anyone listens on /events: one `metrics` event a second (phase, scanout and sent fps, input p50,
/// RAM own/shared refreshed every 5 s), plus a `crash` event per new crash/ANR.
async fn metrics(st: Arc<State>) {
    let mut prev: std::collections::HashMap<String, (u64, u64, u64)> = Default::default();
    let mut mem: std::collections::HashMap<String, (u64, u64, u64)> = Default::default();
    let (mut iv, mut t, mut tick) = (tokio::time::interval(Duration::from_secs(1)), Instant::now(), 0u64);
    loop {
        iv.tick().await;
        let dt = t.elapsed().as_secs_f64().max(0.001);
        t = Instant::now();
        if st.events.receiver_count() == 0 {
            prev.clear();
            continue;
        }
        tick += 1;
        let devs: Vec<(String, Arc<Device>)> = st.devs.lock().unwrap().iter().map(|(k, s)| (k.clone(), s.dev.clone())).collect();
        if tick % 5 == 1 {
            let mut pids = vec![];
            for (id, d) in &devs {
                if let Some(p) = d.boot_pid().await {
                    pids.push((id.clone(), p));
                }
            }
            if let Ok(rows) = squeeze::rows().await {
                mem = tokio::task::spawn_blocking(move || pids.into_iter()
                    .filter_map(|(id, p)| Some((id, squeeze::mem_of(&rows, p)?))).collect()).await.unwrap_or_default();
            }
        }
        let mut rows = vec![];
        for (id, d) in &devs {
            let seq = d.frame_seq();
            // First sight of a Device: start after its current events (`crash_events` has the history).
            let after = match prev.get(id) { Some(p) => p.2, None => d.events.after(u64::MAX, None).1 };
            let (ev, ev_last) = d.events.after(after, None);
            let Some(s) = st.devs.lock().unwrap().get(id).map(|s| (s.phase, s.sent, s.streams, p50(&s.lat), s.lat.len(), s.started.elapsed().as_secs())) else { continue };
            let (sc, se) = prev.get(id).map_or((0.0, 0.0), |p| (seq.saturating_sub(p.0) as f64 / dt, s.1.saturating_sub(p.1) as f64 / dt));
            for e in ev {
                emit(&st, json!({"type": "crash", "device": id, "event": e}));
            }
            prev.insert(id.clone(), (seq, s.1, ev_last));
            let m = mem.get(id);
            rows.push(json!({"id": id, "phase": s.0, "ready": s.0 == "ready", "uptime_s": s.5, "scanout_fps": (sc * 10.0).round() / 10.0,
                "sent_fps": (se * 10.0).round() / 10.0, "streams": s.2, "input_p50_ms": s.3, "input_n": s.4,
                "ws_mb": m.map(|m| m.0), "own_mb": m.map(|m| m.1), "shared_mb": m.map(|m| m.2)}));
        }
        prev.retain(|k, _| devs.iter().any(|(id, _)| id == k));
        emit(&st, json!({"type": "metrics", "available_mb": device::available_mb(), "devices": rows}));
    }
}

async fn stop_all(st: &State) -> Vec<String> {
    let _g = st.lifecycle.lock().await;
    let all: Vec<(String, Arc<Device>)> = std::mem::take(&mut *st.devs.lock().unwrap()).into_iter().map(|(k, s)| (k, s.dev)).collect();
    for (_, d) in &all {
        d.stop().await;
    }
    all.into_iter().map(|(k, _)| k).collect()
}

const DEFAULT_APP: &str = "com.boltbetz.staging";

/// Sum of the per-Device working sets in a `fleet` reply (after squeeze when it ran).
fn fleet_ws(rows: &[Value]) -> u64 {
    rows.iter().filter_map(|r| r["ws_after_squeeze_mb"].as_u64().or(r["ws_mb"].as_u64())).sum()
}

/// Why the fleet must not boot Device `idx` now: `fleet_stop` was called, or host memory is low.
fn next_boot_blocked(st: &State, avail_mb: u64, idx: u32) -> Option<String> {
    if st.fleet_stop.load(Ordering::SeqCst) {
        return Some(format!("fleet_stop called; d{idx} and later were not booted"));
    }
    (avail_mb < st.cfg.min_avail_mb).then(|| format!("host Available {avail_mb} MB < {} MB before d{idx}; not booting it", st.cfg.min_avail_mb))
}

/// `fleet`: boots `n` Devices one after another (ids base..base+n), each to its app's first screen
/// (install from the image's apk.img over the console, `app launch`, screen quiet), then squeezes it
/// when auto_squeeze is on (default). The next boot is refused once host Available is under the
/// floor; a failed Device ends the run. Devices already up stay up (`fleet_stop` stops them all).
async fn fleet(st: &State, req: &Value) -> R<Value> {
    let base = req["base"].as_u64().unwrap_or(0) as u32;
    let n = req["n"].as_u64().ok_or("missing number `n`")? as u32;
    let app = req["app"].as_str().unwrap_or(DEFAULT_APP);
    if app.is_empty() || !app.chars().all(|c| c.is_ascii_alphanumeric() || "._".contains(c)) {
        return Err(format!("bad app package `{app}`"));
    }
    let auto = req["auto_squeeze"].as_bool().unwrap_or(true);
    let (run, _) = device::image(req["image"].as_str().unwrap_or(""))?;
    let apk_size: u64 = std::fs::read_to_string(st.cfg.work.join(run).join("apk.size")).ok()
        .and_then(|s| s.trim().parse().ok()).ok_or(format!("{run}/apk.size missing"))?;
    let install = format!("head -c {apk_size} /dev/block/vdb > /data/local/tmp/p.apk && chmod 644 /data/local/tmp/p.apk && \
        pm install -r /data/local/tmp/p.apk; rm -f /data/local/tmp/p.apk");
    let (t0, avail_before) = (Instant::now(), device::available_mb());
    st.fleet_stop.store(false, Ordering::SeqCst);
    let (mut rows, mut stopped) = (vec![], Value::Null);
    for idx in base..base + n {
        let avail = device::available_mb();
        if let Some(why) = next_boot_blocked(st, avail, idx) {
            stopped = json!(why);
            break;
        }
        let mut row = json!({"id": format!("d{idx}"), "available_mb_before": avail});
        let r: R<()> = async {
            let (d, ready_s) = start_device(st, idx, req).await?;
            row["ready_s"] = json!(ready_s);
            let (o, _) = d.con.exec(&install, Duration::from_secs(300)).await?;
            if !o.contains("Success") {
                return Err(format!("install: {o}"));
            }
            row["launch"] = controls::handle(&d, "app", &json!({"launch": app})).await?["out"].clone();
            // Squeezed here, so a later `app launch` must not squeeze again.
            let o = st.devs.lock().unwrap().get_mut(&d.id).map(|s| { s.auto_squeeze = false; s.opts }).unwrap_or(squeeze::Opts::DEFAULT);
            if auto {
                let sq = auto_squeeze(&st.cfg, &d, o).await?;
                row["ws_before_squeeze_mb"] = sq["ws_before_mb"].clone();
                row["ws_after_squeeze_mb"] = sq["ws_after_mb"].clone();
                row["balloon"] = sq["balloon"].clone();
            } else {
                quiet_for(|| d.frame_seq(), Duration::from_secs(2), Duration::from_secs(30)).await;
                let pid = d.boot_pid().await.ok_or("Device has no boot process")?;
                row["ws_mb"] = squeeze::memory(pid).await?["ws_mb"].clone();
            }
            row["app_pid"] = json!(d.con.exec(&format!("pidof {app}"), Duration::from_secs(10)).await?.0);
            Ok(())
        }.await;
        let failed = r.err();
        if let Some(e) = &failed {
            row["error"] = json!(e);
        }
        rows.push(row);
        if failed.is_some() {
            stopped = json!("a Device failed; later Devices were not booted");
            break;
        }
    }
    let up = rows.iter().filter(|r| r["error"].is_null()).count();
    Ok(json!({"ok": true, "devices": rows, "stopped": stopped, "total": {"requested": n, "up": up, "ws_mb": fleet_ws(&rows),
        "available_mb_before": avail_before, "available_mb_after": device::available_mb(), "elapsed_s": t0.elapsed().as_secs_f64()}}))
}

/// `logs` with follow=true: one reply per batch of new lines (`more: true`) until `duration_ms`
/// passes (then a last reply with `more: false`), the Device stops, or the client hangs up.
async fn follow_logs(st: &State, req: &Value, w: &mut (impl AsyncWriteExt + Unpin)) -> R<()> {
    let d = ready_device(st, req["device"].as_str().unwrap_or("d0"))?;
    let mut f = log_filter(&d, req).await;
    let path = d.dir.join("logcat.log");
    let max = req["max_lines"].as_u64().unwrap_or(500) as usize;
    let end = req["duration_ms"].as_u64().map(|ms| Instant::now() + Duration::from_millis(ms));
    let mut cursor = req["cursor"].as_u64();
    loop {
        let (lines, cur) = logs::read(&path, cursor, &mut f, max)?;
        cursor = Some(cur);
        let done = end.is_some_and(|e| Instant::now() >= e) || !d.ready.load(Ordering::SeqCst);
        if !lines.is_empty() || done {
            let out = format!("{}\n", json!({"id": req["id"], "ok": true, "lines": lines, "cursor": cur, "more": !done}));
            w.write_all(out.as_bytes()).await.map_err(|e| e.to_string())?;
        }
        if done {
            return Ok(());
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
}

async fn handle(st: &Arc<State>, conn: u64, req: &Value) -> R<Value> {
    let call = req["call"].as_str().ok_or("missing `call`")?;
    let devid = req["device"].as_str().unwrap_or("d0");
    match call {
        "start_many" => return start_many(st, req).await,
        "stop_many" => {
            // `devices` (default: every Device), stopped at once.
            let list = ids(req)?.unwrap_or_else(|| st.devs.lock().unwrap().keys().cloned().collect());
            let mut out = vec![];
            for id in list {
                let st = st.clone();
                out.push((id.clone(), tokio::spawn(async move { stop_one(&st, &id).await })));
            }
            let mut rows = vec![];
            for (id, h) in out {
                rows.push(match h.await.map_err(|e| e.to_string()).and_then(|r| r) {
                    Ok(()) => json!({"device": id, "ok": true}),
                    Err(e) => json!({"device": id, "ok": false, "error": e}),
                });
            }
            return Ok(json!({"ok": rows.iter().all(|r| r["ok"] == json!(true)), "devices": rows}));
        }
        "start" => {
            let idx = idx_of(devid)?;
            let avail = device::available_mb();
            let (d, ready_s) = start_device(st, idx, req).await?;
            return Ok(json!({"ok": true, "device": d.id, "ready_s": ready_s, "available_mb_before": avail, "dir": d.dir}));
        }
        "fleet" => return fleet(st, req).await,
        "fleet_stop" => {
            // Raise the flag first: a running fleet must not boot another Device after this stop.
            st.fleet_stop.store(true, Ordering::SeqCst);
            return Ok(json!({"ok": true, "stopped": stop_all(st).await}));
        }
        "status" => {
            let devs: Vec<Value> = st.devs.lock().unwrap().values().map(|s| {
                let mut v = json!({"id": s.dev.id, "ready": s.dev.ready.load(Ordering::SeqCst), "phase": s.phase, "streams": s.streams, "frames_via": s.dev.transport().0,
                    "input_via": s.dev.transport().1, "lease": s.lease == Some(conn), "uptime_s": s.started.elapsed().as_secs()});
                for (k, x) in s.info.as_object().into_iter().flatten() { v[k] = x.clone(); }
                v
            }).collect();
            return Ok(json!({"ok": true, "devices": devs, "available_mb": device::available_mb()}));
        }
        _ => {}
    }
    let d = st.dev(devid).ok_or(format!("no Device `{devid}`"))?;
    if call == "stop" {
        drop(d);
        stop_one(st, devid).await?;
        return Ok(json!({"ok": true}));
    }
    if !d.ready.load(Ordering::SeqCst) {
        return Err(format!("Device `{devid}` is still booting"));
    }
    if controls::CALLS.contains(&call) {
        if st.busy(devid, conn) {
            return Err("busy: Device is leased by another client".into());
        }
        let mut rep = controls::handle(&d, call, req).await?;
        let armed = if call == "app" && req["launch"].is_string() {
            st.devs.lock().unwrap().get_mut(devid).filter(|s| s.auto_squeeze).map(|s| { s.auto_squeeze = false; s.opts })
        } else {
            None
        };
        if let Some(o) = armed {
            rep["auto_squeeze"] = auto_squeeze(&st.cfg, &d, o).await.unwrap_or_else(|e| json!({"ok": false, "error": e}));
        }
        return Ok(rep);
    }
    let input = match call {
        "screenshot" => {
            let size = req["size"].as_str().and_then(device::parse_size);
            let f = d.capture_via(req["via"] == json!("console")).await?;
            let fj = frame_json(&f, size)?;
            *d.scale.lock().unwrap() = fj["scale"].as_f64().unwrap_or(1.0);
            return Ok(json!({"ok": true, "frame": fj}));
        }
        "ui_tree" => {
            // The console shell starts before apexd: it sits in the bootstrap mount namespace and lacks the
            // *CLASSPATH vars, so app_process (uiautomator) can't run there. Borrow both from system_server.
            const UI: &str = "p=$(pidof system_server); for v in $(tr '\\0' '\\n' < /proc/$p/environ | grep CLASSPATH=); \
                do export \"$v\"; done; nsenter -m -t $p -- sh -c 'uiautomator dump /data/local/tmp/ui.xml >/dev/null && \
                cat /data/local/tmp/ui.xml'";
            let (o, code) = d.con.exec(UI, Duration::from_secs(30)).await?;
            if code != 0 {
                return Err(format!("uiautomator exit {code}: {o}"));
            }
            return Ok(json!({"ok": true, "xml": o}));
        }
        "logs" => {
            // One-shot read; with wait_ms it is a long poll that returns once new lines arrive.
            let mut f = log_filter(&d, req).await;
            let path = d.dir.join("logcat.log");
            let max = req["max_lines"].as_u64().unwrap_or(500) as usize;
            let end = Instant::now() + Duration::from_millis(req["wait_ms"].as_u64().unwrap_or(0));
            let mut cursor = req["cursor"].as_u64();
            loop {
                let (lines, cur) = logs::read(&path, cursor, &mut f, max)?;
                if !lines.is_empty() || Instant::now() >= end {
                    return Ok(json!({"ok": true, "lines": lines, "cursor": cur}));
                }
                cursor = Some(cur);
                tokio::time::sleep(Duration::from_millis(100)).await;
            }
        }
        "issues" => {
            // E/F lines grouped and counted, split into the `filter` package's ("app") and the rest ("system").
            let mut f = log_filter(&d, req).await;
            let top = req["top"].as_u64().unwrap_or(40) as usize;
            return Ok(json!({"ok": true, "issues": logs::issues(&d.dir.join("logcat.log"), &mut f, top)?}));
        }
        "squeeze" | "memory" => {
            let pid = d.boot_pid().await.ok_or("Device has no boot process")?;
            if call == "memory" {
                return squeeze::memory(pid).await;
            }
            if st.busy(devid, conn) {
                return Err("busy: Device is leased by another client".into());
            }
            let base = st.devs.lock().unwrap().get(devid).map(|s| s.opts).unwrap_or(squeeze::Opts::DEFAULT);
            let o = squeeze::Opts::from(req, base);
            return squeeze::squeeze(&d, &st.cfg.crosvm(), pid, o).await;
        }
        "install_bundled" => {
            // The image's own APK (apk.img on /dev/block/vdb, size in <run>/apk.size), over the console: no adb needed.
            let name = st.devs.lock().unwrap().get(devid).and_then(|s| s.info["image_name"].as_str().map(str::to_string)).unwrap_or_default();
            let run = device::image(&name)?.0;
            let size: u64 = std::fs::read_to_string(st.cfg.work.join(run).join("apk.size")).ok()
                .and_then(|s| s.trim().parse().ok()).ok_or(format!("{run}/apk.size missing"))?;
            let (o, _) = d.con.exec(&format!("head -c {size} /dev/block/vdb > /data/local/tmp/p.apk && chmod 644 /data/local/tmp/p.apk &&                 pm install -r /data/local/tmp/p.apk; rm -f /data/local/tmp/p.apk"), Duration::from_secs(300)).await?;
            if !o.contains("Success") {
                return Err(format!("install: {o}"));
            }
            return Ok(json!({"ok": true, "out": o.trim()}));
        }
        "crash_events" => {
            // Long poll: events after seq `after`, waiting up to wait_ms for the first one.
            let after = req["after"].as_u64().unwrap_or(0);
            let end = tokio::time::Instant::now() + Duration::from_millis(req["wait_ms"].as_u64().unwrap_or(0));
            loop {
                let notified = d.events.notify.notified();
                let (events, last) = d.events.after(after, req["filter"].as_str().filter(|s| !s.is_empty()));
                if !events.is_empty() || tokio::time::Instant::now() >= end {
                    return Ok(json!({"ok": true, "events": events, "last": last}));
                }
                let _ = tokio::time::timeout_at(end, notified).await;
            }
        }
        "shell" => {
            let secs = req["timeout_s"].as_u64().unwrap_or(120);
            let (o, code) = d.con.exec(req["cmd"].as_str().ok_or("missing `cmd`")?, Duration::from_secs(secs)).await?;
            return Ok(json!({"ok": true, "out": o, "code": code}));
        }
        "lease" | "release" => {
            let mut devs = st.devs.lock().unwrap();
            let s = devs.get_mut(devid).ok_or(format!("no Device `{devid}`"))?;
            return match (call, s.lease) {
                ("lease", Some(c)) if c != conn => Err("busy: Device is leased by another client".into()),
                ("lease", _) => { s.lease = Some(conn); Ok(json!({"ok": true})) }
                (_, l) => { if l == Some(conn) { s.lease = None; } Ok(json!({"ok": true})) }
            };
        }
        "tap" | "swipe" => {
            // device_px: coordinates are device pixels (the panel), not pixels of a scaled screenshot.
            let s = if req["device_px"] == json!(true) { 1.0 } else { *d.scale.lock().unwrap() };
            let p = |k: &str| arg_i(req, k).map(|v| (v as f64 * s).round() as i64);
            if call == "tap" {
                Input::Tap(p("x")?, p("y")?)
            } else {
                Input::Swipe(p("x1")?, p("y1")?, p("x2")?, p("y2")?, arg_i(req, "ms").unwrap_or(300).max(0))
            }
        }
        "type_text" => Input::Shell(device::input_text_cmd(req["text"].as_str().ok_or("missing `text`")?)),
        "key" => Input::Key(device::keycode(req["name"].as_str().ok_or("missing `name`")?)?),
        _ => return Err(format!("unknown call `{call}`")),
    };
    if st.busy(devid, conn) {
        return Err("busy: Device is leased by another client".into());
    }
    let s0 = d.frame_seq();
    let t = Instant::now();
    let deadline = Duration::from_millis(req["deadline_ms"].as_u64().unwrap_or(3000));
    let watch = if req["screenshot"] == json!(false) { None } else { d.first_frame_watch(s0, t, deadline) };
    let via = inject(&d, input).await?;
    let input_ms = t.elapsed().as_millis() as u64;
    if req["screenshot"] == json!(false) {
        return Ok(json!({"ok": true, "input_ms": input_ms, "input_via": via}));
    }
    let quiet = Duration::from_millis(req["settle_ms"].as_u64().unwrap_or(33));
    let (f, settled, frames, mut first) = d.settled(s0, t, quiet, deadline).await?;
    if let Some(w) = watch {
        // settled() starts looking only after the input is fully sent; the watcher saw frames sooner.
        first = w.await.map_err(|e| e.to_string())?.or(first);
    }
    if let Some(ms) = first {
        if let Some(s) = st.devs.lock().unwrap().get_mut(devid) {
            s.lat.push_back(ms);
            if s.lat.len() > 50 { s.lat.pop_front(); }
        }
    }
    let size = req["size"].as_str().and_then(device::parse_size);
    let mut rep = json!({"ok": true, "input_ms": input_ms, "input_via": via, "settled": settled, "frames": frames,
        "first_frame_ms": first, "settled_ms": t.elapsed().as_millis() as u64, "frame": frame_json(&f, size)?});
    if !settled {
        rep["reason"] = json!(if first.is_none() && via == "fast" { "no_frame" } else { "frames_changing" });
    }
    Ok(rep)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn state() -> Arc<State> {
        Arc::new(new_state(Cfg { work: "W".into(), mem: "896".into(), cpus: "2".into(), min_avail_mb: 4000, crosvm_dir: "crosvm-pmem".into() }))
    }

    #[tokio::test]
    async fn batch_calls_check_ids_and_emit() {
        let st = state();
        let mut rx = st.events.subscribe();
        assert!(handle(&st, 1, &json!({"call": "start_many"})).await.unwrap_err().contains("`devices`"));
        assert!(handle(&st, 1, &json!({"call": "start_many", "devices": ["d0", "x1"]})).await.unwrap_err().contains("d<N>"));
        let r = handle(&st, 1, &json!({"call": "start_many", "devices": ["d3"], "image": "slim9"})).await.unwrap();
        assert_eq!((r["ok"].clone(), r["devices"][0]["error"].as_str().map(|e| e.contains("unknown image"))), (json!(false), Some(true)));
        let e = rx.recv().await.unwrap();
        assert_eq!((e["type"].as_str(), e["device"].as_str()), (Some("error"), Some("d3")));
        assert_eq!(handle(&st, 1, &json!({"call": "stop_many"})).await.unwrap()["devices"], json!([]));
        assert_eq!(handle(&st, 1, &json!({"call": "stop_many", "devices": ["d5"]})).await.unwrap()["devices"][0]["ok"], json!(false));
        assert_eq!(p50(&[30, 10, 20].into_iter().collect()), Some(20));
    }

    #[tokio::test]
    async fn fleet_checks_args_before_booting() {
        let st = state();
        assert!(fleet(&st, &json!({})).await.unwrap_err().contains("`n`"));
        assert!(fleet(&st, &json!({"n": 2, "app": "x; reboot"})).await.unwrap_err().contains("bad app"));
        assert!(fleet(&st, &json!({"n": 2, "image": "slim9"})).await.unwrap_err().contains("unknown image"));
        assert!(fleet(&st, &json!({"n": 2, "image": "slim5"})).await.unwrap_err().contains("apk.size"), "W/run-slim5 does not exist");
        assert_eq!(stop_all(&st).await, Vec::<String>::new());
    }

    #[tokio::test]
    async fn fleet_stop_blocks_the_next_boot() {
        let st = state();
        assert_eq!(next_boot_blocked(&st, 8000, 3), None);
        assert!(next_boot_blocked(&st, 3999, 3).unwrap().contains("< 4000 MB before d3"));
        let r = handle(&st, 1, &json!({"call": "fleet_stop"})).await.unwrap();
        assert_eq!(r["stopped"], json!([]));
        assert!(next_boot_blocked(&st, 8000, 3).unwrap().contains("fleet_stop"));
    }

    #[test]
    fn fleet_total_uses_squeezed_ws() {
        let rows = [json!({"ws_after_squeeze_mb": 330, "ws_mb": 999}), json!({"ws_mb": 400}), json!({"error": "boot timeout"})];
        assert_eq!(fleet_ws(&rows), 730);
    }

    #[tokio::test]
    async fn quiet_waits_for_no_new_frames() {
        assert!(quiet_for(|| 7, Duration::from_millis(200), Duration::from_millis(1000)).await);
        let t = Instant::now();
        let busy = move || t.elapsed().as_millis() as u64 / 50; // a new frame every 50 ms
        assert!(!quiet_for(busy, Duration::from_millis(200), Duration::from_millis(600)).await);
    }
}
