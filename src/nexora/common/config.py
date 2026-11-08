"""Configuración tipada del proyecto.

Los valores se leen del entorno. Nunca hay credenciales en el código.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NEXORA_", env_file=".env", extra="ignore")

    environment: str = "local"
    seed: int = 42
    scale: str = "demo"

    pg_dsn: str = "postgresql://etl_writer:etl_writer_dev@localhost:5432/nexora"
    mongo_dsn: str = "mongodb://nexora:change-me@localhost:27017"

    data_dir: str = "data"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
