# Changelog

Versionado siguiendo [SemVer](https://semver.org/lang/es/). Formato basado en
[Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).

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
