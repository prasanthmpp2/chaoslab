from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class EngineHealth(BaseModel):
    name: str
    enabled: bool
    available: bool
    version: str | None = None
    detail: str = ""


class Capability(BaseModel):
    fault_type: str
    description: str
    parameters_schema: dict[str, Any] = Field(default_factory=dict)
    verified: bool = True  # False when the installed tool could not confirm the capability
    note: str = ""


class ResolvedTarget(BaseModel):
    environment: str
    service: str
    containers: list[str] = Field(default_factory=list)
    proxies: list[str] = Field(default_factory=list)
    k8s_namespace: str | None = None
    k8s_labels: dict[str, str] = Field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.environment}:{self.service}"


class FaultSpec(BaseModel):
    run_id: str
    fault_id: str
    fault_type: str
    parameters: dict[str, Any]
    duration_seconds: int
    target: ResolvedTarget

    @property
    def resource_name(self) -> str:
        """Unique, run-owned native resource name."""
        return f"chaos-{self.run_id[:8]}-{self.fault_id[:8]}"


class FaultHandle(BaseModel):
    native_id: str
    details: dict[str, Any] = Field(default_factory=dict)


class FaultStatus(BaseModel):
    present: bool
    active: bool = False
    finished: bool = False  # run-to-completion engines (Chaos Toolkit) report this
    detail: dict[str, Any] = Field(default_factory=dict)


class RecoveryResult(BaseModel):
    recovered: bool
    verifiable: bool = True  # False => cleanup could not be independently confirmed
    detail: str = ""


class NativeOutcome(BaseModel):
    """Engine-native verdict, for engines that evaluate their own steady state."""

    passed: bool | None  # None => inconclusive
    rollback_failed: bool = False
    detail: str = ""
    artifacts: dict[str, bytes] = Field(default_factory=dict)


class FaultEngine(ABC):
    name: str

    @abstractmethod
    async def health_check(self) -> EngineHealth: ...

    @abstractmethod
    async def capabilities(self) -> list[Capability]: ...

    @abstractmethod
    def validate_target(self, target: ResolvedTarget) -> None:
        """Raise SafetyViolation if the target is unusable for this engine."""

    @abstractmethod
    def validate_parameters(self, fault_type: str, parameters: dict[str, Any], target: ResolvedTarget) -> None: ...

    @abstractmethod
    async def inject_fault(self, spec: FaultSpec) -> FaultHandle: ...

    @abstractmethod
    async def inspect_fault(self, spec: FaultSpec, handle: FaultHandle) -> FaultStatus: ...

    @abstractmethod
    async def remove_fault(self, spec: FaultSpec, handle: FaultHandle | None) -> None:
        """Idempotent. Must only remove resources owned by spec.run_id."""

    @abstractmethod
    async def verify_recovery(self, spec: FaultSpec, handle: FaultHandle | None) -> RecoveryResult: ...

    async def native_outcome(self, spec: FaultSpec, handle: FaultHandle) -> NativeOutcome | None:
        return None
