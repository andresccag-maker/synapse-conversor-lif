#!/usr/bin/env bash
# SYN APSE — Conversor LIF: build de AppImage para Linux (sin firma).
#
# PyInstaller no hace cross-compile: este script produce un binario para la
# arquitectura de LA MÁQUINA donde corre. En CI hay un job por arch:
#   - ubuntu-latest      → x86_64
#   - ubuntu-24.04-arm   → aarch64 (ARM64)
#
# Requisitos del sistema (pywebview usa el backend GTK + WebKit2GTK):
#   sudo apt-get install -y \
#     python3-venv libgtk-3-0 libgirepository-1.0-1 gir1.2-gtk-3.0 \
#     gir1.2-webkit2-4.1 libwebkit2gtk-4.1-0   # (en Ubuntu 22.04: -4.0 en vez de -4.1)
#
# Salida:
#   dist/SYN_APSE_Conversor_LIF/                       (onedir PyInstaller)
#   dist/SYN_APSE_Conversor_LIF-v{ver}-linux-{arch}.AppImage   (si appimagetool va)
#   dist/SYN_APSE_Conversor_LIF-v{ver}-linux-{arch}.tar.gz     (fallback / no-FUSE)
#
# Variables:
#   ARCH_SUFFIX  etiqueta de arch para el nombre del artefacto (CI: x86_64 | arm64).
#                Si no se define, se usa `uname -m`.

set -euo pipefail

cd "$(dirname "$0")"

APP_BIN_NAME="SYN_APSE_Conversor_LIF"        # sin espacios/acentos (binario + AppImage)
APP_DISPLAY="SYN APSE — Conversor LIF"
ICON_NAME="synapse-conversor-lif"
ICON_SRC="assets/icon.png"

MACHINE_ARCH="$(uname -m)"                   # x86_64 | aarch64
ARCH_LABEL="${ARCH_SUFFIX:-$MACHINE_ARCH}"

if [ ! -d ".venv" ]; then
  echo "[build] no existe .venv — creándolo"
  # --system-site-packages: el backend GTK de pywebview usa el PyGObject (gi)
  # del sistema (apt python3-gi). PyInstaller --collect-all gi necesita verlo.
  python3 -m venv .venv --system-site-packages
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "[build] instalando dependencias (incluye pyinstaller)"
pip install --upgrade pip >/dev/null
pip install -r requirements.txt >/dev/null
pip install "pyinstaller>=6.0" appdirs >/dev/null

if [ ! -f "$ICON_SRC" ]; then
  echo "[build] ERROR: falta $ICON_SRC — ejecuta antes: .venv/bin/python assets/generate_icon.py" >&2
  exit 1
fi

echo "[build] limpiando builds previos"
rm -rf build dist "${APP_BIN_NAME}.spec"

echo "[build] empaquetando con PyInstaller (onedir)"
# --collect-all gi: incluye PyGObject (bindings GTK) para el backend de pywebview
# en Linux. Las libs nativas GTK/WebKit2 se esperan en el sistema del usuario.
pyinstaller \
  --name "$APP_BIN_NAME" \
  --windowed \
  --add-data "web:web" \
  --noconfirm \
  --clean \
  --collect-all webview \
  --collect-all readlif \
  --collect-all gi \
  --hidden-import appdirs \
  --exclude-module matplotlib \
  --exclude-module tkinter \
  --exclude-module PyQt5 \
  --exclude-module PyQt6 \
  --exclude-module PySide2 \
  --exclude-module PySide6 \
  --exclude-module pandas \
  --exclude-module scipy \
  --exclude-module pytest \
  app.py
# --exclude-module: con --system-site-packages, PyInstaller "ve" paquetes del
# sistema (matplotlib, Qt…) que la app NO usa y cuyos hooks pueden romper el
# análisis (p. ej. matplotlib vs numpy con ABI distinta). La app solo necesita
# gi/webview/readlif/numpy/tifffile/PIL.

DIST_DIR="dist/${APP_BIN_NAME}"
if [ ! -d "$DIST_DIR" ]; then
  echo "[build] ERROR: no se generó $DIST_DIR" >&2
  exit 1
fi

APP_VERSION=$(grep -E '^APP_VERSION' core.py | head -1 | sed -E 's/.*"([^"]+)".*/\1/')
OUT_BASE="${APP_BIN_NAME}-v${APP_VERSION}-linux-${ARCH_LABEL}"

# ---------------------------------------------------------------------------
# AppDir → AppImage
# ---------------------------------------------------------------------------
echo "[build] montando AppDir"
APPDIR="build/AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr"
cp -a "$DIST_DIR/." "$APPDIR/usr/"

# AppRun + .desktop + icono en la raíz del AppDir (lo exige appimagetool)
cp packaging/linux/AppRun "$APPDIR/AppRun"
chmod +x "$APPDIR/AppRun"
sed "s/@ICON_NAME@/${ICON_NAME}/g" packaging/linux/synapse-conversor-lif.desktop \
  > "$APPDIR/${ICON_NAME}.desktop"
cp "$ICON_SRC" "$APPDIR/${ICON_NAME}.png"
# Copia también a la jerarquía estándar de iconos (buenas prácticas AppImage)
mkdir -p "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp "$ICON_SRC" "$APPDIR/usr/share/icons/hicolor/256x256/apps/${ICON_NAME}.png"

# appimagetool para la arch de la máquina (se cachea en assets/, gitignored)
case "$MACHINE_ARCH" in
  x86_64)  AIT_ARCH="x86_64" ;;
  aarch64|arm64) AIT_ARCH="aarch64" ;;
  *) echo "[build] ERROR: arch no soportada para AppImage: $MACHINE_ARCH" >&2; exit 1 ;;
esac
AIT="assets/appimagetool-${AIT_ARCH}.AppImage"
if [ ! -f "$AIT" ]; then
  echo "[build] descargando appimagetool (${AIT_ARCH})"
  curl -sSL -o "$AIT" \
    "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${AIT_ARCH}.AppImage" || true
  chmod +x "$AIT" 2>/dev/null || true
fi

APPIMAGE_OUT="dist/${OUT_BASE}.AppImage"
appimage_ok=0
if [ -s "$AIT" ]; then
  echo "[build] generando AppImage: $APPIMAGE_OUT"
  # APPIMAGE_EXTRACT_AND_RUN evita depender de FUSE en CI/headless.
  if APPIMAGE_EXTRACT_AND_RUN=1 ARCH="$AIT_ARCH" "$AIT" "$APPDIR" "$APPIMAGE_OUT"; then
    appimage_ok=1
  else
    echo "[build] WARN: appimagetool falló — se usará el tarball" >&2
  fi
else
  echo "[build] WARN: appimagetool no disponible — se usará el tarball" >&2
fi

# ---------------------------------------------------------------------------
# Tarball — SIEMPRE (opción sin FUSE para usuarios no técnicos: extraer y
# ejecutar el binario, sin libfuse2 ni chmod especial del AppImage).
# ---------------------------------------------------------------------------
TARBALL="dist/${OUT_BASE}.tar.gz"
echo "[build] generando tarball (sin FUSE): $TARBALL"
tar -C dist -czf "$TARBALL" "$APP_BIN_NAME"

echo
echo "[build] OK"
[ "$appimage_ok" -eq 1 ] && echo "  → $APPIMAGE_OUT"
[ -f "$TARBALL" ] && echo "  → $TARBALL"
echo "  → $DIST_DIR/ (onedir)"
