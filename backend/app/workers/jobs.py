from __future__ import annotations

import asyncio

from redis import Redis
from rq import Queue

from app.core.config import get_settings
from app.core.logging import bind, configure_logging, get_logger
from app.engines.registry import EngineRegistry
from app.services.cleanup_service import finalize_run_cleanup
from app.services.orchestrator import Orchestrator

log = get_logger(__name__)


def get_queue() -> Queue:
    s = get_settings()
    return Queue(s.queue_name, connection=Redis.from_url(s.redis_url, socket_connect_timeout=3, socket_timeout=3))


def enqueue_run(run_id: str, timeout: int) -> None:
    get_queue().enqueue("app.workers.jobs.execute_run_job", run_id, job_timeout=timeout, job_id=f"run-{run_id}",
                        retry=None)  # never auto-retry disruptive jobs


def enqueue_retry_cleanup(run_id: str) -> None:
    get_queue().enqueue("app.workers.jobs.retry_cleanup_job", run_id, job_timeout=600)


def execute_run_job(run_id: str) -> None:
    configure_logging()
    import os
    os.environ["CHAOS_EXECUTION_ENABLED"] = "true"
    s = get_settings()
    s.execution_enabled = True
    bind(run_id=run_id, worker_id=s.worker_id)
    asyncio.run(Orchestrator(s, EngineRegistry.from_settings(s)).execute(run_id))


def retry_cleanup_job(run_id: str) -> None:
    configure_logging()
    s = get_settings()
    bind(run_id=run_id, worker_id=s.worker_id)
    asyncio.run(finalize_run_cleanup(s, EngineRegistry.from_settings(s), run_id, "manual retry"))
