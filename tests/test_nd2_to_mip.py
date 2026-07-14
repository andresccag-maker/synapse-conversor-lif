"""Tests del modo ND2 → TIFF / MIP.

No usa .nd2 reales: monkeypatchea core._open_nd2 con un FakeNd2 que imita la
superficie de la librería `nd2` que usa core (sizes/experiment/is_rgb/dtype/
attributes/metadata/voxel_size/to_dask). Igual patrón que FakeLifFile.
"""

from __future__ import annotations

import json
import re
from collections import namedtuple
from pathlib import Path

import numpy as np
import pytest
import tifffile

import core


# ---------------------------------------------------------------------------
# Fake de la librería nd2 (solo lo que core consume)
# ---------------------------------------------------------------------------

_Vox = namedtuple("_Vox", ["x", "y", "z"])


class _FakeChannelMeta:
    def __init__(self, name: str):
        self.channel = type("C", (), {"name": name})()


class _FakeMetadata:
    def __init__(self, names):
        self.channels = [_FakeChannelMeta(n) for n in names]


class _FakeLoop:
    def __init__(self, type_: str):
        self.type = type_


class FakeNd2:
    """Imita nd2.ND2File. `to_dask()` devuelve un ndarray con el orden de `sizes`.

    Valor determinista por vóxel: p*1000 + z*100 + c*10 + (y+x) — permite verificar
    que se preserva la posición, el canal y la Z correctos (no "todo C=0").
    """

    def __init__(self, sizes, *, dtype=np.uint16, is_rgb=False,
                 loops=("XYPosLoop", "ZStackLoop"), sig_bits=16,
                 voxel=(0.284, 0.284, 1.0), channel_names=("APC", "Tcell")):
        self.sizes = dict(sizes)
        self._dtype = np.dtype(dtype)
        self.is_rgb = is_rgb
        self.experiment = [_FakeLoop(t) for t in loops]
        self.attributes = type("A", (), {"bitsPerComponentSignificant": sig_bits})()
        self.metadata = _FakeMetadata(channel_names)
        self._voxel = _Vox(*voxel)
        axes = list(self.sizes.keys())
        arr = np.zeros(tuple(self.sizes.values()), dtype=self._dtype)
        it = np.nditer(arr, flags=["multi_index"], op_flags=["writeonly"])
        for _ in it:
            d = {a: it.multi_index[i] for i, a in enumerate(axes)}
            it[0] = (d.get("P", 0) * 1000 + d.get("Z", 0) * 100
                     + d.get("C", 0) * 10 + (d.get("Y", 0) + d.get("X", 0)))
        self._arr = arr
        self.closed = False

    @property
    def dtype(self):
        return self._dtype

    def voxel_size(self):
        return self._voxel

    def to_dask(self):
        return self._arr

    def close(self):
        self.closed = True


def _patch_nd2(monkeypatch, fake):
    monkeypatch.setattr(core, "_open_nd2", lambda p: fake)


@pytest.fixture
def fake_nd2_path(tmp_path, monkeypatch):
    """ND2 estándar: 4 posiciones, 3 Z, 5 canales, 12x10, uint16."""
    fake = FakeNd2({"P": 4, "Z": 3, "C": 5, "Y": 10, "X": 12},
                   channel_names=("APC", "Tcell", "MitoTracker", "MVB", "Extra"))
    path = tmp_path / "EXP-NKG2D pocilloA.nd2"
    path.write_bytes(b"FAKE-ND2-CONTENT")
    _patch_nd2(monkeypatch, fake)
    return path, fake


# ---------------------------------------------------------------------------
# 1. read_nd2_info
# ---------------------------------------------------------------------------

def test_read_nd2_info_positions_channels_pixelsize(fake_nd2_path):
    path, _ = fake_nd2_path
    info, _prev = core.read_nd2_info(str(path))
    assert info.n_series == 4                       # 4 posiciones XY
    assert info.filename == "EXP-NKG2D pocilloA.nd2"  # verbatim con .nd2
    assert len(info.sha256) == 64
    s0 = info.series[0]
    assert s0.n_channels == 5
    assert s0.n_z == 3
    assert (s0.width, s0.height) == (12, 10)
    assert s0.bit_depth == [16, 16, 16, 16, 16]
    assert s0.pixel_size_um == pytest.approx(0.284)
    assert s0.pixel_size_source == "nd2_voxel"
    # nombres de canal → luts informativos (índice 0-based)
    assert [l["name"] for l in info.channel_luts] == \
        ["APC", "Tcell", "MitoTracker", "MVB", "Extra"]


