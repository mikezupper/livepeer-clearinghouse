"""Contract-compatible liveness and readiness routes."""

from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse

from clearinghouse.application.ports import Store

router = APIRouter(prefix="/health", tags=["health"])


class Health(BaseModel):
    """Health response defined by contracts/openapi.yaml."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    status: str


class Problem(BaseModel):
    """RFC 9457 problem detail defined by contracts/openapi.yaml."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    type: str
    title: str
    status: int
    request_id: str


def store_from_request(request: Request) -> Store:
    """Resolve the process store without creating hidden module globals."""
    store: Store = request.app.state.store
    return store


StoreDependency = Annotated[Store, Depends(store_from_request)]


@router.get("/live", response_model=Health)
async def liveness() -> Health:
    """Report that the process can serve requests."""
    return Health(status="ok")


@router.get(
    "/ready",
    response_model=Health,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "A required dependency is unavailable",
            "content": {"application/problem+json": {"schema": Problem.model_json_schema()}},
        }
    },
)
async def readiness(request: Request, store: StoreDependency) -> Health | JSONResponse:
    """Report whether required durable dependencies are usable."""
    ready = await store.readiness()
    operational = getattr(request.app.state, "operational_readiness", None)
    if ready and operational is not None:
        try:
            snapshot = await operational.status("20260910_0008")
            ready = snapshot.status == "ready"
        except SQLAlchemyError:
            ready = False
    if ready:
        return Health(status="ok")
    problem = Problem(
        type="urn:livepeer:clearinghouse:dependency-unavailable",
        title="Required dependency unavailable",
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
        request_id=str(uuid4()),
    )
    return JSONResponse(
        status_code=problem.status,
        content=problem.model_dump(),
        media_type="application/problem+json",
    )
