"""Runs only against a disposable Toxiproxy: CHAOS_INTEGRATION=1 CHAOS_TOXIPROXY_URL=http://localhost:8474"""
import os

import httpx
import pytest

from app.core.config import Settings
from app.engines.base import FaultSpec, ResolvedTarget
from app.engines.toxiproxy import ToxiproxyEngine

pytestmark = pytest.mark.skipif(os.environ.get("CHAOS_INTEGRATION") != "1", reason="set CHAOS_INTEGRATION=1")


async def test_inject_verify_remove_roundtrip():
    s = Settings(toxiproxy_url=os.environ["CHAOS_TOXIPROXY_URL"])
    async with httpx.AsyncClient(base_url=s.toxiproxy_url) as c:
        await c.post("/proxies", json={"name": "it-proxy", "listen": "0.0.0.0:18000", "upstream": "127.0.0.1:9"})
    eng = ToxiproxyEngine(s)
    spec = FaultSpec(run_id="it-run-0001", fault_id="it-fault-01", fault_type="network-latency",
                     parameters={"proxy": "it-proxy", "latencyMs": 100}, duration_seconds=5,
                     target=ResolvedTarget(environment="e", service="s", proxies=["it-proxy"]))
    h = await eng.inject_fault(spec)
    assert (await eng.inspect_fault(spec, h)).present
    await eng.remove_fault(spec, h)
    await eng.remove_fault(spec, h)  # idempotent
    assert (await eng.verify_recovery(spec, h)).recovered
    async with httpx.AsyncClient(base_url=s.toxiproxy_url) as c:
        await c.delete("/proxies/it-proxy")
