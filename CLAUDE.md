# CLAUDE.md — Conversor LIF

Utilidad de escritorio que convierte archivos LIF (Leica) de microscopía al formato
que consume el pipeline de Axiom Bio. App local (no servicio web) distribuida vía
una landing con instaladores Mac/Windows. Es el primer eslabón del pipeline de
imagen: LIF → conversión → análisis (cell-analyzer-worker). Landing:
https://converter.axiombio.tech

## Proyecto en Axiom Omni
- Nombre: Conversor LIF — UUID `85160dcf-79f0-44d5-8526-ad6f2ccb8bc1` (slug `conversor-lif`).
- Ese project_id manda para cargar/documentar estado. No lo infiero ni lo cambio.

## 1. Cargar contexto ANTES de tocar código (vía MCP Axiom Omni, carga mínima)
- `get_project_home("conversor-lif")` — estado actual, última entrega, pendientes.
  OBLIGATORIO al empezar. (Proyecto pequeño: el home puede tener poco; complétalo
  tú al documentar.)
- Lee el `README` del repo para el detalle técnico real (estructura, empaquetado,
  deploy de la landing).
- Nada más de carga. No barras con `search_notes`.

## 2. Stack y estructura

### Núcleo Python
- Python 3.11. Lectura LIF: `readlif`. Escritura/imagen: `tifffile`, `numpy`,
  `Pillow`. GUI de escritorio: `pywebview` (en macOS arrastra `pyobjc` y usa el
  WebKit del sistema, sin pasos extra).
- No introducir dependencias nuevas sin que se pidan. Cada lib nueva complica el
  empaquetado y la firma — es una decisión, no un paso intermedio.

### Estructura del proyecto
```
core.py              núcleo: lectura .lif, MIP, naming, escritura TIFF (sin GUI)
app.py               app de escritorio (pywebview)
convert_cli.py       CLI headless (mismo núcleo, sin GUI)
web/index.html       UI
web/style.css        estilos
web/app.js           lógica UI
tests/test_core.py   tests del núcleo (sin .lif real, usa fakes)
requirements.txt
run.sh               crea .venv, instala deps y lanza la app
build_macos.sh       empaqueta con PyInstaller → dist/SYN APSE — Conversor LIF.app
```

`core.py` y `convert_cli.py` **no importan** `pywebview`: la conversión funciona
en modo headless.

### Distribución
- macOS: `./build_macos.sh` → PyInstaller modo `--windowed` → `dist/SYN APSE — Conversor LIF.app`.
  Requiere `assets/icon.png` (1024×1024); genera `assets/icon.icns` con `sips`+`iconutil`.
- Windows: instalador pendiente (ver sección 3 sobre firma).
- Landing estática en Vercel.

## 3. REGLAS DURAS — qué cuidar

### Contrato de formato de salida con el worker
Lo que produce este conversor lo consume `cell-analyzer-worker`. **NO cambies**
el formato, nombres de canal, estructura de carpetas o convención de archivos de
salida sin confirmar que el worker los sigue leyendo. Un cambio de salida aquí
puede romper el análisis aguas abajo en silencio.

