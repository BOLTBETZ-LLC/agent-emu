// Fleet operations on top of the phones: claims (who uses which phone), health (is it usable), build/OTA
// (which app build and update it runs) and disk snapshots (save a signed-in phone, put it back later).
//   {"call":"claim","device":"d2","owner":"me","note":"lane 3"}   {"call":"unclaim","device":"d2","owner":"me"}
//   {"call":"health","device":"d2"}                                fresh check, full detail
//   {"call":"snapshot","device":"d1","name":"lane2-golden"}         {"call":"restore","device":"d1","name":"lane2-golden"}
use crate::device::{self, Device, R};
use crate::State;
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::Duration;

pub const CALLS: &[&str] = &["claim", "unclaim", "health", "snapshot", "restore"];
const APP: &str = "com.boltbetz.staging";

pub async fn handle(st: &Arc<State>, call: &str, req: &Value) -> R<Value> {
    let id = req["device"].as_str().ok_or("missing `device`")?;
    crate::idx_of(id)?;
    match call {
        "claim" | "unclaim" => {
            let r = if call == "claim" { claim(id, req)? } else { unclaim(id, req)? };
            crate::emit(st, json!({"type": "claim", "device": id, "claim": r["claim"]}));
            Ok(r)
        }
        "health" => {
            let h = check(st, id, true).await?;
            crate::emit(st, json!({"type": "health", "device": id, "health": h}));
            Ok(json!({"ok": true, "device": id, "health": h}))
        }
        "snapshot" => snapshot(st, id, req).await,
        _ => restore(st, id, req).await,
    }
}

// ---------- claims ----------

/// device -> {owner, note, since_ms}. In memory: a daemon restart clears them.
static CLAIMS: Mutex<BTreeMap<String, Value>> = Mutex::new(BTreeMap::new());

fn claim(id: &str, req: &Value) -> R<Value> {
    let owner = req["owner"].as_str().filter(|o| !o.trim().is_empty()).ok_or("missing `owner`")?;
    let mut c = CLAIMS.lock().unwrap();
    if let Some(old) = c.get(id) {
        if old["owner"] != json!(owner) && req["force"] != json!(true) {
            return Err(format!("{id} is claimed by {} ({}); pass force: true to take it", old["owner"], old["note"]));
        }
    }
    let v = json!({"owner": owner, "note": req["note"].as_str().unwrap_or(""), "since_ms": device::now_ms()});
    c.insert(id.to_string(), v.clone());
    Ok(json!({"ok": true, "device": id, "claim": v}))
}

fn unclaim(id: &str, req: &Value) -> R<Value> {
    let mut c = CLAIMS.lock().unwrap();
    match c.get(id) {
        None => Ok(json!({"ok": true, "device": id, "claim": null})),
        Some(old) if old["owner"] == req["owner"] || req["force"] == json!(true) => {
            c.remove(id);
            Ok(json!({"ok": true, "device": id, "claim": null}))
        }
        Some(old) => Err(format!("{id} is claimed by {}, not {}; pass force: true to clear it", old["owner"], req["owner"])),
    }
}

pub fn claim_of(id: &str) -> Value {
    CLAIMS.lock().unwrap().get(id).cloned().unwrap_or(Value::Null)
}

pub fn claims() -> Value {
    json!(CLAIMS.lock().unwrap().clone())
}

/// Calls that only read: no warning when a non-owner makes them.
const READS: &[&str] = &["status", "screenshot", "ui_tree", "logs", "issues", "crash_events", "memory", "health", "claim", "unclaim"];

/// A warning for a call on a claimed phone from anyone but its owner (the call still runs).
pub fn claim_warning(req: &Value) -> Option<String> {
    let call = req["call"].as_str()?;
    if READS.contains(&call) {
        return None;
    }
    let id = req["device"].as_str()?;
    let c = claim_of(id);
    (!c.is_null() && c["owner"] != req["owner"]).then(|| {
        format!("{id} is claimed by {} ({}); this call came from {}", c["owner"], c["note"],
            req["owner"].as_str().map_or("no owner".into(), |o| format!("`{o}`")))
    })
}

// ---------- max phones ----------

/// Aaron's rule: at most AE_MAX_PHONES (default 4) phones exist. `force: true` boots one more anyway.
pub fn max_check(running: usize, req: &Value) -> R<()> {
    let max = std::env::var("AE_MAX_PHONES").ok().and_then(|v| v.parse().ok()).unwrap_or(4usize);
    if running >= max && req["force"] != json!(true) {
        return Err(format!("{running} phones already run (max {max}, AE_MAX_PHONES); stop one or pass force: true"));
    }
    Ok(())
}

