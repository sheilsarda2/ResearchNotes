//! Differential check of the PyTorch reader against checkpoints written by PyTorch itself.
//!
//! Compiled by the verifier as an integration test of `burn-store` (copied to
//! `crates/burn-store/tests/fixture_matrix.rs`), so it sees only the crate's public API.
//! The fixture directory (`BURN_PT_FIXTURES`) holds the files and `manifest.tsv`, whose
//! rows are documented in `gen_fixtures.py`. Every check appends one line to the file named
//! by `BURN_PT_RESULTS` so failures can be attributed per fixture.

use burn_core::tensor::DType;
use burn_store::bridge::to_data;
use burn_store::pytorch::PytorchReader;
use burn_store::pytorch::reader::PickleValue;
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::io::Write;
use std::path::PathBuf;

fn fixtures_dir() -> PathBuf {
    PathBuf::from(
        std::env::var("BURN_PT_FIXTURES").expect("BURN_PT_FIXTURES must name the fixture directory"),
    )
}

fn read_manifest(dir: &std::path::Path) -> Vec<Vec<String>> {
    let text = fs::read_to_string(dir.join("manifest.tsv")).expect("manifest.tsv");
    text.lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| line.split('\t').map(str::to_string).collect())
        .collect()
}

struct Results {
    file: Option<fs::File>,
    failures: usize,
    passes: usize,
}

impl Results {
    fn open() -> Self {
        let file = std::env::var("BURN_PT_RESULTS").ok().map(|path| {
            fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(path)
                .expect("results file")
        });
        Self {
            file,
            failures: 0,
            passes: 0,
        }
    }

    fn pass(&mut self, line: String) {
        self.passes += 1;
        self.emit(format!("PASS {line}"));
    }

    fn fail(&mut self, line: String) {
        self.failures += 1;
        self.emit(format!("FAIL {line}"));
    }

    fn emit(&mut self, line: String) {
        println!("{line}");
        if let Some(file) = &mut self.file {
            let _ = writeln!(file, "{line}");
        }
    }
}

/// zlib-compatible CRC-32 (reflected, polynomial 0xEDB88320).
fn crc32(data: &[u8]) -> u32 {
    let mut crc = 0xFFFF_FFFFu32;
    for &byte in data {
        crc ^= byte as u32;
        for _ in 0..8 {
            let mask = 0u32.wrapping_sub(crc & 1);
            crc = (crc >> 1) ^ (0xEDB8_8320 & mask);
        }
    }
    !crc
}

fn bytesum(data: &[u8]) -> u64 {
    data.iter().map(|&b| b as u64).sum()
}

fn dtype_name(dtype: DType) -> String {
    match dtype {
        DType::F64 => "F64".to_string(),
        DType::F32 => "F32".to_string(),
        DType::F16 => "F16".to_string(),
        DType::BF16 => "BF16".to_string(),
        DType::I64 => "I64".to_string(),
        DType::I32 => "I32".to_string(),
        DType::I16 => "I16".to_string(),
        DType::I8 => "I8".to_string(),
        DType::U64 => "U64".to_string(),
        DType::U32 => "U32".to_string(),
        DType::U16 => "U16".to_string(),
        DType::U8 => "U8".to_string(),
        DType::Bool(_) => "BOOL".to_string(),
        other => format!("{other:?}"),
    }
}

fn parse_dims(text: &str) -> Vec<usize> {
    if text == "-" {
        return Vec::new();
    }
    text.split(',').map(|d| d.parse().expect("dim")).collect()
}

#[derive(Clone)]
struct ExpectedTensor {
    dtype: String,
    dims: Vec<usize>,
    numel: usize,
    crc: u32,
    sum: u64,
}

fn key_of(field: &str) -> Option<&str> {
    if field == "-" { None } else { Some(field) }
}

fn open(path: &std::path::Path, key: Option<&str>) -> Result<PytorchReader, String> {
    match key {
        None => PytorchReader::new(path),
        Some(key) => PytorchReader::with_top_level_key(path, key),
    }
    .map_err(|e| e.to_string())
}

