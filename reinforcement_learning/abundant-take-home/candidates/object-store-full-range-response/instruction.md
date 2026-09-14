The checkout at `/workspace/repo` is the Rust `object_store` library. Fix its HTTP store's handling of whole-object range requests.

A client can request all bytes of a nonempty object using `GetOptions.range`, but some HTTP servers return `200 OK` with the entire representation instead of `206 Partial Content`. This occurs when reading small Parquet files served by miniserve. The HTTP store currently rejects such responses even though they contain exactly the bytes requested.

Implement the following behavior using the existing public APIs:

- A `200 OK` response with a valid `Content-Length` is acceptable when resolving the requested `GetRange` against that length yields the entire nonempty object. Support bounded ranges, including ends beyond the object which the existing API clamps; `Offset(0)`; and suffix requests at least as large as the object.
- In this accepted case, return the entire representation through both streaming and collected-byte retrieval, set `GetResult.range` to `0..object_size`, and report the full size in `ObjectMeta`. Preserve location, ETag, and response attributes. A stray `Content-Range` on a 200 response, including a malformed or contradictory one, must not override the complete representation's length or offsets.
- Continue rejecting a 200 response that ignored a request for a proper subrange. A misleading `Content-Range` cannot turn a 200 full representation into a valid partial response. Do not silently return extra bytes or slice an ignored subrange locally.
- Preserve ordinary 206 range validation, correct total-size metadata, non-ranged GET, and HEAD behavior. Keep existing range validity rules; this task adds no special support for empty objects or invalid/empty ranges. A 200 without a usable complete-representation length need not be accepted.
- Accepting response headers must not turn an incomplete body into success. Preserve body error handling and ETag-based retries: after consuming a prefix of an accepted 200, resume using the outstanding range from a valid 206 without duplicating bytes. If no data has been consumed, a retry that requests the whole object may itself be answered with an acceptable 200. A retry that ignores a proper subrange or changes the ETag must still fail.

Keep the change focused on the HTTP backend and the implementation it needs. No cloud credentials or external server are required. A local HTTP fixture suffices for development. Do not change public APIs, dependency versions, or unrelated store behavior.

Rust 1.98.1 is installed and selected through `RUSTUP_TOOLCHAIN`; dependencies are locked and cached. Run upstream tests with, for example, `cargo test --offline --locked --features http --lib --test get_range_file`. Use the existing manifest and lockfile.

Evaluation uses a fresh offline container with independent HTTP tests and the pinned dependencies. Only `/workspace/repo/src/` is transferred as the submitted implementation. Put implementation changes there. Manifests, dependency locks, build configuration, and independent verifier tests come from the pristine evaluation environment. Upstream tests and documentation may be edited for local development but do not replace the independent tests.