def test_read_nd2_info_single_position_and_single_channel(tmp_path, monkeypatch):
    # Sin eje P (1 posición) ni C (1 canal) — deben inferirse como 1.
    fake = FakeNd2({"Z": 4, "Y": 6, "X": 6}, channel_names=())
    path = tmp_path / "solo.nd2"
    path.write_bytes(b"X")
    _patch_nd2(monkeypatch, fake)
    info, _ = core.read_nd2_info(str(path))
    assert info.n_series == 1
    assert info.series[0].n_channels == 1
    assert info.series[0].n_z == 4


# ---------------------------------------------------------------------------
# 2. convert_nd2 MIP: naming, exclusión, MIP identidad, canal preservado
# ---------------------------------------------------------------------------

def test_convert_nd2_mip_flat_naming_exclusion(fake_nd2_path, tmp_path):
    path, fake = fake_nd2_path
    out = tmp_path / "out"
    opts = core.ConvertOptions(
        output_dir=str(out), experiment="EXP", pocillo="Pocillo A",
        exclude_channels_0based=[0],  # excluye C=0
        projection="mip",
    )
    summary = core.convert_nd2(str(path), opts)
    root = Path(summary["output_root"])
    assert root.name == "Pocillo A"           # preserva espacios
    assert root.parent.name == "EXP"
    assert [p for p in root.iterdir() if p.is_dir()] == []   # aplanado

    tifs = sorted(p.name for p in root.iterdir() if p.suffix == ".tif")
    # 4 posiciones × (5 − 1 excluido) = 16
    assert len(tifs) == 16
    base = "EXP-NKG2D pocilloA.nd2"
    assert f"{base} - Image001 - C=1.tif" in tifs   # .nd2 verbatim, Image 1-idx, C 0-idx
    assert f"{base} - Image004 - C=4.tif" in tifs
    # C=0 excluido en TODAS las posiciones (NO renumerado a C=0)
    for n in tifs:
        assert " - C=0.tif" not in n
    # worker: regex C=N extrae el canal correcto
    assert re.search(r"C\s*=\s*(\d+)", tifs[0]).group(1) in {"1", "2", "3", "4"}
    assert summary["series_written"] == 4 and summary["files_written"] == 16


def test_convert_nd2_mip_is_identity_and_preserves_channel(fake_nd2_path, tmp_path):
    path, fake = fake_nd2_path
    out = tmp_path / "out"
    core.convert_nd2(str(path), core.ConvertOptions(
        output_dir=str(out), experiment="EXP", pocillo="A",
        exclude_channels_0based=[], projection="mip"))
    root = out / "EXP" / "A"
    # Posición 0, canal 3: el MIP debe ser max sobre Z del canal 3 REAL del fake.
    saved = tifffile.imread(str(root / "EXP-NKG2D pocilloA.nd2 - Image001 - C=3.tif"))
    plane = saved if saved.ndim == 2 else saved[0]
    expected = fake._arr[0, :, 3, :, :].max(axis=0)  # pos0, canal3, max Z
    assert saved.dtype == np.uint16
    assert np.array_equal(plane, expected)
    # max(axis=0) del TIFF guardado es identidad (1 solo plano).
    assert plane.max() == expected.max()


def test_convert_nd2_zstack_preserves_z(fake_nd2_path, tmp_path):
    path, fake = fake_nd2_path
    out = tmp_path / "outz"
    core.convert_nd2(str(path), core.ConvertOptions(
        output_dir=str(out), experiment="EXP", pocillo="A",
        exclude_channels_0based=[], projection="none"))
    root = out / "EXP" / "A"
    arr = tifffile.imread(str(root / "EXP-NKG2D pocilloA.nd2 - Image002 - C=2.tif"))
    assert arr.shape[0] == 3                                   # Z conservado
    assert np.array_equal(arr, fake._arr[1, :, 2, :, :])       # pos1, canal2, (Z,Y,X)


