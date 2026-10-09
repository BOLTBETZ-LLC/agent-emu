// Structured API layer 1 (issue 15): logcat and crash/ANR events. Both read the Device's host-side
// logcat.log: the guest streams `logcat -b all -v threadtime` to virtio-console 3, which crosvm writes
// to that file, so no guest shell or adb is involved.
use serde_json::{json, Value};
use std::collections::{HashMap, HashSet};
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, Weak};
use std::time::Duration;
use tokio::sync::Notify;

/// A `logs` call without a cursor reads at most this much from the end of the file.
// ponytail: older history is not searched; index the file by time if agents need more.
pub const MAX_SCAN: u64 = 32 << 20;

/// One threadtime line: "MM-DD HH:MM:SS.mmm  PID  TID L TAG     : message".
#[derive(Debug, PartialEq)]
pub struct Line<'a> {
    pub time: &'a str,
    pub pid: u32,
    pub level: &'a str,
    pub tag: &'a str,
    pub msg: &'a str,
}

fn tok<'a>(s: &mut &'a str) -> Option<&'a str> {
    let t = s.trim_start();
    let end = t.find(char::is_whitespace).unwrap_or(t.len());
    *s = &t[end..];
    (end > 0).then(|| &t[..end])
}

pub fn parse(line: &str) -> Option<Line<'_>> {
    let mut s = line;
    let (date, time) = (tok(&mut s)?, tok(&mut s)?);
    let pid = tok(&mut s)?.parse().ok()?;
    let _tid = tok(&mut s)?;
    let level = tok(&mut s)?;
    let rest = s.trim_start();
    let i = rest.find(": ")?;
    let n = date.len() + 1 + time.len();
    let t = line.trim_start();
    Some(Line { time: t.get(..n)?, pid, level, tag: rest[..i].trim_end(), msg: &rest[i + 2..] })
}

/// "MM-DD HH:MM:SS.mmm" in the guest clock (UTC on these images) -> unix ms, in `year`.
// ponytail: logcat has no year; a log spanning New Year sorts wrong. Fine for test runs.
pub fn unix_ms(time: &str, year: i64) -> Option<u64> {
    let n = |a: usize, b: usize| time.get(a..b)?.parse::<i64>().ok();
    let (mo, d, h, mi, s, ms) = (n(0, 2)?, n(3, 5)?, n(6, 8)?, n(9, 11)?, n(12, 14)?, n(15, 18)?);
    // days from civil (Howard Hinnant)
    let y = if mo <= 2 { year - 1 } else { year };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let doy = (153 * (if mo > 2 { mo - 3 } else { mo + 9 }) + 2) / 5 + d - 1;
    let days = era * 146097 + yoe * 365 + yoe / 4 - yoe / 100 + doy - 719468;
    Some((((days * 24 + h) * 60 + mi) * 60 + s) as u64 * 1000 + ms as u64)
}

pub fn this_year() -> i64 {
    let days = (crate::device::now_ms() / 86_400_000) as i64;
    // civil from days, year only
    let z = days + 719468;
    let era = z.div_euclid(146097);
    let doe = z - era * 146097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    yoe + era * 400 + if mp >= 10 { 1 } else { 0 }
}

/// Bracketed event-log payload "[a,b,c]" split into at most `n` fields (the last keeps its commas).
fn fields(msg: &str, n: usize) -> Vec<&str> {
    msg.trim().trim_start_matches('[').trim_end_matches(']').splitn(n, ',').collect()
}

/// `am_proc_start` -> (pid, process name).
pub fn proc_start(l: &Line) -> Option<(u32, String)> {
    if l.tag != "am_proc_start" {
        return None;
    }
    let f = fields(l.msg, 5);
    Some((f.get(1)?.parse().ok()?, f.get(3)?.to_string()))
}

/// True when `process` is the package itself or one of its `pkg:sub` processes.
pub fn is_pkg(process: &str, pkg: &str) -> bool {
    process == pkg || process.strip_prefix(pkg).is_some_and(|r| r.starts_with(':'))
}

// ---------- logs ----------

pub struct Filter {
    pub name: Option<String>,
    pub pids: HashSet<u32>,
    pub since_ms: Option<u64>,
    pub year: i64,
    /// Lowest level kept ("W" keeps W, E, F); None keeps all.
    pub min_level: Option<u8>,
}

/// Logcat level rank: V 0 .. F 5 (unknown letters rank as V).
pub fn rank(level: &str) -> u8 {
    "VDIWEF".find(level).unwrap_or(0) as u8
}

