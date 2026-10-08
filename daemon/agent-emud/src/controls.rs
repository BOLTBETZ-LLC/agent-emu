// Structured layer 2, device controls (issue 15): deep links, GPS, camera image, clock, permissions, apps.
// Every call runs through the guest shell as root: adb (`su 0`) when AGENT_EMU_ADB_PORT is set, else the
// console. Host files (APKs, images) need adb; the console has no file transfer.
use crate::device::{Device, R};
use serde_json::{json, Value};
use std::time::Duration;
use tokio::process::Command;

pub const CALLS: &[&str] = &["deep_link", "set_location", "inject_camera_image", "clock", "permission", "app"];

/// MCP tool schemas, one per call; each takes `device`.
pub fn tools() -> Vec<Value> {
    let (s, n) = (json!({"type": "string"}), json!({"type": "number"}));
    let t = |name: &str, desc: &str, mut props: Value, req: &[&str]| {
        props["device"] = json!({"type": "string", "description": "Device id, e.g. d0"});
        let mut required = vec!["device"];
        required.extend_from_slice(req);
        json!({"name": name, "description": desc, "inputSchema": {"type": "object", "properties": props, "required": required}})
    };
    vec![
        t("deep_link", "Open a URI with an android.intent.action.VIEW intent (am start -W). Optional package restricts the target.",
            json!({"uri": s, "package": s}), &["uri"]),
        t("set_location", "Mock GPS: location on, gps test provider at lat,lon. Verified in dumpsys location.",
            json!({"lat": n, "lon": n, "accuracy": n}), &["lat", "lon"]),
        t("inject_camera_image", "Feed an image file (e.g. a QR code) to the camera. Not supported on the current guest: returns an error naming the gap.",
            json!({"path": {"type": "string", "description": "host path of a PNG/JPEG"}}), &["path"]),
        t("clock", "Guest wall clock. set = epoch ms; advance_ms = jump forward (negative = back); freeze true = stop automatic time sync (the clock still ticks), false = turn it back on. Returns guest ms before and after.",
            json!({"set": n, "advance_ms": n, "freeze": {"type": "boolean"}}), &[]),
        t("permission", "pm grant / pm revoke a runtime permission; returns the granted state from dumpsys package.",
            json!({"pkg": s, "perm": s, "action": {"type": "string", "enum": ["grant", "revoke"]}}), &["pkg", "perm", "action"]),
        t("app", "Exactly one of: install (host APK path, needs adb), uninstall, clear (pm clear), launch (launcher activity, am start -W).",
            json!({"install": s, "uninstall": s, "clear": s, "launch": s}), &[]),
    ]
}

