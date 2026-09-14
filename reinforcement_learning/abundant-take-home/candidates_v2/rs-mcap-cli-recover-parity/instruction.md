# Bring `mcap recover` in the Rust CLI to parity with the Go CLI

You are working in a checkout of the `foxglove/mcap` monorepo at `/workspace/repo`. The
Cargo workspace is `rust/` (crates `rust/mcap`, the library, and `rust/cli`, the `mcap`
binary). The repository is a git repo with the base state committed as tag `base`. Cargo
runs offline (`CARGO_NET_OFFLINE=true`; every crate in `Cargo.lock` is pre-fetched and
pre-built, and no new dependencies can be added), so `cargo build --offline -p mcap-cli`
(from `rust/`) is the build command. The binary is `rust/target/debug/mcap`. Treat the
environment as offline: the grader rebuilds your sources without network, and consulting
the upstream repository's later history for this feature is out of bounds.

The Rust CLI is a port of the legacy Go CLI (`go/cli/mcap`, source present, **no Go
toolchain and no Go binary are available in this environment**). `rust/cli/README.md`
tracks parity; `recover` is the remaining partially ported command. The current Rust
`recover` buffers the whole input in memory, exposes a Go-compatibility flag that the
Rust design no longer wants, always exits 0, and can lose track of what it discarded.
Make `mcap recover` meet the contract below. The contract is what is graded; it is
checked with a hidden differential harness that compares your binary against the Go CLI
built from this same commit, on the conformance corpus and on many deliberately
truncated and corrupted files.

Read `website/docs/spec/index.md` (the MCAP format specification) for record layouts:
Header (op 0x01), Footer (0x02), Schema (0x03), Channel (0x04), Message (0x05), Chunk
(0x06), Message Index (0x07), Chunk Index (0x08), Attachment (0x09), Statistics (0x0B),
Metadata (0x0C), Data End (0x0F); the opcode ranges (0x01-0x7F reserved for the spec,
0x80-0xFF private); and the rule that attachments never appear inside chunks.

## Command surface

```
mcap recover [FILE] [-o|--output PATH] [--chunk-size BYTES] [--compression preserve|none|zstd|lz4]
```

- `FILE` is a local path, an `http://` or `https://` URL, or omitted to read standard
  input. Reading from a terminal on stdin is an error (exit 1, message
  "please supply a file. see --help for usage details.").
- `-o PATH` writes the recovered MCAP to `PATH` (created or truncated). Without `-o`
  the MCAP bytes go to standard output, which must be redirected: writing MCAP to a
  terminal is an error (exit 1, the existing `PLEASE_REDIRECT` message in
  `commands/common.rs`). Standard output carries nothing but MCAP bytes; every
  human-readable line (summary, warnings, errors) goes to standard error. When `-o` is
  given, standard output stays empty.
- `--chunk-size BYTES` is the target uncompressed size of output chunks (default
  `mcap::WriteOptions::DEFAULT_CHUNK_SIZE`, 1 MiB). Output is always chunked; a small
  target on a large input must yield many chunks.
