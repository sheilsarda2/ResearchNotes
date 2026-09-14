# Automatic Dask-to-Zarr chunk alignment for `to_zarr`

The checkout at `/workspace/repo` is pydata/xarray at commit `d21d79e` (June 2025) with zarr-python 3.0.8, dask 2025.5.1, numpy 2.2.6 and pandas 2.2.3 installed. `Dataset.to_zarr` and `DataArray.to_zarr` write Dask-backed variables block by block. When a Dask chunk boundary falls inside a Zarr chunk, two Dask tasks write the same Zarr chunk and, run in parallel, corrupt it. Today `safe_chunks=True` (the default) detects this and raises, and users are told to rechunk by hand; `safe_chunks=False` writes anyway and risks silent data loss (issues #9914 and #10501). Add an opt-in mode in which xarray rechunks the Dask array itself so that the write is safe, and move the chunk-grid logic into a reusable module.

## Public interface

1. `xarray.Dataset.to_zarr`, `xarray.DataArray.to_zarr` and `xarray.backends.api.to_zarr` accept a new keyword-only argument `align_chunks: bool = False`, placed after `safe_chunks`, on every overload. The Dataset and DataArray methods must forward it to the backend writer; a value passed to `Dataset.to_zarr` must reach the Zarr store exactly as a value passed to `DataArray.to_zarr` does. Document the argument in the numpydoc parameter lists next to `safe_chunks`.

2. The flag has to reach the Zarr store object that performs the write; `xarray.backends.zarr.ZarrStore.open_group` and `ZarrStore.open_store` accepting `align_chunks: bool = False` after `safe_chunks` is the natural route. The tests only exercise it through the three `to_zarr` entry points, so the internal route is yours to choose, as long as the existing `extract_zarr_variable_encoding(variable, raise_on_invalid=..., name=..., zarr_format=...)` call form keeps working.

3. A new module `xarray.backends.chunks` exposes the grid logic so other backends can reuse it:

   - `build_grid_chunks(size: int, chunk_size: int, region: slice | None = None) -> tuple[int, ...]`
     The Zarr chunk grid of one dimension, restricted to a region. `size` is the number of elements being written along the dimension, `chunk_size` the Zarr chunk size, and `region.start` (`None` and a `None` region both mean 0) the offset of the first written element in the store; `region.stop` is not used. The result is the sequence of grid pieces the written elements fall into: the first piece is `min(size, chunk_size - start % chunk_size)`, every interior piece is exactly `chunk_size`, and a final piece holds any remainder. The pieces sum to `size`. Examples: `build_grid_chunks(10, chunk_size=3, region=slice(1, 11)) == (2, 3, 3, 2)`; with `region=None`, `slice(None, None)`, `slice(0, None)` or `slice(None, 10)` the result is `(3, 3, 3, 1)`; `build_grid_chunks(2, 10, slice(0, 3)) == (2,)`; `build_grid_chunks(4, 10, slice(7, 10)) == (3, 1)`.

   - `align_nd_chunks(nd_v_chunks: tuple[tuple[int, ...], ...], nd_backend_chunks: tuple[tuple[int, ...], ...]) -> tuple[tuple[int, ...], ...]`
     Given, per dimension, the current Dask chunks and the backend grid pieces (as produced by `build_grid_chunks`: uniform interior pieces, borders possibly partial), return the aligned Dask chunks for each dimension. Raise `ValueError` if the two tuples have different lengths, if a dimension's chunks and grid pieces do not sum to the same total, or if the interior grid pieces of a dimension are not all equal.

   - `grid_rechunk(v: Variable, enc_chunks: tuple[int, ...], region: tuple[slice, ...]) -> Variable`
     Build the grid for every dimension of `v` with `build_grid_chunks(v.shape[i], chunk_size=enc_chunks[i], region=region[i])`, align `v.chunks` against it with `align_nd_chunks`, and return `v.chunk(...)` with the aligned chunks. A variable without chunks (`v.chunks is None`) is returned unchanged. Values are never modified.

   - `validate_grid_chunks_alignment(nd_v_chunks, enc_chunks, backend_shape, region, allow_partial_chunks, name)`
     The existing `safe_chunks` check expressed against the same grid (`allow_partial_chunks` is true unless the store mode is `"r+"`; `backend_shape` is the on-disk shape when the array already exists, otherwise the variable shape). It is a no-op when `nd_v_chunks is None` and otherwise raises `ValueError` as described under "Error contract". It is exercised only through `to_zarr(safe_chunks=True)`.

## Alignment contract

Work per dimension, in store coordinates: the written elements occupy `[start, start + size)` where `start` is the region offset, and Zarr chunk boundaries sit at every multiple of `chunk_size`. Boundaries inside the written range are called grid boundaries; the region start and the region end are always acceptable chunk edges. The aligned chunks must satisfy all of:

- they sum to `size` and are all positive;
- every boundary between two consecutive aligned chunks is a grid boundary, so no Zarr chunk is ever covered by two Dask chunks (partial border chunks belong to exactly one Dask chunk);
- no aligned chunk is larger than `max(chunk_size, largest original chunk)` on that dimension, so memory use does not grow beyond the larger of the two grids;
- an original chunking that already satisfies the alignment property is returned unchanged;
- the original chunking is otherwise preserved as far as possible, greedily from left to right: keep an original chunk whose end lands on a grid boundary or on the region end; otherwise merge it with the following original chunk(s) until a grid boundary or the region end is reached; whenever the merged piece would exceed the maximum above, close it at the largest grid boundary that keeps it within the maximum and continue with the remainder as the start of the next chunk.

Worked examples with `chunk_size=3` and `region=slice(2, 14)` (grid boundaries at offsets 1, 4, 7, 10 within the region): `(6, 6) -> (4, 6, 2)`, `(1, 3, 2, 6) -> (1, 3, 6, 2)`, `(2, 2, 2, 6) -> (4, 6, 2)`, `(3, 1, 3, 5) -> (4, 3, 5)`. With `chunk_size=4, region=slice(1, 13)`: `(1, 1, 1, 4, 3, 2) -> (3, 4, 4, 1)`. With `chunk_size=5, region=slice(4, 16)`: `(5, 7) -> (6, 6)`. With `chunk_size=6, region=slice(0, 13)`: `(6, 7)` and `(6, 6, 1)` are already aligned and unchanged. Dimensions are independent: a 2-D variable aligns each axis separately. When the whole written range lies inside a single Zarr chunk, or the variable has a single chunk on that dimension, the result is one chunk.

`align_nd_chunks` applies the same contract to explicit grid pieces: `align_nd_chunks(((2, 2, 2, 2),), ((3, 3, 2),)) == ((3, 3, 2),)` and `align_nd_chunks(((2, 4), (2, 3)), ((2, 2, 2), (3, 2))) == ((2, 4), (3, 2))`.

## Behaviour of `to_zarr(align_chunks=True)`

- Applies to every Dask-backed variable whose target Zarr chunk shape is known as a tuple, whether that shape comes from `encoding["chunks"]` passed to `to_zarr`, from the variable's own `.encoding`, or from an existing Zarr array being appended to or written into with `region`. The alignment uses the region the variable is written to (region offsets, `region="auto"` resolution and the offset introduced by `append_dim` included). NumPy-backed variables are unaffected.
- Alignment happens before the data is written and before any safe-chunks validation. `align_chunks=True` disables the `safe_chunks` validation for that call (the alignment makes it redundant), so `safe_chunks=True, align_chunks=True` must not raise for misaligned input.
- The Zarr array on disk gets the requested chunk shape, not the Dask chunk shape, and its data equals what an eager (NumPy) write of the same values would produce. In a region or append write, data outside the written region is untouched and the array shape and chunk grid are preserved. Each Zarr chunk is written by exactly one task: with a synchronous Dask scheduler and an instrumented zarr store, no chunk key is set more than once per `to_zarr` call.
- Works for `mode="w"`, `mode="a"`, `region=...` (explicit slices and `"auto"`), `append_dim=...`, Datasets with several variables of different dimensionality, and Zarr formats 2 and 3.
- `align_chunks=False` (the default) leaves all existing behaviour unchanged, including the case `safe_chunks=False`, where the caller's chunking is written as-is.
- Zarr synchronizers are out of scope, and the option does not protect against several uncoordinated processes writing the same array; say so in the docstring.

## Error contract

With `safe_chunks=True` and `align_chunks=False`, a Dask chunking that would make two Dask chunks touch the same Zarr chunk raises `ValueError`. The message must start with `Specified Zarr chunks encoding['chunks']=<chunks!r> for variable named <name!r> would overlap multiple Dask chunks` (capitalised `Zarr` and `Dask`), identify the offending Dask chunk position(s) and axis, mention the region, and list the remedies: rechunk with `chunk()`, modify or delete `encoding['chunks']`, set `safe_chunks=False`, or enable automatic alignment with `align_chunks=True` (that exact `align_chunks=True` token must appear). The existing rules for what counts as misaligned are unchanged: interior Dask chunks must be multiples of the Zarr chunk size; the first Dask chunk must end on a Zarr boundary given the region offset (partial first chunk allowed unless the mode is `"r+"`); and in `"r+"` mode the last chunk must also be aligned unless it is the array's final partial chunk. Encoding chunks that do not match the variable's number of dimensions are still discarded, and non-integer chunk sizes still raise `TypeError`, as before.

## Environment and scope

zarr-python 3.0.8 is installed and you may use it directly to inspect stores you write. The repository test suite runs with `python -m pytest xarray/tests/test_backends.py -k zarr` and `xarray/tests/test_backends_chunks.py` if you add it (all test extras, including `pytest-mypy-plugins` needed by the repo's `addopts`, are installed). Keep the implementation inside `/workspace/repo/xarray/`; only that directory is transferred for evaluation, `xarray/tests/` is replaced by the upstream copy, and nothing outside the package (root `conftest.py`, `pyproject.toml`, docs) is used. Do not add `conftest.py`, `.pth` or compiled files inside the package. Existing zarr backend behaviour that is not described here (encoding extraction, fill values, consolidated metadata, `chunks="auto"` reading) must keep passing the upstream `test_backends.py` zarr tests.
