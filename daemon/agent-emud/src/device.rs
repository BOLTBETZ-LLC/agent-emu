// One Device: per-Device dir, boot via boot-stage1.ps1, the guest console pipe, frames and inputs.
// Fast path (fast.rs): frames from crosvm's scanout mapping, input into virtio-input over named pipes.
// Fallback: the guest shell on hvc1 (`input ...`, `screencap -p | base64`), used when the fast path
// did not open (older crosvm) and for type_text and keys the virtio keyboard lacks.
use crate::fast::{self, Fb, Input};
use base64::Engine;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex as StdMutex, OnceLock};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tokio::io::{AsyncReadExt, AsyncWriteExt, WriteHalf};
use tokio::net::windows::named_pipe::{ClientOptions, NamedPipeClient};
use tokio::process::{Child, Command};
use tokio::sync::{Mutex, Notify};

pub type R<T> = Result<T, String>;
const B64: base64::engine::GeneralPurpose = base64::engine::general_purpose::STANDARD;
const CREATE_NO_WINDOW: u32 = 0x0800_0000;
// Read-only images every Device hard-links from run/ (one disk copy, one Windows file cache).
const SHARED: &[&str] = &["kernel-dax", "initrd-dax-pmem.img", "initrd-dax-slim.img", "boot.img", "init_boot.img",
    "vendor_boot.img", "vbmeta.img", "vbmeta_system.img", "vbmeta_system_dlkm.img", "vbmeta_vendor_dlkm.img",
    "super.img", "apk.img"];
const PARTS: &str = "misc:misc.img:writable frp:frp.img:writable boot_a:boot.img boot_b:boot.img \
    init_boot_a:init_boot.img init_boot_b:init_boot.img vendor_boot_a:vendor_boot.img vendor_boot_b:vendor_boot.img \
    vbmeta_a:vbmeta.img vbmeta_b:vbmeta.img vbmeta_system_a:vbmeta_system.img vbmeta_system_b:vbmeta_system.img \
    vbmeta_system_dlkm_a:vbmeta_system_dlkm.img vbmeta_system_dlkm_b:vbmeta_system_dlkm.img \
    vbmeta_vendor_dlkm_a:vbmeta_vendor_dlkm.img vbmeta_vendor_dlkm_b:vbmeta_vendor_dlkm.img super:super.img \
    userdata:userdata.img:writable metadata:metadata.img:writable";
// SurfaceFlinger 1008 = "disable HW overlays": every frame is composed by the GPU into a target that
// is cleared first. Without it slim3 (no wallpaper, no SystemUI) leaves old pixels in the scanout
// wherever no window draws.
// hide_error_dialogs: slim3 has no Bluetooth HAL peer, so com.android.bluetooth aborts in a loop and
// its "keeps stopping" dialog can cover the app. Never `pm disable-user` it: the next airplane-mode
// change makes BluetoothManagerService unbind a service it never bound, and system_server crash-loops.
// log.tag.RIL S: the RIL logs "Can't connect to port:9600d" (no modem simulator) ~40 MB a minute.
const SETUP: &str = "settings put global hide_error_dialogs 1; \
    am broadcast -a android.intent.action.CLOSE_SYSTEM_DIALOGS; setprop log.tag.RIL S; \
    cmd connectivity airplane-mode enable; settings put global window_animation_scale 0; \
    settings put global transition_animation_scale 0; settings put global animator_duration_scale 0; \
    svc power stayon true; input keyevent KEYCODE_WAKEUP; wm dismiss-keyguard; \
    settings put secure immersive_mode_confirmations confirmed; \
    service call SurfaceFlinger 1008 i32 1; \
    ip link set buried_eth0 up; ip addr add 10.0.2.15/24 dev buried_eth0; \
    ip route add 10.0.2.0/24 dev buried_eth0 table legacy_system";

pub fn now_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_millis() as u64
}

pub struct Cfg {
    pub work: PathBuf,
    pub mem: String,
    pub cpus: String,
    pub min_avail_mb: u64,
}