Formato de salida vigente (aplanado, sin subcarpetas por serie):
```
{salida}/{Experimento}/{Pocillo}/{nombre_lif}.lif - Image{NNN} - C={c}.tif
{salida}/{Experimento}/{Pocillo}/_manifest.json
```
- `{nombre_lif}` verbatim (incluye `.lif` en medio; solo se reemplazan `/` `\`
  y caracteres de control por `_`).
- `Image{NNN}` = `Image` + (índice de serie + 1) con padding a 3.
- `C={c}` canal **0-indexado** (Bio-Formats). Coincide con el `channel_map` del worker.
- Con MIP activo: cada canal se guarda como un único plano `(1, Y, X)`, conservando
  dtype, de modo que un `max(axis=0)` posterior (p. ej. en el worker) sea identidad.
- `_manifest.json` (uno por pocillo) con: `app_version`, `source_filename`,
  `source_sha256`, `experiment`, `pocillo`, `projection`, `excluded_channels`
  (0-indexados) y lista `files: [{series_index, image_label, channel, lut_name, filename}]`.

Si un cambio te obliga a tocar cualquiera de estos campos, **PARA y pregunta**.

### App de escritorio para científicos, no un servidor
Corre en la máquina del usuario (un científico, no un dev). Errores deben ser
legibles para un no-técnico, no un stacktrace de Python crudo. Maneja archivos
LIF corruptos/grandes con gracia.

### Firma de código — estado real
Hoy NO hay Apple Developer ID ni certificado Windows (AuthentiCode). No asumas
que existen. Los binarios van sin firmar (warnings de Gatekeeper/SmartScreen
esperados). Prepara el terreno para notarización futura si toca el empaquetado,
pero no inventes credenciales que no tenemos.

Respeta rutas y permisos del sistema de archivos local (Mac y Windows difieren).

## 4. Verificación antes de entregar

```bash
# 1. Tests unitarios (usan FakeLifFile/FakeLifImage — no necesitan .lif real)
source .venv/bin/activate
python -m pytest tests/ -q
# → todos en verde

# 2. Metadatos de un .lif real (series, canales, Z, bit depth, LUTs)
python convert_cli.py /ruta/al/archivo.lif --info
# → coincide con lo que muestra Fiji

# 3. Conversión MIP
python convert_cli.py /ruta/al/archivo.lif -o /tmp/out --mip --exclude 2
# → TIFF de un único plano; max(axis=0) aplicado al TIFF debe ser identidad (ratio 1:1)

# 4. Conversión Z-stack completo
python convert_cli.py /ruta/al/archivo.lif -o /tmp/out --zstack
# → TIFF preserva dimensión Z (mismas N capas que el original)

# 5. _manifest.json contiene source_sha256, projection, excluded_channels (0-idx),
#    lista files con series_index/image_label/channel/lut_name/filename

# 6. Salida aplanada: TIFFs directamente en {Experimento}/{Pocillo}/, sin subcarpetas

# 7. Build macOS (si se tocó el empaquetado)
./build_macos.sh
# → dist/SYN APSE — Conversor LIF.app se genera sin error
```

> Si un `.lif` rompe el lector, el problema casi siempre será de `readlif`:
> verificar con `--info` antes de tocar el resto de la app.

## 5. Alcance — haz lo pedido, señala el resto
- Implementa SOLO los cambios explícitos del prompt. No refactorices ni reorganices
  lo no pedido.
- Si detectas un bug, una mejora obvia o algo que huele mal cerca de lo que tocas,
  NO lo cambies: anótalo al final de tu resumen como "Observaciones (no aplicadas)"
  para que Andrew decida.
- Si un cambio te obliga a tocar el formato de salida (sección 3), PARA y pregunta.

## 6. Entrega — commit + push + doc automático
Al hacer commit+push al final de una sesión, ejecuta esto automáticamente SIN
esperar a que se pida (si en una sesión NO se quiere doc+, basta con decírtelo):

### Paso A — session_log (upsert_note)
Primero `get_latest_log_by_type("conversor-lif","session_log")` para obtener el
note_id del log anterior; con ese note_id, `upsert_note` con
`frontmatter.latest=false` para desmarcarlo (así solo hay un latest:true). Si
devuelve vacío, sáltate este paso.
Luego crea el log nuevo con `upsert_note`:
- `title`: "Session Log — <qué hiciste en 1 frase> — <YYYY-MM-DD>"
- `project_id`: "85160dcf-79f0-44d5-8526-ad6f2ccb8bc1"
- `slug`: "session-log-<YYYY-MM-DD>-<kebab-feature>"
- `tags`: ["session_log","conversor-lif","python","<feature-tag>"]
- `frontmatter`: {"note_type":"session_log","canonical":true,"latest":true,"event_date":"<YYYY-MM-DD>","source":"claude-code","feature":"<kebab-feature>"}
- `body`: ver plantilla abajo (termina SIEMPRE con `## Proyecto` + wikilink al home)

Plantilla de body:
```
# Session Log — <qué hiciste> — <YYYY-MM-DD>

> <una línea: qué cambió y por qué>

## Cambios aplicados
- **`<archivo>`** — <qué cambió>

## Decisiones
- <por qué; alternativas descartadas>

## Compatibilidad con el pipeline
- Formato de salida: <sin cambios / cambió X — worker verificado / pendiente>

## Verificación
- Conversión LIF de prueba: <ok/err> · empaquetado: <ok/n.a.> · salida válida para worker: <ok>

## Observaciones (no aplicadas)
- <bugs/mejoras detectadas pero NO tocadas — o "ninguna">

## Entrega
- Rama: `<rama>` · commit: `<hash>` · PR: pendiente (lo abre Andrew)

## Pendiente
- <lo que queda; estado de firma/notarización si aplica>

## Proyecto
- [[conversor-lif-project-home]]
```

### Paso B — actualizar Project Home (update_project_home)
`get_project_home("conversor-lif")` para leer el body actual. Luego
`update_project_home("conversor-lif", <body>)` editando SOLO estas secciones, sin
reescribir el resto: "## Última actualización", "## Última entrega",
"## En producción", "## Últimos logs" (añade `[[slug-del-nuevo-log]]` arriba),
"## Pendiente". NUNCA pases `project_id` ni `tags` a `update_project_home`.

## 7. Límites en Axiom Omni (no negociables)
Claude Code en Axiom Omni SOLO usa:
- LECTURA: `get_project_home`, `get_latest_log_by_type` (ambos con slug "conversor-lif").
- ESCRITURA acotada: `upsert_note` (crear el session_log nuevo / flipear latest del
  anterior) y `update_project_home("conversor-lif", ...)`.
Claude Code NUNCA: `search_notes`, `archive_note`, `move_note`, `upsert_project`,
ni toca proyectos distintos de `conversor-lif`, ni edita Project Homes con
`upsert_note` (los homes solo con `update_project_home`).

## Entorno
- App de escritorio Python + landing → Claude Code (este repo).
- Documentación canónica fina → Claude.ai.
