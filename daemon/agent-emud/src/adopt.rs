//! Phones that outlive the daemon. A Device's crosvm runs under its own boot powershell (detached: new process
//! group, out of the daemon's job), and the daemon parks a copy of each pipe handle it holds to the phone
//! (guest console, touch, keyboard) inside that powershell. crosvm accepts one client per pipe and never takes a
//! second one (its console reader stops on a broken pipe), so the parked copy keeps the pipe connected while no
//! daemon runs. `fleet/dN/adopt.json` records the keeper (pid + start time), the parked handles and what `start`
//! asked for. A new daemon on the same agent address takes the handles back and lists the phone as ready.
//!
//! `restart` (`exe` optional: the build to run next) and `quit` with `keep_devices: true` exit without stopping
//! phones. Plain `quit`, `stop` and failed boots stop them and delete adopt.json (Device::stop).
use crate::device::{self, Device, R};
use crate::{emit, squeeze, Slot, State};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex as StdMutex, OnceLock};
use std::time::{Duration, Instant};
use tokio::net::windows::named_pipe::NamedPipeClient;

const FILE: &str = "adopt.json";
/// This daemon's agent address (set in serve): adopt.json records it, so only a daemon on the same address
/// adopts (test daemons on other ports share the fleet dir).
pub static ADDR: OnceLock<String> = OnceLock::new();

pub fn addr() -> &'static str {
    ADDR.get().map(String::as_str).unwrap_or("127.0.0.1:7400")
}
/// Spawn flags that keep a child alive when the daemon exits: its own process group (no Ctrl+C from the
/// daemon's console) and out of the daemon's job (a kill-on-close job would take the phones with it).
pub const DETACH: u32 = 0x0000_0200 | 0x0100_0000; // CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB

const PROCESS_DUP_HANDLE: u32 = 0x0040;
const PROCESS_QUERY_LIMITED_INFORMATION: u32 = 0x1000;
const SYNCHRONIZE: u32 = 0x0010_0000;
const DUPLICATE_SAME_ACCESS: u32 = 2;
const STILL_ACTIVE: u32 = 259;

#[repr(C)]
struct IoStatus { status: isize, info: usize }
#[repr(C)]
struct CompletionInfo { port: isize, key: usize }

extern "system" {
    fn OpenProcess(access: u32, inherit: i32, pid: u32) -> isize;
    fn CloseHandle(h: isize) -> i32;
    fn GetCurrentProcess() -> isize;
    fn DuplicateHandle(sp: isize, sh: isize, tp: isize, th: *mut isize, access: u32, inherit: i32, opts: u32) -> i32;
    fn GetProcessTimes(h: isize, create: *mut u64, exit: *mut u64, kernel: *mut u64, user: *mut u64) -> i32;
    fn GetExitCodeProcess(h: isize, code: *mut u32) -> i32;
    fn WaitForSingleObject(h: isize, ms: u32) -> u32;
    fn NtSetInformationFile(h: isize, iosb: *mut IoStatus, info: *mut CompletionInfo, len: u32, class: u32) -> i32;
}

/// Creation time (FILETIME ticks) of a running `pid`; None when it is not running. (pid, start) names one
/// process: a reused pid has another start.
pub fn proc_start(pid: u32) -> Option<u64> {
    // SAFETY: plain Win32 calls on a handle opened and closed here.
    unsafe {
        let h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if h == 0 {
            return None;
        }
        let (mut c, mut e, mut k, mut u, mut code) = (0u64, 0u64, 0u64, 0u64, 0u32);
        let ok = GetProcessTimes(h, &mut c, &mut e, &mut k, &mut u) != 0 && GetExitCodeProcess(h, &mut code) != 0;
        CloseHandle(h);
        (ok && code == STILL_ACTIVE).then_some(c)
    }
}

pub fn alive(pid: u32, start: u64) -> bool {
    proc_start(pid) == Some(start)
}

