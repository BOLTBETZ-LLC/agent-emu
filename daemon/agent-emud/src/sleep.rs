//! `sleep` / `wake`: a Device between test batches keeps running (apps, sign-in, booted Android) but sits at its idle
//! caps with the screen reader off, so it costs about what an idle phone costs and comes back in seconds, where a
//! stop + wiped start takes minutes. Phase `sleeping` is a daemon-side label on a phone that is really `ready`.

use crate::device::{self, R};
use crate::{emit, ids, idx_of, ops, squeeze, uidump, State};
use serde_json::{json, Value};
use std::sync::Arc;
use std::time::Instant;

pub const CALLS: &[&str] = &["sleep", "wake", "sleep_many", "wake_many"];

/// Calls that leave a sleeping phone asleep: reads of the host side, claims, the lease, and the batch calls above.
const PASSIVE: &[&str] = &["status", "stop", "stop_many", "start", "start_many", "memory", "crash_events", "logs", "issues", "lease", "release",
    "claim", "unclaim", "health", "bugs", "squeeze", "fleet", "fleet_stop", "restart", "quit"];

pub fn is_sleeping(st: &State, id: &str) -> bool {
    st.devs.lock().unwrap().get(id).is_some_and(|s| s.phase == "sleeping")
}

fn set_phase(st: &State, id: &str, p: &'static str) {
    if let Some(s) = st.devs.lock().unwrap().get_mut(id) {
        (s.phase, s.phase_since) = (p, device::now_ms());
    }
    emit(st, json!({"type": "device", "device": id, "phase": p}));
}

/// Any other call that touches a sleeping phone wakes it first (the caps would otherwise stall the input).
pub async fn wake_for(st: &State, call: &str, req: &Value) {
    if PASSIVE.contains(&call) || CALLS.contains(&call) {
        return;
    }
    let mut list: Vec<String> = req["device"].as_str().map(str::to_string).into_iter().collect();
    list.extend(req["devices"].as_array().into_iter().flatten().filter_map(|v| v.as_str().map(str::to_string)));
    for id in list {
        if is_sleeping(st, &id) {
            if let Err(e) = wake(st, &id).await {
                eprintln!("{id}: wake before {call}: {e}");
            }
        }
    }
}

pub async fn handle(st: &Arc<State>, call: &str, req: &Value) -> R<Value> {
    match call {
        "sleep" | "wake" => {
            let id = req["device"].as_str().unwrap_or("d0");
            idx_of(id)?;
            if call == "sleep" { sleep(st, id).await } else { wake(st, id).await }
        }
        _ => {
            // `devices` (default: every Device that is in the state the call needs).
            let sleeping = call == "wake_many";
            let list = ids(req)?.unwrap_or_else(|| {
                st.devs.lock().unwrap().iter().filter(|(_, s)| (s.phase == "sleeping") == sleeping).map(|(k, _)| k.clone()).collect()
            });
            let mut rows = vec![];
            for id in list {
                let r = if sleeping { wake(st, &id).await } else { sleep(st, &id).await };
                rows.push(match r {
                    Ok(_) => json!({"device": id, "ok": true}),
                    Err(e) => json!({"device": id, "ok": false, "error": e}),
                });
            }
            Ok(json!({"ok": rows.iter().all(|r| r["ok"] == json!(true)), "devices": rows}))
        }
    }
}

/// Puts a ready phone to sleep: reader off, idle caps on now. Refused while a test run holds it or it still boots.
pub async fn sleep(st: &State, id: &str) -> R<Value> {
    let (d, phase) = {
        let devs = st.devs.lock().unwrap();
        let s = devs.get(id).ok_or(format!("no Device `{id}`; nothing to sleep (stopped phones start with `start`)"))?;
        (s.dev.clone(), s.phase)
    };
    match phase {
        "sleeping" => return Ok(json!({"ok": true, "device": id, "phase": "sleeping", "already": true})),
        "ready" => {}
        p => return Err(format!("Device `{id}` is still booting (phase `{p}`); only a ready phone can sleep")),
    }
    let claim = ops::claim_of(id);
    if let Some(owner) = claim["owner"].as_str().filter(|o| o.starts_with("run.py-")) {
        return Err(format!("Device `{id}` is claimed by `{owner}`, a test run; it can sleep after the run ends"));
    }
    // Not "in use" any more, so the balloon and the idle re-cap take over; the screen reader's UiAutomation goes too.
    d.last_active_ms.store(0, std::sync::atomic::Ordering::SeqCst);
    uidump::stop(&d).await;
    let capped = squeeze::apply_caps(&d)?;
    set_phase(st, id, "sleeping");
    Ok(json!({"ok": true, "device": id, "phase": "sleeping", "capped_processes": capped.len()}))
}

/// Lifts the caps and marks the phone ready again; the first tap after this is like the first tap after any idle.
pub async fn wake(st: &State, id: &str) -> R<Value> {
    let t = Instant::now();
    let (d, phase) = {
        let devs = st.devs.lock().unwrap();
        let s = devs.get(id).ok_or(format!("Device `{id}` is not running; use `start` (keep_data keeps its disk)"))?;
        (s.dev.clone(), s.phase)
    };
    match phase {
        "ready" => return Ok(json!({"ok": true, "device": id, "phase": "ready", "already": true})),
        "sleeping" => {}
        p => return Err(format!("Device `{id}` is still booting (phase `{p}`)")),
    }
    d.touch();
    set_phase(st, id, "ready");
    Ok(json!({"ok": true, "device": id, "phase": "ready", "woke_ms": t.elapsed().as_millis() as u64}))
}
