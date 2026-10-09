from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Phase = Literal["baseline", "during", "recovery"]


class ProbeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")]
    type: Literal["http", "container-state"] = "http"
    phases: list[Phase] = Field(default_factory=lambda: ["baseline", "during", "recovery"], min_length=1)
    url: str | None = None
    expect_status: Annotated[int, Field(ge=100, le=599, alias="expectStatus")] = 200
    timeout_seconds: Annotated[float, Field(gt=0, le=30, alias="timeoutSeconds")] = 5.0
    samples: Annotated[int, Field(ge=1, le=50)] = 5  # per baseline/recovery phase
    interval_seconds: Annotated[float, Field(ge=0.2, le=30, alias="intervalSeconds")] = 1.0
    container: str | None = None  # logical container name; must be in the target's allowlist

    @model_validator(mode="after")
    def _check(self) -> ProbeSpec:
        if self.type == "http" and not self.url:
            raise ValueError("http probes require url")
        if self.type == "container-state" and not self.container:
            raise ValueError("container-state probes require container")
        return self


class ProbeSample(BaseModel):
    ok: bool
    latency_ms: float
    detail: str = ""
    status_code: int | None = None


class ProbeSummary(BaseModel):
    name: str
    phase: Phase
    samples: int
    errors: int
    error_rate: float
    p95_latency_ms: float | None
