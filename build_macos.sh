#!/usr/bin/env bash
# SYN APSE — Conversor LIF: build de .app para macOS (sin firma).
#
# Requisitos previos:
#   - .venv ya creado con `./run.sh` o `python3 -m venv .venv && pip install -r requirements.txt`
#   - assets/icon.png  CONTRATO:
#       * PNG 1024x1024 RGBA con esquinas TRANSPARENTES (sin fondo blanco horneado).
#       * Contenido dentro de squircle con safe area del 10%.
#       * Regenerable con `.venv/bin/python assets/generate_icon.py`.
#     El script aborta si no se cumple para evitar regresar al "icono en cuadrado blanco"
#     que aparecía en Dock/Launchpad/Finder antes del fix de v0.3.0.
#
# Salida:
#   dist/SYN APSE — Conversor LIF.app
#   dist/SYN APSE Conversor LIF v{version}.dmg  (si `create-dmg` está instalado)

set -euo pipefail

cd "$(dirname "$0")"

APP_NAME="SYN APSE — Conversor LIF"
BUNDLE_ID="com.axiom.synapse-conversor-lif"
ICON_SRC="assets/icon.png"
ICON_ICNS="assets/icon.icns"
ICONSET_DIR="assets/icon.iconset"

if [ ! -d ".venv" ]; then
  echo "[build] no existe .venv — creándolo"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "[build] instalando dependencias (incluye pyinstaller)"
pip install --upgrade pip >/dev/null
pip install -r requirements.txt >/dev/null
pip install "pyinstaller>=6.0" >/dev/null

if [ ! -f "$ICON_SRC" ]; then
  echo "[build] ERROR: falta $ICON_SRC — ejecuta primero: .venv/bin/python assets/generate_icon.py" >&2
  exit 1
fi

echo "[build] validando que $ICON_SRC es RGBA con esquinas transparentes"
python - <<'PY'
import sys
from pathlib import Path
from PIL import Image
import numpy as np

p = Path("assets/icon.png")
img = Image.open(p)
if img.mode != "RGBA":
    sys.exit(f"[icon-validate] {p} no es RGBA (mode={img.mode}). "
             f"Regenera con `assets/generate_icon.py` para obtener un PNG sin fondo blanco horneado.")
arr = np.asarray(img)
corners = [int(arr[0, 0, 3]), int(arr[0, -1, 3]), int(arr[-1, 0, 3]), int(arr[-1, -1, 3])]
if any(c > 8 for c in corners):
    sys.exit(f"[icon-validate] {p} tiene esquinas opacas (alphas={corners}). "
             f"El squircle no recorta el fondo — habrá cuadrado blanco en el Dock. "
             f"Regenera con `assets/generate_icon.py`.")
print(f"[icon-validate] OK  RGBA {img.size}, esquinas alpha={corners}")
PY

echo "[build] generando $ICON_ICNS a partir de $ICON_SRC"
rm -rf "$ICONSET_DIR" "$ICON_ICNS"
mkdir -p "$ICONSET_DIR"
sips -z 16 16     "$ICON_SRC" --out "$ICONSET_DIR/icon_16x16.png"      >/dev/null
sips -z 32 32     "$ICON_SRC" --out "$ICONSET_DIR/icon_16x16@2x.png"   >/dev/null
sips -z 32 32     "$ICON_SRC" --out "$ICONSET_DIR/icon_32x32.png"      >/dev/null
sips -z 64 64     "$ICON_SRC" --out "$ICONSET_DIR/icon_32x32@2x.png"   >/dev/null
sips -z 128 128   "$ICON_SRC" --out "$ICONSET_DIR/icon_128x128.png"    >/dev/null
sips -z 256 256   "$ICON_SRC" --out "$ICONSET_DIR/icon_128x128@2x.png" >/dev/null
sips -z 256 256   "$ICON_SRC" --out "$ICONSET_DIR/icon_256x256.png"    >/dev/null
sips -z 512 512   "$ICON_SRC" --out "$ICONSET_DIR/icon_256x256@2x.png" >/dev/null
sips -z 512 512   "$ICON_SRC" --out "$ICONSET_DIR/icon_512x512.png"    >/dev/null
sips -z 1024 1024 "$ICON_SRC" --out "$ICONSET_DIR/icon_512x512@2x.png" >/dev/null
iconutil -c icns "$ICONSET_DIR" -o "$ICON_ICNS"
rm -rf "$ICONSET_DIR"

echo "[build] limpiando builds previos"
rm -rf build dist "${APP_NAME}.spec"

echo "[build] empaquetando con PyInstaller"
# TARGET_ARCH:
#   - unset/empty → arch nativa (lo más rápido en local).
#   - "universal2" → bundle universal (Intel + Apple Silicon). Requiere un
#     intérprete Python compilado universal2 (lo trae setup-python en CI;
#     local: instalar con `python.org` o `brew install python` puede no).
# En CI macos-14 el job exporta TARGET_ARCH=universal2 para cubrir labs con
# iMacs Intel + Macs M1/M2/M3.
ARCH_FLAG=()
if [ -n "${TARGET_ARCH:-}" ]; then
  ARCH_FLAG=(--target-arch "$TARGET_ARCH")
  echo "[build] target-arch: $TARGET_ARCH"
