"""SYN APSE — Conversor LIF: núcleo de conversión.

Funciona en modo headless. No importa pywebview.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import tifffile
from PIL import Image

APP_VERSION = "0.5.0"

logger = logging.getLogger(__name__)

# Rango confocal típico para detectar lecturas absurdas o invertidas
# en el campo `scale` de readlif (px/µm).
PIXEL_SIZE_UM_MIN = 0.02
PIXEL_SIZE_UM_MAX = 2.0


# ---------------------------------------------------------------------------
# LUT name → RGB
# ---------------------------------------------------------------------------

LUT_RGB: dict = {
    "red":     (255,   0,   0),
    "green":   (  0, 255,   0),
    "blue":    (  0, 128, 255),
    "cyan":    (  0, 255, 255),
    "magenta": (255,   0, 255),
    "yellow":  (255, 255,   0),
    "gray":    (200, 200, 200),
    "grey":    (200, 200, 200),
}
DEFAULT_LUT_RGB = (200, 200, 200)


def _lut_rgb_for(name) -> tuple:
    if not name:
        return DEFAULT_LUT_RGB
    return LUT_RGB.get(str(name).strip().lower(), DEFAULT_LUT_RGB)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SeriesInfo:
    index: int
    name: str
    width: int
    height: int
    n_z: int
    n_t: int
    n_channels: int
    bit_depth: list
    pixel_size_um: Optional[float]
    pixel_size_um_y: Optional[float] = None
    pixel_size_source: str = "unavailable"


@dataclass
class LifInfo:
    path: str
    filename: str
    sha256: str
    n_series: int
    suggested_experiment: str
    suggested_pocillo: str
    series: list = field(default_factory=list)
    channel_luts: list = field(default_factory=list)  # [{"name": str, "rgb": [r,g,b]}]


@dataclass
class ConvertOptions:
    output_dir: str
    experiment: str
    pocillo: str
    exclude_channels_0based: list
    projection: str  # "mip" | "none"
    series_indices: Optional[list] = None


# ---------------------------------------------------------------------------
# Helpers puros
# ---------------------------------------------------------------------------

def sha256_of_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def suggest_experiment_pocillo(filename: str) -> tuple[str, str]:
    stem = Path(filename).stem
    parts = stem.split(" ")
    if len(parts) >= 2:
        return parts[0], parts[1]
    return stem, "General"


# Preserva espacios, "=", "-" y otros caracteres normales. Solo reemplaza
# separadores de ruta (`/`, `\`) y caracteres de control por "_". Usado para
# nombres de carpeta y de fichero del nuevo contrato Bio-Formats.
_SANITIZE_FILENAME_RE = re.compile(r"[\\/\x00-\x1f]")


def sanitize_filename(name) -> str:
    if name is None:
        return "untitled"
    out = _SANITIZE_FILENAME_RE.sub("_", str(name))
    return out if out else "untitled"


def bioformats_channel_filename(lif_filename: str, series_index_0based: int,
                                channel_0based: int) -> str:
    """{nombre_lif} - Image{NNN} - C={c}.tif  (Image 1-indexed, C 0-indexed)."""
    base = sanitize_filename(lif_filename)
    return f"{base} - Image{series_index_0based + 1:03d} - C={channel_0based}.tif"


def project_mip(stack_zyx: np.ndarray) -> np.ndarray:
    if stack_zyx.ndim != 3:
        raise ValueError(f"project_mip expects 3D (Z,Y,X), got shape {stack_zyx.shape}")
    mip = np.max(stack_zyx, axis=0)
    return mip[np.newaxis, ...].astype(stack_zyx.dtype, copy=False)


def make_preview_png_b64(stack_zyx: np.ndarray, rgb: Optional[tuple] = None,
                         max_side: int = 256) -> str:
    """MIP normalizado (percentiles 1/99.5 solo para display).

    Si se pasa `rgb=(r,g,b)`, se devuelve PNG RGB coloreado por el LUT.
    Si no, PNG en escala de grises (modo "L").
    """
    if stack_zyx.ndim == 2:
        plane = stack_zyx
    elif stack_zyx.ndim == 3:
        plane = np.max(stack_zyx, axis=0)
    else:
        raise ValueError("preview requires 2D or 3D array")
    plane = np.asarray(plane, dtype=np.float32)
    lo = float(np.percentile(plane, 1.0))
    hi = float(np.percentile(plane, 99.5))
    if hi <= lo:
        hi = lo + 1.0
    norm = np.clip((plane - lo) / (hi - lo), 0.0, 1.0)

    if rgb is None:
        img = Image.fromarray((norm * 255.0).astype(np.uint8), mode="L")
    else:
        r, g, b = (float(c) for c in rgb)
        rgb_arr = np.stack([
            (norm * r).astype(np.uint8),
            (norm * g).astype(np.uint8),
            (norm * b).astype(np.uint8),
        ], axis=-1)
        img = Image.fromarray(rgb_arr, mode="RGB")

    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return "data:image/png;base64," + b64


# ---------------------------------------------------------------------------
# Acceso a readlif (aislado para tests)
# ---------------------------------------------------------------------------

def _open_lif(path: str):
    from readlif.reader import LifFile
    return LifFile(path)


def _series_pixel_scale(limg) -> tuple[Optional[float], Optional[float], str]:
    """Devuelve (um_px_x, um_px_y, source) leyendo `LifImage.scale` de readlif.

    `scale` está documentado como px/µm (x, y, z, t), así que µm/px = 1/scale.
    Sanity check: el valor debe caer en [PIXEL_SIZE_UM_MIN, PIXEL_SIZE_UM_MAX]
    (rango confocal típico). Si no, se marca como no fiable y se loguea
    warning — no escribimos basura en el TIFF.
    """
    try:
        scale = limg.scale
    except Exception:
        logger.debug("limg.scale lanzó excepción; pixel size no disponible")
        return None, None, "unavailable"

    if not scale:
        logger.debug("limg.scale vacío/None; pixel size no disponible")
        return None, None, "unavailable"

    def _invert(raw) -> Optional[float]:
        try:
            sv = float(raw)
        except (TypeError, ValueError):
            return None
        if sv <= 0:
            return None
        return 1.0 / sv

    sx_raw = scale[0] if len(scale) > 0 else None
    sy_raw = scale[1] if len(scale) > 1 else None

    um_x = _invert(sx_raw)
    um_y = _invert(sy_raw)
    if um_x is None:
        logger.warning("scale[0]=%r no es un px/µm válido; pixel size no disponible", sx_raw)
        return None, None, "unavailable"
    if um_y is None:
        um_y = um_x

    out_of_range = (
        not (PIXEL_SIZE_UM_MIN <= um_x <= PIXEL_SIZE_UM_MAX)
        or not (PIXEL_SIZE_UM_MIN <= um_y <= PIXEL_SIZE_UM_MAX)
    )
    if out_of_range:
        logger.warning(
            "pixel size fuera de rango confocal [%g, %g] µm/px "
            "(um_x=%g, um_y=%g, scale_raw=%r); marcado como no fiable",
            PIXEL_SIZE_UM_MIN, PIXEL_SIZE_UM_MAX, um_x, um_y, scale,
        )
        return None, None, "unavailable"

    return um_x, um_y, "lif_scale"


def _series_pixel_size_um(limg) -> Optional[float]:
    """Back-compat: devuelve solo µm/px en X (o None)."""
    um_x, _, _ = _series_pixel_scale(limg)
    return um_x


def _read_channel_stack(limg, c: int, t: int = 0, m: int = 0) -> np.ndarray:
    planes = []
    nz = int(getattr(limg, "nz", 1) or 1)
    for z in range(nz):
        frame = limg.get_frame(z=z, t=t, c=c, m=m)
        planes.append(np.asarray(frame))
    return np.stack(planes, axis=0)


def read_channel_luts(lif) -> list:
    """LUTs por canal extraídos del XML del .lif.

    Busca el PRIMER conjunto de
       .//Data/Image/ImageDescription/Channels/ChannelDescription
    (los colores son iguales para todas las series de un mismo .lif).

    Devuelve [{"name": <LUTName o "">, "rgb": [r, g, b]}, ...].
    Si no hay xml_root o el XML no tiene canales, devuelve [].
    """
    luts: list = []
    root = getattr(lif, "xml_root", None)
    if root is None:
        return luts
    try:
        for img in root.iter("Image"):
            channels_node = img.find("ImageDescription/Channels")
            if channels_node is None:
                continue
            descriptions = channels_node.findall("ChannelDescription")
            if not descriptions:
                continue
            for desc in descriptions:
                lut_name = desc.get("LUTName") or ""
                luts.append({
                    "name": lut_name,
                    "rgb": list(_lut_rgb_for(lut_name)),
                })
            return luts  # primer conjunto encontrado gana
    except Exception:
        pass
    return luts


def read_lif_info(path: str, with_previews: bool = False) -> tuple[LifInfo, dict]:
    p = Path(path)
    lif = _open_lif(path)
    series_infos: list = []
    previews: dict = {}
    channel_luts = read_channel_luts(lif)

    for idx, limg in enumerate(lif.get_iter_image()):
        dims = limg.dims
        width = int(getattr(dims, "x", 0) or 0)
        height = int(getattr(dims, "y", 0) or 0)
        n_z = int(getattr(limg, "nz", getattr(dims, "z", 1)) or 1)
        n_t = int(getattr(limg, "nt", getattr(dims, "t", 1)) or 1)
        n_channels = int(getattr(limg, "channels", 1) or 1)
        bit_depth_raw = getattr(limg, "bit_depth", ())
        try:
            bit_depth = [int(b) for b in bit_depth_raw]
        except Exception:
            bit_depth = []
        um_x, um_y, px_source = _series_pixel_scale(limg)
        series_infos.append(SeriesInfo(
            index=idx,
            name=str(getattr(limg, "name", f"Series{idx}")),
            width=width,
            height=height,
            n_z=n_z,
            n_t=n_t,
            n_channels=n_channels,
            bit_depth=bit_depth,
            pixel_size_um=um_x,
            pixel_size_um_y=um_y,
            pixel_size_source=px_source,
        ))

        if with_previews:
            previews.setdefault(idx, {})
            for c in range(n_channels):
                try:
                    stack = _read_channel_stack(limg, c=c)
                    lut = channel_luts[c] if c < len(channel_luts) else None
                    rgb = tuple(lut["rgb"]) if lut else None
                    previews[idx][c] = make_preview_png_b64(stack, rgb=rgb)
                except Exception:
                    previews[idx][c] = None

    sugg_exp, sugg_pocillo = suggest_experiment_pocillo(p.name)
    info = LifInfo(
        path=str(p),
        filename=p.name,
        sha256=sha256_of_file(path),
        n_series=len(series_infos),
        suggested_experiment=sugg_exp,
        suggested_pocillo=sugg_pocillo,
        series=series_infos,
        channel_luts=channel_luts,
    )
    return info, previews


# ---------------------------------------------------------------------------
# Escritura TIFF + convert principal
# ---------------------------------------------------------------------------

def _save_tiff(
    out_path: Path,
    arr: np.ndarray,
    bit_depth: Optional[int],
    pixel_size_um: Optional[float] = None,
    pixel_size_um_y: Optional[float] = None,
) -> None:
    target_dtype = np.uint8 if (bit_depth is not None and bit_depth <= 8) else np.uint16
    if arr.dtype != target_dtype:
        arr = arr.astype(target_dtype, copy=False)

    kwargs: dict = {"imagej": True}
    if pixel_size_um is not None:
        um_y = pixel_size_um_y if pixel_size_um_y is not None else pixel_size_um
        # ResolutionUnit=CENTIMETER (3); lectura inversa: µm/px = 1e4 / XResolution.
        kwargs["resolution"] = (1e4 / pixel_size_um, 1e4 / um_y)
        kwargs["resolutionunit"] = "CENTIMETER"
        # Para que Fiji muestre µm en Image > Properties al reabrir el TIFF.
        kwargs["metadata"] = {"unit": "um"}

    tifffile.imwrite(str(out_path), arr, **kwargs)


def convert(
    path: str,
    opts: ConvertOptions,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> dict:
    info, _ = read_lif_info(path, with_previews=False)
    lif = _open_lif(path)

    exp_safe = sanitize_filename(opts.experiment)
    pocillo_safe = sanitize_filename(opts.pocillo)
    output_root = Path(opts.output_dir) / exp_safe / pocillo_safe
    output_root.mkdir(parents=True, exist_ok=True)

    selected = opts.series_indices
    excluded = set(int(c) for c in (opts.exclude_channels_0based or []))

    all_series = list(lif.get_iter_image())
    if selected is not None:
        target_indices = [i for i in selected if 0 <= i < len(all_series)]
    else:
        target_indices = list(range(len(all_series)))

    total = len(target_indices)
    done = 0
    manifest_files: list = []
    details: list = []

    lif_filename = info.filename  # verbatim, incluye ".lif"

    for idx in target_indices:
        limg = all_series[idx]
        series_info = info.series[idx]
        image_label = f"Image{idx + 1:03d}"
        per_series_files: list = []

        um_x = series_info.pixel_size_um
        um_y = series_info.pixel_size_um_y
        px_source = series_info.pixel_size_source

        for c in range(series_info.n_channels):
            if c in excluded:
                continue
            stack = _read_channel_stack(limg, c=c)
            arr = project_mip(stack) if opts.projection == "mip" else stack
            bd = series_info.bit_depth[c] if c < len(series_info.bit_depth) else None
            fname = bioformats_channel_filename(lif_filename, idx, c)
            _save_tiff(
                output_root / fname, arr, bd,
                pixel_size_um=um_x,
                pixel_size_um_y=um_y,
            )

            lut_name = ""
            if c < len(info.channel_luts):
                lut_name = info.channel_luts[c].get("name", "") or ""

            manifest_files.append({
                "series_index": idx,
                "image_label": image_label,
                "channel": c,
                "lut_name": lut_name,
                "filename": fname,
                "pixel_size_um": um_x,
                "pixel_size_um_y": um_y,
                "pixel_size_source": px_source,
            })
            per_series_files.append(fname)

        details.append({
            "series_index": idx,
            "image_label": image_label,
            "channels_written": per_series_files,
        })

        done += 1
        if progress_cb is not None:
            try:
                progress_cb(done, total, f"{output_root} · {image_label}")
            except Exception:
                pass

    manifest = {
        "app_version": APP_VERSION,
        "source_filename": info.filename,
        "source_sha256": info.sha256,
        "experiment": opts.experiment,
        "pocillo": opts.pocillo,
        "projection": opts.projection,
        "excluded_channels": sorted(excluded),  # 0-indexados
        "files": manifest_files,
    }
    with open(output_root / "_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return {
        "experiment": opts.experiment,
        "pocillo": opts.pocillo,
        "output_root": str(output_root),
        "manifest_path": str(output_root / "_manifest.json"),
        "series_written": done,
        "files_written": len(manifest_files),
        "details": details,
    }


# ---------------------------------------------------------------------------
# Modo TIF → MIP (lote sobre carpetas Experimento/Pocillo/*.tif)
# ---------------------------------------------------------------------------
#
# Convierte TIFFs ya exportados (un canal por fichero, Z-stack multipágina)
# directamente a MIP, SIN volver al .lif. Reutiliza el mismo contrato de salida
# que LIF→MIP (mismas funciones project_mip/_save_tiff/bioformats_channel_filename
# + _manifest.json) para que el worker lo lea idéntico. Lo único nuevo es leer
# los TIFF de entrada y derivar serie/canal del nombre y carpetas.

# Sufijo de canal en el nombre del TIFF: "_c1", "_c2", "_ch0"... (case-insensitive)
_TIF_CHANNEL_RE = re.compile(r"_c[h]?(\d+)$", re.IGNORECASE)
_TIF_SUFFIXES = (".tif", ".tiff")


@dataclass
class TifFolderOptions:
    input_dir: str
    output_dir: str
    base_name: Optional[str] = None  # override del {base} en el nombre de salida
    recurse: bool = True


@dataclass
class TifScan:
    input_dir: str
    files: list = field(default_factory=list)  # records: path/rel/experiment/pocillo/image_stem/raw_channel
    n_experiments: int = 0
    n_pocillos: int = 0
    n_images: int = 0
    n_files: int = 0
    raw_channels: list = field(default_factory=list)  # canales crudos únicos (de los sufijos _cN)


def _natural_key(s: str) -> list:
    """Orden natural: imagen2 < imagen10 (no lexicográfico)."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(s))]


