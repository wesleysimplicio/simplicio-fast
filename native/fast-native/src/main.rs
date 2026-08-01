use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::io::{self, BufRead, Read, Write};

const ABI: &str = "simplicio.fast-native/v1";

fn execute(operation: &str, payload: &Value) -> Result<Value, String> {
    match operation {
        "sha256" => {
            let bytes = hex::decode(payload["hex"].as_str().ok_or("hex")?).map_err(|_| "hex")?;
            Ok(json!(hex::encode(Sha256::digest(bytes))))
        }
        "catalog_lookup" => Ok(payload["catalog"]
            .get(payload["key"].as_str().ok_or("key")?)
            .cloned()
            .unwrap_or(Value::Null)),
        "page" => {
            let bytes = hex::decode(payload["hex"].as_str().ok_or("hex")?).map_err(|_| "hex")?;
            let offset = payload["offset"].as_u64().ok_or("offset")? as usize;
            let limit = payload["limit"].as_u64().ok_or("limit")? as usize;
            if limit == 0 || limit > 65536 {
                return Err("bounds".into());
            }
            let end = offset.saturating_add(limit).min(bytes.len());
            let page = if offset >= bytes.len() {
                &[]
            } else {
                &bytes[offset..end]
            };
            Ok(json!(hex::encode(page)))
        }
        "overlay_merge" => {
            let mut merged: BTreeMap<String, Value> =
                serde_json::from_value(payload["base"].clone()).map_err(|_| "base")?;
            let overlay: BTreeMap<String, Value> =
                serde_json::from_value(payload["overlay"].clone()).map_err(|_| "overlay")?;
            for (key, value) in overlay {
                if value.is_null() {
                    merged.remove(&key);
                } else {
                    merged.insert(key, value);
                }
            }
            Ok(json!(merged))
        }
        _ => Err("operation".into()),
    }
}

fn response(request: &Value) -> Value {
    if request["abi"] != ABI {
        return json!({"abi": ABI, "ok": false, "reason": "abi"});
    }
    match execute(
        request["operation"].as_str().unwrap_or(""),
        &request["payload"],
    ) {
        Ok(result) => json!({"abi": ABI, "ok": true, "result": result}),
        Err(reason) => json!({"abi": ABI, "ok": false, "reason": reason}),
    }
}

fn run_session() -> io::Result<()> {
    let stdout = io::stdout();
    let mut output = stdout.lock();
    writeln!(
        output,
        "{}",
        json!({
            "schema": "simplicio.fast.engine-session/v1",
            "abi": ABI,
            "ok": true,
            "capabilities": ["sha256", "catalog_lookup", "page", "overlay_merge"],
            "transport": "stdio-lines"
        })
    )?;
    output.flush()?;
    for line in io::stdin().lock().lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        let request = match serde_json::from_str::<Value>(&line) {
            Ok(value) => value,
            Err(_) => json!({"abi": ABI, "ok": false, "reason": "frame_invalid"}),
        };
        writeln!(output, "{}", response(&request))?;
        output.flush()?;
    }
    Ok(())
}

fn main() {
    if std::env::args().any(|arg| arg == "--session") {
        if run_session().is_err() {
            std::process::exit(2);
        }
        return;
    }
    let mut input = String::new();
    if io::stdin().read_to_string(&mut input).is_err() {
        std::process::exit(2);
    }
    let request: Value = match serde_json::from_str(&input) {
        Ok(value) => value,
        Err(_) => std::process::exit(2),
    };
    if request["abi"] != ABI {
        std::process::exit(3);
    }
    match response(&request) {
        value if value["ok"] == true => println!("{}", value),
        value => {
            println!("{}", value);
            std::process::exit(4);
        }
    }
}
