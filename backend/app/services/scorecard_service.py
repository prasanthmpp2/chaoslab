"""Pure scorecard rules for persisted chaos-run evidence."""
from __future__ import annotations

from typing import Any

SCORECARD_VERSION = 1
WEIGHTS = {"fault_impact": 50.0, "recovery_speed": 30.0, "cleanup": 20.0}


def not_scored(reason: str) -> dict[str, Any]:
    return {
        "version": SCORECARD_VERSION,
        "status": "NOT_SCORED",
        "score": None,
        "weights": WEIGHTS.copy(),
        "components": {
            "fault_impact": {"weight": WEIGHTS["fault_impact"], "score": None},
            "recovery_speed": {"weight": WEIGHTS["recovery_speed"], "score": None},
            "cleanup": {"weight": WEIGHTS["cleanup"], "score": None},
        },
        "reasons": [reason],
    }


def calculate_scorecard(
    *,
    dry_run: bool,
    cancelled: bool = False,
    max_error_rate: float,
    baseline_error_rate: float | None,
    baseline_samples: int,
    during_error_rate: float | None,
    during_samples: int,
    recovery_error_rate: float | None,
    recovery_samples: int,
    recovery_duration_seconds: float | None,
    recovery_deadline_seconds: float,
    recovered: bool | None,
    cleanup_status: str,
) -> dict[str, Any]:
    """Score measured resilience independently from the configured run verdict.

    Fault impact rewards a lower observed error rate, recovery rewards returning
    sooner relative to the configured deadline, and cleanup receives points
    only when the backend confirmed it.
    """
    reasons: list[str] = []
    if dry_run:
        reasons.append("dry run: no fault or resilience measurements were collected")
    if cancelled:
        reasons.append("run was cancelled before completing the planned observation")
    if baseline_samples <= 0 or baseline_error_rate is None:
        reasons.append("baseline measurements are missing")
    elif baseline_error_rate > max_error_rate:
        reasons.append("baseline was already outside the configured error tolerance")
    if during_samples <= 0 or during_error_rate is None:
        reasons.append("during-fault measurements are missing")
    if recovery_samples <= 0 or recovery_error_rate is None or recovery_duration_seconds is None:
        reasons.append("recovery measurements are missing")
    if cleanup_status not in {"SUCCEEDED", "FAILED"}:
        reasons.append("cleanup result is not confirmed")

    impact_score = None if during_error_rate is None or during_samples <= 0 else round(
        WEIGHTS["fault_impact"] * max(0.0, min(1.0, 1.0 - during_error_rate)), 2
    )
    recovery_score = None
    if recovery_duration_seconds is not None and recovery_samples > 0 and recovery_deadline_seconds > 0:
        fraction = max(0.0, min(1.0, recovery_duration_seconds / recovery_deadline_seconds))
        recovery_score = round(WEIGHTS["recovery_speed"] * max(0.0, 1.0 - fraction)
                               if recovered is not False else 0.0, 2)
    cleanup_score = (WEIGHTS["cleanup"] if cleanup_status == "SUCCEEDED" else 0.0
                     if cleanup_status == "FAILED" else None)

    components = {
        "fault_impact": {
            "weight": WEIGHTS["fault_impact"], "score": impact_score,
            "measured_error_rate": during_error_rate, "samples": during_samples,
            "max_error_rate": max_error_rate,
        },
        "recovery_speed": {
            "weight": WEIGHTS["recovery_speed"], "score": recovery_score,
            "duration_seconds": recovery_duration_seconds,
            "deadline_seconds": recovery_deadline_seconds,
            "error_rate": recovery_error_rate, "samples": recovery_samples,
            "recovered": recovered,
        },
        "cleanup": {"weight": WEIGHTS["cleanup"], "score": cleanup_score, "status": cleanup_status},
    }
    if reasons:
        return {
            "version": SCORECARD_VERSION, "status": "NOT_SCORED", "score": None,
            "weights": WEIGHTS.copy(), "components": components, "reasons": reasons,
        }

    total = round((impact_score or 0.0) + (recovery_score or 0.0) + (cleanup_score or 0.0), 2)
    return {
        "version": SCORECARD_VERSION, "status": "SCORED", "score": total,
        "weights": WEIGHTS.copy(), "components": components, "reasons": [],
    }
