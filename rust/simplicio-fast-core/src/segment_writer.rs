//! Atomic content-addressed segmented writer for validated SFAST001/v2 snapshots.
//!
//! A manifest is published only after every segment has been written and
//! synchronised. Existing content-addressed segments are reused. The previous
//! complete manifest is retained for deterministic rollback.

use crate::{hex_bytes, section_bytes, SnapshotError, SnapshotReader};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs::{self, File, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
};

pub const MANIFEST_SCHEMA: &str = "simplicio.fast.segments/v1";
const MANIFEST_NAME: &str = "manifest.json";
const PREVIOUS_MANIFEST_NAME: &str = "manifest.previous.json";

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct PublishedSegment {
    pub name: String,
    pub file: String,
    pub bytes: usize,
    pub sha256: String,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct PublishReceipt {
    pub schema: String,
    pub generation: String,
    pub source_snapshot_sha256: String,
    pub segments: Vec<PublishedSegment>,
    pub written: usize,
    pub reused: usize,
}

pub struct SegmentWriter {
    directory: PathBuf,
}

impl SegmentWriter {
    pub fn new(directory: impl AsRef<Path>) -> Self {
        Self {
            directory: directory.as_ref().to_path_buf(),
        }
    }

    /// Publish all validated snapshot sections without invoking Python.
    pub fn publish(&self, snapshot: &SnapshotReader) -> Result<PublishReceipt, SnapshotError> {
        let payloads = snapshot
            .sections
            .iter()
            .map(|(name, section)| {
                (
                    name.clone(),
                    section_bytes(snapshot.bytes.as_slice(), section).to_vec(),
                )
            })
            .collect();
        self.publish_payloads(
            payloads,
            snapshot.stats().generation,
            hex_bytes(&Sha256::digest(snapshot.bytes.as_slice())),
        )
    }

    fn publish_payloads(
        &self,
        payloads: BTreeMap<String, Vec<u8>>,
        generation: String,
        source_snapshot_sha256: String,
    ) -> Result<PublishReceipt, SnapshotError> {
        fs::create_dir_all(&self.directory)?;
        let mut segments = Vec::with_capacity(payloads.len());
        let mut written = 0;
        let mut reused = 0;

        for (name, bytes) in payloads {
            validate_name(&name)?;
            let digest = hex_bytes(&Sha256::digest(&bytes));
            let file_name = format!("{name}-{digest}.seg");
            let final_path = self.directory.join(&file_name);
            if final_path.exists() {
                let existing = fs::read(&final_path)?;
                if existing.len() != bytes.len()
                    || hex_bytes(&Sha256::digest(&existing)) != digest
                {
                    return Err(SnapshotError::Invalid(format!(
                        "content-addressed segment collision: {name}"
                    )));
                }
                reused += 1;
            } else {
                let temporary = self
                    .directory
                    .join(format!(".{file_name}.{}.tmp", std::process::id()));
                write_synced(&temporary, &bytes)?;
                match fs::rename(&temporary, &final_path) {
                    Ok(()) => written += 1,
                    Err(error) if final_path.exists() => {
                        let _ = fs::remove_file(&temporary);
                        let existing = fs::read(&final_path)?;
                        if existing != bytes {
                            return Err(SnapshotError::Invalid(format!(
                                "content-addressed segment collision: {name}"
                            )));
                        }
                        reused += 1;
                    }
                    Err(error) => {
                        let _ = fs::remove_file(&temporary);
                        return Err(error.into());
                    }
                }
            }
            segments.push(PublishedSegment {
                name,
                file: file_name,
                bytes: bytes.len(),
                sha256: digest,
            });
        }

        let receipt = PublishReceipt {
            schema: MANIFEST_SCHEMA.into(),
            generation,
            source_snapshot_sha256,
            segments,
            written,
            reused,
        };
        let manifest = serde_json::to_vec_pretty(&receipt)
            .map_err(|_| SnapshotError::Invalid("cannot serialize segment manifest".into()))?;
        let manifest_path = self.directory.join(MANIFEST_NAME);
        if manifest_path.exists() {
            let previous_tmp = self
                .directory
                .join(format!(".{PREVIOUS_MANIFEST_NAME}.{}.tmp", std::process::id()));
            fs::copy(&manifest_path, &previous_tmp)?;
            sync_file(&previous_tmp)?;
            replace_file(&previous_tmp, &self.directory.join(PREVIOUS_MANIFEST_NAME))?;
        }
        let manifest_tmp = self
            .directory
            .join(format!(".{MANIFEST_NAME}.{}.tmp", std::process::id()));
        let mut bytes = manifest;
        bytes.push(b'\n');
        write_synced(&manifest_tmp, &bytes)?;
        replace_file(&manifest_tmp, &manifest_path)?;
        sync_directory(&self.directory)?;
        Ok(receipt)
    }
}

fn validate_name(name: &str) -> Result<(), SnapshotError> {
    if name.is_empty()
        || name.len() > 16
        || !name
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'-')
    {
        return Err(SnapshotError::Invalid("unsafe segment name".into()));
    }
    Ok(())
}

