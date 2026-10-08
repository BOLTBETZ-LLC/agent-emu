// agent-emud: owns one Device and serves the computer-use API v1 as newline-delimited JSON on 127.0.0.1:7400.
//   agent-emud [serve] [--addr 127.0.0.1:7400]   daemon
//   agent-emud mcp     [--addr 127.0.0.1:7400]   MCP stdio server forwarding to the daemon
// Request:  {"id":1,"call":"tap","device":"d0","x":100,"y":200}
// Reply:    {"id":1,"ok":true,"ms":12,...} or {"id":1,"ok":false,"error":"..."}
mod device;
mod fast;
mod mcp;

use base64::Engine;
use device::{Cfg, Device, R};
use serde_json::{json, Value};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex as StdMutex};
use std::time::{Duration, Instant};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::TcpListener;
use tokio::sync::Mutex;

struct State {
    cfg: Cfg,
    dev: StdMutex<Option<Arc<Device>>>,
    lifecycle: Mutex<()>, // serializes start/stop
    lease: StdMutex<Option<u64>>,
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
    let st = Arc::new(State { cfg: Cfg::from_env(), dev: StdMutex::new(None), lifecycle: Mutex::new(()), lease: StdMutex::new(None) });
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
                    let mut lease = st.lease.lock().unwrap();
                    if *lease == Some(conn) { *lease = None; }
                });
            }
            _ = tokio::signal::ctrl_c() => {
                let d = st.dev.lock().unwrap().take();
                if let Some(d) = d { d.stop().await; }
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

async fn handle(st: &State, conn: u64, req: &Value) -> R<Value> {
    let call = req["call"].as_str().ok_or("missing `call`")?;
    let devid = req["device"].as_str().unwrap_or("d0");
    match call {
        "start" => {
            let _g = st.lifecycle.lock().await;
            if st.dev.lock().unwrap().is_some() {
                return Err("a Device is already running (v1 drives one Device)".into());
            }
            let idx: u32 = devid.strip_prefix('d').and_then(|s| s.parse().ok()).ok_or("device id must be d<N>")?;
            let t0 = Instant::now();
            let avail = device::available_mb();
            let d = Device::spawn(&st.cfg, idx).await?;
            *st.dev.lock().unwrap() = Some(d.clone());
            if let Err(e) = d.wait_boot(Duration::from_secs(900)).await {
                d.stop().await;
                *st.dev.lock().unwrap() = None;
                return Err(e);
            }
            return Ok(json!({"ok": true, "device": d.id, "ready_s": t0.elapsed().as_secs_f64(), "available_mb_before": avail, "dir": d.dir}));
        }
        "status" => {
            let d = st.dev.lock().unwrap().clone();
            return Ok(json!({"ok": true, "devices": d.map(|d| vec![json!({"id": d.id, "ready": d.ready.load(Ordering::SeqCst),
                "frames_via": d.transport().0, "input_via": d.transport().1})]).unwrap_or_default(),
                "lease": *st.lease.lock().unwrap() == Some(conn), "available_mb": device::available_mb()}));
        }
        _ => {}
    }
    let d = st.dev.lock().unwrap().clone().filter(|d| d.id == devid).ok_or(format!("no Device `{devid}`"))?;
    if call == "stop" {
        let _g = st.lifecycle.lock().await;
        d.stop().await;
        *st.dev.lock().unwrap() = None;
        *st.lease.lock().unwrap() = None;
        return Ok(json!({"ok": true}));
    }
    if !d.ready.load(Ordering::SeqCst) {
        return Err(format!("Device `{devid}` is still booting"));
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
        "shell" => {
            let secs = req["timeout_s"].as_u64().unwrap_or(120);
            let (o, code) = d.con.exec(req["cmd"].as_str().ok_or("missing `cmd`")?, Duration::from_secs(secs)).await?;
            return Ok(json!({"ok": true, "out": o, "code": code}));
        }
        "lease" => {
            let mut l = st.lease.lock().unwrap();
            return match *l {
                Some(c) if c != conn => Err("busy: Device is leased by another client".into()),
                _ => { *l = Some(conn); Ok(json!({"ok": true})) }
            };
        }
        "release" => {
            let mut l = st.lease.lock().unwrap();
            if *l == Some(conn) { *l = None; }
            return Ok(json!({"ok": true}));
        }
        "tap" | "swipe" => {
            let s = *d.scale.lock().unwrap();
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
    if matches!(*st.lease.lock().unwrap(), Some(c) if c != conn) {
        return Err("busy: Device is leased by another client".into());
    }
    let s0 = d.frame_seq();
    let t = Instant::now();
    let via = inject(&d, input).await?;
    let input_ms = t.elapsed().as_millis() as u64;
    if req["screenshot"] == json!(false) {
        return Ok(json!({"ok": true, "input_ms": input_ms, "input_via": via}));
    }
    let deadline = Duration::from_millis(req["deadline_ms"].as_u64().unwrap_or(3000));
    let quiet = Duration::from_millis(req["settle_ms"].as_u64().unwrap_or(33));
    let (f, settled, frames, first) = d.settled(s0, t, quiet, deadline).await?;
    let size = req["size"].as_str().and_then(device::parse_size);
    let mut rep = json!({"ok": true, "input_ms": input_ms, "input_via": via, "settled": settled, "frames": frames,
        "first_frame_ms": first, "settled_ms": t.elapsed().as_millis() as u64, "frame": frame_json(&f, size)?});
    if !settled {
        rep["reason"] = json!(if first.is_none() && via == "fast" { "no_frame" } else { "frames_changing" });
    }
    Ok(rep)
}
