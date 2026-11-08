"""Pruebas de la guardia de estructura."""

from __future__ import annotations

import pytest

from scripts.verify_structure import self_test, validate


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "Makefile",
        "conftest.py",
        "docker-compose.yml",
        "docker-compose.bi.yml",
        ".github/workflows/ci.yml",
        "docs/01_business_context.md",
        "dbt/models/core/facts/fact_shipments.sql",
        "src/nexora/common/config.py",
    ],
)
def test_allowed_paths(path: str) -> None:
    assert validate([path]) == []


@pytest.mark.parametrize(
    "path",
    [
        "random.txt",
        "secrets.env",
        "private/data.csv",
        "data/raw.csv",
        "tmp/notes.md",
    ],
)
def test_rejected_paths(path: str) -> None:
    assert validate([path])


@pytest.mark.parametrize(
    "path",
    [
        "private/note.md",
        "docs/draft.md",
        "config/app.secret",
    ],
)
def test_extra_patterns_are_applied(path: str) -> None:
    patterns = ["private/*", "*.secret", "draft.md"]
    assert validate([path], patterns)


def test_extra_patterns_do_not_block_allowed_paths() -> None:
    patterns = ["private/*", "*.secret", "draft.md"]
    assert validate(["docs/01_business_context.md", "src/nexora/common/config.py"], patterns) == []


def test_self_test_passes() -> None:
    assert self_test() == 0
