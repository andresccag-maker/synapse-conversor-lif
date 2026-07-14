# Changelog

Versionado siguiendo [SemVer](https://semver.org/lang/es/). Formato basado en
[Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).

## [0.5.0] — sin publicar (pendiente de ND2 real + gate humano de tag)

### Added
- **Nuevo modo ND2 → TIFF / MIP (microscopía Nikon)**: tercer modo hermano de
  LIF→MIP y TIF→MIP. Convierte ficheros `.nd2` (cada posición XY = una serie
  `Image{NNN}`; cada canal 0-indexado en su orden real) a MIP `(1, Y, X)` o
  Z-stack completo, con **el mismo contrato de salida que LIF→MIP** (naming
  Bio-Formats `{base}.nd2 - Image{NNN} - C={c}.tif`, estructura aplanada
  `{Exp}/{Pocillo}/` y `_manifest.json`) → `cell-analyzer-worker` lo consume
  idéntico. El manifest añade `conversion_mode` (`nd2_to_mip`/`nd2_to_zstack`).
  - Núcleo `core.py`: `read_nd2_info`, `convert_nd2`, `_open_nd2` (aislado para
    tests), preflight dimensional con **errores tipados**
    (`Nd2Error`/`Nd2PreflightError`/`Nd2ReadError`): rechaza time-lapse (T>1),
    RGB, ejes no soportados y dtype no `uint<=16` (los 12-bit de Nikon se
    preservan como uint16, sin normalizar). **MIP por streaming plano a plano**
    (`np.maximum`, memoria ≈ 2 planos); z-stack rechaza salidas > 4 GiB (BigTIFF
    = decisión humana). Reutiliza `bioformats_channel_filename`/`_save_tiff`/
    `project_mip`.
  - CLI `convert_cli.py`: `--nd2 PATH` (excluyente con `.lif`/`--tif-folder`) y
    `--self-test-nd2 FIXTURE` (autocomprueba el lector ND2 dentro del binario
    congelado). Errores del preflight traducidos a mensaje legible.
  - App de escritorio: tercer modo `ND2 → TIFF / MIP` (comparte con LIF la lista
    de series, chips de exclusión y selector de proyección).
  - Empaquetado: dependencia `nd2==0.11.3` (wheel pure-python, aarch64 OK) +
    `--collect-all nd2/ome_types/dask/resource_backed_dask_array` en los builds y
    self-test del binario congelado por plataforma en CI.
  - Tests: `tests/test_nd2_to_mip.py` (15 tests con fakes + golden manifest +
    preflight). 38 tests totales.

### Changed
- `core.APP_VERSION`: 0.4.2 → 0.5.0. Pill de versión de la UI → v0.5.0.
- Esta versión **incluye los fixes de 0.4.2** (GTK en Linux + TIF→MIP conserva el
  nombre) que nunca llegaron a publicarse con tag propio; v0.5.0 los libera.

### Pendiente (gate humano, ver session_log)
- Validar con ≥1 `.nd2` real: spike de lectura real, fixture `tests/fixtures/
  sample.nd2` para el self-test de CI, y cross-check del worker. **No taggear
  v0.5.0 hasta validar.**

## [0.4.2] — 2026-06-19

### Fixed
- **Linux: la app ya carga GTK** (`pywebview: GTK cannot be loaded` /
  `import gi`). La CI compilaba con `setup-python` 3.11 mientras el binding
  `python3-gi` de apt es para el Python del sistema (3.10), así que
  `--collect-all gi` no empaquetaba nada. Ahora los jobs de Linux usan el Python
  del sistema + `python3-gi`/`python3-gi-cairo` de apt (mismo stack validado en
  la Jetson). Se elimina `generate_icon` en Linux (el icono va commiteado).
- **TIF→MIP CONSERVA SIEMPRE el nombre y espeja la estructura** (el MIP no
  renombra; solo colapsa el Z-stack a 1 plano). Funciona con cualquier convención
  real: sufijo `- C={c}` (EXP109), prefijo `C{c}-...` (EXP116), `..._c{N}`. El
  canal/imagen se detectan mejor-esfuerzo solo para el manifest. Antes re-derivaba
  y rompía: con EXP116 (`C1-...Image003.tif`) ponía todo a `C=0` y renumeraba.

## [0.4.1] — 2026-06-19

### Fixed
- **AppImage de Linux compatible con Ubuntu 22.04+**: los jobs de CI de Linux
  pasan de `ubuntu-latest`/`ubuntu-24.04-arm` (GLIBC 2.39) a `ubuntu-22.04`/
  `ubuntu-22.04-arm` (GLIBC 2.35). Los AppImage son compatibles **hacia
  adelante**, no hacia atrás: el de v0.4.0 (construido en 24.04) exigía
  `GLIBC_2.38` y fallaba en Ubuntu 22.04 (la Jetson y la mayoría de equipos de
  laboratorio) con `libpython3.11.so: version GLIBC_2.38 not found`. Compilando
  en 22.04 el AppImage corre en 22.04 y posteriores.

## [0.4.0] — 2026-06-19

### Added
- **Nuevo modo de conversión TIF → MIP (lote)**: convierte TIFFs ya exportados
  (un canal por fichero, Z-stack multipágina) a MIP sin volver al `.lif`. Se
  elige una carpeta raíz con estructura `Experimento/Pocillo/*.tif` y se genera
  el MIP de cada canal de cada imagen, espejando la estructura de carpetas.
  - Núcleo `core.py`: `scan_tif_folder`, `convert_tif_folder`, `_read_tiff_zstack`,
    `_read_tiff_pixel_size`, `TifFolderOptions`/`TifScan`. Reutiliza `project_mip`,
    `_save_tiff` y `bioformats_channel_filename` → la salida es **byte-idéntica en
    formato** a LIF→MIP (mismo naming `{base} - Image{NNN} - C={c}.tif`, canal
    0-indexado, + `_manifest.json`). El canal se deriva del sufijo `_cN` y se
    **normaliza a 0-based** (robusto a datos 1-based). El pixel size se preserva
    leyendo los tags de resolución del TIFF de entrada.
  - CLI `convert_cli.py`: flag `--tif-folder DIR` (+ `--base-name`), retrocompatible.
  - App de escritorio: selector de modo `LIF → TIFF/MIP` ↔ `TIF → MIP (lote)`,
    selector de carpeta y resumen del escaneo.
- **Build de Linux** (x86_64 + aarch64) vía PyInstaller + AppImage (`build_linux.sh`,
  `packaging/linux/`), con jobs de CI `build-linux-x86_64` (`ubuntu-latest`) y
  `build-linux-arm64` (`ubuntu-24.04-arm`). La landing añade botón de descarga Linux.

### Changed
- `core.APP_VERSION`: 0.3.1 → 0.4.0.
- Pill de versión de la UI corregido (estaba en 0.3.0).

## [0.3.1] — 2026-06-04

### Added
- **Build Intel para macOS** vía matrix CI (`macos-13` + `macos-14`). La Release
  v0.3.1 incluye dos `.dmg`: `arm64` (Apple Silicon nativo) y `x86_64` (Intel
  nativo, también corre en AS vía Rosetta 2). Cubre laboratorios con iMacs/MBP
  Intel anteriores a 2020.
- `build_macos.sh` honora `ARCH_SUFFIX` para añadir la arquitectura al nombre
  del DMG en builds multi-arch.
- Página `axiombio.tech/apps/lif-converter`: dos botones macOS (Apple Silicon /
  Intel) con auto-detección del SO+arquitectura para resaltar el correcto.

### Fixed
- `build_macos.sh:98` "ARCH_FLAG[@]: unbound variable" bajo `set -u` (bash 3.2
  macOS default) cuando `TARGET_ARCH` no se exportaba — idiom empty-safe.
- `axiombio-website/apps/*`: dejaban de mostrar el fondo `bg-platform.webp` del
  resto del site porque sobreescribían `body { background }`.

## [0.3.0] — 2026-06-04

### Added
- **Icono macOS profesional**: nuevo PNG 1024×1024 RGBA con squircle estilo iOS
  (gradiente cian → azul) y monograma "S". Elimina el cuadrado blanco que
  aparecía en Dock/Launchpad/Finder.
- **`assets/generate_icon.py`**: generador procedural de icono. Produce
  `icon.png`, `icon.ico` (multi-resolución Windows), `favicon.ico`,
  `apple-touch-icon.png`. Reproducible.
- **`build_macos.sh`**: validación de alfa del icono antes del build (aborta si
  hay fondo blanco horneado). Generación opcional de `.dmg` con `create-dmg`.
  Hooks listos para firma + notarización (`APPLE_DEVELOPER_ID`,
  `APPLE_NOTARY_PROFILE`).
- **`build_windows.ps1` + `installer.iss`** (NUEVOS): empaquetado completo
  Windows con PyInstaller + Inno Setup. Instalador detecta y, si falta,
  instala el runtime WebView2 Evergreen. Hooks Authenticode listos
  (`WIN_SIGN_CERT`, `WIN_SIGN_PASS`).
- **`.github/workflows/release.yml`**: compila `.dmg` (macos-14) y `.exe`
  (windows-latest) al hacer push de un tag `v*` y los adjunta a un GitHub
  Release.
- **UI rediseñada**: sistema de tokens CSS (espaciado, tipografía, radios,
  sombras), light mode automático vía `prefers-color-scheme`, sticky action
  bar inferior, dropzone con drag&drop, chips con swatch del LUT del canal,
  empty states, monograma de marca en el topbar.

### Changed
- `core.APP_VERSION`: 0.2.0 → 0.3.0.

## [0.2.0]

- Conversión Bio-Formats por canal con pixel size, LUTs y manifest por pocillo.
