//! Safe, dependency-light reader for the SFAST001/v2 snapshot contract.
//!
//! The source tree remains authoritative. This reader treats snapshots as
//! disposable derived data and validates every boundary and checksum before
//! exposing statistics to a caller. It intentionally has no Python runtime
//! dependency and maps validated snapshots read-only when opened from disk.

use memmap2::{Mmap, MmapOptions};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
mod segment_writer;
pub use segment_writer::{PublishReceipt, PublishedSegment, SegmentWriter};

use std::{
    collections::BTreeMap,
    fmt, fs,
    fs::File,
    path::{Path, PathBuf},
};

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
pub(crate) struct Section {
    pub(crate) offset: usize,
    pub(crate) length: usize,
}

pub struct SnapshotReader {
    pub(crate) bytes: SnapshotBytes,
    pub(crate) sections: BTreeMap<String, Section>,
    digest: [u8; 32],
}

pub(crate) enum SnapshotBytes {
    Owned(Vec<u8>),
    Mapped(Mmap),
}

impl SnapshotBytes {
    pub(crate) fn as_slice(&self) -> &[u8] {
        match self {
            Self::Owned(bytes) => bytes,
            Self::Mapped(bytes) => bytes,
        }
    }

    fn len(&self) -> usize {
        self.as_slice().len()
    }
}

#[derive(Debug, Deserialize)]
struct SegmentManifest {
    schema: String,
    segments: Vec<SegmentEntry>,
}

#[derive(Debug, Deserialize)]
struct SegmentEntry {
    name: String,
    file: String,
    bytes: usize,
    sha256: String,
}

enum SegmentBytes {
    Empty,
    Mapped(Mmap),
}

pub struct MappedSegment {
    name: String,
    bytes: SegmentBytes,
    sha256: String,
}

impl MappedSegment {
    pub fn name(&self) -> &str {
        &self.name
    }

    pub fn as_bytes(&self) -> &[u8] {
        match &self.bytes {
            SegmentBytes::Empty => &[],
            SegmentBytes::Mapped(bytes) => bytes,
        }
    }

    pub fn sha256(&self) -> &str {
        &self.sha256
    }
}

pub struct SegmentReader {
    directory: PathBuf,
    manifest: SegmentManifest,
}

impl SegmentReader {
    pub fn open(directory: impl AsRef<Path>) -> Result<Self, SnapshotError> {
        let directory = directory.as_ref().canonicalize()?;
        let manifest_path = directory.join("manifest.json");
        let manifest: SegmentManifest = serde_json::from_slice(&fs::read(manifest_path)?)
            .map_err(|_| SnapshotError::Invalid("invalid segment manifest JSON".into()))?;
        if manifest.schema != "simplicio.fast.segments/v1" {
            return Err(SnapshotError::Invalid(
                "unsupported segment manifest schema".into(),
            ));
        }
        Ok(Self {
            directory,
            manifest,
        })
    }

    pub fn map(&self, name: &str) -> Result<MappedSegment, SnapshotError> {
        let entry = self
            .manifest
            .segments
            .iter()
            .find(|entry| entry.name == name)
            .ok_or_else(|| SnapshotError::Invalid(format!("segment not found: {name}")))?;
        let entry_path = Path::new(&entry.file);
        if entry_path.file_name().and_then(|value| value.to_str()) != Some(entry.file.as_str()) {
            return Err(SnapshotError::Invalid(
                "segment path escapes directory".into(),
            ));
        }
        let path = self.directory.join(entry_path).canonicalize()?;
        if path.strip_prefix(&self.directory).is_err() {
            return Err(SnapshotError::Invalid(
                "segment path escapes directory".into(),
            ));
        }
        let file = File::open(path)?;
        let length = u64_to_usize(file.metadata()?.len())?;
        if length != entry.bytes {
            return Err(SnapshotError::Invalid(format!(
                "segment size mismatch: {}",
                entry.name
            )));
        }
        if length == 0 {
            if entry.sha256 != hex_bytes(&Sha256::digest([])) {
                return Err(SnapshotError::Invalid(format!(
                    "segment checksum mismatch: {}",
                    entry.name
                )));
            }
            return Ok(MappedSegment {
                name: entry.name.clone(),
                bytes: SegmentBytes::Empty,
                sha256: entry.sha256.clone(),
            });
        }
        let mapped = unsafe { MmapOptions::new().map(&file)? };
        let actual = hex_bytes(&Sha256::digest(&mapped));
        if actual != entry.sha256 {
            return Err(SnapshotError::Invalid(format!(
                "segment checksum mismatch: {}",
                entry.name
            )));
        }
        Ok(MappedSegment {
            name: entry.name.clone(),
            bytes: SegmentBytes::Mapped(mapped),
            sha256: actual,
        })
    }
}