/// Phones on this PC, the count `max_check` takes: this daemon's Devices plus every crosvm broker no Device of
/// this daemon owns (another daemon's, a test daemon's). Aaron's cap is per PC, not per daemon.
pub async fn phones_on_pc(st: &State) -> usize {
    let devs: Vec<Arc<Device>> = st.devs.lock().unwrap().values().map(|s| s.dev.clone()).collect();
    let mut own = vec![];
    for d in &devs {
        own.extend(d.boot_pid().await);
    }
    devs.len() + foreign_brokers(&crate::squeeze::rows().await.unwrap_or_default(), &own)
}

/// Brokers in `squeeze::rows` ("pid|ppid|..."): a crosvm whose parent is not a crosvm, one per running phone.
/// Ours have our Devices' boot powershell as parent.
fn foreign_brokers(rows: &str, own_boot: &[u32]) -> usize {
    let procs: Vec<(u32, u32)> = rows.lines().filter_map(|l| {
        let mut f = l.split('|');
        Some((f.next()?.trim().parse().ok()?, f.next()?.trim().parse().ok()?))
    }).collect();
    procs.iter().filter(|&&(_, pp)| !procs.iter().any(|p| p.0 == pp) && !own_boot.contains(&pp)).count()
}

// ---------- health and build ----------

/// device -> last health (from `health` or the idle loop).
static HEALTH: Mutex<BTreeMap<String, Value>> = Mutex::new(BTreeMap::new());

pub fn health_of(id: &str) -> Value {
    HEALTH.lock().unwrap().get(id).cloned().unwrap_or(Value::Null)
}

/// Not ready after this long = stuck boot.
fn stuck_after_s() -> u64 {
    std::env::var("AE_BOOT_STUCK_S").ok().and_then(|v| v.parse().ok()).unwrap_or(600)
}

/// The runtime the staging channel serves now: AE_EXPECTED_RUNTIME, else the first word of
/// <work>/expected-runtime.txt. None = no check.
fn expected_runtime(work: &Path) -> Option<String> {
    std::env::var("AE_EXPECTED_RUNTIME").ok()
        .or_else(|| std::fs::read_to_string(work.join("expected-runtime.txt")).ok())
        .and_then(|s| s.split_whitespace().next().map(str::to_string))
}

/// One guest round trip for network, app and update facts. KEY=value lines.
fn probe_cmd(pkg: &str, host: &str) -> String {
    format!("echo NET=$(ip -4 addr show eth0 2>/dev/null | grep -c 'inet 10.0.2.15/'); \
        echo DNS=$(ping -c 1 -W 2 {host} 2>&1 | head -n 1); \
        a=$(pm path {pkg} 2>/dev/null | head -n 1 | cut -d: -f2); echo APK=$a; \
        if [ -n \"$a\" ]; then echo APK_BYTES=$(stat -c %s $a); \
        dumpsys package {pkg} 2>/dev/null | grep -m 2 -oE 'version(Code|Name)=[^ ]+'; \
        echo UPDATE=$(sqlite3 /data/data/{pkg}/databases/updates.db \
        'select lower(hex(id)), runtime_version, commit_time from updates order by last_accessed desc limit 1' 2>/dev/null); fi")
}

/// Per APK (path, bytes): the facts read out of it once (sha256, runtime, channel, embedded update id).
static APK_FACTS: Mutex<BTreeMap<String, Value>> = Mutex::new(BTreeMap::new());

async fn apk_facts(d: &Device, apk: &str, bytes: &str) -> Value {
    let key = format!("{}:{apk}:{bytes}", d.id);
    if let Some(v) = APK_FACTS.lock().unwrap().get(&key) {
        return v.clone();
    }
    let cmd = format!("a={apk}; echo SHA=$(sha256sum $a | cut -d' ' -f1); echo FP=$(unzip -p $a assets/fingerprint 2>/dev/null); \
        echo EMBEDDED=$(unzip -p $a assets/app.manifest 2>/dev/null | head -c 60); echo MANIFEST=$(unzip -p $a AndroidManifest.xml | base64 -w 0)");
    let Ok((o, 0)) = d.con.exec(&cmd, Duration::from_secs(60)).await else { return Value::Null };
    let kv = kv(&o);
    let meta = kv.get("MANIFEST").and_then(|b| base64::Engine::decode(&base64::engine::general_purpose::STANDARD, b).ok())
        .map(|m| meta_data(&m)).unwrap_or_default();
    // A runtime given as a resource reference is expo's "file:fingerprint" (resolved via resources.arsc); the APK's
    // assets/fingerprint holds it. ponytail: no resources.arsc parser; other references show as unknown.
    let runtime = meta.get("expo.modules.updates.EXPO_RUNTIME_VERSION").filter(|v| !v.starts_with('@'))
        .cloned().or_else(|| kv.get("FP").filter(|f| !f.is_empty()).cloned());
    let channel = meta.get("expo.modules.updates.UPDATES_CONFIGURATION_REQUEST_HEADERS_KEY")
        .and_then(|h| serde_json::from_str::<Value>(h).ok()).and_then(|h| h["expo-channel-name"].as_str().map(str::to_string));
    let embedded = kv.get("EMBEDDED").and_then(|e| e.split('"').nth(3)).map(str::to_string);
    let v = json!({"apk_sha256": kv.get("SHA"), "runtime": runtime, "channel": channel, "embedded_update": embedded,
        "update_url": meta.get("expo.modules.updates.EXPO_UPDATE_URL")});
    APK_FACTS.lock().unwrap().insert(key, v.clone());
    v
}