def _parse_image_and_channel(stem: str) -> tuple[str, Optional[int]]:
    """De 'imagen1_c2' → ('imagen1', 2). Sin sufijo → (stem, None)."""
    m = _TIF_CHANNEL_RE.search(stem)
    if m:
        return stem[: m.start()], int(m.group(1))
    return stem, None


def _read_tiff_zstack(path) -> np.ndarray:
    """Lee un TIFF (un canal) como (Z, Y, X). 2D → (1, Y, X). Aplana singletons."""
    arr = np.asarray(tifffile.imread(str(path)))
    if arr.ndim == 2:
        return arr[np.newaxis, ...]
    if arr.ndim == 3:
        return arr
    arr = np.squeeze(arr)
    if arr.ndim == 2:
        return arr[np.newaxis, ...]
    if arr.ndim == 3:
        return arr
    raise ValueError(
        f"TIFF con forma no soportada {arr.shape} en {path}; "
        "se esperaba un único canal (Z, Y, X)"
    )


def _bit_depth_for_dtype(dtype) -> int:
    return 8 if np.dtype(dtype) == np.uint8 else 16


def _read_tiff_pixel_size(path) -> tuple[Optional[float], Optional[float], str]:
    """Inverso de _save_tiff: lee XResolution/YResolution si ResolutionUnit=cm.

    µm/px = 1e4 / XResolution (cuando ResolutionUnit == 3 = CENTIMETER).
    Sanity-check en rango confocal; si falta o es absurdo, devuelve None.
    """
    try:
        with tifffile.TiffFile(str(path)) as tf:
            tags = tf.pages[0].tags
            unit_tag = tags.get("ResolutionUnit")
            xres_tag = tags.get("XResolution")
            yres_tag = tags.get("YResolution")
            if xres_tag is None or unit_tag is None:
                return None, None, "unavailable"
            if int(getattr(unit_tag, "value", 0) or 0) != 3:  # solo cm es interpretable aquí
                return None, None, "unavailable"

            def _to_float(v) -> Optional[float]:
                if isinstance(v, (tuple, list)) and len(v) == 2 and v[1]:
                    return float(v[0]) / float(v[1])
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None

            xr = _to_float(getattr(xres_tag, "value", None))
            yr = _to_float(getattr(yres_tag, "value", None)) if yres_tag is not None else None
            if not xr or xr <= 0:
                return None, None, "unavailable"
            um_x = 1e4 / xr
            um_y = (1e4 / yr) if (yr and yr > 0) else um_x
            if not (PIXEL_SIZE_UM_MIN <= um_x <= PIXEL_SIZE_UM_MAX):
                logger.warning(
                    "pixel size del TIFF %s fuera de rango [%g, %g] (um_x=%g); no fiable",
                    path, PIXEL_SIZE_UM_MIN, PIXEL_SIZE_UM_MAX, um_x,
                )
                return None, None, "unavailable"
            return um_x, um_y, "tiff_resolution"
    except Exception:
        logger.debug("no se pudo leer pixel size del TIFF %s", path, exc_info=True)
        return None, None, "unavailable"


