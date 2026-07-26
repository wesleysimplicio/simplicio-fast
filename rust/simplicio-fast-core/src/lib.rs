//! Safe, dependency-light reader for the SFAST001/v2 snapshot contract.
//!
//! The source tree remains authoritative. This reader treats snapshots as
//! disposable derived data and validates every boundary and checksum before
//! exposing statistics to a caller. It intentionally has no Python runtime
//! dependency and does not use unsafe mmap access in this first core slice.

use serde::Serialize;
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, fmt, fs, path::Path};

pub const MAGIC: &[u8; 8] = b"SFAST001";
pub const VERSION: u16 = 2;
pub const ENDIAN_MARKER: u16 = 0x0102;
pub const HEADER_SIZE: usize = 80;
pub const SECTION_SIZE: usize = 64;
pub const FILE_RECORD_SIZE: usize = 64;
pub const SYMBOL_RECORD_SIZE: usize = 72;
pub const MAX_SNAPSHOT_BYTES: usize = 512 * 1024 * 1024;
pub const REQUIRED_SECTIONS: [&str; 5] = ["files", "symbols", "relations", "indexes", "strings"];

#[derive(Debug)]
pub enum SnapshotError {
    Io(std::io::Error),
    Invalid(String),
}

impl fmt::Display for SnapshotError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Io(error) => write!(f, "I/O error: {error}"),
            Self::Invalid(reason) => write!(f, "invalid snapshot: {reason}"),
        }
    }
}

impl std::error::Error for SnapshotError {}