/// Every file the reader must accept: tensor names, dtypes, shapes and bytes must match what
/// PyTorch wrote; metadata and `read_pickle_data` are checked where the manifest records them.
#[test]
fn matrix_accepts() {
    let dir = fixtures_dir();
    let rows = read_manifest(&dir);
    let mut results = Results::open();

    let mut expected: BTreeMap<(String, String), BTreeMap<String, ExpectedTensor>> = BTreeMap::new();
    for row in rows.iter().filter(|r| r[0] == "tensor") {
        expected
            .entry((row[1].clone(), row[2].clone()))
            .or_default()
            .insert(
                row[3].clone(),
                ExpectedTensor {
                    dtype: row[4].clone(),
                    dims: parse_dims(&row[5]),
                    numel: row[6].parse().expect("numel"),
                    crc: u32::from_str_radix(&row[7], 16).expect("crc32"),
                    sum: row[8].parse().expect("bytesum"),
                },
            );
    }

    for row in rows.iter().filter(|r| r[0] == "entries") {
        let (file, key_field) = (&row[1], &row[2]);
        let count: usize = row[3].parse().expect("count");
        let label = format!("ACCEPT {file} key={key_field}");
        let path = dir.join(file);
        let reader = match open(&path, key_of(key_field)) {
            Ok(reader) => reader,
            Err(err) => {
                results.fail(format!("{label}: open failed: {err}"));
                continue;
            }
        };
        let mut problems = Vec::new();
        let names: BTreeSet<String> = reader.keys().into_iter().collect();
        if reader.len() != count || names.len() != count {
            problems.push(format!(
                "expected {count} tensors, reader reports len={} keys={}",
                reader.len(),
                names.len()
            ));
        }
        let wanted = expected
            .get(&(file.clone(), key_field.clone()))
            .cloned()
            .unwrap_or_default();
        let wanted_names: BTreeSet<String> = wanted.keys().cloned().collect();
        for missing in wanted_names.difference(&names) {
            problems.push(format!("missing tensor '{missing}'"));
        }
        for extra in names.difference(&wanted_names) {
            problems.push(format!("unexpected tensor '{extra}'"));
        }
        for (name, want) in &wanted {
            let Some(tensor) = reader.get(name) else { continue };
            let data = match to_data(tensor) {
                Ok(data) => data,
                Err(err) => {
                    problems.push(format!("'{name}': materialization failed: {err}"));
                    continue;
                }
            };
            let got_dtype = dtype_name(data.dtype);
            if got_dtype != want.dtype {
                problems.push(format!("'{name}': dtype {got_dtype}, expected {}", want.dtype));
            }
            let got_dims = data.shape.to_vec();
            if got_dims != want.dims {
                problems.push(format!("'{name}': shape {got_dims:?}, expected {:?}", want.dims));
            }
            if data.num_elements() != want.numel {
                problems.push(format!(
                    "'{name}': {} elements, expected {}",
                    data.num_elements(),
                    want.numel
                ));
            }
            let bytes = data.as_bytes();
            let (crc, sum) = (crc32(bytes), bytesum(bytes));
            if crc != want.crc || sum != want.sum {
                problems.push(format!(
                    "'{name}': bytes crc32={crc:08x} sum={sum} ({} bytes), expected crc32={:08x} sum={}",
                    bytes.len(),
                    want.crc,
                    want.sum
                ));
            }
        }
        if problems.is_empty() {
            results.pass(format!("{label}: {count} tensors match"));
        } else {
            results.fail(format!("{label}: {}", problems.join(" | ")));
        }
    }

    for row in rows.iter().filter(|r| r[0] == "meta") {
        let file = &row[1];
        let label = format!("META {file}");
        let reader = match open(&dir.join(file), None) {
            Ok(reader) => reader,
            Err(err) => {
                results.fail(format!("{label}: open failed: {err}"));
                continue;
            }
        };
        let meta = reader.metadata();
        let mut problems = Vec::new();
        let format = format!("{:?}", meta.format_type);
        if format != row[2] {
            problems.push(format!("format_type {format}, expected {}", row[2]));
        }
        let version = key_of(&row[3]).map(str::to_string);
        if meta.pytorch_version != version {
            problems.push(format!(
                "pytorch_version {:?}, expected {version:?}",
                meta.pytorch_version
            ));
        }
        let format_version = key_of(&row[4]).map(str::to_string);
        if meta.format_version != format_version {
            problems.push(format!(
                "format_version {:?}, expected {format_version:?}",
                meta.format_version
            ));
        }
        if let Some(align) = key_of(&row[5]) {
            let expected_align = align == "1";
            if meta.has_storage_alignment != expected_align {
                problems.push(format!(
                    "has_storage_alignment {}, expected {expected_align}",
                    meta.has_storage_alignment
                ));
            }
        }
        if let Some(size) = key_of(&row[6]) {
            let expected_size: usize = size.parse().expect("data size");
            if meta.total_data_size != Some(expected_size) {
                problems.push(format!(
                    "total_data_size {:?}, expected Some({expected_size})",
                    meta.total_data_size
                ));
            }
        }
        if meta.tensor_count != reader.len() {
            problems.push(format!(
                "tensor_count {} but len() is {}",
                meta.tensor_count,
                reader.len()
            ));
        }
        if problems.is_empty() {
            results.pass(format!("{label}: {format}"));
        } else {
            results.fail(format!("{label}: {}", problems.join(" | ")));
        }
    }

    for row in rows.iter().filter(|r| r[0] == "pickle_int") {
        let (file, key) = (&row[1], &row[2]);
        let value: i64 = row[3].parse().expect("int");
        let label = format!("PICKLE {file} key={key}");
        match PytorchReader::read_pickle_data(dir.join(file), Some(key)) {
            Ok(got) if got == PickleValue::Int(value) => results.pass(format!("{label}: Int({value})")),
            Ok(got) => results.fail(format!("{label}: got {got:?}, expected Int({value})")),
            Err(err) => results.fail(format!("{label}: {err}")),
        }
    }

    assert!(
        results.failures == 0,
        "{} fixture checks failed ({} passed)",
        results.failures,
        results.passes
    );
}