- `--compression` selects the output codec. The default is `preserve` (see "Output
  compression"). `none`, `zstd`, `lz4` force a codec. Any other value is a hard failure
  (exit 1) whose error message names all four valid options.
- The Go-only `--always-decode-chunk` / `-a` flag is **removed**: passing it is a
  command-line error (non-zero exit, and the error text names the flag, which clap does
  for unknown arguments). Chunks are always decoded.
- Remote inputs: a URL is read as a single forward stream, exactly like a file or stdin.
  Because it reads the whole object, it requires the existing global
  `--allow-remote-scan` flag; without it, fail (exit 1) before writing any output. A
  plain HTTP server that serves the file with identity encoding is sufficient.

## Exit codes

| code | meaning |
|---|---|
| 0 | clean recovery: every record in the readable data section was recovered; rebuilding indexes, statistics and CRCs is not loss |
| 3 | recovered but lossy: at least one record or message was discarded, or the scan stopped before a clean end (see below) |
| 1 | hard failure: cannot open input or output, invalid `--compression`, remote read without opt-in, or **no complete record could be read** (empty file, bad or short magic, input ending inside the first record) |
| 2 | command-line parsing errors (clap) |

The exit code is decided after the output has been completely written and flushed. On
exit 3 the output file is complete and valid; on exit 1 its contents are unspecified.

## Standard error

Exactly one summary line, on success or lossy completion:

```
Recovered <N> message(s), <M> attachment(s), and <K> metadata record(s).
```

using naive pluralization: `1 message` but `0 messages`, `2 messages`; `1 attachment` /
`0 attachments`; `1 metadata record` / `3 metadata records`. `N`, `M`, `K` are the
counts written to the output. Example: `Recovered 10 messages, 0 attachments, and 0
metadata records.` and `Recovered 1 message, 1 attachment, and 1 metadata record.`

When and only when the recovery is lossy (exit 3), also print a line that starts with
`Recovery was lossy:` describing what was discarded (per record kind: header, schema,
channel, chunk, message, attachment, metadata record, other record) and/or that the
input was truncated. Warnings about individual skipped records may be printed on stderr
as well; nothing else is checked on stderr, and stderr must not be colored when it is not
a terminal.

## Recovery semantics

The input is consumed once, front to back, as a stream. Do not require the input to be
seekable or memory-mappable, and do not hold the whole input (or all messages) in memory:
peak anonymous memory must stay bounded by chunk size, not input size. The verifier
recovers a 512 MiB file (uncompressed 1 MiB chunks) from a path and from a stdin pipe and
requires peak anonymous resident memory below 256 MiB; the base implementation fails this
for stdin.

1. **Framing.** Records are `opcode (1) + length (u64 LE) + body`. A declared length
   above 1 GiB is treated as corrupt framing: the scan stops there (lossy). If the input
   ends in the middle of a record (including inside a Data End or index record), the
   partial record is dropped and the scan stops (lossy, "truncated"). If the input ends
   exactly at a top-level record boundary, that is a clean end (not lossy): a file with
   no Data End, no summary, or no footer/end magic recovers cleanly.
2. **Where the scan ends.** Recovery covers the data section only. The first Data End or
   Footer record ends the scan; the summary section (repeated schemas/channels, indexes,
   statistics, summary offsets) is never read or copied — all of it is regenerated. Bytes
   after the end magic are never read.
3. **Header.** The output Header keeps the input Header's `profile` and `library`. If the
   input Header body cannot be parsed, it is discarded (lossy) and the output uses the
   defaults (empty profile).
4. **Unparsable top-level records.** A top-level record (other than a Chunk) whose body
   does not parse — for example a string whose declared length exceeds the record, or a
   Message shorter than its fixed header — is discarded (lossy) and the scan continues
   with the next record.
5. **Chunks.** Every chunk is decoded and its records are recovered individually and
   re-written; chunk bytes are never copied through. A chunk whose stored
   `uncompressed_crc` does not match its (decodable) content is **not** loss: all of its
   records are recovered and the output chunk gets a correct CRC. A chunk whose payload
   cannot be decoded — unsupported or unknown compression string, decompression failure,
   records not framed within the payload, or a record inside it that fails to parse —
   is discarded and the scan stops (lossy). Records already extracted from that chunk
   before the failure stay recovered. A chunk record whose body itself is unparsable falls
   under rule 4.
6. **Schemas, channels, messages.** Schemas and channels are re-registered with their
   original ids (the output must use the same ids). A repeated definition with identical
   content is fine; a channel referencing a nonzero schema id that has not been recovered
   is discarded (lossy); a message whose channel is not (yet) known is discarded (lossy).
   Recovered messages keep channel id, sequence, log time, publish time and data, in input
   order. Schemas and channels only ever seen after their dependents are not required to
   be rescued.
7. **Attachments and metadata** are copied with all fields (attachment: log time, create
   time, name, media type, data; metadata: name and map).
8. **Other records.** Message Index, Chunk Index, Attachment Index, Metadata Index,
   Statistics and Summary Offset records in the data section are ignored (regenerated).
   Records with an unknown or private opcode, at top level or inside a chunk, are skipped
   and are **not** loss.
9. **Always-valid output.** The output is a complete, spec-conformant MCAP: header,
   chunked data section with message indexes, Data End, summary (schemas, channels,
   statistics, chunk/attachment/metadata indexes), summary offsets, footer, end magic,
   with correct CRCs everywhere. It must be accepted by strict readers and by the Go
   CLI's `mcap doctor`, whether written to a file or to a non-seekable stdout pipe.
10. **Nothing recovered.** If not even one complete top-level record can be read, exit 1.
    If the header was read but nothing else, exit 0 with an empty (but valid) output.

## Output compression

- `--compression none|zstd|lz4`: every output chunk uses that codec.
- `--compression preserve` (default): the codec is chosen from the first
  codec-determining record in stream order. A Chunk determines it: the output reuses that
  chunk's compression (`""`/none, `zstd` or `lz4`; anything else falls back to
  uncompressed). A Message, an Attachment, or an unknown/private record reached before
  any chunk determines uncompressed output (an unchunked input stays uncompressed;
  attachments carry no codec signal and may be large, so they must not be buffered to
  wait for a chunk). Schema, Channel and Metadata records carry no signal and do not
  decide; they may be held until the codec is known and must still land in the output.
  When nothing determines a codec (no chunks and no messages) the output is uncompressed.

