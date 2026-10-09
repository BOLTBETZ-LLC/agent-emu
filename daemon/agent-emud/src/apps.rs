//! The app under test is pluggable. `install_app` puts any Android build on running phones; `set_default_app` makes
//! it the image's app (apk.img on /dev/block/vdb), which phones started without keep_data install at boot and
//! `install_bundled` / `fleet` install. A source is any of: a local .apk path, an https URL of an APK
//! (expo.dev/artifacts/eas/....apk included), an expo.dev build page URL, or an EAS build id (UUID).
//! Builds are cached under <work>/app-cache by source, so a second install of the same source downloads nothing.
use crate::device::{Device, R};
use crate::{controls, State};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;
use tokio::process::Command;

pub const CALLS: &[&str] = &["install_app", "set_default_app", "default_app"];

pub const SOURCES: &str = "source = a local .apk path, an https URL of an .apk (e.g. https://expo.dev/artifacts/eas/<x>.apk), \
an expo.dev build page URL (https://expo.dev/accounts/<a>/projects/<p>/builds/<uuid>) or an EAS build id (UUID; resolved with the \
logged-in eas CLI, run in eas_project or AE_EAS_PROJECT_DIR = any Expo project folder of that account)";

pub async fn handle(st: &Arc<State>, call: &str, req: &Value) -> R<Value> {
    if call == "default_app" {
        let f = st.cfg.work.join("default-app.json");
        return Ok(json!({"ok": true, "default_app": std::fs::read_to_string(f).ok().and_then(|s| serde_json::from_str::<Value>(&s).ok())}));
    }
    let source = req["source"].as_str().map(str::trim).filter(|s| !s.is_empty()).ok_or(format!("missing `source`: {SOURCES}"))?;
    let apk = resolve(&st.cfg.work, source, req["eas_project"].as_str()).await?;
    let mut info = apk_info(&apk).await?;
    info["source"] = json!(source);
    info["apk"] = json!(apk.to_string_lossy());
    if call == "set_default_app" {
        set_default(&st.cfg.work, &apk, &info)?;
        return Ok(json!({"ok": true, "default_app": info,
            "note": "phones started from now on (without keep_data) install it at boot; running phones keep theirs until restarted"}));
    }
    let ids: Vec<String> = match (&req["devices"], req["device"].as_str()) {
        (Value::Array(a), _) => a.iter().filter_map(|v| v.as_str().map(str::to_string)).collect(),
        (_, Some(d)) => vec![d.to_string()],
        _ => return Err("give `device` or `devices`".into()),
    };
    let mut jobs = vec![];
    for id in ids {
        let d = st.devs.lock().unwrap().get(&id).map(|s| s.dev.clone());
        let (apk, pkg) = (apk.clone(), info["package"].as_str().unwrap_or("").to_string());
        jobs.push((id.clone(), tokio::spawn(async move {
            let d = d.ok_or(format!("no Device `{id}`"))?;
            install_on(&d, &apk, &pkg).await
        })));
    }
    let mut rows = vec![];
    let mut all_ok = true;
    for (id, j) in jobs {
        let r = j.await.map_err(|e| e.to_string()).and_then(|r| r);
        all_ok &= r.is_ok();
        rows.push(match r { Ok(v) => json!({"device": id, "ok": true, "installed": v}), Err(e) => json!({"device": id, "ok": false, "error": e}) });
    }
    Ok(json!({"ok": all_ok, "app": info, "devices": rows}))
}

async fn install_on(d: &Device, apk: &Path, pkg: &str) -> R<Value> {
    d.touch();
    let out = controls::handle(d, "app", &json!({"install": apk.to_string_lossy()})).await?;
    let (v, _) = d.con.exec(&format!("dumpsys package {pkg} | grep -m 2 -oE 'version(Code|Name)=[^ ]+' 2>/dev/null"), Duration::from_secs(30)).await?;
    Ok(json!({"out": out["out"], "on_phone": v.split_whitespace().filter(|w| w.starts_with("version")).collect::<Vec<_>>()}))
}

