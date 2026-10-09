//! Fast `ui_tree`: a persistent in-guest dumper (aedump/AeDump.java) that keeps one UiAutomation connection
//! open and writes uiautomator's own XML on request. `uiautomator dump` starts a JVM and waits for idle on
//! every call (2.4 s on an RN screen). The dumper starts on the first ui_tree and again whenever it is gone.

use crate::device::{Device, R};
use base64::Engine;
use std::time::Duration;

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
/// Exit 90 = dex missing in the guest, 91 = dumper did not come up, 92 = no answer / error answer.
fn dump_cmd() -> String {
    let idle = idle_s();
    format!("D=/data/local/tmp; \
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
    Ok(o)
}

/// Before falling back to `uiautomator dump`: only one UiAutomation may be connected, so the dumper goes.
pub async fn stop(d: &Device) {
    let _ = d.con.exec("for p in $(pidof aedump); do kill $p; done; sleep 0.3", Duration::from_secs(10)).await;
}