impl Filter {
    /// Keeps the line when it is new enough and from the package's pids or has the tag. Learns pids
    /// from `am_proc_start` lines, so a restarted app is followed.
    pub fn keep(&mut self, l: &Line) -> bool {
        if let (Some(name), Some((pid, p))) = (&self.name, proc_start(l)) {
            if is_pkg(&p, name) {
                self.pids.insert(pid);
            }
        }
        if self.min_level.is_some_and(|m| rank(l.level) < m) {
            return false;
        }
        if let Some(s) = self.since_ms {
            if unix_ms(l.time, self.year).is_some_and(|t| t < s) {
                return false;
            }
        }
        match &self.name {
            None => true,
            Some(n) => self.pids.contains(&l.pid) || l.tag == n,
        }
    }
}

/// Reads complete lines from `from` (None: the last MAX_SCAN bytes) to the end of the file.
/// Returns (lines kept, at most `max` of the newest; cursor after the last complete line).
pub fn read(path: &Path, from: Option<u64>, f: &mut Filter, max: usize) -> Result<(Vec<String>, u64), String> {
    let mut file = std::fs::File::open(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let len = file.metadata().map_err(|e| e.to_string())?.len();
    let mut start = from.unwrap_or(len.saturating_sub(MAX_SCAN)).min(len);
    file.seek(SeekFrom::Start(start)).map_err(|e| e.to_string())?;
    let mut buf = Vec::with_capacity((len - start) as usize);
    file.take(len - start).read_to_end(&mut buf).map_err(|e| e.to_string())?;
    let mut body = &buf[..];
    if from.is_none() && start > 0 {
        // Started mid-line: skip to the next one.
        let skip = body.iter().position(|&b| b == b'\n').map_or(body.len(), |i| i + 1);
        body = &body[skip..];
        start += skip as u64;
    }
    let complete = body.iter().rposition(|&b| b == b'\n').map_or(0, |i| i + 1);
    let mut out = std::collections::VecDeque::new();
    for raw in String::from_utf8_lossy(&body[..complete]).lines() {
        let raw = raw.trim_end_matches('\r');
        if parse(raw).is_some_and(|l| f.keep(&l)) {
            if out.len() == max {
                out.pop_front();
            }
            out.push_back(raw.to_string());
        }
    }
    Ok((out.into(), start + complete as u64))
}

/// The message with numbers and hex runs replaced by `#`, so repeats of one problem group together.
pub fn normalize(msg: &str) -> String {
    let mut out = String::with_capacity(msg.len());
    let mut in_num = false;
    for w in msg.split_inclusive(|c: char| !c.is_ascii_alphanumeric()) {
        let (word, sep) = w.split_at(w.trim_end_matches(|c: char| !c.is_ascii_alphanumeric()).len());
        let num = !word.is_empty() && (word.chars().all(|c| c.is_ascii_digit())
            || (word.len() >= 6 && word.chars().all(|c| c.is_ascii_hexdigit()) && word.chars().any(|c| c.is_ascii_digit()))
            || (word.starts_with("0x") && word.len() > 2));
        if num {
            if !in_num { out.push('#'); }
        } else {
            out.push_str(word);
        }
        in_num = num && sep.is_empty();
        out.push_str(sep);
    }
    out
}

/// E and F lines grouped by (app or system, level, tag, normalized message), most frequent first.
/// "app" = a pid of the filter's package (learned as `read` does). Scans the last MAX_SCAN bytes.
pub fn issues(path: &Path, f: &mut Filter, top: usize) -> Result<Vec<serde_json::Value>, String> {
    // Every line (not just E/F): `am_proc_start` lines teach `f` the app's pids.
    let mut all = Filter { name: None, pids: HashSet::new(), since_ms: None, year: f.year, min_level: None };
    let (lines, _) = read(path, None, &mut all, usize::MAX)?;
    let mut groups: std::collections::HashMap<(bool, String, String, String), (u64, String)> = Default::default();
    for raw in &lines {
        let Some(l) = parse(raw) else { continue };
        f.keep(&l);
        if rank(l.level) < 4 {
            continue;
        }
        let app = f.pids.contains(&l.pid);
        let e = groups.entry((app, l.level.to_string(), l.tag.to_string(), normalize(l.msg))).or_insert((0, String::new()));
        e.0 += 1;
        e.1 = l.time.to_string();
    }
    let mut v: Vec<_> = groups.into_iter().collect();
    v.sort_by(|a, b| b.1 .0.cmp(&a.1 .0));
    Ok(v.into_iter().take(top).map(|((app, level, tag, msg), (n, last))| serde_json::json!({
        "side": if app { "app" } else { "system" }, "count": n, "level": level, "tag": tag, "message": msg, "last": last})).collect())
}

/// Package of a `wm_set_resumed_activity: [0,<pkg>/<activity>,...]` line (the activity now in front).
pub fn resumed_package(line: &str) -> Option<&str> {
    let rest = &line[line.find("wm_set_resumed_activity: [")? + 26..];
    let comp = rest.split(',').nth(1)?;
    Some(comp.split('/').next()?.trim())
}

/// true on a cameraserver connect line, false on its disconnect line (a dying client is disconnected too).
pub fn camera_open(line: &str) -> Option<bool> {
    if line.contains("CameraService::connect call") {
        Some(true)
    } else if line.contains("CameraService: disconnect: Disconnected client") {
        Some(false)
    } else {
        None
    }
}

// ---------- crash and ANR events ----------

/// Crash/ANR events parsed from the log as it is written. Each event has a `seq` (1, 2, ...).
pub struct Events {
    list: Mutex<Vec<Value>>,
    pub notify: Notify,
}

/// Turns log lines into events:
/// - `am_crash` (Java or native app crash, reported by ActivityManager) -> "crash", with the
///   process's `E AndroidRuntime` lines as `stack`;
/// - `am_anr` -> "anr";
/// - `F libc: Fatal signal` (any native process, system daemons too) -> "native_crash".
#[derive(Default)]
pub struct Parser {
    stacks: HashMap<u32, Vec<String>>,
}

impl Parser {
    pub fn line(&mut self, raw: &str) -> Option<Value> {
        let l = parse(raw)?;
        match (l.tag, l.level) {
            ("AndroidRuntime", "E") => {
                let s = self.stacks.entry(l.pid).or_default();
                if l.msg.starts_with("FATAL EXCEPTION") {
                    s.clear();
                }
                if s.len() < 60 {
                    s.push(l.msg.to_string());
                }
                None
            }
            ("am_crash", _) => {
                // [pid, user, process, flags, exception, message, file, line, recoverable]
                let f = fields(l.msg, 6);
                let pid: u32 = f.first()?.parse().ok()?;
                let tail: Vec<&str> = f.get(5).map_or(vec![], |m| m.rsplitn(4, ',').collect());
                Some(json!({"type": "crash", "time": l.time, "pid": pid, "process": f.get(2)?,
                    "exception": f.get(4).copied().unwrap_or(""), "message": tail.last().copied().unwrap_or(""),
                    "stack": self.stacks.remove(&pid).unwrap_or_default()}))
            }
            ("am_anr", _) => {
                // [user, pid, process, flags, reason]
                let f = fields(l.msg, 5);
                Some(json!({"type": "anr", "time": l.time, "pid": f.get(1)?.parse::<u32>().ok()?,
                    "process": f.get(2)?, "reason": f.get(4).copied().unwrap_or("")}))
            }
            ("libc", "F") if l.msg.starts_with("Fatal signal") => {
                // "Fatal signal 6 (SIGABRT), code -6 (SI_TKILL) in tid 3095 (droid.bluetooth), pid 2899 (droid.bluetooth)"
                let signal = l.msg.split(['(', ')']).nth(1).unwrap_or("");
                let process = l.msg.rsplit(['(', ')']).nth(1).unwrap_or("");
                Some(json!({"type": "native_crash", "time": l.time, "pid": l.pid, "process": process,
                    "signal": signal, "message": l.msg}))
            }
            _ => None,
        }
    }
}

impl Events {
    /// Tails `path` from the start (the file is new each boot) until the Device is dropped.
    pub fn spawn(path: PathBuf) -> Arc<Events> {
        let ev = Arc::new(Events { list: Mutex::new(Vec::new()), notify: Notify::new() });
        let weak: Weak<Events> = Arc::downgrade(&ev);
        tokio::spawn(async move {
            let (mut pos, mut parser, mut rest) = (0u64, Parser::default(), Vec::<u8>::new());
            loop {
                tokio::time::sleep(Duration::from_millis(200)).await;
                let Some(ev) = weak.upgrade() else { return };
                let Ok(mut f) = std::fs::File::open(&path) else { continue };
                let len = f.metadata().map(|m| m.len()).unwrap_or(0);
                if len <= pos || f.seek(SeekFrom::Start(pos)).is_err() {
                    continue;
                }
                let mut buf = Vec::new();
                if f.take(len - pos).read_to_end(&mut buf).is_err() {
                    continue;
                }
                pos += buf.len() as u64;
                rest.extend_from_slice(&buf);
                let Some(end) = rest.iter().rposition(|&b| b == b'\n') else { continue };
                let text = String::from_utf8_lossy(&rest[..=end]).into_owned();
                rest.drain(..=end);
                let new: Vec<Value> = text.lines().filter_map(|l| parser.line(l.trim_end_matches('\r'))).collect();
                if !new.is_empty() {
                    ev.push(new);
                }
            }
        });
        ev
    }

    fn push(&self, new: Vec<Value>) {
        let mut l = self.list.lock().unwrap();
        for mut e in new {
            e["seq"] = json!(l.len() + 1);
            l.push(e);
        }
        drop(l);
        self.notify.notify_waiters();
    }

    /// Events with seq > `after`, optionally only for one package. Returns (events, last seq so far).
    pub fn after(&self, after: u64, pkg: Option<&str>) -> (Vec<Value>, u64) {
        let l = self.list.lock().unwrap();
        let ev = l.iter().skip(after as usize)
            .filter(|e| pkg.map_or(true, |p| e["process"].as_str().is_some_and(|q| is_pkg(q, p))))
            .cloned().collect();
        (ev, l.len() as u64)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resumed_package_from_log() {
        let l = "10-08 22:01:16.687   893  3811 I wm_set_resumed_activity: [0,org.mozilla.firefox/org.mozilla.fenix.customtabs.ExternalAppBrowserActivity,realStartActivityLocked - onActivityStateChanged]";
        assert_eq!(resumed_package(l), Some("org.mozilla.firefox"));
        assert_eq!(resumed_package("10-08 I wm_on_top_resumed_gained_called: [1,x,y]"), None);
    }

    #[test]
    fn camera_open_and_close_from_log() {
        assert_eq!(camera_open("10-09 02:53:38.427   816   923 I CameraService: CameraService::connect call (PID 1866 \"com.boltbetz.staging\", camera ID 0) and Camera API version 2"), Some(true));
        assert_eq!(camera_open("10-09 02:59:32.319   816   923 I CameraService: disconnect: Disconnected client for camera 0 for PID 1866"), Some(false));
        assert_eq!(camera_open("10-09 I CameraService: Start camera streaming for x"), None);
    }

    #[test]
    fn issues_group_repeats_and_split_app_from_system() {
        assert_eq!(normalize("exited 4 times in 4 minutes"), "exited # times in # minutes");
        assert_eq!(normalize("pid 7546 addr 0x7f3a2b deadbeef12 ok"), "pid # addr # # ok");
        assert_eq!(normalize("IThreadChip/chip0 v2"), "IThreadChip/chip0 v2");
        let p = std::env::temp_dir().join(format!("ae-issues-{}.log", std::process::id()));
        std::fs::write(&p, "10-08 04:00:00.000  1009  1100 I am_proc_start: [0,6001,10123,com.x,activity,{com.x/com.x.Main}]
10-08 04:00:01.000  6001  6001 E ReactNativeJS: fetch failed 12
10-08 04:00:02.000  6001  6001 E ReactNativeJS: fetch failed 13
10-08 04:00:03.000     1     1 E init    : exited 4 times in 4 minutes
10-08 04:00:04.000     1     1 W init    : not an issue
").unwrap();
        let mut f = Filter { name: Some("com.x".into()), pids: HashSet::new(), since_ms: None, year: 2026, min_level: None };
        let v = issues(&p, &mut f, 10).unwrap();
        std::fs::remove_file(&p).ok();
        assert_eq!(v.len(), 2, "{v:?}");
        assert_eq!((v[0]["side"].as_str(), v[0]["count"].as_u64(), v[0]["message"].as_str()), (Some("app"), Some(2), Some("fetch failed #")));
        assert_eq!(v[1]["side"], "system");
        let mut w = Filter { name: None, pids: HashSet::new(), since_ms: None, year: 2026, min_level: Some(rank("W")) };
        assert!(!w.keep(&parse("10-08 04:00:04.000     1     1 I init    : x").unwrap()));
        assert!(w.keep(&parse("10-08 04:00:04.000     1     1 W init    : x").unwrap()));
    }

    const CRASH: &str = "10-08 04:12:59.814  5742  5742 E AndroidRuntime: FATAL EXCEPTION: main
10-08 04:12:59.814  5742  5742 E AndroidRuntime: Process: com.boltbetz.staging, PID: 5742
10-08 04:12:59.814  5742  5742 E AndroidRuntime: android.app.RemoteServiceException$CrashedByAdbException: shell-induced crash
10-08 04:12:59.885  1009  3384 I am_crash: [5742,0,com.boltbetz.staging,552091204,android.app.RemoteServiceException$CrashedByAdbException,shell-induced crash, with a comma,ActivityThread.java,2513,0]";

    #[test]
    fn parses_threadtime() {
        let l = parse("10-08 04:14:01.755  1009  6407 I am_anr  : [0,6261,com.x,5,Input: timed out]").unwrap();
        assert_eq!(l, Line { time: "10-08 04:14:01.755", pid: 1009, level: "I", tag: "am_anr", msg: "[0,6261,com.x,5,Input: timed out]" });
        assert!(parse("--------- beginning of crash").is_none());
    }

    #[test]
    fn crash_anr_native_events() {
        let mut p = Parser::default();
        let ev: Vec<Value> = CRASH.lines().filter_map(|l| p.line(l)).collect();
        assert_eq!(ev.len(), 1);
        assert_eq!(ev[0]["type"], "crash");
        assert_eq!(ev[0]["pid"], 5742);
        assert_eq!(ev[0]["process"], "com.boltbetz.staging");
        assert_eq!(ev[0]["message"], "shell-induced crash, with a comma");
        assert_eq!(ev[0]["stack"].as_array().unwrap().len(), 3);
        let a = p.line("10-08 04:14:01.755  1009  6407 I am_anr  : [0,6261,com.x,549994052,Input dispatching timed out (a, b)]").unwrap();
        assert_eq!((a["type"].as_str(), a["pid"].as_u64(), a["reason"].as_str()), (Some("anr"), Some(6261), Some("Input dispatching timed out (a, b)")));
        let n = p.line("10-08 04:00:20.954  2899  3095 F libc    : Fatal signal 6 (SIGABRT), code -6 (SI_TKILL) in tid 3095 (droid.bluetooth), pid 2899 (droid.bluetooth)").unwrap();
        assert_eq!((n["type"].as_str(), n["signal"].as_str(), n["process"].as_str()), (Some("native_crash"), Some("SIGABRT"), Some("droid.bluetooth")));
    }

    #[test]
    fn filter_follows_package_pids_and_time() {
        let mut f = Filter { name: Some("com.x".into()), pids: HashSet::new(), since_ms: None, year: 2026, min_level: None };
        let start = "10-08 04:00:00.000  1009  1039 I am_proc_start: [0,77,10052,com.x:remote,top-activity,{com.x/.A}]";
        assert!(!f.keep(&parse("10-08 04:00:00.001    77    77 I Foo: before start").unwrap()));
        f.keep(&parse(start).unwrap());
        assert!(f.keep(&parse("10-08 04:00:00.002    77    77 I Foo: hi").unwrap()));
        assert!(f.keep(&parse("10-08 04:00:00.002     5     5 I com.x: tag match").unwrap()));
        assert!(!f.keep(&parse("10-08 04:00:00.002     5     5 I Other: no").unwrap()));
        assert!(!is_pkg("com.xy", "com.x"));
        let t = unix_ms("10-08 04:12:59.814", 2026).unwrap();
        assert_eq!(t, 1791432779814); // 2026-10-08T04:12:59.814Z
        f.since_ms = Some(t);
        assert!(!f.keep(&parse("10-08 04:12:59.813    77    77 I Foo: old").unwrap()));
        assert!(f.keep(&parse("10-08 04:12:59.814    77    77 I Foo: new").unwrap()));
    }

    #[test]
    fn read_keeps_complete_lines_and_cursor() {
        let p = std::env::temp_dir().join(format!("ae-logs-{}.log", std::process::id()));
        std::fs::write(&p, format!("{CRASH}\n10-08 04:13:00.000     1     1 I Tail: partial")).unwrap();
        let mut f = Filter { name: None, pids: HashSet::new(), since_ms: None, year: 2026, min_level: None };
        let (lines, cur) = read(&p, Some(0), &mut f, 2).unwrap();
        assert_eq!(lines.len(), 2, "the newest 2 of 4 complete lines");
        assert!(lines[1].contains("am_crash"));
        assert_eq!(cur as usize, CRASH.len() + 1, "cursor stops before the partial line");
        let _ = std::fs::remove_file(&p);
        assert!(this_year() >= 2026);
    }
}
