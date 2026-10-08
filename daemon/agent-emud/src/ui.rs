// Human control panel: a small HTTP/1.1 server on 127.0.0.1:7401 (AE_UI_ADDR) serving one embedded page.
//   GET  /                         the page (ui.html, compiled in)
//   GET  /health                   "ok" (the launcher checks it)
//   POST /api                      JSON request -> the same handler as the binary API ({"call": ...})
//   GET  /frames?device=d0         frame stream: [u32 jpeg_len][u64 seq][u32 w][u32 h][jpeg] per frame, LE
//   GET  /screenshot.png?device=d0 one PNG download
//   POST /upload?device=d0         body = APK, installed with adb
// Localhost only; no auth (the panel can do what the binary API can).
use crate::device::{self, Device, R};
use crate::State;
use serde_json::{json, Value};
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};

const PAGE: &str = include_str!("ui.html");
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
    let len = lines.filter_map(|l| l.split_once(':'))
        .find(|(k, _)| k.trim().eq_ignore_ascii_case("content-length"))
        .and_then(|(_, v)| v.trim().parse().ok()).unwrap_or(0);
    Ok(Req { method, path: path.to_string(), query, len })
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
    let mut body = buf[end + 4..].to_vec();
    if req.len > MAX_UPLOAD {
        return send(&mut sock, 413, "text/plain", b"too large").await;
    }
    while body.len() < req.len {
        let n = sock.read(&mut chunk).await.map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        body.extend_from_slice(&chunk[..n]);
    }
    match (req.method.as_str(), req.path.as_str()) {
        ("GET", "/") => send(&mut sock, 200, "text/html; charset=utf-8", PAGE.as_bytes()).await,
        ("GET", "/health") => send(&mut sock, 200, "text/plain", b"ok").await,
        ("POST", "/api") => {
            let r: Value = serde_json::from_slice(&body).unwrap_or(Value::Null);
            if r["call"] == json!("quit") {
                send(&mut sock, 200, "application/json", br#"{"ok":true}"#).await?;
                let stopped = crate::stop_all(st).await;
                eprintln!("quit from the panel; stopped {stopped:?}");
                std::process::exit(0);
            }
            let t0 = Instant::now();
            let mut rep = crate::handle(st, UI_CONN, &r).await.unwrap_or_else(|e| json!({"ok": false, "error": e}));
            rep["ms"] = json!(t0.elapsed().as_millis() as u64);
            send(&mut sock, 200, "application/json", rep.to_string().as_bytes()).await
        }
        ("GET", "/frames") => frames(st, &mut sock, req.q("device").unwrap_or("d0")).await,
        ("GET", "/screenshot.png") => {
            let d = crate::ready_device(st, req.q("device").unwrap_or("d0"))?;
            let f = d.capture().await?;
            let mut png = Vec::new();
            image::DynamicImage::ImageRgb8(f.rgb).write_to(&mut std::io::Cursor::new(&mut png), image::ImageFormat::Png)
                .map_err(|e| e.to_string())?;
            let head = format!("HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Length: {}\r\n\
                Content-Disposition: attachment; filename=\"{}-{}.png\"\r\nConnection: close\r\n\r\n", png.len(), d.id, device::now_ms());
            sock.write_all(head.as_bytes()).await.map_err(|e| e.to_string())?;
            sock.write_all(&png).await.map_err(|e| e.to_string())
        }
        ("POST", "/upload") => {
            let rep = upload(st, req.q("device").unwrap_or("d0"), &body).await.unwrap_or_else(|e| json!({"ok": false, "error": e}));
            send(&mut sock, 200, "application/json", rep.to_string().as_bytes()).await
        }
        _ => send(&mut sock, 404, "text/plain", b"not found").await,
    }
}

async fn send(sock: &mut TcpStream, code: u16, ctype: &str, body: &[u8]) -> R<()> {
    let head = format!("HTTP/1.1 {code} {}\r\nContent-Type: {ctype}\r\nContent-Length: {}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n",
        if code == 200 { "OK" } else { "Error" }, body.len());
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

/// Streams a frame whenever the scanout changes (checked every 5 ms), and one every 2 s when it does not,
/// until the client goes away or the Device stops.
async fn frames(st: &State, sock: &mut TcpStream, devid: &str) -> R<()> {
    let head = "HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n";
    sock.write_all(head.as_bytes()).await.map_err(|e| e.to_string())?;
    let mut d: Option<Arc<Device>> = None;
    let (mut last, mut sent) = (u64::MAX, Instant::now() - Duration::from_secs(10));
    loop {
        match &d {
            Some(x) if x.ready.load(Ordering::SeqCst) => {}
            Some(_) => return Ok(()), // Device stopped
            None => {
                d = crate::ready_device(st, devid).ok();
                if d.is_none() {
                    tokio::time::sleep(Duration::from_millis(500)).await;
                    if sent.elapsed() > Duration::from_secs(600) { return Ok(()); }
                    continue;
                }
            }
        }
        let dev = d.as_ref().unwrap();
        let s = dev.frame_seq();
        if s != last || sent.elapsed() > Duration::from_secs(2) {
            let f = dev.capture().await?;
            let (jpg, ..) = device::encode(&f.rgb, None)?;
            let (w, h) = f.rgb.dimensions();
            sock.write_all(&frame_record(&jpg, f.generation, w, h)).await.map_err(|e| e.to_string())?;
            (last, sent) = (s, Instant::now());
        }
        tokio::time::sleep(Duration::from_millis(5)).await;
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
    fn page_is_embedded() {
        assert!(PAGE.contains("<title>agent-emu</title>") && PAGE.contains("/frames"));
    }
}
