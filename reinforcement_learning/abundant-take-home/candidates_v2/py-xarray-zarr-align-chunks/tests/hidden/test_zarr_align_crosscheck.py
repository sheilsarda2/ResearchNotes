"""Independent cross-implementation checks for ``to_zarr(align_chunks=True)``.

Reference implementations used here are *not* xarray's reader:

* zarr-python (``zarr.open_group``) reads back every store directly and reports
  values, ``chunks``, ``shape``, ``dtype`` and dimension metadata.
* NumPy eager arrays are the value oracle: the expected on-disk content is
  computed by slicing in-memory arrays, never via ``xr.open_zarr``.
* An instrumented zarr store counts ``set`` calls per chunk key, which exposes
  whether two Dask tasks ever wrote the same Zarr chunk (the corruption the
  feature exists to prevent).

None of the cases below appears in the upstream PR tests, and no expected
value here was produced by the reference implementation of the feature.
"""

from __future__ import annotations

import itertools
import threading
from collections import Counter

import numpy as np
import pytest

import xarray as xr

dask = pytest.importorskip("dask")
zarr = pytest.importorskip("zarr")
pytestmark = pytest.mark.filterwarnings(
    "ignore:.*(Consolidated metadata|consolidated metadata).*",
    "ignore:.*The `compressor` argument.*",
)

