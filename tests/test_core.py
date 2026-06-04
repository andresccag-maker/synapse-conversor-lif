"""Tests del núcleo de SYN APSE — Conversor LIF.

No usa .lif reales: monkeypatchea core._open_lif con un FakeLifFile.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections import namedtuple
from pathlib import Path

import numpy as np
import pytest
import tifffile

import core


# ---------------------------------------------------------------------------
# 1. Helpers puros
# ---------------------------------------------------------------------------

def test_suggest_experiment_pocillo():
    assert core.suggest_experiment_pocillo("Exp113 Pocillo2.lif") == ("Exp113", "Pocillo2")
    # Doble espacio: documenta la fragilidad. "Exp113  Pocillo 2.lif" → split por
    # " " da ["Exp113", "", "Pocillo", "2"]; len>=2 → ("Exp113", "").
    assert core.suggest_experiment_pocillo("Exp113 Pocillo 2.lif") == ("Exp113", "Pocillo")
    assert core.suggest_experiment_pocillo("solo.lif") == ("solo", "General")


def test_sanitize_filename_preserves_spaces_and_dash_and_equals():
    # Espacios, "=", "-" se preservan tal cual.
    assert core.sanitize_filename("EXP51 POCILLO 8 - C=2") == "EXP51 POCILLO 8 - C=2"
    # Separadores de ruta y caracteres de control → "_".
    assert core.sanitize_filename("a/b\\c") == "a_b_c"
    assert core.sanitize_filename("x\x00y\x1fz") == "x_y_z"
    # Otros caracteres "raros" se preservan (a diferencia del antiguo sanitize_component).
    assert core.sanitize_filename("hola: mundo*?") == "hola: mundo*?"
    assert core.sanitize_filename("") == "untitled"
    assert core.sanitize_filename(None) == "untitled"


def test_bioformats_channel_filename():
    fn = core.bioformats_channel_filename("Exp113 P2.lif", 0, 0)
    assert fn == "Exp113 P2.lif - Image001 - C=0.tif"
    # Series 1-indexed con padding 3, canal 0-indexado.
    assert core.bioformats_channel_filename("X.lif", 15, 3) == "X.lif - Image016 - C=3.tif"
    # Separadores de ruta en el nombre del .lif se sanean, espacios se preservan.
    assert (
        core.bioformats_channel_filename("a/b c.lif", 0, 1)
        == "a_b c.lif - Image001 - C=1.tif"
    )


def test_project_mip_shape_and_dtype():
    stack = np.zeros((5, 8, 8), dtype=np.uint16)
    stack[2, 3, 3] = 1234
    stack[4, 3, 3] = 4321
    mip = core.project_mip(stack)
    assert mip.shape == (1, 8, 8)
    assert mip.dtype == np.uint16
    assert mip[0, 3, 3] == 4321


# ---------------------------------------------------------------------------
# 2. LUTs desde XML simulado
# ---------------------------------------------------------------------------

def test_read_channel_luts_from_simulated_xml():
    xml = """<Root>
      <Data>
        <Image>
          <ImageDescription>
            <Channels>
              <ChannelDescription LUTName="Red" />
              <ChannelDescription LUTName="Green" />
              <ChannelDescription LUTName="Gray" />
              <ChannelDescription LUTName="Unknown" />
              <ChannelDescription />
            </Channels>
          </ImageDescription>
        </Image>
        <Image>
          <ImageDescription>
            <Channels>
              <ChannelDescription LUTName="ShouldBeIgnored" />
            </Channels>
          </ImageDescription>
        </Image>
      </Data>
    </Root>"""
    root = ET.fromstring(xml)

    class FakeLif:
        xml_root = root

    luts = core.read_channel_luts(FakeLif())
    assert len(luts) == 5  # primer set, no el del segundo Image
    assert luts[0]["name"] == "Red"
    assert tuple(luts[0]["rgb"]) == (255, 0, 0)
    assert luts[1]["name"] == "Green"
    assert tuple(luts[1]["rgb"]) == (0, 255, 0)
    assert luts[2]["name"] == "Gray"
    assert tuple(luts[2]["rgb"]) == (200, 200, 200)
    assert luts[3]["name"] == "Unknown"
    assert tuple(luts[3]["rgb"]) == (200, 200, 200)  # desconocido → gris por defecto
    assert luts[4]["name"] == ""
    assert tuple(luts[4]["rgb"]) == (200, 200, 200)  # sin LUTName → gris

    # Sin xml_root → lista vacía.
    class NoXml:
        xml_root = None

    assert core.read_channel_luts(NoXml()) == []


# ---------------------------------------------------------------------------
# 3. Fakes para LifFile / LifImage
# ---------------------------------------------------------------------------

Dims = namedtuple("Dims", ["x", "y", "z", "t", "m"])


class FakeLifImage:
    def __init__(self, index: int, name: str, w: int, h: int, nz: int,
                 channels: int, bit_depth=(16,), scale=(10.0, 10.0, 1.0, 0.0)):
        self.index = index
        self.name = name
        self.nz = nz
        self.nt = 1
        self.channels = channels
        self.dims = Dims(x=w, y=h, z=nz, t=1, m=1)
        self.bit_depth = tuple(bit_depth) * channels if len(bit_depth) == 1 else tuple(bit_depth)
        self.scale = scale
        self.settings = {}
        self._w = w
        self._h = h

    def get_frame(self, z=0, t=0, c=0, m=0):
        y_idx = np.arange(self._h, dtype=np.uint16)[:, None]
        x_idx = np.arange(self._w, dtype=np.uint16)[None, :]
        plane = (
            (self.index + 1) * 1000
            + (c + 1) * 100
            + (z + 1) * 10
            + (y_idx + x_idx).astype(np.uint16)
        ).astype(np.uint16)
        return plane


class FakeLifFile:
    def __init__(self, images, xml_root=None):
        self._images = list(images)
        self.xml_root = xml_root

    def get_iter_image(self):
        return iter(self._images)


def _make_fake_lif(n_series=4, channels=5, nz=3, w=12, h=10):
    images = [
        FakeLifImage(i, f"Serie{i+1}", w, h, nz, channels)
        for i in range(n_series)
    ]
    return FakeLifFile(images)


@pytest.fixture
def fake_lif_path(tmp_path, monkeypatch):
    fake = _make_fake_lif()
    path = tmp_path / "Exp113 P2.lif"
    path.write_bytes(b"FAKE-LIF-CONTENT")
    monkeypatch.setattr(core, "_open_lif", lambda p: fake)
    return path


# ---------------------------------------------------------------------------
# 4. convert: salida APLANADA + naming Bio-Formats + exclusión 0-indexada
# ---------------------------------------------------------------------------

def test_convert_flat_output_bioformats_excludes_0based(fake_lif_path, tmp_path):
    out = tmp_path / "out"
    opts = core.ConvertOptions(
        output_dir=str(out),
        experiment="Exp113",
        pocillo="Pocillo 2",  # con espacio: debe preservarse
        exclude_channels_0based=[2],  # excluye C=2 (equivale al antiguo C3)
        projection="mip",
    )
    summary = core.convert(str(fake_lif_path), opts)

    root = Path(summary["output_root"])
    assert root.exists()
    # Carpeta del pocillo preserva espacios.
    assert root.name == "Pocillo 2"
    assert root.parent.name == "Exp113"

    # NO debe haber subcarpetas ImagenNNN/ImageNNN.
    subdirs = [p for p in root.iterdir() if p.is_dir()]
    assert subdirs == []

    # 4 series × (5 canales − 1 excluido) = 16 ficheros + _manifest.json
    tifs = sorted(p.name for p in root.iterdir() if p.suffix == ".tif")
    assert len(tifs) == 16
    # Naming Bio-Formats: incluye ".lif" verbatim, 0-indexado para C, 1-indexado para Image.
    assert "Exp113 P2.lif - Image001 - C=0.tif" in tifs
    assert "Exp113 P2.lif - Image001 - C=1.tif" in tifs
    assert "Exp113 P2.lif - Image001 - C=3.tif" in tifs
    assert "Exp113 P2.lif - Image001 - C=4.tif" in tifs
    # C=2 está excluido en TODAS las series.
    for name in tifs:
        assert " - C=2.tif" not in name
    # Series 4 (idx=3) → Image004.
    assert "Exp113 P2.lif - Image004 - C=0.tif" in tifs

    # El MIP guardado coincide con max manual del stack reproducido.
    fake = core._open_lif(str(fake_lif_path))
    limg = list(fake.get_iter_image())[0]
    manual_stack = np.stack(
        [np.asarray(limg.get_frame(z=z, c=0)) for z in range(limg.nz)],
        axis=0,
    )
    manual_mip = manual_stack.max(axis=0)
    saved = tifffile.imread(str(root / "Exp113 P2.lif - Image001 - C=0.tif"))
    saved_2d = saved.reshape(manual_mip.shape) if saved.ndim != 2 else saved
    assert saved.dtype == np.uint16
    assert np.array_equal(saved_2d, manual_mip)

    assert summary["series_written"] == 4
    assert summary["files_written"] == 16


def test_convert_zstack_preserves_z_flat(fake_lif_path, tmp_path):
    out = tmp_path / "out_z"
    opts = core.ConvertOptions(
        output_dir=str(out),
        experiment="ExpZ",
        pocillo="Pz",
        exclude_channels_0based=[],
        projection="none",
    )
    summary = core.convert(str(fake_lif_path), opts)
    root = Path(summary["output_root"])
    # Sin subcarpetas.
    assert [p for p in root.iterdir() if p.is_dir()] == []
    arr = tifffile.imread(str(root / "Exp113 P2.lif - Image001 - C=0.tif"))
    assert arr.shape[0] == 3  # nz=3, sin proyección


def test_convert_series_subset_flat(fake_lif_path, tmp_path):
    out = tmp_path / "out_sub"
    opts = core.ConvertOptions(
        output_dir=str(out),
        experiment="ExpS",
        pocillo="Ps",
        exclude_channels_0based=[],
        projection="mip",
        series_indices=[1, 3],  # series idx 1 y 3 → Image002 e Image004
    )
    summary = core.convert(str(fake_lif_path), opts)
    root = Path(summary["output_root"])
    tifs = sorted(p.name for p in root.iterdir() if p.suffix == ".tif")
    # 2 series × 5 canales = 10
    assert len(tifs) == 10
    image_labels = sorted({n.split(" - ")[1] for n in tifs})
    assert image_labels == ["Image002", "Image004"]
    assert summary["series_written"] == 2


def test_convert_writes_manifest(fake_lif_path, tmp_path):
    out = tmp_path / "out_man"
    opts = core.ConvertOptions(
        output_dir=str(out),
        experiment="ExpM",
        pocillo="Pm",
        exclude_channels_0based=[1, 4],
        projection="mip",
    )
    summary = core.convert(str(fake_lif_path), opts)
    root = Path(summary["output_root"])

    manifest_path = root / "_manifest.json"
    assert manifest_path.exists()
    assert summary["manifest_path"] == str(manifest_path)

    m = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert m["app_version"] == core.APP_VERSION
    assert m["source_filename"] == "Exp113 P2.lif"
    assert len(m["source_sha256"]) == 64
    assert m["experiment"] == "ExpM"
    assert m["pocillo"] == "Pm"
    assert m["projection"] == "mip"
    assert m["excluded_channels"] == [1, 4]  # 0-indexados

    # files: 4 series × (5 − 2) = 12 entradas
    assert len(m["files"]) == 12
    sample = m["files"][0]
    assert set(sample.keys()) == {
        "series_index", "image_label", "channel", "lut_name", "filename",
        "pixel_size_um", "pixel_size_um_y", "pixel_size_source",
    }
    assert sample["image_label"].startswith("Image")
    assert sample["filename"].endswith(".tif")
    # lut_name vacío porque el fake no tiene xml_root.
    assert sample["lut_name"] == ""
    # Fake scale=(10,10,...) px/µm → µm/px = 0.1 (dentro del rango confocal).
    assert sample["pixel_size_um"] == pytest.approx(0.1)
    assert sample["pixel_size_um_y"] == pytest.approx(0.1)
    assert sample["pixel_size_source"] == "lif_scale"
    # En todos los ficheros del manifest, el canal está en {0,2,3}.
    channels_seen = sorted({f["channel"] for f in m["files"]})
    assert channels_seen == [0, 2, 3]


# ---------------------------------------------------------------------------
# 5. Pixel size: tags TIFF + sanity check + fallback gracioso
# ---------------------------------------------------------------------------

def test_tiff_embeds_resolution_tags(fake_lif_path, tmp_path):
    """Con scale válido en el fake (10 px/µm → 0.1 µm/px), el TIFF debe
    llevar ResolutionUnit=CENTIMETER y XResolution tal que 1e4/X ≈ 0.1."""
    out = tmp_path / "out_res"
    opts = core.ConvertOptions(
        output_dir=str(out),
        experiment="ExpR",
        pocillo="Pr",
        exclude_channels_0based=[],
        projection="mip",
    )
    summary = core.convert(str(fake_lif_path), opts)
    root = Path(summary["output_root"])
    tif_path = root / "Exp113 P2.lif - Image001 - C=0.tif"
    with tifffile.TiffFile(str(tif_path)) as tf:
        tags = tf.pages[0].tags
        # Equivalente a int 3 — el usuario lo pide explícitamente.
        assert int(tags["ResolutionUnit"].value) == 3
        xr = tags["XResolution"].value
        xres = xr[0] / xr[1] if isinstance(xr, tuple) else float(xr)
        assert 1e4 / xres == pytest.approx(0.1, rel=1e-3)


def test_tiff_no_resolution_when_scale_unavailable(tmp_path, monkeypatch):
    """Sin scale: la conversión NO rompe, manifest pixel_size_um=null y el
    TIFF NO lleva tags de resolución."""
    fake = _make_fake_lif()
    # Forzar scale ausente en todas las series del fake.
    for img in fake._images:
        img.scale = None

    path = tmp_path / "NoScale.lif"
    path.write_bytes(b"FAKE")
    monkeypatch.setattr(core, "_open_lif", lambda p: fake)

    out = tmp_path / "out_nores"
    opts = core.ConvertOptions(
        output_dir=str(out),
        experiment="ExpN",
        pocillo="Pn",
        exclude_channels_0based=[],
        projection="mip",
    )
    summary = core.convert(str(path), opts)
    root = Path(summary["output_root"])
    m = json.loads((root / "_manifest.json").read_text(encoding="utf-8"))
    assert all(f["pixel_size_um"] is None for f in m["files"])
    assert all(f["pixel_size_um_y"] is None for f in m["files"])
    assert all(f["pixel_size_source"] == "unavailable" for f in m["files"])

    tif_path = root / m["files"][0]["filename"]
    with tifffile.TiffFile(str(tif_path)) as tf:
        tags = tf.pages[0].tags
        # Sin um_px: no escribimos tags de resolución (o ResolutionUnit=NONE).
        assert "XResolution" not in tags or int(tags["ResolutionUnit"].value) in (0, 1)


def test_pixel_size_out_of_range_is_marked_unavailable(tmp_path, monkeypatch, caplog):
    """Si la escala invertida cae fuera de [0.02, 2.0] µm/px (típicamente
    porque está al revés o es basura), tratamos el valor como no fiable y
    logueamos warning."""
    fake = _make_fake_lif()
    # scale=0.1 px/µm → µm/px = 10.0, fuera de rango.
    for img in fake._images:
        img.scale = (0.1, 0.1, 1.0, 0.0)

    path = tmp_path / "Weird.lif"
    path.write_bytes(b"FAKE")
    monkeypatch.setattr(core, "_open_lif", lambda p: fake)

    out = tmp_path / "out_weird"
    opts = core.ConvertOptions(
        output_dir=str(out),
        experiment="ExpW",
        pocillo="Pw",
        exclude_channels_0based=[],
        projection="mip",
    )
    with caplog.at_level("WARNING", logger="core"):
        summary = core.convert(str(path), opts)
    assert any("fuera de rango" in r.message for r in caplog.records)

    root = Path(summary["output_root"])
    m = json.loads((root / "_manifest.json").read_text(encoding="utf-8"))
    assert m["files"][0]["pixel_size_um"] is None
    assert m["files"][0]["pixel_size_source"] == "unavailable"
