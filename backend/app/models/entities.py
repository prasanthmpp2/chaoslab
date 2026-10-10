from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, JSONType, utcnow


def new_id() -> str:
    return str(uuid.uuid4())


def _ts(**kw):
    return mapped_column(DateTime(timezone=True), default=utcnow, **kw)


class Experiment(Base):
    __tablename__ = "experiments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(63), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    approved_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    definition: Mapped[dict] = mapped_column(JSONType)
    owner: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    versions: Mapped[list[ExperimentVersion]] = relationship(back_populates="experiment")


class ExperimentVersion(Base):
    __tablename__ = "experiment_versions"
    __table_args__ = (UniqueConstraint("experiment_id", "version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    definition: Mapped[dict] = mapped_column(JSONType)
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = _ts()
    experiment: Mapped[Experiment] = relationship(back_populates="versions")


class ExperimentRun(Base):
    __tablename__ = "experiment_runs"
    __table_args__ = (Index("ix_runs_status_created", "status", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.id"), index=True)
    experiment_version: Mapped[int] = mapped_column(Integer)
    definition_snapshot: Mapped[dict] = mapped_column(JSONType)  # immutable once written
    correlation_id: Mapped[str] = mapped_column(String(36), default=new_id, unique=True)
    status: Mapped[str] = mapped_column(String(32), default="VALIDATING")
    outcome: Mapped[str] = mapped_column(String(32), default="PENDING")
    cleanup_status: Mapped[str] = mapped_column(String(32), default="NOT_STARTED")
    recovery_duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    scorecard: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    requested_by: Mapped[str] = mapped_column(String(128))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    requires_attention: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = _ts()
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    faults: Mapped[list[FaultInstance]] = relationship(back_populates="run")


class FaultInstance(Base):
    __tablename__ = "fault_instances"
    __table_args__ = (Index("ix_faults_state_expiry", "state", "expires_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("experiment_runs.id"), index=True)
    engine: Mapped[str] = mapped_column(String(32))
    native_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    target_id: Mapped[str] = mapped_column(String(256))
    fault_type: Mapped[str] = mapped_column(String(64))
    parameters: Mapped[dict] = mapped_column(JSONType)
    native_details: Mapped[dict] = mapped_column(JSONType, default=dict)
    state: Mapped[str] = mapped_column(String(32), default="PENDING")
    created_at: Mapped[datetime] = _ts()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_cleanup_attempt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cleanup_attempts: Mapped[int] = mapped_column(Integer, default=0)
    cleanup_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    requires_attention: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    run: Mapped[ExperimentRun] = relationship(back_populates="faults")


class ProbeResult(Base):
    __tablename__ = "probe_results"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("experiment_runs.id"), index=True)
    probe_name: Mapped[str] = mapped_column(String(64))
    phase: Mapped[str] = mapped_column(String(16))
    measurement: Mapped[float | None] = mapped_column(Float, nullable=True)
    tolerance: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16))  # PASS | FAIL | NO_DATA
    detail: Mapped[dict] = mapped_column(JSONType, default=dict)
    evidence_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = _ts()


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (Index("ix_events_run_time", "run_id", "created_at"),)
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("experiment_runs.id"))
    event_type: Mapped[str] = mapped_column(String(48))
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = _ts()


class Artifact(Base):
    """Large evidence (stdout/stderr, native reports) lives on disk; only metadata is stored here."""

    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("experiment_runs.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    path: Mapped[str] = mapped_column(String(1024))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _ts()


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(128), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str] = mapped_column(String(256))
    outcome: Mapped[str] = mapped_column(String(16))
    meta: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = _ts(index=True)


class TargetLease(Base):
    """One row per leased target; the primary key makes concurrent acquisition atomic."""

    __tablename__ = "target_leases"
    target_key: Mapped[str] = mapped_column(String(256), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("experiment_runs.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = _ts()


class PipelineExecution(Base):
    """Pipeline state is durable in PostgreSQL; source and build artifacts stay on disk/object storage."""

    __tablename__ = "pipeline_executions"
    __table_args__ = (Index("ix_pipeline_status_created", "status", "created_at"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    state: Mapped[dict] = mapped_column(JSONType)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
