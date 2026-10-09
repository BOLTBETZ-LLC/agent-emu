// Human control panel: a small HTTP/1.1 server on 127.0.0.1:7401 (AE_UI_ADDR) serving one embedded page.
//   GET  /                         the page (ui.html, compiled in)
//   GET  /health                   "ok" (the launcher checks it)
//   GET  /reticle.local.js         Reticle pairing token from ~/.reticle/pairing-token (404 without one); the page's
//                                  dev-only snippet connects to the Reticle bridge (:4400) only when it gets one
//   POST /api                      JSON request -> the same handler as the binary API ({"call": ...});
//                                  with "async": true -> 202 {"job": "j1"} at once, the result as a `job` event
//   GET  /events                   Server-Sent Events: device phases, jobs, metrics (1/s), crashes, errors
//   GET  /frames?device=d0         frame stream: [u32 jpeg_len][u64 seq][u32 w][u32 h][jpeg] per frame, LE;
//                                  max_width= | scale=, fps=, quality=, format=mjpeg (multipart, for <img>)
//   GET  /mux?devices=d0,d3&max_width=360&full=d3
//                                  many Devices on one connection (a browser opens at most 6 per host):
//                                  [u32 jpeg_len][u64 seq][u32 w][u32 h][u32 device n][jpeg], LE; `full` goes unscaled
//   GET  /screenshot.png?device=d0 one PNG download
//   POST /upload?device=d0         body = APK, installed with adb
//   GET  /runs                     test runner results, newest first (tests/boltbetz/results, plus the old .scratch runner folder)
//   GET  /runs/<stamp>/<file>      one file of a run (results.json, grid.md, *.png), read-only, that folder only
// Localhost only; no auth (the panel can do what the binary API can). See daemon/API.md.
use crate::device::{self, R};
use crate::State;
use serde_json::{json, Value};
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};

const PAGE: &str = include_str!("ui.html");

/// One encoded frame shared by every subscriber of an output: the JPEG and the Device's size.
pub struct Enc {
    pub seq: u64,
    pub jpeg: Vec<u8>,
    pub w: u32,
    pub h: u32,
}

/// What a stream asks for: (Device id, box to fit the frame into, JPEG quality). One encoder per key.
type HubKey = (String, Option<(u32, u32)>, u8);
static HUBS: std::sync::Mutex<Option<std::collections::HashMap<HubKey, tokio::sync::watch::Sender<Option<Arc<Enc>>>>>> =
    std::sync::Mutex::new(None);

/// A receiver of `d`'s frames fitted into `fit` at quality `q`. The first subscriber starts one encoder thread
/// for that output; it reads each new scanout frame once (a high-resolution sleep poll on the seq: tokio timers
/// tick at ~15.6 ms on Windows), scales (2x2 box straight from the raw pixels for half size and below),
/// encodes, and publishes it latest-wins, so no subscriber can build a backlog. Every 2 s with no new frame
/// it refreshes and republishes. It ends when the last subscriber or the Device goes. None without the fast path.
fn hub(d: &Arc<crate::device::Device>, fit: Option<(u32, u32)>, q: u8) -> Option<tokio::sync::watch::Receiver<Option<Arc<Enc>>>> {
    let fb = d.fb.get()?.clone();
    let key: HubKey = (d.id.clone(), fit, q);
    let mut hubs = HUBS.lock().unwrap();
    let map = hubs.get_or_insert_with(Default::default);
    if let Some(tx) = map.get(&key) {
        return Some(tx.subscribe());
    }
    let (tx, rx) = tokio::sync::watch::channel(None);
    map.insert(key.clone(), tx.clone());
    let d = d.clone();
    std::thread::spawn(move || {
        let (mut last, mut sent) = (u64::MAX, Instant::now() - Duration::from_secs(10));
        loop {
            {
                let mut hubs = HUBS.lock().unwrap();
                if tx.receiver_count() == 0 || !d.ready.load(Ordering::SeqCst) {
                    hubs.get_or_insert_with(Default::default).remove(&key);
                    return;
                }
            }
            let s = fb.seq();
            if s == last && sent.elapsed() < Duration::from_secs(2) {
                std::thread::sleep(Duration::from_micros(500));
                continue;
            }
            if s == last {
                let _ = fb.refresh();
            }
            let Some((e, seq)) = encode_latest(&fb, fit, q) else {
                std::thread::sleep(Duration::from_millis(50));
                continue;
            };
            tx.send_replace(Some(Arc::new(e)));
            (last, sent) = (seq, Instant::now());
        }
    });
    Some(rx)
}

