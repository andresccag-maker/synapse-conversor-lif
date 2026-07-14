"use strict";

const state = {
  mode: "lif",         // "lif" | "nd2" | "tif"
  lifPath: null,
  nd2Path: null,
  outDir: null,
  info: null,
  excluded: new Set(), // canales 0-indexados excluidos
  maxChannels: 0,
  lutNames: [],        // [{name, rgb}], igual orden que canal 0..n-1
  tifDir: null,
  tifScan: null,       // resultado de inspect_tif_folder
};

const el = (id) => document.getElementById(id);

function setOutputPath(p) {
  state.outDir = p;
  el("out-path").textContent = p || "—";
  updateConvertEnabled();
}

function updateConvertEnabled() {
  let ready;
  if (state.mode === "tif") {
    ready = !!(state.tifDir && state.outDir && state.tifScan && state.tifScan.n_files > 0);
  } else if (state.mode === "nd2") {
    ready = !!(state.nd2Path && state.outDir && state.info);
  } else {
    ready = !!(state.lifPath && state.outDir && state.info);
  }
  el("btn-convert").disabled = !ready;
}

function resetFlowState() {
  // Al cambiar de modo, olvida el fichero cargado del flujo anterior: LIF y ND2
  // COMPARTEN state.info/excluded y los campos experimento/pocillo, así que sin
  // este reset una conversión podría heredar los parámetros/estructura de otro
  // fichero (salida mal archivada + exclusión de canal equivocada → datos
  // incorrectos y silenciosos hacia el worker). La carpeta de salida se conserva.
  state.info = null;
  state.excluded = new Set();
  state.maxChannels = 0;
  state.lutNames = [];
  state.lifPath = null;
  state.nd2Path = null;
  state.tifDir = null;
  state.tifScan = null;
  ["exp", "pocillo", "base-name"].forEach((id) => { const e = el(id); if (e) e.value = ""; });
  ["lif-meta", "nd2-meta", "tif-meta"].forEach((id) => { const e = el(id); if (e) e.hidden = true; });
  ["lif-path", "nd2-path", "tif-path"].forEach((id) => { const e = el(id); if (e) e.textContent = "—"; });
  ["scan-experiments", "scan-pocillos", "scan-images", "scan-files"].forEach(
    (id) => { const e = el(id); if (e) e.textContent = "0"; },
  );
  const chn = el("scan-channels"); if (chn) chn.textContent = "—";
  const sc = el("series-count"); if (sc) sc.textContent = "";
  const box = el("result"); if (box) box.hidden = true;
  el("progress-bar").style.width = "0%";
  el("progress-text").textContent = "listo";
  renderChips();
  renderSeries();
}

function setMode(mode) {
  const changed = state.mode !== mode;
  state.mode = mode;
  if (changed) resetFlowState();
  const isLif = mode === "lif";
  const isNd2 = mode === "nd2";
  const isTif = mode === "tif";

  el("mode-lif").classList.toggle("active", isLif);
  el("mode-nd2").classList.toggle("active", isNd2);
  el("mode-tif").classList.toggle("active", isTif);
  el("mode-lif").setAttribute("aria-selected", String(isLif));
  el("mode-nd2").setAttribute("aria-selected", String(isNd2));
  el("mode-tif").setAttribute("aria-selected", String(isTif));

  el("hero-text").innerHTML = isTif
    ? "TIFFs ya exportados (<code>Experimento/Pocillo/*.tif</code>) → MIP por canal · local, sin subir nada"
    : isNd2
    ? "Nikon <code>.nd2</code> → TIFFs por canal · procesamiento local, sin subir nada"
    : "Leica <code>.lif</code> → TIFFs por canal · procesamiento local, sin subir nada";

  // Card 1: origen
  el("lif-block").hidden = !isLif;
  el("nd2-block").hidden = !isNd2;
  el("tif-block").hidden = !isTif;
  // Card 2: campos (experimento/pocillo en LIF y ND2; base solo en TIF)
  el("field-exp").hidden = isTif;
  el("field-pocillo").hidden = isTif;
  el("field-base").hidden = !isTif;
  // Proyección: en TIF se fija a MIP; en LIF/ND2 se puede elegir MIP o Z-stack
  const proj = el("projection");
  if (isTif) {
    proj.value = "mip";
    proj.disabled = true;
  } else {
    proj.disabled = false;
  }
  setProjectionHint();
  // Card 3 (ND2 comparte con LIF la lista de series + chips de exclusión)
  el("card3-title").textContent = isTif ? "TIFFs detectados" : "Series detectadas";
  el("series-section").hidden = isTif;
  el("tif-scan-section").hidden = !isTif;

  updateConvertEnabled();
}

