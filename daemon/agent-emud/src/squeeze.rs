// Lever B as Device options (issue 13; reference fleet_squeeze.py / fleet_cap.py): once the app is on
// screen, drop guest caches, inflate the balloon so the host can discard those guest pages, then hard-cap
// the working set of every crosvm process of the Device. `memory` reports what that leaves.
use crate::device::{Device, R};
use serde_json::{json, Value};
use std::path::Path;
use std::time::{Duration, Instant};

const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const PROCESS_SET_QUOTA: u32 = 0x0100;
const PROCESS_QUERY_INFORMATION: u32 = 0x0400;
const QUOTA_LIMITS_HARDWS_MIN_DISABLE: u32 = 0x2;
const QUOTA_LIMITS_HARDWS_MAX_ENABLE: u32 = 0x4;

extern "system" {
    fn OpenProcess(access: u32, inherit: i32, pid: u32) -> isize;
    fn CloseHandle(h: isize) -> i32;
    fn SetProcessWorkingSetSizeEx(h: isize, min: usize, max: usize, flags: u32) -> i32;
    fn GetProcessWorkingSetSizeEx(h: isize, min: *mut usize, max: *mut usize, flags: *mut u32) -> i32;
}

/// Squeeze sizes in MB; 0 turns that step off.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Opts {
    pub balloon_mb: u64,
    pub cap_main_mb: u64,
    pub cap_helper_mb: u64,
    /// auto_squeeze waits this long after the app launch before squeezing (`squeeze_settle_s`). The
    /// 10-Device proof used 45 s; squeezing 2 s after launch let lmkd kill a 576 MB guest's app.
    pub settle_s: u64,
}

impl Opts {
    pub const DEFAULT: Opts = Opts { balloon_mb: 150, cap_main_mb: 250, cap_helper_mb: 16, settle_s: 45 };

    /// Fields present in `req` override `base`.
    pub fn from(req: &Value, base: Opts) -> Opts {
        let g = |k: &str, d: u64| req[k].as_u64().unwrap_or(d);
        Opts { balloon_mb: g("balloon_mb", base.balloon_mb), cap_main_mb: g("cap_main_mb", base.cap_main_mb),
            cap_helper_mb: g("cap_helper_mb", base.cap_helper_mb), settle_s: g("squeeze_settle_s", base.settle_s) }
    }
}

pub struct Proc {
    pub pid: u32,
    pub main: bool,
    pub ws_mb: u64,
}

/// The Device's crosvm processes: the broker (child of its boot powershell) and the broker's children.
pub async fn procs(boot_pid: u32) -> R<Vec<Proc>> {
    let o = tokio::process::Command::new("powershell")
        .args(["-NoProfile", "-Command", "Get-CimInstance Win32_Process -Filter \"Name='crosvm.exe'\" | \
            ForEach-Object { '{0}|{1}|{2}|{3}' -f $_.ProcessId,$_.ParentProcessId,$_.WorkingSetSize,$_.CommandLine }"])
        .creation_flags(CREATE_NO_WINDOW).output().await.map_err(|e| format!("process list: {e}"))?;
    Ok(tree(&String::from_utf8_lossy(&o.stdout), boot_pid))
}

/// Parses "pid|ppid|ws_bytes|cmdline" rows and keeps the broker under `boot_pid` plus its children.
pub fn tree(rows: &str, boot_pid: u32) -> Vec<Proc> {
    let rows: Vec<(u32, u32, u64, &str)> = rows.lines().filter_map(|l| {
        let mut f = l.trim_end_matches('\r').splitn(4, '|');
        Some((f.next()?.parse().ok()?, f.next()?.parse().ok()?, f.next()?.parse().ok()?, f.next().unwrap_or("")))
    }).collect();
    let Some(broker) = rows.iter().find(|r| r.1 == boot_pid).map(|r| r.0) else { return vec![] };
    rows.iter().filter(|r| r.0 == broker || r.1 == broker)
        .map(|r| Proc { pid: r.0, main: r.3.contains("run-main"), ws_mb: r.2 >> 20 }).collect()
}

/// Hard max working set of `pid` in MB (min 1 MB, min not enforced).
pub fn cap(pid: u32, mb: u64) -> Result<(), String> {
    // SAFETY: plain Win32 calls on a handle we open and close here.
    unsafe {
        let h = OpenProcess(PROCESS_SET_QUOTA | PROCESS_QUERY_INFORMATION, 0, pid);
        if h == 0 {
            return Err(format!("OpenProcess {pid}: {}", std::io::Error::last_os_error()));
        }
        let ok = SetProcessWorkingSetSizeEx(h, 1 << 20, (mb as usize) << 20, QUOTA_LIMITS_HARDWS_MAX_ENABLE | QUOTA_LIMITS_HARDWS_MIN_DISABLE);
        let err = std::io::Error::last_os_error();
        CloseHandle(h);
        if ok == 0 { Err(format!("cap {pid}: {err}")) } else { Ok(()) }
    }
}