fn kv(o: &str) -> BTreeMap<String, String> {
    o.lines().filter_map(|l| l.split_once('=')).map(|(k, v)| (k.trim().to_string(), v.trim().to_string())).collect()
}

/// 32 hex digits -> 8-4-4-4-12.
fn uuid(h: &str) -> String {
    if h.len() != 32 {
        return h.to_string();
    }
    format!("{}-{}-{}-{}-{}", &h[..8], &h[8..12], &h[12..16], &h[16..20], &h[20..])
}

/// The bundled APK the phone's image installs: name of the apk/<name>.img that `dir/apk.img` links to.
fn bundled_name(work: &Path, dir: &Path) -> Option<String> {
    let m = |p: &Path| std::fs::metadata(p).ok().map(|m| (m.len(), m.modified().ok()));
    let want = m(&dir.join("apk.img"))?;
    std::fs::read_dir(work.join("apk")).ok()?.flatten().map(|e| e.path())
        .find(|p| p.extension().is_some_and(|x| x == "img") && m(p) == Some(want))
        .and_then(|p| p.file_stem()?.to_str().map(str::to_string))
}

/// Package of the last `wm_set_resumed_activity` in the last 4 MB of the phone's logcat.log.
fn foreground(dir: &Path) -> Option<String> {
    use std::io::{Read, Seek, SeekFrom};
    let mut f = std::fs::File::open(dir.join("logcat.log")).ok()?;
    let len = f.metadata().ok()?.len();
    f.seek(SeekFrom::Start(len.saturating_sub(4 << 20))).ok()?;
    let mut b = vec![];
    f.read_to_end(&mut b).ok()?;
    String::from_utf8_lossy(&b).lines().filter_map(crate::logs::resumed_package).last().map(str::to_string)
}

