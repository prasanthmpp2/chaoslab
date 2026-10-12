from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.security import Principal, Role, require
from app.db.session import get_db
from app.services.report_service import build_recovery_scorecard_report, build_run_comparison

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])


@router.get("/recovery-scorecard")
def recovery_scorecard(
    experiment_id: str = Query(...),
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
    _: Principal = Depends(require(Role.VIEWER)),
):
    return build_recovery_scorecard_report(db, experiment_id, limit)


@router.get("/compare")
def compare_runs(
    baseline_run_id: str = Query(...),
    candidate_run_id: str = Query(...),
    db: Session = Depends(get_db),
    _: Principal = Depends(require(Role.VIEWER)),
):
    return build_run_comparison(db, baseline_run_id, candidate_run_id)
