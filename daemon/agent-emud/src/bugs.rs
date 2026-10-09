// Live bug feed. The test runner posts every FAIL as an occurrence; occurrences with the same dedupe key fold
// into one bug. Every occurrence is appended to bugs.jsonl (AE_BUGS_FILE, default %LOCALAPPDATA%\agent-emu\bugs.jsonl;
// the bug list is rebuilt from it on start) and pushed as a `bug` event on /events.
//   {"call":"bug","case":"M-home-005-...","area":"home","step":"load","endpoint":"getTransactions","check":"ui:x:present",...}
//       HTTP: POST /bugs with the occurrence as the body
//   {"call":"bugs","status":"open","kind":"app","area":"home","case":"M-home","q":"pin","full":true,"limit":50}
//       HTTP: GET /bugs?status=open&full=1
// Dedupe key = area|step|endpoint|check (the first failed check, or the error with digits folded). The variant
// (fault preset, Synkros code, lifecycle, amount, phone, lane, attempt, build) is not in the key.
use crate::device::{self, R};
use crate::State;
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::io::{BufRead, Write};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

pub const CALLS: &[&str] = &["bug", "bugs"];
/// Occurrences kept per bug in memory (the file keeps all of them).
const KEEP: usize = 30;
const MAX_LINE: usize = 256 << 10;

struct Book {
    path: PathBuf,
    bugs: BTreeMap<String, Value>,
}

static BOOK: Mutex<Option<Book>> = Mutex::new(None);

fn path() -> PathBuf {
    std::env::var_os("AE_BUGS_FILE").map(PathBuf::from).unwrap_or_else(|| {
        PathBuf::from(std::env::var_os("LOCALAPPDATA").unwrap_or_default()).join("agent-emu").join("bugs.jsonl")
    })
}

/// The bug list, loaded from the file on first use.
fn with_book<T>(f: impl FnOnce(&mut Book) -> T) -> T {
    let mut g = BOOK.lock().unwrap();
    let b = g.get_or_insert_with(|| load(path()));
    f(b)
}

fn load(path: PathBuf) -> Book {
    let mut b = Book { path, bugs: BTreeMap::new() };
    if let Ok(f) = std::fs::File::open(&b.path) {
        for line in std::io::BufReader::new(f).lines().map_while(Result::ok) {
            if let Ok(o) = serde_json::from_str::<Value>(&line) {
                fold(&mut b.bugs, o);
            }
        }
    }
    b
}

/// 12 hex chars of FNV-1a 64 (stable across builds, unlike DefaultHasher).
fn hash12(s: &str) -> String {
    let h = s.bytes().fold(0xcbf2_9ce4_8422_2325u64, |h, c| (h ^ c as u64).wrapping_mul(0x0100_0000_01b3));
    format!("{h:016x}")[..12].to_string()
}

/// "ui|area|step|endpoint|check". Step and endpoint are folded to lowercase letters and digits, so a matrix row's
/// `home_load`/`getTransactions` and a case id's `homeload`/`gettransactions` give one key.
pub fn sig(o: &Value) -> String {
    let f = |k: &str| o[k].as_str().unwrap_or("").to_string();
    let norm = |k: &str| f(k).chars().filter(char::is_ascii_alphanumeric).collect::<String>().to_ascii_lowercase();
    let ep = Some(norm("endpoint")).filter(|e| !e.is_empty()).unwrap_or("none".into());
    ["ui".into(), f("area"), norm("step"), ep, f("check")].join("|")
}

fn push_uniq(v: &mut Value, x: &Value) {
    if x.is_null() {
        return;
    }
    let a = v.as_array_mut().unwrap();
    if !a.contains(x) {
        a.push(x.clone());
    }
}

