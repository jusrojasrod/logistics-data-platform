"""Command line entry point of the synthetic data generator.

The command writes, into ``--out``, one CSV per source entity plus
``defect_manifest.json``. It does not compute any value itself: volumes come
from the profile and the files from ``dataset.write_dataset``, which streams
the rows to disk, so every profile runs in bounded memory.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from nexora.generators.config import HISTORY_MONTHS
from nexora.generators.dataset import write_dataset

# Distinct base shipments per volume profile: 50 000 in demo, 200 000 in local
# and 2 000 000 in benchmark. Injected duplicates are written on top of this
# base. The benchmark profile only measures generation to files: its folder is
# never published to the operational database.
SCALE_SHIPMENTS: dict[str, int] = {
    "demo": 50_000,
    "local": 200_000,
    "benchmark": 2_000_000,
}

DEFAULT_SCALE: str = "demo"
DEFAULT_SEED: int = 42
DEFAULT_OUTPUT_ROOT: str = "data"


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser of the generator."""
    parser = argparse.ArgumentParser(
        prog="nexora-generate",
        description=(
            "Genera los CSV sintéticos de origen del TMS (envíos, dimensiones, flota, viajes, "
            "tramos, paradas, eventos, combustible, tenders e incidencias), del ERP (tarifas, "
            "facturas, líneas de factura, pagos, liquidaciones de transportistas y costos de "
            "flota) y del CRM (contactos, contratos y leads), con las filas tardías del ERP y "
            "del CRM en la subcarpeta late/, y el manifiesto de defectos inyectados."
        ),
    )
    parser.add_argument(
        "--scale",
        choices=sorted(SCALE_SHIPMENTS),
        default=DEFAULT_SCALE,
        help=(
            "perfil de volumen: demo (50 000 envíos), local (200 000) o benchmark "
            "(2 000 000, solo para medir la generación a archivos). Por defecto: demo"
        ),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"semilla de la generación. Por defecto: {DEFAULT_SEED}",
    )
    parser.add_argument(
        "--months",
        type=int,
        default=HISTORY_MONTHS,
        help=f"meses de historia hasta la fecha de corte. Por defecto: {HISTORY_MONTHS}",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="carpeta de salida. Por defecto: data/<perfil>",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the generator and return the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.months < 1:
        parser.error(f"--months debe ser positivo: {args.months}")

    out = args.out if args.out is not None else Path(DEFAULT_OUTPUT_ROOT) / args.scale
    summary = write_dataset(out, args.scale, SCALE_SHIPMENTS[args.scale], args.seed, args.months)

    print(
        f"generado: {out} ({summary.shipments} envíos base, "
        f"{len(summary.defects)} tipos de defecto)"
    )
    for name, rows in summary.files.items():
        print(f"  {name}: {rows} filas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
