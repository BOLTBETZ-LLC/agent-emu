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
const SHARED: &[&str] = &["kernel", "initrd.img", "kernel-dax", "initrd-dax-pmem.img", "initrd-dax-slim.img", "boot.img", "init_boot.img",
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
// SIGSTOP the RIL: it spins ~31% of a vCPU retrying a modem that isn't there, over vsock (~4,900 virtio18
// rx and tx interrupts/s each), which cost the host ~1 core per idle Device (2026-10-08: 1.07 -> 0.04 cores;
// BoltBetz launched after, no ANR or crash). renice alone left the vsock storm. Never `ctl.stop
// vendor.ril-daemon`: com.android.phone then hangs waiting for the radio HAL and ANR-restarts every ~45 s.
const SETUP: &str = "settings put global hide_error_dialogs 1; \
    am broadcast -a android.intent.action.CLOSE_SYSTEM_DIALOGS; setprop log.tag.RIL S; kill -STOP $(pidof libcuttlefish-rild) 2>/dev/null; \
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
    /// crosvm checkout under `work` whose release build boots Devices (AE_CROSVM_DIR): crosvm-pmem, or
    /// crosvm-diet (256-entry vhost-user queues, branch agent-emu-diet).
    pub crosvm_dir: String,
}

impl Cfg {
    pub fn from_env() -> Cfg {
        let e = |k: &str, d: &str| std::env::var(k).unwrap_or_else(|_| d.to_string());
        Cfg {
            work: PathBuf::from(e("AE_WORK", "C:/dev/agent-emu-work")),
            mem: e("AE_MEM", "896"),
            cpus: e("AE_CPUS", "2"),
            min_avail_mb: e("AE_MIN_AVAIL_MB", "1500").parse().unwrap_or(1500),
            crosvm_dir: e("AE_CROSVM_DIR", "crosvm-pmem"),
        }
    }
    /// AE_CROSVM (full path, set by the installed launcher) or the release build under `work/crosvm_dir`.
    pub fn crosvm(&self) -> PathBuf {
        if let Some(p) = std::env::var_os("AE_CROSVM") {
            return PathBuf::from(p);
        }
        self.work.join(&self.crosvm_dir).join("target/release/crosvm.exe")
    }
    /// crosvm built with gfxstream (the GPU worker's branch agent-emu-gpu): AE_CROSVM_GPU or work/crosvm-gpu.
    pub fn crosvm_gpu(&self) -> PathBuf {
        std::env::var_os("AE_CROSVM_GPU").map(PathBuf::from)
            .unwrap_or_else(|| self.work.join("crosvm-gpu/target/release/crosvm.exe"))
    }
}

/// The browser every Device gets (Firefox for Android, x86_64): `<work>/browser/browser.img` is the APK
/// padded to 4 KiB, attached read-only to every Device as one more virtio-blk disk (one host file, shared);
/// browser.size holds the APK's byte count and browser.pkg its package. None when the image is not there.
pub fn browser(work: &Path) -> Option<(PathBuf, u64, String)> {
    let d = work.join("browser");
    let size = std::fs::read_to_string(d.join("browser.size")).ok()?.trim().parse().ok()?;
    let pkg = std::fs::read_to_string(d.join("browser.pkg")).ok()?.trim().to_string();
    let img = d.join("browser.img");
    img.exists().then_some((img, size, pkg))
}

/// Guest command: find the disk whose size is `img_bytes`, stream the APK (`size` bytes) into `pm install`,
/// make `pkg` the default browser (Auth0 and Plaid open Custom Tabs in it). Skips the install when present.
pub fn browser_setup_cmd(img_bytes: u64, size: u64, pkg: &str) -> String {
    format!("if ! pm path {pkg} >/dev/null 2>&1; then dev=; for b in /sys/block/vd*; do \
        [ $(( $(cat $b/size) * 512 )) -eq {img_bytes} ] && dev=/dev/block/${{b##*/}}; done; \
        [ -n \"$dev\" ] || {{ echo no browser disk; exit 1; }}; head -c {size} $dev | pm install -r -S {size} || exit 1; fi; \
        cmd role add-role-holder android.app.role.BROWSER {pkg} 0 && cmd role get-role-holders android.app.role.BROWSER")
}

