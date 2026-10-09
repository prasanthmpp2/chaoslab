from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_registry
from app.core.config import Settings, get_settings
from app.core.security import Principal, Role, require
from app.db.session import get_db
from app.engines.registry import EngineRegistry
from app.models.entities import ExperimentRun

router = APIRouter(prefix="/api/v1", tags=["engines"])


@router.get("/engines")
async def list_engines(reg: EngineRegistry = Depends(get_registry), _: Principal = Depends(require(Role.VIEWER))):
    async def _one(n: str):
        eng = reg.get(n)
        h = await eng.health_check()
        try:
            caps = [c.fault_type for c in await eng.capabilities()]
        except Exception:
            caps = []
        d = h.model_dump()
        d["configured"] = h.enabled
        d["reachable"] = h.available
        d["capabilities"] = caps
        return d

    return await asyncio.gather(*(_one(n) for n in reg.names()))


@router.get("/engines/{engine_name}/capabilities")
async def capabilities(engine_name: str, reg: EngineRegistry = Depends(get_registry),
                       _: Principal = Depends(require(Role.VIEWER))):
    eng = reg.get(engine_name)
    h = await eng.health_check()
    h_dict = h.model_dump()
    h_dict["configured"] = h.enabled
    h_dict["reachable"] = h.available
    caps = await eng.capabilities()
    h_dict["capabilities"] = [c.fault_type for c in caps]
    return {"engine": engine_name, "health": h_dict,
            "capabilities": [c.model_dump() for c in caps]}


@router.get("/environments")
def environments(s: Settings = Depends(get_settings), db: Session = Depends(get_db),
                 _: Principal = Depends(require(Role.VIEWER))):
    active_statuses = {"QUEUED", "VALIDATING", "RUNNING_BASELINE", "INJECTING", "OBSERVING", "ABORTING", "CLEANING_UP", "VERIFYING_RECOVERY"}
    active_by_env: dict[str, int] = {}
    try:
        runs = db.query(ExperimentRun).filter(ExperimentRun.status.in_(active_statuses)).all()
        for r in runs:
            env = getattr(r, "environment", None) or (r.definition_snapshot or {}).get("spec", {}).get("target", {}).get("environment")
            if env:
                active_by_env[env] = active_by_env.get(env, 0) + 1
    except Exception:
        active_by_env = {}

    return [
        {
            "id": name,
            "name": name,
            "runtime": e.description or "Docker Compose",
            "healthy": True,
            "engines": e.engines,
            "targets": sorted(e.services.keys()),
            "services": sorted(e.services.keys()),
            "active_runs": active_by_env.get(name, 0),
            "max_duration_seconds": e.max_duration_seconds,
            "max_targets": e.max_targets,
        }
        for name, e in s.environments.items()
    ]
