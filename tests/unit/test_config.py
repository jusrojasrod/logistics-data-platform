"""Pruebas de las cadenas de conexión construidas desde el entorno."""

from __future__ import annotations

import pytest
from psycopg.conninfo import conninfo_to_dict

from nexora.common.config import Settings

ENV_VARS = (
    "NEXORA_PG_DSN",
    "NEXORA_TMS_DSN",
    "ETL_WRITER_PASSWORD",
    "TMS_WRITER_PASSWORD",
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def build_settings() -> Settings:
    return Settings(_env_file=None)


def test_default_dsns_have_no_password(clean_env: pytest.MonkeyPatch) -> None:
    s = build_settings()
    assert "password" not in conninfo_to_dict(s.pg_dsn)
    assert "password" not in conninfo_to_dict(s.tms_dsn)
    assert conninfo_to_dict(s.pg_dsn)["user"] == "etl_writer"
    assert conninfo_to_dict(s.tms_dsn)["user"] == "tms_writer"


def test_etl_conninfo_includes_env_password(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("ETL_WRITER_PASSWORD", "from-env-etl")
    info = conninfo_to_dict(build_settings().etl_conninfo())
    assert info["password"] == "from-env-etl"
    assert info["user"] == "etl_writer"
    assert info["dbname"] == "nexora"


def test_tms_conninfo_includes_env_password(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("TMS_WRITER_PASSWORD", "from-env-tms")
    info = conninfo_to_dict(build_settings().tms_conninfo())
    assert info["password"] == "from-env-tms"
    assert info["user"] == "tms_writer"


def test_conninfo_without_password(clean_env: pytest.MonkeyPatch) -> None:
    s = build_settings()
    assert "password" not in conninfo_to_dict(s.etl_conninfo())
    assert "password" not in conninfo_to_dict(s.tms_conninfo())


def test_dsn_password_wins(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("NEXORA_PG_DSN", "postgresql://etl_writer:in-dsn@db:5432/nexora")
    clean_env.setenv("ETL_WRITER_PASSWORD", "from-env-etl")
    clean_env.setenv("NEXORA_TMS_DSN", "postgresql://tms_writer:in-dsn-tms@db:5432/nexora")
    clean_env.setenv("TMS_WRITER_PASSWORD", "from-env-tms")
    s = build_settings()
    assert conninfo_to_dict(s.etl_conninfo())["password"] == "in-dsn"
    assert conninfo_to_dict(s.tms_conninfo())["password"] == "in-dsn-tms"


def test_password_is_not_exposed_in_repr(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("ETL_WRITER_PASSWORD", "from-env-etl")
    assert "from-env-etl" not in repr(build_settings())