enum Outcome {
    RejectedAtOpen(String),
    RejectedAtRead(String),
    Accepted(usize),
}

/// One file the reader must refuse, selected by `BURN_PT_REJECT_FILE`; run in its own process
/// by the verifier so an abort or a runaway allocation is attributed to that file alone.
/// `BURN_PT_REJECT_PHASE` is `open` (PytorchReader::new must fail) or `any` (either open or
/// materializing some tensor must fail). Accepting the file, or panicking, is a failure.
#[test]
fn reject_case() {
    let file = std::env::var("BURN_PT_REJECT_FILE").expect("BURN_PT_REJECT_FILE");
    let phase = std::env::var("BURN_PT_REJECT_PHASE").unwrap_or_else(|_| "any".to_string());
    let path = fixtures_dir().join(&file);
    let mut results = Results::open();
    let label = format!("REJECT {file} phase={phase}");

    let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        match PytorchReader::new(&path) {
            Err(err) => Outcome::RejectedAtOpen(err.to_string()),
            Ok(reader) => {
                let mut errors = Vec::new();
                let mut loaded = 0usize;
                for name in reader.keys() {
                    match reader.get(&name).map(to_data) {
                        Some(Ok(_)) => loaded += 1,
                        Some(Err(err)) => errors.push(format!("'{name}': {err}")),
                        None => errors.push(format!("'{name}': vanished")),
                    }
                }
                if errors.is_empty() {
                    Outcome::Accepted(loaded)
                } else {
                    Outcome::RejectedAtRead(errors.join(" | "))
                }
            }
        }
    }));

    let verdict = match outcome {
        Err(_) => Err("panicked".to_string()),
        Ok(Outcome::Accepted(n)) => Err(format!("accepted the file and loaded {n} tensors")),
        Ok(Outcome::RejectedAtOpen(err)) => Ok(format!("rejected at open: {err}")),
        Ok(Outcome::RejectedAtRead(err)) if phase == "any" => Ok(format!("rejected at read: {err}")),
        Ok(Outcome::RejectedAtRead(err)) => Err(format!("opened successfully; rejected only at read: {err}")),
    };
    match verdict {
        Ok(detail) => results.pass(format!("{label}: {detail}")),
        Err(detail) => {
            results.fail(format!("{label}: {detail}"));
            panic!("{label}: {detail}");
        }
    }
}
