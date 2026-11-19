"""Aplicación ASGI del mock TMS: healthcheck sin token y recursos con Bearer."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query

from nexora.common.api_contracts import (
    DEFAULT_PAGE_SIZE,
    FIRST_PAGE,
    MAX_PAGE_SIZE,
    RESOURCE_CONTRACTS,
)
from nexora.generators.api.repository import ResourceRepository
from nexora.generators.api.resources import build_page


def create_app(repository: ResourceRepository, token: str) -> FastAPI:
    """Construye la aplicación ASGI del mock con su repositorio y su token.

    Sin token la aplicación no se crea: el servicio no puede servir los recursos
    sin la variable de entorno configurada.
    """
    if not token:
        raise ValueError("NEXORA_API_TOKEN no configurado: el mock no puede arrancar sin token")

    def require_token(authorization: str | None = Header(default=None)) -> None:
        if authorization != f"Bearer {token}":
            raise HTTPException(
                status_code=401,
                detail="Not authenticated",
                headers={"WWW-Authenticate": "Bearer"},
            )

    app = FastAPI(title="Mock TMS API", version="1.0.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    router = APIRouter(prefix="/v1", dependencies=[Depends(require_token)])

    @router.get("/{resource}")
    def get_resource(
        resource: str,
        page: int = Query(default=FIRST_PAGE, ge=FIRST_PAGE),
        page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    ) -> dict[str, Any]:
        contract = RESOURCE_CONTRACTS.get(resource)
        if contract is None:
            raise HTTPException(status_code=404, detail="Recurso desconocido")
        return build_page(repository, contract, page, page_size)

    app.include_router(router)
    return app