## Intentional divergences from the Go CLI (do not "fix" these toward Go)

- Go copies chunk bytes through verbatim by default (emitting bad-CRC or undecodable
  chunks) and exposes `--always-decode-chunk`; Rust always decodes and re-encodes, and
  has no such flag.
- Go defaults to `--compression zstd`; Rust defaults to `preserve`.
- Go exits 0 once recovery starts, even on truncated input; Rust uses exit 3 for lossy
  recovery and 1 when nothing was recovered.
- Go's summary line is `Recovered %d messages, %d attachments, and %d metadata records.`
  with no singular forms; Rust pluralizes naively as specified above.

Everything else about *what* is recovered must match Go's `recover -a` (decode chunks):
the same schemas, channels, messages (content and order), attachments and metadata for
valid inputs and for inputs truncated at any offset.

## Scope and rules

- Edit code under `rust/cli/src` (and `rust/mcap/src` if you need library changes; the
  upstream `rust/mcap/tests` must keep passing). Only those two directories are
  collected for grading. Do not add dependencies or edit `Cargo.toml`, `Cargo.lock`,
  `build.rs` or anything under `tests/`; those are restored to their pristine state
  before grading. Keep the other CLI commands working: `cargo test -p mcap-cli` must pass
  (update the existing unit tests that construct `RecoverCommand` as needed). Updating
  `rust/cli/README.md` is welcome but not graded.
- Useful local data: `tests/conformance/data/**/*.mcap` (416 small spec-conformance
  files with their expected record listings in the sibling `.json`),
  `rust/mcap/tests/data/*.mcap` (multi-chunk zstd and uncompressed files, and
  `chunk_not_closed.mcap` with an absurd chunk length), `testdata/mcap/demo.mcap`. You can
  build corrupted inputs yourself with `truncate`, `dd`, or a short script.
- The grader builds your sources offline in a clean tree, runs `cargo test -p mcap`
  (upstream integration tests) and `cargo test -p mcap-cli`, then runs the differential
  harness: your `mcap recover` versus the Go CLI on every corpus file, on files
  truncated at hundreds of offsets, files with corrupted chunk CRCs, undecodable chunks,
  unparsable or removed records, unknown opcodes, missing summary/footer/Data End, stdin
  and stdout modes, a loopback HTTP server, and the 512 MiB memory test. Outputs are
  compared record by record with the Go reader and validated with Go `mcap doctor`.
  Your binary must not invoke any other program; the grader rejects sources that
  reference external executables.
