//! zarrs_oracle: an independent (Rust, zarrs-based) reference for zarr v3 arrays.
//!
//! All subcommands operate on a filesystem store rooted at <dir> with the array at "/".
//!
//!   check    <dir>           open the array (parse metadata, bind the codec chain)
//!   retrieve <dir>           decode the whole array; print raw little-endian element bytes as hex
//!   store    <dir> <hex>     encode <hex> (raw element bytes of the whole array) through the
//!                            codec chain and write the chunk(s)
//!   batch    <file>          run many operations from <file>; one per line:
//!                            `check\t<dir>` | `retrieve\t<dir>` | `store\t<dir>\t<hex>`
//!                            and print one result line per input line:
//!                            `<status>\t<payload>` where status is one of
//!                            ok | open_error | decode_error | encode_error | usage_error
//!
//! Exit codes for single commands: 0 ok, 1 usage, 2 open/metadata error, 3 decode error,
//! 4 encode error. `batch` exits 0 whenever every line was processed (per-line status in output).
use std::io::Write;
use std::sync::Arc;

use zarrs::array::{Array, ArrayBytes};
use zarrs::filesystem::FilesystemStore;

#[derive(Debug)]
enum Status {
    Ok,
    OpenError,
    DecodeError,
    EncodeError,
    UsageError,
}

impl Status {
    fn label(&self) -> &'static str {
        match self {
            Status::Ok => "ok",
            Status::OpenError => "open_error",
            Status::DecodeError => "decode_error",
            Status::EncodeError => "encode_error",
            Status::UsageError => "usage_error",
        }
    }
    fn exit_code(&self) -> i32 {
        match self {
            Status::Ok => 0,
            Status::UsageError => 1,
            Status::OpenError => 2,
            Status::DecodeError => 3,
            Status::EncodeError => 4,
        }
    }
}

fn hex_encode(bytes: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        out.push(DIGITS[(b >> 4) as usize] as char);
        out.push(DIGITS[(b & 0x0f) as usize] as char);
    }
    out
}

fn hex_decode(s: &str) -> Result<Vec<u8>, String> {
    let s = s.trim();
    if s.len() % 2 != 0 {
        return Err("hex string has odd length".to_string());
    }
    let bytes = s.as_bytes();
    let mut out = Vec::with_capacity(bytes.len() / 2);
    for i in (0..bytes.len()).step_by(2) {
        let hi = (bytes[i] as char).to_digit(16).ok_or("bad hex digit")?;
        let lo = (bytes[i + 1] as char).to_digit(16).ok_or("bad hex digit")?;
        out.push(((hi << 4) | lo) as u8);
    }
    Ok(out)
}

fn sanitize(msg: String) -> String {
    msg.replace(['\t', '\n', '\r'], " ")
}

fn open(dir: &str) -> Result<Array<FilesystemStore>, String> {
    let store = FilesystemStore::new(dir).map_err(|e| format!("store error: {e}"))?;
    Array::open(Arc::new(store), "/").map_err(|e| format!("open error: {e}"))
}

/// Execute one operation. Returns (status, payload).
fn run_op(op: &str, dir: &str, hex: Option<&str>) -> (Status, String) {
    let array = match open(dir) {
        Ok(a) => a,
        Err(e) => return (Status::OpenError, sanitize(e)),
    };
    match op {
        "check" => (Status::Ok, format!("{}", array.data_type())),
        "retrieve" => {
            let subset = array.subset_all();
            let bytes = match array.retrieve_array_subset::<ArrayBytes<'static>>(&subset) {
                Ok(b) => b,
                Err(e) => return (Status::DecodeError, sanitize(format!("decode error: {e}"))),
            };
            match bytes.into_fixed() {
                Ok(fixed) => (Status::Ok, hex_encode(&fixed)),
                Err(e) => (Status::DecodeError, sanitize(format!("decode error: {e}"))),
            }
        }
        "store" => {
            let Some(hex) = hex else {
                return (Status::UsageError, "store requires hex payload".to_string());
            };
            let raw = match hex_decode(hex) {
                Ok(r) => r,
                Err(e) => return (Status::UsageError, e),
            };
            let subset = array.subset_all();
            match array.store_array_subset(&subset, ArrayBytes::new_flen(raw)) {
                Ok(()) => (Status::Ok, String::new()),
                Err(e) => (Status::EncodeError, sanitize(format!("encode error: {e}"))),
            }
        }
        _ => (Status::UsageError, format!("unknown op {op}")),
    }
}

fn usage() -> ! {
    eprintln!("usage: zarrs_oracle check <dir> | retrieve <dir> | store <dir> <hex> | batch <file>");
    std::process::exit(1)
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 3 {
        usage();
    }
    let cmd = args[1].as_str();
    if cmd == "batch" {
        let content = match std::fs::read_to_string(&args[2]) {
            Ok(c) => c,
            Err(e) => {
                eprintln!("cannot read batch file: {e}");
                std::process::exit(1);
            }
        };
        let stdout = std::io::stdout();
        let mut out = std::io::BufWriter::new(stdout.lock());
        for line in content.lines() {
            if line.trim().is_empty() {
                continue;
            }
            let fields: Vec<&str> = line.split('\t').collect();
            let (status, payload) = if fields.len() < 2 {
                (Status::UsageError, "malformed batch line".to_string())
            } else {
                run_op(fields[0], fields[1], fields.get(2).copied())
            };
            writeln!(out, "{}\t{}", status.label(), payload).expect("stdout write failed");
        }
        out.flush().expect("stdout flush failed");
        return;
    }
    let dir = args[2].as_str();
    let hex = args.get(3).map(String::as_str);
    if cmd == "store" && hex.is_none() {
        usage();
    }
    let (status, payload) = run_op(cmd, dir, hex);
    match status {
        Status::Ok => {
            if !payload.is_empty() {
                println!("{payload}");
            }
        }
        _ => eprintln!("{payload}"),
    }
    std::process::exit(status.exit_code());
}