/// Health of `id`. `full`: also look at the screen for signed in/out (a ui dump; only when the app is in front).
pub async fn check(st: &Arc<State>, id: &str, full: bool) -> R<Value> {
    let (d, phase, uptime) = {
        let devs = st.devs.lock().unwrap();
        let s = devs.get(id).ok_or(format!("no Device `{id}`"))?;
        (s.dev.clone(), s.phase, s.started.elapsed().as_secs())
    };
    let mut problems: Vec<String> = vec![];
    let mut warns: Vec<String> = vec![];
    let mut h = json!({"phase": phase, "uptime_s": uptime, "checked_ms": device::now_ms()});
    if phase != "ready" {
        let stuck = uptime > stuck_after_s();
        h["stuck"] = json!(stuck);
        if stuck {
            problems.push(format!("boot phase `{phase}` after {uptime} s"));
        }
        return Ok(finish(id, h, problems, warns));
    }
    // adb from the host, the way installs and tools reach the phone.
    let adb = if d.net {
        let r = crate::controls::adb_raw(Some(&d), &["shell", "echo", "ok"], Duration::from_secs(8)).await;
        matches!(&r, Ok((o, 0)) if o.trim() == "ok").then_some(()).ok_or_else(|| match r {
            Ok((o, c)) => format!("exit {c}: {}", o.trim()),
            Err(e) => e,
        })
    } else {
        Err("booted without net".into())
    };
    h["adb"] = json!({"ok": adb.is_ok(), "port": 6520 + d.idx(), "error": adb.as_ref().err()});
    if let Err(e) = &adb {
        problems.push(format!("adb: {e}"));
    }
    let host = std::env::var("AE_HEALTH_HOST").unwrap_or_else(|_| "staging.bbapp01.com".into());
    let (o, _) = d.con.exec(&probe_cmd(APP, &host), Duration::from_secs(30)).await?;
    let kv = kv(&o);
    let ip = kv.get("NET").is_some_and(|n| n != "0");
    let dns = kv.get("DNS").cloned().unwrap_or_default();
    let dns_ok = dns.starts_with("PING ") && dns.contains('(');
    h["network"] = json!({"ip_10_0_2_15": ip, "dns_ok": dns_ok, "dns": dns, "host": host});
    if !ip {
        problems.push("network: eth0 has no 10.0.2.15".into());
    }
    if !dns_ok {
        problems.push(format!("network: {host} does not resolve ({dns})"));
    }
    let apk = kv.get("APK").filter(|a| !a.is_empty()).cloned();
    let fg = foreground(&d.dir);
    h["foreground"] = json!(fg);
    match &apk {
        None => problems.push(format!("{APP} not installed")),
        Some(a) => {
            let bytes = kv.get("APK_BYTES").cloned().unwrap_or_default();
            let mut b = apk_facts(&d, a, &bytes).await;
            if b.is_null() {
                b = json!({});
            }
            b["version_name"] = json!(kv.get("versionName"));
            b["version_code"] = json!(kv.get("versionCode").and_then(|v| v.parse::<u64>().ok()));
            b["apk_bytes"] = json!(bytes.parse::<u64>().ok());
            // The image's bundled APK (apk/<name>.img, EAS build id in the name) when the sizes agree.
            let bundled = std::fs::read_to_string(d.dir.join("apk.size")).ok().is_some_and(|s| s.trim() == bytes);
            b["bundled"] = json!(bundled);
            if bundled {
                if let Some(n) = bundled_name(&st.cfg.work, &d.dir) {
                    b["eas_build"] = json!(n.rsplit('-').next());
                    b["bundled_apk"] = json!(n);
                }
            }
            let up: Vec<&str> = kv.get("UPDATE").map(|u| u.split('|').collect()).unwrap_or_default();
            if up.len() == 3 {
                b["update_id"] = json!(uuid(up[0]));
                b["update_runtime"] = json!(up[1]);
                b["update_commit_ms"] = json!(up[2].parse::<u64>().ok());
                b["update_embedded"] = json!(b["embedded_update"].as_str() == Some(uuid(up[0]).as_str()));
            }
            let expected = expected_runtime(&st.cfg.work);
            b["expected_runtime"] = json!(expected);
            if let (Some(e), Some(r)) = (&expected, b["runtime"].as_str().map(str::to_string)) {
                let r = r.as_str();
                b["runtime_match"] = json!(e == r);
                if e != r {
                    warns.push(format!("runtime {} but staging serves {}: this phone never gets the staging OTA", short(r), short(e)));
                }
            }
            h["build"] = b;
            if full && fg.as_deref() == Some(APP) {
                let xml = crate::ui_xml(&d).await.unwrap_or_default();
                let signed = signed_in(&xml);
                h["signed_in"] = json!(signed);
                if signed == Some(false) {
                    problems.push("app signed out (start or login screen)".into());
                }
            }
        }
    }
    if fg.as_deref().is_some_and(|f| f != APP) {
        warns.push(format!("{} in front, not the app", fg.as_deref().unwrap_or("")));
    }
    // Newest app crash or ANR since boot.
    let (ev, _) = d.events.after(0, Some(APP));
    h["last_crash"] = ev.last().cloned().unwrap_or(Value::Null);
    if let Some(e) = ev.last() {
        warns.push(format!("app {} at {}", e["kind"].as_str().unwrap_or("crash"), e["time"].as_str().unwrap_or("?")));
    }
    if !full {
        h["signed_in"] = health_of(id)["signed_in"].clone(); // keep the last look at the screen
    }
    Ok(finish(id, h, problems, warns))
}

fn short(s: &str) -> &str {
    &s[..s.len().min(12)]
}

/// true on Home or the tabs, false on Start/Login, None elsewhere.
fn signed_in(xml: &str) -> Option<bool> {
    let has = |id: &str| xml.contains(&format!("resource-id=\"{id}\""));
    if has("home-screen") || has("tab-home") {
        Some(true)
    } else if has("start-screen") || has("login-screen") {
        Some(false)
    } else {
        None
    }
}

/// level: "bad" (problems), "warn" (warnings) or "ok"; summary = the first of them. Cached for `status`.
fn finish(id: &str, mut h: Value, problems: Vec<String>, warns: Vec<String>) -> Value {
    h["level"] = json!(if !problems.is_empty() { "bad" } else if !warns.is_empty() { "warn" } else { "ok" });
    h["summary"] = json!(problems.first().or(warns.first()).cloned().unwrap_or_else(|| "healthy".into()));
    h["problems"] = json!(problems);
    h["warnings"] = json!(warns);
    HEALTH.lock().unwrap().insert(id.to_string(), h.clone());
    h
}