def _derive_experiment_pocillo(rel_parts: tuple) -> tuple[str, str]:
    """De la ruta relativa al root deriva (Experimento, Pocillo).

    root/Exp/Pocillo/img.tif → (Exp, Pocillo). Con menos profundidad, degrada.
    """
    if len(rel_parts) >= 3:
        return rel_parts[-3], rel_parts[-2]
    if len(rel_parts) == 2:
        return "Experimento", rel_parts[-2]
    return "Experimento", "General"


# Detección (mejor-esfuerzo) de canal e imagen en el nombre — SOLO para metadata
# del manifest y el conteo. El nombre del fichero SIEMPRE se conserva en la salida
# (el MIP no debe renombrar). Reconoce las convenciones reales del usuario:
#   - sufijo:  "{base} - Image{NNN} - C={c}.tif"   (EXP109 / salida del worker)
#   - prefijo: "C{c}-{base} - Image{NNN}.tif"      (EXP116)
#   - sufijo:  "..._c{N}.tif"
_CHAN_SUFFIX_RE = re.compile(r" - Image(\d+) - C=(\d+)$")
_CHAN_PREFIX_RE = re.compile(r"^C(\d+)[ _-]")
_IMAGE_LABEL_RE = re.compile(r"[Ii]mage[n]?\s*(\d+)")


