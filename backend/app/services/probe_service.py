from __future__ import annotations

import asyncio
import ipaddress
import math
import socket
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.config import Settings
from app.core.exceptions import SafetyViolation
from app.engines.base import ResolvedTarget
from app.schemas.probe import Phase, ProbeSample, ProbeSpec, ProbeSummary

_BLOCKED_HOSTS = {"metadata.google.internal", "metadata", "instance-data"}


def _ip_blocked(ip: ipaddress._BaseAddress) -> bool:
    return ip.is_link_local or ip.is_unspecified or ip.is_multicast or ip.is_reserved or str(ip) == "169.254.169.254"


def check_probe_url(settings: Settings, url: str) -> str:
    """Static check (definition time). Returns the hostname."""
    u = urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise SafetyViolation("probe URLs must be http(s) with a hostname")
    if u.username or u.password:
        raise SafetyViolation("credentials in probe URLs are not allowed")
    host = u.hostname.lower()
    if host in _BLOCKED_HOSTS:
        raise SafetyViolation(f"probe host '{host}' is blocked")
    try:
        if _ip_blocked(ipaddress.ip_address(host)):
            raise SafetyViolation(f"probe address '{host}' is blocked")
    except ValueError:
        pass
    allowed = {h.lower() for h in settings.probe_allowed_hosts}
    if host not in allowed:
        raise SafetyViolation(f"probe host '{host}' is not in CHAOS_PROBE_ALLOWED_HOSTS")
    return host


async def _resolve_guard(host: str, port: int) -> None:
    """Runtime check: refuse hosts that resolve to link-local / metadata ranges."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    for info in infos:
        if _ip_blocked(ipaddress.ip_address(info[4][0])):
            raise SafetyViolation(f"{host} resolves to a blocked address")


def p95(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return s[max(0, math.ceil(0.95 * len(s)) - 1)]


def summarize(name: str, phase: Phase, samples: list[ProbeSample]) -> ProbeSummary:
    errs = sum(1 for s in samples if not s.ok)
    return ProbeSummary(name=name, phase=phase, samples=len(samples), errors=errs,
                        error_rate=(errs / len(samples)) if samples else 0.0,
                        p95_latency_ms=p95([s.latency_ms for s in samples if s.ok]))


class ProbeService:
    def __init__(self, settings: Settings, docker_client: Any | None = None) -> None:
        self.s = settings
        self._docker = docker_client

    async def sample(self, spec: ProbeSpec, target: ResolvedTarget) -> ProbeSample:
        if spec.type == "http":
            return await self._http(spec)
        return await self._container(spec, target)

    async def _http(self, spec: ProbeSpec) -> ProbeSample:
        assert spec.url
        host = check_probe_url(self.s, spec.url)
        u = urlsplit(spec.url)
        t0 = time.perf_counter()
        try:
            await _resolve_guard(host, u.port or (443 if u.scheme == "https" else 80))
            async with httpx.AsyncClient(timeout=spec.timeout_seconds, follow_redirects=False) as c:
                r = await c.get(spec.url)
            ms = (time.perf_counter() - t0) * 1000
            return ProbeSample(ok=r.status_code == spec.expect_status, latency_ms=ms, status_code=r.status_code,
                               detail="" if r.status_code == spec.expect_status else f"status {r.status_code}")
        except SafetyViolation as exc:
            return ProbeSample(ok=False, latency_ms=0, detail=f"blocked: {exc.message}")
        except (httpx.HTTPError, OSError) as exc:
            return ProbeSample(ok=False, latency_ms=(time.perf_counter() - t0) * 1000, detail=type(exc).__name__)

    async def _container(self, spec: ProbeSpec, target: ResolvedTarget) -> ProbeSample:
        if spec.container not in target.containers:
            return ProbeSample(ok=False, latency_ms=0, detail="container not in target allowlist")
        if not self.s.docker_enabled:
            return ProbeSample(ok=False, latency_ms=0, detail="docker disabled in this process")
        t0 = time.perf_counter()

        def work() -> str:
            if self._docker is None:
                import docker

                self._docker = docker.from_env(timeout=10)
            return self._docker.containers.get(spec.container).status

        try:
            status = await asyncio.to_thread(work)
        except Exception as exc:  # noqa: BLE001
            return ProbeSample(ok=False, latency_ms=0, detail=type(exc).__name__)
        return ProbeSample(ok=status == "running", latency_ms=(time.perf_counter() - t0) * 1000, detail=status)

    async def collect(self, specs: list[ProbeSpec], phase: Phase, target: ResolvedTarget) -> dict[str, list[ProbeSample]]:
        """Baseline/recovery: `samples` sequential samples per probe."""
        out: dict[str, list[ProbeSample]] = {}
        for sp in [s for s in specs if phase in s.phases]:
            out[sp.name] = []
            for i in range(sp.samples):
                out[sp.name].append(await self.sample(sp, target))
                if i < sp.samples - 1:
                    await asyncio.sleep(sp.interval_seconds)
        return out