/// Fold occurrence `o` (already carrying `key` and `ts`) into its bug. Returns (bug, new).
fn fold(bugs: &mut BTreeMap<String, Value>, o: Value) -> (Value, bool) {
    let key = o["key"].as_str().unwrap_or("").to_string();
    let ts = o["ts"].clone();
    let new = !bugs.contains_key(&key);
    let b = bugs.entry(key.clone()).or_insert_with(|| {
        json!({"key": key, "sig": o["sig"], "title": o["title"].as_str().map(str::to_string).unwrap_or_else(|| sig(&o)),
            "kind": o["kind"].as_str().unwrap_or("unsorted"), "status": "open",
            "area": o["area"], "step": o["step"], "endpoint": o["endpoint"], "check": o["check"],
            "first_seen": ts, "count": 0, "cases": [], "phones": [], "runs": [], "occurrences": []})
    });
    b["count"] = json!(b["count"].as_u64().unwrap_or(0) + 1);
    b["last_seen"] = ts;
    push_uniq(&mut b["cases"], &o["case"]);
    push_uniq(&mut b["phones"], &o["phone"]);
    push_uniq(&mut b["runs"], &o["run"]);
    let occ = b["occurrences"].as_array_mut().unwrap();
    occ.push(o);
    if occ.len() > KEEP {
        occ.remove(0);
    }
    (b.clone(), new)
}

/// A bug without its occurrences (lists, events).
fn head(b: &Value) -> Value {
    let mut h = b.clone();
    if let Some(o) = h.as_object_mut() {
        o.remove("occurrences");
    }
    h
}

/// `bug`: record one occurrence. Needs `case`; `area`, `step`, `endpoint`, `check` make the key.
pub fn record(st: &Arc<State>, req: &Value) -> R<Value> {
    let mut o = req.clone();
    let m = o.as_object_mut().ok_or("occurrence must be a JSON object")?;
    m.remove("call");
    m.remove("id");
    if !o["case"].is_string() {
        return Err("missing `case`".into());
    }
    let s = sig(&o);
    o["sig"] = json!(s);
    o["key"] = json!(hash12(&s));
    if !o["ts"].is_u64() {
        o["ts"] = json!(device::now_ms());
    }
    let line = o.to_string();
    if line.len() > MAX_LINE {
        return Err(format!("occurrence is {} KB; max {} KB (trim logs/ui)", line.len() >> 10, MAX_LINE >> 10));
    }
    let (b, new) = with_book(|bk| -> R<(Value, bool)> {
        if let Some(d) = bk.path.parent() {
            let _ = std::fs::create_dir_all(d);
        }
        let mut f = std::fs::OpenOptions::new().create(true).append(true).open(&bk.path)
            .map_err(|e| format!("{}: {e}", bk.path.display()))?;
        writeln!(f, "{line}").map_err(|e| e.to_string())?;
        Ok(fold(&mut bk.bugs, o.clone()))
    })?;
    crate::emit(st, json!({"type": "bug", "key": b["key"], "new": new, "count": b["count"], "bug": head(&b), "occurrence": o}));
    Ok(json!({"ok": true, "key": b["key"], "new": new, "count": b["count"]}))
}

/// `bugs`: bugs, newest last_seen first. Filters: key, status, kind, area, case (prefix of any case), q (text in
/// title/sig), since (ms, last_seen), limit (default 200). `full: true` includes the kept occurrences; otherwise
/// only the newest one, as `last`.
pub fn list(req: &Value) -> Value {
    let s = |k: &str| req[k].as_str().filter(|v| !v.is_empty()).map(str::to_string);
    let full = req["full"] == json!(true) || req["full"] == json!("1") || req["full"] == json!("true");
    let num = |k: &str| req[k].as_u64().or_else(|| req[k].as_str().and_then(|v| v.parse().ok()));
    let (limit, since) = (num("limit").unwrap_or(200) as usize, num("since").unwrap_or(0));
    with_book(|bk| {
        let all = bk.bugs.len();
        let open = bk.bugs.values().filter(|b| b["status"] == json!("open")).count();
        let mut out: Vec<&Value> = bk.bugs.values().filter(|b| {
            s("key").is_none_or(|k| b["key"] == json!(k))
                && ["status", "kind", "area"].iter().all(|f| s(f).is_none_or(|v| b[*f] == json!(v)))
                && s("case").is_none_or(|c| b["cases"].as_array().is_some_and(|a| a.iter().any(|x| x.as_str().is_some_and(|x| x.starts_with(&c)))))
                && s("q").is_none_or(|q| {
                    let q = q.to_lowercase();
                    [&b["title"], &b["sig"]].iter().any(|t| t.as_str().is_some_and(|t| t.to_lowercase().contains(&q)))
                })
                && b["last_seen"].as_u64().unwrap_or(0) >= since
        }).collect();
        out.sort_by_key(|b| std::cmp::Reverse(b["last_seen"].as_u64().unwrap_or(0)));
        let shown: Vec<Value> = out.iter().take(limit).map(|b| {
            if full {
                (*b).clone()
            } else {
                let mut h = head(b);
                h["last"] = b["occurrences"].as_array().and_then(|a| a.last()).cloned().unwrap_or(Value::Null);
                h
            }
        }).collect();
        json!({"ok": true, "total": all, "open": open, "matched": out.len(), "bugs": shown, "file": bk.path})
    })
}

