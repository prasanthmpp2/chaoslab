from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_registry
from app.api.v1.runs import RunSubmitResponse
from app.core.config import Settings, get_settings
from app.core.exceptions import NotFound
from app.core.security import Principal, Role, require
from app.db.session import get_db
from app.engines.registry import EngineRegistry
from app.models import Experiment
from app.schemas.run import ExperimentOut
from app.services.experiment_service import ExperimentService
from app.workers.jobs import enqueue_run

router = APIRouter(prefix="/api/v1/experiments", tags=["experiments"])


def svc(db: Session = Depends(get_db), s: Settings = Depends(get_settings),
        reg: EngineRegistry = Depends(get_registry)) -> ExperimentService:
    return ExperimentService(db, s, reg)


@router.post("", response_model=ExperimentOut, status_code=201)
def create(definition: dict[str, Any] = Body(...), p: Principal = Depends(require(Role.AUTHOR)),
           service: ExperimentService = Depends(svc)):
    return service.create(definition, p.user)


@router.get("", response_model=list[ExperimentOut])
def list_experiments(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                     db: Session = Depends(get_db), _: Principal = Depends(require(Role.VIEWER))):
    q = select(Experiment).where(Experiment.archived.is_(False)).order_by(Experiment.created_at.desc())
    return list(db.scalars(q.limit(limit).offset(offset)))


@router.get("/{experiment_id}", response_model=ExperimentOut)
def get_one(experiment_id: str, db: Session = Depends(get_db), _: Principal = Depends(require(Role.VIEWER))):
    e = db.get(Experiment, experiment_id)
    if e is None or e.archived:
        raise NotFound("experiment not found")
    return e


@router.put("/{experiment_id}", response_model=ExperimentOut)
def update(experiment_id: str, definition: dict[str, Any] = Body(...), p: Principal = Depends(require(Role.AUTHOR)),
           service: ExperimentService = Depends(svc)):
    return service.update(experiment_id, definition, p.user)


@router.delete("/{experiment_id}", status_code=204)
def delete(experiment_id: str, p: Principal = Depends(require(Role.AUTHOR)), service: ExperimentService = Depends(svc)):
    service.archive(experiment_id, p.user)
    return Response(status_code=204)


@router.post("/{experiment_id}/validate")
def validate(experiment_id: str, db: Session = Depends(get_db), service: ExperimentService = Depends(svc),
             _: Principal = Depends(require(Role.AUTHOR, Role.OPERATOR))):
    e = db.get(Experiment, experiment_id)
    if e is None or e.archived:
        raise NotFound("experiment not found")
    return service.validate(e.definition)


@router.post("/{experiment_id}/approve", response_model=ExperimentOut)
def approve(experiment_id: str, p: Principal = Depends(require(Role.APPROVER)), service: ExperimentService = Depends(svc)):
    return service.approve(experiment_id, p.user)


@router.post("/{experiment_id}/runs", response_model=RunSubmitResponse, status_code=202)
def submit_run(experiment_id: str, p: Principal = Depends(require(Role.OPERATOR)), service: ExperimentService = Depends(svc)):
    run = service.start_run(experiment_id, p.user, enqueue_run)
    return RunSubmitResponse(run_id=run.id, status=run.status, dry_run=run.dry_run)
