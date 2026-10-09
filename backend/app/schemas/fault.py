"""Fault catalog: which (engine, fault type) pairs exist and their typed parameters.

Not every engine supports every fault. This table is the single source of truth.
Toxiproxy has no packet-loss toxic, so none is offered for it.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.exceptions import ValidationRejected


class _P(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


Direction = Literal["upstream", "downstream"]


# ---- Toxiproxy -------------------------------------------------------------
class ToxiproxyBase(_P):
    proxy: str = Field(min_length=1, max_length=128)
    direction: Direction = "downstream"
    toxicity: Annotated[float, Field(ge=0.0, le=1.0)] = 1.0


class ToxiLatency(ToxiproxyBase):
    latency_ms: Annotated[int, Field(ge=0, le=30_000, alias="latencyMs")]
    jitter_ms: Annotated[int, Field(ge=0, le=10_000, alias="jitterMs")] = 0


class ToxiBandwidth(ToxiproxyBase):
    rate_kbps: Annotated[int, Field(ge=1, le=1_000_000, alias="rateKbps")]  # Toxiproxy rate is KB/s


class ToxiTimeout(ToxiproxyBase):
    timeout_ms: Annotated[int, Field(ge=0, le=600_000, alias="timeoutMs")]


class ToxiReset(ToxiproxyBase):
    timeout_ms: Annotated[int, Field(ge=0, le=600_000, alias="timeoutMs")] = 0


class ToxiDataLimit(ToxiproxyBase):
    bytes: Annotated[int, Field(ge=0, le=1_073_741_824)]


# ---- Pumba -----------------------------------------------------------------
class PumbaKill(_P):
    signal: Literal["SIGKILL", "SIGTERM", "SIGSTOP", "SIGHUP", "SIGINT"] = "SIGKILL"


class PumbaNoParams(_P):
    pass


class PumbaDelay(_P):
    time_ms: Annotated[int, Field(ge=1, le=30_000, alias="timeMs")]
    jitter_ms: Annotated[int, Field(ge=0, le=10_000, alias="jitterMs")] = 0


class PumbaLoss(_P):
    percent: Annotated[float, Field(gt=0, le=100)]


class PumbaCpuStress(_P):
    workers: Annotated[int, Field(ge=1, le=8)] = 1


# ---- Chaos Toolkit ---------------------------------------------------------
class ChaosToolkitParams(_P):
    native: dict = Field(description="Native Chaos Toolkit experiment document (JSON object)")


# ---- Chaos Mesh ------------------------------------------------------------
class MeshPodKill(_P):
    mode: Literal["one", "all", "fixed-percent"] = "one"
    value: str | None = None


class MeshNetworkDelay(_P):
    latency_ms: Annotated[int, Field(ge=1, le=30_000, alias="latencyMs")]
    jitter_ms: Annotated[int, Field(ge=0, le=10_000, alias="jitterMs")] = 0
    mode: Literal["one", "all", "fixed-percent"] = "one"
    value: str | None = None


FAULT_CATALOG: dict[tuple[str, str], type[BaseModel]] = {
    ("toxiproxy", "network-latency"): ToxiLatency,
    ("toxiproxy", "bandwidth-limit"): ToxiBandwidth,
    ("toxiproxy", "connection-timeout"): ToxiTimeout,
    ("toxiproxy", "connection-reset"): ToxiReset,
    ("toxiproxy", "data-limit"): ToxiDataLimit,
    ("pumba", "container-kill"): PumbaKill,
    ("pumba", "container-stop"): PumbaNoParams,
    ("pumba", "container-pause"): PumbaNoParams,
    ("pumba", "network-delay"): PumbaDelay,
    ("pumba", "network-loss"): PumbaLoss,
    ("pumba", "cpu-stress"): PumbaCpuStress,
    ("chaos_toolkit", "native-experiment"): ChaosToolkitParams,
    ("chaos_mesh", "pod-kill"): MeshPodKill,
    ("chaos_mesh", "network-delay"): MeshNetworkDelay,
}

ENGINES = sorted({e for e, _ in FAULT_CATALOG})


def fault_types_for(engine: str) -> list[str]:
    norm = engine.replace("-", "_")
    return sorted(t for e, t in FAULT_CATALOG if e in (engine, norm))


def validate_fault_parameters(engine: str, fault_type: str, params: dict) -> BaseModel:
    norm = engine.replace("-", "_")
    model = FAULT_CATALOG.get((engine, fault_type)) or FAULT_CATALOG.get((norm, fault_type))
    if model is None:
        raise ValidationRejected(
            f"Unsupported engine/fault combination: {engine}/{fault_type}",
            {"supported": {e: fault_types_for(e) for e in ENGINES}},
        )
    try:
        return model.model_validate(params)
    except ValidationError as exc:
        raise ValidationRejected(
            f"Invalid parameters for {engine}/{fault_type}",
            {"errors": [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]},
        ) from exc
