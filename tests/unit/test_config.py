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
    "NEXORA_API_PORT",
    "NEXORA_API_BASE_URL",
    "NEXORA_API_TOKEN",
    "NEXORA_API_DRIFT",
    "NEXORA_API_LIVE",
    "NEXORA_MONGO_APP_DSN",
    "MONGO_APP_PASSWORD",
    "NEXORA_WEATHER_URL",
    "NEXORA_GEOCODING_URL",
    "NEXORA_EXCHANGE_RATE_URL",
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


def test_api_defaults(clean_env: pytest.MonkeyPatch) -> None:
    s = build_settings()
    assert s.api_port == 8090
    assert s.api_base_url == "http://127.0.0.1:8090"
    assert s.api_token is None
    assert s.api_timeout_s == 10.0
    assert s.api_page_size == 200
    assert s.api_rate_limit == 5
    assert s.api_drift == 0
    assert s.api_live == 0
    assert s.api_cache_dir == "data/tmp/api_cache"
    assert s.api_seed == 42
    assert s.weather_url is None
    assert s.geocoding_url is None
    assert s.exchange_rate_url is None
    assert s.mongo_app_dsn.startswith("mongodb://nexora_app:")
    assert "authSource=admin" in s.mongo_app_dsn
    assert s.mongo_app_password is None


def test_api_fields_from_env(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("NEXORA_API_PORT", "8091")
    clean_env.setenv("NEXORA_API_BASE_URL", "http://mock-api:8000")
    clean_env.setenv("NEXORA_API_DRIFT", "1")
    clean_env.setenv("NEXORA_API_LIVE", "1")
    clean_env.setenv("NEXORA_WEATHER_URL", "http://mock/weather")
    clean_env.setenv("NEXORA_GEOCODING_URL", "http://mock/geocode")
    clean_env.setenv("NEXORA_EXCHANGE_RATE_URL", "http://mock/fx")
    clean_env.setenv("NEXORA_MONGO_APP_DSN", "mongodb://nexora_app@db:27017/?authSource=admin")
    s = build_settings()
    assert s.api_port == 8091
    assert s.api_base_url == "http://mock-api:8000"
    assert s.api_drift == 1
    assert s.api_live == 1
    assert s.weather_url == "http://mock/weather"
    assert s.geocoding_url == "http://mock/geocode"
    assert s.exchange_rate_url == "http://mock/fx"
    assert s.mongo_app_dsn == "mongodb://nexora_app@db:27017/?authSource=admin"


def test_api_token_is_secret(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("NEXORA_API_TOKEN", "token-de-prueba")
    s = build_settings()
    assert s.api_token is not None
    assert s.api_token.get_secret_value() == "token-de-prueba"
    assert "token-de-prueba" not in repr(s)


def test_mongo_app_password_is_secret(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("MONGO_APP_PASSWORD", "clave-de-prueba")
    s = build_settings()
    assert s.mongo_app_password is not None
    assert s.mongo_app_password.get_secret_value() == "clave-de-prueba"
    assert "clave-de-prueba" not in repr(s)