impl Cfg {
    pub fn from_env() -> Cfg {
        let e = |k: &str, d: &str| std::env::var(k).unwrap_or_else(|_| d.to_string());
        Cfg {
            work: PathBuf::from(e("AE_WORK", "C:/dev/agent-emu-work")),
            mem: e("AE_MEM", "896"),
            cpus: e("AE_CPUS", "2"),
            min_avail_mb: e("AE_MIN_AVAIL_MB", "4000").parse().unwrap_or(4000),
        }
    }
    pub fn crosvm(&self) -> PathBuf {
        self.work.join("crosvm-pmem/target/release/crosvm.exe")
    }
}

fn win(p: &Path) -> String {
    p.to_string_lossy().replace('/', "\\")
}

// ---------- host checks ----------

#[repr(C)]
struct MemStatus { len: u32, load: u32, total_phys: u64, avail_phys: u64, total_pf: u64, avail_pf: u64, total_virt: u64, avail_virt: u64, avail_ext: u64 }
extern "system" {
    fn GlobalMemoryStatusEx(p: *mut MemStatus) -> i32;
}

/// Host available physical memory in MB (same figure as `\Memory\Available MBytes`).
pub fn available_mb() -> u64 {
    let mut m = MemStatus { len: std::mem::size_of::<MemStatus>() as u32, load: 0, total_phys: 0, avail_phys: 0, total_pf: 0, avail_pf: 0, total_virt: 0, avail_virt: 0, avail_ext: 0 };
    unsafe { GlobalMemoryStatusEx(&mut m) };
    m.avail_phys >> 20
}

async fn crosvm_running() -> bool {
    let o = Command::new("tasklist").args(["/FI", "IMAGENAME eq crosvm.exe", "/NH"]).creation_flags(CREATE_NO_WINDOW).output().await;
    o.map(|o| String::from_utf8_lossy(&o.stdout).to_lowercase().contains("crosvm.exe")).unwrap_or(false)
}

// ---------- per-Device dir (port of fleet.py make_device) ----------

fn make_device(cfg: &Cfg, idx: u32) -> R<PathBuf> {
    let run = cfg.work.join("run");
    let d = cfg.work.join(format!("fleet/d{idx}"));
    std::fs::create_dir_all(&d).map_err(|e| e.to_string())?;
    for f in SHARED {
        if !d.join(f).exists() {
            std::fs::hard_link(run.join(f), d.join(f)).map_err(|e| format!("link {f}: {e}"))?;
        }
    }
    for (n, sz) in [("frp", 1u64 << 20), ("metadata", 64 << 20), ("userdata", 8 << 30), ("misc", 1 << 20), ("out", 64 << 20)] {
        let f = std::fs::File::create(d.join(format!("{n}.img"))).map_err(|e| format!("{n}.img: {e}"))?;
        f.set_len(sz).map_err(|e| e.to_string())?;
    }
    for f in ["kernel.log", "logcat.log", "crosvm.log", "console.log", "os_composite.img", "os_composite.img.filler",
        "os_composite.img.footer", "os_composite.img.header", "fb.bin"] {
        let _ = std::fs::remove_file(d.join(f));
    }
    let st = std::process::Command::new(cfg.crosvm()).arg("create_composite").arg("os_composite.img")
        .args(PARTS.split_whitespace()).current_dir(&d).creation_flags(CREATE_NO_WINDOW).output()
        .map_err(|e| format!("create_composite: {e}"))?;
    if !st.status.success() {
        return Err(format!("create_composite: {}", String::from_utf8_lossy(&st.stderr)));
    }
    Ok(d)
}
use std::os::windows::process::CommandExt as _;

// ---------- console ----------

/// Owns the guest console named pipe (crosvm accepts one client). Commands run one at a time.
pub struct Console {
    w: Mutex<WriteHalf<NamedPipeClient>>,
    buf: Arc<StdMutex<Vec<u8>>>,
    notify: Arc<Notify>,
    closed: Arc<AtomicBool>,
    seq: AtomicU64,
}

