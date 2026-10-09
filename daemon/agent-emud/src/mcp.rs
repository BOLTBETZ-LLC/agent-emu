// MCP stdio server: one tool per computer-use call, each forwarded to the daemon over one TCP connection
// (so a `lease` taken here holds until this MCP process exits).
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::net::TcpStream;

fn tools() -> Value {
    let dev = json!({"type": "string", "description": "Device id, e.g. d0"});
    let num = json!({"type": "number"});
    let px = json!({"type": "boolean", "description": "true = x/y are device pixels, not pixels of the last screenshot"});
    let ids = json!({"type": "array", "items": {"type": "string"}, "description": "Device ids, e.g. [\"d0\",\"d1\"]"});
    let opt = json!({
        "screenshot": {"type": "boolean", "description": "false = return only the input ack"},
        "deadline_ms": {"type": "number", "description": "settle deadline, default 3000"},
        "size": {"type": "string", "description": "fit the frame inside WxH, e.g. 360x640"}});
    let tool = |name: &str, desc: &str, mut props: Value, req: &[&str], with_opt: bool| {
        props["device"] = dev.clone();
        if with_opt {
            for (k, v) in opt.as_object().unwrap() { props[k] = v.clone(); }
        }
        let mut required = vec!["device"];
        required.extend_from_slice(req);
        json!({"name": name, "description": desc, "inputSchema": {"type": "object", "properties": props, "required": required}})
    };
    let mut v = json!([
        tool("screenshot", "JPEG q75 of the screen, with scale, capture time and generation.", json!({"size": {"type": "string", "description": "fit inside WxH"}}), &[], false),
        tool("tap", "Tap at x,y (pixels of the last frame received; device_px true = device pixels, e.g. a ui_tree bounds center). Returns a settled screenshot.", json!({"x": num, "y": num, "device_px": px.clone()}), &["x", "y"], true),
        tool("swipe", "Swipe from x1,y1 to x2,y2 over ms. Returns a settled screenshot.", json!({"x1": num, "y1": num, "x2": num, "y2": num, "ms": num, "device_px": px.clone()}), &["x1", "y1", "x2", "y2"], true),
        tool("type_text", "Type text into the focused field. Returns a settled screenshot.", json!({"text": {"type": "string"}}), &["text"], true),
        tool("key", "Press a key: back, home, enter, app_switch, del, ... Returns a settled screenshot.", json!({"name": {"type": "string"}}), &["name"], true),
        tool("ui_tree", "uiautomator XML of the current screen.", json!({}), &[], false),
        tool("logs", "Logcat lines (threadtime), newest last. filter = package (its processes, restarts followed) or tag.             Pass the returned cursor back to get only newer lines; with follow (or wait_ms) the call waits until new lines arrive.",
            json!({"filter": {"type": "string"}, "level": {"type": "string", "description": "V D I W E F: that level and up"}, "since": {"type": "number", "description": "unix ms; drop older lines"},
                "cursor": {"type": "number", "description": "from a previous reply"}, "max_lines": {"type": "number", "description": "default 500"},
                "follow": {"type": "boolean", "description": "wait up to wait_ms (default 10000) for new lines"}, "wait_ms": num}), &[], false),
        tool("crash_events", "Crash, ANR and native-crash events since boot, each with a seq. Pass `after` = the last seq seen             and wait_ms to block until the next one arrives. filter = package.",
            json!({"filter": {"type": "string"}, "after": {"type": "number"}, "wait_ms": num}), &[], false),
        tool("squeeze", "Lever B: drop guest caches, inflate the balloon, then hard-cap the working set of each crosvm process of the Device. Call once the app is on screen. MB values; 0 turns a step off. Defaults come from `start` (150 / 250 / 16). The balloon grows 50 MB at a time while guest MemAvailable stays over 100 MB; the reply has the final size.", json!({"balloon_mb": num, "cap_main_mb": num, "cap_helper_mb": num}), &[], false),
        tool("memory", "Host working set of the Device's crosvm processes: total MB, and per process the role, MB and hard cap.",
            json!({}), &[], false),
        tool("lease", "Take exclusive input control of a Device.", json!({}), &[], false),
        tool("release", "Give up the lease.", json!({}), &[], false),
        tool("start", "Boot the Device and wait until phase ready (about 2 min; blocks). keep_data true reuses its disks (apps, sign-ins); \
            without it the disks are wiped. Refused while host Available RAM is under the floor.",
            json!({"image": {"type": "string", "description": "slim5 (BoltBetz phones), phone, phone-n, slim4, slim3n"},
                "screen": {"type": "string", "description": "iphone17promax-native | -3q | -half | legacy"}, "keep_data": {"type": "boolean"},
                "mem": num, "cpus": num, "render": {"type": "string", "description": "gfxstream (default) | software"},
                "auto_squeeze": {"type": "boolean"}, "boot_cap_mb": num, "idle_cap_mb": num}), &[], false),
        tool("stop", "Stop the Device (syncs the guest first so keep_data can reuse its disks).", json!({}), &[], false),
        tool("install_bundled", "Install the image's own APK (BoltBetz staging) over the console; no adb needed.", json!({}), &[], false),
        tool("issues", "E and F logcat lines since boot, grouped and counted (app vs system), most frequent first.",
            json!({"filter": {"type": "string", "description": "package, e.g. com.boltbetz.staging"}, "top": num}), &[], false),
        tool("shell", "Root shell command in the guest (console). Reply: out, code.",
            json!({"cmd": {"type": "string"}, "timeout_s": num}), &["cmd"], false),
    ]);
    v.as_array_mut().unwrap().extend(crate::controls::tools());
    // Fleet calls act on many Devices, so they take no `device`.
    v.as_array_mut().unwrap().extend([
        json!({"name": "fleet", "description": "Boot n Devices one at a time (ids d<base>..), each to the app's first screen \
            (install from the image, app launch, screen quiet), squeezed unless auto_squeeze is false. The next boot is refused \
            when host Available is under the floor. Returns per-Device ready time and working set, plus a fleet total.",
            "inputSchema": {"type": "object", "required": ["n"], "properties": {
                "n": num, "base": {"type": "number", "description": "first Device index, default 0"},
                "image": {"type": "string", "description": "slim3 | slim4 | slim5"}, "mem": num,
                "cpus": {"type": "number", "description": "vCPUs per Device, default AE_CPUS (2)"},
                "net": {"type": "boolean", "description": "false = no virtio-net and no adb (untested)"},
                "auto_squeeze": {"type": "boolean", "description": "default true"},
                "squeeze_settle_s": {"type": "number", "description": "wait after launch before squeezing, default 45"},
                "app": {"type": "string", "description": "package to launch, default com.boltbetz.staging"},
                "balloon_mb": num, "cap_main_mb": num, "cap_helper_mb": num}}}),
        json!({"name": "fleet_stop", "description": "Stop every Device this daemon runs.", "inputSchema": {"type": "object", "properties": {}}}),
        json!({"name": "status", "description": "Every Device: id, ready, phase (spawning, booting, android, setup, browser, ready, stopped), image, screen, mem, uptime; plus host available_mb.",
            "inputSchema": {"type": "object", "properties": {}}}),
        json!({"name": "start_many", "description": "Boot several Devices, up to `parallel` at once; takes the `start` options. Blocks until all are done.",
            "inputSchema": {"type": "object", "required": ["devices"], "properties": {"devices": ids.clone(), "parallel": num,
                "image": {"type": "string"}, "screen": {"type": "string"}, "keep_data": {"type": "boolean"}, "mem": num, "auto_squeeze": {"type": "boolean"}}}}),
        json!({"name": "stop_many", "description": "Stop the listed Devices at once (default: all).",
            "inputSchema": {"type": "object", "properties": {"devices": ids.clone()}}}),
        // Test runner (tests/boltbetz/run.py), run by this MCP process, not the daemon. Blocks until done.
        json!({"name": "run_case", "description": "Run BoltBetz test case(s) on one Device with tests/boltbetz/run.py. \
            case = id or id prefix, comma list allowed (e.g. L4-01). Returns the PASS/FAIL grid, exit code and results folder.",
            "inputSchema": {"type": "object", "required": ["device", "case"], "properties": {"device": dev.clone(),
                "case": {"type": "string", "description": "case id or prefix, e.g. L4-01-home-renders"}}}}),
        json!({"name": "run_lanes", "description": "Run the BoltBetz test lanes, dealt to phones in order (L1 -> first phone, ...), \
            in parallel. Returns the PASS/FAIL grid, exit code and results folder. One run per phone at a time.",
            "inputSchema": {"type": "object", "required": ["phones"], "properties": {"phones": ids.clone(),
                "lanes": {"type": "array", "items": {"type": "string"}, "description": "only these lanes, e.g. [\"L2\",\"L4\"]; default all"}}}}),
    ]);
    v
}

