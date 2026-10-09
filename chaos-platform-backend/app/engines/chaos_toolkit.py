"""Chaos Toolkit adapter: `chaos validate` / `chaos run --journal-path` in an isolated child process.

The native journal (not the exit code) decides the outcome: status/deviated/steady_states/rollbacks.
Dangerous native constructs (process providers, unlisted python modules, secrets, controls) are rejected.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.core.exceptions import SafetyViolation, ValidationRejected
from app.engines.base import (
    Capability,
    EngineHealth,
    FaultEngine,
    FaultHandle,
    FaultSpec,
    FaultStatus,
    NativeOutcome,
    RecoveryResult,
    ResolvedTarget,
)
from app.engines.proc import run_argv, terminate
from app.schemas.fault import FAULT_CATALOG

_FORBIDDEN_TOP = ("secrets", "controls", "extensions")


def _activities(doc: dict) -> list[dict]:
    acts: list[dict] = []
    for key in ("method", "rollbacks"):
        acts += [a for a in doc.get(key, []) if isinstance(a, dict)]
    ssh = doc.get("steady-state-hypothesis") or {}
    acts += [a for a in ssh.get("probes", []) if isinstance(a, dict)]
    return acts


def check_native_safety(doc: dict, settings: Settings) -> None:
    for k in _FORBIDDEN_TOP:
        if doc.get(k):
            raise SafetyViolation(f"native experiment key '{k}' is not allowed")
    for act in _activities(doc):
        prov = act.get("provider") or {}
        ptype = prov.get("type")
        if ptype == "process":
            raise SafetyViolation("process providers (arbitrary commands) are not allowed")
        if ptype == "python":
            mod = str(prov.get("module", ""))
            if not any(mod == m or mod.startswith(m + ".") for m in settings.chaostoolkit_allowed_modules):
                raise SafetyViolation(f"python module '{mod}' is not in CHAOS_CHAOSTOOLKIT_ALLOWED_MODULES")
        if ptype not in ("python", "http"):
            raise ValidationRejected(f"unsupported provider type {ptype!r}")


def interpret_journal(journal: dict, exit_code: int | None) -> NativeOutcome:
    status = journal.get("status")
    deviated = bool(journal.get("deviated", False))
    rollbacks = journal.get("rollbacks") or []
    rb_failed = any(r.get("status") == "failed" for r in rollbacks if isinstance(r, dict))
    if status == "completed" and not deviated:
        return NativeOutcome(passed=True, rollback_failed=rb_failed, detail="completed; steady state held")
    if status == "completed" and deviated:
        return NativeOutcome(passed=False, rollback_failed=rb_failed, detail="completed; steady state deviated")
    return NativeOutcome(passed=None, rollback_failed=rb_failed,
                         detail=f"inconclusive: status={status!r} exit={exit_code} deviated={deviated}")


class ChaosToolkitEngine(FaultEngine):
    name = "chaos_toolkit"

    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self._runs: dict[str, dict[str, Any]] = {}

    async def health_check(self) -> EngineHealth:
        try:
            r = await run_argv([self.s.chaostoolkit_path, "--version"], timeout=15)
        except FileNotFoundError:
            return EngineHealth(name=self.name, enabled=True, available=False, detail=f"{self.s.chaostoolkit_path} not found")
        ok = r.exit_code == 0
        return EngineHealth(name=self.name, enabled=True, available=ok,
                            version=(r.stdout or r.stderr).strip()[:100] or None, detail="" if ok else r.stderr[:200])

    async def capabilities(self) -> list[Capability]:
        h = await self.health_check()
        return [Capability(fault_type=t, description="Run a native Chaos Toolkit experiment (python/http providers only)",
                           parameters_schema=m.model_json_schema(by_alias=True), verified=h.available,
                           note="Available actions/probes depend on installed chaostoolkit extensions")
                for (e, t), m in FAULT_CATALOG.items() if e == self.name]

    def validate_target(self, target: ResolvedTarget) -> None:
        pass  # targets are expressed inside the native experiment; safety is enforced on providers

    def validate_parameters(self, fault_type: str, parameters: dict[str, Any], target: ResolvedTarget) -> None:
        check_native_safety(parameters["native"], self.s)

    async def validate_with_cli(self, native: dict) -> tuple[bool, str]:
        with tempfile.TemporaryDirectory(prefix="ctk-") as d:
            path = Path(d) / "experiment.json"
            path.write_text(json.dumps(native))
            r = await run_argv([self.s.chaostoolkit_path, "validate", str(path)], timeout=60,
                               env=self._env(), max_bytes=self.s.max_output_bytes)
            return r.exit_code == 0 and not r.timed_out, (r.stdout + r.stderr)[-2000:]

    def _env(self) -> dict[str, str]:
        return {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "PYTHONPATH")}

    async def inject_fault(self, spec: FaultSpec) -> FaultHandle:
        native = spec.parameters["native"]
        check_native_safety(native, self.s)
        ok, out = await self.validate_with_cli(native)
        if not ok:
            raise ValidationRejected("chaos validate failed", {"output": out})
        workdir = tempfile.mkdtemp(prefix=f"ctk-{spec.run_id[:8]}-")
        exp, journal = Path(workdir) / "experiment.json", Path(workdir) / "journal.json"
        exp.write_text(json.dumps(native))
        proc = await asyncio.create_subprocess_exec(
            self.s.chaostoolkit_path, "run", "--journal-path", str(journal), str(exp),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=self._env(), cwd=workdir,
            start_new_session=True)
        self._runs[spec.fault_id] = {"proc": proc, "journal": journal, "workdir": workdir}
        return FaultHandle(native_id=f"ctk:{proc.pid}", details={"pid": proc.pid, "workdir": workdir})

    async def inspect_fault(self, spec: FaultSpec, handle: FaultHandle) -> FaultStatus:
        r = self._runs.get(spec.fault_id)
        if r is None:
            return FaultStatus(present=False, finished=True)
        done = r["proc"].returncode is not None
        return FaultStatus(present=not done, active=not done, finished=done, detail={"exit_code": r["proc"].returncode})

    async def remove_fault(self, spec: FaultSpec, handle: FaultHandle | None) -> None:
        r = self._runs.get(spec.fault_id)
        if r is not None and r["proc"].returncode is None:
            await terminate(r["proc"], grace=15)  # SIGTERM lets Chaos Toolkit run its rollbacks

    async def native_outcome(self, spec: FaultSpec, handle: FaultHandle) -> NativeOutcome | None:
        r = self._runs.get(spec.fault_id)
        if r is None:
            return None
        proc = r["proc"]
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=5)
        except TimeoutError:
            out, err = b"", b""
        arts = {"stdout.txt": out[: self.s.max_output_bytes], "stderr.txt": err[: self.s.max_output_bytes]}
        jpath: Path = r["journal"]
        if not jpath.exists():
            return NativeOutcome(passed=None, detail=f"no journal produced (exit {proc.returncode})", artifacts=arts)
        raw = jpath.read_bytes()
        arts["journal.json"] = raw
        res = interpret_journal(json.loads(raw), proc.returncode)
        res.artifacts = arts
        return res

    async def verify_recovery(self, spec: FaultSpec, handle: FaultHandle | None) -> RecoveryResult:
        r = self._runs.get(spec.fault_id)
        if r is None:
            return RecoveryResult(recovered=False, verifiable=False, detail="no run record")
        if r["proc"].returncode is None:
            return RecoveryResult(recovered=False, detail="chaos process still running")
        out = await self.native_outcome(spec, handle) if handle else None
        if out and out.rollback_failed:
            return RecoveryResult(recovered=False, detail="one or more native rollbacks failed")
        return RecoveryResult(recovered=True, verifiable=False,
                              detail="process exited, rollbacks reported OK; side effects are not independently verified")