/// The hard max working set in MB, or None when the process has no hard max.
pub fn cap_of(pid: u32) -> Option<u64> {
    // SAFETY: as in `cap`.
    unsafe {
        let h = OpenProcess(PROCESS_QUERY_INFORMATION, 0, pid);
        if h == 0 {
            return None;
        }
        let (mut min, mut max, mut flags) = (0usize, 0usize, 0u32);
        let ok = GetProcessWorkingSetSizeEx(h, &mut min, &mut max, &mut flags);
        CloseHandle(h);
        (ok != 0 && flags & QUOTA_LIMITS_HARDWS_MAX_ENABLE != 0).then_some((max >> 20) as u64)
    }
}

/// `memory` reply: total and per-process working set with the hard cap of each.
pub async fn memory(boot_pid: u32) -> R<Value> {
    let p = procs(boot_pid).await?;
    if p.is_empty() {
        return Err("no crosvm processes found for this Device".into());
    }
    let list: Vec<Value> = p.iter().map(|p| json!({"pid": p.pid, "role": if p.main { "main" } else { "helper" },
        "ws_mb": p.ws_mb, "cap_mb": cap_of(p.pid)})).collect();
    Ok(json!({"ok": true, "ws_mb": p.iter().map(|p| p.ws_mb).sum::<u64>(), "processes": list}))
}

/// `balloon_actual` in bytes from `crosvm balloon_stats` (pretty JSON; the key may be nested).
pub fn balloon_actual(stats_json: &str) -> Option<u64> {
    fn find(v: &Value) -> Option<u64> {
        match v {
            Value::Object(m) => m.get("balloon_actual").and_then(Value::as_u64).or_else(|| m.values().find_map(find)),
            Value::Array(a) => a.iter().find_map(find),
            _ => None,
        }
    }
    find(&serde_json::from_str(stats_json).ok()?)
}

async fn crosvm(exe: &Path, args: &[&str]) -> R<String> {
    let o = tokio::process::Command::new(exe).args(args).creation_flags(CREATE_NO_WINDOW).output().await
        .map_err(|e| format!("crosvm {}: {e}", args[0]))?;
    if !o.status.success() {
        return Err(format!("crosvm {}: {}", args[0], String::from_utf8_lossy(&o.stderr).trim()));
    }
    Ok(String::from_utf8_lossy(&o.stdout).into_owned())
}

/// Guest MemAvailable below this means the balloon is too big.
pub const MIN_GUEST_AVAILABLE_MB: u64 = 100;

/// "MemAvailable:   123456 kB" -> MB.
pub fn mem_available_mb(meminfo: &str) -> Option<u64> {
    let l = meminfo.lines().find(|l| l.starts_with("MemAvailable:"))?;
    Some(l.split_whitespace().nth(1)?.parse::<u64>().ok()? / 1024)
}

/// Next balloon size in MB, or None to keep this one. No reading (the shell hung) counts as starved.
pub fn next_target(avail_mb: Option<u64>, target_mb: u64) -> Option<u64> {
    if target_mb == 0 || avail_mb.is_some_and(|a| a > MIN_GUEST_AVAILABLE_MB) {
        return None;
    }
    Some(target_mb.saturating_sub(50))
}

async fn guest_available(d: &Device) -> Option<u64> {
    d.con.exec("grep MemAvailable /proc/meminfo", Duration::from_secs(10)).await.ok().and_then(|(o, _)| mem_available_mb(&o))
}

/// Sets the balloon to `mb` and waits until it gets there (95%), stops moving for 5 s, or 90 s pass.
/// Returns the balloon's actual size in bytes.
async fn set_balloon(exe: &Path, pipe: &str, mb: u64) -> R<u64> {
    let want = mb << 20;
    crosvm(exe, &["balloon", &want.to_string(), pipe]).await?;
    let (mut last, mut still) = (u64::MAX, Instant::now());
    let end = Instant::now() + Duration::from_secs(90);
    loop {
        tokio::time::sleep(Duration::from_millis(500)).await;
        let a = balloon_actual(&crosvm(exe, &["balloon_stats", pipe]).await?).unwrap_or(0);
        if a != last {
            (last, still) = (a, Instant::now());
        }
        if a.abs_diff(want) <= want / 20 || still.elapsed() > Duration::from_secs(5) || Instant::now() > end {
            return Ok(a);
        }
    }
}

