from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.security import key_is_sensitive
from app.schemas.fault import validate_fault_parameters
from app.schemas.probe import ProbeSpec

NAME = r"^[a-z0-9][a-z0-9-]{1,62}$"


class _S(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Metadata(_S):
    name: Annotated[str, Field(pattern=NAME)]
    version: str = "1"
    description: str = Field(default="", max_length=2000)
    owner: str | None = None
    tags: list[Annotated[str, Field(max_length=64)]] = Field(default_factory=list, max_length=20)


class Target(_S):
    environment: str
    service: str


class Fault(_S):
    engine: str
    type: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    duration_seconds: Annotated[int, Field(ge=1, le=3600, alias="durationSeconds")]


class Hypothesis(_S):
    max_error_rate: Annotated[float, Field(ge=0, le=1, alias="maxErrorRate")] = 0.05
    max_p95_latency_ms: Annotated[float | None, Field(gt=0, alias="maxP95LatencyMs")] = None
    require_recovery: bool = Field(default=True, alias="requireRecovery")


class Safety(_S):
    environment_allowlist: list[str] = Field(min_length=1, alias="environmentAllowlist")
    max_duration_seconds: Annotated[int, Field(ge=1, le=3600, alias="maxDurationSeconds")] = 60
    abort_on_error_rate: Annotated[float | None, Field(gt=0, le=1, alias="abortOnErrorRate")] = None


class Cleanup(_S):
    remove_faults: bool = Field(default=True, alias="removeFaults")
    verify_recovery: bool = Field(default=True, alias="verifyRecovery")


class Limits(_S):
    run_timeout_seconds: Annotated[int, Field(ge=10, le=7200, alias="runTimeoutSeconds")] = 300


class Spec(_S):
    target: Target
    fault: Fault
    hypothesis: Hypothesis = Field(default_factory=Hypothesis)
    safety: Safety
    cleanup: Cleanup = Field(default_factory=Cleanup)
    probes: list[ProbeSpec] = Field(default_factory=list, max_length=10)
    limits: Limits = Field(default_factory=Limits)


class ExperimentDocument(_S):
    api_version: Literal["chaos.example.io/v1"] = Field(alias="apiVersion")
    kind: Literal["Experiment"]
    metadata: Metadata
    spec: Spec

    @field_validator("spec")
    @classmethod
    def _no_secrets(cls, spec: Spec) -> Spec:
        def walk(o: Any, path: str = "") -> None:
            if isinstance(o, dict):
                for k, v in o.items():
                    if key_is_sensitive(str(k)):
                        raise ValueError(f"credential-like key '{path}{k}' is not allowed in definitions")
                    walk(v, f"{path}{k}.")
            elif isinstance(o, list):
                for v in o:
                    walk(v, path)

        walk(spec.fault.parameters)
        return spec

    @model_validator(mode="after")
    def _semantic(self) -> ExperimentDocument:
        s = self.spec
        if s.target.environment not in s.safety.environment_allowlist:
            raise ValueError("target.environment must be listed in safety.environmentAllowlist")
        if s.fault.duration_seconds > s.safety.max_duration_seconds:
            raise ValueError("fault.durationSeconds exceeds safety.maxDurationSeconds")
        if not s.cleanup.remove_faults:
            raise ValueError("cleanup.removeFaults must be true: every fault must be removable")
        names = [p.name for p in s.probes]
        if len(names) != len(set(names)):
            raise ValueError("probe names must be unique")
        # Typed parameter validation; raises ValidationRejected (a PlatformError) on bad input.
        # Wrapped so pydantic reports it as a normal validation error.
        try:
            validate_fault_parameters(s.fault.engine, s.fault.type, s.fault.parameters)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"{exc} {getattr(exc, 'details', '')}") from exc
        return self

    @property
    def safety_environment_allowlist(self) -> list[str]:
        return self.spec.safety.environment_allowlist