/// Install the image's app (apk.img on vdb, `size` bytes) over the console: no adb needed.
pub async fn install_image_apk(d: &Device, size: u64) -> R<String> {
    if size == 0 {
        return Err(format!("no default app on this image yet: call set_default_app {{source}} ({SOURCES}), or install_app"));
    }
    let (o, _) = d.con.exec(&format!("head -c {size} /dev/block/vdb > /data/local/tmp/p.apk && chmod 644 /data/local/tmp/p.apk && \
        pm install -r /data/local/tmp/p.apk; rm -f /data/local/tmp/p.apk"), Duration::from_secs(300)).await?;
    if !o.contains("Success") {
        return Err(format!("install: {o}"));
    }
    // DAX images: bind the app's code from pmem1 over the fresh install (vendor appdax.rc; no-op elsewhere).
    d.con.exec("setprop sys.agentemu.appdax 0; setprop sys.agentemu.appdax 1", Duration::from_secs(10)).await?;
    Ok(o.trim().to_string())
}

/// apk.size linked into the Device dir at start (0 or missing = the image has no app).
pub fn image_apk_size(d: &Device) -> u64 {
    std::fs::read_to_string(d.dir.join("apk.size")).ok().and_then(|s| s.trim().parse().ok()).unwrap_or(0)
}

// ---------- source -> local APK ----------

async fn resolve(work: &Path, source: &str, eas_project: Option<&str>) -> R<PathBuf> {
    let src = source.trim_matches('"');
    let p = Path::new(src);
    if !src.starts_with("http") && p.is_file() {
        return check_kind(p.to_path_buf()).await;
    }
    if let Some(id) = uuid_in(src).filter(|_| !src.starts_with("http") || src.contains("/builds/")) {
        let url = eas_build_url(&id, eas_project).await?;
        return check_kind(download(work, &url).await?).await;
    }
    if src.starts_with("https://") || src.starts_with("http://") {
        return check_kind(download(work, src).await?).await;
    }
    Err(format!("source `{src}` is not a file on this PC, a URL or an EAS build id. {SOURCES}"))
}

fn uuid_in(s: &str) -> Option<String> {
    let b = s.as_bytes();
    (0..b.len().saturating_sub(35)).map(|i| &s[i..i + 36]).find(|c| {
        c.char_indices().all(|(i, ch)| if [8, 13, 18, 23].contains(&i) { ch == '-' } else { ch.is_ascii_hexdigit() })
    }).map(str::to_lowercase)
}

/// EAS build id -> artifact URL with `eas build:view` (works on any build the logged-in account can see; the CLI
/// only needs to run inside some Expo project folder).
async fn eas_build_url(id: &str, project: Option<&str>) -> R<String> {
    let dir = project.map(str::to_string).or_else(|| std::env::var("AE_EAS_PROJECT_DIR").ok()).ok_or(format!(
        "EAS build {id}: pass eas_project (any Expo project folder) or set AE_EAS_PROJECT_DIR, or give the build's artifact URL (https://expo.dev/artifacts/eas/...)"))?;
    let o = Command::new("cmd").args(["/d", "/c", "eas", "build:view", id, "--json"]).current_dir(&dir)
        .env("EAS_NO_VCS", "1").env("EXPO_NO_TELEMETRY", "1").kill_on_drop(true).output();
    let o = tokio::time::timeout(Duration::from_secs(120), o).await.map_err(|_| "eas build:view timed out".to_string())?
        .map_err(|e| format!("eas CLI not runnable ({e}); install it with `npm i -g eas-cli` and `eas login`"))?;
    let out = String::from_utf8_lossy(&o.stdout);
    let v: Value = out.find('{').and_then(|i| serde_json::from_str(&out[i..]).ok()).ok_or_else(|| {
        let err = String::from_utf8_lossy(&o.stderr);
        let err = err.lines().filter(|l| !l.contains("eas-cli@") && !l.contains("upgrade") && !l.trim().is_empty()).collect::<Vec<_>>().join(" ");
        format!("eas build:view {id} failed (logged in? `eas whoami`): {err}")
    })?;
    if v["platform"].as_str().is_some_and(|p| !p.eq_ignore_ascii_case("android")) {
        return Err(format!("EAS build {id} is {}, not Android", v["platform"]));
    }
    v["artifacts"]["buildUrl"].as_str().or(v["artifacts"]["applicationArchiveUrl"].as_str()).map(str::to_string)
        .ok_or(format!("EAS build {id} has no artifact (status {})", v["status"]))
}