/// run.py: AE_TESTS_DIR, else tests/boltbetz beside the exe or in any folder above it (checkout or install root).
fn runner() -> Result<std::path::PathBuf, String> {
    if let Some(d) = std::env::var_os("AE_TESTS_DIR") {
        return Ok(std::path::PathBuf::from(d).join("run.py"));
    }
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    exe.ancestors().map(|a| a.join("tests").join("boltbetz").join("run.py")).find(|p| p.is_file())
        .ok_or_else(|| format!("tests/boltbetz/run.py not found above {} (set AE_TESTS_DIR)", exe.display()))
}

/// run_case / run_lanes -> run.py argv.
fn runner_args(name: &str, a: &Value) -> Result<Vec<String>, String> {
    let s = |k: &str| a[k].as_str().filter(|v| !v.is_empty()).map(String::from).ok_or(format!("{k} required"));
    let list = |k: &str| a[k].as_array().map(|v| v.iter().filter_map(Value::as_str).collect::<Vec<_>>().join(","));
    match name {
        "run_case" => Ok(vec!["--phones".into(), s("device")?, "--case".into(), s("case")?]),
        _ => {
            let phones = list("phones").filter(|p| !p.is_empty()).ok_or("phones required")?;
            let mut v = vec!["--phones".into(), phones];
            if let Some(l) = list("lanes").filter(|l| !l.is_empty()) { v.extend(["--lanes".into(), l]); }
            Ok(v)
        }
    }
}

