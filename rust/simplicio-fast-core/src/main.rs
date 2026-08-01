use simplicio_fast_core::{manifest, SegmentReader, SegmentWriter, SnapshotReader};
use std::{env, process::ExitCode};

fn print_help() {
    println!("simplicio-fast-rs — Rust snapshot and segment engine");
    println!();
    println!("Usage:");
    println!("  simplicio-fast-rs --version [--json]");
    println!("  simplicio-fast-rs --stats <snapshot.sfast>");
    println!("  simplicio-fast-rs --query <snapshot.sfast> <term> [--path <file>] [--kind <kind>] [--limit <positive>]");
    println!("  simplicio-fast-rs --context <snapshot.sfast> <repo> <term> [--limit <positive>] [--max-lines <positive>] [--max-bytes <positive>] [--max-tokens <positive>]");
    println!("  simplicio-fast-rs --publish-segments <snapshot.sfast> <directory>");
    println!("  simplicio-fast-rs --segment <directory> <name>");
    println!();
    println!("Use --help or -h to show this message.");
}

fn main() -> ExitCode {
    let args: Vec<String> = env::args().collect();
    if args.iter().any(|arg| arg == "--help" || arg == "-h") {
        print_help();
        return ExitCode::SUCCESS;
    }
    if args.iter().any(|arg| arg == "--version") {
        println!("{}", manifest());
        return ExitCode::SUCCESS;
    }
    if let Some(index) = args.iter().position(|arg| arg == "--query") {
        let Some(path) = args.get(index + 1) else {
            eprintln!("missing snapshot path");
            return ExitCode::from(2);
        };
        let Some(term) = args.get(index + 2) else {
            eprintln!("missing query term");
            return ExitCode::from(2);
        };
        let limit = args
            .iter()
            .position(|arg| arg == "--limit")
            .and_then(|position| args.get(position + 1))
            .and_then(|value| value.parse::<usize>().ok())
            .unwrap_or(50);
        let path_filter = args
            .iter()
            .position(|arg| arg == "--path")
            .and_then(|position| args.get(position + 1))
            .map(String::as_str);
        let kind_filter = args
            .iter()
            .position(|arg| arg == "--kind")
            .and_then(|position| args.get(position + 1))
            .map(String::as_str);
        return match SnapshotReader::open(path)
            .and_then(|snapshot| snapshot.query_filtered(term, path_filter, kind_filter, limit))
        {
            Ok(receipt) => {
                println!(
                    "{}",
                    serde_json::json!({
                        "schema":"simplicio.fast.query/v1",
                        "engine":"rust",
                        "matches":receipt.matches,
                        "planner": {
                            "selected_index": receipt.selected_index,
                            "candidates_visited": receipt.candidates_visited,
                            "records_decoded": receipt.records_decoded,
                        }
                    })
                );
                ExitCode::SUCCESS
            }
            Err(error) => {
                println!(
                    "{}",
                    serde_json::json!({"schema":"simplicio.fast.error/v1", "engine":"rust", "status":"error", "reason":error.to_string()})
                );
                ExitCode::from(2)
            }
        };
    }
    if let Some(index) = args.iter().position(|arg| arg == "--context") {
        let Some(snapshot_path) = args.get(index + 1) else {
            eprintln!("missing snapshot path");
            return ExitCode::from(2);
        };
        let Some(root) = args.get(index + 2) else {
            eprintln!("missing repository root");
            return ExitCode::from(2);
        };
        let Some(term) = args.get(index + 3) else {
            eprintln!("missing context term");
            return ExitCode::from(2);
        };
        let value = |name: &str, default: usize| {
            args.iter()
                .position(|arg| arg == name)
                .and_then(|position| args.get(position + 1))
                .and_then(|value| value.parse::<usize>().ok())
                .unwrap_or(default)
        };
        return match SnapshotReader::open(snapshot_path).and_then(|snapshot| {
            snapshot.context(
                std::path::Path::new(root),
                term,
                value("--limit", 10),
                value("--max-lines", 120) as u32,
                value("--max-bytes", 32_000),
                value("--max-tokens", 8_000),
            )
        }) {
            Ok(spans) => {
                println!(
                    "{}",
                    serde_json::json!({"schema":"simplicio.fast.context/v1", "engine":"rust", "spans":spans})
                );
                ExitCode::SUCCESS
            }
            Err(error) => {
                println!(
                    "{}",
                    serde_json::json!({"schema":"simplicio.fast.error/v1", "engine":"rust", "status":"error", "reason":error.to_string()})
                );
                ExitCode::from(2)
            }
        };
    }
    if let Some(index) = args.iter().position(|arg| arg == "--publish-segments") {
        let Some(snapshot_path) = args.get(index + 1) else {
            eprintln!("missing snapshot path");
            return ExitCode::from(2);
        };
        let Some(directory) = args.get(index + 2) else {
            eprintln!("missing segment directory");
            return ExitCode::from(2);
        };
        return match SnapshotReader::open(snapshot_path)
            .and_then(|snapshot| SegmentWriter::new(directory).publish(&snapshot))
        {
            Ok(receipt) => {
                println!(
                    "{}",
                    serde_json::to_string(&receipt).expect("serializable receipt")
                );
                ExitCode::SUCCESS
            }
            Err(error) => {
                println!(
                    "{}",
                    serde_json::json!({"schema":"simplicio.fast.error/v1", "engine":"rust", "status":"error", "reason":error.to_string()})
                );
                ExitCode::from(2)
            }
        };
    }
    if let Some(index) = args.iter().position(|arg| arg == "--segment") {
        let Some(directory) = args.get(index + 1) else {
            eprintln!("missing segment directory");
            return ExitCode::from(2);
        };
        let Some(name) = args.get(index + 2) else {
            eprintln!("missing segment name");
            return ExitCode::from(2);
        };
        return match SegmentReader::open(directory).and_then(|reader| reader.map(name)) {
            Ok(segment) => {
                println!(
                    "{}",
                    serde_json::json!({
                        "schema":"simplicio.fast.segment-map/v1",
                        "engine":"rust",
                        "name":segment.name(),
                        "bytes":segment.as_bytes().len(),
                        "sha256":segment.sha256()
                    })
                );
                ExitCode::SUCCESS
            }
            Err(error) => {
                println!(
                    "{}",
                    serde_json::json!({"schema":"simplicio.fast.error/v1", "engine":"rust", "status":"error", "reason":error.to_string()})
                );
                ExitCode::from(2)
            }
        };
    }
    let Some(index) = args.iter().position(|arg| arg == "--stats") else {
        eprintln!("missing command: expected --version, --stats <snapshot>, --publish-segments <snapshot> <directory> or --segment <directory> <name>");
        return ExitCode::from(2);
    };
    let Some(path) = args.get(index + 1) else {
        eprintln!("missing snapshot path");
        return ExitCode::from(2);
    };
    match SnapshotReader::open(path) {
        Ok(snapshot) => {
            println!(
                "{}",
                serde_json::json!({"schema":"simplicio.fast.stats/v1", "engine":"rust", "stats":snapshot.stats()})
            );
            ExitCode::SUCCESS
        }
        Err(error) => {
            println!(
                "{}",
                serde_json::json!({"schema":"simplicio.fast.error/v1", "engine":"rust", "status":"error", "reason":error.to_string()})
            );
            ExitCode::from(2)
        }
    }
}
