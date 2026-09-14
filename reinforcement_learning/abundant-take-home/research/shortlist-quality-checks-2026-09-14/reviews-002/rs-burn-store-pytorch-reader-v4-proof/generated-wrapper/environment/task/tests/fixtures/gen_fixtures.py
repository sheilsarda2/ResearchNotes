#!/usr/bin/env python3
"""
Generate the PyTorch checkpoint fixture matrix and its reference manifest.

Run once while building the verifier image (torch is installed there; it is not available
to the agent). Every file the reader must accept is written by the pinned `torch.save`
(or, for containers torch can no longer write, assembled by hand from the format's
description) and described in `manifest.tsv`, which the Rust harness compares the
agent-built reader against. Files the reader must refuse are listed as `reject` rows.

Manifest rows (tab separated):

  meta       <file> <Zip|Legacy|Tar|Pickle> <version|-> <format_version|-> <align 0|1|-> <data_size|->
  entries    <file> <key|-> <count>                 tensors reachable from the root (or the key)
  tensor     <file> <key|-> <name> <dtype> <dims|-> <numel> <crc32> <bytesum>
  pickle_int <file> <key> <value>                   read_pickle_data(file, Some(key)) == Int(value)
  reject     <file> <open|any> <note>               must fail; `open` means PytorchReader::new fails

`dtype` uses the reader's names: F32 F64 F16 BF16 I64 I32 I16 I8 U64 U32 U16 U8 BOOL.
`crc32` is zlib.crc32 of the row-major little-endian bytes of the tensor (bool as one 0/1
byte per element); `bytesum` is the sum of those bytes.
"""

import argparse
import io
import os
import pickle
import struct
import sys
import tarfile
import zipfile
import zlib
from collections import OrderedDict
from pathlib import Path

import numpy as np
import torch
from torch import nn

DTYPE_NAMES = {
    torch.float32: "F32",
    torch.float64: "F64",
    torch.float16: "F16",
    torch.bfloat16: "BF16",
    torch.int64: "I64",
    torch.int32: "I32",
    torch.int16: "I16",
    torch.int8: "I8",
    torch.uint8: "U8",
    torch.bool: "BOOL",
    torch.uint16: "U16",
    torch.uint32: "U32",
    torch.uint64: "U64",
}

STORAGE_FORMATS = {
    # torch storage class -> (struct format, tensor class, dtype name)
    "FloatStorage": ("<f", "FloatTensor", "F32"),
    "DoubleStorage": ("<d", "DoubleTensor", "F64"),
    "LongStorage": ("<q", "LongTensor", "I64"),
    "IntStorage": ("<i", "IntTensor", "I32"),
    "ShortStorage": ("<h", "ShortTensor", "I16"),
    "ByteStorage": ("<B", "ByteTensor", "U8"),
    "CharStorage": ("<b", "CharTensor", "I8"),
}


class MyTensor(torch.Tensor):
    """A tensor subclass; torch pickles it through torch._tensor._rebuild_from_type_v2."""


class Manifest:
    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        self.rows = []
        self.accept_files = set()
        self.reject_files = set()

    def path(self, name):
        return self.out_dir / name

    def meta(self, name, fmt, version="-", format_version="-", align="-", data_size="-"):
        self.rows.append(["meta", name, fmt, version, format_version, str(align), str(data_size)])

    def entries(self, name, key, obj):
        """Record every tensor reachable from `obj` through nested dicts, like the reader."""
        tensors = []
        walk(obj, [], tensors)
        self.rows.append(["entries", name, key or "-", str(len(tensors))])
        for tname, t in tensors:
            data = tensor_bytes(t)
            dims = ",".join(str(d) for d in t.shape) if t.dim() > 0 else "-"
            self.rows.append(
                [
                    "tensor",
                    name,
                    key or "-",
                    tname,
                    DTYPE_NAMES[t.dtype],
                    dims,
                    str(t.numel()),
                    "%08x" % (zlib.crc32(data) & 0xFFFFFFFF),
                    str(sum(data)),
                ]
            )
        self.accept_files.add(name)

    def pickle_int(self, name, key, value):
        self.rows.append(["pickle_int", name, key, str(int(value))])

    def reject(self, name, phase, note):
        assert phase in ("open", "any")
        self.rows.append(["reject", name, phase, note])
        self.reject_files.add(name)

    def write(self):
        for row in self.rows:
            for field in row:
                assert "\t" not in field and "\n" not in field, row
        with open(self.out_dir / "manifest.tsv", "w") as f:
            for row in self.rows:
                f.write("\t".join(row) + "\n")
        summary = {
            "accept_files": len(self.accept_files),
            "reject_files": len(self.reject_files),
            "tensor_rows": sum(1 for r in self.rows if r[0] == "tensor"),
            "entries_rows": sum(1 for r in self.rows if r[0] == "entries"),
            "meta_rows": sum(1 for r in self.rows if r[0] == "meta"),
            "pickle_int_rows": sum(1 for r in self.rows if r[0] == "pickle_int"),
            "torch": torch.__version__,
        }
        with open(self.out_dir / "summary.txt", "w") as f:
            for k, v in summary.items():
                f.write(f"{k}={v}\n")
        return summary


