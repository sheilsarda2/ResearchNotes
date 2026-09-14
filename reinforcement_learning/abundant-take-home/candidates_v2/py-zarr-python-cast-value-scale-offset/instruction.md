# Add the `cast_value` and `scale_offset` array-to-array codecs to zarr-python

You are working in `/workspace/repo`, a checkout of **zarr-python** (the `zarr` package, Zarr v3
implementation). The repository is installed in editable mode; every dependency, including the
test dependencies and the optional Rust-backed kernel package `cast-value-rs` (importable as
`cast_value_rs`), is already present. No network access is needed and none should be assumed.

zarr-python ships the legacy `numcodecs.fixedscaleoffset` codec, which has no specification and
does not generalise to other implementations. The Zarr extensions registry now defines two
spec'd, composable replacements: **`scale_offset`** (affine transform) and **`cast_value`**
(numeric type conversion with explicit rounding, out-of-range and special-value rules). Your job
is to implement both as first-class Zarr v3 `array -> array` codecs in this repository so that

* arrays created with them from Python round-trip, and
* arrays written by *another* Zarr implementation that follows the same specification open,
  decode to identical bytes, and re-encode to byte-identical chunks.

The verifier compares your `cast_value` codec against an independent implementation written in
Rust (the `zarrs` library) over a large matrix of data types, rounding modes, out-of-range
policies and scalar maps, byte for byte in both directions. That reference is only present in
the verifier image; you cannot run it. The `scale_offset` codec is checked against exact
(arbitrary-precision) arithmetic. Hidden unit tests derived from the project's own test suite and
the existing upstream codec test suites (which must keep passing) complete the check.

Only `/workspace/repo/src/zarr` is collected from your container. The verifier rebuilds a
pristine tree, overlays your `src/zarr`, restores `src/zarr/testing/` and the whole `tests/`
directory from the pristine copy, and then runs its own tests. Anything you put under `tests/`
is therefore irrelevant to scoring, and changes to `src/zarr/testing/` are discarded. Package
code must not spawn processes, register `atexit` hooks, or reach for pytest internals.

Requirements below carry identifiers (`G-*`, `CV-*`, `SO-*`); every hidden assertion traces to
one of them.

---

## G. General

**G-1 Integration.** Both codecs subclass `zarr.abc.codec.ArrayArrayCodec` (frozen dataclasses,
like the existing codecs), implement the abstract `_encode_single` / `_decode_single`
coroutines, and should also provide the synchronous `_encode_sync` / `_decode_sync` methods so
the synchronous codec pipeline can use them. They must work as `filters=[...]` entries of
`zarr.create_array(...)` for Zarr v3 arrays, in any chain position that the Zarr v3 codec
rules permit (they are `array -> array` codecs, so before the `bytes` serializer), and they must
compose with each other (`scale_offset` followed by `cast_value`). Failures inside encode or
decode must propagate as exceptions out of the array write (`arr[...] = ...`) or read
(`arr[...]`) that triggered them; they must not be swallowed.

**G-2 Registration and exports.** `zarr.codecs.cast_value.CastValue` and
`zarr.codecs.scale_offset.ScaleOffset` are importable from those module paths, are re-exported
from `zarr.codecs` (and listed in its `__all__`), and are registered in the codec registry under
the names `"cast_value"` and `"scale_offset"` respectively, so that array metadata naming those
codecs resolves to your classes when an array is opened.

**G-3 Nothing else regresses.** All existing codecs, including `numcodecs.fixedscaleoffset`, the
sharding codec, the sync codec pipeline and the codec entry-point machinery keep their current
behaviour. The upstream test suites under `tests/test_codecs/`, `tests/test_codec_entrypoints.py`,
`tests/test_codec_pipeline.py` and `tests/test_sync_codec_pipeline.py` must pass unchanged
(the verifier runs them). Note that the project's pytest configuration turns warnings into
errors.

**G-4 Housekeeping (not scored).** A towncrier fragment under `changes/` and an optional
dependency extra in `pyproject.toml` for `cast-value-rs` are welcome but not evaluated.