/// Runs lever B on a ready Device. Returns working sets before/after and the balloon reached.
pub async fn squeeze(d: &Device, exe: &Path, boot_pid: u32, o: Opts) -> R<Value> {
    let before = memory(boot_pid).await?;
    let t0 = Instant::now();
    d.con.exec("insmod /system_dlkm/lib/modules/virtio_balloon.ko 2>/dev/null; sync; echo 3 > /proc/sys/vm/drop_caches",
        Duration::from_secs(30)).await?;
    let mut balloon = Value::Null;
    if o.balloon_mb > 0 {
        let pipe = format!(r"\\.\pipe\ae-vm-{}", d.idx());
        // A balloon too big for the guest starves it: 896 MB guest at 350 MB gave MemAvailable 8.7 MB and
        // hung shells; inflating 150 MB in one go let lmkd kill the app under test. So ramp up 50 MB at
        // a time, read MemAvailable after each step, and step back once it is under 100 MB.
        let (mut target, mut actual, mut steps) = (0u64, 0u64, vec![]);
        let mut avail_mb = guest_available(d).await;
        steps.push(json!({"balloon_mb": 0, "guest_available_mb": avail_mb}));
        while target < o.balloon_mb && next_target(avail_mb, target + 50).is_none() {
            target = (target + 50).min(o.balloon_mb);
            actual = set_balloon(exe, &pipe, target).await?;
            avail_mb = guest_available(d).await;
            steps.push(json!({"balloon_mb": target, "guest_available_mb": avail_mb}));
            if let Some(t) = next_target(avail_mb, target) {
                target = t;
                actual = set_balloon(exe, &pipe, target).await?;
                avail_mb = guest_available(d).await;
                steps.push(json!({"balloon_mb": target, "guest_available_mb": avail_mb}));
                break;
            }
        }
        balloon = json!({"requested_mb": o.balloon_mb, "final_mb": target, "actual_mb": actual >> 20,
            "guest_available_mb": avail_mb, "steps": steps, "wait_ms": t0.elapsed().as_millis() as u64});
    }
    let mut capped = vec![];
    for p in procs(boot_pid).await? {
        let mb = if p.main { o.cap_main_mb } else { o.cap_helper_mb };
        if mb > 0 {
            cap(p.pid, mb)?;
            capped.push(p.pid);
        }
    }
    let after = memory(boot_pid).await?;
    Ok(json!({"ok": true, "opts": {"balloon_mb": o.balloon_mb, "cap_main_mb": o.cap_main_mb, "cap_helper_mb": o.cap_helper_mb},
        "balloon": balloon, "capped": capped, "ws_before_mb": before["ws_mb"], "ws_after_mb": after["ws_mb"],
        "processes": after["processes"], "squeeze_ms": t0.elapsed().as_millis() as u64}))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tree_keeps_only_this_device() {
        let rows = "10|5|1048576000|crosvm.exe run-mp --socket \\\\.\\pipe\\ae-vm-0\r\n\
            11|10|268435456|crosvm.exe run-main ...\n12|10|16777216|crosvm.exe device block\n\
            20|6|999|crosvm.exe run-mp other worker\n21|20|999|crosvm.exe run-main other\nbad row";
        let p = tree(rows, 5);
        assert_eq!(p.iter().map(|p| p.pid).collect::<Vec<_>>(), vec![10, 11, 12]);
        assert_eq!(p.iter().map(|p| p.main).collect::<Vec<_>>(), vec![false, true, false]);
        assert_eq!(p[0].ws_mb, 1000);
        assert!(tree(rows, 99).is_empty());
    }

    #[test]
    fn opts_and_balloon_stats() {
        let o = Opts::from(&json!({"balloon_mb": 0, "cap_main_mb": 300}), Opts::DEFAULT);
        assert_eq!(o, Opts { balloon_mb: 0, cap_main_mb: 300, cap_helper_mb: 16, settle_s: 45 });
        assert_eq!(Opts::from(&json!({"squeeze_settle_s": 5}), Opts::DEFAULT).settle_s, 5);
        let s = r#"{"BalloonStats": {"stats": {"free_memory": 1}, "balloon_actual": 367001600}}"#;
        assert_eq!(balloon_actual(s), Some(367001600));
        assert_eq!(balloon_actual("not json"), None);
    }

    #[test]
    fn balloon_backs_off_until_guest_has_room() {
        assert_eq!(mem_available_mb("MemTotal: 900000 kB
MemAvailable:    8908 kB
"), Some(8));
        assert_eq!(mem_available_mb("nothing"), None);
        assert_eq!(next_target(Some(8), 150), Some(100));
        assert_eq!(next_target(None, 150), Some(100), "a hung shell counts as starved");
        assert_eq!(next_target(Some(101), 150), None);
        assert_eq!(next_target(Some(20), 30), Some(0));
        assert_eq!(next_target(Some(20), 0), None, "balloon empty: nothing more to give back");
    }
}