impl Console {
    async fn open(name: &str, log: PathBuf, wait: Duration) -> R<Console> {
        let end = Instant::now() + wait;
        let pipe = loop {
            match ClientOptions::new().open(name) {
                Ok(p) => break p,
                Err(_) if Instant::now() < end => tokio::time::sleep(Duration::from_millis(300)).await,
                Err(e) => return Err(format!("console pipe {name}: {e}")),
            }
        };
        let (mut r, w) = tokio::io::split(pipe);
        let (buf, notify, closed) = (Arc::new(StdMutex::new(Vec::new())), Arc::new(Notify::new()), Arc::new(AtomicBool::new(false)));
        let (b, n, c) = (buf.clone(), notify.clone(), closed.clone());
        tokio::spawn(async move {
            let mut f = tokio::fs::OpenOptions::new().create(true).append(true).open(&log).await.ok();
            let mut chunk = vec![0u8; 1 << 16];
            loop {
                let k = match r.read(&mut chunk).await { Ok(0) | Err(_) => break, Ok(k) => k };
                b.lock().unwrap().extend_from_slice(&chunk[..k]);
                n.notify_waiters();
                if let Some(f) = f.as_mut() { let _ = f.write_all(&chunk[..k]).await; }
            }
            c.store(true, Ordering::SeqCst);
            n.notify_waiters();
        });
        Ok(Console { w: Mutex::new(w), buf, notify, closed, seq: AtomicU64::new(0) })
    }

    /// Run `cmd` as root in the guest shell; returns (stdout+stderr, exit code).
    pub async fn exec(&self, cmd: &str, timeout: Duration) -> R<(String, i32)> {
        let mut w = self.w.lock().await;
        let tag = format!("{:x}{:x}", now_ms() & 0xffffff, self.seq.fetch_add(1, Ordering::Relaxed));
        self.buf.lock().unwrap().clear();
        w.write_all(wrap(cmd, &tag).as_bytes()).await.map_err(|e| format!("console write: {e}"))?;
        w.flush().await.map_err(|e| e.to_string())?;
        let end = tokio::time::Instant::now() + timeout;
        loop {
            let notified = self.notify.notified();
            if let Some(r) = parse_reply(&self.buf.lock().unwrap(), &tag) {
                return Ok(r);
            }
            if self.closed.load(Ordering::SeqCst) {
                return Err("console closed".into());
            }
            if tokio::time::timeout_at(end, notified).await.is_err() {
                return Err(format!("console timeout after {} ms: {cmd}", timeout.as_millis()));
            }
        }
    }
}

/// The markers are split by quotes on the command line, so the tty echo never contains them.
fn wrap(cmd: &str, tag: &str) -> String {
    format!("echo __AS{tag}'__'; su 0 sh -c '{}' 2>&1; echo __AE{tag}'__'$?\n", cmd.replace('\'', r"'\''"))
}

fn parse_reply(buf: &[u8], tag: &str) -> Option<(String, i32)> {
    let s = String::from_utf8_lossy(buf);
    let (a, e) = (format!("__AS{tag}__"), format!("__AE{tag}__"));
    let ei = s.find(&e)?;
    let rest = &s[ei + e.len()..];
    let code = rest[..rest.find('\n')?].trim().parse().unwrap_or(-1);
    let si = s[..ei].find(&a).map(|i| i + a.len()).unwrap_or(0);
    Some((s[si..ei].replace('\r', "").trim_matches('\n').to_string(), code))
}

// ---------- frames and inputs ----------

pub struct Frame {
    pub rgb: image::RgbImage,
    pub captured_ms: u64,
    pub generation: u64,
    pub capture_ms: u64,
    /// ms since crosvm posted this frame (fast path only).
    pub age_ms: Option<u64>,
}

