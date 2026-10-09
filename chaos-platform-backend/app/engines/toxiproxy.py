"""Toxiproxy adapter using the management HTTP API (GET/POST/DELETE /proxies/{proxy}/toxics).

Toxic types and attributes per the Toxiproxy README: latency{latency,jitter}, bandwidth{rate KB/s},
timeout{timeout}, reset_peer{timeout}, limit_data{bytes}, slow_close{delay}, slicer, ...
Toxiproxy has NO packet-loss toxic; this adapter does not pretend otherwise.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

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
from app.schemas.fault import FAULT_CATALOG, validate_fault_parameters


def build_toxic(spec: FaultSpec) -> dict[str, Any]:
    p = validate_fault_parameters("toxiproxy", spec.fault_type, spec.parameters)
    d = p.model_dump()
    attrs: dict[str, Any]
    match spec.fault_type:
        case "network-latency":
            ttype, attrs = "latency", {"latency": d["latency_ms"], "jitter": d["jitter_ms"]}
        case "bandwidth-limit":
            ttype, attrs = "bandwidth", {"rate": d["rate_kbps"]}
        case "connection-timeout":
            ttype, attrs = "timeout", {"timeout": d["timeout_ms"]}
        case "connection-reset":
            ttype, attrs = "reset_peer", {"timeout": d["timeout_ms"]}
        case "data-limit":
            ttype, attrs = "limit_data", {"bytes": d["bytes"]}
        case _:
            raise EngineError(f"unsupported toxiproxy fault {spec.fault_type}", "unsupported")
    return {
        "name": spec.resource_name, "type": ttype, "stream": d["direction"],
        "toxicity": d["toxicity"], "attributes": attrs,
    }


class ToxiproxyEngine(FaultEngine):
    name = "toxiproxy"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.s = settings
        self._client = client

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.s.toxiproxy_url or "",
                timeout=httpx.Timeout(self.s.toxiproxy_request_timeout, connect=self.s.toxiproxy_connect_timeout),
            )
        return self._client

    async def _req(self, method: str, path: str, *, retries: int = 0, **kw) -> httpx.Response:
        """Retries only for GET/DELETE (idempotent). POST is never blindly retried."""
        if not self.s.toxiproxy_url:
            raise EngineError("CHAOS_TOXIPROXY_URL is not configured", "config")
        if retries and method not in ("GET", "DELETE"):
            raise ValueError("refusing to retry a non-idempotent request")
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                return await self._http().request(method, path, **kw)
            except httpx.HTTPError as exc:
                last = exc
                await asyncio.sleep(0.25 * (attempt + 1))
        raise EngineError(f"toxiproxy request failed: {last!r}", "network") from last

    async def health_check(self) -> EngineHealth:
        if not self.s.toxiproxy_url:
            return EngineHealth(name=self.name, enabled=False, available=False, detail="CHAOS_TOXIPROXY_URL not set")
        try:
            r = await self._req("GET", "/version", retries=1)
            return EngineHealth(name=self.name, enabled=True, available=r.status_code == 200,
                                version=r.text.strip() or None, detail=f"HTTP {r.status_code}")
        except EngineError as exc:
            return EngineHealth(name=self.name, enabled=True, available=False, detail=exc.message)

    async def capabilities(self) -> list[Capability]:
        h = await self.health_check()
        out = []
        for (eng, ftype), model in FAULT_CATALOG.items():
            if eng == self.name:
                out.append(Capability(fault_type=ftype, description=f"Toxiproxy toxic for {ftype}",
                                      parameters_schema=model.model_json_schema(by_alias=True),
                                      verified=h.available,
                                      note="" if h.available else "Toxiproxy API not reachable; unverified"))
        return out

    def validate_target(self, target: ResolvedTarget) -> None:
        if not target.proxies:
            raise SafetyViolation(f"service '{target.service}' has no allowlisted toxiproxy proxies")

    def validate_parameters(self, fault_type: str, parameters: dict[str, Any], target: ResolvedTarget) -> None:
        p = validate_fault_parameters(self.name, fault_type, parameters)
        if p.proxy not in target.proxies:  # type: ignore[attr-defined]
            raise SafetyViolation(f"proxy '{p.proxy}' is not allowlisted for service '{target.service}'")  # type: ignore[attr-defined]

    async def inject_fault(self, spec: FaultSpec) -> FaultHandle:
        proxy = spec.parameters["proxy"]
        toxic = build_toxic(spec)
        path = f"/proxies/{proxy}/toxics"
        try:
            r = await self._req("POST", path, json=toxic)
        except EngineError:
            # Timeout may have happened after the server applied it; reconcile instead of retrying POST.
            existing = await self._req("GET", f"{path}/{toxic['name']}", retries=2)
            if existing.status_code == 200:
                return FaultHandle(native_id=f"{proxy}/{toxic['name']}", details={"toxic": existing.json()})
            raise
        if r.status_code == 409:  # already exists: injection is idempotent for the same unique name
            r = await self._req("GET", f"{path}/{toxic['name']}", retries=2)
        if r.status_code != 200:
            raise EngineError(f"toxiproxy rejected toxic: HTTP {r.status_code} {r.text[:300]}", "inject")
        return FaultHandle(native_id=f"{proxy}/{toxic['name']}", details={"toxic": r.json()})

    @staticmethod
    def _split(native_id: str) -> tuple[str, str]:
        proxy, _, name = native_id.partition("/")
        return proxy, name

    async def inspect_fault(self, spec: FaultSpec, handle: FaultHandle) -> FaultStatus:
        proxy, name = self._split(handle.native_id)
        r = await self._req("GET", f"/proxies/{proxy}/toxics/{name}", retries=2)
        if r.status_code == 404:
            return FaultStatus(present=False)
        r.raise_for_status()
        return FaultStatus(present=True, active=True, detail=r.json())

    async def remove_fault(self, spec: FaultSpec, handle: FaultHandle | None) -> None:
        proxy = spec.parameters["proxy"]
        name = spec.resource_name  # derived from run/fault ids: we can only ever delete our own toxic
        if handle is not None:
            p2, n2 = self._split(handle.native_id)
            if n2 != name or p2 != proxy:
                raise SafetyViolation("handle does not match run-owned resource; refusing to delete")
        r = await self._req("DELETE", f"/proxies/{proxy}/toxics/{name}", retries=3)
        if r.status_code not in (200, 204, 404):  # 404 => already gone (idempotent)
            raise EngineError(f"toxiproxy delete failed: HTTP {r.status_code} {r.text[:200]}", "cleanup")

    async def verify_recovery(self, spec: FaultSpec, handle: FaultHandle | None) -> RecoveryResult:
        proxy = spec.parameters["proxy"]
        r = await self._req("GET", f"/proxies/{proxy}/toxics", retries=2)
        if r.status_code != 200:
            return RecoveryResult(recovered=False, detail=f"cannot list toxics: HTTP {r.status_code}")
        if any(t.get("name") == spec.resource_name for t in r.json()):
            return RecoveryResult(recovered=False, detail="toxic still present on proxy")
        pr = await self._req("GET", f"/proxies/{proxy}", retries=2)
        if pr.status_code != 200 or not pr.json().get("enabled", False):
            return RecoveryResult(recovered=False, detail="proxy missing or disabled")
        if self.s.toxiproxy_data_host:  # confirm the listener accepts connections again
            port = str(pr.json().get("listen", "")).rsplit(":", 1)[-1]
            try:
                _, w = await asyncio.wait_for(
                    asyncio.open_connection(self.s.toxiproxy_data_host, int(port)), timeout=3)
                w.close()
            except (OSError, TimeoutError, ValueError) as exc:
                return RecoveryResult(recovered=False, detail=f"proxy listener unreachable: {exc!r}")
            return RecoveryResult(recovered=True, detail="toxic removed; proxy enabled; listener accepts connections")
        return RecoveryResult(recovered=True, detail="toxic removed; proxy enabled (TCP check not configured)")
