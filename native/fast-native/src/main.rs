use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use simplicio_fast_core::SnapshotReader;
use std::collections::{BTreeMap, HashMap};
use std::io::{self, BufRead, Read, Write};

const ABI: &str = "simplicio.fast-native/v1";

struct SessionState {
    snapshots: HashMap<String, SnapshotReader>,
}

impl SessionState {
    fn new() -> Self {
        Self {
            snapshots: HashMap::new(),
        }
    }

    fn snapshot(&mut self, path: &str) -> Result<&SnapshotReader, String> {
        if !self.snapshots.contains_key(path) {
            let reader = SnapshotReader::open(path).map_err(|error| error.to_string())?;
            self.snapshots.insert(path.to_owned(), reader);
        }
        self.snapshots
            .get(path)
            .ok_or_else(|| "snapshot cache insertion failed".into())
    }
}

fn execute(operation: &str, payload: &Value, state: &mut SessionState) -> Result<Value, String> {
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
        "query" => {
            let snapshot = payload["snapshot"].as_str().ok_or("snapshot")?;
            let term = payload["term"].as_str().ok_or("term")?;
            let path = payload["path"].as_str();
            let kind = payload["kind"].as_str();
            let limit = payload["limit"].as_u64().unwrap_or(50) as usize;
            let cursor = payload["cursor"].as_u64().map(|value| value as usize);
            let reader = state.snapshot(snapshot)?;
            let receipt = reader
                .query_filtered_after(term, path, kind, limit, cursor)
                .map_err(|error| error.to_string())?;
            Ok(json!({
                "matches": receipt.matches,
                "planner": {
                    "selected_index": receipt.selected_index,
                    "candidates_visited": receipt.candidates_visited,
                    "records_decoded": receipt.records_decoded,
                    "next_cursor": receipt.next_cursor,
                }
            }))
        }
        "context" => {
            let snapshot = payload["snapshot"].as_str().ok_or("snapshot")?;
            let root = payload["root"].as_str().ok_or("root")?;
            let term = payload["term"].as_str().ok_or("term")?;
            let limit = payload["limit"].as_u64().unwrap_or(10) as usize;
            let max_lines = payload["max_lines"].as_u64().unwrap_or(120) as u32;
            let max_bytes = payload["max_bytes"].as_u64().unwrap_or(32_000) as usize;
            let max_tokens = payload["max_tokens"].as_u64().unwrap_or(8_000) as usize;
            let reader = state.snapshot(snapshot)?;
            let receipt = reader
                .context_with_receipt(
                    std::path::Path::new(root),
                    term,
                    limit,
                    max_lines,
                    max_bytes,
                    max_tokens,
                )
                .map_err(|error| error.to_string())?;
            Ok(json!({
                "spans": receipt.spans,
                "planner": {
                    "source_files_read": receipt.source_files_read,
                    "source_cache_hits": receipt.source_cache_hits,
                    "source_bytes_read": receipt.source_bytes_read,
                }
            }))
        }
        "stats" => {
            let snapshot = payload["snapshot"].as_str().ok_or("snapshot")?;
            let reader = state.snapshot(snapshot)?;
            Ok(json!(reader.stats()))
        }
        "session_cache_stats" => Ok(json!({
            "snapshots": state.snapshots.len(),
        })),
        _ => Err("operation".into()),
    }
}

fn response(request: &Value, state: &mut SessionState) -> Value {
    if request["abi"] != ABI {
        return json!({"abi": ABI, "ok": false, "reason": "abi"});
    }
    match execute(
        request["operation"].as_str().unwrap_or(""),
        &request["payload"],
        state,
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
            "capabilities": ["sha256", "catalog_lookup", "page", "overlay_merge", "query", "context", "stats", "session_cache_stats", "mmap_snapshot_cache"],
            "transport": "stdio-lines"
        })
    )?;
    output.flush()?;
    let mut state = SessionState::new();
    for line in io::stdin().lock().lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        let request = match serde_json::from_str::<Value>(&line) {
            Ok(value) => value,
            Err(_) => json!({"abi": ABI, "ok": false, "reason": "frame_invalid"}),
        };
        writeln!(output, "{}", response(&request, &mut state))?;
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
    let mut state = SessionState::new();
    match response(&request, &mut state) {
        value if value["ok"] == true => println!("{}", value),
        value => {
            println!("{}", value);
            std::process::exit(4);
        }
    }
}