/// Runs run.py with AE_PYTHON (default python); stdout ends with the grid and the results folder.
fn run_tests(name: &str, a: &Value) -> Value {
    let res = runner_args(name, a).and_then(|args| {
        let py = runner()?;
        let out = std::process::Command::new(std::env::var("AE_PYTHON").unwrap_or("python".into()))
            .arg(&py).args(&args).current_dir(py.parent().unwrap()).env("PYTHONUTF8", "1")
            .stdin(std::process::Stdio::null()).output().map_err(|e| format!("python: {e}"))?;
        let stdout = String::from_utf8_lossy(&out.stdout).into_owned();
        let results = stdout.lines().rev().find(|l| !l.trim().is_empty()).unwrap_or("").trim().to_string();
        let err = String::from_utf8_lossy(&out.stderr);
        let tail = &err[err.len().saturating_sub(2000)..];
        Ok(json!({"content": [{"type": "text", "text": stdout},
            {"type": "text", "text": json!({"exit": out.status.code(), "passed": out.status.success(), "results": results, "stderr": tail}).to_string()}]}))
    });
    res.unwrap_or_else(|e| json!({"content": [{"type": "text", "text": e}], "isError": true}))
}

struct Conn { r: BufReader<TcpStream>, w: TcpStream }

fn forward(conn: &mut Option<Conn>, addr: &str, req: &Value) -> Result<Value, String> {
    if conn.is_none() {
        let s = TcpStream::connect(addr).map_err(|e| format!("daemon {addr}: {e}"))?;
        let _ = s.set_nodelay(true);
        *conn = Some(Conn { r: BufReader::new(s.try_clone().map_err(|e| e.to_string())?), w: s });
    }
    let c = conn.as_mut().unwrap();
    let res = (|| {
        writeln!(c.w, "{req}").map_err(|e| e.to_string())?;
        let mut line = String::new();
        if c.r.read_line(&mut line).map_err(|e| e.to_string())? == 0 { return Err("daemon closed".to_string()); }
        serde_json::from_str::<Value>(&line).map_err(|e| e.to_string())
    })();
    if res.is_err() { *conn = None; }
    res
}