pub fn handle(st: &Arc<State>, call: &str, req: &Value) -> R<Value> {
    if call == "bug" { record(st, req) } else { Ok(list(req)) }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn occ(case: &str, area: &str, step: &str, ep: &str, check: &str) -> Value {
        let mut o = json!({"case": case, "area": area, "step": step, "endpoint": ep, "check": check, "ts": 1, "phone": "d0"});
        let s = sig(&o);
        o["key"] = json!(hash12(&s));
        o["sig"] = json!(s);
        o
    }

    #[test]
    fn same_key_folds_and_variant_is_dropped() {
        let mut bugs = BTreeMap::new();
        let (_, new) = fold(&mut bugs, occ("M-home-005-load-gettransactions-forbidden", "home", "load", "getTransactions", "ui:tx-error:present"));
        assert!(new);
        let (b, new) = fold(&mut bugs, occ("M-home-008-load-gettransactions-p500-bgresume", "home", "load", "getTransactions", "ui:tx-error:present"));
        assert!(!new);
        assert_eq!((b["count"].as_u64(), b["cases"].as_array().unwrap().len()), (Some(2), 2));
        fold(&mut bugs, occ("M-deposit-006", "deposit", "submit", "", "ui:enter-pin-title:absent"));
        assert_eq!(bugs.len(), 2);
        assert_eq!(sig(&json!({"area": "a", "step": "s", "check": "c"})), "ui|a|s|none|c");
        assert_eq!(sig(&json!({"area": "home", "step": "home_load", "endpoint": "getTransactions", "check": "c"})),
            sig(&json!({"area": "home", "step": "homeload", "endpoint": "gettransactions", "check": "c"})));
        assert_eq!(hash12("ui|a|s|none|c"), hash12("ui|a|s|none|c"));
        assert_ne!(hash12("ui|a|s|none|c"), hash12("ui|a|s|none|d"));
    }

    #[test]
    fn rebuilds_from_the_file_and_filters() {
        let p = std::env::temp_dir().join(format!("ae-bugs-test-{}.jsonl", std::process::id()));
        let lines = [occ("C-1", "home", "load", "x", "k1"), occ("C-2", "home", "load", "x", "k1"), occ("C-3", "wallet", "open", "", "k2")];
        std::fs::write(&p, lines.iter().map(|l| l.to_string() + "\n").collect::<String>() + "not json\n").unwrap();
        let b = load(p.clone());
        let _ = std::fs::remove_file(&p);
        assert_eq!(b.bugs.len(), 2);
        *BOOK.lock().unwrap() = Some(b);
        let r = list(&json!({"area": "home"}));
        assert_eq!((r["matched"].as_u64(), r["bugs"][0]["count"].as_u64()), (Some(1), Some(2)));
        assert!(r["bugs"][0]["occurrences"].is_null() && r["bugs"][0]["last"]["case"] == json!("C-2"));
        assert_eq!(list(&json!({"case": "C-3", "full": true}))["bugs"][0]["occurrences"].as_array().unwrap().len(), 1);
        assert_eq!(list(&json!({"q": "nothing"}))["matched"], json!(0));
    }
}
