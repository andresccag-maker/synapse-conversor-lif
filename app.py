"""SYN APSE — Conversor LIF: app de escritorio con pywebview."""

from __future__ import annotations

import json
import sys
import threading
import traceback
from dataclasses import asdict
from pathlib import Path

import webview

import core


class Api:
    def __init__(self) -> None:
        self._window = None
        self._lif_path: str | None = None
        self._tif_dir: str | None = None
        self._nd2_path: str | None = None

    def set_window(self, window) -> None:
        self._window = window

    # ---------------- file dialogs ----------------

    def choose_lif(self):
        if self._window is None:
            return None
        file_types = ("Leica LIF (*.lif)", "Todos los archivos (*.*)")
        result = self._window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=False,
            file_types=file_types,
        )
        if not result:
            return None
        path = result[0] if isinstance(result, (list, tuple)) else result
        self._lif_path = path
        return path

    def choose_nd2(self):
        if self._window is None:
            return None
        file_types = ("Nikon ND2 (*.nd2)", "Todos los archivos (*.*)")
        result = self._window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=False,
            file_types=file_types,
        )
        if not result:
            return None
        path = result[0] if isinstance(result, (list, tuple)) else result
        self._nd2_path = path
        return path

    def choose_output(self):
        if self._window is None:
            return None
        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        if not result:
            return None
        return result[0] if isinstance(result, (list, tuple)) else result

    def choose_tif_folder(self):
        if self._window is None:
            return None
        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        if not result:
            return None
        path = result[0] if isinstance(result, (list, tuple)) else result
        self._tif_dir = path
        return path

    # ---------------- inspection ----------------

    def inspect(self, path: str):
        info, _ = core.read_lif_info(path, with_previews=False)
        payload = asdict(info)
        self._lif_path = path
        threading.Thread(
            target=self._previews_worker,
            args=(path,),
            daemon=True,
        ).start()
        return payload

    def _previews_worker(self, path: str) -> None:
        try:
            lif = core._open_lif(path)
            luts = core.read_channel_luts(lif)
            for idx, limg in enumerate(lif.get_iter_image()):
                n_channels = int(getattr(limg, "channels", 1) or 1)
                for c in range(n_channels):
                    lut = luts[c] if c < len(luts) else None
                    lut_name = lut["name"] if lut else ""
                    rgb = tuple(lut["rgb"]) if lut else None
                    try:
                        stack = core._read_channel_stack(limg, c=c)
                        uri = core.make_preview_png_b64(stack, rgb=rgb)
                        self._emit("preview", {
                            "series": idx,
                            "channel": c,
                            "uri": uri,
                            "lut_name": lut_name,
                        })
                    except Exception as exc:
                        self._emit("preview", {
                            "series": idx,
                            "channel": c,
                            "uri": None,
                            "lut_name": lut_name,
                            "error": str(exc),
                        })
            self._emit("previews_done", {})
        except Exception as exc:
            self._emit("error", {
                "message": f"Previews failed: {exc}",
                "trace": traceback.format_exc(),
            })

    # ---------------- inspección ND2 ----------------

    def inspect_nd2(self, path: str):
        # El preflight dimensional corre dentro de read_nd2_info; si el ND2 no es
        # convertible (T>1, RGB, eje raro…), devolvemos un error legible en vez de
        # reventar la promesa del front.
        try:
            info, _ = core.read_nd2_info(path, with_previews=False)
        except core.Nd2Error as exc:
            return {"error": str(exc)}
        payload = asdict(info)
        self._nd2_path = path
        threading.Thread(
            target=self._nd2_previews_worker,
            args=(path,),
            daemon=True,
        ).start()
        return payload

    def _nd2_previews_worker(self, path: str) -> None:
        try:
            f = core._open_nd2(path)
            try:
                sizes = core._nd2_sizes(f)
                n_pos = int(sizes.get("P", 1) or 1)
                n_c = int(sizes.get("C", 1) or 1)
                axes = core._nd2_axis_order(f)
                lazy = core._nd2_to_lazy(f)
                names = core._nd2_channel_names(f, n_c)
                for pos in range(n_pos):
                    for c in range(n_c):
                        lut_name = names[c] if c < len(names) else ""
                        rgb = tuple(core._lut_rgb_for(lut_name))
                        try:
                            stack = core._read_nd2_stack(lazy, axes, pos, c)
                            uri = core.make_preview_png_b64(stack, rgb=rgb)
                            self._emit("preview", {
                                "series": pos,
                                "channel": c,
                                "uri": uri,
                                "lut_name": lut_name,
                            })
                        except Exception as exc:
                            self._emit("preview", {
                                "series": pos,
                                "channel": c,
                                "uri": None,
                                "lut_name": lut_name,
                                "error": str(exc),
                            })
                self._emit("previews_done", {})
            finally:
                core._nd2_close(f)
        except Exception as exc:
            self._emit("error", {
                "message": f"Previews ND2 failed: {exc}",
                "trace": traceback.format_exc(),
            })

    # ---------------- conversion ----------------

    def run_convert(self, opts_dict: dict):
        if not self._lif_path:
            self._emit("error", {"message": "No hay .lif seleccionado", "trace": ""})
            return False
        path = self._lif_path
        threading.Thread(
            target=self._convert_worker,
            args=(path, opts_dict),
            daemon=True,
        ).start()
        return True

    def _convert_worker(self, path: str, opts_dict: dict) -> None:
        try:
            opts = core.ConvertOptions(
                output_dir=opts_dict["output_dir"],
                experiment=opts_dict.get("experiment", "Experimento"),
                pocillo=opts_dict.get("pocillo", "General"),
                exclude_channels_0based=[
                    int(c) for c in opts_dict.get("exclude_channels_0based", [])
                ],
                projection=opts_dict.get("projection", "mip"),
                series_indices=opts_dict.get("series_indices"),
            )

            def cb(done: int, total: int, folder: str) -> None:
                self._emit("progress", {"done": done, "total": total, "folder": folder})

            summary = core.convert(path, opts, progress_cb=cb)
            self._emit("done", summary)
        except Exception as exc:
            self._emit("error", {
                "message": str(exc),
                "trace": traceback.format_exc(),
            })

    # ---------------- conversión ND2 ----------------

    def run_convert_nd2(self, opts_dict: dict):
        if not self._nd2_path:
            self._emit("error", {"message": "No hay .nd2 seleccionado", "trace": ""})
            return False
        path = self._nd2_path
        threading.Thread(
            target=self._convert_nd2_worker,
            args=(path, opts_dict),
            daemon=True,
        ).start()
        return True

    def _convert_nd2_worker(self, path: str, opts_dict: dict) -> None:
        try:
            opts = core.ConvertOptions(
                output_dir=opts_dict["output_dir"],
                experiment=opts_dict.get("experiment", "Experimento"),
                pocillo=opts_dict.get("pocillo", "General"),
                exclude_channels_0based=[
                    int(c) for c in opts_dict.get("exclude_channels_0based", [])
                ],
                projection=opts_dict.get("projection", "mip"),
                series_indices=opts_dict.get("series_indices"),
            )

            def cb(done: int, total: int, folder: str) -> None:
                self._emit("progress", {"done": done, "total": total, "folder": folder})

            summary = core.convert_nd2(path, opts, progress_cb=cb)
            self._emit("done", summary)
        except core.Nd2Error as exc:
            # Preflight/lectura: mensaje legible para un científico, sin stacktrace.
            self._emit("error", {"message": str(exc), "trace": ""})
        except Exception as exc:
            self._emit("error", {
                "message": str(exc),
                "trace": traceback.format_exc(),
            })

    # ---------------- modo TIF → MIP ----------------

    def inspect_tif_folder(self, path: str):
        self._tif_dir = path
        scan = core.scan_tif_folder(path)
        return asdict(scan)

    def run_convert_tif(self, opts_dict: dict):
        tif_dir = opts_dict.get("input_dir") or self._tif_dir
        if not tif_dir:
            self._emit("error", {"message": "No hay carpeta de TIFFs seleccionada", "trace": ""})
            return False
        threading.Thread(
            target=self._convert_tif_worker,
            args=(tif_dir, opts_dict),
            daemon=True,
        ).start()
        return True

    def _convert_tif_worker(self, tif_dir: str, opts_dict: dict) -> None:
        try:
            opts = core.TifFolderOptions(
                input_dir=tif_dir,
                output_dir=opts_dict["output_dir"],
                base_name=opts_dict.get("base_name") or None,
            )

            def cb(done: int, total: int, folder: str) -> None:
                self._emit("progress", {"done": done, "total": total, "folder": folder})

            summary = core.convert_tif_folder(opts, progress_cb=cb)
            # Normaliza el resumen para que el front muestre el mismo "done"
            summary.setdefault("series_written", summary.get("pocillos_written", 0))
            summary.setdefault("output_root", summary.get("output_dir", ""))
            self._emit("done", summary)
        except Exception as exc:
            self._emit("error", {
                "message": str(exc),
                "trace": traceback.format_exc(),
            })

    # ---------------- bridge ----------------

    def _emit(self, type_: str, payload) -> None:
        if self._window is None:
            return
        js = "window.pyEvent({type}, {payload})".format(
            type=json.dumps(type_),
            payload=json.dumps(payload),
        )
        try:
            self._window.evaluate_js(js)
        except Exception:
            pass


def main() -> None:
    # Autocomprobación del lector ND2 dentro del binario CONGELADO (usada en CI):
    #   ./SYN_APSE_Conversor_LIF --self-test-nd2 <fixture.nd2>
    # Se intercepta ANTES de abrir la ventana; delega en el CLI headless y sale
    # con su código (0 OK / 1 fallo). No abre GUI.
    if "--self-test-nd2" in sys.argv:
        import convert_cli
        raise SystemExit(convert_cli.main(sys.argv[1:]))

    api = Api()
    here = Path(__file__).resolve().parent
    index_path = str(here / "web" / "index.html")
    window = webview.create_window(
        "SYN APSE — Conversor LIF",
        index_path,
        js_api=api,
        width=1040,
        height=760,
        min_size=(880, 600),
        background_color="#0d1117",
    )
    api.set_window(window)
    webview.start(debug=False)


if __name__ == "__main__":
    main()