def walk(obj, path, out):
    if isinstance(obj, torch.Tensor):
        out.append((".".join(path), obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            assert isinstance(k, (str, int)), k
            walk(v, path + [str(k)], out)
    # Lists, tuples and other objects are not descended into: the reader collects tensors
    # from nested dictionaries only.


def tensor_bytes(t: torch.Tensor) -> bytes:
    t = t.detach().contiguous().cpu()
    if t.numel() == 0:
        return b""
    flat = t.reshape(-1)
    if t.dtype in (torch.bfloat16, torch.float16):
        flat = flat.view(torch.int16)
    if t.dtype == torch.bool:
        flat = flat.to(torch.uint8)
    arr = flat.numpy()
    assert arr.dtype.byteorder in ("=", "|") and sys.byteorder == "little"
    return arr.tobytes()


# ---------------------------------------------------------------------------------------------
# ZIP helpers (inspecting what torch wrote, and rewriting it)
# ---------------------------------------------------------------------------------------------


def zip_root(zf: zipfile.ZipFile) -> str:
    roots = [n[: -len("data.pkl")] for n in zf.namelist() if n.endswith("data.pkl")]
    roots = [r for r in roots if r == "" or r.endswith("/")]
    return min(roots, key=len)


def zip_meta(m: Manifest, name):
    with zipfile.ZipFile(m.path(name)) as zf:
        root = zip_root(zf)
        names = set(zf.namelist())

        def text(entry):
            full = root + entry
            return zf.read(full).decode().strip() if full in names else "-"

        data_size = sum(
            zf.getinfo(n).file_size
            for n in names
            if n.startswith(root + "data/") and not n.endswith("/")
        )
        m.meta(
            name,
            "Zip",
            version=text("version"),
            format_version=text(".format_version"),
            align=1 if (root + ".storage_alignment") in names else 0,
            data_size=data_size,
        )


def rezip(src: Path, dst: Path, new_root, edit=None, compression=zipfile.ZIP_STORED, drop=()):
    """Rewrite a checkpoint with entries moved under `new_root`, optionally editing bytes."""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", compression=compression) as zout:
        root = zip_root(zin)
        for info in zin.infolist():
            if info.is_dir():
                continue
            rel = info.filename[len(root):] if info.filename.startswith(root) else info.filename
            if rel in drop:
                continue
            data = zin.read(info.filename)
            if edit is not None:
                data = edit(rel, data)
            zout.writestr(new_root + rel, data)


# ---------------------------------------------------------------------------------------------
# Hand-made containers
# ---------------------------------------------------------------------------------------------


class RawStorage:
    def __init__(self, key, storage_class, values, fmt):
        self.key = key
        self.storage_class = storage_class
        self.values = values
        self.fmt = fmt

    def data(self):
        return b"".join(struct.pack(self.fmt, v) for v in self.values)


class RawTensor:
    """A tensor described by `torch._utils._rebuild_tensor_v2` (or `_v3`) arguments."""

    def __init__(self, storage, shape, stride, offset=0, v3_dtype=None):
        self.storage = storage
        self.shape = tuple(shape)
        self.stride = tuple(stride)
        self.offset = offset
        self.v3_dtype = v3_dtype

    def __reduce_ex__(self, protocol):
        if self.v3_dtype is not None:
            return (
                torch._utils._rebuild_tensor_v3,
                (self.storage, self.offset, self.shape, self.stride, False, OrderedDict(), self.v3_dtype),
            )
        return (
            torch._utils._rebuild_tensor_v2,
            (self.storage, self.offset, self.shape, self.stride, False, OrderedDict()),
        )


class RawPickler(pickle.Pickler):
    def persistent_id(self, obj):
        if isinstance(obj, RawStorage):
            return ("storage", getattr(torch, obj.storage_class), obj.key, "cpu", len(obj.values))
        return None


def write_raw_zip(path: Path, obj, storages, root, protocol=2, byteorder="little",
                  version="3\n", extra_entries=None, compression=zipfile.ZIP_STORED,
                  edit=None):
    buf = io.BytesIO()
    RawPickler(buf, protocol=protocol).dump(obj)
    entries = [("data.pkl", buf.getvalue())]
    if byteorder is not None:
        entries.append(("byteorder", byteorder.encode()))
    for s in storages:
        entries.append((f"data/{s.key}", s.data()))
    if version is not None:
        entries.append(("version", version.encode()))
    entries.extend(extra_entries or [])
    with zipfile.ZipFile(path, "w", compression=compression) as zf:
        for name, data in entries:
            if edit is not None:
                data = edit(name, data)
            zf.writestr(root + name, data)


def raw_tensor_bytes(raw: RawTensor):
    """Row-major bytes of a RawTensor over its storage, for the manifest."""
    fmt = raw.storage.fmt
    size = struct.calcsize(fmt)
    data = raw.storage.data()
    out = bytearray()
    numel = 1
    for d in raw.shape:
        numel *= d
    for linear in range(numel):
        rem = linear
        idx = raw.offset
        for dim, step in zip(reversed(raw.shape), reversed(raw.stride)):
            idx += (rem % dim) * step
            rem //= dim
        out += data[idx * size : (idx + 1) * size]
    return bytes(out)


def record_raw(m: Manifest, name, key, tensors):
    """Manifest rows for hand-made tensors: name -> (RawTensor, dtype name)."""
    m.rows.append(["entries", name, key or "-", str(len(tensors))])
    for tname, (raw, dtype) in tensors.items():
        data = raw_tensor_bytes(raw)
        numel = 1
        for d in raw.shape:
            numel *= d
        dims = ",".join(str(d) for d in raw.shape) if raw.shape else "-"
        m.rows.append(
            ["tensor", name, key or "-", tname, dtype, dims, str(numel),
             "%08x" % (zlib.crc32(data) & 0xFFFFFFFF), str(sum(data))]
        )
    m.accept_files.add(name)


def pickle_int_value(value):
    if 0 <= value < 256:
        return b"K" + bytes([value])
    if 0 <= value < 65536:
        return b"M" + struct.pack("<H", value)
    return b"J" + struct.pack("<i", value)


def pickle_tuple_with_torch_class(items, class_name):
    """Pickle `(*items, torch.<class_name>)` by hand (protocol 2)."""
    out = io.BytesIO()
    out.write(b"\x80\x02(")
    for item in items:
        if isinstance(item, int):
            out.write(pickle_int_value(item))
        else:
            enc = item.encode("utf-8")
            out.write(b"U" + bytes([len(enc)]) + enc)
    out.write(b"ctorch\n" + class_name.encode("ascii") + b"\nt.")
    return out.getvalue()


def row_major_stride(shape):
    stride = []
    step = 1
    for dim in reversed(shape):
        stride.insert(0, step)
        step *= dim
    return stride


class _TarPersistent:
    def __init__(self, key):
        self.key = key


def write_tar_checkpoint(path: Path, tensors, dtypes, views=(), storage_count_override=None,
                         truncate_storage_bytes=0):
    """
    The container PyTorch wrote before 0.1.10:
      sys_info  pickle {protocol_version, little_endian, type_sizes}
      storages  count pickle; per storage: (key, location, class) pickle, i64 numel, bytes;
                then a pickle list of (view key, root key, offset, numel)
      tensors   count pickle; per tensor: (key, storage key, class) pickle, i32 ndim,
                4 unused bytes, ndim i64 sizes, ndim i64 strides, i64 storage offset
      pickle    the saved object; tensors are persistent ids naming `tensors` entries
    Returns name -> (RawTensor-like description, dtype) for the manifest.
    """
    storages = io.BytesIO()
    table = io.BytesIO()
    count = len(tensors) if storage_count_override is None else storage_count_override
    pickle.dump(count, storages, protocol=2)
    pickle.dump(len(tensors) + len(views), table, protocol=2)
    described = OrderedDict()
    state = OrderedDict()
    names = list(tensors)
    for index, (name, (values, shape)) in enumerate(tensors.items()):
        storage_key, tensor_key = 1000 + index, 2000 + index
        fmt, tensor_class, dtype = STORAGE_FORMATS[dtypes[name]]
        storages.write(pickle_tuple_with_torch_class([storage_key, "cpu"], dtypes[name]))
        storages.write(struct.pack("<q", len(values)))
        data = b"".join(struct.pack(fmt, v) for v in values)
        if truncate_storage_bytes and index == 0:
            data = data[: len(data) - truncate_storage_bytes]
        storages.write(data)
        table.write(pickle_tuple_with_torch_class([tensor_key, storage_key], tensor_class))
        table.write(struct.pack("<i", len(shape)) + b"\x00" * 4)
        table.write(struct.pack(f"<{len(shape)}q", *shape))
        table.write(struct.pack(f"<{len(shape)}q", *row_major_stride(shape)))
        table.write(struct.pack("<q", 0))
        state[name] = _TarPersistent(tensor_key)
        raw = RawTensor(RawStorage(str(storage_key), dtypes[name], values, fmt), shape,
                        row_major_stride(shape))
        described[name] = (raw, dtype)
    view_rows = []
    for index, (name, root_name, offset, numel, shape) in enumerate(views):
        root_index = names.index(root_name)
        view_key, tensor_key = 3000 + index, 4000 + index
        fmt, tensor_class, dtype = STORAGE_FORMATS[dtypes[root_name]]
        view_rows.append((view_key, 1000 + root_index, offset, numel))
        table.write(pickle_tuple_with_torch_class([tensor_key, view_key], tensor_class))
        table.write(struct.pack("<i", len(shape)) + b"\x00" * 4)
        table.write(struct.pack(f"<{len(shape)}q", *shape))
        table.write(struct.pack(f"<{len(shape)}q", *row_major_stride(shape)))
        table.write(struct.pack("<q", 0))
        state[name] = _TarPersistent(tensor_key)
        root_values = tensors[root_name][0]
        raw = RawTensor(RawStorage(str(view_key), dtypes[root_name], root_values, fmt), shape,
                        row_major_stride(shape), offset=offset)
        described[name] = (raw, dtype)
    pickle.dump(view_rows, storages, protocol=2)

    class P(pickle.Pickler):
        def persistent_id(self, obj):
            return str(obj.key) if isinstance(obj, _TarPersistent) else None

    main = io.BytesIO()
    P(main, protocol=2).dump(state)
    sys_info = pickle.dumps(
        {"protocol_version": 1000, "little_endian": True,
         "type_sizes": {"short": 2, "int": 4, "long": 8}},
        protocol=2,
    )
    with tarfile.open(path, "w") as tar:
        for entry, data in [("sys_info", sys_info), ("pickle", main.getvalue()),
                            ("tensors", table.getvalue()), ("storages", storages.getvalue())]:
            info = tarfile.TarInfo(name=entry)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return described, len(storages.getvalue())


# ---------------------------------------------------------------------------------------------
# The matrix
# ---------------------------------------------------------------------------------------------


def small_state_dict(seed=0):
    g = torch.Generator().manual_seed(seed)
    return OrderedDict(
        [
            ("linear.weight", torch.randn(3, 4, generator=g)),
            ("linear.bias", torch.randn(3, generator=g)),
            ("norm.running_var", torch.rand(3, generator=g, dtype=torch.float64)),
            ("counter", torch.tensor(5, dtype=torch.int64)),
        ]
    )


def save(m: Manifest, name, obj, **kwargs):
    torch.save(obj, m.path(name), **kwargs)


def build_torch_matrix(m: Manifest):
    torch.manual_seed(1234)

    # A1: every dtype with a typed storage class.
    d = OrderedDict()
    for dt in (torch.float32, torch.float64, torch.float16, torch.bfloat16, torch.int64,
               torch.int32, torch.int16, torch.int8, torch.uint8):
        if dt.is_floating_point:
            d[f"t_{DTYPE_NAMES[dt].lower()}"] = (torch.randn(3, 4) * 100).to(dt)
        else:
            info = torch.iinfo(dt)
            d[f"t_{DTYPE_NAMES[dt].lower()}"] = torch.randint(
                max(info.min, -1000), min(info.max, 1000) + 1, (3, 4), dtype=torch.int64
            ).to(dt)
    d["t_bool"] = torch.tensor([[True, False, True], [False, False, True]])
    save(m, "dtypes_typed.pt", d)
    zip_meta(m, "dtypes_typed.pt")
    m.entries("dtypes_typed.pt", None, d)

    # A2: unsigned dtypes without a typed storage class (written through _rebuild_tensor_v3).
    d = OrderedDict(
        [
            ("u16", torch.tensor([[0, 1, 65535], [256, 512, 1024]], dtype=torch.uint16)),
            ("u32", torch.tensor([0, 1, 4294967295, 70000], dtype=torch.uint32)),
            ("u64", torch.tensor([0, 1, 2**40, 18446744073709551615], dtype=torch.uint64)),
        ]
    )
    save(m, "dtypes_v3_unsigned.pt", d)
    zip_meta(m, "dtypes_v3_unsigned.pt")
    m.entries("dtypes_v3_unsigned.pt", None, d)

    # A3: a real module state_dict (OrderedDict with _metadata; dotted and numeric keys;
    # buffers including an int64 scalar).
    model = nn.Sequential(
        nn.Linear(4, 3),
        nn.BatchNorm1d(3),
        nn.Sequential(nn.Linear(3, 2), nn.LayerNorm(2)),
    )
    with torch.no_grad():
        model(torch.randn(8, 4))  # advance running stats and num_batches_tracked
    sd = model.state_dict()
    save(m, "module_state_dict.pt", sd)
    zip_meta(m, "module_state_dict.pt")
    m.entries("module_state_dict.pt", None, sd)

    # A4: a training checkpoint; tensors under int keys in the optimizer state.
    ckpt = {
        "epoch": 7,
        "model_state_dict": small_state_dict(1),
        "optimizer_state_dict": {
            "state": {
                0: {"step": torch.tensor(12.0), "exp_avg": torch.randn(3, 4),
                    "exp_avg_sq": torch.rand(3, 4)},
                1: {"step": torch.tensor(12.0), "exp_avg": torch.randn(3),
                    "exp_avg_sq": torch.rand(3)},
            },
            "param_groups": [{"lr": 0.001, "betas": (0.9, 0.999), "params": [0, 1]}],
        },
        "loss": 0.25,
        "tag": "run-1",
        "extra": None,
        "flags": [True, False],
        "shape": (1, 2),
    }
    save(m, "checkpoint.pt", ckpt)
    zip_meta(m, "checkpoint.pt")
    m.entries("checkpoint.pt", None, ckpt)
    m.entries("checkpoint.pt", "model_state_dict", ckpt["model_state_dict"])
    m.entries("checkpoint.pt", "optimizer_state_dict", ckpt["optimizer_state_dict"])
    m.pickle_int("checkpoint.pt", "epoch", 7)

    # A5: several views of one storage (torch.save deduplicates the storage).
    base = torch.arange(24, dtype=torch.float32)
    d = OrderedDict(
        [
            ("full", base),
            ("head", base[:6]),
            ("tail_matrix", base[12:].view(3, 4)),
            ("every_other", base[::2]),
            ("transposed", base.view(4, 6).t()),
            ("expanded", base[:4].view(4, 1).expand(4, 5)),
            ("scalar_view", base[7]),
            ("column", base.view(6, 4)[:, 1]),
        ]
    )
    save(m, "shared_storage_views.pt", d)
    zip_meta(m, "shared_storage_views.pt")
    m.entries("shared_storage_views.pt", None, d)

    # A6: non-contiguous 3-D views.
    t = torch.arange(60, dtype=torch.float32).view(3, 4, 5)
    d = OrderedDict(
        [
            ("permuted", t.permute(2, 0, 1)),
            ("narrowed", t[:, 1:3, ::2]),
            ("diagonal", t[0].diagonal()),
            ("as_strided", t.as_strided((3, 3), (1, 2), 2)),
            ("unsqueezed_expand", t[0, 0].unsqueeze(1).expand(5, 3)),
            ("int_permuted", torch.arange(24, dtype=torch.int16).view(2, 3, 4).permute(1, 2, 0)),
        ]
    )
    save(m, "noncontiguous_3d.pt", d)
    zip_meta(m, "noncontiguous_3d.pt")
    m.entries("noncontiguous_3d.pt", None, d)

    # A7: scalars and empty tensors.
    d = OrderedDict(
        [
            ("s_f32", torch.tensor(2.5)),
            ("s_i64", torch.tensor(-3)),
            ("s_bool", torch.tensor(True)),
            ("s_bf16", torch.tensor(1.5, dtype=torch.bfloat16)),
            ("e0", torch.empty(0)),
            ("e203", torch.empty(2, 0, 3, dtype=torch.int32)),
            ("one", torch.tensor([7], dtype=torch.int16)),
        ]
    )
    save(m, "scalars_and_empty.pt", d)
    zip_meta(m, "scalars_and_empty.pt")
    m.entries("scalars_and_empty.pt", None, d)

    # A8: large storages.
    d = OrderedDict(
        [
            ("big_f32", torch.randn(1024, 1024)),
            ("big_i64", torch.arange(2048 * 512, dtype=torch.int64).view(2048, 512)),
            ("big_u8", torch.randint(0, 256, (3000, 1000), dtype=torch.uint8)),
            ("big_view", torch.randn(700, 900).t()),
        ]
    )
    save(m, "large_tensors.pt", d)
    zip_meta(m, "large_tensors.pt")
    m.entries("large_tensors.pt", None, d)

    # A9: legacy container with mixed dtypes, uncloned views and a Parameter.
    base = torch.arange(100, dtype=torch.float32)
    d = OrderedDict(
        [
            ("weight", torch.randn(2, 3)),
            ("bias", torch.ones(2, dtype=torch.float64)),
            ("ids", torch.tensor([1, 2, 3], dtype=torch.int64)),
            ("flags", torch.tensor([True, False, True])),
            ("half", torch.randn(4).to(torch.float16)),
            ("view_a", base[10:20]),
            ("view_b", base[50:60].view(2, 5)),
            ("param", nn.Parameter(torch.randn(3, 2))),
        ]
    )
    save(m, "legacy_default.pt", d, _use_new_zipfile_serialization=False)
    m.meta("legacy_default.pt", "Legacy")
    m.entries("legacy_default.pt", None, d)

    # A10: legacy container written at pickle protocol 4 (FRAME in front of the magic).
    d = small_state_dict(2)
    save(m, "legacy_protocol4.pt", d, _use_new_zipfile_serialization=False, pickle_protocol=4)
    m.meta("legacy_protocol4.pt", "Legacy")
    m.entries("legacy_protocol4.pt", None, d)

    # A11-13: ZIP container at pickle protocols 3, 4 and 5.
    for proto in (3, 4, 5):
        d = small_state_dict(10 + proto)
        name = f"zip_protocol{proto}.pt"
        save(m, name, d, pickle_protocol=proto)
        zip_meta(m, name)
        m.entries(name, None, d)

    # A14: saved through a file object: entries live under `archive/`.
    d = small_state_dict(3)
    buf = io.BytesIO()
    torch.save(d, buf)
    m.path("zip_fileobj_archive_root.pt").write_bytes(buf.getvalue())
    with zipfile.ZipFile(m.path("zip_fileobj_archive_root.pt")) as zf:
        assert zip_root(zf) == "archive/", zf.namelist()
    zip_meta(m, "zip_fileobj_archive_root.pt")
    m.entries("zip_fileobj_archive_root.pt", None, d)

    # A15: Parameters and requires_grad tensors outside a state_dict.
    d = OrderedDict(
        [
            ("p", nn.Parameter(torch.randn(3, 3))),
            ("p_frozen", nn.Parameter(torch.randn(2), requires_grad=False)),
            ("req", torch.randn(2).requires_grad_(True)),
            ("buf", torch.zeros(2, dtype=torch.bool)),
        ]
    )
    save(m, "parameters_and_grad.pt", d)
    zip_meta(m, "parameters_and_grad.pt")
    m.entries("parameters_and_grad.pt", None, d)

    # A16: Python objects the reader does not interpret, next to tensors.
    d = {
        "w": torch.randn(2, 2),
        "device": torch.device("cpu"),
        "dtype": torch.float32,
        "size": torch.Size([2, 2]),
        "np_scalar": np.float64(1.5),
        "np_array": np.arange(4, dtype=np.int32),
        "nested": {"v": torch.randn(3), "note": "x"},
        "count": 3,
        "ratio": 0.5,
        "flag": True,
        "nothing": None,
        "raw": b"\x00\x01",
        "seq": [1, 2, 3],
        "pair": (1, "a"),
        "big_int": 2**70,
        "neg": -17,
    }
    save(m, "opaque_metadata.pt", d)
    zip_meta(m, "opaque_metadata.pt")
    m.entries("opaque_metadata.pt", None, d)
    m.pickle_int("opaque_metadata.pt", "count", 3)
    m.pickle_int("opaque_metadata.pt", "neg", -17)

    # A17: many tensors (storage keys and memo indices beyond one byte).
    d = OrderedDict((f"layer{i}.weight", torch.full((2,), float(i))) for i in range(300))
    save(m, "many_tensors.pt", d)
    zip_meta(m, "many_tensors.pt")
    m.entries("many_tensors.pt", None, d)

    # A18: special floating-point values, byte-exact.
    d = OrderedDict(
        [
            ("f32", torch.tensor([float("nan"), float("inf"), float("-inf"), -0.0, 1e-45, 3.4e38])),
            ("f64", torch.tensor([float("nan"), float("inf"), -0.0, 5e-324, 1.7e308], dtype=torch.float64)),
            ("f16", torch.tensor([float("nan"), float("inf"), -0.0, 6e-8, 65504.0], dtype=torch.float16)),
            ("bf16", torch.tensor([float("nan"), float("-inf"), -0.0, 1e-40, 3e38], dtype=torch.bfloat16)),
        ]
    )
    save(m, "special_floats.pt", d)
    zip_meta(m, "special_floats.pt")
    m.entries("special_floats.pt", None, d)

    # A19: a torch.Tensor subclass (rebuilt through torch._tensor._rebuild_from_type_v2).
    sub = torch.randn(2, 2).as_subclass(MyTensor)
    d = OrderedDict([("t", sub), ("plain", torch.ones(2))])
    save(m, "subclass_tensor.pt", d)
    zip_meta(m, "subclass_tensor.pt")
    m.entries("subclass_tensor.pt", None, d)

    # A20: int dictionary keys become decimal names.
    d = {0: torch.randn(2), 1: torch.randn(2), "s": {2: torch.randn(1)}}
    save(m, "int_keys.pt", d)
    zip_meta(m, "int_keys.pt")
    m.entries("int_keys.pt", None, d)

    # A21: pickle protocol 2 with a Python-2 style BINSTRING key (str keys as bytes).
    # torch does not write these; they come from files converted with py2 pickles. Skipped:
    # not part of the contract.

    return sd


def build_crafted_accepts(m: Manifest):
    f32 = RawStorage("0", "FloatStorage", [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], "<f")
    i64 = RawStorage("1", "LongStorage", [-5, 0, 5], "<q")

    def two_tensors():
        return OrderedDict(
            [("weight", RawTensor(f32, (2, 3), (3, 1))), ("ids", RawTensor(i64, (3,), (1,)))]
        ), {"weight": (RawTensor(f32, (2, 3), (3, 1)), "F32"), "ids": (RawTensor(i64, (3,), (1,)), "I64")}

    # entries at the archive root, no directory
    obj, desc = two_tensors()
    write_raw_zip(m.path("crafted_root_level.pt"), obj, [f32, i64], root="")
    zip_meta(m, "crafted_root_level.pt")
    record_raw(m, "crafted_root_level.pt", None, desc)

    # entries under a deep directory
    obj, desc = two_tensors()
    write_raw_zip(m.path("crafted_deep_root.pt"), obj, [f32, i64], root="ckpt/inner/")
    zip_meta(m, "crafted_deep_root.pt")
    record_raw(m, "crafted_deep_root.pt", None, desc)

    # no version and no byteorder entries at all (older writers)
    obj, desc = two_tensors()
    write_raw_zip(m.path("crafted_no_version_entries.pt"), obj, [f32, i64], root="model/",
                  byteorder=None, version=None)
    zip_meta(m, "crafted_no_version_entries.pt")
    record_raw(m, "crafted_no_version_entries.pt", None, desc)

    # deflate-compressed entries
    obj, desc = two_tensors()
    write_raw_zip(m.path("crafted_deflated.pt"), obj, [f32, i64], root="model/",
                  compression=zipfile.ZIP_DEFLATED)
    zip_meta(m, "crafted_deflated.pt")
    record_raw(m, "crafted_deflated.pt", None, desc)

    # protocol 4 pickle with a storage larger than the tensor that reads it, plus an
    # offset and a stride, and a trailing junk-padded storage
    big = RawStorage("0", "FloatStorage", [float(i) for i in range(20)], "<f")
    obj = OrderedDict(
        [
            ("window", RawTensor(big, (2, 3), (3, 1), offset=4)),
            ("strided", RawTensor(big, (4,), (5,), offset=1)),
        ]
    )
    desc = {"window": (RawTensor(big, (2, 3), (3, 1), offset=4), "F32"),
            "strided": (RawTensor(big, (4,), (5,), offset=1), "F32")}

    def pad(name, data):
        return data + b"\xff" * 1024 if name == "data/0" else data

    write_raw_zip(m.path("crafted_offsets_protocol4.pt"), obj, [big], root="model/", protocol=4,
                  edit=pad)
    zip_meta(m, "crafted_offsets_protocol4.pt")
    record_raw(m, "crafted_offsets_protocol4.pt", None, desc)

    # UntypedStorage with an explicit dtype through _rebuild_tensor_v3 (what torch writes for
    # dtypes without a storage class), here for a float32 tensor
    class UntypedRaw(RawStorage):
        pass

    class UntypedPickler(pickle.Pickler):
        def persistent_id(self, obj):
            if isinstance(obj, UntypedRaw):
                return ("storage", torch.UntypedStorage, obj.key, "cpu", len(obj.data()))
            return None

    ust = UntypedRaw("0", "UntypedStorage", [0.5, -0.5, 2.0, 8.0], "<f")
    raw = RawTensor(ust, (2, 2), (2, 1), v3_dtype=torch.float32)
    buf = io.BytesIO()
    UntypedPickler(buf, protocol=2).dump(OrderedDict([("t", raw)]))
    with zipfile.ZipFile(m.path("crafted_untyped_storage_v3.pt"), "w") as zf:
        zf.writestr("model/data.pkl", buf.getvalue())
        zf.writestr("model/byteorder", b"little")
        zf.writestr("model/data/0", ust.data())
        zf.writestr("model/version", b"3\n")
    zip_meta(m, "crafted_untyped_storage_v3.pt")
    record_raw(m, "crafted_untyped_storage_v3.pt", None, {"t": (raw, "F32")})

    # plain pickle (no container): configuration only
    cfg = {"n": 5, "name": "cfg", "layers": [1, 2, 3], "nested": {"k": 1.5}}
    with open(m.path("plain_pickle_dict.pkl"), "wb") as f:
        pickle.dump(cfg, f, protocol=2)
    m.meta("plain_pickle_dict.pkl", "Pickle")
    m.rows.append(["entries", "plain_pickle_dict.pkl", "-", "0"])
    m.accept_files.add("plain_pickle_dict.pkl")
    m.pickle_int("plain_pickle_dict.pkl", "n", 5)

    # the pre-0.1.10 TAR container, with a storage view
    desc, data_size = write_tar_checkpoint(
        m.path("tar_early.tar"),
        OrderedDict(
            [
                ("root", ([float(i) * 0.5 for i in range(10)], [10])),
                ("matrix", ([1.5, 2.5, 3.5, 4.5, 5.5, 6.5], [2, 3])),
                ("ids", ([7, -7, 70], [3])),
                ("bytes", ([0, 127, 255], [3])),
            ]
        ),
        {"root": "FloatStorage", "matrix": "DoubleStorage", "ids": "LongStorage",
         "bytes": "ByteStorage"},
        views=[("window", "root", 3, 4, [2, 2])],
    )
    m.meta("tar_early.tar", "Tar", data_size=data_size)
    record_raw(m, "tar_early.tar", None, desc)


def build_rejects(m: Manifest):
    src = m.path("zip_protocol3.pt")  # a small torch-written checkpoint to corrupt

    rezip(src, m.path("r_big_endian_marker.pt"), "model/",
          edit=lambda n, d: b"big" if n == "byteorder" else d)
    m.reject("r_big_endian_marker.pt", "open", "byteorder entry is 'big'")

    rezip(src, m.path("r_byteorder_unknown.pt"), "model/",
          edit=lambda n, d: b"middle" if n == "byteorder" else d)
    m.reject("r_byteorder_unknown.pt", "open", "byteorder entry is neither little nor big")

    rezip(src, m.path("r_no_data_pkl.pt"), "model/", drop=("data.pkl",))
    m.reject("r_no_data_pkl.pt", "open", "ZIP without data.pkl")

    rezip(src, m.path("r_storage_short.pt"), "model/",
          edit=lambda n, d: d[: len(d) // 2] if n == "data/0" else d)
    m.reject("r_storage_short.pt", "any", "storage entry shorter than the pickle declares")

    rezip(src, m.path("r_storage_missing.pt"), "model/", drop=("data/0",))
    m.reject("r_storage_missing.pt", "any", "storage entry missing from the archive")

    # a byte flipped inside a stored entry, CRC left behind
    rezip(src, m.path("r_zip_crc_mismatch.pt"), "model/")
    path = m.path("r_zip_crc_mismatch.pt")
    with zipfile.ZipFile(path) as zf:
        info = zf.getinfo("model/data/0")
        assert info.compress_type == zipfile.ZIP_STORED
        header_len = 30 + len(info.filename.encode()) + len(info.extra)
        data_start = info.header_offset + header_len
    b = bytearray(path.read_bytes())
    b[data_start] ^= 0xFF
    path.write_bytes(bytes(b))
    m.reject("r_zip_crc_mismatch.pt", "any", "stored entry fails its CRC")

    # truncated archive
    raw = src.read_bytes()
    m.path("r_zip_truncated_file.pt").write_bytes(raw[: len(raw) // 2])
    m.reject("r_zip_truncated_file.pt", "open", "ZIP file cut in half")

    # data.pkl cut before STOP
    rezip(src, m.path("r_truncated_pickle.pt"), "model/",
          edit=lambda n, d: d[:-3] if n == "data.pkl" else d)
    m.reject("r_truncated_pickle.pt", "open", "data.pkl ends before STOP")

    # broadcast bomb: (2^21, 2^21) view with zero strides over 32 elements
    st = RawStorage("0", "FloatStorage", [1.0] * 32, "<f")
    write_raw_zip(m.path("r_broadcast_bomb.pt"),
                  OrderedDict([("bomb", RawTensor(st, (1 << 21, 1 << 21), (0, 0)))]), [st],
                  root="model/")
    m.reject("r_broadcast_bomb.pt", "open", "zero-stride view would materialize 16 TiB")

    # extent past the storage
    write_raw_zip(m.path("r_offset_past_storage.pt"),
                  OrderedDict([("t", RawTensor(st, (4, 8), (8, 1), offset=8))]), [st],
                  root="model/")
    m.reject("r_offset_past_storage.pt", "open", "offset plus extent exceeds the storage")

    # stride overflow
    write_raw_zip(m.path("r_stride_overflow.pt"),
                  OrderedDict([("t", RawTensor(st, (2, 2), (2**62, 2**62)))]), [st],
                  root="model/")
    m.reject("r_stride_overflow.pt", "open", "stride arithmetic overflows")

    # typed storage vs v3 dtype mismatch
    write_raw_zip(m.path("r_dtype_mismatch_v3.pt"),
                  OrderedDict([("t", RawTensor(st, (4,), (1,), v3_dtype=torch.int64))]), [st],
                  root="model/")
    m.reject("r_dtype_mismatch_v3.pt", "open", "FloatStorage rebuilt as int64")

    # a tensor consumed by an unknown callable: must not be silently dropped
    class Wrapped:
        def __init__(self, inner):
            self.inner = inner

        def __reduce_ex__(self, protocol):
            return (Wrapped, (self.inner,))

    Wrapped.__module__ = "mylib"
    Wrapped.__qualname__ = "wrap"
    sys.modules.setdefault("mylib", type(sys)("mylib")).wrap = Wrapped
    write_raw_zip(m.path("r_tensor_in_unknown_object.pt"),
                  OrderedDict([("ok", RawTensor(st, (2,), (1,))),
                               ("wrapped", Wrapped(RawTensor(st, (2,), (1,))))]), [st],
                  root="model/")
    m.reject("r_tensor_in_unknown_object.pt", "open",
             "tensor passed to an uninterpreted callable")

    # torch-produced unsupported tensors
    model = nn.Sequential(nn.Linear(2, 2), nn.ReLU())
    save(m, "r_full_model.pt", model)
    m.reject("r_full_model.pt", "open", "torch.save(model): root is a module object")

    i = torch.tensor([[0, 1], [1, 0]])
    v = torch.tensor([3.0, 4.0])
    save(m, "r_sparse_coo.pt", {"dense": torch.ones(2), "sparse": torch.sparse_coo_tensor(i, v, (2, 2))})
    m.reject("r_sparse_coo.pt", "open", "sparse COO tensor")

    q = torch.quantize_per_tensor(torch.tensor([1.0, 2.0, 3.0]), scale=0.1, zero_point=0,
                                  dtype=torch.qint8)
    save(m, "r_quantized.pt", {"q": q})
    m.reject("r_quantized.pt", "open", "quantized tensor (_rebuild_qtensor)")

    # legacy container corruptions from a torch-written file
    legacy = m.path("legacy_protocol4.pt").read_bytes()
    m.path("r_legacy_truncated.pt").write_bytes(legacy[:-16])
    m.reject("r_legacy_truncated.pt", "open", "legacy storage data cut short")

    legacy2 = bytearray(m.path("legacy_default.pt").read_bytes())
    marker = b"little_endian"
    pos = legacy2.find(marker)
    assert pos > 0
    flag = pos + len(marker)
    if legacy2[flag] == ord("q"):  # BINPUT memo of the key
        flag += 2
    elif legacy2[flag] == ord("r"):  # LONG_BINPUT
        flag += 5
    assert legacy2[flag] == 0x88, hex(legacy2[flag])
    big_legacy = bytearray(legacy2)
    big_legacy[flag] = 0x89
    m.path("r_legacy_big_endian.pt").write_bytes(bytes(big_legacy))
    m.reject("r_legacy_big_endian.pt", "open", "legacy sys_info little_endian=False")

    no_flag = bytearray(legacy2)
    no_flag[pos:pos + len(marker)] = b"little_endiaN"
    m.path("r_legacy_no_endian_flag.pt").write_bytes(bytes(no_flag))
    m.reject("r_legacy_no_endian_flag.pt", "open", "legacy sys_info without little_endian")

    # plain pickles
    dup_bomb = bytearray(b"\x80\x02K\x01")
    for _ in range(60):
        dup_bomb += b"2\x86"  # DUP, TUPLE2: doubles the tree every two bytes
    dup_bomb += b"."
    m.path("r_memo_bomb.pkl").write_bytes(bytes(dup_bomb))
    m.reject("r_memo_bomb.pkl", "open", "DUP/TUPLE2 doubling bomb")

    m.path("r_deep_nesting.pkl").write_bytes(b"\x80\x02N" + b"\x85" * 200_000 + b".")
    m.reject("r_deep_nesting.pkl", "open", "200000 nested tuples")

    m.path("r_ext_opcode.pkl").write_bytes(b"\x80\x02\x82\x01.")
    m.reject("r_ext_opcode.pkl", "open", "EXT1 opcode")

    m.path("r_protocol_6.pkl").write_bytes(b"\x80\x06}.")
    m.reject("r_protocol_6.pkl", "open", "pickle protocol 6")

    m.path("r_stack_underflow.pkl").write_bytes(b"\x80\x02.")
    m.reject("r_stack_underflow.pkl", "open", "STOP on an empty stack")

    m.path("r_bad_opcode.pkl").write_bytes(b"\x80\x02\xff.")
    m.reject("r_bad_opcode.pkl", "open", "byte that is not an opcode")

    buf = io.BytesIO()
    RawPickler(buf, protocol=2).dump(OrderedDict([("t", RawTensor(st, (4,), (1,)))]))
    m.path("r_plain_pickle_with_storage.pkl").write_bytes(buf.getvalue())
    m.reject("r_plain_pickle_with_storage.pkl", "open",
             "plain pickle referencing a storage persistent id")

    # a memoized 1 MiB bytes fetched 200 times
    payload = b"\xab" * (1 << 20)
    bomb = bytearray(b"\x80\x02B" + struct.pack("<I", len(payload)) + payload + b"q\x00(")
    bomb += b"h\x00" * 200
    bomb += b"t."
    m.path("r_memo_copies.pkl").write_bytes(bytes(bomb))
    m.reject("r_memo_copies.pkl", "open", "memoized large payload fetched 200 times")

    # TAR corruptions
    with tarfile.open(m.path("r_tar_absurd_count.tar"), "w") as tar:
        for entry, data in [("storages", b"\x80\x02\x8a\x05\x00\x00\x00\x00\x02."),  # 2^33
                            ("tensors", b"\x80\x02K\x00."), ("pickle", b"\x80\x02}.")]:
            info = tarfile.TarInfo(name=entry)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    m.reject("r_tar_absurd_count.tar", "open", "TAR storages count of 2^33")

    write_tar_checkpoint(
        m.path("r_tar_truncated_storages.tar"),
        OrderedDict([("t", ([1.0, 2.0, 3.0, 4.0], [4]))]),
        {"t": "FloatStorage"},
        truncate_storage_bytes=8,
    )
    m.reject("r_tar_truncated_storages.tar", "open", "TAR storage shorter than its count")

    with tarfile.open(m.path("r_tar_missing_entries.tar"), "w") as tar:
        data = pickle.dumps({"protocol_version": 1000, "little_endian": True}, protocol=2)
        info = tarfile.TarInfo(name="sys_info")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    m.reject("r_tar_missing_entries.tar", "open", "TAR without storages/tensors/pickle")

    # a file with the ZIP signature and nothing valid behind it
    m.path("r_zip_signature_only.pt").write_bytes(b"PK\x03\x04" + b"\x00" * 40)
    m.reject("r_zip_signature_only.pt", "open", "ZIP signature followed by zeros")

    # an empty file
    m.path("r_empty_file.pt").write_bytes(b"")
    m.reject("r_empty_file.pt", "open", "zero-length file")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("out_dir")
    args = parser.parse_args()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for stale in out.iterdir():
        if stale.is_file():
            stale.unlink()
    m = Manifest(out)
    build_torch_matrix(m)
    build_crafted_accepts(m)
    build_rejects(m)
    summary = m.write()
    total = sum(p.stat().st_size for p in out.iterdir())
    print(f"fixtures: {summary}  total_bytes={total}")
    # Sanity: every accept file loads back with torch itself where torch wrote it.
    for name in sorted(m.accept_files):
        if name.endswith(".pt") and not name.startswith("crafted_"):
            torch.load(out / name, weights_only=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