/// Waits up to `limit` for process `pid` to exit (the daemon `restart` replaces).
pub fn wait_exit(pid: u32, limit: Duration) {
    // SAFETY: handle opened and closed here; 0 = already gone.
    unsafe {
        let h = OpenProcess(SYNCHRONIZE, 0, pid);
        if h != 0 {
            WaitForSingleObject(h, limit.as_millis() as u32);
            CloseHandle(h);
        }
    }
}

/// Copies our handle `h` into process `pid`; returns its value there. The copy keeps the pipe open after we exit.
fn park(pid: u32, h: isize) -> R<isize> {
    // SAFETY: Win32 calls; the target handle lives in the other process until it exits.
    unsafe {
        let p = OpenProcess(PROCESS_DUP_HANDLE, 0, pid);
        if p == 0 {
            return Err(format!("open keeper {pid}: {}", std::io::Error::last_os_error()));
        }
        let mut out = 0isize;
        let ok = DuplicateHandle(GetCurrentProcess(), h, p, &mut out, 0, 0, DUPLICATE_SAME_ACCESS) != 0;
        let err = std::io::Error::last_os_error();
        CloseHandle(p);
        if ok { Ok(out) } else { Err(format!("park handle in {pid}: {err}")) }
    }
}

/// Copies handle `h` of process `pid` into this one and frees it from the dead daemon's I/O completion port (a
/// file object belongs to one port for life unless replaced, so tokio could not register it otherwise).
fn fetch(pid: u32, h: isize) -> R<isize> {
    // SAFETY: Win32/NT calls on handles opened here; `out` is ours and goes to tokio on success.
    unsafe {
        let p = OpenProcess(PROCESS_DUP_HANDLE, 0, pid);
        if p == 0 {
            return Err(format!("open keeper {pid}: {}", std::io::Error::last_os_error()));
        }
        let mut out = 0isize;
        let ok = DuplicateHandle(p, h, GetCurrentProcess(), &mut out, 0, 0, DUPLICATE_SAME_ACCESS) != 0;
        let err = std::io::Error::last_os_error();
        CloseHandle(p);
        if !ok {
            return Err(format!("fetch handle {h:#x} from {pid}: {err}"));
        }
        unbind_port(out).map(|_| out).inspect_err(|_| { CloseHandle(out); })
    }
}

/// Removes `h`'s file object from its I/O completion port (FileReplaceCompletionInformation, Windows 8.1+).
fn unbind_port(h: isize) -> R<()> {
    let (mut io, mut ci) = (IoStatus { status: 0, info: 0 }, CompletionInfo { port: 0, key: 0 });
    // SAFETY: valid handle, buffers sized for the class.
    let s = unsafe { NtSetInformationFile(h, &mut io, &mut ci, std::mem::size_of::<CompletionInfo>() as u32, 61) };
    if s == 0 { Ok(()) } else { Err(format!("unbind completion port: NTSTATUS {s:#x}")) }
}

/// Our pipe as a tokio client. Safety: `h` is an overlapped named-pipe client handle owned by the caller.
unsafe fn client(h: isize) -> R<NamedPipeClient> {
    NamedPipeClient::from_raw_handle(h as _).map_err(|e| format!("register pipe: {e}"))
}

pub fn path(dir: &Path) -> PathBuf {
    dir.join(FILE)
}

fn me() -> (u32, u64) {
    let p = std::process::id();
    (p, proc_start(p).unwrap_or(0))
}