/// The Android SDK emulator dir whose lib64 holds libgfxstream_backend.dll (AE_SDK_EMULATOR).
pub fn sdk_emulator() -> PathBuf {
    std::env::var_os("AE_SDK_EMULATOR").map(PathBuf::from).unwrap_or_else(|| {
        PathBuf::from(std::env::var_os("LOCALAPPDATA").unwrap_or_default()).join("Android/Sdk/emulator")
    })
}

/// gfxstream rendering (host GPU through ANGLE/Vulkan): crosvm --gpu keys.
const GFX_GPU: &str = "backend=gfxstream,context-types=gfxstream-vulkan:gfxstream-composer,egl=false,gles=false,glx=false,surfaceless=true,vulkan=true";

/// crosvm's PATH for gfxstream: the SDK's gles_angle and lib64 first, then only Windows dirs. Never the
/// inherited PATH: another libGLESv2.dll on it (WezTerm's ANGLE) crashed crosvm.
pub fn gfx_path(emu: &Path) -> String {
    let win = std::env::var("SystemRoot").unwrap_or_else(|_| r"C:\Windows".into());
    let e = emu.to_string_lossy().replace('/', "\\");
    format!(r"{e}\lib64\gles_angle;{e}\lib64;{e};{win}\System32;{win};{win}\System32\Wbem;{win}\System32\WindowsPowerShell\v1.0")
}

/// Copies initrd `src` to `dst` with its bootconfig trailer switched to gfxstream through ANGLE
/// (Cuttlefish gpu_mode=gfxstream_guest_angle) and lcd_density `dpi`. Other keys stay in order.
/// Inserts cpio archive `cpio` into initrd `f` just before its bootconfig trailer (the later archive's
/// files win), each part padded to 512 bytes.
pub fn initrd_overlay(f: &Path, cpio: &Path) -> R<()> {
    let d = std::fs::read(f).map_err(|e| format!("{}: {e}", f.display()))?;
    let c = std::fs::read(cpio).map_err(|e| format!("{}: {e}", cpio.display()))?;
    let n = d.len();
    if !d.ends_with(b"#BOOTCONFIG\n") || n < 20 {
        return Err(format!("{}: no bootconfig trailer", f.display()));
    }
    let size = u32::from_le_bytes(d[n - 20..n - 16].try_into().unwrap()) as usize;
    let cut = n.checked_sub(20 + size).ok_or("bootconfig size past the start of the file")?;
    let mut o = d[..cut].to_vec();
    o.resize(o.len().div_ceil(512) * 512, 0);
    o.extend_from_slice(&c);
    o.resize(o.len().div_ceil(512) * 512, 0);
    o.extend_from_slice(&d[cut..]);
    std::fs::write(f, o).map_err(|e| format!("{}: {e}", f.display()))
}

