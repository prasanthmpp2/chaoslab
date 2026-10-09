import json

import httpx
import pytest

from app.core.exceptions import SafetyViolation
from app.engines.base import FaultHandle, FaultSpec, ResolvedTarget
from app.engines.toxiproxy import ToxiproxyEngine, build_toxic

RUN, FAULT = "11111111-aaaa", "22222222-bbbb"


def spec(**p):
    params = {"proxy": "payment-db", "direction": "downstream", "latencyMs": 500, "jitterMs": 100, **p}
    return FaultSpec(run_id=RUN, fault_id=FAULT, fault_type="network-latency", parameters=params, duration_seconds=30,
                     target=ResolvedTarget(environment="e", service="s", proxies=["payment-db"]))


def engine(settings, handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://toxi")
    return ToxiproxyEngine(settings, client)


def test_build_toxic_matches_toxiproxy_api():
    t = build_toxic(spec())
    assert t == {"name": "chaos-11111111-22222222", "type": "latency", "stream": "downstream", "toxicity": 1.0,
                 "attributes": {"latency": 500, "jitter": 100}}


async def test_inject_posts_unique_named_toxic(settings):
    seen = {}

    def h(req):
        seen["path"], seen["body"] = req.url.path, json.loads(req.content)
        return httpx.Response(200, json=seen["body"])

    handle = await engine(settings, h).inject_fault(spec())
    assert seen["path"] == "/proxies/payment-db/toxics"
    assert handle.native_id == "payment-db/chaos-11111111-22222222"


async def test_inject_conflict_is_idempotent(settings):
    def h(req):
        if req.method == "POST":
            return httpx.Response(409, text="exists")
        return httpx.Response(200, json={"name": "chaos-11111111-22222222"})

    handle = await engine(settings, h).inject_fault(spec())
    assert handle.native_id.endswith("chaos-11111111-22222222")


async def test_remove_is_idempotent_on_404(settings):
    await engine(settings, lambda r: httpx.Response(404)).remove_fault(spec(), None)


async def test_remove_refuses_foreign_resource(settings):
    with pytest.raises(SafetyViolation):
        await engine(settings, lambda r: httpx.Response(200)).remove_fault(
            spec(), FaultHandle(native_id="payment-db/someone-elses-toxic"))


async def test_remove_retries_safe_delete_on_network_error(settings):
    calls = {"n": 0}

    def h(req):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("boom")
        return httpx.Response(204)

    await engine(settings, h).remove_fault(spec(), None)
    assert calls["n"] == 3


async def test_verify_detects_leftover_toxic(settings):
    def h(req):
        if req.url.path.endswith("/toxics"):
            return httpx.Response(200, json=[{"name": "chaos-11111111-22222222"}])
        return httpx.Response(200, json={"enabled": True, "listen": "[::]:8000"})

    res = await engine(settings, h).verify_recovery(spec(), None)
    assert not res.recovered


async def test_verify_ok_when_removed_and_enabled(settings):
    def h(req):
        if req.url.path.endswith("/toxics"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"enabled": True, "listen": "[::]:8000"})

    assert (await engine(settings, h).verify_recovery(spec(), None)).recovered


async def test_post_is_never_retried(settings):
    n = {"c": 0}

    def h(req):
        n["c"] += 1
        raise httpx.ConnectError("down")

    from app.core.exceptions import EngineError

    with pytest.raises(EngineError):
        await engine(settings, h).inject_fault(spec())
    posts = n["c"]
    assert posts <= 1 + 3  # one POST, then GET reconciliation attempts only
