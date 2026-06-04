"""SYN APSE — Conversor LIF: app de escritorio con pywebview."""

from __future__ import annotations

import json
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

    def choose_output(self):
        if self._window is None:
            return None
        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        if not result:
            return None
        return result[0] if isinstance(result, (list, tuple)) else result

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
