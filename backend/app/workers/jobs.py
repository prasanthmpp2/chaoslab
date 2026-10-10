from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timedelta, timezone
import uuid

from redis import Redis
from rq import Queue
from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import bind, configure_logging, get_logger
from app.db.session import session_scope
from app.engines.registry import EngineRegistry
from app.models import PipelineExecution
from app.services.cleanup_service import finalize_run_cleanup
from app.services.orchestrator import Orchestrator

log = get_logger(__name__)


def get_queue() -> Queue:
    s = get_settings()
    return Queue(s.queue_name, connection=Redis.from_url(s.redis_url, socket_connect_timeout=3, socket_timeout=3))


def get_pipeline_queue() -> Queue:
    s = get_settings()
    return Queue(s.pipeline_queue_name, connection=Redis.from_url(s.redis_url, socket_connect_timeout=3, socket_timeout=3))


def enqueue_run(run_id: str, timeout: int) -> None:
    get_queue().enqueue("app.workers.jobs.execute_run_job", run_id, job_timeout=timeout, job_id=f"run-{run_id}",
                        retry=None)  # never auto-retry disruptive jobs


def enqueue_retry_cleanup(run_id: str) -> None:
    get_queue().enqueue("app.workers.jobs.retry_cleanup_job", run_id, job_timeout=600)


def enqueue_pipeline(pipeline_id: str) -> None:
    get_pipeline_queue().enqueue(
        "app.workers.jobs.execute_pipeline_job", pipeline_id,
        job_timeout=3600, job_id=f"pipeline-{pipeline_id}", retry=None,
    )


def enqueue_pipeline_teardown(pipeline_id: str) -> None:
    from app.services.pipeline_service import _save_pipeline_state, get_pipeline

    settings = get_settings()
    state = get_pipeline(settings, pipeline_id)
    if state is None:
        raise ValueError(f"pipeline {pipeline_id} not found")
    job_id = f"pipeline-teardown-{pipeline_id}-{uuid.uuid4().hex}"
    state["teardown_job_id"] = job_id
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_pipeline_state(settings, state)
    get_pipeline_queue().enqueue(
        "app.workers.jobs.teardown_pipeline_job", pipeline_id,
        job_timeout=600, job_id=job_id, retry=None,
    )


def execute_run_job(run_id: str) -> None:
    configure_logging()
    s = get_settings()
    bind(run_id=run_id, worker_id=s.worker_id)
    asyncio.run(Orchestrator(s, EngineRegistry.from_settings(s)).execute(run_id))


def execute_pipeline_job(pipeline_id: str) -> None:
    configure_logging()
    from app.services.pipeline_service import PipelineService

    settings = get_settings()
    bind(pipeline_id=pipeline_id, worker_id=settings.worker_id)
    asyncio.run(PipelineService(settings).execute_pipeline(pipeline_id))


def teardown_pipeline_job(pipeline_id: str) -> None:
    configure_logging()
    from app.services.pipeline_service import PipelineService, _save_pipeline_state, get_pipeline

    settings = get_settings()
    state = get_pipeline(settings, pipeline_id)
    if state is None:
        return
    try:
        if not PipelineService(settings).teardown_pipeline(pipeline_id):
            raise RuntimeError("one or more pipeline resources could not be removed")
        state = get_pipeline(settings, pipeline_id) or state
        state["status"] = "FAILED" if state.get("pipeline_failure_reason") else "TORN_DOWN"
        state["current_stage"] = (
            "Pipeline failed; resources were cleaned up"
            if state.get("pipeline_failure_reason") else "Pipeline torn down"
        )
        if state.get("pipeline_failure_reason"):
            state["error"] = state["pipeline_failure_reason"]
    except Exception as exc:  # noqa: BLE001
        state = get_pipeline(settings, pipeline_id) or state
        state["status"] = "TEARDOWN_FAILED"
        state["error"] = f"{type(exc).__name__}: {exc}"[:500]
    _save_pipeline_state(settings, state)


def reconcile_pipeline_jobs(settings) -> int:
    """Repair pipeline state when an API or RQ worker stops between transitions."""
    active = {"QUEUED", "RUNNING", "TEARING_DOWN"}
    with session_scope() as db:
        rows = db.scalars(select(PipelineExecution).where(PipelineExecution.status.in_(active))).all()
        records = [(row.id, copy.deepcopy(row.state)) for row in rows]
    queue = get_pipeline_queue()
    now = datetime.now(timezone.utc)
    changed = 0

    for pipeline_id, state in records:
        phase = state.get("status")
        job_id = state.get("teardown_job_id") if phase == "TEARING_DOWN" else f"pipeline-{pipeline_id}"
        job = queue.fetch_job(job_id) if job_id else None
        job_status = job.get_status(refresh=True).value if job else None
        if job_status in {"queued", "started", "deferred", "scheduled"}:
            continue

        if phase == "QUEUED":
            if job is None:
                try:
                    updated = datetime.fromisoformat(state["updated_at"])
                    if updated.tzinfo is None:
                        updated = updated.replace(tzinfo=timezone.utc)
                except (KeyError, ValueError):
                    updated = now
                if now - updated >= timedelta(seconds=settings.queued_requeue_seconds):
                    enqueue_pipeline(pipeline_id)
            else:
                state["status"] = "FAILED"
                state["error"] = f"Pipeline job ended unexpectedly ({job_status})"
                _save_pipeline_state(settings, state)
                changed += 1
            continue

        if phase == "RUNNING":
            state["pipeline_failure_reason"] = f"Pipeline worker stopped before completion ({job_status or 'missing job'})"
            state["status"] = "TEARING_DOWN"
            state["current_stage"] = "Pipeline interrupted; cleanup queued"
            state["updated_at"] = now.isoformat()
            _save_pipeline_state(settings, state)
            enqueue_pipeline_teardown(pipeline_id)
            changed += 1
            continue

        # A lost teardown enqueue is retried after the normal queue grace period.
        if job is None:
            try:
                updated = datetime.fromisoformat(state["updated_at"])
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
            except (KeyError, ValueError):
                updated = now
            if now - updated >= timedelta(seconds=settings.queued_requeue_seconds):
                enqueue_pipeline_teardown(pipeline_id)
            continue

        state["status"] = "TEARDOWN_FAILED"
        state["error"] = f"Pipeline teardown job ended unexpectedly ({job_status})"
        _save_pipeline_state(settings, state)
        changed += 1
    return changed


def retry_cleanup_job(run_id: str) -> None:
    configure_logging()
    s = get_settings()
    bind(run_id=run_id, worker_id=s.worker_id)
    asyncio.run(finalize_run_cleanup(s, EngineRegistry.from_settings(s), run_id, "manual retry"))