def test_convert_nd2_series_subset(fake_nd2_path, tmp_path):
    path, _ = fake_nd2_path
    out = tmp_path / "outs"
    summary = core.convert_nd2(str(path), core.ConvertOptions(
        output_dir=str(out), experiment="EXP", pocillo="A",
        exclude_channels_0based=[], projection="mip",
        series_indices=[1, 3]))
    root = Path(summary["output_root"])
    tifs = sorted(p.name for p in root.iterdir() if p.suffix == ".tif")
    labels = sorted({n.split(" - ")[1] for n in tifs})
    assert labels == ["Image002", "Image004"]      # índices P preservados (no renumerados)
    assert summary["series_written"] == 2


# ---------------------------------------------------------------------------
# 3. Golden manifest (contrato con el worker)
# ---------------------------------------------------------------------------

def test_convert_nd2_manifest_schema(fake_nd2_path, tmp_path):
    path, _ = fake_nd2_path
    out = tmp_path / "outm"
    summary = core.convert_nd2(str(path), core.ConvertOptions(
        output_dir=str(out), experiment="EXP", pocillo="A",
        exclude_channels_0based=[1, 4], projection="mip"))
    m = json.loads(Path(summary["manifest_path"]).read_text(encoding="utf-8"))

    assert set(m.keys()) == {
        "app_version", "conversion_mode", "source_filename", "source_sha256",
        "experiment", "pocillo", "projection", "excluded_channels", "files",
    }
    assert m["app_version"] == core.APP_VERSION
    assert m["conversion_mode"] == "nd2_to_mip"
    assert m["source_filename"] == "EXP-NKG2D pocilloA.nd2"
    assert len(m["source_sha256"]) == 64
    assert m["projection"] == "mip"
    assert m["excluded_channels"] == [1, 4]                # 0-indexados, ordenados

    # 4 posiciones × (5 − 2) = 12 entradas
    assert len(m["files"]) == 12
    sample = m["files"][0]
    assert set(sample.keys()) == {
        "series_index", "image_label", "channel", "lut_name", "filename",
        "pixel_size_um", "pixel_size_um_y", "pixel_size_source",
    }
    assert sample["image_label"].startswith("Image")
    assert sample["filename"].endswith(".tif")
    assert sample["pixel_size_um"] == pytest.approx(0.284)
    assert sample["pixel_size_source"] == "nd2_voxel"
    # canal NUNCA renumerado tras excluir: quedan {0,2,3}
    assert sorted({f["channel"] for f in m["files"]}) == [0, 2, 3]
    # lut_name = nombre de canal por índice 0-based
    by_ch = {f["channel"]: f["lut_name"] for f in m["files"]}
    assert by_ch[0] == "APC" and by_ch[2] == "MitoTracker" and by_ch[3] == "MVB"


def test_convert_nd2_zstack_manifest_mode(fake_nd2_path, tmp_path):
    path, _ = fake_nd2_path
    out = tmp_path / "outzm"
    summary = core.convert_nd2(str(path), core.ConvertOptions(
        output_dir=str(out), experiment="EXP", pocillo="A",
        exclude_channels_0based=[], projection="none"))
    m = json.loads(Path(summary["manifest_path"]).read_text(encoding="utf-8"))
    assert m["conversion_mode"] == "nd2_to_zstack"
    assert m["projection"] == "none"


# ---------------------------------------------------------------------------
# 4. Preflight dimensional (errores tipados)
# ---------------------------------------------------------------------------

def test_preflight_rejects_timelapse(tmp_path, monkeypatch):
    fake = FakeNd2({"P": 1, "T": 2, "Z": 2, "C": 2, "Y": 4, "X": 5},
                   loops=("TimeLoop",))
    path = tmp_path / "t.nd2"; path.write_bytes(b"X")
    _patch_nd2(monkeypatch, fake)
    with pytest.raises(core.Nd2PreflightError, match="time-lapse|temporal"):
        core.read_nd2_info(str(path))