def _parse_tif_stem(stem: str) -> dict:
    """Extrae canal e imagen del nombre (mejor-esfuerzo, solo metadata)."""
    channel = None
    image_label = None
    m = _CHAN_SUFFIX_RE.search(stem)
    if m:
        channel = int(m.group(2))
        image_label = f"Image{int(m.group(1)):03d}"
    else:
        mp = _CHAN_PREFIX_RE.match(stem)
        if mp:
            channel = int(mp.group(1))
        else:
            _, raw = _parse_image_and_channel(stem)  # "..._c{N}"
            channel = raw
        mi = _IMAGE_LABEL_RE.search(stem)
        if mi:
            image_label = f"Image{int(mi.group(1)):03d}"
    return {
        "channel": channel,
        "image_label": image_label,
        "image_key": image_label or stem,
    }


def scan_tif_folder(input_dir: str, recurse: bool = True) -> TifScan:
    """Inventaría los TIFFs bajo input_dir (estructura, imágenes, canales).

    No lee píxeles ni calcula hashes (barato, apto para preview de UI).
    """
    root = Path(input_dir)
    if not root.exists():
        return TifScan(input_dir=str(root))
    if recurse:
        candidates = [p for p in root.rglob("*") if p.is_file()]
    else:
        candidates = [p for p in root.iterdir() if p.is_file()]

    records: list = []
    folders: set = set()
    images: set = set()
    raw_channels: set = set()

    for f in sorted(candidates, key=lambda p: _natural_key(str(p))):
        if f.suffix.lower() not in _TIF_SUFFIXES:
            continue
        if f.name == "_manifest.json":
            continue
        rel = f.relative_to(root)
        rel_parent = str(rel.parent)  # "." si el fichero está en la raíz elegida
        info = _parse_tif_stem(f.stem)
        records.append({
            "path": str(f),
            "rel": str(rel),
            "rel_parent": rel_parent,
            "name": f.name,
            "channel": info["channel"],
            "image_label": info["image_label"],
            "image_key": info["image_key"],
        })
        folders.add(rel_parent)
        images.add((rel_parent, info["image_key"]))
        if info["channel"] is not None:
            raw_channels.add(info["channel"])

    return TifScan(
        input_dir=str(root),
        files=records,
        n_experiments=len(folders),       # nº de carpetas con TIFFs (pocillos)
        n_pocillos=len(folders),
        n_images=len(images),             # distintas imágenes (Image{NNN} / stem)
        n_files=len(records),
        raw_channels=sorted(raw_channels),
    )