pub async fn handle(d: &Device, call: &str, req: &Value) -> R<Value> {
    let str_arg = |k: &str| req[k].as_str().filter(|v| !v.is_empty()).ok_or(format!("missing string `{k}`"));
    let num = |k: &str| req[k].as_f64().ok_or(format!("missing number `{k}`"));
    match call {
        "deep_link" => {
            let mut cmd = format!("am start -W -a android.intent.action.VIEW -d {}", q(str_arg("uri")?));
            if let Some(p) = req["package"].as_str() {
                cmd += &format!(" {}", name(p)?);
            }
            let out = sh(d, &cmd).await?;
            if out.contains("Error") {
                return Err(out);
            }
            Ok(json!({"ok": true, "out": out}))
        }
        "set_location" => {
            let (lat, lon) = (num("lat")?, num("lon")?);
            if !(-90.0..=90.0).contains(&lat) || !(-180.0..=180.0).contains(&lon) {
                return Err("lat must be in [-90,90], lon in [-180,180]".into());
            }
            let acc = req["accuracy"].as_f64().unwrap_or(5.0);
            sh(d, &format!("cmd location set-location-enabled true && cmd location providers add-test-provider gps && \
                cmd location providers set-test-provider-enabled gps true && \
                cmd location providers set-test-provider-location gps --location {lat},{lon} --accuracy {acc}")).await?;
            let want = location_tag(lat, lon);
            let line = sh(d, &format!("dumpsys location | grep -F -m 1 {}", q(&want))).await
                .map_err(|_| format!("dumpsys location has no `{want}` after set"))?;
            Ok(json!({"ok": true, "verified": line.trim()}))
        }
        "inject_camera_image" => {
            let path = str_arg("path")?;
            if !std::path::Path::new(path).is_file() {
                return Err(format!("no such host file: {path}"));
            }
            let hal = sh(d, "getprop ro.boot.vendor.apex.com.google.emulated.camera.provider.hal").await?;
            Err(format!("unsupported: the guest camera is the AOSP emulated camera HAL ({}, \
                libgooglecamerahwl_impl.so). Its frames come from EmulatedScene, a procedural scene with no file or \
                pipe input, so an image cannot reach the camera without a guest HAL change (a provider that reads \
                frames from a file or virtio pipe). Nothing was injected.", hal.trim()))
        }
        "clock" => {
            let before = guest_ms(d).await?;
            let target = match (req["set"].as_f64(), req["advance_ms"].as_f64()) {
                (Some(_), Some(_)) => return Err("give `set` or `advance_ms`, not both".into()),
                (Some(t), None) => Some(t as i64),
                (None, Some(a)) => Some(before + a as i64),
                (None, None) => None,
            };
            let freeze = req["freeze"].as_bool();
            if target.is_none() && freeze.is_none() {
                return Err("give one of `set`, `advance_ms`, `freeze`".into());
            }
            // Automatic time detection would undo a manual time; any set/advance turns it off.
            let auto = freeze.map(|f| !f).unwrap_or(false);
            let mut cmd = format!("cmd time_detector set_auto_detection_enabled {auto}");
            if let Some(t) = target {
                if t <= 0 {
                    return Err(format!("target time {t} ms is not after 1970"));
                }
                // ms computed here: the guest sh (mksh) does 32-bit arithmetic.
                cmd += &format!(" && cmd alarm set-time {t}");
            }
            sh(d, &cmd).await?;
            let after = guest_ms(d).await?;
            let mut rep = json!({"ok": true, "before_ms": before, "after_ms": after, "auto_time": auto});
            if let Some(t) = target {
                rep["target_ms"] = json!(t);
                rep["error_ms"] = json!(after - t);
            }
            if freeze == Some(true) {
                rep["note"] = json!("automatic time sync off; the guest clock still ticks (no true stop without a hypervisor clock hook)");
            }
            Ok(rep)
        }
        "permission" => {
            let (pkg, perm) = (name(str_arg("pkg")?)?, name(str_arg("perm")?)?);
            let act = match str_arg("action")? { a @ ("grant" | "revoke") => a, a => return Err(format!("action must be grant or revoke, got {a}")) };
            sh(d, &format!("pm {act} {pkg} {perm}")).await?;
            let state = sh(d, &format!("dumpsys package {pkg} | grep -F -m 1 '{perm}: granted='")).await.unwrap_or_default();
            let granted = state.contains("granted=true");
            if granted != (act == "grant") {
                return Err(format!("pm {act} ran but dumpsys shows: {}", state.trim()));
            }
            Ok(json!({"ok": true, "granted": granted, "verified": state.trim()}))
        }
        "app" => {
            let keys: Vec<&str> = ["install", "uninstall", "clear", "launch"].into_iter().filter(|k| req[*k].is_string()).collect();
            let [k] = keys[..] else { return Err("give exactly one of install, uninstall, clear, launch".into()) };
            let v = str_arg(k)?;
            let out = match k {
                "install" => adb_host(d, &["install", "-r", v], Duration::from_secs(600)).await?,
                "uninstall" => sh(d, &format!("pm uninstall {}", name(v)?)).await?,
                "clear" => sh(d, &format!("pm clear {}", name(v)?)).await?,
                _ => {
                    let p = name(v)?;
                    let act = sh(d, &format!("cmd package resolve-activity --brief -c android.intent.category.LAUNCHER {p} | tail -n 1")).await?;
                    let act = act.trim();
                    if !act.contains('/') {
                        return Err(format!("no launcher activity for {p}: {act}"));
                    }
                    sh(d, &format!("am start -W -n {}", q(act))).await?
                }
            };
            let ok = if k == "launch" { out.contains("Status: ok") } else { out.contains("Success") };
            if !ok {
                return Err(format!("app {k}: {out}"));
            }
            Ok(json!({"ok": true, "out": out.trim()}))
        }
        _ => Err(format!("unknown control `{call}`")),
    }
}