/// The latest scanout frame as JPEG fitted into `fit`, and its seq. Half size and below read a 2x2 box
/// downscale straight from the mapping (no full-size copy).
fn encode_latest(fb: &crate::fast::Fb, fit: Option<(u32, u32)>, q: u8) -> Option<(Enc, u64)> {
    let (w, h) = fb.dims();
    let r = fit.map_or(1.0, |(fw, fh)| (fw as f64 / w.max(1) as f64).min(fh as f64 / h.max(1) as f64));
    if r > 0.5 {
        let p = fb.read_px()?;
        return encode_px(&p, fit, q).ok().map(|e| (e, p.seq));
    }
    let (rgb, hw, hh, w, h, seq) = fb.read_half()?;
    let half = image::RgbImage::from_raw(hw, hh, rgb)?;
    let (tw, th) = (((w as f64 * r).round() as u32).max(1), ((h as f64 * r).round() as u32).max(1));
    let img = if tw < hw { image::imageops::thumbnail(&half, tw, th) } else { half };
    let (jpeg, ..) = device::encode_q(&img, None, q).ok()?;
    Some((Enc { seq, jpeg, w, h }, seq))
}

/// Raw scanout pixels -> JPEG fitted into `fit` (aspect kept). Half size and below start from a 2x2 box
/// downscale of the raw pixels, which skips the full-size RGB pass.
fn encode_px(p: &crate::fast::RawPx, fit: Option<(u32, u32)>, q: u8) -> R<Enc> {
    let (w, h) = (p.w, p.h);
    let r = fit.map_or(1.0, |(fw, fh)| (fw as f64 / w as f64).min(fh as f64 / h as f64)).min(1.0);
    let img = if r <= 0.5 {
        let half = image::RgbImage::from_raw(w / 2, h / 2, crate::fast::half_rgb(&p.px, w as usize, h as usize, p.stride, p.red_first))
            .ok_or("half frame")?;
        let (tw, th) = (((w as f64 * r).round() as u32).max(1), ((h as f64 * r).round() as u32).max(1));
        if tw < half.width() { image::imageops::thumbnail(&half, tw, th) } else { half }
    } else {
        image::RgbImage::from_raw(w, h, crate::fast::to_rgb(&p.px, w as usize, p.stride, p.red_first)).ok_or("frame")?
    };
    let fit = (r < 1.0 && r > 0.5).then(|| (((w as f64 * r).round() as u32).max(1), ((h as f64 * r).round() as u32).max(1)));
    let (jpeg, ..) = device::encode_q(&img, fit, q)?;
    Ok(Enc { seq: p.seq, jpeg, w, h })
}
/// Lease owner for every panel request: the panel is one client.
const UI_CONN: u64 = u64::MAX - 1;
const MAX_UPLOAD: usize = 512 << 20;

pub async fn serve(st: Arc<State>, addr: String) {
    let l = match TcpListener::bind(&addr).await {
        Ok(l) => l,
        Err(e) => return eprintln!("control panel: bind {addr}: {e}"),
    };
    eprintln!("control panel on http://{addr}/");
    while let Ok((sock, _)) = l.accept().await {
        let st = st.clone();
        tokio::spawn(async move {
            let _ = sock.set_nodelay(true);
            if let Err(e) = conn(&st, sock).await {
                eprintln!("panel: {e}");
            }
        });
    }
}

pub struct Req {
    pub method: String,
    pub path: String,
    pub query: Vec<(String, String)>,
    pub len: usize,
    pub origin: Option<String>,
}

impl Req {
    pub fn q(&self, k: &str) -> Option<&str> {
        self.query.iter().find(|(a, _)| a == k).map(|(_, v)| v.as_str())
    }
}

/// Request line + headers (up to the blank line) -> method, path, query, Content-Length.
pub fn parse_head(head: &str) -> R<Req> {
    let mut lines = head.split("\r\n");
    let mut first = lines.next().unwrap_or("").split(' ');
    let (method, target) = (first.next().unwrap_or("").to_string(), first.next().ok_or("bad request line")?);
    let (path, qs) = target.split_once('?').unwrap_or((target, ""));
    let query = qs.split('&').filter(|p| !p.is_empty())
        .map(|p| { let (k, v) = p.split_once('=').unwrap_or((p, "")); (unescape(k), unescape(v)) }).collect();
    let heads: Vec<(&str, &str)> = lines.filter_map(|l| l.split_once(':')).collect();
    let h = |k: &str| heads.iter().find(|(n, _)| n.trim().eq_ignore_ascii_case(k)).map(|(_, v)| v.trim());
    let len = h("content-length").and_then(|v| v.parse().ok()).unwrap_or(0);
    Ok(Req { method, path: path.to_string(), query, len, origin: h("origin").map(str::to_string) })
}

/// CORS headers for a page served from another localhost port (a dashboard dev server); none otherwise.
pub fn cors(origin: Option<&str>) -> String {
    match origin {
        Some(o) if ["http://127.0.0.1", "http://localhost"].iter().any(|h| o == *h || o.strip_prefix(h).is_some_and(|r| r.starts_with(':'))) =>
            format!("Access-Control-Allow-Origin: {o}\r\nVary: Origin\r\n"),
        _ => String::new(),
    }
}