/// JPEG q75, optionally fitted inside `size` (aspect kept). Returns (jpeg, w, h, device_w, device_h, scale).
pub fn encode(rgb: &image::RgbImage, size: Option<(u32, u32)>) -> R<(Vec<u8>, u32, u32, u32, u32, f64)> {
    let (dw, dh) = rgb.dimensions();
    let scaled;
    let img = match size {
        Some((w, h)) if w < dw || h < dh => {
            scaled = image::DynamicImage::ImageRgb8(rgb.clone()).resize(w, h, image::imageops::FilterType::Triangle).to_rgb8();
            &scaled
        }
        _ => rgb,
    };
    let mut out = Vec::new();
    // jpeg-encoder with AVX2: about 3x faster than image's encoder on this PC.
    jpeg_encoder::Encoder::new(&mut out, 75)
        .encode(img.as_raw(), img.width() as u16, img.height() as u16, jpeg_encoder::ColorType::Rgb)
        .map_err(|e| e.to_string())?;
    Ok((out, img.width(), img.height(), dw, dh, dw as f64 / img.width() as f64))
}

pub fn parse_size(s: &str) -> Option<(u32, u32)> {
    let (w, h) = s.split_once('x')?;
    Some((w.trim().parse().ok()?, h.trim().parse().ok()?))
}

/// `back` -> KEYCODE_BACK. Only [A-Z0-9_] reaches the shell.
pub fn keycode(name: &str) -> R<String> {
    let n = name.trim().to_ascii_uppercase();
    if n.is_empty() || !n.chars().all(|c| c.is_ascii_alphanumeric() || c == '_') {
        return Err(format!("bad key name: {name}"));
    }
    Ok(if n.starts_with("KEYCODE_") || n.chars().all(|c| c.is_ascii_digit()) { n } else { format!("KEYCODE_{n}") })
}

/// `input text` argument: spaces become %s, then single-quoted for the guest sh.
pub fn input_text_cmd(text: &str) -> String {
    format!("input text '{}'", text.replace('%', r"\%").replace(' ', "%s").replace('\'', r"'\''"))
}

pub struct Device {
    pub id: String,
    pub dir: PathBuf,
    boot: Mutex<Option<Child>>,
    pub con: Console,
    pub ready: AtomicBool,
    last: StdMutex<Option<Vec<u8>>>,
    gen: AtomicU64,
    pub scale: StdMutex<f64>,
    idx: u32,
    pub input: OnceLock<Input>,
    pub fb: OnceLock<Arc<Fb>>,
    /// Crash/ANR events parsed from this boot's logcat.log (logs.rs).
    pub events: Arc<crate::logs::Events>,
}