def test_preflight_rejects_rgb(tmp_path, monkeypatch):
    fake = FakeNd2({"P": 1, "Z": 2, "C": 1, "Y": 4, "X": 5}, is_rgb=True)
    path = tmp_path / "rgb.nd2"; path.write_bytes(b"X")
    _patch_nd2(monkeypatch, fake)
    with pytest.raises(core.Nd2PreflightError, match="RGB"):
        core.read_nd2_info(str(path))


def test_preflight_rejects_unknown_axis(tmp_path, monkeypatch):
    fake = FakeNd2({"P": 1, "M": 2, "Z": 2, "C": 2, "Y": 4, "X": 5})
    path = tmp_path / "m.nd2"; path.write_bytes(b"X")
    _patch_nd2(monkeypatch, fake)
    with pytest.raises(core.Nd2PreflightError, match="no soportado"):
        core.read_nd2_info(str(path))


def test_preflight_rejects_float_dtype(tmp_path, monkeypatch):
    fake = FakeNd2({"P": 1, "Z": 2, "C": 2, "Y": 4, "X": 5}, dtype=np.float32)
    path = tmp_path / "f.nd2"; path.write_bytes(b"X")
    _patch_nd2(monkeypatch, fake)
    with pytest.raises(core.Nd2PreflightError, match="píxel|pixel"):
        core.read_nd2_info(str(path))


def test_preflight_singleton_axes_pass(tmp_path, monkeypatch):
    # T=1 (singleton) NO debe abortar: se colapsa sin ambigüedad.
    fake = FakeNd2({"P": 2, "T": 1, "Z": 2, "C": 2, "Y": 4, "X": 5},
                   loops=("XYPosLoop",))
    path = tmp_path / "t1.nd2"; path.write_bytes(b"X")
    _patch_nd2(monkeypatch, fake)
    info, _ = core.read_nd2_info(str(path))
    assert info.n_series == 2


# ---------------------------------------------------------------------------
# 5. Pixel size fuera de rango → no fiable
# ---------------------------------------------------------------------------

def test_pixel_size_out_of_range_unavailable(tmp_path, monkeypatch, caplog):
    # voxel 5 µm/px cae fuera de [0.02, 2.0] → marcado no fiable.
    fake = FakeNd2({"P": 1, "Z": 2, "C": 2, "Y": 4, "X": 5}, voxel=(5.0, 5.0, 1.0))
    path = tmp_path / "big.nd2"; path.write_bytes(b"X")
    _patch_nd2(monkeypatch, fake)
    with caplog.at_level("WARNING", logger="core"):
        info, _ = core.read_nd2_info(str(path))
    assert info.series[0].pixel_size_um is None
    assert info.series[0].pixel_size_source == "unavailable"


# ---------------------------------------------------------------------------
# 6. Robustez de orden de ejes (nd2 puede variar el orden de sizes)
# ---------------------------------------------------------------------------

def test_axis_order_robustness(tmp_path, monkeypatch):
    # Orden atípico con T=1 intercalado: el indexado por nombre debe seguir dando
    # el canal/posición/Z correctos.
    fake = FakeNd2({"P": 2, "T": 1, "C": 2, "Z": 3, "Y": 4, "X": 5},
                   loops=("XYPosLoop",))
    path = tmp_path / "ord.nd2"; path.write_bytes(b"X")
    _patch_nd2(monkeypatch, fake)
    out = tmp_path / "o"
    core.convert_nd2(str(path), core.ConvertOptions(
        output_dir=str(out), experiment="E", pocillo="P",
        exclude_channels_0based=[], projection="none"))
    root = out / "E" / "P"
    arr = tifffile.imread(str(root / "ord.nd2 - Image001 - C=1.tif"))
    # pos0, canal1, todos los Z (índice por nombre, no por posición en sizes)
    expected = np.take(np.take(fake._arr, 0, axis=list(fake.sizes).index("P")),
                       0, axis=0)  # colapsa P y luego T(=1)
    # expected ahora tiene ejes [C, Z, Y, X]; seleccionamos C=1
    expected = expected[1]  # (Z, Y, X)
    assert arr.shape == expected.shape
    assert np.array_equal(arr, expected)