/// Resumable download into <work>/app-cache, named by the URL.
async fn download(work: &Path, url: &str) -> R<PathBuf> {
    let dir = work.join("app-cache");
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let h = url.bytes().fold(0xcbf2_9ce4_8422_2325u64, |h, b| (h ^ b as u64).wrapping_mul(0x100_0000_01b3)); // FNV-1a
    let ext = url.split(['?', '#']).next().unwrap_or("").rsplit('.').next().filter(|e| e.len() <= 4).unwrap_or("bin");
    let file = dir.join(format!("{h:016x}.{}", ext.to_lowercase()));
    if file.is_file() {
        return Ok(file);
    }
    let part = file.with_extension("part");
    let o = Command::new("curl.exe").args(["-L", "--fail", "-sS", "--retry", "3", "-C", "-", "-o"]).arg(&part).arg(url)
        .kill_on_drop(true).output();
    let o = tokio::time::timeout(Duration::from_secs(1800), o).await.map_err(|_| format!("download {url} timed out"))?
        .map_err(|e| format!("curl: {e}"))?;
    if !o.status.success() {
        return Err(format!("download {url}: {}", String::from_utf8_lossy(&o.stderr).trim()));
    }
    std::fs::rename(&part, &file).map_err(|e| e.to_string())?;
    Ok(file)
}

/// The zip's entry list says what it is: APK (AndroidManifest.xml at the top) or an App Bundle.
async fn check_kind(p: PathBuf) -> R<PathBuf> {
    let names = tar(&["-t"], &p).await?;
    let names = String::from_utf8_lossy(&names);
    if names.lines().any(|l| l == "AndroidManifest.xml") {
        return Ok(p);
    }
    if names.lines().any(|l| l == "BundleConfig.pb" || l == "base/manifest/AndroidManifest.xml") {
        return Err(format!("{} is an Android App Bundle (.aab): phones install APKs only. Use an APK build \
            (EAS profile with android.buildType \"apk\") or convert it with bundletool build-apks --mode universal", p.display()));
    }
    Err(format!("{} is not an Android APK", p.display()))
}

async fn tar(args: &[&str], zip: &Path) -> R<Vec<u8>> {
    let tar = std::env::var("SystemRoot").map(|r| format!("{r}\\System32\\tar.exe")).unwrap_or("tar.exe".into());
    let o = Command::new(tar).args(args).arg("-f").arg(zip).kill_on_drop(true).output().await.map_err(|e| format!("tar: {e}"))?;
    if !o.status.success() {
        return Err(format!("not a zip ({}): {}", zip.display(), String::from_utf8_lossy(&o.stderr).trim()));
    }
    Ok(o.stdout)
}

/// package, version and Expo runtime/channel straight from the APK's binary AndroidManifest.xml.
async fn apk_info(apk: &Path) -> R<Value> {
    let m = member(apk, "AndroidManifest.xml").await?;
    let (pkg, vname, vcode) = manifest_attrs(&m);
    let pkg = pkg.ok_or("AndroidManifest.xml has no package")?;
    let meta = crate::ops::meta_data(&m);
    let fp = member(apk, "assets/fingerprint").await.ok().map(|b| String::from_utf8_lossy(&b).trim().to_string()).filter(|s| !s.is_empty());
    let runtime = meta.get("expo.modules.updates.EXPO_RUNTIME_VERSION").filter(|v| !v.starts_with('@')).cloned().or(fp);
    let channel = meta.get("expo.modules.updates.UPDATES_CONFIGURATION_REQUEST_HEADERS_KEY")
        .and_then(|h| serde_json::from_str::<Value>(h).ok()).and_then(|h| h["expo-channel-name"].as_str().map(str::to_string));
    Ok(json!({"package": pkg, "version_name": vname, "version_code": vcode, "runtime": runtime, "channel": channel,
        "bytes": std::fs::metadata(apk).map(|m| m.len()).unwrap_or(0)}))
}

async fn member(zip: &Path, name: &str) -> R<Vec<u8>> {
    let tar = std::env::var("SystemRoot").map(|r| format!("{r}\\System32\\tar.exe")).unwrap_or("tar.exe".into());
    let o = Command::new(tar).arg("-xOf").arg(zip).arg(name).kill_on_drop(true).output().await.map_err(|e| format!("tar: {e}"))?;
    if !o.status.success() || o.stdout.is_empty() {
        return Err(format!("{name} not in {}", zip.display()));
    }
    Ok(o.stdout)
}

