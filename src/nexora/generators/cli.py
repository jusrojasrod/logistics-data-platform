"""Command line entry point of the synthetic data generator.

The command orchestrates the generator modules and writes, into ``--out``, one
CSV per source entity plus ``defect_manifest.json``. It does not compute any
value itself: volumes come from the profile, defects from ``defects.py``.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from nexora.generators.config import HISTORY_MONTHS
from nexora.generators.defects import build_manifest, inject_defects, write_manifest
from nexora.generators.dimensions import write_dimensions
from nexora.generators.shipments import (
    SHIPMENTS_COLUMNS,
    SHIPMENTS_ENTITY,
    ShipmentTable,
    generate_shipments,
)

# Distinct base shipments per volume profile (D-002). Injected duplicates are
# written on top of this base.
SCALE_SHIPMENTS: dict[str, int] = {
    "demo": 50_000,
    "local": 200_000,
}

DEFAULT_SCALE: str = "demo"
DEFAULT_SEED: int = 42
DEFAULT_OUTPUT_ROOT: str = "data"


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser of the generator."""
    parser = argparse.ArgumentParser(
        prog="nexora-generate",
        description=(
            "Genera los CSV sintéticos de origen (envíos y dimensiones) y el manifiesto "
            "de defectos inyectados."
        ),
    )
    parser.add_argument(
        "--scale",
        choices=sorted(SCALE_SHIPMENTS),
        default=DEFAULT_SCALE,
        help="perfil de volumen: demo (50 000 envíos) o local (200 000). Por defecto: demo",
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
    base_rows = generate_shipments(SCALE_SHIPMENTS[args.scale], args.seed, args.months)
    result = inject_defects(base_rows, args.seed)

    write_dimensions(out)
    ShipmentTable(name=SHIPMENTS_ENTITY, columns=SHIPMENTS_COLUMNS, rows=result.rows).write_csv(out)
    write_manifest(out, build_manifest(args.scale, args.seed, args.months, result))

    print(
        f"generado: {out} ({result.shipments} envíos base, "
        f"{len(result.rows)} filas con duplicados, {len(result.defects)} tipos de defecto)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