impl SnapshotReader {
    pub fn open(path: impl AsRef<Path>) -> Result<Self, SnapshotError> {
        let file = File::open(path)?;
        let length = u64_to_usize(file.metadata()?.len())?;
        if length > MAX_SNAPSHOT_BYTES {
            return Err(SnapshotError::Invalid(
                "snapshot size limit exceeded".into(),
            ));
        }
        // The mapped bytes are validated by `from_storage` before any record
        // is exposed. The mapping is read-only and owned by the reader.
        let mapped = unsafe { MmapOptions::new().map(&file)? };
        Self::from_storage(SnapshotBytes::Mapped(mapped))
    }

    pub fn from_bytes(bytes: Vec<u8>) -> Result<Self, SnapshotError> {
        Self::from_storage(SnapshotBytes::Owned(bytes))
    }

    fn from_storage(bytes: SnapshotBytes) -> Result<Self, SnapshotError> {
        let raw = bytes.as_slice();
        if raw.len() < HEADER_SIZE {
            return Err(SnapshotError::Invalid("truncated header".into()));
        }
        if raw.len() > MAX_SNAPSHOT_BYTES {
            return Err(SnapshotError::Invalid(
                "snapshot size limit exceeded".into(),
            ));
        }
        if &raw[..8] != MAGIC {
            return Err(SnapshotError::Invalid("invalid magic".into()));
        }
        let version = u16_at(raw, 8)?;
        let endian = u16_at(raw, 10)?;
        let section_count = u32_at(raw, 12)? as usize;
        let directory_offset = u64_to_usize(u64_at(raw, 24)?)?;
        let directory_size = u64_to_usize(u64_at(raw, 32)?)?;
        let total_size = u64_to_usize(u64_at(raw, 40)?)?;
        if version != VERSION || endian != ENDIAN_MARKER {
            return Err(SnapshotError::Invalid(
                "unsupported version or endian marker".into(),
            ));
        }
        if total_size != raw.len() {
            return Err(SnapshotError::Invalid("total size mismatch".into()));
        }
        if !(REQUIRED_SECTIONS.len()..=32).contains(&section_count)
            || directory_size != section_count * SECTION_SIZE
            || directory_offset < HEADER_SIZE
            || !region_valid(directory_offset, directory_size, raw.len())
        {
            return Err(SnapshotError::Invalid("invalid section directory".into()));
        }

        let expected_digest = slice32(raw, 48)?;
        let mut whole = raw.to_vec();
        whole[48..80].fill(0);
        let payload_digest = Sha256::digest(&whole);
        if payload_digest.as_slice() != expected_digest {
            return Err(SnapshotError::Invalid("snapshot checksum mismatch".into()));
        }
        let snapshot_digest = Sha256::digest(raw);

        let directory_end = directory_offset + directory_size;
        let mut sections = BTreeMap::new();
        let mut ranges = Vec::with_capacity(section_count);
        for index in 0..section_count {
            let start = directory_offset + index * SECTION_SIZE;
            let name_bytes = &raw[start..start + 16];
            let name_end = name_bytes.iter().position(|byte| *byte == 0).unwrap_or(16);
            let name = std::str::from_utf8(&name_bytes[..name_end])
                .map_err(|_| SnapshotError::Invalid("non-ASCII section name".into()))?;
            if name.is_empty() || sections.contains_key(name) {
                return Err(SnapshotError::Invalid(
                    "duplicate or empty section name".into(),
                ));
            }
            let offset = u64_to_usize(u64_at(raw, start + 16)?)?;
            let length = u64_to_usize(u64_at(raw, start + 24)?)?;
            if offset % 8 != 0 || offset < directory_end || !region_valid(offset, length, raw.len())
            {
                return Err(SnapshotError::Invalid(format!(
                    "invalid section bounds: {name}"
                )));
            }
            let expected = slice32(raw, start + 32)?;
            let actual = Sha256::digest(&raw[offset..offset + length]);
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
            serde_json::from_slice(section_bytes(raw, &sections["relations"]))
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
            self.bytes.as_slice(),
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
                self.bytes.as_slice(),
                strings,
                u32_at(self.bytes.as_slice(), base)? as usize,
                u32_at(self.bytes.as_slice(), base + 4)? as usize,
            )?;
            let qualified_name = read_text(
                self.bytes.as_slice(),
                strings,
                u32_at(self.bytes.as_slice(), base + 8)? as usize,
                u32_at(self.bytes.as_slice(), base + 12)? as usize,
            )?;
            let signature = read_text(
                self.bytes.as_slice(),
                strings,
                u32_at(self.bytes.as_slice(), base + 16)? as usize,
                u32_at(self.bytes.as_slice(), base + 20)? as usize,
            )?;
            let file_index = u32_at(self.bytes.as_slice(), base + 24)? as usize;
            let file = files
                .get(file_index)
                .ok_or_else(|| SnapshotError::Invalid("symbol file index out of bounds".into()))?;
            let kind = kind_name(u32_at(self.bytes.as_slice(), base + 36)?)?;
            let symbol_id = hex_bytes(&self.bytes.as_slice()[base + 40..base + 72]);
            result.push(RustSymbol {
                name,
                qualified_name,
                kind,
                file: file.clone(),
                line: u32_at(self.bytes.as_slice(), base + 28)?,
                end_line: u32_at(self.bytes.as_slice(), base + 32)?,
                symbol_id,
                signature,
            });
        }
        Ok(result)
    }

    pub fn query(&self, term: &str, limit: usize) -> Result<Vec<RustSymbol>, SnapshotError> {
        if limit == 0 {
            return Err(SnapshotError::Invalid(
                "query limit must be positive".into(),
            ));
        }
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
        if max_results == 0 || max_lines == 0 || max_bytes == 0 || max_tokens == 0 {
            return Err(SnapshotError::Invalid(
                "context limits must be positive".into(),
            ));
        }
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
                self.bytes.as_slice(),
                strings,
                u32_at(self.bytes.as_slice(), base)? as usize,
                u32_at(self.bytes.as_slice(), base + 4)? as usize,
            )?;
            let digest: [u8; 32] = self.bytes.as_slice()[base + 16..base + 48]
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
        "capabilities": ["version", "stats", "query", "context", "snapshot-read", "segment-map", "segment-write"],
        "formats": ["SFAST001/v2"]
        ,"conformance": {
            "passed": true,
            "digest": "sha256:1318d1ccc7f7cfe6622fd49855fe4f5dfd48771bd1d2d3403545f8d9ec1863cf",
            "corpus_sha256": "15d0f17f2bd3f897f823e75fd08e1a1e4866e8c48f395c41a00688eb8b7d6cbc",
            "source_commit": "54bc8cdea36c1f228e5612d98e5f2773175ab85a",
            "engine_sha256": {
                "python": "e8d0e2cc786e718a98a3a09d1887b222ea9bec59d4efae8aeae050feb28e1965",
                "rust": "9258cc600bc4bda434f3e2e61ed750e665b4b4bd0d3c41db664538df1733a695"
            }
        }
    })
}