function fmtPx(v) {
  if (v == null) return "µm/px: no disponible";
  return "µm/px: " + v.toFixed(4);
}
function fmtBitDepth(arr) {
  if (!arr || !arr.length) return "?";
  return arr.join(",");
}

function lutNameFor(c) {
  if (state.lutNames && c < state.lutNames.length) {
    return state.lutNames[c].name || "—";
  }
  return "—";
}

function renderSeries() {
  const list = el("series-list");
  list.innerHTML = "";
  if (!state.info || !state.info.series.length) {
    list.innerHTML = '<div class="empty">Sin archivo cargado.</div>';
    return;
  }
  for (const s of state.info.series) {
    const node = document.createElement("div");
    node.className = "series";
    node.dataset.index = s.index;

    const imageLabel = "Image" + String(s.index + 1).padStart(3, "0");
    const head = document.createElement("div");
    head.className = "series-head";
    head.innerHTML =
      '<div><div class="series-name"></div></div>' +
      '<div class="series-folder"></div>';
    head.querySelector(".series-name").textContent = s.name;
    head.querySelector(".series-folder").textContent = imageLabel;

    const dims = document.createElement("div");
    dims.className = "series-dims";
    dims.textContent =
      s.width + "×" + s.height +
      " · Z=" + s.n_z +
      " · C=" + s.n_channels +
      " · " + fmtPx(s.pixel_size_um) +
      " · bit_depth=" + fmtBitDepth(s.bit_depth);

    const thumbs = document.createElement("div");
    thumbs.className = "thumbs";
    for (let c = 0; c < s.n_channels; c++) {
      const t = document.createElement("div");
      t.className = "thumb";
      t.dataset.series = s.index;
      t.dataset.channel = c;
      t.innerHTML =
        '<div class="thumb-img">…</div>' +
        '<div class="thumb-label">C=' + c + " · " + lutNameFor(c) + "</div>";
      if (state.excluded.has(c)) t.classList.add("excluded");
      thumbs.appendChild(t);
    }

    node.appendChild(head);
    node.appendChild(dims);
    node.appendChild(thumbs);
    list.appendChild(node);
  }
}