---

## CV. The `cast_value` codec

Specification source: the Zarr extensions registry, `codecs/cast_value`. The normative content
is restated here.

**CV-1 Purpose.** `cast_value` converts the *numerical value* of each element to a new data type
during encoding and converts back during decoding. It never reinterprets bit patterns and
leaves shape and other array properties intact. The output of encoding is an array whose data
type is the configured target data type; the output of decoding has the array's own data type.

**CV-2 Constructor and attributes.**

```python
CastValue(
    *,
    data_type: str | ZDType,           # target data type: Zarr v3 name or a ZDType instance
    rounding: RoundingMode = "nearest-even",
    out_of_range: Literal["clamp", "wrap"] | None = None,
    scalar_map: ScalarMapJSON | ScalarMap | None = None,
)
```

All parameters are keyword-only. A string `data_type` is resolved with
`zarr.core.dtype.get_data_type_from_json(name, zarr_format=3)`; a `ZDType` is used as-is.
Instances expose:

* `dtype` — the resolved target `ZDType` (so `codec.dtype.to_native_dtype()` is the numpy dtype);
* `rounding` — the rounding mode string exactly as supplied;
* `out_of_range` — `"clamp"`, `"wrap"` or `None`, exactly as supplied;
* `scalar_map` — `None`, or the **normalized** scalar map (see CV-5);
* class attribute `is_fixed_size = True`.

Two instances compare equal when these four attributes are equal; in particular
`CastValue.from_dict(codec.to_dict()) == codec` holds for every valid codec.

**CV-3 Permitted target data types.** The target must be one of the real-number data types of the
specification: `int2 int4 int8 int16 int32 int64 uint2 uint4 uint8 uint16 uint32 uint64
float4_e2m1fn float6_e2m3fn float6_e3m2fn float8_e3m4 float8_e4m3 float8_e4m3b11fnuz
float8_e4m3fnuz float8_e5m2 float8_e5m2fnuz float8_e8m0fnu bfloat16 float16 float32 float64`
(compared by Zarr v3 name, i.e. `zdtype.to_json(zarr_format=3)`). Any other target (`bool`,
complex, strings, bytes, datetimes, ...) is rejected **at construction** with `ValueError` whose
message contains `Invalid target data type`. This repository currently resolves the eight
fixed-width integer names and `float16`/`float32`/`float64`; those eleven are what the verifier
exercises.

**CV-4 JSON form.** `to_dict()` returns

```python
{"name": "cast_value", "configuration": {"data_type": <target name>, ...}}
```

where `configuration` contains `data_type` (the Zarr v3 name string) always, `rounding` **only
if** it differs from `"nearest-even"`, `out_of_range` **only if** it is not `None`, and
`scalar_map` **only if** it is not `None`, in which case it is
`{"encode": [(in, out), ...], "decode": [(in, out), ...]}` holding one list of 2-tuples per
direction that is present in the normalized map, in insertion order (JSON serialization renders
the tuples as 2-element arrays). `from_dict(d)` accepts exactly that shape; `configuration` is
required for this codec; its keys are exactly the constructor's keyword arguments, and an
unknown key must raise an exception (readers must treat documents with additional keys as
invalid).

**CV-5 Scalar map.** In JSON, `scalar_map` is an object with optional `encode` and `decode`
members, each a list of `[input, output]` pairs. For `encode`, inputs are values of the array's
data type and outputs values of the target; for `decode` the roles are swapped. Scalars use the
Zarr v3 fill-value encoding of their data type: integers and finite floats as JSON numbers,
`NaN`, `+Infinity`/`Infinity`, `-Infinity` as those strings. The **normalized** in-memory form is a
dict of dicts, e.g. `{"encode": [("NaN", 0)]}` becomes `{"encode": {"NaN": 0}}`; values are kept
as given (no numeric conversion). Provide a module-level function
`zarr.codecs.cast_value.parse_scalar_map(obj)` that returns this normalized form, accepting
either the JSON form (lists of pairs) or an already-normalized form (dicts) for each direction,
and including only the directions that are present and not `None`. If an input value is repeated
in a direction, the first occurrence wins. During encoding, an element equal to an `encode`
input is replaced by the mapped output and bypasses every other rule; during decoding the same
holds for `decode`. The scalar map is evaluated **before** any representability check.

