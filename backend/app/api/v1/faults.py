from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import NotFound
from app.core.security import Principal, Role, require
from app.db.session import get_db
from app.models import FaultInstance
from app.schemas.run import FaultOut, FaultState

router = APIRouter(prefix="/api/v1", tags=["faults"])
_OP = Depends(require(Role.OPERATOR))


@router.get("/runs/{run_id}/faults", response_model=list[FaultOut])
def run_faults(run_id: str, db: Session = Depends(get_db), _: Principal = _OP):
    return list(db.scalars(select(FaultInstance).where(FaultInstance.run_id == run_id)))


@router.get("/faults/active", response_model=list[FaultOut])
def active(db: Session = Depends(get_db), _: Principal = _OP):
    """Faults that are live or need attention (ACTIVE, PENDING, REMOVAL_FAILED)."""
    states = [FaultState.ACTIVE.value, FaultState.PENDING.value, FaultState.REMOVAL_FAILED.value]
    return list(db.scalars(select(FaultInstance).where(FaultInstance.state.in_(states))
                           .order_by(FaultInstance.created_at)))


@router.get("/faults/{fault_id}", response_model=FaultOut)
def fault(fault_id: str, db: Session = Depends(get_db), _: Principal = _OP):
    f = db.get(FaultInstance, fault_id)
    if f is None:
        raise NotFound("fault not found")
    return f
