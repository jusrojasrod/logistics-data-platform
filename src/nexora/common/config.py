"""Configuración tipada del proyecto.

Los valores se leen del entorno. Nunca hay credenciales en el código: las
cadenas de conexión no llevan contraseña y la de cada rol se toma de su
variable de entorno (o de `.env`).
"""

from __future__ import annotations

from functools import lru_cache

from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def _with_password(dsn: str, secret: SecretStr | None) -> str:
    """Añade la contraseña a la DSN solo si la DSN no trae ya una."""
    if secret is None or conninfo_to_dict(dsn).get("password"):
        return dsn
    return make_conninfo(dsn, **{"password": secret.get_secret_value()})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NEXORA_", env_file=".env", extra="ignore")

    environment: str = "local"
    seed: int = 42
    scale: str = "demo"

    pg_dsn: str = "postgresql://etl_writer@localhost:5432/nexora"
    tms_dsn: str = "postgresql://tms_writer@localhost:5432/nexora"
    mongo_dsn: str = "mongodb://nexora:change-me@localhost:27017"

    etl_writer_password: SecretStr | None = Field(
        default=None, validation_alias="ETL_WRITER_PASSWORD"
    )
    tms_writer_password: SecretStr | None = Field(
        default=None, validation_alias="TMS_WRITER_PASSWORD"
    )

    data_dir: str = "data"

    def etl_conninfo(self) -> str:
        """Cadena de conexión de `etl_writer` con la contraseña del entorno."""
        return _with_password(self.pg_dsn, self.etl_writer_password)

    def tms_conninfo(self) -> str:
        """Cadena de conexión de `tms_writer` con la contraseña del entorno."""
        return _with_password(self.tms_dsn, self.tms_writer_password)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