**CV-6 Validation at array creation / open (`validate(shape=, dtype=, chunk_grid=)`).** The
`dtype` argument is the array's data type.

* If it is not one of the CV-3 names (e.g. complex, bool, string dtypes): `ValueError` whose
  message contains `only supports integer and floating-point`.
* `out_of_range="wrap"` is only permitted when the **array's** data type is an integer type; a
  floating-point array combined with `"wrap"` raises `ValueError` whose message contains
  `only valid for integer`. (The specification additionally intends the *target* to be an
  integer type; that combination is not exercised.)
* Every scalar-map entry must be representable: `encode` inputs in the array dtype, `encode`
  outputs in the target dtype, `decode` inputs in the target dtype, `decode` outputs in the
  array dtype, where "representable" means `zdtype.from_json_scalar(value, zarr_format=3)`
  succeeds. Otherwise `ValueError` whose message contains
  `not representable in dtype <numpy dtype name>` naming the dtype of the offending side (for
  example `scalar_map encode key 'NaN' is not representable in dtype int32.`).

These checks run whenever an array with this codec is created or opened from metadata, so
invalid metadata written by anyone is rejected by `zarr.open_array` / `zarr.create_array`.

**CV-7 Element conversion procedure.** For every element, in this order:

1. If the scalar map for the current direction has an entry for the input value, emit the mapped
   output.
2. Else if the value is exactly representable in the output data type (numerical value only; for
   floats, payload preservation is not required), emit it unchanged.
3. Else apply `rounding` first and then, if the rounded value is outside the output type's
   range, the `out_of_range` policy. If no policy is configured and the value is out of range —
   or a NaN/infinity meets an output type without NaN/infinity and no scalar map entry covers it
   — the conversion **fails**: raise `ValueError` (from the write or read that triggered it).

Rounding modes (applied whenever the target cannot represent the value exactly, including
float-to-float narrowing and integer-to-float conversions that lose precision):

| `rounding` | meaning |
|---|---|
| `nearest-even` (default) | round to nearest, ties to even (IEEE 754 roundTiesToEven) |
| `towards-zero` | truncate |
| `towards-positive` | ceiling |
| `towards-negative` | floor |
| `nearest-away` | round to nearest, ties away from zero |

Out-of-range policies:

| `out_of_range` | meaning |
|---|---|
| `clamp` | clip to the target's minimum/maximum; for float targets, values beyond the finite range become ±Infinity |
| `wrap` | integer targets only: reduce modulo 2^N (N = bit width) with two's-complement semantics (e.g. `128 -> int8` gives `-128`, `-32769 -> int16` gives `32767`) |
| absent | out-of-range values are an error (`ValueError`) |

Rounding precedes the out-of-range rule (`127.5 -> int8` with `nearest-even` and `clamp` gives
`127`; with `wrap` gives `-128`). When both types have IEEE 754 semantics, NaN propagates
(unless mapped) and signed zero is preserved. Decoding applies the same procedure with the
target as input type and the array's dtype as output type, using the `decode` scalar map.
A conforming implementation is fully determined by these rules: for the same stored bytes and
metadata, your decoded values must equal the reference's, and for the same source values your
encoded chunk must be byte-identical to the reference's.

**CV-8 Fill value and chunk spec (`resolve_metadata(chunk_spec)`).** The resolved chunk spec
has the target dtype and a fill value obtained by casting the incoming fill value with the
*encode* procedure (including the `encode` scalar map), as a scalar of the target dtype. This
keeps partially written / unwritten chunks correct: reading an unwritten region returns the
array's fill value.

