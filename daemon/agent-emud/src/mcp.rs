// MCP stdio server: one tool per computer-use call, each forwarded to the daemon over one TCP connection
// (so a `lease` taken here holds until this MCP process exits).
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::net::TcpStream;

fn tools() -> Value {
    let dev = json!({"type": "string", "description": "Device id, e.g. d0"});
    let num = json!({"type": "number"});
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
    json!([
        tool("screenshot", "JPEG q75 of the screen, with scale, capture time and generation.", json!({"size": {"type": "string", "description": "fit inside WxH"}}), &[], false),
        tool("tap", "Tap at x,y (pixels of the last frame received). Returns a settled screenshot.", json!({"x": num, "y": num}), &["x", "y"], true),
        tool("swipe", "Swipe from x1,y1 to x2,y2 over ms. Returns a settled screenshot.", json!({"x1": num, "y1": num, "x2": num, "y2": num, "ms": num}), &["x1", "y1", "x2", "y2"], true),
        tool("type_text", "Type text into the focused field. Returns a settled screenshot.", json!({"text": {"type": "string"}}), &["text"], true),
        tool("key", "Press a key: back, home, enter, app_switch, del, ... Returns a settled screenshot.", json!({"name": {"type": "string"}}), &["name"], true),
        tool("ui_tree", "uiautomator XML of the current screen.", json!({}), &[], false),
        tool("logs", "Logcat lines (threadtime), newest last. filter = package (its processes, restarts followed) or tag.             Pass the returned cursor back to get only newer lines; with follow (or wait_ms) the call waits until new lines arrive.",
            json!({"filter": {"type": "string"}, "since": {"type": "number", "description": "unix ms; drop older lines"},
                "cursor": {"type": "number", "description": "from a previous reply"}, "max_lines": {"type": "number", "description": "default 500"},
                "follow": {"type": "boolean", "description": "wait up to wait_ms (default 10000) for new lines"}, "wait_ms": num}), &[], false),
        tool("crash_events", "Crash, ANR and native-crash events since boot, each with a seq. Pass `after` = the last seq seen             and wait_ms to block until the next one arrives. filter = package.",
            json!({"filter": {"type": "string"}, "after": {"type": "number"}, "wait_ms": num}), &[], false),
        tool("lease", "Take exclusive input control of a Device.", json!({}), &[], false),
        tool("release", "Give up the lease.", json!({}), &[], false),
    ])
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
            assert_eq!(t["inputSchema"]["required"][0], "device", "{}", t["name"]);
        }
    }
}
