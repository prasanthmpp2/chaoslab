"""Standalone reconciliation loop: `python -m app.workers.reconciler`."""
from __future__ import annotations

import asyncio
import time
from datetime import timedelta

from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import session_scope
from app.engines.registry import EngineRegistry
from app.models import ExperimentRun
from app.models.base import utcnow
from app.schemas.run import RunStatus
from app.services import metrics
from app.services.cleanup_service import reconcile_once
from app.workers.jobs import enqueue_run, get_queue

log = get_logger("reconciler")


def requeue_abandoned_queued(settings) -> int:
    """QUEUED runs that no worker claimed (e.g. Redis lost the job) are re-enqueued; claim is atomic so it's safe."""
    n = 0
    cut = utcnow() - timedelta(seconds=settings.queued_requeue_seconds)
    with session_scope() as db:
        ids = [r.id for r in db.scalars(select(ExperimentRun).where(
            ExperimentRun.status == RunStatus.QUEUED.value, ExperimentRun.queued_at < cut))]
    for rid in ids:
        try:
            enqueue_run(rid, 600)
            n += 1
        except Exception:  # noqa: BLE001
            log.warning("redis unavailable while requeueing", extra={"error_category": "redis"})
            break
    return n


async def main() -> None:
    configure_logging()
    s = get_settings()
    registry = EngineRegistry.from_settings(s)
    log.info("reconciler started")
    while True:
        try:
            stats = await reconcile_once(s, registry)
            stats["requeued"] = requeue_abandoned_queued(s)
            try:
                metrics.QUEUE_DEPTH.set(len(get_queue()))
            except Exception:  # noqa: BLE001
                pass
            metrics.WORKER_LAST_RECONCILE.set(time.time())
            if any(stats.values()):
                log.info(f"reconcile pass: {stats}")
        except Exception:  # noqa: BLE001  (database outage etc.: keep looping)
            log.exception("reconcile pass failed", extra={"error_category": "reconcile"})
        await asyncio.sleep(s.reconcile_interval_seconds)


if __name__ == "__main__":
    asyncio.run(main())