**CV-9 `compute_encoded_size(input_byte_length, chunk_spec)`.** Returns
`(input_byte_length // source_itemsize) * target_itemsize`, where `source_itemsize` is the item
size of `chunk_spec.dtype`. If the chunk dtype is not one of the CV-3 names (this covers
variable-length dtypes whose item size is 0): `ValueError` whose message contains
`fixed-size integer and floating-point data types`.

**CV-10 Interoperability.** Arrays whose `zarr.json` was produced by another implementation
(optional fields absent or present, scalar maps in JSON list form with `"NaN"`/`"Infinity"`
strings) must open, decode to the same bytes as the reference, and, when the same source values
are written into a metadata-only copy, produce a byte-identical chunk. `metadata.to_dict()` of
such an array must reproduce the codec configuration with the same meaning (defaults may be
omitted). Documents the specification calls invalid (unknown configuration keys, wrap on a
floating-point array, unsupported target type, unrepresentable scalar-map entries) must be
rejected with an exception at open.

**CV-11 Kernel.** You may implement the conversion yourself (numpy or otherwise) or use the
installed `cast_value_rs.cast_array(arr, *, target_dtype, rounding_mode, out_of_range_mode=None,
scalar_map_entries=None)`; `help(cast_value_rs.cast_array)` documents it. The `rounding_mode` and
`out_of_range_mode` strings are the same as this codec's, and `scalar_map_entries` takes numeric
keys/values (convert `"NaN"`-style strings to floats first). If you rely on it, raise `ImportError`
with an installation hint when it is missing, but do not make its absence affect metadata
parsing or validation.

---

## SO. The `scale_offset` codec

Specification source: the Zarr extensions registry, `codecs/scale_offset`.

**SO-1 Purpose.** Encoding computes `out = (in - offset) * scale`; decoding computes
`out = (in / scale) + offset`. The data type is unchanged. Together with `cast_value` this
supersedes `numcodecs.fixedscaleoffset`, which stays available.

**SO-2 Constructor and attributes.**

```python
ScaleOffset(*, offset: int | float | str = 0, scale: int | float | str = 1)
```

Keyword-only. `offset` and `scale` are stored exactly as supplied (an `int` stays an `int`, a
`float` a `float`, a `str` a `str`) and exposed as attributes `offset` and `scale`. A value of any
other type raises `TypeError` whose message contains `offset must be a number or string` or
`scale must be a number or string`. Class attribute `is_fixed_size = True`. Instances compare
equal when both attributes are equal; `ScaleOffset.from_dict(c.to_dict()) == c`.

**SO-3 JSON form.** If `offset == 0` and `scale == 1` (numeric comparison), `to_dict()` returns
exactly `{"name": "scale_offset"}` with **no** `configuration` key. Otherwise it returns
`{"name": "scale_offset", "configuration": {...}}` containing `offset` only if it differs from 0
and `scale` only if it differs from 1, each with the value as supplied. `from_dict` accepts a
document without `configuration` (both defaults), and one with either or both keys; unknown
keys must raise. The parameters are JSON-encoded scalars in the array dtype's fill-value
encoding, so strings such as `"NaN"` are legal spellings for float arrays.

**SO-4 Validation at creation / open.** With the array's data type `dtype`:

* not an integer or floating-point dtype (complex, bool, strings, ...): `ValueError` containing
  `only supports integer and floating-point`;
* `scale == 0`: `ValueError` containing `scale must be non-zero`;
* `offset` or `scale` not representable in the array dtype, i.e.
  `dtype.from_json_scalar(value, zarr_format=3)` fails (a float such as `1.5` or a string such as
  `"NaN"` on an integer array): `ValueError` containing `offset value <repr> is not representable`
  or `scale value <repr> is not representable`, where `<repr>` is Python's `repr()` of the
  supplied value (so `offset value 1.5 is not representable ...` and
  `offset value 'NaN' is not representable ...`).