/// Every 60 s: health of each phone that is idle (no input for a minute) or not ready yet, as a `health` event.
/// Phones in use (a test run) are left alone; their last health stays, with its `checked_ms`.
pub async fn health_loop(st: Arc<State>) {
    loop {
        tokio::time::sleep(Duration::from_secs(60)).await;
        let ids: Vec<(String, bool)> = st.devs.lock().unwrap().iter()
            .map(|(k, s)| (k.clone(), s.phase != "ready" || !s.dev.in_use())).collect();
        HEALTH.lock().unwrap().retain(|k, _| ids.iter().any(|(id, _)| id == k));
        for (id, idle) in ids {
            if !idle {
                continue;
            }
            match check(&st, &id, true).await {
                Ok(h) => crate::emit(&st, json!({"type": "health", "device": id, "health": h})),
                Err(e) => eprintln!("{id}: health: {e}"),
            }
        }
    }
}

/// meta-data android:name -> android:value of a binary AndroidManifest.xml. Typed values print as numbers,
/// references as "@0x7f...".
pub fn meta_data(b: &[u8]) -> BTreeMap<String, String> {
    let u16_at = |p: usize| b.get(p..p + 2).map(|s| u16::from_le_bytes([s[0], s[1]]) as usize);
    let u32_at = |p: usize| b.get(p..p + 4).map(|s| u32::from_le_bytes([s[0], s[1], s[2], s[3]]) as usize);
    let mut strings: Vec<String> = vec![];
    let mut out = BTreeMap::new();
    let mut pos = 8;
    while let (Some(t), Some(hs), Some(sz)) = (u16_at(pos), u16_at(pos + 2), u32_at(pos + 4)) {
        if sz == 0 {
            break;
        }
        if t == 0x0001 {
            strings = string_pool(b, pos, hs).unwrap_or_default();
        } else if t == 0x0102 {
            let s = |i: usize| strings.get(i).map(String::as_str).unwrap_or("");
            if let (Some(name), Some(at), Some(asz), Some(n)) = (u32_at(pos + 20), u16_at(pos + 24), u16_at(pos + 26), u16_at(pos + 28)) {
                if s(name) == "meta-data" {
                    let (mut k, mut v) = (None, None);
                    for i in 0..n {
                        let a = pos + 16 + at + i * asz;
                        let (Some(an), Some(raw), Some(dt), Some(data)) = (u32_at(a + 4), u32_at(a + 8), b.get(a + 15), u32_at(a + 16)) else { break };
                        let val = if raw != 0xFFFF_FFFF {
                            s(raw).to_string()
                        } else if *dt == 3 {
                            s(data).to_string()
                        } else if *dt == 1 {
                            format!("@0x{data:08x}")
                        } else {
                            (data as u32 as i32).to_string()
                        };
                        match s(an) {
                            "name" => k = Some(val),
                            "value" => v = Some(val),
                            _ => {}
                        }
                    }
                    if let (Some(k), Some(v)) = (k, v) {
                        out.insert(k, v);
                    }
                }
            }
        }
        pos += sz;
    }
    out
}

pub fn string_pool(b: &[u8], pos: usize, hs: usize) -> Option<Vec<String>> {
    let u32_at = |p: usize| b.get(p..p + 4).map(|s| u32::from_le_bytes([s[0], s[1], s[2], s[3]]) as usize);
    let (n, flags, start) = (u32_at(pos + 8)?, u32_at(pos + 16)?, u32_at(pos + 20)?);
    let utf8 = flags & 0x100 != 0;
    (0..n).map(|i| {
        let mut p = pos + start + u32_at(pos + hs + 4 * i)?;
        if utf8 {
            p += if *b.get(p)? & 0x80 != 0 { 2 } else { 1 }; // UTF-16 length, unused
            let mut l = *b.get(p)? as usize;
            p += 1;
            if l & 0x80 != 0 {
                l = ((l & 0x7f) << 8) | *b.get(p)? as usize;
                p += 1;
            }
            Some(String::from_utf8_lossy(b.get(p..p + l)?).into_owned())
        } else {
            let mut l = u16::from_le_bytes([*b.get(p)?, *b.get(p + 1)?]) as usize;
            p += 2;
            if l & 0x8000 != 0 {
                l = ((l & 0x7fff) << 16) | u16::from_le_bytes([*b.get(p)?, *b.get(p + 1)?]) as usize;
                p += 2;
            }
            let w: Vec<u16> = b.get(p..p + 2 * l)?.chunks_exact(2).map(|c| u16::from_le_bytes([c[0], c[1]])).collect();
            Some(String::from_utf16_lossy(&w))
        }
    }).collect()
}

// ---------- snapshots ----------

/// The phone's writable disks; everything else in its dir is rebuilt or relinked at start.
const DISKS: &[&str] = &["userdata.img", "metadata.img", "misc.img", "frp.img"];

