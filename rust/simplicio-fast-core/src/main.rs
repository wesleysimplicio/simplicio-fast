use simplicio_fast_core::{manifest, SnapshotReader};
use std::{env, process::ExitCode};

fn main() -> ExitCode {
    let args: Vec<String> = env::args().collect();
    if args.iter().any(|arg| arg == "--version") {
        println!("{}", manifest());
        return ExitCode::SUCCESS;
    }
    let Some(index) = args.iter().position(|arg| arg == "--stats") else {
        eprintln!("missing command: expected --version or --stats <snapshot>");
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
