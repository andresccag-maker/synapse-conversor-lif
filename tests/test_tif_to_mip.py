"""Tests del modo TIF → MIP del Conversor LIF.

Usa TIFFs sintéticos reales (escritos con tifffile), sin .lif ni readlif.
El árbol simula Experimento/Pocillo/<imagen>_c<N>.tif con Z-stacks por canal.
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
    """Escribe un Z-stack multipágina (nz, h, w) con un MIP conocido y
    espacialmente variable. Devuelve el stack para comparar."""
    y_idx = np.arange(h)[:, None]
    x_idx = np.arange(w)[None, :]
    stack = np.zeros((nz, h, w), dtype=dtype)
    for z in range(nz):
        stack[z] = (base_val + z + (y_idx + x_idx)).astype(dtype)
    kwargs = {}
    if pixel_size_um is not None:
        kwargs["resolution"] = (1e4 / pixel_size_um, 1e4 / pixel_size_um)
        kwargs["resolutionunit"] = "CENTIMETER"
        kwargs["metadata"] = {"unit": "um"}
        kwargs["imagej"] = True
    else:
        # Z-stack multipágina real (un plano gris por página), no RGB-separado.
        kwargs["photometric"] = "minisblack"
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(path), stack, **kwargs)
    return stack


def _make_tree(root: Path, exp="Exp51", pocillo="Pocillo 8",
               images=("imagen1", "imagen2"), channels=(1, 2), **kw) -> dict:
    """Crea root/exp/pocillo/<imagen>_c<N>.tif. Devuelve {(img,ch): stack}."""
    stacks = {}
    for img in images:
        for ch in channels:
            p = root / exp / pocillo / f"{img}_c{ch}.tif"
            stacks[(img, ch)] = _write_zstack(p, **kw)
    return stacks


# ---------------------------------------------------------------------------
# scan_tif_folder
# ---------------------------------------------------------------------------

def test_scan_tif_folder_counts_and_derivation(tmp_path):
    root = tmp_path / "in"
    _make_tree(root)  # 1 exp · 1 pocillo · 2 imágenes · canales 1,2 → 4 ficheros
    scan = core.scan_tif_folder(str(root))
    assert scan.n_experiments == 1
    assert scan.n_pocillos == 1
    assert scan.n_images == 2
    assert scan.n_files == 4
    assert scan.raw_channels == [1, 2]
    rec = scan.files[0]
    assert rec["experiment"] == "Exp51"
    assert rec["pocillo"] == "Pocillo 8"
    assert rec["image_stem"] in ("imagen1", "imagen2")
    assert rec["raw_channel"] in (1, 2)


def test_scan_ignores_non_tif_and_manifest(tmp_path):
    root = tmp_path / "in"
    _make_tree(root, images=("imagen1",), channels=(1,))
    (root / "Exp51" / "Pocillo 8" / "_manifest.json").write_text("{}", encoding="utf-8")
    (root / "Exp51" / "Pocillo 8" / "notas.txt").write_text("hola", encoding="utf-8")
    scan = core.scan_tif_folder(str(root))
    assert scan.n_files == 1


# ---------------------------------------------------------------------------
# convert_tif_folder: naming canónico + MIP + estructura
# ---------------------------------------------------------------------------

def test_convert_canonical_naming_and_mip(tmp_path):
    root = tmp_path / "in"
    stacks = _make_tree(root)
    out = tmp_path / "out"
    summary = core.convert_tif_folder(
        core.TifFolderOptions(input_dir=str(root), output_dir=str(out))
    )

    pocillo_dir = out / "Exp51" / "Pocillo 8"
    assert pocillo_dir.exists()
    # Estructura espejo, sin subcarpetas dentro del pocillo.
    assert [p for p in pocillo_dir.iterdir() if p.is_dir()] == []

    tifs = sorted(p.name for p in pocillo_dir.iterdir() if p.suffix == ".tif")
    assert len(tifs) == 4
    # base = nombre del pocillo; canal _c1→C=0, _c2→C=1; imagen1→Image001, imagen2→Image002.
    assert "Pocillo 8 - Image001 - C=0.tif" in tifs
    assert "Pocillo 8 - Image001 - C=1.tif" in tifs
    assert "Pocillo 8 - Image002 - C=0.tif" in tifs
    assert "Pocillo 8 - Image002 - C=1.tif" in tifs

    # MIP de imagen1/c1 = max sobre Z del stack original, forma (1, Y, X).
    saved = tifffile.imread(str(pocillo_dir / "Pocillo 8 - Image001 - C=0.tif"))
    manual_mip = stacks[("imagen1", 1)].max(axis=0)
    saved_2d = saved.reshape(manual_mip.shape) if saved.ndim != 2 else saved
    assert np.array_equal(saved_2d, manual_mip)

    assert summary["files_written"] == 4
    assert summary["pocillos_written"] == 1
    assert summary["experiments_written"] == 1


def test_mip_is_single_plane(tmp_path):
    root = tmp_path / "in"
    _make_tree(root, images=("imagen1",), channels=(1,), nz=5)
    out = tmp_path / "out"
    core.convert_tif_folder(core.TifFolderOptions(input_dir=str(root), output_dir=str(out)))
    saved = tifffile.imread(str(out / "Exp51" / "Pocillo 8" / "Pocillo 8 - Image001 - C=0.tif"))
    # MIP colapsa Z: o (Y,X) 2D o (1,Y,X). Nunca conserva los 5 planos.
    assert saved.ndim == 2 or saved.shape[0] == 1


def test_channel_normalization_zero_based_input(tmp_path):
    """Entrada ya 0-based (c0, c1) → C=0, C=1 (min raw = 0)."""
    root = tmp_path / "in"
    _make_tree(root, images=("img",), channels=(0, 1))
    out = tmp_path / "out"
    core.convert_tif_folder(core.TifFolderOptions(input_dir=str(root), output_dir=str(out)))
    tifs = sorted(p.name for p in (out / "Exp51" / "Pocillo 8").iterdir() if p.suffix == ".tif")
    assert "Pocillo 8 - Image001 - C=0.tif" in tifs
    assert "Pocillo 8 - Image001 - C=1.tif" in tifs


def test_base_name_override(tmp_path):
    root = tmp_path / "in"
    _make_tree(root, images=("img",), channels=(1,))
    out = tmp_path / "out"
    core.convert_tif_folder(
        core.TifFolderOptions(input_dir=str(root), output_dir=str(out), base_name="EXP51")
    )
    tifs = [p.name for p in (out / "Exp51" / "Pocillo 8").iterdir() if p.suffix == ".tif"]
    assert tifs == ["EXP51 - Image001 - C=0.tif"]


def test_2d_input_is_handled(tmp_path):
    """Un .tif 2D (sin Z) → 'MIP' es identidad (1, Y, X)."""
    root = tmp_path / "in" / "Exp" / "Poc"
    root.mkdir(parents=True)
    plane = np.arange(6 * 8, dtype=np.uint16).reshape(6, 8)
    tifffile.imwrite(str(root / "img_c1.tif"), plane)
    out = tmp_path / "out"
    core.convert_tif_folder(
        core.TifFolderOptions(input_dir=str(tmp_path / "in"), output_dir=str(out))
    )
    saved = tifffile.imread(str(out / "Exp" / "Poc" / "Poc - Image001 - C=0.tif"))
    saved_2d = saved.reshape(plane.shape) if saved.ndim != 2 else saved
    assert np.array_equal(saved_2d, plane)


# ---------------------------------------------------------------------------
# Manifest + pixel size round-trip
# ---------------------------------------------------------------------------

def test_manifest_fields(tmp_path):
    root = tmp_path / "in"
    _make_tree(root)
    out = tmp_path / "out"
    core.convert_tif_folder(core.TifFolderOptions(input_dir=str(root), output_dir=str(out)))
    m = json.loads((out / "Exp51" / "Pocillo 8" / "_manifest.json").read_text(encoding="utf-8"))
    assert m["app_version"] == core.APP_VERSION
    assert m["conversion_mode"] == "tif_to_mip"
    assert m["projection"] == "mip"
    assert m["excluded_channels"] == []
    assert m["experiment"] == "Exp51"
    assert m["pocillo"] == "Pocillo 8"
    assert len(m["files"]) == 4
    f = m["files"][0]
    # Superset del manifest LIF: campos núcleo + trazabilidad TIF.
    for key in ("series_index", "image_label", "channel", "lut_name", "filename",
                "pixel_size_um", "pixel_size_um_y", "pixel_size_source",
                "source_tif", "source_sha256", "source_channel_raw"):
        assert key in f
    assert len(f["source_sha256"]) == 64
    assert f["lut_name"] == ""
    assert f["source_tif"].endswith(".tif")


def test_pixel_size_roundtrip_from_input_tiff(tmp_path):
    """Si el TIFF de entrada lleva tags de resolución (cm), el MIP los preserva."""
    root = tmp_path / "in"
    _make_tree(root, images=("img",), channels=(1,), pixel_size_um=0.1)
    out = tmp_path / "out"
    core.convert_tif_folder(core.TifFolderOptions(input_dir=str(root), output_dir=str(out)))

    pocillo_dir = out / "Exp51" / "Pocillo 8"
    m = json.loads((pocillo_dir / "_manifest.json").read_text(encoding="utf-8"))
    f = m["files"][0]
    assert f["pixel_size_um"] == pytest.approx(0.1, rel=1e-3)
    assert f["pixel_size_source"] == "tiff_resolution"

    with tifffile.TiffFile(str(pocillo_dir / f["filename"])) as tf:
        tags = tf.pages[0].tags
        assert int(tags["ResolutionUnit"].value) == 3
        xr = tags["XResolution"].value
        xres = xr[0] / xr[1] if isinstance(xr, tuple) else float(xr)
        assert 1e4 / xres == pytest.approx(0.1, rel=1e-3)


def test_pixel_size_absent_when_input_has_no_tags(tmp_path):
    root = tmp_path / "in"
    _make_tree(root, images=("img",), channels=(1,))  # sin pixel_size_um
    out = tmp_path / "out"
    core.convert_tif_folder(core.TifFolderOptions(input_dir=str(root), output_dir=str(out)))
    m = json.loads((out / "Exp51" / "Pocillo 8" / "_manifest.json").read_text(encoding="utf-8"))
    f = m["files"][0]
    assert f["pixel_size_um"] is None
    assert f["pixel_size_source"] == "unavailable"


def test_empty_folder_raises(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError):
        core.convert_tif_folder(
            core.TifFolderOptions(input_dir=str(tmp_path / "empty"), output_dir=str(tmp_path / "o"))
        )
