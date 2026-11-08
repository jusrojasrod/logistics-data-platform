#!/usr/bin/env python3
"""Verifica que el árbol del repositorio respete la estructura pública aprobada.

La allowlist es explícita: directorios de primer nivel y archivos raíz conocidos.
Los patrones adicionales son opcionales y se leen de un archivo local, de modo
que este script no contiene nombres de rutas externas al proyecto.
"""

from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys
from pathlib import Path

ALLOWED_TOP_LEVEL = {
    "docs",
    "dbt",
    "src",
    "airflow",
    "dashboards",
    "infra",
    "tests",
    "benchmarks",
    "scripts",
    ".github",
}

ALLOWED_ROOT_FILES = {
    "README.md",
    "LICENSE",
    "Makefile",
    "pyproject.toml",
    "uv.lock",
    "conftest.py",
    ".pre-commit-config.yaml",
    ".gitignore",
    ".env.example",
}

ROOT_PATTERNS = ("docker-compose.yml", "docker-compose.*.yml")

EXCLUDED_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "data",
    "node_modules",
    ".pytest_cache",
    ".ruff_cache",
    "target",
    "logs",
    "dbt_packages",
}

DEFAULT_PATTERNS_FILE = Path(".git/info/private-patterns")


def load_patterns(path: Path) -> list[str]:
    """Lee patrones adicionales de un archivo local. Si no existe, no hay ninguno."""
    if not path.is_file():
        return []
    patterns: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if entry and not entry.startswith("#"):
            patterns.append(entry)
    return patterns


def matches_any(path: str, patterns: list[str]) -> bool:
    candidates = {path, f"/{path}", path.rsplit("/", 1)[-1]}
    return any(
        fnmatch.fnmatch(candidate, pattern) for pattern in patterns for candidate in candidates
    )


def validate(paths: list[str], patterns: list[str] | None = None) -> list[str]:
    """Devuelve la lista de problemas encontrados en las rutas dadas."""
    extra = patterns or []
    problems: list[str] = []

    for raw in paths:
        path = raw.replace("\\", "/").removeprefix("./").strip()
        if not path:
            continue
        parts = path.split("/")

        if matches_any(path, extra):
            problems.append(f"ruta no permitida por la estructura del proyecto: {path}")
            continue

        if len(parts) == 1:
            if path in ALLOWED_ROOT_FILES:
                continue
            if any(fnmatch.fnmatch(path, pattern) for pattern in ROOT_PATTERNS):
                continue
            problems.append(f"archivo raíz no permitido: {path}")
            continue

        if parts[0] not in ALLOWED_TOP_LEVEL:
            problems.append(f"directorio no permitido: {path}")

    return problems


def tracked_files(root: Path) -> list[str] | None:
    """Lista archivos rastreados por git; None si no es un repositorio."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return [item for item in result.stdout.split("\0") if item]


def walk_files(root: Path) -> list[str]:
    files: list[str] = []
    for path in root.rglob("*"):
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        if path.is_file():
            files.append(path.relative_to(root).as_posix())
    return files


def self_test() -> int:
    valid = [
        "README.md",
        "LICENSE",
        "Makefile",
        "conftest.py",
        "docker-compose.bi.yml",
        "docs/01_business_context.md",
        "dbt/models/core/facts/fact_shipments.sql",
        ".github/workflows/ci.yml",
        "src/nexora/common/config.py",
    ]
    invalid_without_patterns = ["random.txt", "secrets.env", "private/data.csv"]
    patterns = ["private/*", "*.secret", "draft.md"]
    invalid_with_patterns = ["private/note.md", "docs/draft.md", "notes.secret"]

    failures = validate(valid, patterns)
    if failures:
        print("self-test: las rutas válidas fueron rechazadas:", *failures, sep="\n  ")
        return 1

    for candidate in invalid_without_patterns:
        if not validate([candidate], patterns):
            print(f"self-test: la guardia no detectó {candidate}")
            return 1

    for candidate in invalid_with_patterns:
        if not validate([candidate], patterns):
            print(f"self-test: la guardia no detectó {candidate}")
            return 1

    print("self-test ok")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="raíz del repositorio")
    parser.add_argument("--patterns", default=None, help="archivo de patrones adicionales")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="valida la guardia con casos sintéticos",
    )
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    root = Path(args.root).resolve()
    patterns_path = Path(args.patterns) if args.patterns else root / DEFAULT_PATTERNS_FILE

    paths = tracked_files(root)
    if not paths:
        paths = walk_files(root)

    problems = validate(paths, load_patterns(patterns_path))
    if problems:
        print("Se encontraron rutas fuera de la estructura permitida:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(f"estructura ok: {len(paths)} archivos dentro de la allowlist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