impl Device {
    /// Check the host, build the Device dir, spawn boot-stage1.ps1 and open the console pipe.
    pub async fn spawn(cfg: &Cfg, idx: u32) -> R<Arc<Device>> {
        let avail = available_mb();
        if avail < cfg.min_avail_mb {
            return Err(format!("host Available {avail} MB < {} MB; not booting", cfg.min_avail_mb));
        }
        // AE_ALLOW_OTHER_CROSVM=1: another worker's Device may run beside this one (distinct id/pipes/ports).
        if std::env::var_os("AE_ALLOW_OTHER_CROSVM").is_none() && crosvm_running().await {
            return Err("a crosvm.exe is already running; one Device at a time".into());
        }
        let dir = make_device(cfg, idx)?;
        let pmem = cfg.work.join("run/system-pmem.img");
        let child = Command::new("powershell")
            .args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]).arg(cfg.work.join("boot-stage1.ps1"))
            .env("AE_DIR", win(&dir)).env("AE_ID", idx.to_string()).env("AE_MEM", &cfg.mem).env("AE_CPUS", &cfg.cpus)
            .env("AE_EXTRA", format!("--socket PIPE:ae-vm-{idx} --pmem path={},ro=true --input multi-touch[path={}] --input keyboard[path={}]",
                pmem.to_string_lossy().replace('\\', "/"), fast::touch_pipe(idx), fast::kbd_pipe(idx)))
            // No desktop window: crosvm's 2D GPU uses the stub display; frames still come from fb.bin.
            .env("AGENT_EMU_HEADLESS", "1")
            .env("AGENT_EMU_FB", win(&dir.join("fb.bin"))).env("AGENT_EMU_FB_PIPE", fast::fb_pipe(idx))
            // slirp forwards 127.0.0.1:6520+N to adbd at 10.0.2.15:5555 (SETUP gives the guest NIC that address).
            .env("AGENT_EMU_ADB_PORT", (6520 + idx).to_string())
            .env("AE_KERNEL", "kernel-dax").env("AE_INITRD", "initrd-dax-pmem.img").env("AE_CROSVM", win(&cfg.crosvm()))
            .stdin(std::process::Stdio::null()).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null())
            .creation_flags(CREATE_NO_WINDOW).spawn().map_err(|e| format!("spawn boot: {e}"))?;
        let con = match Console::open(&format!(r"\\.\pipe\agentemu-console-{idx}"), dir.join("console.log"), Duration::from_secs(60)).await {
            Ok(c) => c,
            Err(e) => {
                kill_tree(child.id()).await;
                return Err(e);
            }
        };
        Ok(Arc::new(Device {
            id: format!("d{idx}"), dir: dir.clone(), boot: Mutex::new(Some(child)), con, ready: AtomicBool::new(false),
            last: StdMutex::new(None), gen: AtomicU64::new(0), scale: StdMutex::new(1.0),
            idx, input: OnceLock::new(), fb: OnceLock::new(), events: crate::logs::Events::spawn(dir.join("logcat.log")),
        }))
    }

    /// Poll until sys.boot_completed and dev.bootcomplete, then apply the v1 Device settings.
    /// Aborts when host Available memory falls under `min_avail_mb` while booting.
    pub async fn wait_boot(&self, limit: Duration, min_avail_mb: u64) -> R<()> {
        let end = Instant::now() + limit;
        loop {
            let avail = available_mb();
            if avail < min_avail_mb {
                return Err(format!("host Available {avail} MB < {min_avail_mb} MB during boot; stopped"));
            }
            if let Ok((o, _)) = self.con.exec("getprop sys.boot_completed; getprop dev.bootcomplete", Duration::from_secs(20)).await {
                if o.split_whitespace().collect::<Vec<_>>() == ["1", "1"] {
                    break;
                }
            }
            if self.boot.lock().await.as_mut().map_or(true, |c| c.try_wait().ok().flatten().is_some()) {
                return Err("crosvm exited during boot (see crosvm.log)".into());
            }
            if Instant::now() > end {
                return Err("boot timeout".into());
            }
            tokio::time::sleep(Duration::from_secs(3)).await;
        }
        self.con.exec(SETUP, Duration::from_secs(60)).await?;
        match Input::open(self.idx) {
            Ok(i) => { let _ = self.input.set(i); }
            Err(e) => eprintln!("{}: fast input off, console fallback: {e}", self.id),
        }
        match Fb::open(&self.dir.join("fb.bin"), &fast::fb_pipe(self.idx)) {
            Ok(f) if f.seq() > 0 => { let _ = self.fb.set(Arc::new(f)); }
            Ok(_) => eprintln!("{}: fast frames off: scanout mapping has no frame yet", self.id),
            Err(e) => eprintln!("{}: fast frames off, console fallback: {e}", self.id),
        }
        self.ready.store(true, Ordering::SeqCst);
        Ok(())
    }

    /// ("fast" | "console") for frames and for input.
    pub fn transport(&self) -> (&'static str, &'static str) {
        let t = |b: bool| if b { "fast" } else { "console" };
        (t(self.fb.get().is_some()), t(self.input.get().is_some()))
    }

    /// Scanout frames posted so far (0 without the fast path).
    pub fn frame_seq(&self) -> u64 {
        self.fb.get().map_or(0, |f| f.seq())
    }

    pub fn idx(&self) -> u32 {
        self.idx
    }

    /// pid of the boot powershell, the parent of this Device's crosvm broker.
    pub async fn boot_pid(&self) -> Option<u32> {
        self.boot.lock().await.as_ref().and_then(|c| c.id())
    }

    pub async fn stop(&self) {
        self.ready.store(false, Ordering::SeqCst);
        if let Some(c) = self.boot.lock().await.take() {
            kill_tree(c.id()).await;
        }
    }

    pub async fn capture(&self) -> R<Frame> {
        self.capture_via(false).await
    }

    /// `console` forces guest `screencap` even when the scanout mapping is open.
    pub async fn capture_via(&self, console: bool) -> R<Frame> {
        let (t0, captured_ms) = (Instant::now(), now_ms());
        if let Some(fb) = self.fb.get().filter(|_| !console) {
            let fb = fb.clone();
            let r = tokio::task::spawn_blocking(move || fb.refresh().map(|_| fb.read())).await.map_err(|e| e.to_string())??
                .ok_or("scanout mapping: no consistent frame")?;
            return Ok(Frame { rgb: r.rgb, captured_ms, generation: r.seq, capture_ms: t0.elapsed().as_millis() as u64,
                age_ms: Some(captured_ms.saturating_sub(r.flush_us / 1000)) });
        }
        let (out, code) = self.con.exec("screencap -p | base64 -w 0", Duration::from_secs(30)).await?;
        if code != 0 {
            return Err(format!("screencap exit {code}: {}", out.chars().take(200).collect::<String>()));
        }
        let png = B64.decode(out.split_whitespace().collect::<String>()).map_err(|e| format!("screencap base64: {e}"))?;
        {
            let mut last = self.last.lock().unwrap();
            if last.as_deref() != Some(&png[..]) {
                self.gen.fetch_add(1, Ordering::SeqCst);
                *last = Some(png.clone());
            }
        }
        let rgb = image::load_from_memory_with_format(&png, image::ImageFormat::Png).map_err(|e| e.to_string())?.to_rgb8();
        Ok(Frame { rgb, captured_ms, generation: self.gen.load(Ordering::SeqCst), capture_ms: t0.elapsed().as_millis() as u64, age_ms: None })
    }

    /// Fast path: after an input started at `t_in` (scanout seq `s0` before it), wait for the first new
    /// frame, then until no new frame is posted for `quiet`, or the deadline. Only seq numbers are compared;
    /// the one final frame is captured (and encoded by the caller). Console path: screenshots until two
    /// in a row are identical. Returns (frame, settled, new frames, ms from input to the first new frame).
    pub async fn settled(&self, s0: u64, t_in: Instant, quiet: Duration, deadline: Duration) -> R<(Frame, bool, u32, Option<u64>)> {
        if let Some(fb) = self.fb.get().cloned() {
            let (settled, first) = tokio::task::spawn_blocking(move || wait_quiet(&|| fb.seq(), s0, t_in, quiet, deadline))
                .await.map_err(|e| e.to_string())?;
            let f = self.capture().await?;
            let n = f.generation.saturating_sub(s0) as u32;
            return Ok((f, settled, n, first));
        }
        let (f, settled, n) = self.settled_console(deadline).await?;
        Ok((f, settled, n, None))
    }

    async fn settled_console(&self, deadline: Duration) -> R<(Frame, bool, u32)> {
        let end = Instant::now() + deadline;
        let mut prev = self.capture().await?;
        let mut n = 1;
        while Instant::now() < end {
            let f = self.capture().await?;
            n += 1;
            if f.rgb == prev.rgb {
                return Ok((f, true, n));
            }
            prev = f;
        }
        Ok((prev, false, n))
    }
}

