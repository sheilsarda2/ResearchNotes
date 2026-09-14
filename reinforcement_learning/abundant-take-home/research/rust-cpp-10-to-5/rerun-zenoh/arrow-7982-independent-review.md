# Independent review: Arrow Rust dictionary value metadata (#7982)

September 13, 2026. Read-only review of the other research lane, its pinned source, probe and results; no independent re-execution or implementation. Recommendation: **retain for the final five if its proposed task explicitly covers Rust representation and C Data schema preservation, and resolves source-compatibility expectations before construction. Exclude IPC format work.** It is more substantial naturally than the two small routing/export repairs, but no difficulty or agent-step measurement exists.

## What is established

[#7982](https://github.com/apache/arrow-rs/issues/7982) is an actual library implementer's reproduction using arro3, GeoArrow, nanoarrow and PyArrow. A dictionary whose values carry `geoarrow.wkb` extension metadata loses that metadata through the Rust type representation. The reporter mentions automatically dictionary-encoded Parquet output as a practical trigger. This is one affected implementer and a concrete interop scenario, not a quantified production deployment or proof of widespread demand.

The [current-main probe](../rust-other/probe/src/bin/arrow_metadata.rs) is valid for the C Data schema layer. It constructs its own `FFI_ArrowSchema` before invoking unsafe `with_metadata`, meeting that method's ownership precondition. Ordinary binary field metadata survives the control. Dictionary-value metadata becomes `{}` through `FFI_ArrowSchema → Field → FFI_ArrowSchema`; the saved run exits 101 at the intended assertion. It exercises real arrow-schema conversion code and no IPC serialization.

At source pin `4cd8be954f6bc6b6dd265140207365b59a9900ec`, `DataType::try_from` converts `dictionary` recursively to a **DataType**, not a Field, and `DataType::Dictionary(Box<DataType>, Box<DataType>)` has nowhere to retain value-field metadata. Field conversion retains only the parent metadata. Export reconstructs the child from the metadata-free value datatype. This directly explains the measured loss.

## Contract distinction matters

The [C Data specification](https://arrow.apache.org/docs/format/CDataInterface.html#dictionary-encoded-arrays) represents dictionary values with a separate `ArrowSchema`, so parent and value metadata have distinct locations. Its extension section specifies the familiar metadata keys. The spec also explicitly permits consumers to ignore metadata; therefore describe this as requested metadata-preserving interoperability, **not mandatory format compliance**.

The IPC flatbuffer ambiguity is separate. [Arrow #49704](https://github.com/apache/arrow/issues/49704) later clarifies that Dictionary-of-Extension works through C Data but does not roundtrip through IPC, because IPC conflates it with Extension-of-Dictionary. Do not rely on the earlier April 7 #7982 comment's description of C++ fallback as proof of complete IPC support. The [format discussion](https://lists.apache.org/thread/4xj0yrl2vn0lbrv39p3dvd28okvkjxyz) proposes dictionary value metadata in the encoding table, and the author [explicitly claims intended implementation work](https://github.com/apache/arrow/issues/49704#issuecomment-4686991120). A task extending IPC schema is therefore in public-work overlap. The existing C Data layout can represent the distinction already and is independently actionable.

## Current probe is necessary, not a complete task specification

A Field-only metadata side channel could pass the current probe while leaving the original bare `DataType` / `arro3.DataType.dictionary` problem unsolved. The task must cover public Rust representation of dictionary value metadata, then conversion of that representation through C Data, including a cloned standalone dictionary datatype. Otherwise label it a narrower Field roundtrip repair and do not claim the issue is solved.

Changing `Dictionary`'s second payload from `Box<DataType>` to `FieldRef` would break current construction and pattern-match sites. Adding another enum variant also affects exhaustive matching because DataType is not `#[non_exhaustive]`. A task cannot fairly demand a representation change and silently insist every external constructor and exhaustive match keeps compiling. Decide and disclose the allowed API change, or provide a compatibility design that genuinely preserves the requested information. This is a construction/design decision, not a reason to manufacture unrelated functionality.

Useful acceptance boundaries:

- Preserve parent metadata and dictionary-value metadata separately, including the same key with different values at both levels. Do not move value extension tags onto the parent.
- Preserve arbitrary supported metadata, not only the two GeoArrow keys; use a second extension name so hard-coding fails.
- Retain the represented value metadata through datatype/field clones and C Data export after releasing the original schema. Avoid process-global pointer caches or source-lifetime dependence.
- Keep metadata-free dictionaries, signed/unsigned index types, field flags, nesting within list/struct, and ordinary scalar extensions working.
- Exercise the chosen representation's logical equality/hash/clone behavior and impacted array/schema construction paths. Avoid demanding deep arbitrary dictionary-of-dictionary IPC support; [#10230](https://github.com/apache/arrow-rs/pull/10230) explicitly rejects that IPC case.
- Define the behavior when a metadata-bearing dictionary reaches existing IPC APIs. Preserve existing supported cases; do not silently impose a new wire format or registry heuristic.

These are proposed checks, not claims that the current one-fixture probe has tested them.

## Prior art and public-work audit

I inspected the other lane's issue/timeline and PR search evidence, then independently saved additional all-state searches in this directory's `sources/arrow-review-*.json`. Queries include dictionary with field metadata, FieldRef, extension:name, C Data, and all 128 dictionary-metadata matches. No exact arrow-rs representation/C Data implementation or explicit arrow-rs work claim was identified. The two bare-number #7982 search matches are unrelated benchmark/kernel PRs, not linked fixes.

Relevant actual diffs inspected:

| PR | Actual scope | Why it does not resolve this reproduction |
|---|---|---|
| [#10515](https://github.com/apache/arrow-rs/pull/10515) | Import dictionary-ordered flag into Field | Preserves parent flag, not dictionary-value metadata. |
| [#10811](https://github.com/apache/arrow-rs/pull/10811) | Integration JSON field metadata | Preserves outer field metadata only; no dictionary datatype representation change. |
| [#10902](https://github.com/apache/arrow-rs/pull/10902), open | Configurable semantic equality for nested datatypes | Compares existing representation; does not add value metadata storage. |
| [#9011](https://github.com/apache/arrow-rs/pull/9011) | Parquet dictionary page access | Parquet files only, no arrow-schema representation changes. |
| [#10810](https://github.com/apache/arrow-rs/pull/10810) and [#11071](https://github.com/apache/arrow-rs/pull/11071), latter open | Dictionary-encoded Variant metadata/value normalization | `parquet-variant-compute` implementation, not extension Field metadata. |

The nanoarrow [#861](https://github.com/apache/arrow-nanoarrow/pull/861) implementation concerns another project's IPC decoder. It is useful prior art, but it is not an available arrow-rs fix for the reproduced representation loss. The explicit standards work above still limits any expanded IPC task.

Absence findings are bounded to these public searches and supplied evidence; they do not establish that no private or unreferenced fork work exists.

## Relative ranking

For easy-to-specify Harbor behavior: Rerun selected export and Zenoh completeness are the clearest. Arrow is a defensible next choice with representation/API decisions recorded. Zenoh reconnect has stronger repeated current execution than many reserves, but its missing readiness promise makes it a weaker immediate task contract than completeness; do not infer that `.await` on declaration must wait for the network.