/// Records Device `id` (just ready) so a later daemon can adopt it: parks its pipe handles in the boot
/// powershell (first time only) and writes adopt.json. `req` is the `start` request.
pub async fn save(st: &State, id: &str, req: Option<&Value>, addr: &str) -> R<()> {
    let (d, info, started_ms) = {
        let devs = st.devs.lock().unwrap();
        let s = devs.get(id).ok_or(format!("no Device `{id}`"))?;
        (s.dev.clone(), s.info.clone(), device::now_ms().saturating_sub(s.started.elapsed().as_millis() as u64))
    };
    let f = path(&d.dir);
    let old: Value = std::fs::read_to_string(&f).ok().and_then(|s| serde_json::from_str(&s).ok()).unwrap_or(Value::Null);
    let keeper = d.boot_pid().await.ok_or("Device has no boot process")?;
    let kstart = proc_start(keeper).ok_or("boot process is gone")?;
    let handles = if old["keeper"]["pid"] == json!(keeper) && old["keeper"]["start"] == json!(kstart) && old["handles"].is_object() {
        old["handles"].clone()
    } else {
        let input = d.input.get().map(|i| i.raw);
        json!({"con": park(keeper, d.con.raw)?,
            "touch": input.map(|r| park(keeper, r[0])).transpose()?, "kbd": input.map(|r| park(keeper, r[1])).transpose()?})
    };
    let (pid, start) = me();
    let v = json!({"id": id, "idx": d.idx(), "addr": addr, "daemon": {"pid": pid, "start": start},
        "keeper": {"pid": keeper, "start": kstart}, "handles": handles,
        "req": req.cloned().unwrap_or(old["req"].clone()), "info": info, "started_ms": started_ms,
        "net": d.net, "phone": d.phone, "density": d.density, "crosvm": d.crosvm, "browser": d.browser,
        "headroom_mb": d.headroom_mb.load(Ordering::SeqCst), "headroom_in_mb": d.headroom_in_mb.load(Ordering::SeqCst),
        "balloon_base_mb": d.balloon_base_mb.load(Ordering::SeqCst), "caps": *d.caps.lock().unwrap()});
    std::fs::write(&f, serde_json::to_vec_pretty(&v).unwrap()).map_err(|e| format!("{}: {e}", f.display()))
}

/// Why `v` (an adopt.json) cannot be adopted by this daemon on `addr`, or None. `stale` = the phone is gone
/// (delete the file); otherwise it belongs to someone else (leave it).
pub fn check(v: &Value, addr: &str) -> Option<(bool, String)> {
    let pid = |k: &str| v[k]["pid"].as_u64().map(|p| p as u32);
    let start = |k: &str| v[k]["start"].as_u64().unwrap_or(0);
    let (Some(kp), Some(dp)) = (pid("keeper"), pid("daemon")) else { return Some((true, "bad adopt.json".into())) };
    if !alive(kp, start("keeper")) {
        return Some((true, format!("boot process {kp} is gone (phone stopped or pid reused)")));
    }
    if v["addr"].as_str() != Some(addr) {
        return Some((false, format!("belongs to the daemon on {}", v["addr"])));
    }
    if dp != std::process::id() && alive(dp, start("daemon")) {
        return Some((false, format!("daemon {dp} still runs it")));
    }
    None
}

/// At startup: adopt every running phone a previous daemon on `addr` left in the fleet dir.
pub async fn adopt_all(st: &Arc<State>, addr: &str) {
    let Ok(rd) = std::fs::read_dir(st.cfg.work.join("fleet")) else { return };
    for e in rd.flatten() {
        let f = e.path().join(FILE);
        let Ok(s) = std::fs::read_to_string(&f) else { continue };
        let v: Value = serde_json::from_str(&s).unwrap_or(Value::Null);
        let id = v["id"].as_str().unwrap_or("?").to_string();
        match check(&v, addr) {
            Some((true, why)) => {
                eprintln!("adopt {id}: {why}; removing {}", f.display());
                let _ = std::fs::remove_file(&f);
            }
            Some((false, why)) => eprintln!("adopt {id}: skipped, {why}"),
            None => match adopt_one(st, &e.path(), &v).await {
                Ok(()) => {
                    eprintln!("adopted {id}");
                    if let Err(e) = save(st, &id, None, addr).await { eprintln!("adopt {id}: save: {e}"); }
                }
                Err(err) => {
                    // The phone runs but this daemon cannot reach it: leave it for a person, say so.
                    eprintln!("adopt {id}: {err}");
                    emit(st, json!({"type": "error", "device": id, "call": "adopt", "error": err}));
                }
            },
        }
    }
}