pub fn gfx_initrd(src: &Path, dst: &Path, dpi: u32) -> R<()> {
    let d = std::fs::read(src).map_err(|e| format!("{}: {e}", src.display()))?;
    const MAGIC: &[u8] = b"#BOOTCONFIG\n";
    if !d.ends_with(MAGIC) || d.len() < 20 {
        return Err(format!("{}: no bootconfig trailer", src.display()));
    }
    let n = d.len();
    let size = u32::from_le_bytes(d[n - 20..n - 16].try_into().unwrap()) as usize;
    let body = d.get(n - 20 - size..n - 20).ok_or("bootconfig size past the start of the file")?;
    let want = [("androidboot.hardware.egl", "angle".to_string()), ("androidboot.hardware.vulkan", "ranchu".to_string()),
        ("androidboot.hardware.gltransport", "virtio-gpu-asg".to_string()), ("androidboot.cpuvulkan.version", "0".to_string()),
        ("androidboot.opengles.version", "196609".to_string()),
        ("androidboot.lcd_density", dpi.to_string())];
    let text = String::from_utf8_lossy(body);
    let mut out: Vec<String> = text.trim_end_matches('\0').lines().filter(|l| !l.is_empty())
        .filter(|l| !want.iter().any(|(k, _)| l.split('=').next().map(str::trim) == Some(*k))).map(str::to_string).collect();
    out.extend(want.iter().map(|(k, v)| format!("{k}={v}")));
    let mut bc = (out.join("\n") + "\n").into_bytes();
    bc.resize(bc.len().div_ceil(4) * 4, 0);
    let sum = bc.iter().fold(0u32, |a, &b| a.wrapping_add(b as u32));
    let mut f = d[..n - 20 - size].to_vec();
    f.extend_from_slice(&bc);
    f.extend_from_slice(&(bc.len() as u32).to_le_bytes());
    f.extend_from_slice(&sum.to_le_bytes());
    f.extend_from_slice(MAGIC);
    let _ = std::fs::remove_file(dst); // a hard link from another image must not be written through
    std::fs::write(dst, f).map_err(|e| format!("{}: {e}", dst.display()))
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

// Guest diet (issue 13, lever C), on every Device. Kernel: one block queue per disk with tag depth 64
// (the vhost-user queues were ~180 MB of slab), no THP (min_free_kbytes 22528 -> 3603), no KFENCE pool.
// crosvm: one virtio-snd for the GPU instead of one per display, console sinks only up to num=11
// (hvc10 must stay: the oemlock HAL waits on it forever).
/// daemon/boot-device.ps1 next to this checkout (exe is daemon/target/<profile>/agent-emud.exe), or AE_BOOT_SCRIPT.
fn boot_script() -> PathBuf {
    std::env::var_os("AE_BOOT_SCRIPT").map(PathBuf::from).unwrap_or_else(|| {
        let exe = std::env::current_exe().unwrap_or_default();
        exe.ancestors().nth(3).map(|d| d.join("boot-device.ps1")).unwrap_or_else(|| PathBuf::from("boot-device.ps1"))
    })
}

const PHONE_NET_FROM: &str =
    "ip link set buried_eth0 up; ip addr add 10.0.2.15/24 dev buried_eth0; ip route add 10.0.2.0/24 dev buried_eth0 table legacy_system";
const PHONE_NET_TO: &str =
    "ip link set buried_eth0 down; ip link set buried_eth0 name eth0; ip link set eth0 up; sleep 6; ip addr add 10.0.2.15/24 dev eth0";

pub const DIET_PARAMS: &str = "virtio_blk.num_request_queues=1 virtio_blk.queue_depth=64 transparent_hugepage=never kfence.sample_interval=0";
const DIET_SINKS: &str = "11";
const DIET_GPU: &str = "audio-device-mode=one-global";

/// The stock phone image boots like stage 1 (stock kernel and initrd, system in super) and keeps the network.
pub fn is_phone(name: &str) -> bool {
    name == "phone" || name == "phone-n"
}

/// Guest image: (run dir under AE_WORK holding super.img + system-pmem.img, default guest MB or None = AE_MEM).
pub fn image(name: &str) -> R<(&'static str, Option<&'static str>)> {
    match name {
        "slim3" => Ok(("run", None)),
        // slim3n (default): slim3 minus the Cuttlefish NFC HAL apex. Its panic left com.android.nfc
        // ANR-restarting every ~45-60 s; 15 min soak on slim3n: 0 ANRs (issue 10).
        "" | "slim3n" => Ok(("run-slim3n", None)),
        // phone: the stock Cuttlefish 15581820 image (SystemUI, Launcher3, Settings, wallpaper) for people.
        // Stock kernel and initrd, system on the super block device (no pmem), 2 GB.
        "phone" => Ok(("run-full", Some("2048"))),
        // phone-n: the phone minus the Cuttlefish NFC and Thread network HAL apexes, which crash-loop with
        // no host simulator (com.android.nfc ANR every ~45 s, vendor.threadnetwork_hal restarts).
        // Built by agent-emu-work/stage2/phone-n/build.sh; run-phone-n = run-full with its super and vbmeta.
        // 10-minute soak 2026-10-08: no crash, ANR or init restart loop (with Bluetooth off, see wait_boot).
        "phone-n" => Ok(("run-phone-n", Some("2048"))),
        // slim4: the diet floor was 640 MB without the daemon's extra devices (virtio-net for adb, two
        // virtio-input, fb). Through the daemon at 640, lmkd killed the app as TOP 3 of 3 times (low on
        // swap, thrashing); at 704 it stays up. `start {mem}` overrides.
        "slim4" => Ok(("run-slim4", Some("704"))),
        // slim5: slim4 plus a home stub and tuned lmkd. 768 MB: at native 1320x2868 with gfxstream, lmkd killed
        // BoltBetz at 640 even with the no-backing virtio-gpu module (issue 13, GPU native-res RAM).
        "slim5" => Ok(("run-slim5", Some("768"))),
        _ => Err(format!("unknown image `{name}` (phone, phone-n, slim3, slim3n, slim4, slim5)")),
    }
}

/// Guest display (width px, height px, density dpi) for screen profile `name`.
/// "" | "iphone17promax" (default): Apple's iPhone 17 Pro Max, 6.9", "2868-by-1320-pixel resolution at 460 ppi"
/// (support.apple.com/en-us/125091, read 2026-10-08), so 440 x 956 points at 3x. Density makes the Android layout the same
/// 440 dp wide: native pixels (1320 / 480 dpi * 160 = 440 dp). "-half" is 656 x 1424 at 238 dpi = 441 x 957 dp
/// (crosvm rounds the width down to a multiple of 8, so not 660).
/// "legacy" (or "small"): the old 720 x 1080 at 320 dpi (360 dp wide).
/// "-native" / "-3q" / "-half" pick 1, 3/4 or 1/2 of the native pixels, all at the same 440 dp layout.
pub fn screen(name: &str, phone: bool) -> R<(u32, u32, u32)> {
    match (name, phone) {
        ("" | "iphone17promax", true) => screen("iphone17promax-native", true),
        // Lean images too: gfxstream draws native pixels on the host GPU (slim5 at 768 MB, 630 MB host, issue 13).
        ("" | "iphone17promax", false) => screen("iphone17promax-native", false),
        ("iphone17promax-native", _) => Ok((1320, 2868, 480)),
        ("iphone17promax-3q", _) => Ok((984, 2140, 358)),
        ("iphone17promax-half", _) => Ok((656, 1424, 238)),
        ("legacy" | "small", _) => Ok((720, 1080, 320)),
        _ => Err(format!("unknown screen `{name}` (iphone17promax[-native|-3q|-half], legacy)")),
    }
}

fn make_device(cfg: &Cfg, idx: u32, run: &str) -> R<PathBuf> {
    let run = cfg.work.join(run);
    let d = cfg.work.join(format!("fleet/d{idx}"));
    std::fs::create_dir_all(&d).map_err(|e| e.to_string())?;
    // Relink every start: the Device dir may hold links into another image's run dir.
    for f in SHARED {
        // A link can't be deleted while any process (another Device) has the file open; then keep it if it
        // already is this image's file (same size and mtime: all links share one file record).
        if std::fs::remove_file(d.join(f)).is_err() && d.join(f).exists() {
            let meta = |p: PathBuf| std::fs::metadata(p).ok().map(|m| (m.len(), m.modified().ok()));
            if meta(d.join(f)) == meta(run.join(f)) {
                continue;
            }
            return Err(format!("{f}: an old link is in use and is not this image's file"));
        }
        if run.join(f).exists() {
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
    encode_q(rgb, size, 75)
}

/// `encode` at JPEG quality `q` (1-100).
pub fn encode_q(rgb: &image::RgbImage, size: Option<(u32, u32)>, q: u8) -> R<(Vec<u8>, u32, u32, u32, u32, f64)> {
    let (dw, dh) = rgb.dimensions();
    let scaled;
    let img = match size {
        Some((w, h)) if w < dw || h < dh => {
            // Fit inside (w, h), aspect kept; thumbnail (box filter) is ~1.5x faster than a Triangle resize.
            let r = (w as f64 / dw as f64).min(h as f64 / dh as f64);
            scaled = image::imageops::thumbnail(rgb, ((dw as f64 * r).round() as u32).max(1), ((dh as f64 * r).round() as u32).max(1));
            &scaled
        }
        _ => rgb,
    };
    let mut out = Vec::new();
    // jpeg-encoder with AVX2: about 3x faster than image's encoder on this PC.
    jpeg_encoder::Encoder::new(&mut out, q.clamp(1, 100))
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
    /// Booted with virtio-net, so adb answers on 127.0.0.1:6520+idx.
    pub net: bool,
    /// The full phone image: setup leaves airplane mode off.
    pub phone: bool,
    /// Android density (dpi) for the screen profile, set by `wm density` after boot.
    pub density: u32,
    /// The crosvm binary this Device runs (gfxstream or software build); squeeze talks to it.
    pub crosvm: PathBuf,
    /// (browser.img bytes, APK bytes, package) when the browser disk is attached.
    pub browser: Option<(u64, u64, String)>,
    /// Browser headroom (MB): extra guest RAM held in the balloon while the app is in front, given back while a
    /// browser (Auth0, Plaid Custom Tabs) is. 0 = none.
    pub headroom_mb: AtomicU64,
    /// The headroom is in the balloon now.
    pub headroom_on: AtomicBool,
    /// Balloon MB that `squeeze` set, on top of the headroom.
    pub balloon_base_mb: AtomicU64,
}

impl Device {
    /// Check the host, build the Device dir, spawn boot-stage1.ps1 and open the console pipe.
    /// `net: false` boots without virtio-net (so without adb); only the console, fast input and frame
    /// paths remain. Untested.
    /// `others_ok`: skip the "no other crosvm running" check (the caller already runs Devices).
    /// `gfx`: render with gfxstream on the host GPU; false = crosvm's 2D software renderer.
    /// `refresh_hz`: the guest display's refresh rate.
    pub async fn spawn(cfg: &Cfg, idx: u32, image_name: &str, screen_name: &str, mem: Option<&str>, cpus: Option<&str>, net: bool, others_ok: bool, gfx: bool, refresh_hz: u32) -> R<Arc<Device>> {
        let (run, image_mem) = image(image_name)?;
        let (sw, sh, dpi) = screen(screen_name, is_phone(image_name))?;
        let mem = mem.or(image_mem).unwrap_or(&cfg.mem).to_string();
        let avail = available_mb();
        if avail < cfg.min_avail_mb {
            return Err(format!("host Available {avail} MB < {} MB; not booting", cfg.min_avail_mb));
        }
        // AE_ALLOW_OTHER_CROSVM=1: another worker's Device may run beside this one (distinct id/pipes/ports).
        if !others_ok && std::env::var_os("AE_ALLOW_OTHER_CROSVM").is_none() && crosvm_running().await {
            return Err("a crosvm.exe is already running; one Device at a time".into());
        }
        let dir = make_device(cfg, idx, run)?;
        let pmem = cfg.work.join(run).join("system-pmem.img");
        let initrd = if is_phone(image_name) { "initrd.img" } else { "initrd-dax-pmem.img" };
        let mut cmd = Command::new("powershell");
        let browser = browser(&cfg.work).and_then(|(img, size, pkg)| {
            cmd.env("AE_BROWSER_IMG", win(&img));
            Some((std::fs::metadata(&img).ok()?.len(), size, pkg))
        });
        let crosvm = if gfx {
            let (exe, emu) = (cfg.crosvm_gpu(), sdk_emulator());
            for f in [exe.clone(), emu.join("lib64/libgfxstream_backend.dll")] {
                if !f.exists() {
                    return Err(format!("gfxstream rendering needs {}; start with render: \"software\" to use the CPU", f.display()));
                }
            }
            gfx_initrd(&dir.join(initrd), &dir.join("initrd-gfx.img"), dpi)?;
            if !is_phone(image_name) {
                // DAX-kernel images: virtio-gpu that skips guest pages for GPU-only buffers (GPU-INTEGRATION.md).
                let kmod = cfg.work.join("gpu/kmod/virtio-gpu-nobacking.cpio");
                if kmod.exists() {
                    initrd_overlay(&dir.join("initrd-gfx.img"), &kmod)?;
                }
            }
            cmd.env("AE_PATH", gfx_path(&emu)).env("AE_GPU_BACKEND", GFX_GPU).env("AE_INITRD", "initrd-gfx.img");
            exe
        } else {
            cmd.env("AE_INITRD", initrd);
            cfg.crosvm()
        };
        if net {
            // slirp forwards 127.0.0.1:6520+N to adbd at 10.0.2.15:5555 (SETUP gives the guest NIC that address).
            cmd.env("AGENT_EMU_ADB_PORT", (6520 + idx).to_string());
        } else {
            cmd.env("AGENT_EMU_NO_NET", "1").env_remove("AGENT_EMU_ADB_PORT");
        }
        let child = cmd
            .args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]).arg(boot_script())
            .env("AE_PARAMS", DIET_PARAMS).env("AE_SINKS", DIET_SINKS).env("AE_GPU_EXTRA", DIET_GPU)
            .env("AE_DIR", win(&dir)).env("AE_ID", idx.to_string()).env("AE_MEM", &mem).env("AE_CPUS", cpus.unwrap_or(&cfg.cpus))
            .env("AE_DISPLAY", format!("{sw},{sh}")).env("AE_DPI", dpi.to_string()).env("AE_REFRESH", refresh_hz.to_string())
            .env("AE_EXTRA", format!("--socket PIPE:ae-vm-{idx}{} --input multi-touch[path={}] --input keyboard[path={}]",
                if is_phone(image_name) { String::new() } else { format!(" --pmem path={},ro=true", pmem.to_string_lossy().replace('\\', "/")) },
                fast::touch_pipe(idx), fast::kbd_pipe(idx)))
            // No desktop window: crosvm's 2D GPU uses the stub display; frames still come from fb.bin.
            .env("AGENT_EMU_HEADLESS", "1")
            .env("AGENT_EMU_FB", win(&dir.join("fb.bin"))).env("AGENT_EMU_FB_PIPE", fast::fb_pipe(idx))
            .env("AE_KERNEL", if is_phone(image_name) { "kernel" } else { "kernel-dax" })
            .env("AE_CROSVM", win(&crosvm))
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
            idx, input: OnceLock::new(), fb: OnceLock::new(), events: crate::logs::Events::spawn(dir.join("logcat.log")), net, phone: is_phone(image_name), density: dpi, crosvm, browser,
            headroom_mb: AtomicU64::new(0), headroom_on: AtomicBool::new(false), balloon_base_mb: AtomicU64::new(0),
        }))
    }

    /// Poll until sys.boot_completed and dev.bootcomplete, then apply the v1 Device settings.
    /// Aborts when host Available memory falls under `min_avail_mb` while booting.
    /// `phase` hears "android" (the guest shell answers), "setup" (boot completed) and nothing else.
    pub async fn wait_boot(&self, limit: Duration, min_avail_mb: u64, phase: &(dyn Fn(&'static str) + Sync)) -> R<()> {
        let mut shell = false;
        let end = Instant::now() + limit;
        loop {
            let avail = available_mb();
            if avail < min_avail_mb {
                return Err(format!("host Available {avail} MB < {min_avail_mb} MB during boot; stopped"));
            }
            if let Ok((o, _)) = self.con.exec("getprop sys.boot_completed; getprop dev.bootcomplete", Duration::from_secs(20)).await {
                if !shell {
                    shell = true;
                    phase("android");
                }
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
        // The phone image is for people: keep its network (agents' lean images run in airplane mode).
        // Cuttlefish hides its ethernet ("buried_eth0") and expects Wi-Fi from a host simulator that is not
        // here, so Android had no default network. Renamed eth0, Android's Ethernet service takes it and
        // gets DHCP from slirp; 10.0.2.15 stays on it because slirp forwards adb to that address.
        // Bluetooth has no host peer (no rootcanal): AdapterService aborts at StartEverything about once a
        // minute while Bluetooth is on. Airplane mode keeps it off on the lean images; the phone turns it off.
        let setup = if self.phone {
            SETUP.replace("cmd connectivity airplane-mode enable; ", "cmd bluetooth_manager disable; ").replace(PHONE_NET_FROM, PHONE_NET_TO)
        } else {
            SETUP.to_string()
        };
        // The image's bootconfig fixes androidboot.lcd_density=320; the screen profile's density wins here.
        let setup = format!("wm density {}; {setup}", self.density);
        phase("setup");
        self.con.exec(&setup, Duration::from_secs(60)).await?;
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
    /// Starts timing the first frame after `s0` before the input is sent, so a frame drawn while a long
    /// input (a swipe) is still being written counts from the input's start, not its end. None without
    /// the scanout mapping.
    pub fn first_frame_watch(&self, s0: u64, t_in: Instant, deadline: Duration) -> Option<tokio::task::JoinHandle<Option<u64>>> {
        let fb = self.fb.get()?.clone();
        Some(tokio::task::spawn_blocking(move || first_change(&|| fb.seq(), s0, t_in, deadline)))
    }

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

    /// The frame crosvm last posted to the scanout mapping, without asking it to refresh first (streams call
    /// this after the seq moved, so the mapping already holds that frame). Falls back to `capture`.
    pub async fn capture_posted(&self) -> R<Frame> {
        let Some(fb) = self.fb.get().cloned() else { return self.capture().await };
        let (t0, captured_ms) = (Instant::now(), now_ms());
        let r = tokio::task::spawn_blocking(move || fb.read()).await.map_err(|e| e.to_string())?
            .ok_or("scanout mapping: no consistent frame")?;
        Ok(Frame { rgb: r.rgb, captured_ms, generation: r.seq, capture_ms: t0.elapsed().as_millis() as u64,
            age_ms: Some(captured_ms.saturating_sub(r.flush_us / 1000)) })
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
/// ms from `t_in` to the first frame after `s0`, polling every ~0.5 ms; None by the deadline.
fn first_change(seq: &dyn Fn() -> u64, s0: u64, t_in: Instant, deadline: Duration) -> Option<u64> {
    loop {
        if seq() != s0 {
            return Some(t_in.elapsed().as_millis() as u64);
        }
        if t_in.elapsed() >= deadline {
            return None;
        }
        std::thread::sleep(Duration::from_micros(500));
    }
}

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
    fn browser_setup() {
        let c = browser_setup_cmd(139436032, 139432402, "org.mozilla.firefox");
        assert!(c.contains("-eq 139436032 ] && dev=/dev/block/${b##*/}") && c.contains("head -c 139432402 $dev | pm install -r -S 139432402"));
        assert!(c.contains("cmd role add-role-holder android.app.role.BROWSER org.mozilla.firefox 0"));
        let w = std::env::temp_dir().join(format!("ae-br-{}", std::process::id()));
        assert!(browser(&w).is_none());
        std::fs::create_dir_all(w.join("browser")).unwrap();
        for (f, v) in [("browser.size", "12\n"), ("browser.pkg", "org.x\n"), ("browser.img", "x")] {
            std::fs::write(w.join("browser").join(f), v).unwrap();
        }
        assert_eq!(browser(&w).map(|b| (b.1, b.2)), Some((12, "org.x".to_string())));
        std::fs::remove_dir_all(&w).ok();
    }

    #[test]
    fn gfx_initrd_rewrites_bootconfig() {
        let t = std::env::temp_dir().join(format!("ae-gfx-{}", std::process::id()));
        let bc = b"androidboot.hardware.vulkan=pastel\nandroidboot.lcd_density=320\nandroidboot.x=1\n\0\0";
        let sum = bc.iter().fold(0u32, |a, &b| a + b as u32);
        let mut f = b"RAMDISK".to_vec();
        f.extend_from_slice(bc);
        f.extend_from_slice(&(bc.len() as u32).to_le_bytes());
        f.extend_from_slice(&sum.to_le_bytes());
        f.extend_from_slice(b"#BOOTCONFIG\n");
        std::fs::write(t.with_extension("in"), &f).unwrap();
        gfx_initrd(&t.with_extension("in"), &t.with_extension("out"), 238).unwrap();
        let o = std::fs::read(t.with_extension("out")).unwrap();
        let n = o.len();
        let size = u32::from_le_bytes(o[n - 20..n - 16].try_into().unwrap()) as usize;
        let body = &o[n - 20 - size..n - 20];
        assert_eq!(u32::from_le_bytes(o[n - 16..n - 12].try_into().unwrap()), body.iter().fold(0u32, |a, &b| a + b as u32));
        assert!(o.starts_with(b"RAMDISK") && size % 4 == 0);
        assert_eq!(String::from_utf8_lossy(body).trim_end_matches('\0'), "androidboot.x=1\nandroidboot.hardware.egl=angle\n\
            androidboot.hardware.vulkan=ranchu\nandroidboot.hardware.gltransport=virtio-gpu-asg\nandroidboot.cpuvulkan.version=0\n\
            androidboot.opengles.version=196609\nandroidboot.lcd_density=238\n");
        assert!(gfx_initrd(&t.with_extension("out2"), &t.with_extension("x"), 1).is_err());
        std::fs::write(t.with_extension("cpio"), b"070701CPIO").unwrap();
        initrd_overlay(&t.with_extension("out"), &t.with_extension("cpio")).unwrap();
        let v = std::fs::read(t.with_extension("out")).unwrap();
        assert!(v.ends_with(&o[n - 20 - size..]) && v.windows(10).any(|w| w == b"070701CPIO"), "cpio before the bootconfig");
        assert_eq!(v.iter().position(|&b| b == b'0').unwrap() % 512, 0);
        for e in ["in", "out", "cpio"] { std::fs::remove_file(t.with_extension(e)).ok(); }
        assert!(gfx_path(Path::new("E:/emu")).starts_with(r"E:\emu\lib64\gles_angle;E:\emu\lib64;E:\emu;"));
    }

    #[test]
    fn screens_lay_out_440_dp_wide() {
        for (name, phone) in [("", true), ("", false), ("iphone17promax-3q", true), ("iphone17promax-half", true)] {
            let (w, h, dpi) = screen(name, phone).unwrap();
            assert_eq!(w % 8, 0, "crosvm rounds the width down to a multiple of 8");
            assert!((w * 160 / dpi).abs_diff(440) <= 1 && (h * 160 / dpi).abs_diff(956) <= 1, "{w}x{h}@{dpi}");
        }
        assert_eq!(screen("small", true).unwrap(), (720, 1080, 320));
        assert_eq!(screen("legacy", false).unwrap(), (720, 1080, 320));
        assert!(screen("tablet", true).is_err());
    }

    #[test]
    fn images() {
        assert_eq!(image("slim4").unwrap(), ("run-slim4", Some("704")));
        assert_eq!(image("").unwrap(), ("run-slim3n", None));
        assert_eq!(image("slim3").unwrap(), ("run", None));
        assert_eq!(image("slim5").unwrap(), ("run-slim5", Some("768")));
        assert!(image("slim9").is_err());
        assert_eq!(image("phone").unwrap(), ("run-full", Some("2048")));
        assert_eq!(image("phone-n").unwrap(), ("run-phone-n", Some("2048")));
        assert!(is_phone("phone") && is_phone("phone-n") && !is_phone("") && SETUP.contains("cmd connectivity airplane-mode enable; "));
        assert!(SETUP.contains(PHONE_NET_FROM), "the phone network swap must match SETUP");
        assert!(SETUP.contains("kill -STOP $(pidof libcuttlefish-rild)"), "an idle Device must not spin on the RIL");
        let cfg = Cfg { work: PathBuf::from("W"), mem: "896".into(), cpus: "2".into(), min_avail_mb: 4000, crosvm_dir: "crosvm-diet".into() };
        assert_eq!(cfg.crosvm(), PathBuf::from("W/crosvm-diet/target/release/crosvm.exe"));
        assert!(DIET_PARAMS.contains("virtio_blk.num_request_queues=1") && DIET_PARAMS.contains("transparent_hugepage=never"));
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
    fn first_change_times_from_the_input() {
        let t = Instant::now();
        let seq = || if t.elapsed() < Duration::from_millis(40) { 9 } else { 10 };
        let ms = first_change(&seq, 9, t, Duration::from_millis(500)).unwrap();
        assert!((40..55).contains(&ms), "{ms}");
        assert_eq!(first_change(&|| 9, 9, Instant::now(), Duration::from_millis(30)), None);
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

