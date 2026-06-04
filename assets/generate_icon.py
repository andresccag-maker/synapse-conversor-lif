"""Genera assets/icon.png (1024x1024 RGBA) y derivados para SYN APSE — Conversor LIF.

Contrato:
  - PNG RGBA con esquinas TRANSPARENTES (sin fondo blanco horneado).
  - Squircle iOS-style centrado con margen de seguridad del 10%.
  - Monograma "S" blanco sobre gradiente cian → azul oscuro.

Salidas:
  - assets/icon.png              (1024x1024 RGBA, fuente del .icns macOS y de todo lo demás)
  - assets/icon.ico              (multi-resolución Windows)
  - assets/favicon.ico           (16/32/48 para la pestaña de la web de descarga)
  - assets/apple-touch-icon.png  (180x180 para iOS/PWA)
  - assets/icon-source.png       (backup del icon.png anterior, una sola vez)

Para regenerar:
  .venv/bin/python assets/generate_icon.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ASSETS = Path(__file__).resolve().parent

SIZE = 1024
SAFE_MARGIN_RATIO = 0.10
SQUIRCLE_EXPONENT = 4.5  # iOS-like: 4 = redondeado, mayor = más cuadrado

COLOR_TOP = (14, 165, 233)    # #0EA5E9 cian biotech
COLOR_BOTTOM = (30, 58, 138)  # #1E3A8A azul profundo
LETTER_COLOR = (255, 255, 255, 255)
LETTER = "S"
LETTER_HEIGHT_RATIO = 0.62  # % del squircle que ocupa la altura del glifo

FONT_CANDIDATES = [
    # macOS
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    # Windows (CI: windows-latest)
    "C:\\Windows\\Fonts\\arialbd.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
    "C:\\Windows\\Fonts\\segoeuib.ttf",
    "C:\\Windows\\Fonts\\segoeui.ttf",
    # Linux (CI: ubuntu — fallback de seguridad aunque el icono no se genera ahí)
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


def squircle_mask(size: int, exponent: float) -> Image.Image:
    """Máscara L con squircle anti-aliased (superellipse |x/a|^n + |y/b|^n <= 1)."""
    a = size / 2.0
    yy, xx = np.meshgrid(np.arange(size), np.arange(size), indexing="ij")
    cx = cy = a - 0.5
    rx = (xx - cx) / a
    ry = (yy - cy) / a
    val = np.abs(rx) ** exponent + np.abs(ry) ** exponent
    edge = 0.04  # anchura del borde antialiased
    alpha = np.clip((1.0 + edge - val) / edge * 255.0, 0, 255).astype(np.uint8)
    # Sin `mode=`: deprecado en Pillow 13. Para uint8 2-D auto-detecta 'L'.
    return Image.fromarray(alpha)


def vertical_gradient(size: int, top: tuple, bottom: tuple) -> Image.Image:
    t = np.linspace(0, 1, size, dtype=np.float32).reshape(-1, 1)
    grad = np.zeros((size, size, 3), dtype=np.uint8)
    grad[..., 0] = (top[0] * (1 - t) + bottom[0] * t).astype(np.uint8)
    grad[..., 1] = (top[1] * (1 - t) + bottom[1] * t).astype(np.uint8)
    grad[..., 2] = (top[2] * (1 - t) + bottom[2] * t).astype(np.uint8)
    # Sin `mode=`: deprecado en Pillow 13. Para uint8 3-D (H,W,3) auto-detecta 'RGB'.
    return Image.fromarray(grad).convert("RGBA")


def find_font(target_height_px: int) -> ImageFont.FreeTypeFont:
    """Localiza una fuente sans bold del sistema y la pide al tamaño aproximado.

    El tamaño en puntos no es exactamente la altura del glifo; iteramos para
    acercarnos a target_height_px.
    """
    chosen_path = None
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            chosen_path = path
            break
    if chosen_path is None:
        # Fallar duro: el default bitmap de Pillow produce una "S" minúscula
        # ilegible y horneamos un icono malo silenciosamente. Mejor abortar.
        raise SystemExit(
            "[generate_icon] no encontré ninguna fuente del sistema en "
            f"{FONT_CANDIDATES!r} — añade la ruta correcta o instala una "
            "fuente como DejaVu/Liberation."
        )

    # Bisección simple: busca el font-size que da altura ~= target.
    lo, hi = 50, 2000
    best = lo
    while lo <= hi:
        mid = (lo + hi) // 2
        try:
            f = ImageFont.truetype(chosen_path, mid)
        except Exception:
            return ImageFont.load_default()
        left, top, right, bottom = f.getbbox(LETTER)
        h = bottom - top
        if h < target_height_px:
            best = mid
            lo = mid + 10
        else:
            hi = mid - 10
    return ImageFont.truetype(chosen_path, best)


def render_icon() -> Image.Image:
    safe_margin = int(SIZE * SAFE_MARGIN_RATIO)
    squircle_size = SIZE - 2 * safe_margin

    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))

    mask = squircle_mask(squircle_size, SQUIRCLE_EXPONENT)
    grad = vertical_gradient(squircle_size, COLOR_TOP, COLOR_BOTTOM)
    grad.putalpha(mask)
    canvas.paste(grad, (safe_margin, safe_margin), grad)

    target_h = int(squircle_size * LETTER_HEIGHT_RATIO)
    font = find_font(target_h)
    draw = ImageDraw.Draw(canvas)
    left, top, right, bottom = draw.textbbox((0, 0), LETTER, font=font)
    tw, th = right - left, bottom - top
    cx = SIZE / 2 - tw / 2 - left
    cy = SIZE / 2 - th / 2 - top
    draw.text((cx, cy), LETTER, fill=LETTER_COLOR, font=font)

    return canvas


def validate_alpha(img: Image.Image) -> None:
    """Aborta si el icono no es RGBA o si las esquinas no son transparentes."""
    if img.mode != "RGBA":
        raise SystemExit(f"icono generado no es RGBA (mode={img.mode})")
    arr = np.asarray(img)
    corners = [arr[0, 0, 3], arr[0, -1, 3], arr[-1, 0, 3], arr[-1, -1, 3]]
    if any(c > 8 for c in corners):
        raise SystemExit(f"esquinas opacas (alphas={corners}) — el squircle no recortó")


def main() -> None:
    icon_png = ASSETS / "icon.png"
    backup = ASSETS / "icon-source.png"
    if icon_png.exists() and not backup.exists():
        icon_png.rename(backup)
        print(f"[icon] backup → {backup.name}")

    img = render_icon()
    validate_alpha(img)
    img.save(icon_png, format="PNG", optimize=True)
    print(f"[icon] {icon_png.name}  ({img.size[0]}x{img.size[1]} RGBA)")

    ico_sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    img.save(ASSETS / "icon.ico", format="ICO", sizes=ico_sizes)
    print(f"[icon] icon.ico  ({len(ico_sizes)} resoluciones)")

    img.save(ASSETS / "favicon.ico", format="ICO", sizes=[(16, 16), (32, 32), (48, 48)])
    print(f"[icon] favicon.ico  (16/32/48)")

    img.resize((180, 180), Image.Resampling.LANCZOS).save(
        ASSETS / "apple-touch-icon.png", format="PNG", optimize=True
    )
    print(f"[icon] apple-touch-icon.png  (180x180)")


if __name__ == "__main__":
    main()