impl From<std::io::Error> for SnapshotError {
    fn from(error: std::io::Error) -> Self {
        Self::Io(error)
    }
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct SnapshotStats {
    pub schema: String,
    pub format_version: u16,
    pub bytes: usize,
    pub files: usize,
    pub symbols: usize,
    pub relations: usize,
    pub sections: Vec<String>,
    pub generation: String,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct RustSymbol {
    pub name: String,
    pub qualified_name: String,
    pub kind: String,
    pub file: String,
    pub line: u32,
    pub end_line: u32,
    pub symbol_id: String,
    pub signature: String,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct RustContextSpan {
    pub symbol: String,
    pub kind: String,
    pub file: String,
    pub start_line: u32,
    pub end_line: u32,
    pub source_sha256: String,
    pub content: String,
    pub symbol_id: String,
    pub tokens: usize,
}

#[derive(Debug, Clone)]
struct Section {
    offset: usize,
    length: usize,
}

pub struct SnapshotReader {
    bytes: Vec<u8>,
    sections: BTreeMap<String, Section>,
    digest: [u8; 32],
}

impl SnapshotReader {
    pub fn open(path: impl AsRef<Path>) -> Result<Self, SnapshotError> {
        Self::from_bytes(fs::read(path)?)
    }

    pub fn from_bytes(bytes: Vec<u8>) -> Result<Self, SnapshotError> {
        if bytes.len() < HEADER_SIZE {
            return Err(SnapshotError::Invalid("truncated header".into()));
        }
        if bytes.len() > MAX_SNAPSHOT_BYTES {
            return Err(SnapshotError::Invalid(
                "snapshot size limit exceeded".into(),
            ));
        }
        if &bytes[..8] != MAGIC {
            return Err(SnapshotError::Invalid("invalid magic".into()));
        }
        let version = u16_at(&bytes, 8)?;
        let endian = u16_at(&bytes, 10)?;
        let section_count = u32_at(&bytes, 12)? as usize;
        let directory_offset = u64_at(&bytes, 24)? as usize;
        let directory_size = u64_at(&bytes, 32)? as usize;
        let total_size = u64_at(&bytes, 40)? as usize;
        if version != VERSION || endian != ENDIAN_MARKER {
            return Err(SnapshotError::Invalid(
                "unsupported version or endian marker".into(),
            ));
        }
        if total_size != bytes.len() {
            return Err(SnapshotError::Invalid("total size mismatch".into()));
        }
        if !(REQUIRED_SECTIONS.len()..=32).contains(&section_count)
            || directory_size != section_count * SECTION_SIZE
            || directory_offset < HEADER_SIZE
            || !region_valid(directory_offset, directory_size, bytes.len())
        {
            return Err(SnapshotError::Invalid("invalid section directory".into()));
        }

        let expected_digest = slice32(&bytes, 48)?;
        let mut whole = bytes.clone();
        whole[48..80].fill(0);
        let payload_digest = Sha256::digest(&whole);
        if payload_digest.as_slice() != expected_digest {
            return Err(SnapshotError::Invalid("snapshot checksum mismatch".into()));
        }
        let snapshot_digest = Sha256::digest(&bytes);

        let directory_end = directory_offset + directory_size;
        let mut sections = BTreeMap::new();
        let mut ranges = Vec::with_capacity(section_count);
        for index in 0..section_count {
            let start = directory_offset + index * SECTION_SIZE;
            let name_bytes = &bytes[start..start + 16];
            let name_end = name_bytes.iter().position(|byte| *byte == 0).unwrap_or(16);
            let name = std::str::from_utf8(&name_bytes[..name_end])
                .map_err(|_| SnapshotError::Invalid("non-ASCII section name".into()))?;
            if name.is_empty() || sections.contains_key(name) {
                return Err(SnapshotError::Invalid(
                    "duplicate or empty section name".into(),
                ));
            }
            let offset = u64_at(&bytes, start + 16)? as usize;
            let length = u64_at(&bytes, start + 24)? as usize;
            if offset % 8 != 0
                || offset < directory_end
                || !region_valid(offset, length, bytes.len())
            {
                return Err(SnapshotError::Invalid(format!(
                    "invalid section bounds: {name}"
                )));
            }
            let expected = slice32(&bytes, start + 32)?;
            let actual = Sha256::digest(&bytes[offset..offset + length]);
            if actual.as_slice() != expected {
                return Err(SnapshotError::Invalid(format!(
                    "section checksum mismatch: {name}"
                )));
            }
            ranges.push((offset, offset + length));
            sections.insert(name.to_owned(), Section { offset, length });
        }
        for (index, (left_start, left_end)) in ranges.iter().enumerate() {
            for (right_start, right_end) in ranges.iter().skip(index + 1) {
                if *left_start < *right_end && *right_start < *left_end {
                    return Err(SnapshotError::Invalid("overlapping sections".into()));
                }
            }
        }
        for required in REQUIRED_SECTIONS {
            if !sections.contains_key(required) {
                return Err(SnapshotError::Invalid(format!(
                    "missing section: {required}"
                )));
            }
        }
        if sections["files"].length % FILE_RECORD_SIZE != 0
            || sections["symbols"].length % SYMBOL_RECORD_SIZE != 0
        {
            return Err(SnapshotError::Invalid(
                "record section is not aligned".into(),
            ));
        }
        let relation_value: serde_json::Value =
            serde_json::from_slice(section_bytes(&bytes, &sections["relations"]))
                .map_err(|_| SnapshotError::Invalid("invalid relation JSON".into()))?;
        if !relation_value.is_array() {
            return Err(SnapshotError::Invalid(
                "relation section is not an array".into(),
            ));
        }
        Ok(Self {
            bytes,
            sections,
            digest: snapshot_digest.into(),
        })
    }

    pub fn stats(&self) -> SnapshotStats {
        let relations = serde_json::from_slice::<serde_json::Value>(section_bytes(
            &self.bytes,
            &self.sections["relations"],
        ))
        .ok()
        .and_then(|value| value.as_array().map(Vec::len))
        .unwrap_or(0);
        SnapshotStats {
            schema: "simplicio.fast.rust-stats/v1".into(),
            format_version: VERSION,
            bytes: self.bytes.len(),
            files: self.sections["files"].length / FILE_RECORD_SIZE,
            symbols: self.sections["symbols"].length / SYMBOL_RECORD_SIZE,
            relations,
            sections: self.sections.keys().cloned().collect(),
            generation: format!("SFAST001:{}", hex(&self.digest)),
        }
    }

    pub fn symbols(&self) -> Result<Vec<RustSymbol>, SnapshotError> {
        let files = self.file_paths()?;
        let strings = &self.sections["strings"];
        let symbols = &self.sections["symbols"];
        let mut result = Vec::with_capacity(symbols.length / SYMBOL_RECORD_SIZE);
        for index in 0..symbols.length / SYMBOL_RECORD_SIZE {
            let base = symbols.offset + index * SYMBOL_RECORD_SIZE;
            let name = read_text(
                &self.bytes,
                strings,
                u32_at(&self.bytes, base)? as usize,
                u32_at(&self.bytes, base + 4)? as usize,
            )?;
            let qualified_name = read_text(
                &self.bytes,
                strings,
                u32_at(&self.bytes, base + 8)? as usize,
                u32_at(&self.bytes, base + 12)? as usize,
            )?;
            let signature = read_text(
                &self.bytes,
                strings,
                u32_at(&self.bytes, base + 16)? as usize,
                u32_at(&self.bytes, base + 20)? as usize,
            )?;
            let file_index = u32_at(&self.bytes, base + 24)? as usize;
            let file = files
                .get(file_index)
                .ok_or_else(|| SnapshotError::Invalid("symbol file index out of bounds".into()))?;
            let kind = kind_name(u32_at(&self.bytes, base + 36)?)?;
            let symbol_id = hex_bytes(&self.bytes[base + 40..base + 72]);
            result.push(RustSymbol {
                name,
                qualified_name,
                kind,
                file: file.clone(),
                line: u32_at(&self.bytes, base + 28)?,
                end_line: u32_at(&self.bytes, base + 32)?,
                symbol_id,
                signature,
            });
        }
        Ok(result)
    }

    pub fn query(&self, term: &str, limit: usize) -> Result<Vec<RustSymbol>, SnapshotError> {
        let needle = term.to_lowercase();
        let mut matches: Vec<RustSymbol> = self
            .symbols()?
            .into_iter()
            .filter(|symbol| {
                symbol.name.to_lowercase().contains(&needle)
                    || symbol.qualified_name.to_lowercase().contains(&needle)
            })
            .collect();
        matches.sort_by(|left, right| {
            left.name
                .to_lowercase()
                .cmp(&right.name.to_lowercase())
                .then(left.qualified_name.cmp(&right.qualified_name))
                .then(left.file.cmp(&right.file))
                .then(left.line.cmp(&right.line))
        });
        matches.truncate(limit);
        Ok(matches)
    }

    pub fn context(
        &self,
        root: &Path,
        term: &str,
        max_results: usize,
        max_lines: u32,
        max_bytes: usize,
        max_tokens: usize,
    ) -> Result<Vec<RustContextSpan>, SnapshotError> {
        let root = root
            .canonicalize()
            .map_err(|_| SnapshotError::Invalid("repository root is unavailable".into()))?;
        let files = self.file_info()?;
        let mut result = Vec::new();
        let mut consumed_bytes = 0;
        let mut consumed_tokens = 0;
        for symbol in self.query(term, max_results)? {
            let Some((_, expected_digest)) = files.iter().find(|(path, _)| path == &symbol.file)
            else {
                return Err(SnapshotError::Invalid(
                    "symbol references unknown file".into(),
                ));
            };
            let path = root
                .join(&symbol.file)
                .canonicalize()
                .map_err(|_| SnapshotError::Invalid(format!("source missing: {}", symbol.file)))?;
            if path.strip_prefix(&root).is_err() {
                return Err(SnapshotError::Invalid("snapshot path escapes root".into()));
            }
            let bytes = fs::read(&path)?;
            let actual_digest = Sha256::digest(&bytes);
            if actual_digest.as_slice() != expected_digest {
                return Err(SnapshotError::Invalid(format!(
                    "stale source: {}",
                    symbol.file
                )));
            }
            let text = String::from_utf8(bytes)
                .map_err(|_| SnapshotError::Invalid("source is not UTF-8".into()))?;
            let lines: Vec<&str> = text.lines().collect();
            let start = symbol.line.max(1) as usize;
            let end = std::cmp::min(
                symbol.end_line,
                symbol.line.saturating_add(max_lines).saturating_sub(1),
            ) as usize;
            if start > lines.len() || end < start {
                return Err(SnapshotError::Invalid(
                    "symbol line bounds out of range".into(),
                ));
            }
            let mut content = lines[start - 1..end].join("\n");
            if consumed_bytes + content.len() > max_bytes {
                let remaining = max_bytes.saturating_sub(consumed_bytes);
                if remaining == 0 {
                    break;
                }
                content = truncate_utf8(&content, remaining);
            }
            let mut tokens = std::cmp::max(1, (content.len() + 3) / 4);
            if consumed_tokens + tokens > max_tokens {
                let remaining = max_tokens.saturating_sub(consumed_tokens);
                if remaining == 0 {
                    break;
                }
                content = truncate_utf8(&content, remaining * 4);
                tokens = std::cmp::max(1, (content.len() + 3) / 4);
            }
            consumed_bytes += content.len();
            consumed_tokens += tokens;
            result.push(RustContextSpan {
                symbol: symbol.qualified_name,
                kind: symbol.kind,
                file: symbol.file,
                start_line: symbol.line,
                end_line: end as u32,
                source_sha256: hex_bytes(&actual_digest),
                content,
                symbol_id: symbol.symbol_id,
                tokens,
            });
        }
        Ok(result)
    }

    fn file_paths(&self) -> Result<Vec<String>, SnapshotError> {
        Ok(self
            .file_info()?
            .into_iter()
            .map(|(path, _)| path)
            .collect())
    }

    fn file_info(&self) -> Result<Vec<(String, [u8; 32])>, SnapshotError> {
        let files = &self.sections["files"];
        let strings = &self.sections["strings"];
        let mut paths = Vec::with_capacity(files.length / FILE_RECORD_SIZE);
        for index in 0..files.length / FILE_RECORD_SIZE {
            let base = files.offset + index * FILE_RECORD_SIZE;
            let path = read_text(
                &self.bytes,
                strings,
                u32_at(&self.bytes, base)? as usize,
                u32_at(&self.bytes, base + 4)? as usize,
            )?;
            let digest: [u8; 32] = self.bytes[base + 16..base + 48]
                .try_into()
                .map_err(|_| SnapshotError::Invalid("file digest bounds".into()))?;
            paths.push((path, digest));
        }
        Ok(paths)
    }
}

pub fn manifest() -> serde_json::Value {
    serde_json::json!({
        "schema": "simplicio.fast.engine-manifest/v1",
        "engine": "rust",
        "version": env!("CARGO_PKG_VERSION"),
        "status": "available",
        "reference": false,
        "fallback": false,
        "capabilities": ["version", "stats", "doctor", "snapshot-read"],
        "formats": ["SFAST001/v2"]
        ,"conformance": {"passed": false, "reason": "harness_not_integrated"}
    })
}

fn region_valid(offset: usize, length: usize, total: usize) -> bool {
    offset <= total && length <= total.saturating_sub(offset)
}

fn section_bytes<'a>(bytes: &'a [u8], section: &Section) -> &'a [u8] {
    &bytes[section.offset..section.offset + section.length]
}

fn read_text(
    bytes: &[u8],
    strings: &Section,
    offset: usize,
    length: usize,
) -> Result<String, SnapshotError> {
    if !region_valid(offset, length, strings.length) {
        return Err(SnapshotError::Invalid("string bounds out of range".into()));
    }
    String::from_utf8(bytes[strings.offset + offset..strings.offset + offset + length].to_vec())
        .map_err(|_| SnapshotError::Invalid("invalid UTF-8 string".into()))
}

fn truncate_utf8(value: &str, max_bytes: usize) -> String {
    String::from_utf8_lossy(&value.as_bytes()[..max_bytes.min(value.len())]).into_owned()
}

fn kind_name(kind: u32) -> Result<String, SnapshotError> {
    let value = match kind {
        1 => "class",
        2 => "function",
        3 => "async_function",
        4 => "import",
        5 => "namespace",
        6 => "interface",
        7 => "struct",
        8 => "trait",
        9 => "enum",
        _ => return Err(SnapshotError::Invalid("unknown symbol kind".into())),
    };
    Ok(value.into())
}

fn u16_at(bytes: &[u8], offset: usize) -> Result<u16, SnapshotError> {
    let slice = bytes
        .get(offset..offset + 2)
        .ok_or_else(|| SnapshotError::Invalid("truncated integer".into()))?;
    Ok(u16::from_le_bytes([slice[0], slice[1]]))
}

fn u32_at(bytes: &[u8], offset: usize) -> Result<u32, SnapshotError> {
    let slice = bytes
        .get(offset..offset + 4)
        .ok_or_else(|| SnapshotError::Invalid("truncated integer".into()))?;
    Ok(u32::from_le_bytes([slice[0], slice[1], slice[2], slice[3]]))
}

fn u64_at(bytes: &[u8], offset: usize) -> Result<u64, SnapshotError> {
    let slice = bytes
        .get(offset..offset + 8)
        .ok_or_else(|| SnapshotError::Invalid("truncated integer".into()))?;
    Ok(u64::from_le_bytes(
        slice.try_into().expect("checked length"),
    ))
}

fn slice32(bytes: &[u8], offset: usize) -> Result<&[u8; 32], SnapshotError> {
    bytes
        .get(offset..offset + 32)
        .and_then(|slice| slice.try_into().ok())
        .ok_or_else(|| SnapshotError::Invalid("truncated checksum".into()))
}

fn hex(bytes: &[u8; 32]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn hex_bytes(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_short_input() {
        let result = SnapshotReader::from_bytes(vec![0; HEADER_SIZE - 1]);
        assert!(
            matches!(result, Err(SnapshotError::Invalid(reason)) if reason == "truncated header")
        );
    }

    #[test]
    fn manifest_is_versioned() {
        assert_eq!(manifest()["schema"], "simplicio.fast.engine-manifest/v1");
        assert_eq!(manifest()["engine"], "rust");
    }
}