/// Daemon reply -> MCP tool result: the frame becomes an image block, everything else a text block.
fn to_content(mut rep: Value) -> Value {
    if rep["ok"] != json!(true) {
        return json!({"content": [{"type": "text", "text": rep["error"].as_str().unwrap_or("error")}], "isError": true});
    }
    let mut content = vec![];
    if let Some(jpeg) = rep["frame"].as_object_mut().and_then(|f| f.remove("jpeg")) {
        content.push(json!({"type": "image", "data": jpeg, "mimeType": "image/jpeg"}));
    }
    if let Some(xml) = rep.as_object_mut().and_then(|o| o.remove("xml")) {
        content.push(json!({"type": "text", "text": xml}));
    }
    if let Some(Value::Array(lines)) = rep.as_object_mut().and_then(|o| o.remove("lines")) {
        let text: Vec<&str> = lines.iter().filter_map(Value::as_str).collect();
        content.push(json!({"type": "text", "text": text.join("
")}));
    }
    content.push(json!({"type": "text", "text": rep.to_string()}));
    json!({"content": content})
}

pub fn run(addr: &str) {
    let mut conn = None;
    let stdout = std::io::stdout();
    for line in std::io::stdin().lock().lines() {
        let Ok(line) = line else { break };
        let Ok(msg) = serde_json::from_str::<Value>(&line) else { continue };
        let Some(id) = msg.get("id").cloned() else { continue }; // notifications need no reply
        let p = &msg["params"];
        let result = match msg["method"].as_str().unwrap_or("") {
            "initialize" => Ok(json!({"protocolVersion": p["protocolVersion"].as_str().unwrap_or("2025-06-18"),
                "capabilities": {"tools": {}}, "serverInfo": {"name": "agent-emu", "version": env!("CARGO_PKG_VERSION")}})),
            "ping" => Ok(json!({})),
            "tools/list" => Ok(json!({"tools": tools()})),
            "tools/call" if matches!(p["name"].as_str(), Some("run_case" | "run_lanes")) => {
                Ok(run_tests(p["name"].as_str().unwrap(), &p["arguments"]))
            }
            "tools/call" => {
                let mut req = p["arguments"].clone();
                if !req.is_object() { req = json!({}); }
                req["call"] = p["name"].clone();
                if req["call"] == json!("logs") && req["follow"] == json!(true) {
                    // MCP is request/response: follow becomes a long poll.
                    req["follow"] = json!(false);
                    if req["wait_ms"].is_null() { req["wait_ms"] = json!(10000); }
                }
                Ok(match forward(&mut conn, addr, &req) {
                    Ok(rep) => to_content(rep),
                    Err(e) => json!({"content": [{"type": "text", "text": e}], "isError": true}),
                })
            }
            m => Err(json!({"code": -32601, "message": format!("method not found: {m}")})),
        };
        let mut out = json!({"jsonrpc": "2.0", "id": id});
        match result { Ok(r) => out["result"] = r, Err(e) => out["error"] = e }
        let mut o = stdout.lock();
        let _ = writeln!(o, "{out}");
        let _ = o.flush();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn frame_becomes_image_block() {
        let c = to_content(json!({"ok": true, "settled": true, "frame": {"jpeg": "AAA", "scale": 1.0}}));
        assert_eq!(c["content"][0]["type"], "image");
        assert_eq!(c["content"][0]["data"], "AAA");
        assert!(!c["content"][1]["text"].as_str().unwrap().contains("AAA"));
        let e = to_content(json!({"ok": false, "error": "busy"}));
        assert_eq!(e["isError"], true);
    }

    #[test]
    fn log_lines_become_text() {
        let c = to_content(json!({"ok": true, "lines": ["a", "b"], "cursor": 5}));
        assert_eq!(c["content"][0]["text"], "a\nb");
        assert!(c["content"][1]["text"].as_str().unwrap().contains("\"cursor\":5"));
    }

    #[test]
    fn every_tool_takes_device() {
        for t in tools().as_array().unwrap() {
            if ["fleet", "fleet_stop", "status", "start_many", "stop_many", "run_lanes"].contains(&t["name"].as_str().unwrap()) {
                continue;
            }
            assert_eq!(t["inputSchema"]["required"][0], "device", "{}", t["name"]);
        }
        let names: Vec<_> = tools().as_array().unwrap().iter().map(|t| t["name"].clone()).collect();
        for n in ["fleet", "fleet_stop", "status", "start", "stop", "start_many", "stop_many", "install_bundled", "issues", "shell",
            "run_case", "run_lanes"] {
            assert!(names.contains(&json!(n)), "{n}");
        }
    }

    #[test]
    fn runner_argv() {
        assert_eq!(runner_args("run_case", &json!({"device": "d3", "case": "L4-01"})).unwrap(), ["--phones", "d3", "--case", "L4-01"]);
        assert_eq!(runner_args("run_lanes", &json!({"phones": ["d0", "d1"], "lanes": ["L2"]})).unwrap(),
            ["--phones", "d0,d1", "--lanes", "L2"]);
        assert!(runner_args("run_lanes", &json!({"phones": []})).is_err());
        assert!(runner_args("run_case", &json!({"device": "d3"})).is_err());
    }
}
