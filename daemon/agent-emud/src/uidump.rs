//! Fast `ui_tree`: a persistent in-guest dumper (aedump/AeDump.java) that keeps one UiAutomation connection
//! open and writes uiautomator's own XML on request. `uiautomator dump` starts a JVM and waits for idle on
//! every call (2.4 s on an RN screen). The dumper starts on the first ui_tree and again whenever it is gone.

use crate::device::{Device, R};
use base64::Engine;
use std::collections::HashMap;
use std::sync::Mutex;
use std::time::Duration;

/// Start of the `ui_tree` error while an Appium UiAutomator2 server holds the phone.
pub const YIELDED: &str = "yielded to Appium (UiAutomator2 running)";
const YIELDED_MSG: &str = "yielded to Appium (UiAutomator2 running); use Appium or stop its session";
/// Guest check for Appium's UiAutomator2 server; its test package (`...server.test`) matches too.
/// AE_APPIUM_PROC swaps the process name (tests stand in a throwaway process for Appium).
fn appium_ps() -> String {
    format!("ps -A -o NAME | grep -q {}", std::env::var("AE_APPIUM_PROC").unwrap_or_else(|_| "io.appium.uiautomator2.server".into()))
}

/// Per Device: (Appium seen and not yet gone, epoch ms of the last dump that worked).
static READER: Mutex<Option<HashMap<String, (bool, u64)>>> = Mutex::new(None);

fn mark(id: &str, f: impl FnOnce(&mut (bool, u64))) {
    f(READER.lock().unwrap().get_or_insert_with(Default::default).entry(id.to_string()).or_default());
}

/// `status.ui_reader`: "yielded_appium" while Appium holds the phone, "on" while a dump worked within the dumper's
/// idle time (the in-guest dumper is alive), else "off" (never started, idled out, stopped, or phone asleep).
pub fn state(id: &str) -> &'static str {
    let (appium, last) = READER.lock().unwrap().as_ref().and_then(|m| m.get(id).copied()).unwrap_or_default();
    if appium {
        "yielded_appium"
    } else if last > 0 && (idle_s() == 0 || crate::device::now_ms().saturating_sub(last) < idle_s() * 1000) {
        "on"
    } else {
        "off"
    }
}

/// Phones whose reader ran in the last two minutes, or that are yielded: the ones the watcher looks at.
fn watched() -> Vec<(String, bool)> {
    let now = crate::device::now_ms();
    READER.lock().unwrap().as_ref().map(|m| m.iter()
        .filter(|(_, &(a, last))| a || (last > 0 && now.saturating_sub(last) < 120_000)).map(|(k, &(a, _))| (k.clone(), a)).collect()).unwrap_or_default()
}

/// Every 2 s, for phones whose reader ran lately: if Appium's UiAutomator2 server is up, drop our dumper (two
/// UiAutomation connections kill Appium's session: "instrumentation process is not running"); once it is gone,
/// resume. Only the `aedump` process is ever killed, never Appium's.
pub async fn watch(st: std::sync::Arc<crate::State>) {
    loop {
        tokio::time::sleep(Duration::from_secs(2)).await;
        for (id, yielded) in watched() {
            let Some(d) = st.dev(&id) else { continue };
            let Ok((_, code)) = d.con.exec(&appium_ps(), Duration::from_secs(10)).await else { continue };
            if code == 0 && !yielded {
                stop(&d).await;
                mark(&id, |m| m.0 = true);
            } else if code != 0 && yielded {
                mark(&id, |m| m.0 = false);
            }
        }
    }
}

const DEX: &[u8] = include_bytes!("../aedump/aedump.dex");
/// Bump the name when AeDump.java changes, so a guest with the old dex gets the new one.
const DEX_PATH: &str = "/data/local/tmp/aedump3.dex";
/// The dumper exits after this many seconds without a request (~42 MB PSS while it lives); the next ui_tree
/// starts it again (~0.5 s). AE_UI_DUMPER_IDLE_S overrides; 0 keeps it alive.
fn idle_s() -> u64 {
    std::env::var("AE_UI_DUMPER_IDLE_S").ok().and_then(|v| v.parse().ok()).unwrap_or(30)
}

