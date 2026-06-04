# SYN APSE — Conversor LIF

App de escritorio **offline** que convierte archivos Leica `.lif` en canales
TIFF organizados, sustituyendo dos macros de Fiji (separar series/canales y
"Cajón de Sastre") en un solo paso con interfaz gráfica.

## Qué hace

- Lee un `.lif` local con `readlif` (Python puro, sin Java/JVM).
- **Salida APLANADA** en la carpeta del pocillo (sin subcarpetas por
  serie). Naming estilo Bio-Formats, canal 0-indexado:

  ```
  {salida}/{Experimento}/{Pocillo}/{nombre_lif}.lif - Image{NNN} - C={c}.tif
  {salida}/{Experimento}/{Pocillo}/_manifest.json
  ```

  Ejemplo real (5 canales, 3 series):

  ```
  Exp51 PLACA FN/POCILLO 8/
  ├── EXP51 PLACA FN POCILLO 8 S1086D.lif - Image001 - C=0.tif
  ├── EXP51 PLACA FN POCILLO 8 S1086D.lif - Image001 - C=1.tif
  ├── …
  ├── EXP51 PLACA FN POCILLO 8 S1086D.lif - Image003 - C=4.tif
  └── _manifest.json
  ```

  - `{nombre_lif}` se preserva **verbatim** (incluye la extensión `.lif`
    en medio del nombre; solo se reemplazan `/` `\` y caracteres de
    control por `_`).
  - `Image{NNN}` = `Image` + (índice de serie + 1) con padding a 3.
  - `C={c}` = canal **0-indexado** (Bio-Formats: C=0, C=1, …). Esto
    coincide con la convención del `channel_map` del worker downstream.
  - Las carpetas `{Experimento}` y `{Pocillo}` conservan el nombre tal
    cual lo teclea el usuario (preservan espacios).
- Preserva el bit depth nativo (uint8 si ≤8, si no uint16).
- Permite **excluir canales** (p. ej. transmisión) por índice
  **0-indexado**.
- Permite **proyección MIP** opcional. Con MIP activo, cada canal se guarda
  como un único plano con forma `(1, Y, X)`, conservando dtype, de modo que
  un `max(axis=0)` posterior (por ejemplo un worker downstream) sea
  identidad. Modo alternativo "Z-stack completo": idéntico a la macro.
- Sugiere `Experimento` y `Pocillo` a partir del nombre del archivo, pero
  son **editables**.
- **Previews en color** según el LUT del propio `.lif` (Red, Green, Blue,
  Cyan, Magenta, Yellow, Gray…). El nombre del LUT se muestra debajo de
  cada thumbnail (p. ej. `C=2 · Gray`). El LUT **Gray** suele ser el
  canal de **transmisión** y es habitual marcarlo para exclusión.
- **`_manifest.json`** (uno por pocillo) con: `app_version`,
  `source_filename`, `source_sha256`, `experiment`, `pocillo`,
  `projection`, `excluded_channels` (0-indexados), y la lista
  `files: [{series_index, image_label, channel, lut_name, filename}]`.

## Qué NO hace

- No abre conexiones de red.
- No tiene autenticación, base de datos ni telemetría.
- No depende de Fiji/ImageJ ni de la JVM.
- No modifica los datos crudos: lo único transformado es la proyección,
  cuando se elige MIP.

## Requisitos

- macOS (probado primero aquí); Linux/Windows deberían funcionar igual.
- Python 3.10+.
- Dependencias en [requirements.txt](requirements.txt). En macOS,
  `pywebview` arrastra `pyobjc` y usa el WebKit del sistema, sin pasos
  extra.

## Arranque

### Recomendado

```bash
./run.sh
```

Crea `.venv`, instala las dependencias y lanza la app.

### Manual

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

## Validación headless (sin GUI)

`convert_cli.py` usa el mismo núcleo que la app, sin tocar pywebview.

```bash
# Solo metadatos: nº de series, canales, Z, bit depth y LUTs por canal.
python convert_cli.py /ruta/al/archivo.lif --info

# Conversión con MIP, excluyendo el canal de transmisión (suele ser el
# LUT "Gray", típicamente C=2):
python convert_cli.py /ruta/al/archivo.lif -o /tmp/out --mip --exclude 2

# Conversión sin proyección (Z-stack completo, idéntico a la macro):
python convert_cli.py /ruta/al/archivo.lif -o /tmp/out --zstack
```

> `--exclude` toma índices **0-indexados** (Bio-Formats). El bloque
> `luts:` que imprime `--info` indica qué canal es cuál (`C=0:Red`,
> `C=2:Gray`, etc.) — el canal `Gray` suele ser la transmisión.

## Checklist de validación (Fase 0)

1. Ejecutar `python -m pytest tests/ -q` → todos en verde.
2. Ejecutar `python convert_cli.py <lif> --info` con un `.lif` real y
   comprobar que el nº de series, los canales, Z, el bit depth y los
   nombres de LUT coinciden con lo que muestra Fiji.
3. Convertir con `--mip` y abrir un TIFF en Fiji: debe ser un único plano
   con la misma intensidad máxima por píxel que la proyección manual.
4. **Punto crítico (compatibilidad con worker downstream):** subir una
   imagen conocida tras MIP y compararla contra la misma imagen tras
   `max(axis=0)` aplicado al TIFF de salida. El ratio debe ser **idéntico
   1:1**, porque la salida MIP ya es de un único plano `(1, Y, X)` y el
   `max(axis=0)` del worker debe ser identidad.
5. Convertir con `--zstack` y comprobar que el TIFF preserva la dimensión
   Z (mismas N capas que el original).
6. Verificar que `_manifest.json` en la carpeta del pocillo contiene
   `source_sha256`, `projection`, `excluded_channels` (0-indexados) y
   una lista `files` con `series_index`, `image_label`, `channel`,
   `lut_name` y `filename` poblados.
7. Verificar que la salida es **aplanada** (TIFFs directamente en
   `{Experimento}/{Pocillo}/`, sin subcarpetas `Image{NNN}/`).

## Estructura del proyecto

```
core.py              núcleo: lectura .lif, MIP, naming, escritura TIFF (sin GUI)
app.py               app de escritorio (pywebview)
convert_cli.py       CLI headless (mismo núcleo, sin GUI)
web/index.html       UI
web/style.css        estilos
web/app.js           lógica UI
tests/test_core.py   tests del núcleo (sin .lif real, usa fakes)
requirements.txt
run.sh
README.md
```

`core.py` y `convert_cli.py` **no importan** `pywebview`: la conversión
funciona en modo headless.

## Tests

```bash
source .venv/bin/activate
python -m pytest tests/ -q
```

Los tests usan `FakeLifFile` / `FakeLifImage` (monkeypatch de
`core._open_lif`), por lo que **no necesitan un `.lif` real**.

## Build a `.app` con icono (macOS, sin firma)

```bash
./build_macos.sh
```

- Requiere `assets/icon.png` (1024×1024 recomendado).
- Genera `assets/icon.icns` con `sips` + `iconutil` (herramientas
  nativas de macOS).
- Empaqueta con PyInstaller en modo `--windowed`, incluyendo `web/` y
  todos los submódulos de `webview` y `readlif`.
- Resultado: `dist/SYN APSE — Conversor LIF.app`.
- Para usarla:

  ```bash
  open "dist/SYN APSE — Conversor LIF.app"
  cp -R "dist/SYN APSE — Conversor LIF.app" /Applications/
  ```

La app **no está firmada ni notarizada**, así que está pensada para tu
propia máquina. Si la copias a otro Mac, Gatekeeper avisará la primera
vez (botón derecho → Abrir → Abrir).

La firma + notarización con Developer ID es una fase posterior.

## Notas

- Si un futuro `.lif` rompe el lector, el problema casi siempre será de
  `readlif`: verificar con `python convert_cli.py <lif> --info` antes
  de tocar el resto de la app.