from zarr.storage import MemoryStore  # noqa: E402


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
class CountingMemoryStore(MemoryStore):
    """MemoryStore that counts how many times each key is written."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_counts: Counter[str] = Counter()
        self._count_lock = threading.Lock()

    async def set(self, key, value, byte_range=None):  # type: ignore[override]
        with self._count_lock:
            self.set_counts[key] += 1
        return await super().set(key, value, byte_range=byte_range)

    async def set_if_not_exists(self, key, value):  # type: ignore[override]
        with self._count_lock:
            self.set_counts[key] += 1
        return await super().set_if_not_exists(key, value)

    def chunk_write_counts(self, array_name: str) -> dict[str, int]:
        return {
            k: v
            for k, v in self.set_counts.items()
            if k.startswith(f"{array_name}/") and not k.endswith((".json", ".zarray", ".zattrs", ".zgroup", ".zmetadata"))
        }


def open_array_direct(store, name: str):
    """Read an array with zarr-python only (no xarray)."""
    group = zarr.open_group(store, mode="r", use_consolidated=False)
    return group[name]


def dims_of(arr) -> tuple[str, ...]:
    """Dimension names as stored on disk (v2 attribute or v3 metadata)."""
    if arr.metadata.zarr_format == 2:
        return tuple(arr.attrs["_ARRAY_DIMENSIONS"])
    return tuple(arr.metadata.dimension_names)


def make_dataset(shape, chunks, offset=0, name="v", dims=None):
    dims = dims or tuple(f"d{i}" for i in range(len(shape)))
    data = (np.arange(np.prod(shape)) + offset).reshape(shape).astype("int64")
    coords = {d: np.arange(n) for d, n in zip(dims, shape, strict=True)}
    ds = xr.Dataset({name: (dims, data)}, coords=coords)
    return ds.chunk(dict(zip(dims, chunks, strict=True))), data


def store_for(zarr_format: int):
    return CountingMemoryStore(), {"zarr_format": zarr_format}


# --------------------------------------------------------------------------- #
# A + B: fresh writes with misaligned Dask chunks
# --------------------------------------------------------------------------- #
FRESH_CASES = [
    # (shape, dask chunks, encoding chunks)
    pytest.param((10,), ((3, 3, 3, 1),), (4,), id="1d-3s-into-4s"),
    pytest.param((13,), ((5, 5, 3),), (2,), id="1d-5s-into-2s"),
    pytest.param((7,), ((1,) * 7,), (3,), id="1d-ones-into-3s"),
    pytest.param((9,), ((2, 7),), (20,), id="1d-enc-bigger-than-array"),
    pytest.param((8, 9), ((3, 5), (4, 4, 1)), (5, 3), id="2d-mixed"),
    pytest.param((6, 6), ((1,) * 6, (2, 2, 2)), (4, 4), id="2d-ones-and-2s-into-4s"),
    pytest.param((5, 4, 6), ((2, 3), (1, 3), (4, 2)), (3, 2, 5), id="3d"),
]


@pytest.mark.parametrize("zarr_format", [2, 3], ids=["v2", "v3"])
@pytest.mark.parametrize("shape, dchunks, enc", FRESH_CASES)
def test_fresh_write_values_and_layout_via_zarr_python(shape, dchunks, enc, zarr_format):
    ds, expected = make_dataset(shape, dchunks)
    store, fmt = store_for(zarr_format)
    with dask.config.set(scheduler="synchronous"):
        ds.to_zarr(store, mode="w", align_chunks=True, encoding={"v": {"chunks": enc}}, **fmt)

    arr = open_array_direct(store, "v")
    # values are what an eager NumPy write would have produced
    np.testing.assert_array_equal(arr[:], expected)
    # the requested Zarr chunk grid is honoured exactly, not replaced by the Dask grid
    assert tuple(arr.chunks) == tuple(enc)
    assert arr.shape == shape
    assert arr.dtype == np.dtype("int64")
    assert dims_of(arr) == tuple(f"d{i}" for i in range(len(shape)))
    # every Zarr chunk was written by exactly one task
    counts = store.chunk_write_counts("v")
    assert counts, "no chunk keys were written"
    assert max(counts.values()) == 1, counts


@pytest.mark.parametrize("zarr_format", [2, 3], ids=["v2", "v3"])
@pytest.mark.parametrize("shape, dchunks, enc", FRESH_CASES)
def test_fresh_write_matches_eager_numpy_write(shape, dchunks, enc, zarr_format):
    ds, expected = make_dataset(shape, dchunks)
    lazy_store, fmt = store_for(zarr_format)
    eager_store, _ = store_for(zarr_format)
    with dask.config.set(scheduler="synchronous"):
        ds.to_zarr(lazy_store, mode="w", align_chunks=True, encoding={"v": {"chunks": enc}}, **fmt)
    ds.compute().to_zarr(eager_store, mode="w", encoding={"v": {"chunks": enc}}, **fmt)

    lazy = open_array_direct(lazy_store, "v")
    eager = open_array_direct(eager_store, "v")
    np.testing.assert_array_equal(lazy[:], eager[:])
    assert lazy.chunks == eager.chunks
    assert lazy.shape == eager.shape
    assert lazy.dtype == eager.dtype
    assert set(lazy_store.chunk_write_counts("v")) == set(eager_store.chunk_write_counts("v"))


def test_unsafe_unaligned_write_is_left_alone():
    """With align_chunks=False and safe_chunks=False the caller's chunking is
    written as-is; the instrumented store must observe overlapping writes.
    This is the negative control that shows the counter detects overlap."""
    ds, expected = make_dataset((10,), ((3, 3, 3, 1),))
    store, fmt = store_for(3)
    with dask.config.set(scheduler="synchronous"):
        ds.to_zarr(store, mode="w", safe_chunks=False, encoding={"v": {"chunks": (4,)}}, **fmt)
    counts = store.chunk_write_counts("v")
    assert max(counts.values()) >= 2, counts
    # (synchronous scheduler, so the values are still right)
    np.testing.assert_array_equal(open_array_direct(store, "v")[:], expected)


# --------------------------------------------------------------------------- #
# safe_chunks contract
# --------------------------------------------------------------------------- #
def test_safe_chunks_error_names_the_remedy():
    ds, _ = make_dataset((10,), ((3, 3, 3, 1),))
    store, fmt = store_for(3)
    with pytest.raises(ValueError, match=r"would overlap multiple Dask chunks") as info:
        ds.to_zarr(store, mode="w", encoding={"v": {"chunks": (4,)}}, **fmt)
    assert "align_chunks=True" in str(info.value)
    assert "Specified Zarr chunks" in str(info.value)
    assert "'v'" in str(info.value)


def test_align_chunks_overrides_safe_chunks():
    ds, expected = make_dataset((10,), ((3, 3, 3, 1),))
    store, fmt = store_for(3)
    ds.to_zarr(store, mode="w", safe_chunks=True, align_chunks=True, encoding={"v": {"chunks": (4,)}}, **fmt)
    np.testing.assert_array_equal(open_array_direct(store, "v")[:], expected)


def test_align_chunks_default_is_false_and_validation_unchanged():
    ds, _ = make_dataset((10,), ((3, 3, 3, 1),))
    store, fmt = store_for(3)
    with pytest.raises(ValueError, match=r"would overlap multiple Dask chunks"):
        ds.to_zarr(store, mode="w", encoding={"v": {"chunks": (4,)}}, **fmt)
    # aligned Dask chunks still pass without the option
    ok, expected = make_dataset((10,), ((4, 4, 2),))
    store2, _ = store_for(3)
    ok.to_zarr(store2, mode="w", encoding={"v": {"chunks": (4,)}}, **fmt)
    np.testing.assert_array_equal(open_array_direct(store2, "v")[:], expected)


# --------------------------------------------------------------------------- #
# G + H: region writes and appends, Dataset path, data outside region untouched
# --------------------------------------------------------------------------- #
REGION_CASES = [
    # (full shape, zarr chunks, region slices, dask chunks of the region piece)
    pytest.param((20,), (4,), (slice(3, 15),), ((5, 5, 2),), id="1d-offset-region"),
    pytest.param((20,), (4,), (slice(0, 20),), ((3,) * 6 + (2,),), id="1d-full-region-misaligned"),
    pytest.param((20,), (6,), (slice(7, 9),), ((1, 1),), id="1d-region-inside-one-chunk"),
    pytest.param((12, 10), (5, 4), (slice(2, 11), slice(1, 10)), ((4, 5), (2, 2, 5)), id="2d-offset-region"),
    pytest.param((9, 9), (3, 3), (slice(1, 9), slice(0, 4)), ((1,) * 8, (3, 1)), id="2d-ones"),
]


@pytest.mark.parametrize("zarr_format", [2, 3], ids=["v2", "v3"])
@pytest.mark.parametrize("shape, zchunks, region, dchunks", REGION_CASES)
def test_region_write_only_touches_region(shape, zchunks, region, dchunks, zarr_format):
    dims = tuple(f"d{i}" for i in range(len(shape)))
    base = np.zeros(shape, dtype="int64") - 1
    coords = {d: np.arange(n) for d, n in zip(dims, shape, strict=True)}
    full = xr.Dataset({"v": (dims, base)}, coords=coords)
    store, fmt = store_for(zarr_format)
    full.to_zarr(store, mode="w", encoding={"v": {"chunks": zchunks}}, **fmt)
    store.set_counts.clear()

    piece_shape = tuple(r.stop - r.start for r in region)
    piece_vals = (np.arange(np.prod(piece_shape)) + 100).reshape(piece_shape).astype("int64")
    piece = xr.Dataset(
        {"v": (dims, piece_vals)},
        coords={d: np.arange(r.start, r.stop) for d, r in zip(dims, region, strict=True)},
    ).chunk(dict(zip(dims, dchunks, strict=True)))

    with dask.config.set(scheduler="synchronous"):
        piece.to_zarr(store, region=dict(zip(dims, region, strict=True)), align_chunks=True)

    expected = base.copy()
    expected[tuple(region)] = piece_vals
    arr = open_array_direct(store, "v")
    np.testing.assert_array_equal(arr[:], expected)
    assert tuple(arr.chunks) == tuple(zchunks)
    counts = store.chunk_write_counts("v")
    assert counts and max(counts.values()) == 1, counts


@pytest.mark.parametrize("zarr_format", [2, 3], ids=["v2", "v3"])
def test_region_auto_dataset_with_mixed_variables(zarr_format):
    """The Dataset code path (not just DataArray) must honour align_chunks,
    including variables that do not span every dimension."""
    x = np.arange(12)
    y = np.arange(5)
    ds = xr.Dataset(
        {
            "a": (("x", "y"), np.zeros((12, 5), dtype="float64")),
            "b": ("x", np.zeros(12, dtype="float64")),
        },
        coords={"x": x, "y": y},
    )
    store, fmt = store_for(zarr_format)
    ds.to_zarr(store, mode="w", encoding={"a": {"chunks": (4, 5)}, "b": {"chunks": (4,)}}, **fmt)
    store.set_counts.clear()

    sub = ds.isel(x=slice(2, 11)).copy()
    sub["a"].values[:] = 7.5
    sub["b"].values[:] = 2.5
    sub = sub.chunk({"x": (2, 3, 4), "y": (5,)})
    with dask.config.set(scheduler="synchronous"):
        sub.to_zarr(store, region="auto", align_chunks=True)

    a = open_array_direct(store, "a")[:]
    b = open_array_direct(store, "b")[:]
    exp_a = np.zeros((12, 5))
    exp_a[2:11] = 7.5
    exp_b = np.zeros(12)
    exp_b[2:11] = 2.5
    np.testing.assert_array_equal(a, exp_a)
    np.testing.assert_array_equal(b, exp_b)
    for name in ("a", "b"):
        counts = store.chunk_write_counts(name)
        assert counts and max(counts.values()) == 1, (name, counts)


@pytest.mark.parametrize("zarr_format", [2, 3], ids=["v2", "v3"])
def test_append_dim_with_alignment(zarr_format):
    ds0, d0 = make_dataset((7,), ((7,),), dims=("t",))
    store, fmt = store_for(zarr_format)
    ds0.to_zarr(store, mode="w", encoding={"v": {"chunks": (4,)}}, **fmt)
    store.set_counts.clear()

    ds1, d1 = make_dataset((9,), ((2, 2, 2, 3),), offset=100, dims=("t",))
    ds1 = ds1.assign_coords(t=np.arange(7, 16))
    with dask.config.set(scheduler="synchronous"):
        ds1.to_zarr(store, append_dim="t", align_chunks=True)

    arr = open_array_direct(store, "v")
    np.testing.assert_array_equal(arr[:], np.concatenate([d0, d1]))
    assert tuple(arr.chunks) == (4,)
    assert arr.shape == (16,)
    counts = store.chunk_write_counts("v")
    assert counts and max(counts.values()) == 1, counts


# --------------------------------------------------------------------------- #
# D + E: contract of the alignment helpers (properties, not expected tuples)
# --------------------------------------------------------------------------- #
def _random_partition(rng, total, max_part):
    parts = []
    while total > 0:
        p = int(rng.integers(1, max_part + 1))
        p = min(p, total)
        parts.append(p)
        total -= p
    return tuple(parts)


def _property_cases(n=150, seed=20250605):
    rng = np.random.default_rng(seed)
    cases = []
    for _ in range(n):
        chunk_size = int(rng.integers(1, 9))
        start = int(rng.integers(0, 12))
        size = int(rng.integers(1, 40))
        var_chunks = _random_partition(rng, size, int(rng.integers(1, 12)))
        cases.append((chunk_size, start, size, var_chunks))
    return cases


PROPERTY_CASES = _property_cases()


def test_build_grid_chunks_contract():
    from xarray.backends.chunks import build_grid_chunks

    for chunk_size, start, size, _ in PROPERTY_CASES:
        got = build_grid_chunks(size, chunk_size=chunk_size, region=slice(start, start + size))
        assert sum(got) == size, (chunk_size, start, size, got)
        assert all(c > 0 for c in got)
        first = min(size, chunk_size - start % chunk_size)
        assert got[0] == first
        # interior chunks are full Zarr chunks; only the borders may be partial
        assert all(c == chunk_size for c in got[1:-1])
        if len(got) > 1:
            assert got[-1] <= chunk_size
    # region=None means the region starts at 0
    assert build_grid_chunks(10, chunk_size=4) == build_grid_chunks(10, chunk_size=4, region=slice(0, 10))
    assert build_grid_chunks(10, chunk_size=4, region=slice(None, None)) == (4, 4, 2)


def test_grid_rechunk_contract():
    from xarray.backends.chunks import grid_rechunk

    checked = 0
    for chunk_size, start, size, var_chunks in PROPERTY_CASES:
        v = xr.Variable(("x",), np.arange(size)).chunk({"x": var_chunks})
        out = grid_rechunk(v, enc_chunks=(chunk_size,), region=(slice(start, start + size),))
        assert isinstance(out, xr.Variable)
        (chunks,) = out.chunks
        assert sum(chunks) == size, (chunk_size, start, var_chunks, chunks)
        # every interior boundary lands on a Zarr chunk boundary of the store
        bounds = np.cumsum(chunks)[:-1] + start
        assert all(b % chunk_size == 0 for b in bounds), (chunk_size, start, var_chunks, chunks)
        # never bigger than the larger of the Zarr chunk and the largest input chunk
        assert max(chunks) <= max(chunk_size, max(var_chunks)), (chunk_size, start, var_chunks, chunks)
        # values are untouched
        np.testing.assert_array_equal(out.values, np.arange(size))
        checked += 1
    assert checked == len(PROPERTY_CASES)


def test_grid_rechunk_preserves_already_aligned_chunks():
    from xarray.backends.chunks import grid_rechunk

    for chunk_size, start, size, _ in PROPERTY_CASES:
        # build an aligned partition: borders follow the grid, interior multiples of chunk_size
        first = min(size, chunk_size - start % chunk_size)
        rest = size - first
        rng = np.random.default_rng(size * 31 + start)
        parts = [first]
        while rest > 0:
            k = int(rng.integers(1, 4)) * chunk_size
            k = min(k, rest)
            parts.append(k)
            rest -= k
        parts = tuple(p for p in parts if p > 0)
        v = xr.Variable(("x",), np.arange(size)).chunk({"x": parts})
        out = grid_rechunk(v, enc_chunks=(chunk_size,), region=(slice(start, start + size),))
        assert out.chunks == (parts,), (chunk_size, start, parts, out.chunks)


def test_grid_rechunk_numpy_variable_is_returned_unchanged():
    from xarray.backends.chunks import grid_rechunk

    v = xr.Variable(("x",), np.arange(10))
    out = grid_rechunk(v, enc_chunks=(3,), region=(slice(0, 10),))
    assert out.chunks is None
    np.testing.assert_array_equal(out.values, np.arange(10))


def test_align_nd_chunks_contract():
    from xarray.backends.chunks import align_nd_chunks

    rng = np.random.default_rng(7)
    for _ in range(60):
        ndim = int(rng.integers(1, 4))
        v_chunks, b_chunks = [], []
        for _ in range(ndim):
            chunk_size = int(rng.integers(1, 7))
            size = int(rng.integers(1, 30))
            start = int(rng.integers(0, 10))
            first = min(size, chunk_size - start % chunk_size)
            grid = [first] + [chunk_size] * ((size - first) // chunk_size)
            if (size - first) % chunk_size:
                grid.append((size - first) % chunk_size)
            b_chunks.append(tuple(grid))
            v_chunks.append(_random_partition(rng, size, int(rng.integers(1, 9))))
        out = align_nd_chunks(nd_v_chunks=tuple(v_chunks), nd_backend_chunks=tuple(b_chunks))
        assert len(out) == ndim
        for got, vc, bc in zip(out, v_chunks, b_chunks, strict=True):
            assert sum(got) == sum(vc)
            grid_bounds = set(np.cumsum(bc)[:-1].tolist())
            for b in np.cumsum(got)[:-1].tolist():
                assert b in grid_bounds, (vc, bc, got)
    with pytest.raises(ValueError):
        align_nd_chunks(nd_v_chunks=((3, 3),), nd_backend_chunks=((2, 2, 2), (1,)))
    with pytest.raises(ValueError):
        align_nd_chunks(nd_v_chunks=((3, 4),), nd_backend_chunks=((2, 2, 2),))