def convert_tif_folder(
    opts: TifFolderOptions,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> dict:
    """Hace el MIP de cada TIFF (Z-stack) bajo input_dir, CONSERVANDO el nombre del
    fichero y ESPEJANDO la estructura de carpetas en output_dir.

    El MIP no renombra: solo colapsa el Z-stack a 1 plano. Así funciona con
    cualquier convención de nombres ("- C={c}", "C{c}-...", "..._c{N}", etc.).
    """
    scan = scan_tif_folder(opts.input_dir, recurse=opts.recurse)
    if scan.n_files == 0:
        raise ValueError(f"No se encontraron TIFFs (.tif/.tiff) bajo {opts.input_dir}")

    # Agrupa por carpeta relativa (cada pocillo se procesa por separado).
    groups: dict = {}
    for rec in scan.files:
        groups.setdefault(rec["rel_parent"], []).append(rec)

    total = scan.n_files
    done = 0
    details: list = []
    folders_written = 0

    for rel_parent, recs in groups.items():
        # Espeja la estructura: si el TIFF está en la raíz elegida, va directo a
        # output_dir; si está en subcarpeta (pocillo), se replica esa subcarpeta.
        output_root = Path(opts.output_dir) if rel_parent == "." else Path(opts.output_dir) / rel_parent
        output_root.mkdir(parents=True, exist_ok=True)

        manifest_files: list = []
        per_folder_files: list = []

        for rec in sorted(recs, key=lambda r: _natural_key(r["name"])):
            src = rec["path"]
            stack = _read_tiff_zstack(src)
            mip = project_mip(stack)
            bd = _bit_depth_for_dtype(stack.dtype)
            um_x, um_y, px_source = _read_tiff_pixel_size(src)

            fname = rec["name"]   # SIEMPRE conservar el nombre de entrada

            _save_tiff(
                output_root / fname, mip, bd,
                pixel_size_um=um_x,
                pixel_size_um_y=um_y,
            )

            manifest_files.append({
                "image_label": rec["image_label"],
                "channel": rec["channel"],
                "filename": fname,
                "pixel_size_um": um_x,
                "pixel_size_um_y": um_y,
                "pixel_size_source": px_source,
                "source_tif": rec["rel"],
                "source_sha256": sha256_of_file(src),
                "preserved_name": True,
            })
            per_folder_files.append(fname)

            done += 1
            if progress_cb is not None:
                try:
                    progress_cb(done, total, f"{output_root} · {fname}")
                except Exception:
                    pass

        manifest = {
            "app_version": APP_VERSION,
            "conversion_mode": "tif_to_mip",
            "source_dir": str(Path(opts.input_dir)),
            "folder": rel_parent,
            "projection": "mip",
            "excluded_channels": [],
            "files": manifest_files,
        }
        with open(output_root / "_manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        folders_written += 1
        details.append({
            "folder": rel_parent,
            "output_root": str(output_root),
            "files_written": per_folder_files,
        })

    return {
        "input_dir": str(Path(opts.input_dir)),
        "output_dir": str(Path(opts.output_dir)),
        "experiments_written": folders_written,
        "pocillos_written": folders_written,
        "files_written": done,
        "details": details,
    }


# ---------------------------------------------------------------------------
# Modo ND2 → TIFF / MIP (microscopía Nikon)
# ---------------------------------------------------------------------------
#
# Tercer modo, hermano de LIF→MIP. Lee ficheros Nikon .nd2 y produce EXACTAMENTE
# el mismo contrato de salida que convert() (naming Bio-Formats, estructura
# aplanada {Exp}/{Pocillo}/, _manifest.json) para que cell-analyzer-worker lo
# consuma idéntico. Solo cambian: el lector (`nd2` en vez de `readlif`), la
# iteración de "series" (= posiciones XY del ND2) y un preflight dimensional
# estricto.
#
# Requisito de corrección #1: preservar el índice de canal REAL (0-based, en el
# orden de adquisición, SIN reordenar) — el worker mapea rol→canal por ese índice
# (columna channel_map en BD). "Todo C=0" o renumerar tras excluir rompería el
# análisis aguas abajo.

# Ejes que sabemos mapear sin ambigüedad. Cualquier otro con tamaño > 1 aborta.
_ND2_SUPPORTED_AXES = ("P", "Z", "C", "Y", "X")
# Límite del formato TIFF clásico (sin BigTIFF). Un z-stack de salida mayor exige
# BigTIFF → decisión humana (§ preflight); por defecto se rechaza con mensaje claro.
_TIFF_CLASSIC_LIMIT_BYTES = 4 * 1024 ** 3


class Nd2Error(ValueError):
    """Base de errores ND2. Subclase de ValueError para captura back-compatible."""


class Nd2PreflightError(Nd2Error):
    """Estructura ND2 no soportada (T>1, RGB, eje desconocido, dtype, BigTIFF)."""


class Nd2ReadError(Nd2Error):
    """Fallo al abrir/leer el ND2 con la librería `nd2`."""


def _open_nd2(path: str):
    """Aísla la importación de `nd2` (fakeable en tests, como `_open_lif`)."""
    try:
        import nd2
    except Exception as exc:  # pragma: no cover - entorno sin la dependencia
        raise Nd2ReadError(
            "El lector de ND2 (paquete `nd2`) no está disponible en este entorno."
        ) from exc
    try:
        return nd2.ND2File(path)
    except Exception as exc:
        raise Nd2ReadError(f"No se pudo abrir el ND2: {exc}") from exc


def _nd2_close(f) -> None:
    try:
        close = getattr(f, "close", None)
        if callable(close):
            close()
    except Exception:
        pass


def _nd2_sizes(f) -> dict:
    """`f.sizes` normalizado a claves en mayúscula (P/T/Z/C/Y/X)."""
    raw = dict(getattr(f, "sizes", {}) or {})
    return {str(k).upper(): int(v) for k, v in raw.items()}


def _nd2_axis_order(f) -> list:
    return [str(a).upper() for a in getattr(f, "sizes", {}).keys()]


def _nd2_experiment_axis_types(f) -> set:
    """Tipos de loop del experimento ('TimeLoop', 'NETimeLoop', 'XYPosLoop'...)."""
    types: set = set()
    try:
        for loop in (getattr(f, "experiment", None) or []):
            t = getattr(loop, "type", None)
            if t:
                types.add(str(t))
    except Exception:
        pass
    return types


def _nd2_preflight(f, sizes: dict) -> None:
    """Valida que el ND2 se convierte SIN ambigüedad. Lanza Nd2PreflightError.

    Reglas (núcleo headless — nunca diálogos interactivos aquí; la GUI/CLI traduce
    la excepción a mensaje legible):
      - Solo ejes P (posición XY), Z, C (canal), Y, X. Cualquier otro con tamaño
        > 1 (M/V/U/custom) → abortar, nunca colapsar en silencio.
      - T > 1 (time-lapse): detectado por loop (TimeLoop/NETimeLoop) y por tamaño
        → no soportado.
      - RGB / componentes de color (`is_rgb`) → rechazado (no partir en canales
        biológicos).
      - dtype: entero sin signo de 8 o 16 bits. Los 12-bit de Nikon llegan
        desempaquetados como uint16 → se PRESERVAN sin normalizar ni desplazar.
    """
    if bool(getattr(f, "is_rgb", False)):
        raise Nd2PreflightError(
            "El ND2 es RGB (imagen en color). No se parte en canales biológicos "
            "automáticamente; expórtalo como multicanal en escala de grises."
        )

    exp_types = _nd2_experiment_axis_types(f)
    for axis, size in sizes.items():
        a = str(axis).upper()
        if a in _ND2_SUPPORTED_AXES:
            continue
        if int(size or 1) <= 1:
            continue  # eje presente pero singleton → se colapsa sin ambigüedad
        if a == "T":
            raise Nd2PreflightError(
                f"El ND2 tiene una serie temporal (T={size}). El conversor no "
                "soporta time-lapse; separa los tiempos antes de convertir."
            )
        raise Nd2PreflightError(
            f"El ND2 tiene un eje no soportado '{axis}' (tamaño {size}). Solo se "
            "soportan posición (P), Z, canal (C) y las dimensiones XY."
        )
    if ({"TimeLoop", "NETimeLoop"} & exp_types) and int(sizes.get("T", 1) or 1) > 1:
        raise Nd2PreflightError(
            "El ND2 contiene un bucle temporal (time-lapse), no soportado."
        )

    dt = np.dtype(getattr(f, "dtype", np.uint16))
    if dt.kind != "u" or dt.itemsize > 2:
        raise Nd2PreflightError(
            f"Tipo de píxel no soportado ({dt}). Se esperaba entero sin signo de "
            "8 o 16 bits (los 12-bit de Nikon llegan como uint16)."
        )


def _nd2_significant_bits(f, dtype) -> int:
    """Bits significativos por canal → elige uint8/uint16 en `_save_tiff`."""
    try:
        b = int(getattr(getattr(f, "attributes", None),
                        "bitsPerComponentSignificant", 0) or 0)
    except Exception:
        b = 0
    if b <= 0:
        b = 8 * np.dtype(dtype).itemsize
    return b


def _nd2_channel_names(f, n_channels: int) -> list:
    """Nombres de canal (informativos). Pueden venir vacíos o DUPLICADOS — válido;
    el naming y las exclusiones usan SIEMPRE el índice C 0-based, nunca el nombre."""
    names: list = []
    try:
        md = getattr(f, "metadata", None)
        chans = getattr(md, "channels", None) or []
        for ch in chans:
            nm = getattr(getattr(ch, "channel", None), "name", None)
            names.append(str(nm) if nm else "")
    except Exception:
        names = []
    if len(names) < n_channels:
        names += [""] * (n_channels - len(names))
    return names[:n_channels]


def _nd2_voxel_um(f) -> tuple:
    """(um_x, um_y, source) desde `f.voxel_size()` con el mismo sanity-check confocal
    que el .lif. nd2 expone el voxel size a nivel de fichero (consistente entre
    posiciones por construcción)."""
    try:
        vs = f.voxel_size()
        vx = getattr(vs, "x", None)
        vy = getattr(vs, "y", None)
        um_x = float(vx) if vx else None
        um_y = float(vy) if vy else None
    except Exception:
        return None, None, "unavailable"
    if not um_x or um_x <= 0:
        return None, None, "unavailable"
    if not um_y or um_y <= 0:
        um_y = um_x
    if not (PIXEL_SIZE_UM_MIN <= um_x <= PIXEL_SIZE_UM_MAX) or \
       not (PIXEL_SIZE_UM_MIN <= um_y <= PIXEL_SIZE_UM_MAX):
        logger.warning(
            "pixel size ND2 fuera de rango confocal [%g, %g] µm/px (um_x=%g); "
            "marcado como no fiable", PIXEL_SIZE_UM_MIN, PIXEL_SIZE_UM_MAX, um_x,
        )
        return None, None, "unavailable"
    return um_x, um_y, "nd2_voxel"


def _nd2_to_lazy(f):
    """Array perezoso indexable por el orden de `f.sizes` (dask en real; en tests el
    fake puede devolver un ndarray, que se indexa igual)."""
    to_dask = getattr(f, "to_dask", None)
    if callable(to_dask):
        return to_dask()
    raise Nd2ReadError("El objeto ND2 no expone `to_dask()`.")


def _nd2_compute(sub) -> np.ndarray:
    """Materializa un chunk perezoso (dask) o pasa a ndarray (fake)."""
    return np.asarray(sub.compute() if hasattr(sub, "compute") else sub)


def _nd2_index(axes: list, pos: int, c: int, z=None) -> tuple:
    """Índice que selecciona (posición, canal[, z]) dejando Y/X completas.

    Ejes P/C se fijan al valor dado; Z: plano `z` o slice completo; Y/X: completos;
    cualquier otro eje (p. ej. T, ya garantizado singleton por el preflight) → 0.
    Los ejes ausentes en `axes` simplemente no aparecen (dimensión implícita)."""
    idx = []
    for a in axes:
        if a == "P":
            idx.append(pos)
        elif a == "C":
            idx.append(c)
        elif a == "Z":
            idx.append(slice(None) if z is None else z)
        elif a in ("Y", "X"):
            idx.append(slice(None))
        else:
            idx.append(0)
    return tuple(idx)


def _read_nd2_stack(lazy, axes: list, pos: int, c: int) -> np.ndarray:
    """(Z, Y, X) de una (posición, canal). Materializa un canal — SOLO para previews."""
    arr = _nd2_compute(lazy[_nd2_index(axes, pos, c)])
    if arr.ndim == 2:
        arr = arr[np.newaxis, ...]
    return arr


def _iter_nd2_planes(lazy, axes: list, pos: int, c: int, n_z: int):
    """Itera los planos (Y, X) del z-stack de una (posición, canal) SIN materializar
    todo el z-stack de golpe (memoria ≈ 1 plano)."""
    if "Z" not in axes:
        plane = _nd2_compute(lazy[_nd2_index(axes, pos, c)])
        yield np.squeeze(plane) if plane.ndim > 2 else plane
        return
    for z in range(n_z):
        plane = _nd2_compute(lazy[_nd2_index(axes, pos, c, z=z)])
        yield np.squeeze(plane) if plane.ndim > 2 else plane


def _nd2_mip_stream(lazy, axes: list, pos: int, c: int, n_z: int) -> np.ndarray:
    """MIP por acumulación plano a plano (np.maximum) → (1, Y, X). Memoria ≈ 2 planos.
    Equivale a project_mip(stack) pero sin cargar el z-stack completo."""
    acc = None
    for plane in _iter_nd2_planes(lazy, axes, pos, c, n_z):
        plane = np.asarray(plane)
        acc = plane if acc is None else np.maximum(acc, plane)
    if acc is None:
        raise Nd2ReadError("z-stack vacío al proyectar MIP.")
    return acc[np.newaxis, ...].astype(acc.dtype, copy=False)


def read_nd2_info(path: str, with_previews: bool = False) -> tuple[LifInfo, dict]:
    """Metadatos de un .nd2 (corre el preflight). Reutiliza LifInfo/SeriesInfo:
    cada "serie" es una posición XY (P). Devuelve (info, previews) como read_lif_info."""
    p = Path(path)
    f = _open_nd2(path)
    try:
        sizes = _nd2_sizes(f)
        _nd2_preflight(f, sizes)

        n_positions = int(sizes.get("P", 1) or 1)
        n_z = int(sizes.get("Z", 1) or 1)
        n_c = int(sizes.get("C", 1) or 1)
        n_t = int(sizes.get("T", 1) or 1)
        width = int(sizes.get("X", 0) or 0)
        height = int(sizes.get("Y", 0) or 0)

        dtype = np.dtype(getattr(f, "dtype", np.uint16))
        sig_bits = _nd2_significant_bits(f, dtype)
        um_x, um_y, px_source = _nd2_voxel_um(f)
        ch_names = _nd2_channel_names(f, n_c)
        channel_luts = [{"name": nm, "rgb": list(_lut_rgb_for(nm))} for nm in ch_names]

        series_infos: list = []
        previews: dict = {}
        lazy = _nd2_to_lazy(f) if with_previews else None
        axes = _nd2_axis_order(f)
        for pos in range(n_positions):
            series_infos.append(SeriesInfo(
                index=pos,
                name=f"P{pos + 1}",
                width=width,
                height=height,
                n_z=n_z,
                n_t=n_t,
                n_channels=n_c,
                bit_depth=[sig_bits] * n_c,
                pixel_size_um=um_x,
                pixel_size_um_y=um_y,
                pixel_size_source=px_source,
            ))
            if with_previews:
                previews.setdefault(pos, {})
                for c in range(n_c):
                    try:
                        stack = _read_nd2_stack(lazy, axes, pos, c)
                        lut = channel_luts[c] if c < len(channel_luts) else None
                        rgb = tuple(lut["rgb"]) if lut else None
                        previews[pos][c] = make_preview_png_b64(stack, rgb=rgb)
                    except Exception:
                        previews[pos][c] = None
    finally:
        _nd2_close(f)

    sugg_exp, sugg_pocillo = suggest_experiment_pocillo(p.name)
    info = LifInfo(
        path=str(p),
        filename=p.name,          # verbatim, incluye ".nd2"
        sha256=sha256_of_file(path),
        n_series=len(series_infos),
        suggested_experiment=sugg_exp,
        suggested_pocillo=sugg_pocillo,
        series=series_infos,
        channel_luts=channel_luts,
    )
    return info, previews


def convert_nd2(
    path: str,
    opts: ConvertOptions,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> dict:
    """Convierte un .nd2 al MISMO contrato de salida que convert() (LIF).

    Serie = posición XY (P) → Image{NNN} (índice P 0-based +1, pad-3). Por cada
    canal 0-based en orden de adquisición (saltando exclude_channels_0based):
    MIP → (1,Y,X) por streaming; z-stack completo → (Z,Y,X). Naming vía
    bioformats_channel_filename (source verbatim con `.nd2`). _manifest.json con el
    esquema de convert() + `conversion_mode` (nd2_to_mip / nd2_to_zstack)."""
    info, _ = read_nd2_info(path, with_previews=False)  # el preflight corre aquí
    f = _open_nd2(path)
    try:
        sizes = _nd2_sizes(f)
        axes = _nd2_axis_order(f)
        lazy = _nd2_to_lazy(f)
        n_z = int(sizes.get("Z", 1) or 1)
        dtype = np.dtype(getattr(f, "dtype", np.uint16))
        is_mip = opts.projection == "mip"

        exp_safe = sanitize_filename(opts.experiment)
        pocillo_safe = sanitize_filename(opts.pocillo)
        output_root = Path(opts.output_dir) / exp_safe / pocillo_safe
        output_root.mkdir(parents=True, exist_ok=True)

        excluded = set(int(c) for c in (opts.exclude_channels_0based or []))
        selected = opts.series_indices
        if selected is not None:
            target_indices = [i for i in selected if 0 <= i < info.n_series]
        else:
            target_indices = list(range(info.n_series))

        source_filename = info.filename  # verbatim, incluye ".nd2"
        total = len(target_indices)
        done = 0
        manifest_files: list = []
        details: list = []

        for pos in target_indices:
            series_info = info.series[pos]
            image_label = f"Image{pos + 1:03d}"
            um_x = series_info.pixel_size_um
            um_y = series_info.pixel_size_um_y
            px_source = series_info.pixel_size_source
            per_series_files: list = []

            for c in range(series_info.n_channels):
                if c in excluded:
                    continue
                bd = series_info.bit_depth[c] if c < len(series_info.bit_depth) else None
                fname = bioformats_channel_filename(source_filename, pos, c)

                if is_mip:
                    arr = _nd2_mip_stream(lazy, axes, pos, c, n_z)  # (1, Y, X) streaming
                else:
                    # z-stack completo: reconstruye (Z,Y,X) desde los planos. Memoria
                    # ≈ un canal (igual que LIF). Rechaza salidas > 4 GiB (BigTIFF =
                    # decisión humana) para no materializar volúmenes gigantes.
                    est_bytes = int(n_z) * int(series_info.height) * \
                        int(series_info.width) * int(dtype.itemsize)
                    if est_bytes > _TIFF_CLASSIC_LIMIT_BYTES:
                        raise Nd2PreflightError(
                            f"El z-stack de salida (~{est_bytes / 1024 ** 3:.1f} GiB, "
                            f"posición {pos}, canal {c}) supera 4 GiB y requeriría "
                            "BigTIFF. Confírmalo con el equipo o usa MIP."
                        )
                    planes = [np.asarray(pl) for pl in
                              _iter_nd2_planes(lazy, axes, pos, c, n_z)]
                    arr = np.stack(planes, axis=0)  # (Z, Y, X)

                _save_tiff(
                    output_root / fname, arr, bd,
                    pixel_size_um=um_x,
                    pixel_size_um_y=um_y,
                )

                lut_name = ""
                if c < len(info.channel_luts):
                    lut_name = info.channel_luts[c].get("name", "") or ""

                manifest_files.append({
                    "series_index": pos,
                    "image_label": image_label,
                    "channel": c,
                    "lut_name": lut_name,
                    "filename": fname,
                    "pixel_size_um": um_x,
                    "pixel_size_um_y": um_y,
                    "pixel_size_source": px_source,
                })
                per_series_files.append(fname)

            details.append({
                "series_index": pos,
                "image_label": image_label,
                "channels_written": per_series_files,
            })

            done += 1
            if progress_cb is not None:
                try:
                    progress_cb(done, total, f"{output_root} · {image_label}")
                except Exception:
                    pass

        manifest = {
            "app_version": APP_VERSION,
            "conversion_mode": "nd2_to_mip" if is_mip else "nd2_to_zstack",
            "source_filename": info.filename,
            "source_sha256": info.sha256,
            "experiment": opts.experiment,
            "pocillo": opts.pocillo,
            "projection": opts.projection,
            "excluded_channels": sorted(excluded),  # 0-indexados
            "files": manifest_files,
        }
        with open(output_root / "_manifest.json", "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)

        return {
            "experiment": opts.experiment,
            "pocillo": opts.pocillo,
            "output_root": str(output_root),
            "manifest_path": str(output_root / "_manifest.json"),
            "series_written": done,
            "files_written": len(manifest_files),
            "details": details,
        }
    finally:
        _nd2_close(f)