async fn adopt_one(st: &Arc<State>, dir: &Path, v: &Value) -> R<()> {
    let id = v["id"].as_str().ok_or("no id")?.to_string();
    let idx = v["idx"].as_u64().ok_or("no idx")? as u32;
    let kp = v["keeper"]["pid"].as_u64().ok_or("no keeper")? as u32;
    let ks = v["keeper"]["start"].as_u64().unwrap_or(0);
    let h = |k: &str| v["handles"][k].as_i64().map(|x| x as isize);
    let con = fetch(kp, h("con").ok_or("no console handle")?)?;
    // SAFETY: `con` is our fresh overlapped client handle from fetch.
    let con = device::Console::from_pipe(unsafe { client(con)? }, dir.join("console.log"));
    con.exec("true", Duration::from_secs(10)).await.map_err(|e| format!("console does not answer: {e}"))?;
    let input = match (h("touch"), h("kbd")) {
        // SAFETY: as above.
        (Some(t), Some(k)) => Some(crate::fast::Input::from_pipes(unsafe { client(fetch(kp, t)?)? }, unsafe { client(fetch(kp, k)?)? })),
        _ => None,
    };
    let fb = crate::fast::Fb::open(&dir.join("fb.bin"), &crate::fast::fb_pipe(idx)).ok().filter(|f| f.seq() > 0);
    let pids: Vec<(u32, bool)> = squeeze::procs(kp).await?.iter().map(|p| (p.pid, p.main)).collect();
    if pids.is_empty() {
        return Err(format!("no crosvm under boot process {kp}"));
    }
    let browser = serde_json::from_value::<Option<(u64, u64, String)>>(v["browser"].clone()).unwrap_or(None);
    let caps = serde_json::from_value::<Option<(u64, u64)>>(v["caps"].clone()).unwrap_or(None);
    let d = Arc::new(Device {
        id: id.clone(), dir: dir.to_path_buf(), boot: Default::default(), con, ready: AtomicBool::new(true),
        last: StdMutex::new(None), gen: AtomicU64::new(0), scale: StdMutex::new(1.0), idx,
        input: input.map(OnceLock::from).unwrap_or_default(), fb: fb.map(|f| OnceLock::from(Arc::new(f))).unwrap_or_default(),
        events: crate::logs::Events::spawn(dir.join("logcat.log")),
        net: v["net"].as_bool().unwrap_or(true), phone: v["phone"].as_bool().unwrap_or(false),
        density: v["density"].as_u64().unwrap_or(0) as u32,
        crosvm: PathBuf::from(v["crosvm"].as_str().unwrap_or_default()), browser,
        headroom_mb: AtomicU64::new(v["headroom_mb"].as_u64().unwrap_or(0)),
        // Unknown after the gap: the balloon loop sets an absolute size on its next step anyway.
        headroom_in_mb: AtomicU64::new(v["headroom_in_mb"].as_u64().unwrap_or(0)),
        balloon_base_mb: AtomicU64::new(v["balloon_base_mb"].as_u64().unwrap_or(0)),
        caps: StdMutex::new(caps), capped: AtomicBool::new(false), pids: StdMutex::new(pids),
        last_active_ms: AtomicU64::new(device::now_ms()), focus: Default::default(), adopted: Some((kp, ks)),
    });
    let req = v["req"].clone();
    let started = Instant::now().checked_sub(Duration::from_millis(device::now_ms().saturating_sub(v["started_ms"].as_u64().unwrap_or(0))))
        .unwrap_or_else(Instant::now);
    {
        let mut devs = st.devs.lock().unwrap();
        if devs.contains_key(&id) {
            return Err(format!("`{id}` is already listed"));
        }
        devs.insert(id.clone(), Slot { dev: d.clone(), opts: squeeze::Opts::from(&req, squeeze::Opts::DEFAULT), auto_squeeze: false,
            lease: None, info: v["info"].clone(), started, phase: "ready", lat: Default::default(), sent: 0, streams: 0 });
    }
    if let Some((_, _, pkg)) = d.browser.clone().filter(|_| d.headroom_mb.load(Ordering::SeqCst) > 0) {
        let (d2, ev) = (d.clone(), st.events.clone());
        tokio::spawn(async move { crate::browser_balloon(&d2, &pkg, &ev).await });
    }
    emit(st, json!({"type": "device", "device": id, "phase": "ready", "adopted": true, "t_s": 0.0}));
    Ok(())
}

