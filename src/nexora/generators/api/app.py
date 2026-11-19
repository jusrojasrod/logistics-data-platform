"""Aplicación ASGI del mock TMS: healthcheck sin token y recursos con Bearer."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query

from nexora.common.api_contracts import (
    DEFAULT_PAGE_SIZE,
    FIRST_PAGE,
    MAX_PAGE_SIZE,
    RESOURCE_CONTRACTS,
)
from nexora.generators.api.drift import DriftPlan
from nexora.generators.api.faults import FaultPlan
from nexora.generators.api.repository import ResourceRepository
from nexora.generators.api.resources import build_page


def create_app(
    repository: ResourceRepository,
    token: str,
    *,
    fault_plan: FaultPlan | None = None,
    drift_plan: DriftPlan | None = None,
) -> FastAPI:
    """Construye la aplicación ASGI del mock con su repositorio y su token.

    Sin token la aplicación no se crea: el servicio no puede servir los recursos
    sin la variable de entorno configurada. Los planes de fallos y de drift se
    inyectan para las pruebas; si se omiten, los fallos están activos y el drift
    se lee de ``NEXORA_API_DRIFT`` en el momento de crear la aplicación, no al
    importar el módulo.
    """
    if not token:
        raise ValueError("NEXORA_API_TOKEN no configurado: el mock no puede arrancar sin token")
    if fault_plan is None:
        fault_plan = FaultPlan()
    if drift_plan is None:
        drift_plan = DriftPlan(enabled=os.getenv("NEXORA_API_DRIFT", "0") == "1")

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
        fault = fault_plan.consume(contract.endpoint, page)
        if fault is not None:
            raise HTTPException(
                status_code=fault.status,
                detail="Fallo transitorio simulado",
                headers={"Retry-After": str(fault.retry_after_s)},
            )
        envelope = build_page(repository, contract, page, page_size)
        return drift_plan.apply_to_page(envelope, contract, page)

    app.include_router(router)
    return app
