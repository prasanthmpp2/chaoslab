from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class RunStatus(StrEnum):
    VALIDATING = "VALIDATING"
    REJECTED = "REJECTED"
    QUEUED = "QUEUED"
    RUNNING_BASELINE = "RUNNING_BASELINE"
    INJECTING = "INJECTING"
    OBSERVING = "OBSERVING"
    ABORTING = "ABORTING"
    CLEANING_UP = "CLEANING_UP"
    VERIFYING_RECOVERY = "VERIFYING_RECOVERY"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"
    CLEANUP_FAILED = "CLEANUP_FAILED"


class Outcome(StrEnum):
    PENDING = "PENDING"
    PASSED = "PASSED"
    FAILED_HYPOTHESIS = "FAILED_HYPOTHESIS"
    INCONCLUSIVE = "INCONCLUSIVE"
    DRY_RUN = "DRY_RUN"
    ERROR = "ERROR"


class CleanupStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    NOT_REQUIRED = "NOT_REQUIRED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class FaultState(StrEnum):
    PENDING = "PENDING"  # registered (with expiry) before injection
    ACTIVE = "ACTIVE"
    REMOVED = "REMOVED"  # removed AND verified
    REMOVAL_FAILED = "REMOVAL_FAILED"
    UNVERIFIED = "UNVERIFIED"  # removal issued but recovery could not be confirmed


TERMINAL = {
    RunStatus.REJECTED, RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.ABORTED, RunStatus.CLEANUP_FAILED,
}
ACTIVE_STATES = {
    RunStatus.RUNNING_BASELINE, RunStatus.INJECTING, RunStatus.OBSERVING, RunStatus.ABORTING,
    RunStatus.CLEANING_UP, RunStatus.VERIFYING_RECOVERY,
}

ALLOWED: dict[RunStatus, set[RunStatus]] = {
    RunStatus.VALIDATING: {RunStatus.QUEUED, RunStatus.REJECTED},
    RunStatus.QUEUED: {RunStatus.RUNNING_BASELINE, RunStatus.ABORTED, RunStatus.FAILED},
    RunStatus.RUNNING_BASELINE: {
        RunStatus.INJECTING, RunStatus.ABORTING, RunStatus.FAILED, RunStatus.SUCCEEDED, RunStatus.CLEANING_UP,
    },
    RunStatus.INJECTING: {RunStatus.OBSERVING, RunStatus.ABORTING, RunStatus.CLEANING_UP},
    RunStatus.OBSERVING: {RunStatus.CLEANING_UP, RunStatus.ABORTING},
    RunStatus.ABORTING: {RunStatus.CLEANING_UP},
    RunStatus.CLEANING_UP: {RunStatus.VERIFYING_RECOVERY, RunStatus.CLEANUP_FAILED, RunStatus.FAILED, RunStatus.ABORTED},
    RunStatus.VERIFYING_RECOVERY: {
        RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.ABORTED, RunStatus.CLEANUP_FAILED,
    },
    # Terminal states; CLEANUP_FAILED may be retried back into cleanup by an operator.
    RunStatus.CLEANUP_FAILED: {RunStatus.CLEANING_UP},
}


def can_transition(src: RunStatus, dst: RunStatus) -> bool:
    return dst in ALLOWED.get(src, set())


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    experiment_id: str
    experiment_version: int
    correlation_id: str
    status: str
    outcome: str
    cleanup_status: str
    dry_run: bool
    requested_by: str
    cancel_requested: bool
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    worker_id: str | None
    error_summary: str | None
    requires_attention: bool
    definition_snapshot: dict[str, Any]


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    event_type: str
    message: str
    details: dict[str, Any]
    created_at: datetime


class ProbeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    probe_name: str
    phase: str
    measurement: float | None
    tolerance: float | None
    status: str
    detail: dict[str, Any]
    created_at: datetime
    evidence_ref: str | None


class FaultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    run_id: str
    engine: str
    native_id: str | None
    target_id: str
    fault_type: str
    parameters: dict[str, Any]
    state: str
    created_at: datetime
    expires_at: datetime
    last_cleanup_attempt: datetime | None
    cleanup_error: str | None
    requires_attention: bool


class ArtifactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    size_bytes: int
    sha256: str
    created_at: datetime


class ExperimentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    description: str
    current_version: int
    approved_version: int | None
    definition: dict[str, Any]
    owner: str
    created_at: datetime
    updated_at: datetime
    archived: bool