/// Saves every Device for the next daemon. Refused while one is still booting (it has no handles to park yet).
pub async fn save_all(st: &State, addr: &str) -> R<Vec<String>> {
    let ids: Vec<(String, &'static str)> = st.devs.lock().unwrap().iter().map(|(k, s)| (k.clone(), s.phase)).collect();
    if let Some((id, p)) = ids.iter().find(|(_, p)| *p != "ready") {
        return Err(format!("{id} is `{p}`; wait for ready or stop it, then restart"));
    }
    for (id, _) in &ids {
        save(st, id, None, addr).await?;
    }
    Ok(ids.into_iter().map(|(k, _)| k).collect())
}

/// `restart`: save every Device, start `exe` (default this exe) with the same arguments, exit without
/// stopping phones. The new daemon waits for this one to exit before it binds the ports, then adopts.
pub async fn restart(st: &State, req: &Value, addr: &str) -> R<Value> {
    let _g = st.lifecycle.lock().await;
    let kept = save_all(st, addr).await?;
    let exe = req["exe"].as_str().map(PathBuf::from).unwrap_or(std::env::current_exe().map_err(|e| e.to_string())?);
    let args: Vec<String> = std::env::args().skip(1).collect();
    let args = strip_after_pid(&args);
    let mut cmd = std::process::Command::new(&exe);
    cmd.args(&args).arg("--after-pid").arg(std::process::id().to_string());
    use std::os::windows::process::CommandExt;
    let child = cmd.creation_flags(0x0800_0000 | DETACH).spawn().or_else(|_| cmd.creation_flags(0x0800_0000 | 0x200).spawn())
        .map_err(|e| format!("spawn {}: {e}", exe.display()))?;
    eprintln!("restart: kept {kept:?}, next daemon pid {}", child.id());
    tokio::spawn(async {
        tokio::time::sleep(Duration::from_millis(300)).await;
        std::process::exit(0);
    });
    Ok(json!({"ok": true, "kept": kept, "next_pid": child.id(), "exe": exe}))
}

fn strip_after_pid(args: &[String]) -> Vec<String> {
    let mut out = vec![];
    let mut it = args.iter();
    while let Some(a) = it.next() {
        if a == "--after-pid" { it.next(); } else { out.push(a.clone()); }
    }
    out
}

/// `--after-pid N` on the command line: wait (up to 60 s) for the daemon being replaced to exit.
pub fn wait_for_predecessor() {
    let args: Vec<String> = std::env::args().collect();
    if let Some(p) = args.iter().position(|a| a == "--after-pid").and_then(|i| args.get(i + 1)).and_then(|p| p.parse().ok()) {
        wait_exit(p, Duration::from_secs(60));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::windows::named_pipe::{ClientOptions, ServerOptions};

    #[test]
    fn proc_identity() {
        let (p, s) = me();
        assert!(s > 0 && alive(p, s) && !alive(p, s + 1), "a reused pid has another start time");
        let mut c = std::process::Command::new("cmd").args(["/c", "exit 0"]).spawn().unwrap();
        let pid = c.id();
        c.wait().unwrap();
        drop(c);
        assert_eq!(proc_start(pid), None, "exited process is not alive");
        assert_eq!(strip_after_pid(&["--addr".into(), "x".into(), "--after-pid".into(), "9".into()]), vec!["--addr".to_string(), "x".into()]);
    }

    #[test]
    fn check_states() {
        let (p, s) = me();
        let k = std::process::Command::new("cmd").args(["/c", "ping -n 30 127.0.0.1 >nul"]).spawn().unwrap();
        let ks = proc_start(k.id()).unwrap();
        let v = |addr: &str, dp: u32, ds: u64, kp: u32, kst: u64| json!({"addr": addr, "daemon": {"pid": dp, "start": ds}, "keeper": {"pid": kp, "start": kst}});
        assert_eq!(check(&v("a", p, s, k.id(), ks), "a"), None, "our own record (after restart the pid is ours)");
        assert!(check(&v("a", p, s, k.id(), ks + 1), "a").unwrap().0, "keeper pid reused = stale");
        assert!(!check(&v("b", 1, 0, k.id(), ks), "a").unwrap().0, "other daemon's address: skip, keep file");
        let mut k = k;
        let _ = k.kill();
        let _ = k.wait();
        assert!(check(&v("a", 1, 0, k.id(), ks), "a").unwrap().0, "keeper exited = stale");
    }

    /// The whole hand-off without a phone: a stub crosvm (pipe server, one client for life, like crosvm's console),
    /// a daemon that registers its client with tokio, parks the handle in a keeper process and goes away (its runtime
    /// and its handle dropped), then a second runtime fetches the parked handle and talks on the same connection.
    #[test]
    fn pipe_survives_daemon_handoff() {
        let name = format!(r"\\.\pipe\ae-adopt-test-{}", std::process::id());
        let mut keeper = std::process::Command::new("cmd").args(["/c", "ping -n 30 127.0.0.1 >nul"]).spawn().unwrap();
        let kp = keeper.id();
        // Stub crosvm: echoes each line back upper-cased; a broken pipe ends it (crosvm's reader stops too).
        let server = std::thread::spawn({
            let name = name.clone();
            move || tokio::runtime::Runtime::new().unwrap().block_on(async move {
                let mut s = ServerOptions::new().first_pipe_instance(true).create(&name).unwrap();
                s.connect().await.unwrap();
                let mut b = [0u8; 64];
                let mut seen = 0;
                loop {
                    let n = match s.read(&mut b).await { Ok(0) | Err(_) => return seen, Ok(n) => n };
                    seen += 1;
                    s.write_all(&b[..n].to_ascii_uppercase()).await.unwrap();
                }
            })
        });
        std::thread::sleep(Duration::from_millis(200));
        // Daemon 1.
        let parked = tokio::runtime::Runtime::new().unwrap().block_on(async {
            use std::os::windows::io::AsRawHandle;
            let mut c = ClientOptions::new().open(&name).unwrap();
            c.write_all(b"one").await.unwrap();
            let mut b = [0u8; 3];
            c.read_exact(&mut b).await.unwrap();
            assert_eq!(&b, b"ONE");
            park(kp, c.as_raw_handle() as isize).unwrap()
        }); // runtime and client dropped: daemon 1 is gone
        std::thread::sleep(Duration::from_millis(200));
        // Daemon 2.
        tokio::runtime::Runtime::new().unwrap().block_on(async {
            let h = fetch(kp, parked).unwrap();
            let mut c = unsafe { client(h) }.unwrap();
            c.write_all(b"two").await.unwrap();
            let mut b = [0u8; 3];
            c.read_exact(&mut b).await.unwrap();
            assert_eq!(&b, b"TWO", "same connection after the hand-off");
        });
        let _ = keeper.kill();
        let _ = keeper.wait();
        assert_eq!(server.join().unwrap(), 2, "server saw both daemons on one connection, then the pipe closed");
    }
}