/// AGENT_EMU_ADB_PORT if set, else the Device's own forward (127.0.0.1:6520+idx) when it has net.
fn adb_port(d: Option<&Device>) -> Option<String> {
    std::env::var("AGENT_EMU_ADB_PORT").ok().filter(|p| !p.is_empty())
        .or_else(|| d.filter(|d| d.net).map(|d| (6520 + d.idx()).to_string()))
}

/// Root shell in the guest; non-zero exit is an error carrying the output.
async fn sh(d: &Device, cmd: &str) -> R<String> {
    let (out, code) = match adb_port(None) {
        Some(_) => {
            let o = adb_raw(None, &["shell", &format!("su 0 sh -c {}", q(cmd))], Duration::from_secs(120)).await?;
            (o.0, o.1)
        }
        None => d.con.exec(cmd, Duration::from_secs(120)).await?,
    };
    if code != 0 {
        return Err(format!("`{cmd}` exit {code}: {}", out.trim()));
    }
    Ok(out)
}

/// adb against the Device; host-file operations need it.
async fn adb_host(d: &Device, args: &[&str], timeout: Duration) -> R<String> {
    if adb_port(Some(d)).is_none() {
        return Err("needs adb: boot the Device with net on (or set AGENT_EMU_ADB_PORT)".into());
    }
    let (out, code) = adb_raw(Some(d), args, timeout).await?;
    if code != 0 {
        return Err(format!("adb {} exit {code}: {}", args.join(" "), out.trim()));
    }
    Ok(out)
}

async fn adb_raw(d: Option<&Device>, args: &[&str], timeout: Duration) -> R<(String, i32)> {
    let serial = format!("127.0.0.1:{}", adb_port(d).ok_or("no adb port for this Device")?);
    let run = |a: Vec<&str>| {
        let mut c = Command::new("adb");
        c.args(a).kill_on_drop(true);
        async move { tokio::time::timeout(timeout, c.output()).await }
    };
    // Idempotent; also re-attaches after an adb server restart.
    let _ = run(vec!["connect", &serial]).await;
    let mut a = vec!["-s", serial.as_str()];
    a.extend_from_slice(args);
    let o = run(a).await.map_err(|_| format!("adb {} timed out", args.join(" ")))?.map_err(|e| format!("adb: {e}"))?;
    let out = format!("{}{}", String::from_utf8_lossy(&o.stdout), String::from_utf8_lossy(&o.stderr)).replace('\r', "");
    Ok((out, o.status.code().unwrap_or(-1)))
}

async fn guest_ms(d: &Device) -> R<i64> {
    let o = sh(d, "date +%s%3N").await?;
    o.trim().parse().map_err(|_| format!("guest date: {o}"))
}

/// Single-quote for the guest sh.
fn q(s: &str) -> String {
    format!("'{}'", s.replace('\'', r"'\''"))
}

/// Package or permission name: only [A-Za-z0-9._] reaches the shell.
fn name(s: &str) -> R<&str> {
    if !s.is_empty() && s.chars().all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '_') {
        Ok(s)
    } else {
        Err(format!("bad package or permission name: {s}"))
    }
}

/// How dumpsys location prints a gps fix: `Location[gps 36.114700,-115.172800`.
fn location_tag(lat: f64, lon: f64) -> String {
    format!("Location[gps {lat:.6},{lon:.6}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn quoting_and_names() {
        assert_eq!(q("a'b"), r"'a'\''b'");
        assert_eq!(name("com.boltbetz.staging").unwrap(), "com.boltbetz.staging");
        assert_eq!(name("android.permission.CAMERA").unwrap(), "android.permission.CAMERA");
        assert!(name("x; reboot").is_err() && name("").is_err());
    }

    #[test]
    fn location_matches_dumpsys() {
        let line = "last location=Location[gps 36.114700,-115.172800 hAcc=100.0 et=+2m32s324ms mock]";
        assert!(line.contains(&location_tag(36.1147, -115.1728)));
    }

    #[test]
    fn every_tool_takes_device_and_is_a_call() {
        let t = tools();
        assert_eq!(t.len(), CALLS.len());
        for (t, c) in t.iter().zip(CALLS) {
            assert_eq!(t["name"], *c);
            assert_eq!(t["inputSchema"]["required"][0], "device");
        }
    }
}