**SO-5 Arithmetic semantics.** Both directions are computed with the arithmetic semantics of the
array's own data type, and the result has that same dtype. `offset` and `scale` are interpreted
through the dtype's JSON scalar decoding (so `0.1` on a `float32` array is the `float32` nearest to
0.1).

*Integers.* The mathematically exact result must be produced or the operation must fail; silent
wrap-around is forbidden. Encoding: if `(x - offset) * scale` is outside the dtype's range for any
element, raise `ValueError` containing `outside the range of dtype <numpy name>` (e.g. `... dtype
int8`, `... dtype uint8`, `... dtype uint64`). Decoding: if any stored element is not exactly
divisible by `scale`, raise `ValueError` containing `non-zero remainder`; if the exact quotient
plus `offset` is outside the range, raise `ValueError` containing `outside the range of dtype
<numpy name>`. Negative `scale` values are legal for signed dtypes. The full `uint64` range
(values above 2**63) must round-trip; unsigned underflow (`x < offset`) is an error.

*Floats.* IEEE arithmetic in the array's dtype: `NaN`, `+inf` and `-inf` propagate; there is no
range check. The computation must **not** widen the dtype (a `float32` array stays `float32`).

*Module-level helpers.* `zarr/codecs/scale_offset.py` exposes two pure functions used by the
codec and exercised directly:

```python
_encode(arr: np.ndarray, offset: np.generic, scale: np.generic) -> np.ndarray
_decode(arr: np.ndarray, offset: np.generic, scale: np.generic, *, scale_repr: object) -> np.ndarray
```

They implement the rules above for a numpy array and numpy-scalar parameters and return an
array of `arr.dtype`. If numpy type promotion would give the result a different dtype (for
example a `float32` array combined with `np.float64` scalars), they raise `ValueError` containing
`changed dtype from <in> to <out>` (e.g. `changed dtype from float32 to float64`) instead of
returning a widened array. `scale_repr` is the user-supplied `scale` value, used only in the
`non-zero remainder` error message.

**SO-6 Fill value and size.** `resolve_metadata` applies the encode formula to the fill value (dtype
unchanged), so unwritten chunks read back as the array's fill value and the encoded fill stays
aligned with the data for fill-value-aware codecs downstream. `compute_encoded_size(n, spec)`
returns `n` unchanged.

**SO-7 Composition.** The specification's worked examples must behave as written: a `uint16`
array with `ScaleOffset(offset=1000)` followed by `CastValue(data_type="uint8")` stores
`[1000, 1001, 1255]` as bytes `00 01 ff` and round-trips (a value of 1256 fails to encode); a
`float64` array with fill value `NaN`, `ScaleOffset(offset=-10, scale=0.1)` followed by
`CastValue(data_type="uint8", rounding="nearest-even", scalar_map={"encode": [("NaN", 0)],
"decode": [(0, "NaN")]})` stores `NaN` as `0`, decodes `0` back to `NaN`, and reads unwritten
chunks as `NaN`. `ScaleOffset(offset=0, scale=10)` followed by `CastValue(data_type="int16",
rounding="nearest-even", out_of_range="clamp")` reproduces `arange(100) * 0.1` to one decimal.

---

## Verification summary

| group | what | count |
|---|---|---|
| import | both classes import, are exported, and resolve in the registry | 1 |
| pr_tests | hidden unit tests (serialization, construction, validation, encode/decode, errors) | 63 |
| upstream | existing codec, pipeline and entry-point suites, unchanged | 505 |
| cast_value_matrix | zarr-python vs zarrs, byte-identical decode and encode, both directions, 11 dtypes x 11 dtypes x roundings x policies x scalar maps, including must-fail cases | 3790 |
| foreign_metadata | hand-written / zarrs-written arrays opened by your code; invalid documents rejected | 12 |
| scale_offset_reference | exact-arithmetic reference: encode bytes, decode from raw chunks, error messages, metadata JSON, fill values, spec composition examples | 100 |

Reward is 1 only if every group passes with exactly the listed number of cases (no skips). Error
types and message fragments named above are asserted verbatim.