/// No boot image (-Ximage at a missing file): outside the zygote, ART copies the boot image into ~33 MB of private
/// memory; without it the dumper loads only the classes it uses (anon 42.6 -> 16.9 MB, same XML, start ~0.4 s).
/// One console round trip: start the dumper if it isn't running, ask for a dump, print it.
/// Exit 93 = an Appium UiAutomator2 server holds the one UiAutomation, 90 = dex missing in the guest, 91 = dumper did not come up, 92 = no answer / error answer.
fn dump_cmd() -> String {
    let (idle, appium_ps) = (idle_s(), appium_ps());
    format!("D=/data/local/tmp; \
        if {appium_ps}; then exit 93; fi; \
        if ! {{ pidof aedump >/dev/null && [ -p $D/ae.req ]; }}; then \
          [ -f {DEX_PATH} ] || exit 90; rm -f $D/ae.req $D/ae.res; \
          p=$(pidof system_server); for v in $(tr '\\0' '\\n' < /proc/$p/environ | grep CLASSPATH=); do export \"$v\"; done; \
          setsid nsenter -m -t $p -- sh -c 'CLASSPATH={DEX_PATH}:/system/framework/uiautomator.jar \
            exec app_process -Ximage:/aedump-no-boot-image -Xnoimage-dex2oat /system/bin --nice-name=aedump AeDump {idle}' </dev/null >$D/aedump.log 2>&1 & \
          i=0; while [ ! -p $D/ae.req ]; do i=$((i+1)); [ $i -gt 100 ] && exit 91; sleep 0.1; done; \
        fi; \
        r=$(timeout 5 sh -c 'echo d > /data/local/tmp/ae.req && head -n 1 /data/local/tmp/ae.res'); \
        [ \"$r\" = ok ] || {{ echo \"aedump: $r\"; exit 92; }}; cat $D/ae.xml")
}

async fn upload_dex(d: &Device) -> R<()> {
    let b64 = base64::engine::general_purpose::STANDARD.encode(DEX);
    // Small pieces: the console is a tty, whose line buffer holds 4096 bytes.
    d.con.exec("rm -f /data/local/tmp/aedump.b64", Duration::from_secs(10)).await?;
    for c in b64.as_bytes().chunks(2000) {
        let c = std::str::from_utf8(c).unwrap();
        d.con.exec(&format!("echo -n {c} >> /data/local/tmp/aedump.b64"), Duration::from_secs(10)).await?;
    }
    let (o, code) = d.con.exec(&format!("base64 -d /data/local/tmp/aedump.b64 > {DEX_PATH}.tmp && \
        mv {DEX_PATH}.tmp {DEX_PATH} && rm /data/local/tmp/aedump.b64"), Duration::from_secs(10)).await?;
    if code != 0 {
        return Err(format!("aedump upload: {o}"));
    }
    Ok(())
}

/// The dump from the persistent dumper.
pub async fn fast(d: &Device) -> R<String> {
    let cmd = dump_cmd();
    let (mut o, mut code) = d.con.exec(&cmd, Duration::from_secs(20)).await?;
    if code == 93 {
        mark(&d.id, |m| m.0 = true);
        return Err(YIELDED_MSG.into());
    }
    if code == 90 {
        upload_dex(d).await?;
        (o, code) = d.con.exec(&cmd, Duration::from_secs(20)).await?;
    }
    if code == 92 {
        // The dumper may have hit its idle exit just as this request came in: once more starts a new one.
        (o, code) = d.con.exec(&cmd, Duration::from_secs(20)).await?;
    }
    if code != 0 {
        return Err(format!("aedump exit {code}: {o}"));
    }
    mark(&d.id, |m| *m = (false, crate::device::now_ms()));
    Ok(o)
}

/// Before falling back to `uiautomator dump`: only one UiAutomation may be connected, so the dumper goes.
pub async fn stop(d: &Device) {
    mark(&d.id, |m| m.1 = 0);
    let _ = d.con.exec("for p in $(pidof aedump); do kill $p; done; sleep 0.3", Duration::from_secs(10)).await;
}