/// Settle rule (issue 10, 2026-10-08): the first frame after `s0`, then `quiet` with no new frame.
/// Polls `seq` every ~1 ms. No frame by the deadline is settled=false.
/// Returns (settled, ms from `t_in` to the first frame after `s0`).
fn wait_quiet(seq: &dyn Fn() -> u64, s0: u64, t_in: Instant, quiet: Duration, deadline: Duration) -> (bool, Option<u64>) {
    let (mut last, mut changed) = (s0, None);
    let mut first = None;
    loop {
        let now = Instant::now();
        let s = seq();
        if s != last {
            last = s;
            changed = Some(now);
            first.get_or_insert((now - t_in).as_millis() as u64);
        }
        if changed.is_some_and(|c| now - c >= quiet) {
            return (true, first);
        }
        if now - t_in >= deadline {
            return (false, first);
        }
        std::thread::sleep(Duration::from_millis(1));
    }
}

async fn kill_tree(pid: Option<u32>) {
    if let Some(pid) = pid {
        let _ = Command::new("taskkill").args(["/F", "/T", "/PID", &pid.to_string()]).creation_flags(CREATE_NO_WINDOW).output().await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reply_parsed_and_echo_ignored() {
        let tag = "abc1";
        let cmd = "getprop x";
        let echo = wrap(cmd, tag).replace('\n', "\r\n");
        assert!(parse_reply(echo.as_bytes(), tag).is_none(), "echo alone must not match");
        let buf = format!("{echo}__ASabc1__\r\nline1\r\nline2\r\n__AEabc1__0\r\nconsole:/ # ");
        assert_eq!(parse_reply(buf.as_bytes(), tag), Some(("line1\nline2".into(), 0)));
        let partial = format!("{echo}__ASabc1__\r\nx\r\n__AEabc1__1");
        assert!(parse_reply(partial.as_bytes(), tag).is_none(), "needs the newline after the exit code");
        assert_eq!(parse_reply(format!("{partial}\r\n").as_bytes(), tag).unwrap().1, 1);
    }

    #[test]
    fn wrap_escapes_quotes() {
        assert!(wrap("echo 'hi'", "t").contains(r"su 0 sh -c 'echo '\''hi'\''' 2>&1"));
    }

    #[test]
    fn keys() {
        assert_eq!(keycode("back").unwrap(), "KEYCODE_BACK");
        assert_eq!(keycode("KEYCODE_HOME").unwrap(), "KEYCODE_HOME");
        assert_eq!(keycode("66").unwrap(), "66");
        assert!(keycode("back; reboot").is_err());
    }

    #[test]
    fn text_and_size() {
        assert_eq!(input_text_cmd("a b'c"), r"input text 'a%sb'\''c'");
        assert_eq!(parse_size("706x1568"), Some((706, 1568)));
        assert_eq!(parse_size("706"), None);
    }

    #[test]
    fn settle_needs_a_frame_then_quiet() {
        let (q, dl) = (Duration::from_millis(33), Duration::from_millis(300));
        let t = Instant::now();
        assert_eq!(wait_quiet(&|| 5, 5, t, q, dl), (false, None), "no new frame: deadline, not settled");
        assert!(t.elapsed() >= dl);
        let t = Instant::now();
        let seq = || if t.elapsed() < Duration::from_millis(50) { 5 } else if t.elapsed() < Duration::from_millis(70) { 6 } else { 7 };
        let (ok, first) = wait_quiet(&seq, 5, t, q, dl);
        assert!(ok && (50..60).contains(&first.unwrap()));
        let e = t.elapsed().as_millis() as u64;
        assert!((103..140).contains(&e), "settles 33 ms after the last frame, took {e} ms");
        let t = Instant::now();
        let busy = || t.elapsed().as_millis() as u64 / 10;
        assert!(!wait_quiet(&busy, 0, t, q, dl).0, "frames keep coming: not settled");
    }

    #[test]
    fn encode_scales() {
        let img = image::RgbImage::from_pixel(720, 1280, image::Rgb([10, 20, 30]));
        let (jpg, w, h, dw, dh, scale) = encode(&img, Some((360, 640))).unwrap();
        assert_eq!((w, h, dw, dh, scale), (360, 640, 720, 1280, 2.0));
        assert_eq!(&jpg[..2], &[0xFF, 0xD8]);
        assert_eq!(encode(&img, None).unwrap().5, 1.0);
    }
}