function lutRgbCss(c) {
  if (state.lutNames && c < state.lutNames.length) {
    const rgb = state.lutNames[c].rgb;
    if (rgb && rgb.length === 3) {
      return `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
    }
  }
  return null;
}

function renderChips() {
  const root = el("chips-excl");
  root.innerHTML = "";
  if (!state.maxChannels) {
    root.innerHTML = '<span class="muted" style="font-size:12px">—</span>';
    return;
  }
  for (let c = 0; c < state.maxChannels; c++) {
    const chip = document.createElement("span");
    chip.className = "chip" + (state.excluded.has(c) ? " active" : "");
    const swatchColor = lutRgbCss(c);
    if (swatchColor) chip.style.setProperty("--swatch", swatchColor);
    const swatch = document.createElement("span");
    swatch.className = "swatch";
    chip.appendChild(swatch);
    const label = document.createElement("span");
    label.textContent = "C=" + c;
    chip.appendChild(label);
    chip.addEventListener("click", () => {
      if (state.excluded.has(c)) state.excluded.delete(c);
      else state.excluded.add(c);
      renderChips();
      document.querySelectorAll(".thumb").forEach((t) => {
        const ch = parseInt(t.dataset.channel, 10);
        t.classList.toggle("excluded", state.excluded.has(ch));
      });
    });
    root.appendChild(chip);
  }
}

function setProjectionHint() {
  if (state.mode === "tif") {
    el("proj-hint").textContent =
      "TIF → MIP: proyección máxima por canal. Cada .tif (Z-stack) → un plano (1, Y, X).";
    return;
  }
  const mode = el("projection").value;
  el("proj-hint").textContent =
    mode === "mip"
      ? "MIP: cada canal se guarda como un único plano (1, Y, X) preservando dtype."
      : "Z-stack completo: idéntico a la macro original, sin proyección.";
}

function renderTifScan(scan) {
  el("scan-experiments").textContent = scan.n_experiments;
  el("scan-pocillos").textContent = scan.n_pocillos;
  el("scan-images").textContent = scan.n_images;
  el("scan-files").textContent = scan.n_files;
  el("scan-channels").textContent =
    scan.raw_channels && scan.raw_channels.length
      ? scan.raw_channels.map((c) => "c" + c).join(", ")
      : "—";
  const hint = el("scan-hint");
  if (hint) {
    hint.textContent = scan.n_files
      ? scan.n_files + " ficheros · cada canal de cada imagen → MIP (naming worker, C 0-indexado)."
      : "No se encontraron .tif bajo la carpeta elegida.";
  }
  const count = el("series-count");
  if (count) count.textContent = scan.n_files + " ficheros";
}

// -------- pywebview bridge --------

function whenBridgeReady() {
  return new Promise((resolve) => {
    if (window.pywebview && window.pywebview.api) {
      resolve();
      return;
    }
    window.addEventListener("pywebviewready", () => resolve(), { once: true });
  });
}

window.pyEvent = function (type, payload) {
  switch (type) {
    case "preview":
      handlePreview(payload);
      break;
    case "previews_done":
      break;
    case "progress":
      handleProgress(payload);
      break;
    case "done":
      handleDone(payload);
      break;
    case "error":
      handleError(payload);
      break;
  }
};

function handlePreview(p) {
  const sel =
    '.thumb[data-series="' + p.series + '"][data-channel="' + p.channel + '"]';
  const t = document.querySelector(sel);
  if (!t) return;
  const slot = t.querySelector(".thumb-img");
  if (p.uri) {
    slot.innerHTML = "";
    const img = document.createElement("img");
    img.src = p.uri;
    slot.appendChild(img);
  } else {
    slot.textContent = "—";
  }
  if (p.lut_name) {
    const label = t.querySelector(".thumb-label");
    if (label) label.textContent = "C=" + p.channel + " · " + p.lut_name;
  }
}

function handleProgress(p) {
  const pct = p.total ? Math.round((p.done / p.total) * 100) : 0;
  el("progress-bar").style.width = pct + "%";
  el("progress-text").textContent =
    "[" + p.done + "/" + p.total + "] " + p.folder;
}

function handleDone(s) {
  el("progress-bar").style.width = "100%";
  el("progress-text").textContent =
    state.mode === "tif"
      ? "Listo · " + (s.pocillos_written || 0) + " pocillo(s) · " + (s.files_written || 0) + " MIPs"
      : "Listo · " + (s.series_written || 0) + " serie(s) · " + (s.files_written || 0) + " ficheros";
  const box = el("result");
  box.hidden = false;
  box.className = "result ok";
  box.innerHTML =
    "Conversión completada. Salida: <code></code>";
  box.querySelector("code").textContent = s.output_root || s.output_dir || "";
}

function handleError(e) {
  const box = el("result");
  box.hidden = false;
  box.className = "result err";
  box.textContent = "Error: " + e.message + (e.trace ? "\n\n" + e.trace : "");
  el("progress-text").textContent = "error";
}

// -------- wire up --------

async function init() {
  await whenBridgeReady();
  const api = window.pywebview.api;

  async function loadLif(p) {
    state.lifPath = p;
    el("lif-path").textContent = p;
    el("progress-text").textContent = "inspeccionando…";
    try {
      const info = await api.inspect(p);
      state.info = info;
      state.lutNames = info.channel_luts || [];
      state.maxChannels = info.series.reduce(
        (m, s) => Math.max(m, s.n_channels),
        0,
      );
      state.excluded = new Set();
      el("exp").value = info.suggested_experiment;
      el("pocillo").value = info.suggested_pocillo;
      el("lif-meta").hidden = false;
      el("lif-summary").textContent =
        info.n_series + " series · SHA256 " + info.sha256.slice(0, 12) + "…";
      const seriesCount = el("series-count");
      if (seriesCount) seriesCount.textContent = info.n_series + " series";
      renderChips();
      renderSeries();
      el("progress-text").textContent = "listo";
      updateConvertEnabled();
    } catch (err) {
      handleError({ message: String(err), trace: "" });
    }
  }

  async function loadTifFolder(p) {
    state.tifDir = p;
    el("tif-path").textContent = p;
    el("progress-text").textContent = "escaneando…";
    try {
      const scan = await api.inspect_tif_folder(p);
      state.tifScan = scan;
      renderTifScan(scan);
      el("tif-meta").hidden = false;
      el("tif-summary").textContent =
        scan.n_files + " .tif · " + scan.n_pocillos + " pocillo(s) · " + scan.n_images + " imagen(es)";
      el("progress-text").textContent = scan.n_files ? "listo" : "sin .tif en la carpeta";
      updateConvertEnabled();
    } catch (err) {
      handleError({ message: String(err), trace: "" });
    }
  }

  async function loadNd2(p) {
    state.nd2Path = p;
    el("nd2-path").textContent = p;
    el("progress-text").textContent = "inspeccionando…";
    try {
      const info = await api.inspect_nd2(p);
      if (info && info.error) {
        // Preflight rechazó el ND2 (T>1, RGB, eje raro…): mensaje legible.
        state.nd2Path = null;
        el("nd2-meta").hidden = false;
        el("nd2-summary").textContent = "no convertible";
        handleError({ message: info.error, trace: "" });
        el("progress-text").textContent = "ND2 no convertible";
        updateConvertEnabled();
        return;
      }
      state.info = info;
      state.lutNames = info.channel_luts || [];
      state.maxChannels = info.series.reduce(
        (m, s) => Math.max(m, s.n_channels),
        0,
      );
      state.excluded = new Set();
      el("exp").value = info.suggested_experiment;
      el("pocillo").value = info.suggested_pocillo;
      el("nd2-meta").hidden = false;
      el("nd2-summary").textContent =
        info.n_series + " posición(es) · SHA256 " + info.sha256.slice(0, 12) + "…";
      const seriesCount = el("series-count");
      if (seriesCount) seriesCount.textContent = info.n_series + " posiciones";
      renderChips();
      renderSeries();
      el("progress-text").textContent = "listo";
      updateConvertEnabled();
    } catch (err) {
      // Excepción no-Nd2Error (p. ej. IO al hashear): no dejes el ND2 roto como
      // "cargado y listo" — limpia el estado para que Convertir quede deshabilitado.
      state.nd2Path = null;
      state.info = null;
      handleError({ message: String(err), trace: "" });
      updateConvertEnabled();
    }
  }

  el("mode-lif").addEventListener("click", () => setMode("lif"));
  el("mode-nd2").addEventListener("click", () => setMode("nd2"));
  el("mode-tif").addEventListener("click", () => setMode("tif"));

  el("btn-pick-tif").addEventListener("click", async () => {
    const p = await api.choose_tif_folder();
    if (!p) return;
    await loadTifFolder(p);
  });

  el("btn-pick-lif").addEventListener("click", async () => {
    const p = await api.choose_lif();
    if (!p) return;
    await loadLif(p);
  });

  el("btn-pick-nd2").addEventListener("click", async () => {
    const p = await api.choose_nd2();
    if (!p) return;
    await loadNd2(p);
  });

  // Drag & drop sobre la dropzone (complementa el botón).
  const dz = el("dropzone");
  if (dz) {
    ["dragenter", "dragover"].forEach((ev) =>
      dz.addEventListener(ev, (e) => {
        e.preventDefault();
        e.stopPropagation();
        dz.classList.add("dragover");
      }),
    );
    ["dragleave", "drop"].forEach((ev) =>
      dz.addEventListener(ev, (e) => {
        e.preventDefault();
        e.stopPropagation();
        dz.classList.remove("dragover");
      }),
    );
    dz.addEventListener("drop", async (e) => {
      const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (!file) return;
      const path = file.path || file.name; // pywebview expone .path; navegador puro no.
      if (!path.toLowerCase().endsWith(".lif")) {
        handleError({ message: "Solo se aceptan ficheros .lif", trace: "" });
        return;
      }
      await loadLif(path);
    });
  }

  // Drag & drop del .nd2 (espeja la dropzone de LIF).
  const ndz = el("nd2-dropzone");
  if (ndz) {
    ["dragenter", "dragover"].forEach((ev) =>
      ndz.addEventListener(ev, (e) => {
        e.preventDefault();
        e.stopPropagation();
        ndz.classList.add("dragover");
      }),
    );
    ["dragleave", "drop"].forEach((ev) =>
      ndz.addEventListener(ev, (e) => {
        e.preventDefault();
        e.stopPropagation();
        ndz.classList.remove("dragover");
      }),
    );
    ndz.addEventListener("drop", async (e) => {
      const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (!file) return;
      const path = file.path || file.name;
      if (!path.toLowerCase().endsWith(".nd2")) {
        handleError({ message: "Solo se aceptan ficheros .nd2", trace: "" });
        return;
      }
      await loadNd2(path);
    });
  }

  el("btn-pick-out").addEventListener("click", async () => {
    const p = await api.choose_output();
    if (!p) return;
    setOutputPath(p);
  });

  el("projection").addEventListener("change", setProjectionHint);
  setProjectionHint();

  el("btn-convert").addEventListener("click", async () => {
    el("result").hidden = true;
    el("progress-bar").style.width = "0%";
    el("progress-text").textContent = "iniciando…";
    if (state.mode === "tif") {
      await api.run_convert_tif({
        input_dir: state.tifDir,
        output_dir: state.outDir,
        base_name: el("base-name").value || "",
      });
      return;
    }
    const opts = {
      output_dir: state.outDir,
      experiment: el("exp").value || "Experimento",
      pocillo: el("pocillo").value || "General",
      exclude_channels_0based: Array.from(state.excluded.values()).sort(
        (a, b) => a - b,
      ),
      projection: el("projection").value,
    };
    if (state.mode === "nd2") {
      await api.run_convert_nd2(opts);
    } else {
      await api.run_convert(opts);
    }
  });

  setMode("lif");
}

document.addEventListener("DOMContentLoaded", init);