/// <manifest package=".." android:versionName=".." android:versionCode=".."> from binary XML.
fn manifest_attrs(b: &[u8]) -> (Option<String>, Option<String>, Option<u64>) {
    let u16_at = |p: usize| b.get(p..p + 2).map(|s| u16::from_le_bytes([s[0], s[1]]) as usize);
    let u32_at = |p: usize| b.get(p..p + 4).map(|s| u32::from_le_bytes([s[0], s[1], s[2], s[3]]) as usize);
    let (mut strings, mut pos) = (vec![], 8);
    while let (Some(t), Some(hs), Some(sz)) = (u16_at(pos), u16_at(pos + 2), u32_at(pos + 4)) {
        if sz == 0 {
            break;
        }
        if t == 0x0001 {
            strings = crate::ops::string_pool(b, pos, hs).unwrap_or_default();
        } else if t == 0x0102 {
            let s = |i: usize| strings.get(i).cloned().unwrap_or_default();
            if let (Some(name), Some(at), Some(asz), Some(n)) = (u32_at(pos + 20), u16_at(pos + 24), u16_at(pos + 26), u16_at(pos + 28)) {
                if s(name) == "manifest" {
                    let (mut pkg, mut vn, mut vc) = (None, None, None);
                    for i in 0..n {
                        let a = pos + 16 + at + i * asz;
                        let (Some(an), Some(raw), Some(dt), Some(data)) = (u32_at(a + 4), u32_at(a + 8), b.get(a + 15), u32_at(a + 16)) else { break };
                        let text = if raw != 0xFFFF_FFFF { Some(s(raw)) } else if *dt == 3 { Some(s(data)) } else { None };
                        match s(an).as_str() {
                            "package" => pkg = text,
                            "versionName" => vn = text,
                            "versionCode" => vc = text.and_then(|t| t.parse().ok()).or(Some(data as u64)),
                            _ => {}
                        }
                    }
                    return (pkg, vn, vc);
                }
            }
        }
        pos += sz;
    }
    (None, None, None)
}

/// The APK becomes the image's app: <work>/apk/<stem>.img (padded to 4 KiB) + .size, hard-linked as apk.img/apk.size
/// into every run dir (what set-bundled-apk.ps1 does). Running phones keep their own links until restarted.
fn set_default(work: &Path, apk: &Path, info: &Value) -> R<()> {
    let clean = |s: &str| s.chars().map(|c| if c.is_ascii_alphanumeric() || c == '.' { c } else { '_' }).collect::<String>();
    let stem = format!("{}-{}-{}", clean(info["package"].as_str().unwrap_or("app")), clean(info["version_name"].as_str().unwrap_or("x")),
        info["bytes"]);
    let dir = work.join("apk");
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let (img, size) = (dir.join(format!("{stem}.img")), dir.join(format!("{stem}.size")));
    let len = std::fs::metadata(apk).map_err(|e| e.to_string())?.len();
    if !img.is_file() {
        let tmp = img.with_extension("tmp");
        std::fs::copy(apk, &tmp).map_err(|e| format!("copy apk: {e}"))?;
        std::fs::OpenOptions::new().write(true).open(&tmp).and_then(|f| f.set_len(len.div_ceil(4096) * 4096)).map_err(|e| e.to_string())?;
        std::fs::rename(&tmp, &img).map_err(|e| e.to_string())?;
    }
    std::fs::write(&size, len.to_string()).map_err(|e| e.to_string())?;
    let mut linked = 0;
    for e in std::fs::read_dir(work).map_err(|e| e.to_string())?.flatten() {
        let run = e.path();
        if !run.join("super.img").is_file() || !e.file_name().to_string_lossy().starts_with("run") {
            continue;
        }
        for (src, name) in [(&img, "apk.img"), (&size, "apk.size")] {
            let _ = std::fs::remove_file(run.join(name));
            std::fs::hard_link(src, run.join(name)).map_err(|e| format!("link {name} into {}: {e}", run.display()))?;
        }
        linked += 1;
    }
    if linked == 0 {
        return Err(format!("no image run dir under {}", work.display()));
    }
    let mut rec = info.clone();
    rec["image_file"] = json!(img.to_string_lossy());
    std::fs::write(work.join("default-app.json"), serde_json::to_string_pretty(&rec).unwrap_or_default()).map_err(|e| e.to_string())?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn finds_build_ids() {
        assert_eq!(uuid_in("https://expo.dev/accounts/a/projects/p/builds/4cd7f3dc-7570-4c66-b85f-349fe5b5e3a9").as_deref(),
            Some("4cd7f3dc-7570-4c66-b85f-349fe5b5e3a9"));
        assert_eq!(uuid_in("4CD7F3DC-7570-4C66-B85F-349FE5B5E3A9").as_deref(), Some("4cd7f3dc-7570-4c66-b85f-349fe5b5e3a9"));
        assert_eq!(uuid_in("https://expo.dev/artifacts/eas/PrDreXFiALCuSSTALkhmPQvdrc.apk"), None);
    }
}