fn write_synced(path: &Path, bytes: &[u8]) -> Result<(), SnapshotError> {
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)?;
    if let Err(error) = file.write_all(bytes).and_then(|_| file.sync_all()) {
        drop(file);
        let _ = fs::remove_file(path);
        return Err(error.into());
    }
    Ok(())
}

fn sync_file(path: &Path) -> Result<(), SnapshotError> {
    File::open(path)?.sync_all()?;
    Ok(())
}

fn replace_file(source: &Path, destination: &Path) -> Result<(), SnapshotError> {
    #[cfg(windows)]
    if destination.exists() {
        fs::remove_file(destination)?;
    }
    fs::rename(source, destination)?;
    Ok(())
}

#[cfg(unix)]
fn sync_directory(directory: &Path) -> Result<(), SnapshotError> {
    File::open(directory)?.sync_all()?;
    Ok(())
}

#[cfg(not(unix))]
fn sync_directory(_directory: &Path) -> Result<(), SnapshotError> {
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn fixture_directory() -> PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        std::env::temp_dir().join(format!(
            "simplicio-fast-writer-{}-{nonce}",
            std::process::id()
        ))
    }

    fn payload(value: &[u8]) -> BTreeMap<String, Vec<u8>> {
        BTreeMap::from([
            ("files".into(), value.to_vec()),
            ("indexes".into(), b"{}".to_vec()),
            ("relations".into(), b"[]".to_vec()),
            ("strings".into(), b"names".to_vec()),
            ("symbols".into(), b"records".to_vec()),
        ])
    }

    #[test]
    fn no_change_refresh_reuses_every_segment() {
        let directory = fixture_directory();
        let writer = SegmentWriter::new(&directory);
        let first = writer
            .publish_payloads(payload(b"v1"), "gen-1".into(), "a".repeat(64))
            .expect("first publish");
        assert_eq!(first.written, 5);
        assert_eq!(first.reused, 0);

        let second = writer
            .publish_payloads(payload(b"v1"), "gen-1".into(), "a".repeat(64))
            .expect("no-change refresh");
        assert_eq!(second.written, 0);
        assert_eq!(second.reused, 5);
        assert!(directory.join(PREVIOUS_MANIFEST_NAME).is_file());
        fs::remove_dir_all(directory).expect("remove fixture");
    }

    #[test]
    fn one_changed_section_only_writes_one_segment_and_preserves_previous_manifest() {
        let directory = fixture_directory();
        let writer = SegmentWriter::new(&directory);
        writer
            .publish_payloads(payload(b"v1"), "gen-1".into(), "a".repeat(64))
            .expect("first publish");
        let second = writer
            .publish_payloads(payload(b"v2"), "gen-2".into(), "b".repeat(64))
            .expect("incremental publish");
        assert_eq!(second.written, 1);
        assert_eq!(second.reused, 4);

        let current: Value =
            serde_json::from_slice(&fs::read(directory.join(MANIFEST_NAME)).expect("current"))
                .expect("current JSON");
        let previous: Value = serde_json::from_slice(
            &fs::read(directory.join(PREVIOUS_MANIFEST_NAME)).expect("previous"),
        )
        .expect("previous JSON");
        assert_eq!(current["generation"], "gen-2");
        assert_eq!(previous["generation"], "gen-1");
        fs::remove_dir_all(directory).expect("remove fixture");
    }

    #[test]
    fn unsafe_segment_names_fail_closed_before_publication() {
        let directory = fixture_directory();
        let writer = SegmentWriter::new(&directory);
        let result = writer.publish_payloads(
            BTreeMap::from([("../escape".into(), b"bad".to_vec())]),
            "gen".into(),
            "a".repeat(64),
        );
        assert!(matches!(
            result,
            Err(SnapshotError::Invalid(reason)) if reason == "unsafe segment name"
        ));
        assert!(!directory.join(MANIFEST_NAME).exists());
        fs::remove_dir_all(directory).expect("remove fixture");
    }
}