fn unescape(s: &str) -> String {
    let b = s.as_bytes();
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        match b[i] {
            b'+' => { out.push(b' '); i += 1; }
            b'%' if i + 2 < b.len() => {
                match u8::from_str_radix(std::str::from_utf8(&b[i + 1..i + 3]).unwrap_or("zz"), 16) {
                    Ok(v) => { out.push(v); i += 3; }
                    Err(_) => { out.push(b'%'); i += 1; }
                }
            }
            c => { out.push(c); i += 1; }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

async fn conn(st: &Arc<State>, mut sock: TcpStream) -> R<()> {
    let mut buf = Vec::with_capacity(4096);
    let mut chunk = [0u8; 4096];
    let end = loop {
        let n = sock.read(&mut chunk).await.map_err(|e| e.to_string())?;
        if n == 0 {
            return Ok(());
        }
        buf.extend_from_slice(&chunk[..n]);
        if let Some(i) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
            break i;
        }
        if buf.len() > 64 << 10 {
            return Err("headers too large".into());
        }
    };
    let req = parse_head(&String::from_utf8_lossy(&buf[..end]))?;
    let cors = cors(req.origin.as_deref());
    let c = cors.as_str();
    let mut body = buf[end + 4..].to_vec();
    if req.len > MAX_UPLOAD {
        return send(&mut sock, 413, "text/plain", b"too large", c).await;
    }
    while body.len() < req.len {
        let n = sock.read(&mut chunk).await.map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        body.extend_from_slice(&chunk[..n]);
    }
    match (req.method.as_str(), req.path.as_str()) {
        ("OPTIONS", _) => {
            let head = format!("HTTP/1.1 204 No Content\r\n{c}Access-Control-Allow-Methods: GET, POST\r\n\
                Access-Control-Allow-Headers: Content-Type\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
            sock.write_all(head.as_bytes()).await.map_err(|e| e.to_string())
        }
        ("GET", "/") => match std::env::var("AE_UI_FILE") {
            // AE_UI_FILE: serve the page from disk on every load (panel development without a rebuild).
            Ok(p) => send(&mut sock, 200, "text/html; charset=utf-8", &std::fs::read(&p).map_err(|e| format!("{p}: {e}"))?, c).await,
            Err(_) => send(&mut sock, 200, "text/html; charset=utf-8", PAGE.as_bytes(), c).await,
        },
        ("GET", "/health") => send(&mut sock, 200, "text/plain", b"ok", c).await,
        // Reticle pairing token for the page's dev-only connect snippet, read at request time from this
        // machine's ~/.reticle/pairing-token (written by `reticle init`); never in the repo. Loopback only; 404 without one.
        ("GET", "/reticle.local.js") => {
            let loopback = sock.peer_addr().is_ok_and(|a| a.ip().is_loopback());
            let tok = std::env::var_os("USERPROFILE").or_else(|| std::env::var_os("HOME"))
                .and_then(|h| std::fs::read_to_string(std::path::Path::new(&h).join(".reticle").join("pairing-token")).ok())
                .map(|t| t.trim().to_string())
                .filter(|t| loopback && !t.is_empty() && t.chars().all(|ch| ch.is_ascii_alphanumeric()));
            match tok {
                Some(t) => send(&mut sock, 200, "text/javascript", format!("export const token = '{t}';\n").as_bytes(), "").await,
                None => send(&mut sock, 404, "text/plain", b"not found", "").await,
            }
        }
        ("GET", "/events") => events(st, &mut sock, c).await,
        ("POST", "/api") => {
            let r: Value = serde_json::from_slice(&body).unwrap_or(Value::Null);
            if r["async"] == json!(true) && r["call"] != json!("quit") {
                let job = format!("j{}", st.jobs.fetch_add(1, Ordering::Relaxed));
                let rep = json!({"ok": true, "job": job});
                crate::emit(st, json!({"type": "job", "job": job, "call": r["call"], "device": r["device"], "state": "running"}));
                let st = st.clone();
                tokio::spawn(async move {
                    let t0 = Instant::now();
                    let ev = match crate::handle(&st, UI_CONN, &r).await {
                        Ok(v) if v["ok"] != json!(false) => json!({"state": "done", "result": v}),
                        Ok(v) => json!({"state": "failed", "error": v["error"], "result": v}),
                        Err(e) => json!({"state": "failed", "error": e}),
                    };
                    let mut ev = ev;
                    for (k, v) in [("type", json!("job")), ("job", json!(job)), ("call", r["call"].clone()), ("device", r["device"].clone()),
                        ("ms", json!(t0.elapsed().as_millis() as u64))] {
                        ev[k] = v;
                    }
                    crate::emit(&st, ev);
                });
                return send(&mut sock, 202, "application/json", rep.to_string().as_bytes(), c).await;
            }
            if r["call"] == json!("quit") && r["keep_devices"] == json!(true) {
                // Exit, phones keep running; the next daemon on the same agent address adopts them.
                let kept = crate::adopt::save_all(st, crate::adopt::addr()).await;
                let rep = json!({"ok": kept.is_ok(), "kept": kept.as_ref().ok(), "error": kept.as_ref().err()});
                send(&mut sock, 200, "application/json", rep.to_string().as_bytes(), c).await?;
                if kept.is_ok() {
                    eprintln!("quit from the panel; kept {kept:?} running");
                    std::process::exit(0);
                }
                return Ok(());
            }
            if r["call"] == json!("quit") {
                send(&mut sock, 200, "application/json", br#"{"ok":true}"#, c).await?;
                let stopped = crate::stop_all(st).await;
                eprintln!("quit from the panel; stopped {stopped:?}");
                std::process::exit(0);
            }
            let t0 = Instant::now();
            let mut rep = crate::handle(st, UI_CONN, &r).await.unwrap_or_else(|e| json!({"ok": false, "error": e}));
            rep["ms"] = json!(t0.elapsed().as_millis() as u64);
            send(&mut sock, 200, "application/json", rep.to_string().as_bytes(), c).await
        }
        ("GET", "/mux") => mux(st, &mut sock, &req, c).await,
        ("GET", "/frames") => frames(st, &mut sock, req.q("device").unwrap_or("d0"), StreamOpts::from(&req)?, c).await,
        ("GET", "/screenshot.png") => {
            let d = crate::ready_device(st, req.q("device").unwrap_or("d0"))?;
            let f = d.capture().await?;
            let mut png = Vec::new();
            image::DynamicImage::ImageRgb8(f.rgb).write_to(&mut std::io::Cursor::new(&mut png), image::ImageFormat::Png)
                .map_err(|e| e.to_string())?;
            let head = format!("HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Length: {}\r\n\
                Content-Disposition: attachment; filename=\"{}-{}.png\"\r\n{c}Connection: close\r\n\r\n", png.len(), d.id, device::now_ms());
            sock.write_all(head.as_bytes()).await.map_err(|e| e.to_string())?;
            sock.write_all(&png).await.map_err(|e| e.to_string())
        }
        ("POST", "/upload") => {
            let rep = upload(st, req.q("device").unwrap_or("d0"), &body).await.unwrap_or_else(|e| json!({"ok": false, "error": e}));
            send(&mut sock, 200, "application/json", rep.to_string().as_bytes(), c).await
        }
        ("GET", "/runs") => send(&mut sock, 200, "application/json", runs_list().to_string().as_bytes(), c).await,
        ("GET", p) if p.starts_with("/runs/") => match {
            let (rest, w) = (p[6..].to_string(), req.q("w").and_then(|w| w.parse::<u32>().ok()));
            tokio::task::spawn_blocking(move || run_file(&rest, w)).await.ok().flatten()
        } {
            Some((ctype, bytes)) => send(&mut sock, 200, ctype, &bytes, c).await,
            None => send(&mut sock, 404, "text/plain", b"not found", c).await,
        },
        _ => send(&mut sock, 404, "text/plain", b"not found", c).await,
    }
}

/// Test runner results folders, newest layout first: AE_RUNS_DIR; else AE_TESTS_DIR/results or tests/boltbetz/results
/// beside the exe or in any folder above it (checkout or install root, like mcp.rs finds run.py), plus the old
/// .scratch/test-matrix/runner/results kept for history.
fn runs_dirs() -> Vec<std::path::PathBuf> {
    if let Some(d) = std::env::var_os("AE_RUNS_DIR") {
        return vec![d.into()];
    }
    let exe = std::env::current_exe().unwrap_or_default();
    let above = |rel: &str| exe.ancestors().map(|a| a.join(rel)).find(|p| p.is_dir());
    let tests = std::env::var_os("AE_TESTS_DIR").map(|d| std::path::PathBuf::from(d).join("results"))
        .or_else(|| above("tests/boltbetz/results"));
    tests.into_iter().chain(above(".scratch/test-matrix/runner/results")).collect()
}

/// The folder of run `stamp` (first results folder that has it).
fn run_dir(stamp: &str) -> Option<std::path::PathBuf> {
    runs_dirs().into_iter().map(|d| d.join(stamp)).find(|d| d.join("results.json").is_file())
}

/// GET /runs: the newest 30 runs, newest first, each `{stamp, total, passed, failed, wall_s}` read from its results.json.
fn runs_list() -> Value {
    let mut stamps: Vec<(String, std::path::PathBuf)> = runs_dirs().iter()
        .flat_map(|d| std::fs::read_dir(d).into_iter().flatten().flatten())
        .filter(|e| e.path().join("results.json").is_file())
        .filter_map(|e| Some((e.file_name().into_string().ok()?, e.path()))).collect();
    stamps.sort_unstable_by(|a, b| b.0.cmp(&a.0));
    stamps.dedup_by(|a, b| a.0 == b.0);
    let runs: Vec<Value> = stamps.into_iter().take(30).map(|(s, dir)| {
        let r: Value = std::fs::read(dir.join("results.json")).ok()
            .and_then(|b| serde_json::from_slice(&b).ok()).unwrap_or(Value::Null);
        let cases = r["results"].as_array().cloned().unwrap_or_default();
        let passed = cases.iter().filter(|c| c["status"] == json!("PASS")).count();
        json!({"stamp": s, "total": cases.len(), "passed": passed, "failed": cases.len() - passed, "wall_s": r["total_wall_s"]})
    }).collect();
    json!({"ok": true, "runs": runs})
}

/// GET /runs/<stamp>/<file>: one file of a run (results.json, grid.md, a PNG). Plain names only, no subfolders.
/// `?w=340` on a PNG returns a JPEG thumbnail that wide (the panel's screenshot grid).
fn run_file(rest: &str, w: Option<u32>) -> Option<(&'static str, Vec<u8>)> {
    let (stamp, file) = rest.split_once('/')?;
    let ok = |s: &str| !s.is_empty() && !s.starts_with('.') && s.chars().all(|c| c.is_ascii_alphanumeric() || "-_.".contains(c));
    if !ok(stamp) || !ok(file) {
        return None;
    }
    let ctype = match file.rsplit('.').next()? {
        "json" => "application/json",
        "png" => "image/png",
        "md" => "text/plain; charset=utf-8",
        _ => return None,
    };
    let bytes = std::fs::read(run_dir(stamp)?.join(file)).ok()?;
    match w {
        Some(w) if ctype == "image/png" && (16..=2000).contains(&w) => {
            let img = image::load_from_memory(&bytes).ok()?.to_rgb8();
            let h = (img.height() as u64 * w as u64 / img.width().max(1) as u64).max(1) as u32;
            let small = if w < img.width() { image::imageops::thumbnail(&img, w, h) } else { img };
            device::encode_q(&small, None, 80).ok().map(|(j, ..)| ("image/jpeg", j))
        }
        _ => Some((ctype, bytes)),
    }
}

async fn send(sock: &mut TcpStream, code: u16, ctype: &str, body: &[u8], cors: &str) -> R<()> {
    let head = format!("HTTP/1.1 {code} {}\r\nContent-Type: {ctype}\r\nContent-Length: {}\r\nCache-Control: no-store\r\n{cors}Connection: close\r\n\r\n",
        match code { 200 => "OK", 202 => "Accepted", 404 => "Not Found", _ => "Error" }, body.len());
    sock.write_all(head.as_bytes()).await.map_err(|e| e.to_string())?;
    sock.write_all(body).await.map_err(|e| e.to_string())
}

async fn upload(st: &State, devid: &str, apk: &[u8]) -> R<Value> {
    if apk.len() < 4 || &apk[..2] != b"PK" {
        return Err("that is not an APK (zip) file".into());
    }
    let d = crate::ready_device(st, devid)?;
    let path = d.dir.join("upload.apk");
    std::fs::write(&path, apk).map_err(|e| format!("save upload: {e}"))?;
    crate::controls::handle(&d, "app", &json!({"install": path.to_string_lossy()})).await
}

/// One frame record: [u32 jpeg_len][u64 seq][u32 w][u32 h][jpeg], little endian.
pub fn frame_record(jpeg: &[u8], seq: u64, w: u32, h: u32) -> Vec<u8> {
    let mut out = Vec::with_capacity(20 + jpeg.len());
    out.extend_from_slice(&(jpeg.len() as u32).to_le_bytes());
    out.extend_from_slice(&seq.to_le_bytes());
    out.extend_from_slice(&w.to_le_bytes());
    out.extend_from_slice(&h.to_le_bytes());
    out.extend_from_slice(jpeg);
    out
}

/// Frame stream query, for /frames and /mux: output size (`max_width` px, or `scale` 0-1 of the device size;
/// default: half for screens wider than 1000 px, else full), max rate (`fps`, default every new scanout
/// frame), JPEG `quality` (default 75) and, for /frames only, `format` (`raw` records, default, or `mjpeg`
/// multipart for an <img>).
pub struct StreamOpts {
    max_width: Option<u32>,
    /// /mux: width the `full` (focused) phone is shown at; its frames are scaled to it before encoding.
    full_width: Option<u32>,
    scale: Option<f64>,
    gap: Duration,
    quality: u8,
    mjpeg: bool,
}

impl StreamOpts {
    fn from(req: &Req) -> R<StreamOpts> {
        let num = |k: &str| req.q(k).map(|v| v.parse::<f64>().map_err(|_| format!("bad number `{k}={v}`"))).transpose();
        let fps = num("fps")?;
        if fps.is_some_and(|f| !(f > 0.0 && f <= 120.0)) {
            return Err("fps must be in (0, 120]".into());
        }
        let scale = num("scale")?;
        if scale.is_some_and(|s| !(s > 0.0 && s <= 1.0)) {
            return Err("scale must be in (0, 1]".into());
        }
        let mjpeg = match req.q("format").unwrap_or("raw") {
            "raw" => false,
            "mjpeg" => true,
            f => return Err(format!("unknown format `{f}` (raw, mjpeg)")),
        };
        Ok(StreamOpts { max_width: num("max_width")?.filter(|&w| w > 0.0).map(|w| w.max(16.0) as u32),
            full_width: num("full_width")?.filter(|&w| w > 0.0).map(|w| w.max(16.0) as u32), scale,
            gap: fps.map_or(Duration::ZERO, |f| Duration::from_secs_f64(1.0 / f)), quality: num("quality")?.unwrap_or(75.0).clamp(1.0, 100.0) as u8, mjpeg })
    }

    /// Box to fit the frame into (aspect kept by `encode`), or None for full size.
    fn size(&self, w: u32, h: u32) -> Option<(u32, u32)> {
        match (self.max_width, self.scale) {
            (Some(mw), _) => (mw < w).then_some((mw, h)),
            (None, Some(s)) => (s < 1.0).then(|| (((w as f64 * s).round() as u32).max(1), ((h as f64 * s).round() as u32).max(1))),
            (None, None) => (w > 1000).then_some((w / 2, h / 2)),
        }
    }
}

/// GET /frames: one Device's frames until the client goes away or the Device stops (waits up to 10 min
/// for one that is not up yet).
async fn frames(st: &State, sock: &mut TcpStream, devid: &str, o: StreamOpts, cors: &str) -> R<()> {
    let ctype = if o.mjpeg { "multipart/x-mixed-replace; boundary=frame" } else { "application/octet-stream" };
    let head = format!("HTTP/1.1 200 OK\r\nContent-Type: {ctype}\r\nCache-Control: no-store\r\n{cors}Connection: close\r\n\r\n");
    sock.write_all(head.as_bytes()).await.map_err(|e| e.to_string())?;
    let (tx, mut rx) = tokio::sync::mpsc::channel::<Vec<u8>>(2);
    let write = async move {
        while let Some(rec) = rx.recv().await {
            if sock.write_all(&rec).await.is_err() {
                return;
            }
        }
    };
    tokio::join!(feed(st, devid, None, &o, false, true, tx), write);
    Ok(())
}

/// `/mux`: one task per Device feeds frame records into a channel; this connection writes them in order.
/// Same query as /frames (format aside), with `devices=d0,d3` and `full=d3` (that one unscaled).
async fn mux(st: &Arc<State>, sock: &mut TcpStream, req: &Req, cors: &str) -> R<()> {
    let o = Arc::new(StreamOpts::from(req)?);
    let head = format!("HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\nCache-Control: no-store\r\n{cors}Connection: close\r\n\r\n");
    sock.write_all(head.as_bytes()).await.map_err(|e| e.to_string())?;
    let full = req.q("full").unwrap_or("");
    let (tx, mut rx) = tokio::sync::mpsc::channel::<Vec<u8>>(16);
    for id in req.q("devices").unwrap_or("").split(',') {
        let Some(n) = id.strip_prefix('d').and_then(|s| s.parse::<u32>().ok()) else { continue };
        let (st, tx, id, o, full) = (st.clone(), tx.clone(), id.to_string(), o.clone(), id == full);
        tokio::spawn(async move { feed(&st, &id, Some(n), &o, full, false, tx).await });
    }
    drop(tx);
    while let Some(rec) = rx.recv().await {
        sock.write_all(&rec).await.map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// Frame records of one Device: on every screen change (at most one per `o.gap`), and every 2 s when there
/// is none. `n`: the /mux record (device number after the header). Waits while the Device is not up; ends
/// when the receiver goes away, or (`end_on_stop`) when a Device it streamed stops or 10 min pass without one.
async fn feed(st: &State, id: &str, n: Option<u32>, o: &StreamOpts, full: bool, end_on_stop: bool, tx: tokio::sync::mpsc::Sender<Vec<u8>>) {
    let (mut last, mut sent, t0, mut seen) = (u64::MAX, Instant::now() - Duration::from_secs(10), Instant::now(), false);
    // A focused stream (/frames, or the /mux `full` phone) means someone is looking at and using this phone.
    let focused = full || n.is_none();
    let count = |by: i32| if let Some(s) = st.devs.lock().unwrap().get_mut(id) { s.streams = s.streams.saturating_add_signed(by); };
    while !tx.is_closed() {
        let Ok(d) = crate::ready_device(st, id) else {
            if end_on_stop && (seen || t0.elapsed() > Duration::from_secs(600)) {
                return;
            }
            if seen { seen = false; }
            last = u64::MAX;
            tokio::time::sleep(Duration::from_millis(400)).await;
            continue;
        };
        if !seen {
            seen = true;
            count(1);
            if focused {
                d.focus.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                d.touch();
            }
        }
        // Fast path: a shared encoder pushes each new frame the moment crosvm posts it.
        let fit = d.fb.get().and_then(|f| f.read_px()).map(|p| if full { o.full_width.filter(|&fw| fw < p.w).map(|fw| (fw, p.h)) } else { o.size(p.w, p.h) });
        if let Some(mut rx) = fit.and_then(|fit| hub(&d, fit, o.quality)) {
            loop {
                tokio::select! {
                    r = rx.changed() => if r.is_err() { break },
                    _ = tx.closed() => break,
                }
                let Some(e) = rx.borrow_and_update().clone() else { continue };
                // 4 ms slack: frames posted a little faster than the cap (60 Hz jitter) must not halve the rate.
                if sent.elapsed() + Duration::from_millis(4) < o.gap {
                    continue;
                }
                let rec = record_of(&e, o.mjpeg, n);
                let delivered = match (n, tx.try_send(rec)) {
                    (_, Ok(())) => true,
                    (_, Err(tokio::sync::mpsc::error::TrySendError::Closed(_))) => break,
                    (Some(_), Err(tokio::sync::mpsc::error::TrySendError::Full(_))) => false,
                    (None, Err(tokio::sync::mpsc::error::TrySendError::Full(r))) => {
                        if tx.send(r).await.is_err() { break }
                        true
                    }
                };
                if delivered {
                    if let Some(sl) = st.devs.lock().unwrap().get_mut(id) { sl.sent += 1; }
                    sent = Instant::now();
                }
                if focused {
                    d.last_active_ms.store(device::now_ms(), std::sync::atomic::Ordering::SeqCst);
                }
            }
            continue;
        }
        let s = d.frame_seq();
        if (s != last && sent.elapsed() >= o.gap) || sent.elapsed() > Duration::from_secs(2) {
            // A new frame is already in the mapping; the 2 s keepalive asks crosvm to refresh it.
            let f = if s != last { d.capture_posted().await } else { d.capture().await };
            // Encode off the async threads, so phones encode in parallel and never stall another's feed.
            let (q, mjpeg) = (o.quality, o.mjpeg);
            let fit = f.as_ref().ok().and_then(|f| {
                let (w, h) = f.rgb.dimensions();
                if full { o.full_width.filter(|&fw| fw < w).map(|fw| (fw, h)) } else { o.size(w, h) }
            });
            let rec = match f {
                Ok(f) => tokio::task::spawn_blocking(move || record(&f, fit, q, mjpeg, n)).await.unwrap_or_else(|e| Err(e.to_string())),
                Err(e) => Err(e),
            };
            let Ok(rec) = rec else {
                tokio::time::sleep(Duration::from_millis(400)).await;
                continue;
            };
            // /mux: a full queue drops this phone's frame (the next one replaces it) instead of blocking the
            // other phones' feeds behind a slow reader.
            let delivered = match (n, tx.try_send(rec)) {
                (_, Ok(())) => true,
                (_, Err(tokio::sync::mpsc::error::TrySendError::Closed(_))) => break,
                (Some(_), Err(tokio::sync::mpsc::error::TrySendError::Full(_))) => false,
                (None, Err(tokio::sync::mpsc::error::TrySendError::Full(r))) => {
                    if tx.send(r).await.is_err() { break }
                    true
                }
            };
            if delivered {
                if let Some(sl) = st.devs.lock().unwrap().get_mut(id) { sl.sent += 1; }
            }
            (last, sent) = (s, Instant::now());
        }
        tokio::time::sleep(Duration::from_millis(2)).await;
    }
    if seen {
        count(-1);
        if focused {
            if let Ok(d) = crate::ready_device(st, id) {
                d.last_active_ms.store(device::now_ms(), std::sync::atomic::Ordering::SeqCst);
                d.focus.fetch_sub(1, std::sync::atomic::Ordering::SeqCst);
            }
        }
    }
}

/// One stream record of frame `f` fitted into `fit` at JPEG quality `q`: an MJPEG part, a /mux record (`n` =
/// device number after the header) or a /frames record. Records carry the device size, which a page draws at
/// and maps clicks to.
fn record(f: &device::Frame, fit: Option<(u32, u32)>, q: u8, mjpeg: bool, n: Option<u32>) -> R<Vec<u8>> {
    let (w, h) = f.rgb.dimensions();
    let (jpg, ..) = device::encode_q(&f.rgb, fit, q)?;
    Ok(match n {
        _ if mjpeg => {
            let mut p = format!("--frame\r\nContent-Type: image/jpeg\r\nContent-Length: {}\r\nX-Seq: {}\r\n\r\n", jpg.len(), f.generation).into_bytes();
            p.extend_from_slice(&jpg);
            p.extend_from_slice(b"\r\n");
            p
        }
        Some(n) => {
            let mut rec = frame_record(&jpg, f.generation, w, h);
            rec.splice(20..20, n.to_le_bytes());
            rec
        }
        None => frame_record(&jpg, f.generation, w, h),
    })
}

/// A stream record of an encoded frame: an MJPEG part, a /mux record (`n` = device number) or a /frames record.
fn record_of(e: &Enc, mjpeg: bool, n: Option<u32>) -> Vec<u8> {
    match n {
        _ if mjpeg => {
            let mut p = format!("--frame\r\nContent-Type: image/jpeg\r\nContent-Length: {}\r\nX-Seq: {}\r\n\r\n", e.jpeg.len(), e.seq).into_bytes();
            p.extend_from_slice(&e.jpeg);
            p.extend_from_slice(b"\r\n");
            p
        }
        Some(n) => {
            let mut rec = frame_record(&e.jpeg, e.seq, e.w, e.h);
            rec.splice(20..20, n.to_le_bytes());
            rec
        }
        None => frame_record(&e.jpeg, e.seq, e.w, e.h),
    }
}

/// GET /events: Server-Sent Events, one JSON object per `data:` line, each with a `type`
/// (snapshot, device, job, metrics, crash, error, lagged). Starts with a `snapshot` (the `status` reply).
async fn events(st: &Arc<State>, sock: &mut TcpStream, cors: &str) -> R<()> {
    let mut rx = st.events.subscribe();
    let head = format!("HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-store\r\n{cors}Connection: close\r\n\r\n");
    sock.write_all(head.as_bytes()).await.map_err(|e| e.to_string())?;
    let mut snap = crate::handle(st, UI_CONN, &json!({"call": "status"})).await?;
    snap["type"] = json!("snapshot");
    sock.write_all(format!("data: {snap}\n\n").as_bytes()).await.map_err(|e| e.to_string())?;
    loop {
        let msg = match tokio::time::timeout(Duration::from_secs(15), rx.recv()).await {
            Err(_) => ": ping\n\n".to_string(),
            Ok(Ok(v)) => format!("data: {v}\n\n"),
            Ok(Err(tokio::sync::broadcast::error::RecvError::Lagged(n))) => format!("data: {}\n\n", json!({"type": "lagged", "missed": n})),
            Ok(Err(_)) => return Ok(()),
        };
        sock.write_all(msg.as_bytes()).await.map_err(|e| e.to_string())?;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_request_head() {
        let r = parse_head("POST /upload?device=d0&x=a%20b+c HTTP/1.1\r\nHost: x\r\ncontent-length: 42\r\n").unwrap();
        assert_eq!((r.method.as_str(), r.path.as_str(), r.len), ("POST", "/upload", 42));
        assert_eq!(r.q("device"), Some("d0"));
        assert_eq!(r.q("x"), Some("a b c"));
        assert_eq!(parse_head("GET / HTTP/1.1").unwrap().len, 0);
        assert_eq!(unescape("100%"), "100%");
        let r = parse_head("GET /events HTTP/1.1\r\nOrigin: http://localhost:5173\r\n").unwrap();
        assert_eq!(cors(r.origin.as_deref()), "Access-Control-Allow-Origin: http://localhost:5173\r\nVary: Origin\r\n");
        assert_eq!(cors(Some("http://localhost.evil.com")), "");
        assert_eq!(cors(Some("https://example.com")), "");
        assert_eq!(cors(None), "");
    }

    #[test]
    fn stream_opts_size_and_rate() {
        let o = |q: &str| StreamOpts::from(&parse_head(&format!("GET /frames?{q} HTTP/1.1")).unwrap());
        let d = o("").unwrap();
        assert_eq!((d.size(1320, 2868), d.size(656, 1424), d.gap), (Some((660, 1434)), None, Duration::ZERO));
        assert_eq!(o("max_width=220&fps=10").unwrap().size(656, 1424), Some((220, 1424)));
        assert_eq!(o("max_width=0").unwrap().size(656, 1424), None);
        assert_eq!(o("fps=10").unwrap().gap, Duration::from_millis(100));
        assert_eq!(o("scale=0.5").unwrap().size(656, 1424), Some((328, 712)));
        assert_eq!(o("scale=1").unwrap().size(1320, 2868), None);
        assert!(o("fps=0").is_err() && o("scale=2").is_err() && o("format=gif").is_err());
        assert!(o("format=mjpeg&quality=50").unwrap().mjpeg);
    }

    #[test]
    fn frame_record_layout() {
        let r = frame_record(&[9, 8, 7], 5, 720, 1080);
        assert_eq!(u32::from_le_bytes(r[0..4].try_into().unwrap()), 3);
        assert_eq!(u64::from_le_bytes(r[4..12].try_into().unwrap()), 5);
        assert_eq!(u32::from_le_bytes(r[12..16].try_into().unwrap()), 720);
        assert_eq!(u32::from_le_bytes(r[16..20].try_into().unwrap()), 1080);
        assert_eq!(&r[20..], &[9, 8, 7]);
    }

    #[test]
    fn run_files_stay_in_the_runs_folder() {
        for bad in ["../x/results.json", "a/../b.png", "a/b/c.png", "a/.png", "a/x.exe", "/results.json", "a"] {
            assert!(run_file(bad, None).is_none(), "{bad}");
        }
    }

    #[test]
    fn page_is_embedded() {
        assert!(PAGE.contains("<title>agent-emu</title>") && PAGE.contains("/mux"));
    }
}
