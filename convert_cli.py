"""SYN APSE — Conversor LIF: CLI headless."""

from __future__ import annotations

import argparse
import json
import sys

import core


def _print_info(info) -> None:
    print(f"filename : {info.filename}")
    print(f"sha256   : {info.sha256}")
    print(f"n_series : {info.n_series}")
    print(f"sugerido : experimento={info.suggested_experiment}  pocillo={info.suggested_pocillo}")
    if info.channel_luts:
        luts = ", ".join(
            f"C={i}:{lut['name'] or '?'}" for i, lut in enumerate(info.channel_luts)
        )
        print(f"luts     : {luts}")
    print("series:")
    for s in info.series:
        px = f"{s.pixel_size_um:.4f} µm/px" if s.pixel_size_um else "?"
        bd = ",".join(str(b) for b in s.bit_depth) if s.bit_depth else "?"
        print(
            f"  [{s.index:>3}] {s.name}  {s.width}x{s.height}  Z={s.n_z}  "
            f"C={s.n_channels}  px={px}  bit_depth={bd}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="convert_cli",
        description="SYN APSE — Conversor LIF (headless)",
    )
    parser.add_argument("lif", help="ruta al archivo .lif")
    parser.add_argument("-o", "--output", help="carpeta de salida")
    parser.add_argument("--experiment", help="nombre del experimento (override sugerencia)")
    parser.add_argument("--pocillo", help="nombre del pocillo (override sugerencia)")
    parser.add_argument(
        "--exclude",
        type=int,
        nargs="*",
        default=[],
        help="canales a excluir (0-indexados, Bio-Formats), ej: --exclude 2",
    )
    proj = parser.add_mutually_exclusive_group()
    proj.add_argument("--mip", action="store_true", help="proyección MIP (por defecto)")
    proj.add_argument("--zstack", action="store_true", help="conservar Z-stack completo")
    parser.add_argument("--info", action="store_true", help="solo imprimir metadatos")

    args = parser.parse_args(argv)

    info, _ = core.read_lif_info(args.lif, with_previews=False)
    _print_info(info)

    if args.info:
        return 0

    if not args.output:
        print("ERROR: se requiere -o/--output cuando no se pasa --info", file=sys.stderr)
        return 2

    projection = "none" if args.zstack else "mip"
    experiment = args.experiment or info.suggested_experiment
    pocillo = args.pocillo or info.suggested_pocillo

    opts = core.ConvertOptions(
        output_dir=args.output,
        experiment=experiment,
        pocillo=pocillo,
        exclude_channels_0based=list(args.exclude or []),
        projection=projection,
    )

    def cb(done: int, total: int, folder: str) -> None:
        print(f"[{done}/{total}] {folder}")

    summary = core.convert(args.lif, opts, progress_cb=cb)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
