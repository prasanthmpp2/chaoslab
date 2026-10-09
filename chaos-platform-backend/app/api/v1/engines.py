from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from app.api.deps import get_registry
from app.core.config import Settings, get_settings
from app.core.security import Principal, Role, require
from app.engines.registry import EngineRegistry

router = APIRouter(prefix="/api/v1", tags=["engines"])


@router.get("/engines")
async def list_engines(reg: EngineRegistry = Depends(get_registry), _: Principal = Depends(require(Role.VIEWER))):
    healths = await asyncio.gather(*(reg.get(n).health_check() for n in reg.names()))
    return [h.model_dump() for h in healths]


@router.get("/engines/{engine_name}/capabilities")
async def capabilities(engine_name: str, reg: EngineRegistry = Depends(get_registry),
                       _: Principal = Depends(require(Role.VIEWER))):
    eng = reg.get(engine_name)
    return {"engine": engine_name, "health": (await eng.health_check()).model_dump(),
            "capabilities": [c.model_dump() for c in await eng.capabilities()]}


@router.get("/environments")
def environments(s: Settings = Depends(get_settings), _: Principal = Depends(require(Role.VIEWER))):
    return {name: {"description": e.description, "engines": e.engines, "services": sorted(e.services),
                   "max_duration_seconds": e.max_duration_seconds, "max_targets": e.max_targets}
            for name, e in s.environments.items()} | {"_execution_enabled": s.execution_enabled}
