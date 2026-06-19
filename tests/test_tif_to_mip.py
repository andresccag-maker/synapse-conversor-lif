"""Tests del modo TIF → MIP del Conversor LIF.

TIFFs sintéticos reales (tifffile). El modo TIF→MIP hace el MIP de cada Z-stack
ESPEJANDO la estructura de carpetas; conserva el nombre si ya es canónico
("{base} - Image{NNN} - C={c}.tif") y lo re-deriva si es estilo "..._c{N}".
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import tifffile

import core


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_zstack(path: Path, nz=4, h=6, w=8, base_val=100, dtype=np.uint16,
                  pixel_size_um=None) -> np.ndarray:
    """Z-stack multipágina (nz, h, w) con MIP conocido. Devuelve el stack."""
    y = np.arange(h)[:, None]
    x = np.arange(w)[None, :]
    stack = np.zeros((nz, h, w), dtype=dtype)
    for z in range(nz):
        stack[z] = (base_val + z + (y + x)).astype(dtype)
    kwargs = {}
    if pixel_size_um is not None:
        kwargs["resolution"] = (1e4 / pixel_size_um, 1e4 / pixel_size_um)
        kwargs["resolutionunit"] = "CENTIMETER"
        kwargs["metadata"] = {"unit": "um"}
        kwargs["imagej"] = True
    else:
        kwargs["photometric"] = "minisblack"
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(path), stack, **kwargs)
    return stack


def _canon(base, img, ch):
    return f"{base} - Image{img:03d} - C={ch}.tif"


# ---------------------------------------------------------------------------
# scan_tif_folder
# ---------------------------------------------------------------------------

def test_scan_counts_and_canonical(tmp_path):
    root = tmp_path / "TIF"
    poc = root / "POCILLO_1"
    for img in (3, 22):
        for ch in (0, 1):
            _write_zstack(poc / _canon("Exp.lif", img, ch))
    scan = core.scan_tif_folder(str(root))
    assert scan.n_files == 4
    assert scan.n_pocillos == 1            # una carpeta con TIFFs
    assert scan.n_images == 2              # Image003, Image022
    assert sorted(scan.raw_channels) == [0, 1]
    rec = scan.files[0]
    assert rec["canonical"] is True
    assert rec["rel_parent"] == "POCILLO_1"


def test_scan_nonexistent_folder(tmp_path):
    scan = core.scan_tif_folder(str(tmp_path / "nope"))
    assert scan.n_files == 0


# ---------------------------------------------------------------------------
# Conservar nombre canónico + espejar estructura + MIP
# ---------------------------------------------------------------------------

def test_canonical_preserved_and_mirrored(tmp_path):
    root = tmp_path / "TIF"
    stacks = {}
    for img in (3, 22):
        for ch in (0, 1):
            stacks[(img, ch)] = _write_zstack(root / "POCILLO_1" / _canon("Exp.lif", img, ch), nz=5)
    out = tmp_path / "MIP"
    summary = core.convert_tif_folder(core.TifFolderOptions(input_dir=str(root), output_dir=str(out)))

    pdir = out / "POCILLO_1"
    assert pdir.exists()
    names = sorted(p.name for p in pdir.iterdir() if p.suffix == ".tif")
    # nombres canónicos CONSERVADOS verbatim, estructura POCILLO_1 espejada
    assert _canon("Exp.lif", 3, 0) in names
    assert _canon("Exp.lif", 22, 1) in names
    assert len(names) == 4

    # MIP de un plano = max sobre Z del stack original
    saved = tifffile.imread(str(pdir / _canon("Exp.lif", 3, 0)))
    manual = stacks[(3, 0)].max(axis=0)
    saved2d = saved.reshape(manual.shape) if saved.ndim != 2 else saved
    assert saved.ndim == 2 or saved.shape[0] == 1
    assert np.array_equal(saved2d, manual)

    assert summary["files_written"] == 4
    assert summary["pocillos_written"] == 1


def test_channel_suffix_rederived(tmp_path):
    """Entrada estilo "..._c{N}" (no canónica) → se re-deriva al naming worker."""
    root = tmp_path / "in"
    for img in ("img1",):
        for ch in (1, 2):
            _write_zstack(root / "POCILLO_1" / f"{img}_c{ch}.tif")
    out = tmp_path / "out"
    core.convert_tif_folder(core.TifFolderOptions(input_dir=str(root), output_dir=str(out)))
    names = sorted(p.name for p in (out / "POCILLO_1").iterdir() if p.suffix == ".tif")
    # base = nombre de la carpeta; canal _c1→C=0, _c2→C=1
    assert "POCILLO_1 - Image001 - C=0.tif" in names
    assert "POCILLO_1 - Image001 - C=1.tif" in names


def test_mirror_multiple_pocillos(tmp_path):
    root = tmp_path / "TIF"
    _write_zstack(root / "POCILLO_1" / _canon("Exp.lif", 1, 0))
    _write_zstack(root / "POCILLO_3" / _canon("Exp.lif", 1, 0))
    out = tmp_path / "MIP"
    summary = core.convert_tif_folder(core.TifFolderOptions(input_dir=str(root), output_dir=str(out)))
    assert (out / "POCILLO_1" / _canon("Exp.lif", 1, 0)).exists()
    assert (out / "POCILLO_3" / _canon("Exp.lif", 1, 0)).exists()
    assert summary["pocillos_written"] == 2


def test_files_in_root_go_flat(tmp_path):
    """TIFFs en la raíz elegida (sin subcarpeta) → salida directa en output_dir."""
    root = tmp_path / "POCILLO_1"
    _write_zstack(root / _canon("Exp.lif", 1, 0))
    out = tmp_path / "out"
    core.convert_tif_folder(core.TifFolderOptions(input_dir=str(root), output_dir=str(out)))
    assert (out / _canon("Exp.lif", 1, 0)).exists()


def test_2d_input_handled(tmp_path):
    root = tmp_path / "in"
    root.mkdir()
    plane = np.arange(6 * 8, dtype=np.uint16).reshape(6, 8)
    tifffile.imwrite(str(root / _canon("Exp.lif", 1, 0)), plane, photometric="minisblack")
    out = tmp_path / "out"
    core.convert_tif_folder(core.TifFolderOptions(input_dir=str(root), output_dir=str(out)))
    saved = tifffile.imread(str(out / _canon("Exp.lif", 1, 0)))
    saved2d = saved.reshape(plane.shape) if saved.ndim != 2 else saved
    assert np.array_equal(saved2d, plane)


# ---------------------------------------------------------------------------
# Manifest + pixel size + errores
# ---------------------------------------------------------------------------

def test_manifest_fields(tmp_path):
    root = tmp_path / "TIF"
    _write_zstack(root / "POCILLO_1" / _canon("Exp.lif", 3, 0))
    _write_zstack(root / "POCILLO_1" / _canon("Exp.lif", 3, 1))
    out = tmp_path / "MIP"
    core.convert_tif_folder(core.TifFolderOptions(input_dir=str(root), output_dir=str(out)))
    m = json.loads((out / "POCILLO_1" / "_manifest.json").read_text(encoding="utf-8"))
    assert m["app_version"] == core.APP_VERSION
    assert m["conversion_mode"] == "tif_to_mip"
    assert m["projection"] == "mip"
    assert len(m["files"]) == 2
    f = m["files"][0]
    for key in ("image_label", "channel", "filename", "pixel_size_um",
                "pixel_size_source", "source_tif", "source_sha256", "preserved_name"):
        assert key in f
    assert f["preserved_name"] is True
    assert len(f["source_sha256"]) == 64


def test_pixel_size_roundtrip(tmp_path):
    root = tmp_path / "TIF"
    _write_zstack(root / "POCILLO_1" / _canon("Exp.lif", 1, 0), pixel_size_um=0.1)
    out = tmp_path / "MIP"
    core.convert_tif_folder(core.TifFolderOptions(input_dir=str(root), output_dir=str(out)))
    m = json.loads((out / "POCILLO_1" / "_manifest.json").read_text(encoding="utf-8"))
    assert m["files"][0]["pixel_size_um"] == pytest.approx(0.1, rel=1e-3)
    assert m["files"][0]["pixel_size_source"] == "tiff_resolution"


def test_empty_folder_raises(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError):
        core.convert_tif_folder(
            core.TifFolderOptions(input_dir=str(tmp_path / "empty"), output_dir=str(tmp_path / "o"))
        )