fi
pyinstaller \
  --name "$APP_NAME" \
  --windowed \
  --icon "$ICON_ICNS" \
  --add-data "web:web" \
  --osx-bundle-identifier "$BUNDLE_ID" \
  --noconfirm \
  --clean \
  --collect-all webview \
  --collect-all readlif \
  --collect-all nd2 \
  --collect-all ome_types \
  --collect-all dask \
  --collect-all resource_backed_dask_array \
  --copy-metadata nd2 \
  --copy-metadata ome-types \
  --copy-metadata dask \
  "${ARCH_FLAG[@]+"${ARCH_FLAG[@]}"}" \
  app.py
# Nota: la sintaxis "${ARRAY[@]+"${ARRAY[@]}"}" es empty-safe bajo `set -u`
# (bash 3.2 default de macOS) — expande a NADA si el array está vacío en
# lugar de quejarse de "unbound variable".

APP_PATH="dist/${APP_NAME}.app"
if [ ! -d "$APP_PATH" ]; then
  echo "[build] ERROR: no se generó $APP_PATH" >&2
  exit 1
fi

# Quita el atributo de cuarentena por si acaso (apps sin firma)
xattr -dr com.apple.quarantine "$APP_PATH" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Firma + notarización (opcional). Se ejecuta solo si las env vars están.
# Cuando llegue Apple Developer ID, exportar:
#   APPLE_DEVELOPER_ID="Developer ID Application: Tu Nombre (TEAMID)"
#   APPLE_NOTARY_PROFILE="notary"   # perfil guardado con `notarytool store-credentials`
# ---------------------------------------------------------------------------
if [ -n "${APPLE_DEVELOPER_ID:-}" ]; then
  echo "[build] firmando con $APPLE_DEVELOPER_ID"
  codesign --deep --force --options runtime \
    --entitlements <(/usr/libexec/PlistBuddy -x -c "Print" /dev/stdin <<<'<plist version="1.0"><dict/></plist>') \
    --sign "$APPLE_DEVELOPER_ID" "$APP_PATH"
  if [ -n "${APPLE_NOTARY_PROFILE:-}" ]; then
    echo "[build] notarizando con perfil $APPLE_NOTARY_PROFILE"
    ZIP_PATH="dist/${APP_NAME}.zip"
    /usr/bin/ditto -c -k --keepParent "$APP_PATH" "$ZIP_PATH"
    xcrun notarytool submit "$ZIP_PATH" --keychain-profile "$APPLE_NOTARY_PROFILE" --wait
    xcrun stapler staple "$APP_PATH"
    rm -f "$ZIP_PATH"
  fi
else
  echo "[build] (sin firma — define APPLE_DEVELOPER_ID para firmar/notarizar)"
fi

# ---------------------------------------------------------------------------
# .dmg para distribución (B.1 del plan). Requiere `brew install create-dmg`.
# Si create-dmg no está, se salta sin error — la .app sigue siendo válida.
# ---------------------------------------------------------------------------
APP_VERSION=$(grep -E '^APP_VERSION' core.py | head -1 | sed -E 's/.*"([^"]+)".*/\1/')
# ARCH_SUFFIX permite distinguir DMGs en matrix builds (CI):
#   ARCH_SUFFIX=arm64  → "SYN APSE Conversor LIF v0.3.1 arm64.dmg"
#   ARCH_SUFFIX=x86_64 → "SYN APSE Conversor LIF v0.3.1 x86_64.dmg"
#   sin set            → "SYN APSE Conversor LIF v0.3.1.dmg" (local single-arch)
DMG_ARCH_LABEL=""
if [ -n "${ARCH_SUFFIX:-}" ]; then
  DMG_ARCH_LABEL=" ${ARCH_SUFFIX}"
fi
DMG_PATH="dist/SYN APSE Conversor LIF v${APP_VERSION}${DMG_ARCH_LABEL}.dmg"
if command -v create-dmg >/dev/null 2>&1; then
  echo "[build] generando DMG: $DMG_PATH"
  rm -f "$DMG_PATH"
  create-dmg \
    --volname "SYN APSE Conversor LIF" \
    --volicon "$ICON_ICNS" \
    --window-pos 200 120 \
    --window-size 600 380 \
    --icon-size 128 \
    --icon "${APP_NAME}.app" 160 180 \
    --hide-extension "${APP_NAME}.app" \
    --app-drop-link 440 180 \
    --no-internet-enable \
    --skip-jenkins \
    "$DMG_PATH" \
    "$APP_PATH" || echo "[build] WARN: create-dmg falló — la .app está OK"
  # --skip-jenkins evita que create-dmg use AppleScript para "embellecer" la
  # ventana del DMG; si no, Finder auto-monta la imagen temporal y la deja
  # "en uso", impidiendo el unmount/convert final.
else
  echo "[build] (sin DMG — instala con: brew install create-dmg)"
fi

echo
echo "[build] OK"
echo "  → $APP_PATH"
[ -f "$DMG_PATH" ] && echo "  → $DMG_PATH"
echo
echo "Para abrirla:"
echo "  open \"$APP_PATH\""
echo "Para instalarla:"
echo "  cp -R \"$APP_PATH\" /Applications/"
