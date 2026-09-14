# Consistent DiskCache snapshots and restore

DiskCache stores a database and, for larger values, separate payload files. Copying the live directory can combine different moments in time. Implement an offline snapshot format and atomic restore in the supplied DiskCache checkout at `/workspace/repo`.

Add `Cache.snapshot(destination, *, progress=None)` and `FanoutCache.snapshot(destination, *, progress=None)`, plus `verify_snapshot(path)` and `restore_snapshot(path, destination, *, progress=None)` in `diskcache.snapshot`. Snapshot returns the manifest described below. Verification returns the validated manifest. Restore returns an open `Cache` or `FanoutCache` configured for the captured kind, disk codec, and shard count. All functions accept strings and path-like objects. Raise `ValueError` for invalid snapshots or invalid destinations, and preserve callback exceptions.

The snapshot must represent one consistent state of all participating shards, including concurrent clients using ordinary writes and `FanoutCache.transact()`. Preserve serialized keys and values, file-backed values, stored absolute expiry times, tags, eviction settings, and database ordering metadata. Expired entries must not regain lifetime after restoration. Capture both built-in `Disk` and `JSONDisk`, including their settings; reject custom disk subclasses explicitly. Existing APIs keep their behavior. Do not convert the snapshot by iterating through public `get`/`set` operations: that loses metadata and can execute deserialization.

A Cache snapshot includes its own database and referenced payload files. A Fanout snapshot includes exactly its numbered hash shards; independently named `cache()`, `deque()`, and `index()` stores are outside this feature's scope. Unreferenced payload files, journals, and unrelated files are not snapshot members. Snapshotting from inside a transaction owned by the calling thread on any participating shard must raise `ValueError` before publishing anything. Snapshotting other threads' transactions should wait for a consistent committed state.

Use a directory containing `manifest.json` and `data/`. The UTF-8 JSON manifest is version 1 and contains:

- `format_version`: integer `1`;
- `kind`: `cache` or `fanout`;
- `shards`: positive integer (`1` for a Cache);
- `disk`: `Disk` or `JSONDisk`;
- `files`: mapping of POSIX relative paths under `data/` to objects with `sha256` (lowercase hex) and `size` (bytes).

For Cache, its native layout starts at `data/cache.db`; Fanout uses `data/000/cache.db`, `data/001/cache.db`, etc., retaining DiskCache's minimum three-digit numbering. Database files must be self-contained, without WAL recovery. Additional top-level manifest fields are permitted. Consumers must not assume private temporary filenames or a specific JSON whitespace/order.

`verify_snapshot` is read-only and checks supported manifest fields, exact file inventory, byte sizes and hashes, SQLite integrity, and the correspondence between each database's referenced payloads and included files. Reject missing or extra files, corrupt databases even if hashes were recomputed, absolute paths, traversal, symlinks, malformed layouts, and absent payload references. Verification must not unpickle values, instantiate arbitrary manifest-named classes, or open a writable database. Hashes detect accidental changes, not malicious rewriting of an entire self-consistent snapshot; no signature/authenticity feature is required.

Both creation and restore publish a complete destination directory atomically. The destination must not exist (even as an empty directory or dangling symlink), and creation destinations must be outside the source cache. Restore destinations must also be outside the snapshot. On validation, copy, or callback failure, an existing destination is untouched and a previously absent destination remains absent. If a process is killed before publication, a later attempt using that destination can succeed. Orphaned staging directories may remain after an uncatchable kill. Flush staged files and directory entries before publication, and flush the parent directory afterward. Power-loss simulation is not required, but do not claim atomic durability without these flushes.

Call `progress(event)` after each member file has been copied, before destination publication. `event` is a dict with `phase` equal to `copy` and `path` equal to its manifest-relative member path. Each member produces one event on a successful operation; order is unspecified. Callbacks can report progress or cancel by raising. Do not emit a successful return before verification and publication have completed. Restore validates first, stages its own copy, checks that staged bytes still match the manifest, and publishes only then; concurrent modification of an input snapshot must not silently produce corrupted restored data.

Provide a CLI:

```
python -m diskcache.snapshot create SOURCE DESTINATION [--shards N] [--disk Disk|JSONDisk]
python -m diskcache.snapshot verify SNAPSHOT
python -m diskcache.snapshot restore SNAPSHOT DESTINATION
```

`create` opens a Cache unless `--shards` is supplied, in which case it opens a FanoutCache with that count. The disk option defaults to `Disk`. CLI commands print the manifest as JSON on success and exit nonzero for failures. `restore` closes the returned cache before exit. CLI users supply the source's actual codec and topology; topology inference from arbitrary directories is not required.

Implement the feature in the existing package and add useful upstream-style tests. The grader checks public behavior, concurrency and process interruption, and existing Cache/Fanout/Disk behavior. No network or external service is required.

Evaluation uses a fresh offline container with the pinned dependencies. Only `/workspace/repo/diskcache/` is transferred as the submitted implementation. Keep runtime feature code inside that package; upstream tests and documentation may also be edited for development but do not replace the independent verifier.
