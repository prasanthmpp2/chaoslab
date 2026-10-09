"""Pumba adapter. Commands are argv arrays executed without a shell.

Command shapes follow the Pumba CLI docs (kill/stop/pause/netem/stress). Because flags differ between
releases, `capabilities()` probes `pumba <subcommand> --help` on the installed binary and reports a fault as
unverified when that probe fails. Container names are resolved/validated through the Docker SDK against the
configured allowlist; labels are never used as the authorization boundary.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from app.core.config import Settings
from app.core.exceptions import EngineError, SafetyViolation
from app.engines.base import (
    Capability,
    EngineHealth,
    FaultEngine,
    FaultHandle,
    FaultSpec,
    FaultStatus,
    RecoveryResult,
    ResolvedTarget,
)
from app.engines.proc import ProcResult, run_argv, terminate
from app.schemas.fault import FAULT_CATALOG, validate_fault_parameters

_SUBCOMMAND = {
    "container-kill": "kill", "container-stop": "stop", "container-pause": "pause",
    "network-delay": "netem", "network-loss": "netem", "cpu-stress": "stress",
}
_SINGLE_SHOT = {"container-kill"}  # complete immediately; recovery is by restarting the container
_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")


def build_argv(settings: Settings, spec: FaultSpec, containers: list[str]) -> list[str]:
    p = validate_fault_parameters("pumba", spec.fault_type, spec.parameters).model_dump()
    for c in containers:
        if not _NAME_RE.match(c):
            raise SafetyViolation(f"unsafe container name {c!r}")
    d = f"{spec.duration_seconds}s"
    base = [settings.pumba_path, "--log-level", "info"]
    match spec.fault_type:
        case "container-kill":
            return [*base, "kill", "--signal", p["signal"], *containers]
        case "container-stop":
            return [*base, "stop", "--duration", d, *containers]
        case "container-pause":
            return [*base, "pause", "--duration", d, *containers]
        case "network-delay":
            tc = ["--tc-image", settings.pumba_tc_image] if settings.pumba_tc_image else []
            return [*base, "netem", "--duration", d, *tc, "delay", "--time", str(p["time_ms"]),
                    "--jitter", str(p["jitter_ms"]), *containers]
        case "network-loss":
            tc = ["--tc-image", settings.pumba_tc_image] if settings.pumba_tc_image else []
            return [*base, "netem", "--duration", d, *tc, "loss", "--percent", str(p["percent"]), *containers]
        case "cpu-stress":
            stressors = f"--cpu {p['workers']} --timeout {spec.duration_seconds}s"
            return [*base, "stress", "--duration", d, "--stressors", stressors, *containers]
    raise EngineError(f"unsupported pumba fault {spec.fault_type}", "unsupported")


class PumbaEngine(FaultEngine):
    name = "pumba"

    def __init__(self, settings: Settings, docker_client: Any | None = None) -> None:
        self.s = settings
        self._docker = docker_client
        self._procs: dict[str, asyncio.subprocess.Process] = {}  # fault_id -> live pumba process
        self._captured: dict[str, tuple[bytes, bytes]] = {}

    def _client(self):
        if not self.s.docker_enabled:
            raise EngineError("CHAOS_DOCKER_ENABLED=false: Docker access is disabled for this process", "config")
        if self._docker is None:
            import docker  # local import: only the privileged worker needs the SDK/socket

            self._docker = docker.from_env(timeout=10)
        return self._docker

    async def _version(self) -> ProcResult:
        return await run_argv([self.s.pumba_path, "--version"], timeout=10)

    async def health_check(self) -> EngineHealth:
        if not self.s.docker_enabled:
            return EngineHealth(name=self.name, enabled=False, available=False, detail="CHAOS_DOCKER_ENABLED=false")
        try:
            r = await self._version()
        except FileNotFoundError:
            return EngineHealth(name=self.name, enabled=True, available=False, detail=f"{self.s.pumba_path} not found")
        if r.exit_code != 0:
            return EngineHealth(name=self.name, enabled=True, available=False, detail=r.stderr[:200])
        try:
            await asyncio.to_thread(self._client().ping)
        except Exception as exc:  # noqa: BLE001
            return EngineHealth(name=self.name, enabled=True, available=False, version=r.stdout.strip(),
                                detail=f"docker unreachable: {exc!r}")
        return EngineHealth(name=self.name, enabled=True, available=True, version=r.stdout.strip(),
                            detail="privileged prerequisites " + ("attested" if self.s.pumba_privileged_ok else "NOT attested"))

    async def capabilities(self) -> list[Capability]:
        h = await self.health_check()
        probed: dict[str, bool] = {}
        for sub in set(_SUBCOMMAND.values()):
            if not h.available:
                probed[sub] = False
                continue
            r = await run_argv([self.s.pumba_path, sub, "--help"], timeout=10)
            probed[sub] = r.exit_code == 0
        caps = []
        for (eng, ftype), model in FAULT_CATALOG.items():
            if eng != self.name:
                continue
            ok = probed.get(_SUBCOMMAND[ftype], False)
            needs_priv = ftype.startswith("network") or ftype == "cpu-stress"
            note = "" if ok else "installed pumba did not confirm this subcommand"
            if ok and needs_priv and not self.s.pumba_privileged_ok:
                ok, note = False, "requires verified NET_ADMIN/tc or stress-ng prerequisites (CHAOS_PUMBA_PRIVILEGED_OK)"
            caps.append(Capability(fault_type=ftype, description=f"pumba {_SUBCOMMAND[ftype]}",
                                   parameters_schema=model.model_json_schema(by_alias=True), verified=ok, note=note))
        return caps

    def validate_target(self, target: ResolvedTarget) -> None:
        if not target.containers:
            raise SafetyViolation(f"service '{target.service}' has no allowlisted containers")

    def validate_parameters(self, fault_type: str, parameters: dict[str, Any], target: ResolvedTarget) -> None:
        validate_fault_parameters(self.name, fault_type, parameters)
        if (fault_type.startswith("network") or fault_type == "cpu-stress") and not self.s.pumba_privileged_ok:
            raise SafetyViolation("privileged prerequisites for this Pumba fault have not been attested")

    async def _resolve(self, target: ResolvedTarget) -> dict[str, str]:
        """Map allowlisted container names to IDs; exactly one running-or-existing match each."""
        client = self._client()

        def work() -> dict[str, str]:
            out = {}
            for name in target.containers:
                found = client.containers.list(all=True, filters={"name": f"^/{name}$"})
                if len(found) != 1:
                    raise SafetyViolation(f"container '{name}' resolved to {len(found)} candidates; expected 1")
                out[name] = found[0].id
            return out

        return await asyncio.to_thread(work)

    async def inject_fault(self, spec: FaultSpec) -> FaultHandle:
        ids = await self._resolve(spec.target)
        argv = build_argv(self.s, spec, list(ids))
        prior = await asyncio.to_thread(self._states, list(ids.values()))
        if spec.fault_type in _SINGLE_SHOT:
            r = await run_argv(argv, timeout=30, max_bytes=self.s.max_output_bytes)
            if r.exit_code != 0 or r.timed_out:
                raise EngineError(f"pumba exited {r.exit_code} timed_out={r.timed_out}: {r.stderr[:300]}", "inject")
            return FaultHandle(native_id=f"single-shot:{spec.resource_name}",
                               details={"argv": argv, "containers": ids, "prior_state": prior})
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
        self._procs[spec.fault_id] = proc
        await asyncio.sleep(1.0)  # fail fast on immediate CLI errors
        if proc.returncode not in (None, 0):
            _, err = await proc.communicate()
            raise EngineError(f"pumba exited {proc.returncode}: {err[:300].decode(errors='replace')}", "inject")
        return FaultHandle(native_id=f"pid:{proc.pid}:{spec.resource_name}",
                           details={"argv": argv, "containers": ids, "prior_state": prior, "pid": proc.pid})

    def _states(self, ids: list[str]) -> dict[str, str]:
        client = self._client()
        return {i: client.containers.get(i).status for i in ids}

    async def inspect_fault(self, spec: FaultSpec, handle: FaultHandle) -> FaultStatus:
        proc = self._procs.get(spec.fault_id)
        states = await asyncio.to_thread(self._states, list(handle.details["containers"].values()))
        if proc is None:
            return FaultStatus(present=False, finished=True, detail={"container_states": states})
        running = proc.returncode is None
        return FaultStatus(present=running, active=running, finished=not running,
                           detail={"exit_code": proc.returncode, "container_states": states})

    async def remove_fault(self, spec: FaultSpec, handle: FaultHandle | None) -> None:
        proc = self._procs.pop(spec.fault_id, None)
        if proc is not None:
            await terminate(proc)  # Pumba restores netem/pause on termination signals (per its docs)
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=2)
                self._captured[spec.fault_id] = (out, err)
            except (TimeoutError, ValueError):
                pass
        if handle is None:
            return
        prior = handle.details.get("prior_state", {})

        def restore() -> None:
            client = self._client()
            for cid, was in prior.items():
                c = client.containers.get(cid)
                c.reload()
                if c.status == "paused" and was != "paused":
                    c.unpause()
                elif c.status in ("exited", "created") and was == "running":
                    c.start()

        await asyncio.to_thread(restore)

    async def verify_recovery(self, spec: FaultSpec, handle: FaultHandle | None) -> RecoveryResult:
        if handle is None:
            return RecoveryResult(recovered=False, verifiable=False, detail="no handle recorded")
        prior = handle.details.get("prior_state", {})
        states = await asyncio.to_thread(self._states, list(prior))
        bad = {c: s for c, s in states.items() if prior.get(c) == "running" and s != "running"}
        if bad:
            return RecoveryResult(recovered=False, detail=f"containers not running: {bad}")
        if spec.fault_type.startswith("network"):
            # Confirm netem is gone if `tc` exists in the container; otherwise we cannot verify independently.
            def check() -> bool | None:
                for cid in prior:
                    res = self._client().containers.get(cid).exec_run(["tc", "qdisc", "show"])
                    if res.exit_code != 0:
                        return None
                    if b"netem" in res.output:
                        return False
                return True

            tc = await asyncio.to_thread(check)
            if tc is None:
                return RecoveryResult(recovered=True, verifiable=False,
                                      detail="containers running; netem removal not independently verifiable (no tc)")
            if not tc:
                return RecoveryResult(recovered=False, detail="netem qdisc still present")
        return RecoveryResult(recovered=True, detail="containers in expected state")
