"""Persisted run state machine executed by the RQ worker."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import update

from app.core.config import Settings
from app.core.logging import bind, get_logger
from app.db.session import session_scope
from app.engines.base import FaultHandle, FaultSpec, NativeOutcome, ResolvedTarget
from app.engines.registry import EngineRegistry
from app.models import ExperimentRun, FaultInstance
from app.models.base import utcnow
from app.repositories import runs as repo
from app.schemas.experiment import ExperimentDocument
from app.schemas.probe import ProbeSample
from app.schemas.run import CleanupStatus, FaultState, Outcome, RunStatus
from app.services import metrics
from app.services.cleanup_service import cleanup_fault, final_status
from app.services.probe_service import ProbeService, p95, summarize
from app.services.safety_service import check_definition
from app.services.scorecard_service import calculate_scorecard

log = get_logger(__name__)


@dataclass
class Ctx:
    run_id: str
    doc: ExperimentDocument
    target: ResolvedTarget
    spec: FaultSpec | None = None
    fault_id: str | None = None
    handle: FaultHandle | None = None
    baseline: dict[str, list[ProbeSample]] = field(default_factory=dict)
    during: dict[str, list[ProbeSample]] = field(default_factory=dict)
    recovery: dict[str, list[ProbeSample]] = field(default_factory=dict)
    native: NativeOutcome | None = None
    cancelled: bool = False
    abort_reason: str | None = None
    error: str | None = None
    outcome: Outcome = Outcome.PENDING
    recovery_duration_seconds: float | None = None
    recovered: bool | None = None
    notes: list[str] = field(default_factory=list)


def rates(samples: dict[str, list[ProbeSample]]) -> tuple[float, float | None]:
    allv = [s for v in samples.values() for s in v]
    if not allv:
        return 0.0, None
    return sum(1 for s in allv if not s.ok) / len(allv), p95([s.latency_ms for s in allv if s.ok])


class Orchestrator:
    def __init__(self, settings: Settings, registry: EngineRegistry, probes: ProbeService | None = None) -> None:
        self.s, self.registry = settings, registry
        self.probes = probes or ProbeService(settings)

    # ---- helpers -------------------------------------------------------
    def _claim(self, run_id: str) -> bool:
        """Atomic QUEUED -> RUNNING_BASELINE claim; only one worker can win."""
        with session_scope() as db:
            res = db.execute(update(ExperimentRun).where(
                ExperimentRun.id == run_id, ExperimentRun.status == RunStatus.QUEUED.value).values(
                status=RunStatus.RUNNING_BASELINE.value, worker_id=self.s.worker_id, started_at=utcnow(),
                heartbeat_at=utcnow()))
            if res.rowcount != 1:
                return False
            repo.add_event(db, run_id, "claimed", f"claimed by {self.s.worker_id}")
            return True

    def _set_status(self, run_id: str, dst: RunStatus, msg: str = "") -> None:
        with session_scope() as db:
            run = db.get(ExperimentRun, run_id)
            repo.transition(db, run, dst, msg)
            run.heartbeat_at = utcnow()

    def _event(self, run_id: str, kind: str, msg: str, details: dict | None = None) -> None:
        with session_scope() as db:
            repo.add_event(db, run_id, kind, msg, details)

    def _heartbeat_and_cancel(self, run_id: str) -> bool:
        with session_scope() as db:
            run = db.get(ExperimentRun, run_id)
            run.heartbeat_at = utcnow()
            return run.cancel_requested

    # ---- main entry ----------------------------------------------------
    async def execute(self, run_id: str) -> None:
        if not self._claim(run_id):
            log.info("run not claimable (already claimed/cancelled)")
            return
        with session_scope() as db:
            run = db.get(ExperimentRun, run_id)
            doc = ExperimentDocument.model_validate(run.definition_snapshot)
            experiment_id, dry_run = run.experiment_id, run.dry_run
        bind(run_id=run_id, experiment_id=experiment_id, engine=doc.spec.fault.engine, worker_id=self.s.worker_id)
        started = time.monotonic()
        ctx = Ctx(run_id=run_id, doc=doc, target=None)  # type: ignore[arg-type]
        hb = asyncio.create_task(self._heartbeat_loop(run_id))  # keeps the reconciler from adopting a live run
        try:
            await self._execute(ctx, dry_run, started)
        finally:
            hb.cancel()

    async def _heartbeat_loop(self, run_id: str) -> None:
        while True:
            await asyncio.sleep(10)
            try:
                await asyncio.to_thread(self._heartbeat_and_cancel, run_id)
            except Exception:  # noqa: BLE001
                log.warning("heartbeat write failed", extra={"error_category": "database"})

    async def _execute(self, ctx: Ctx, dry_run: bool, started: float) -> None:
        doc = ctx.doc
        try:
            ctx.target = check_definition(self.s, self.registry, doc, verify_pipeline_container=True)  # re-check at execution time
            if dry_run or not self.s.execution_enabled:
                self._dry_run(ctx)
                return
            await asyncio.wait_for(self._inject_and_observe(ctx), doc.spec.limits.run_timeout_seconds)
        except TimeoutError:
            ctx.error, ctx.abort_reason = "run deadline exceeded", "deadline"
            ctx.outcome = Outcome.ERROR
        except Exception as exc:  # noqa: BLE001
            log.exception("run failed", extra={"event": "run_error", "error_category": getattr(exc, "category", "internal")})
            ctx.error = f"{type(exc).__name__}: {exc}"[:1000]
            ctx.outcome = Outcome.ERROR
        await self._finish(ctx, started)

    def _dry_run(self, ctx: Ctx) -> None:
        f = ctx.doc.spec.fault
        with session_scope() as db:
            run = db.get(ExperimentRun, ctx.run_id)
            repo.add_event(db, ctx.run_id, "dry_run",
                           f"DRY RUN: would inject {f.engine}/{f.type} on {ctx.target.key} for {f.duration_seconds}s; "
                           "nothing was injected and no measurements were taken",
                           {"parameters": f.parameters})
            run.outcome = Outcome.DRY_RUN.value
            run.cleanup_status = CleanupStatus.NOT_REQUIRED.value
            run.scorecard = calculate_scorecard(
                dry_run=True, max_error_rate=ctx.doc.spec.hypothesis.max_error_rate,
                cancelled=False,
                baseline_error_rate=None, baseline_samples=0, during_error_rate=None, during_samples=0,
                recovery_error_rate=None, recovery_samples=0, recovery_duration_seconds=None,
                recovery_deadline_seconds=self.s.recovery_timeout_seconds, recovered=None,
                cleanup_status=run.cleanup_status,
            )
            repo.transition(db, run, RunStatus.SUCCEEDED, "dry run complete")
        metrics.RUNS_COMPLETED.labels(status="SUCCEEDED", outcome="DRY_RUN").inc()

    # ---- phases --------------------------------------------------------
    async def _inject_and_observe(self, ctx: Ctx) -> None:
        d, run_id = ctx.doc, ctx.run_id
        s = d.spec
        adapter = self.registry.get(s.fault.engine)
        health = await adapter.health_check()
        if not health.available:
            raise RuntimeError(f"engine {adapter.name} unavailable: {health.detail}")

        ctx.baseline = await self.probes.collect(s.probes, "baseline", ctx.target)
        base_rate, base_p95 = rates(ctx.baseline)
        self._event(run_id, "baseline", f"baseline error_rate={base_rate:.3f} p95={base_p95}",
                    {"error_rate": base_rate, "p95_ms": base_p95})
        if ctx.baseline and base_rate > s.hypothesis.max_error_rate:
            ctx.outcome = Outcome.INCONCLUSIVE
            ctx.notes.append("baseline already outside tolerance; fault not injected")
            return
        if self._heartbeat_and_cancel(run_id):
            ctx.cancelled = True
            return

        self._set_status(run_id, RunStatus.INJECTING, "injecting fault")
        fault_id = _new_uuid()
        spec = FaultSpec(run_id=run_id, fault_id=fault_id, fault_type=s.fault.type, parameters=s.fault.parameters,
                         duration_seconds=s.fault.duration_seconds, target=ctx.target)
        expires = utcnow() + timedelta(seconds=s.fault.duration_seconds + self.s.fault_expiry_grace_seconds
                                       + self.s.run_grace_seconds)
        with session_scope() as db:  # registered with its expiry BEFORE injection
            db.add(FaultInstance(id=fault_id, run_id=run_id, engine=s.fault.engine, target_id=ctx.target.key,
                                 fault_type=s.fault.type, parameters=s.fault.parameters, state=FaultState.PENDING.value,
                                 expires_at=expires, native_details={"duration_seconds": s.fault.duration_seconds}))
        ctx.spec, ctx.fault_id = spec, fault_id
        try:
            ctx.handle = await adapter.inject_fault(spec)
        except Exception:
            metrics.INJECTION_FAILURES.labels(engine=s.fault.engine).inc()
            raise
        with session_scope() as db:
            f = db.get(FaultInstance, fault_id)
            f.native_id, f.state = ctx.handle.native_id, FaultState.ACTIVE.value
            f.native_details = {**f.native_details, "handle": ctx.handle.details}
            repo.add_event(db, run_id, "fault_injected", f"{s.fault.engine}/{s.fault.type} injected",
                           {"native_id": ctx.handle.native_id, "parameters": s.fault.parameters})

        self._set_status(run_id, RunStatus.OBSERVING, "observing")
        await self._observe(ctx, adapter)

    async def _observe(self, ctx: Ctx, adapter) -> None:
        s = ctx.doc.spec
        during = [p for p in s.probes if "during" in p.phases]
        end = time.monotonic() + s.fault.duration_seconds
        tick = min([p.interval_seconds for p in during] + [1.0])
        while time.monotonic() < end:
            if self._heartbeat_and_cancel(ctx.run_id):
                ctx.cancelled = True
                self._event(ctx.run_id, "cancel", "cancellation requested; stopping observation")
                return
            for p in during:
                ctx.during.setdefault(p.name, []).append(await self.probes.sample(p, ctx.target))
            if s.safety.abort_on_error_rate is not None:
                total = sum(len(v) for v in ctx.during.values())
                rate, _ = rates(ctx.during)
                if total >= 3 and rate >= s.safety.abort_on_error_rate:
                    ctx.abort_reason = f"abort condition: error rate {rate:.3f} >= {s.safety.abort_on_error_rate}"
                    self._event(ctx.run_id, "abort_condition", ctx.abort_reason)
                    return
            try:
                st = await adapter.inspect_fault(ctx.spec, ctx.handle)
                if st.finished and adapter.name == "chaos_toolkit":
                    return
            except Exception as exc:  # noqa: BLE001
                self._event(ctx.run_id, "inspect_error", f"inspect failed: {exc!r}"[:300])
            await asyncio.sleep(max(0.0, min(tick, end - time.monotonic())))

    async def _finish(self, ctx: Ctx, started: float) -> None:
        run_id, s = ctx.run_id, ctx.doc.spec
        adapter = self.registry.get(s.fault.engine)
        aborting = ctx.cancelled or ctx.abort_reason is not None
        with session_scope() as db:
            run = db.get(ExperimentRun, run_id)
            cur = RunStatus(run.status)
            if aborting and cur in (RunStatus.RUNNING_BASELINE, RunStatus.INJECTING, RunStatus.OBSERVING):
                repo.transition(db, run, RunStatus.ABORTING, ctx.abort_reason or "cancelled")
            repo.transition(db, run, RunStatus.CLEANING_UP, "cleanup (always runs)")

        # Chaos Toolkit's own verdict must be read before its process is terminated/cleaned.
        if ctx.handle and ctx.spec:
            try:
                ctx.native = await adapter.native_outcome(ctx.spec, ctx.handle)
            except Exception as exc:  # noqa: BLE001
                ctx.notes.append(f"native outcome unavailable: {exc!r}"[:200])

        cleanup_ok = True
        if ctx.fault_id:
            state = await cleanup_fault(self.s, self.registry, ctx.fault_id)
            cleanup_ok = state != FaultState.REMOVAL_FAILED
        with session_scope() as db:
            run = db.get(ExperimentRun, run_id)
            run.cleanup_status = (CleanupStatus.SUCCEEDED if cleanup_ok else CleanupStatus.FAILED).value if ctx.fault_id \
                else CleanupStatus.NOT_REQUIRED.value
            if not cleanup_ok:
                run.requires_attention = True
                run.outcome = ctx.outcome.value if ctx.outcome != Outcome.PENDING else Outcome.ERROR.value
                run.recovery_duration_seconds = ctx.recovery_duration_seconds
                self._persist(db, ctx)
                run.scorecard = self._scorecard(ctx, run.cleanup_status)
                repo.add_event(db, run_id, "alert", "CLEANUP FAILED: manual remediation required; see /faults/active")
                repo.transition(db, run, RunStatus.CLEANUP_FAILED, "cleanup failed")
                run.error_summary = (ctx.error or "") + " Cleanup failed; faults may still be active."
                metrics.RUNS_COMPLETED.labels(status="CLEANUP_FAILED", outcome=run.outcome).inc()
                metrics.RUN_DURATION.observe(time.monotonic() - started)
                return
            repo.transition(db, run, RunStatus.VERIFYING_RECOVERY, "verifying recovery")

        recovered = True
        if ctx.fault_id and s.cleanup.verify_recovery and s.probes:
            recovered = await self._recovery_probes(ctx)
        elif ctx.fault_id:
            ctx.recovered = None
        with session_scope() as db:
            run = db.get(ExperimentRun, run_id)
            self._decide(ctx, recovered)
            run.outcome = ctx.outcome.value
            run.recovery_duration_seconds = ctx.recovery_duration_seconds
            run.error_summary = ctx.error or ctx.abort_reason or ("; ".join(ctx.notes) or None)
            run.cancel_requested = run.cancel_requested or ctx.cancelled
            self._persist(db, ctx)
            run.scorecard = self._scorecard(ctx, run.cleanup_status)
            dst = final_status(run, True)
            if ctx.abort_reason and dst == RunStatus.SUCCEEDED:
                dst = RunStatus.ABORTED
            repo.transition(db, run, dst, f"finished: outcome={ctx.outcome.value}")
            metrics.RUNS_COMPLETED.labels(status=dst.value, outcome=run.outcome).inc()
            metrics.RUN_DURATION.observe(time.monotonic() - started)

    async def _recovery_probes(self, ctx: Ctx) -> bool:
        s = ctx.doc.spec
        started = time.monotonic()
        deadline = started + self.s.recovery_timeout_seconds
        while True:
            ctx.recovery = await self.probes.collect(s.probes, "recovery", ctx.target)
            rate, _ = rates(ctx.recovery)
            elapsed = time.monotonic() - started
            if not ctx.recovery:
                ctx.recovery_duration_seconds = elapsed
                ctx.recovered = True
                return True
            if rate <= s.hypothesis.max_error_rate:
                ctx.recovery_duration_seconds = elapsed
                ctx.recovered = elapsed <= self.s.recovery_timeout_seconds
                return ctx.recovered
            if time.monotonic() >= deadline:
                ctx.recovery_duration_seconds = elapsed
                ctx.recovered = False
                return False
            await asyncio.sleep(2)

    def _scorecard(self, ctx: Ctx, cleanup_status: str) -> dict:
        baseline_rate, _ = rates(ctx.baseline)
        during_rate, _ = rates(ctx.during)
        recovery_rate, _ = rates(ctx.recovery)
        return calculate_scorecard(
            dry_run=False,
            cancelled=ctx.cancelled,
            max_error_rate=ctx.doc.spec.hypothesis.max_error_rate,
            baseline_error_rate=baseline_rate if ctx.baseline else None,
            baseline_samples=sum(len(samples) for samples in ctx.baseline.values()),
            during_error_rate=during_rate if ctx.during else None,
            during_samples=sum(len(samples) for samples in ctx.during.values()),
            recovery_error_rate=recovery_rate if ctx.recovery else None,
            recovery_samples=sum(len(samples) for samples in ctx.recovery.values()),
            recovery_duration_seconds=ctx.recovery_duration_seconds,
            recovery_deadline_seconds=float(self.s.recovery_timeout_seconds),
            recovered=ctx.recovered,
            cleanup_status=cleanup_status,
        )

    def _decide(self, ctx: Ctx, recovered: bool) -> None:
        h = ctx.doc.spec.hypothesis
        if ctx.outcome in (Outcome.ERROR, Outcome.INCONCLUSIVE):
            return
        if ctx.cancelled:
            ctx.outcome = Outcome.INCONCLUSIVE
            return
        if ctx.abort_reason:
            ctx.outcome = Outcome.FAILED_HYPOTHESIS
            return
        if h.require_recovery and not ctx.recovery:
            ctx.outcome = Outcome.INCONCLUSIVE
            ctx.notes.append("recovery measurements were not collected; resilience could not be verified")
            return
        failures: list[str] = []
        rate, p = rates(ctx.during)
        if ctx.during and rate > h.max_error_rate:
            failures.append(f"during-fault error rate {rate:.3f} > {h.max_error_rate}")
        if ctx.during and h.max_p95_latency_ms is not None and p is not None and p > h.max_p95_latency_ms:
            failures.append(f"during-fault p95 {p:.0f}ms > {h.max_p95_latency_ms}ms")
        if h.require_recovery and ctx.recovery and not recovered:
            failures.append("service did not recover within the recovery window")
        if ctx.native is not None:
            if ctx.native.passed is False:
                failures.append(f"chaos toolkit: {ctx.native.detail}")
            if ctx.native.passed is None and not ctx.during:
                ctx.outcome = Outcome.INCONCLUSIVE
                ctx.notes.append(ctx.native.detail)
                return
        if failures:
            ctx.outcome = Outcome.FAILED_HYPOTHESIS
            ctx.notes.extend(failures)
        elif not ctx.during and not (ctx.native and ctx.native.passed):
            ctx.outcome = Outcome.INCONCLUSIVE
            ctx.notes.append("no during-fault measurements were collected; resilience was not measured")
        else:
            ctx.outcome = Outcome.PASSED

    def _persist(self, db, ctx: Ctx) -> None:
        h = ctx.doc.spec.hypothesis
        for phase, data in (("baseline", ctx.baseline), ("during", ctx.during), ("recovery", ctx.recovery)):
            for name, samples in data.items():
                summ = summarize(name, phase, samples)  # type: ignore[arg-type]
                art = repo.store_artifact(db, self.s, ctx.run_id, f"samples-{phase}-{name}.json",
                                          json.dumps([x.model_dump() for x in samples]).encode())
                ok = summ.error_rate <= h.max_error_rate
                repo.add_probe_result(db, ctx.run_id, name, phase, summ.error_rate, h.max_error_rate,
                                      "PASS" if ok else "FAIL", {"samples": summ.samples, "errors": summ.errors},
                                      evidence_ref=art.name)
                if summ.p95_latency_ms is not None:
                    tol = h.max_p95_latency_ms
                    repo.add_probe_result(db, ctx.run_id, f"{name}:p95_ms", phase, summ.p95_latency_ms, tol,
                                          "PASS" if tol is None or summ.p95_latency_ms <= tol else "FAIL",
                                          evidence_ref=art.name)
        if ctx.native:
            for n, data in ctx.native.artifacts.items():
                repo.store_artifact(db, self.s, ctx.run_id, f"native-{n}", data)


def _new_uuid() -> str:
    import uuid

    return str(uuid.uuid4())