fn u64_to_usize(value: u64) -> Result<usize, SnapshotError> {
    usize::try_from(value)
        .map_err(|_| SnapshotError::Invalid("64-bit field exceeds platform usize".into()))
}
fn region_valid(offset: usize, length: usize, total: usize) -> bool {
    offset <= total && length <= total.saturating_sub(offset)
}

pub(crate) fn section_bytes<'a>(bytes: &'a [u8], section: &Section) -> &'a [u8] {
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
    let end = value
        .char_indices()
        .take_while(|(offset, _)| *offset <= max_bytes)
        .map(|(offset, character)| offset + character.len_utf8())
        .take_while(|end| *end <= max_bytes)
        .last()
        .unwrap_or(0);
    value[..end].to_owned()
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

pub(crate) fn hex_bytes(bytes: &[u8]) -> String {
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

    fn empty_reader() -> SnapshotReader {
        SnapshotReader {
            bytes: SnapshotBytes::Owned(Vec::new()),
            sections: BTreeMap::new(),
            digest: [0; 32],
        }
    }

    #[test]
    fn query_rejects_zero_limit() {
        let result = empty_reader().query("symbol", 0);
        assert!(
            matches!(result, Err(SnapshotError::Invalid(reason)) if reason == "query limit must be positive")
        );
    }

    #[test]
    fn context_rejects_zero_budget() {
        let result = empty_reader().context(Path::new("."), "symbol", 1, 1, 1, 0);
        assert!(
            matches!(result, Err(SnapshotError::Invalid(reason)) if reason == "context limits must be positive")
        );
    }

    #[test]
    fn accepts_the_platform_usize_boundary() {
        let boundary = usize::MAX as u64;
        assert_eq!(
            u64_to_usize(boundary).expect("platform boundary"),
            usize::MAX
        );
        if usize::BITS < 64 {
            assert!(matches!(
                u64_to_usize(u64::MAX),
                Err(SnapshotError::Invalid(reason))
                    if reason == "64-bit field exceeds platform usize"
            ));
        }
    }

    #[test]
    fn truncate_utf8_stays_on_character_boundaries() {
        let truncated = truncate_utf8("café", 4);
        assert_eq!(truncated, "caf");
        assert!(truncated.len() <= 4);
        assert_eq!(truncate_utf8("café", 0), "");
        assert_eq!(truncate_utf8("abcdef", 3), "abc");
    }

    #[test]
    fn manifest_is_versioned() {
        assert_eq!(manifest()["schema"], "simplicio.fast.engine-manifest/v1");
        assert_eq!(manifest()["engine"], "rust");
    }

    #[test]
    fn open_maps_and_validates_a_file_boundary() {
        let path = std::env::temp_dir().join(format!(
            "simplicio-fast-invalid-{}.sfast",
            std::process::id()
        ));
        std::fs::write(&path, vec![0; HEADER_SIZE - 1]).expect("write fixture");
        let result = SnapshotReader::open(&path);
        let _ = std::fs::remove_file(&path);
        assert!(
            matches!(result, Err(SnapshotError::Invalid(reason)) if reason == "truncated header")
        );
    }

    #[test]
    fn segment_reader_maps_and_verifies_one_content_addressed_segment() {
        let directory =
            std::env::temp_dir().join(format!("simplicio-fast-segments-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&directory);
        std::fs::create_dir_all(&directory).expect("create segment fixture");
        let bytes = b"segment-payload";
        let digest = hex_bytes(&Sha256::digest(bytes));
        std::fs::write(directory.join("symbols.seg"), bytes).expect("write segment");
        std::fs::write(
            directory.join("manifest.json"),
            serde_json::json!({
                "schema": "simplicio.fast.segments/v1",
                "segments": [{"name":"symbols","file":"symbols.seg","bytes":bytes.len(),"sha256":digest}]
            })
            .to_string(),
        )
        .expect("write manifest");
        let mapped = SegmentReader::open(&directory)
            .expect("open segment store")
            .map("symbols")
            .expect("map segment");
        assert_eq!(mapped.as_bytes(), bytes);
        assert_eq!(mapped.sha256(), digest);
        drop(mapped);
        std::fs::remove_dir_all(directory).expect("remove segment fixture");
    }
}