fn snap_dir(work: &Path, name: &str) -> R<PathBuf> {
    if name.is_empty() || name.len() > 64 || name.starts_with('.') || !name.chars().all(|c| c.is_ascii_alphanumeric() || "-_.".contains(c)) {
        return Err(format!("bad snapshot name `{name}` (letters, digits, - _ .)"));
    }
    Ok(work.join("snapshots").join(name))
}

/// Saves a running phone's disks: `sync`, stop, copy only the data in use (seconds), start it again with keep_data
/// (same image and screen; ~2 min). crosvm opens the disks exclusively, so they cannot be read while it runs.
/// Stamped with the disks' data.id (Device, image, system image).
async fn snapshot(st: &Arc<State>, id: &str, req: &Value) -> R<Value> {
    let name = req["name"].as_str().ok_or("missing `name`")?;
    let dir = snap_dir(&st.cfg.work, name)?;
    let d = crate::ready_device(st, id)?;
    let info = st.devs.lock().unwrap().get(id).map(|s| s.info.clone()).unwrap_or_default();
    let stamp = std::fs::read_to_string(d.dir.join("data.id")).map_err(|e| format!("{id} data.id: {e}"))?;
    let (ddir, tmp) = (d.dir.clone(), dir.with_extension("tmp"));
    drop(d);
    let _ = std::fs::remove_dir_all(&tmp);
    std::fs::create_dir_all(&tmp).map_err(|e| e.to_string())?;
    let t0 = std::time::Instant::now();
    crate::stop_one(st, id).await?;
    let (src, dst) = (ddir, tmp.clone());
    let copied = tokio::task::spawn_blocking(move || DISKS.iter().map(|f| copy_sparse(&src.join(f), &dst.join(f))).sum::<R<u64>>())
        .await.map_err(|e| e.to_string()).and_then(|r| r);
    let disks_s = t0.elapsed().as_secs_f64();
    let started = boot_again(st, id, &info["image_name"], &info["screen"]["name"]).await;
    let bytes = copied?;
    let meta = json!({"name": name, "device": id, "stamp": stamp, "image_name": info["image_name"], "screen": info["screen"]["name"],
        "created_ms": device::now_ms(), "data_bytes": bytes, "disks_s": disks_s});
    std::fs::write(tmp.join("snap.json"), serde_json::to_vec_pretty(&meta).unwrap()).map_err(|e| e.to_string())?;
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::rename(&tmp, &dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    let ready_s = started.map_err(|e| format!("snapshot `{name}` saved, but {id} did not start again: {e}"))?;
    Ok(json!({"ok": true, "snapshot": meta, "dir": dir, "ready_s": ready_s}))
}

/// Starts `id` again on its kept disks with the image and screen it ran with (past the phone limit: it held a slot).
async fn boot_again(st: &Arc<State>, id: &str, image: &Value, screen: &Value) -> R<f64> {
    let mut req = json!({"image": image, "screen": screen, "keep_data": true, "force": true});
    if image.as_str().unwrap_or("").is_empty() {
        req.as_object_mut().unwrap().remove("image");
    }
    Ok(crate::start_device(st, crate::idx_of(id)?, &req).await?.1)
}

/// Puts a snapshot back: stop the phone (if up), copy the disks in, start it with keep_data (same image and screen).
/// Refused for another Device's snapshot or one whose image or system image changed since.
async fn restore(st: &Arc<State>, id: &str, req: &Value) -> R<Value> {
    let name = req["name"].as_str().ok_or("missing `name`")?;
    let dir = snap_dir(&st.cfg.work, name)?;
    let meta: Value = std::fs::read(dir.join("snap.json")).ok().and_then(|b| serde_json::from_slice(&b).ok())
        .ok_or(format!("no snapshot `{name}`"))?;
    if meta["device"] != json!(id) {
        return Err(format!("snapshot `{name}` is of {}, not {id}", meta["device"]));
    }
    let image = meta["image_name"].as_str().unwrap_or("");
    let need = device::data_stamp(&st.cfg.work, crate::idx_of(id)?, device::image(image)?.0);
    if meta["stamp"] != json!(need) {
        return Err(format!("snapshot `{name}` is `{}`, {id} on {image} now needs `{need}` (image or system image changed)",
            meta["stamp"].as_str().unwrap_or("").trim()));
    }
    let t0 = std::time::Instant::now();
    let running = st.dev(id).is_some();
    if running {
        crate::stop_one(st, id).await?;
    }
    let ddir = st.cfg.work.join("fleet").join(id);
    let (src, dst) = (dir.clone(), ddir.clone());
    tokio::task::spawn_blocking(move || DISKS.iter().map(|f| copy_sparse(&src.join(f), &dst.join(f))).sum::<R<u64>>())
        .await.map_err(|e| e.to_string())??;
    std::fs::write(ddir.join("data.id"), &need).map_err(|e| format!("data.id: {e}"))?;
    let disks_s = t0.elapsed().as_secs_f64();
    if !running {
        crate::ops::max_check(phones_on_pc(st).await, req)?;
    }
    let ready_s = boot_again(st, id, &meta["image_name"], &meta["screen"]).await?;
    Ok(json!({"ok": true, "device": id, "name": name, "disks_s": disks_s, "ready_s": ready_s, "was_running": running}))
}

#[repr(C)]
#[derive(Clone, Copy)]
struct Range {
    off: i64,
    len: i64,
}

extern "system" {
    fn DeviceIoControl(h: isize, code: u32, inb: *const std::ffi::c_void, inl: u32, outb: *mut std::ffi::c_void, outl: u32,
        ret: *mut u32, ov: *mut std::ffi::c_void) -> i32;
    fn GetLastError() -> u32;
}
const FSCTL_SET_SPARSE: u32 = 0x0009_00C4;
const FSCTL_QUERY_ALLOCATED_RANGES: u32 = 0x0009_40CF;

/// The allocated ranges of `f` (all of it for a file that is not sparse, or when the query fails).
fn allocated(f: &std::fs::File, len: u64) -> Vec<(u64, u64)> {
    use std::os::windows::io::AsRawHandle;
    let h = f.as_raw_handle() as isize;
    let (mut out, mut from) = (vec![], 0i64);
    loop {
        let q = Range { off: from, len: len as i64 - from };
        let mut buf = [Range { off: 0, len: 0 }; 256];
        let mut got = 0u32;
        // SAFETY: in/out buffers are valid for their stated sizes for the call's duration.
        let ok = unsafe { DeviceIoControl(h, FSCTL_QUERY_ALLOCATED_RANGES, &q as *const Range as _, 16,
            buf.as_mut_ptr() as _, std::mem::size_of_val(&buf) as u32, &mut got, std::ptr::null_mut()) };
        let more = ok == 0 && unsafe { GetLastError() } == 234; // ERROR_MORE_DATA
        if ok == 0 && !more {
            return vec![(0, len)];
        }
        let rs = &buf[..got as usize / 16];
        out.extend(rs.iter().map(|r| (r.off as u64, r.len as u64)));
        match (more, rs.last()) {
            (true, Some(r)) => from = r.off + r.len,
            _ => return out,
        }
    }
}

/// Copies `src` to a new sparse `dst`, writing only the 1 MiB blocks that hold data. Returns those bytes.
pub fn copy_sparse(src: &Path, dst: &Path) -> R<u64> {
    use std::os::windows::fs::{FileExt, OpenOptionsExt};
    use std::os::windows::io::AsRawHandle;
    let e = |p: &Path, x: std::io::Error| format!("{}: {x}", p.display());
    let s = std::fs::OpenOptions::new().read(true).custom_flags(0x0800_0000).open(src).map_err(|x| e(src, x))?; // sequential scan
    let len = s.metadata().map_err(|x| e(src, x))?.len();
    let _ = std::fs::remove_file(dst); // never write through a hard link
    let d = std::fs::File::create(dst).map_err(|x| e(dst, x))?;
    let mut ret = 0u32;
    // SAFETY: no buffers; the handle is open for the call.
    unsafe { DeviceIoControl(d.as_raw_handle() as isize, FSCTL_SET_SPARSE, std::ptr::null(), 0, std::ptr::null_mut(), 0, &mut ret, std::ptr::null_mut()) };
    let mut buf = vec![0u8; 1 << 20];
    let mut data = 0u64;
    for (off, n) in allocated(&s, len) {
        let mut p = off;
        while p < off + n {
            let want = ((off + n - p) as usize).min(buf.len());
            let mut got = 0;
            while got < want {
                let k = s.seek_read(&mut buf[got..want], p + got as u64).map_err(|x| e(src, x))?;
                if k == 0 {
                    break;
                }
                got += k;
            }
            if got == 0 {
                break;
            }
            let chunk = &buf[..got];
            if !chunk.chunks(16).all(|c| c.iter().all(|&b| b == 0)) {
                d.seek_write(chunk, p).map_err(|x| e(dst, x))?;
                data += got as u64;
            }
            p += got as u64;
        }
    }
    d.set_len(len).map_err(|x| e(dst, x))?;
    // Written pages count against host Available until flushed; a boot right after fell under the floor.
    d.sync_all().map_err(|x| e(dst, x))?;
    Ok(data)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn claims_warn_non_owners_and_guard_takeover() {
        let c = |r: Value| claim(r["device"].as_str().unwrap(), &r);
        c(json!({"device": "d90", "owner": "a", "note": "lane 2"})).unwrap();
        assert!(c(json!({"device": "d90", "owner": "b"})).unwrap_err().contains("claimed by \"a\""));
        assert_eq!(claim_warning(&json!({"call": "tap", "device": "d90", "owner": "a"})), None);
        assert!(claim_warning(&json!({"call": "tap", "device": "d90"})).unwrap().contains("no owner"));
        assert!(claim_warning(&json!({"call": "shell", "device": "d90", "owner": "b"})).unwrap().contains("`b`"));
        assert_eq!(claim_warning(&json!({"call": "ui_tree", "device": "d90", "owner": "b"})), None, "reads never warn");
        assert_eq!(claim_warning(&json!({"call": "tap", "device": "d91"})), None, "unclaimed");
        assert!(unclaim("d90", &json!({"owner": "b"})).is_err());
        c(json!({"device": "d90", "owner": "b", "force": true})).unwrap();
        unclaim("d90", &json!({"owner": "b"})).unwrap();
        assert!(claim_of("d90").is_null());
    }

    #[test]
    fn max_phones() {
        assert!(max_check(3, &json!({})).is_ok());
        assert!(max_check(4, &json!({})).unwrap_err().contains("max 4"));
        assert!(max_check(4, &json!({"force": true})).is_ok());
        // Two phones: ours (broker 10, parent = our boot powershell 1) and another daemon's (broker 20, parent 2).
        let rows = "10|1|0|crosvm run-mp
11|10|0|crosvm run-main
20|2|0|crosvm run-mp
21|20|0|crosvm device block
";
        assert_eq!(foreign_brokers(rows, &[1]), 1);
        assert_eq!(foreign_brokers(rows, &[]), 2);
        assert_eq!(foreign_brokers("", &[1]), 0);
    }

    #[test]
    fn manifest_meta_data() {
        // AndroidManifest.xml of EAS staging build 4cd7f3dc (1.4.0, versionCode 22).
        let m = meta_data(include_bytes!("testdata-manifest.bin"));
        assert_eq!(m["expo.modules.updates.UPDATES_CONFIGURATION_REQUEST_HEADERS_KEY"], r#"{"expo-channel-name":"staging"}"#);
        assert_eq!(m["expo.modules.updates.EXPO_UPDATE_URL"], "https://u.expo.dev/b914a0a3-22dd-4d1f-9eee-f8097d44df27");
        assert_eq!(m["expo.modules.updates.EXPO_RUNTIME_VERSION"], "@0x7f120085", "a reference: file:fingerprint");
        assert_eq!(m["com.plaid.link.react_native"], "12.4.0");
        assert!(meta_data(b"junk").is_empty());
    }

    #[test]
    fn small_parsers() {
        assert_eq!(uuid("01a12267312d727b83b03e3dd5d867ff"), "01a12267-312d-727b-83b0-3e3dd5d867ff");
        let k = kv("NET=1\nDNS=PING x (1.2.3.4) 56(84)\nversionCode=22\nUPDATE=ab|cd|12\n");
        assert_eq!((k["NET"].as_str(), k["versionCode"].as_str(), k["UPDATE"].as_str()), ("1", "22", "ab|cd|12"));
        assert_eq!(signed_in(r#"<node resource-id="home-screen" />"#), Some(true));
        assert_eq!(signed_in(r#"<node resource-id="start-screen" />"#), Some(false));
        assert_eq!(signed_in(r#"<node resource-id="wallet-main-screen" />"#), None);
        assert!(snap_dir(Path::new("W"), "lane2-golden").is_ok());
        for bad in ["", "../x", "a/b", ".hidden", "a b"] {
            assert!(snap_dir(Path::new("W"), bad).is_err(), "{bad}");
        }
        assert!(probe_cmd(APP, "h").contains("sqlite3 /data/data/com.boltbetz.staging/databases/updates.db"));
    }

    #[test]
    fn sparse_copy_keeps_bytes_skips_zeros() {
        let t = std::env::temp_dir().join(format!("ae-sparse-{}", std::process::id()));
        std::fs::create_dir_all(&t).unwrap();
        let mut v = vec![0u8; 5 << 20];
        v[3] = 7;
        v[(3 << 20) + 5] = 9;
        v[(5 << 20) - 1] = 1;
        std::fs::write(t.join("a"), &v).unwrap();
        std::fs::write(t.join("b"), b"old").unwrap();
        let data = copy_sparse(&t.join("a"), &t.join("b")).unwrap();
        assert_eq!(data, 3 << 20, "three of five 1 MiB blocks hold data");
        assert_eq!(std::fs::read(t.join("b")).unwrap(), v);
        // and a sparse source copies the same again (allocated-range path)
        copy_sparse(&t.join("b"), &t.join("c")).unwrap();
        assert_eq!(std::fs::read(t.join("c")).unwrap(), v);
        std::fs::remove_dir_all(&t).ok();
    }
}
