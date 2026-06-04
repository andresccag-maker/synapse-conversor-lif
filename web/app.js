"use strict";

const state = {
  lifPath: null,
  outDir: null,
  info: null,
  excluded: new Set(), // canales 0-indexados excluidos
  maxChannels: 0,
  lutNames: [],        // [{name, rgb}], igual orden que canal 0..n-1
};

const el = (id) => document.getElementById(id);

function setOutputPath(p) {
  state.outDir = p;
  el("out-path").textContent = p || "—";
  updateConvertEnabled();
}

function updateConvertEnabled() {
  el("btn-convert").disabled = !(state.lifPath && state.outDir && state.info);
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
  const mode = el("projection").value;
  el("proj-hint").textContent =
    mode === "mip"
      ? "MIP: cada canal se guarda como un único plano (1, Y, X) preservando dtype."
      : "Z-stack completo: idéntico a la macro original, sin proyección.";
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
    "Listo · " + s.series_written + " serie(s) · " +
    (s.files_written || 0) + " ficheros";
  const box = el("result");
  box.hidden = false;
  box.className = "result ok";
  box.innerHTML =
    "Conversión completada. Salida: <code></code>";
  box.querySelector("code").textContent = s.output_root;
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

  el("btn-pick-lif").addEventListener("click", async () => {
    const p = await api.choose_lif();
    if (!p) return;
    await loadLif(p);
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

  el("btn-pick-out").addEventListener("click", async () => {
    const p = await api.choose_output();
    if (!p) return;
    setOutputPath(p);
  });

  el("projection").addEventListener("change", setProjectionHint);
  setProjectionHint();

  el("btn-convert").addEventListener("click", async () => {
    const opts = {
      output_dir: state.outDir,
      experiment: el("exp").value || "Experimento",
      pocillo: el("pocillo").value || "General",
      exclude_channels_0based: Array.from(state.excluded.values()).sort(
        (a, b) => a - b,
      ),
      projection: el("projection").value,
    };
    el("result").hidden = true;
    el("progress-bar").style.width = "0%";
    el("progress-text").textContent = "iniciando…";
    await api.run_convert(opts);
  });
}

document.addEventListener("DOMContentLoaded", init);
