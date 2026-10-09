import copy

import pytest

from app.core.config import ApiKeyEntry, EnvironmentConfig, ServiceTarget, Settings
from app.core.security import hash_key

EXAMPLE = {
    "apiVersion": "chaos.example.io/v1",
    "kind": "Experiment",
    "metadata": {"name": "database-latency-test", "version": "1"},
    "spec": {
        "target": {"environment": "docker-test", "service": "payment-service"},
        "fault": {"engine": "toxiproxy", "type": "network-latency",
                  "parameters": {"proxy": "payment-db", "direction": "downstream", "latencyMs": 500, "jitterMs": 100},
                  "durationSeconds": 30},
        "hypothesis": {"maxErrorRate": 0.02, "maxP95LatencyMs": 1500, "requireRecovery": True},
        "safety": {"environmentAllowlist": ["docker-test"], "maxDurationSeconds": 60, "abortOnErrorRate": 0.10},
        "cleanup": {"removeFaults": True, "verifyRecovery": True},
        "probes": [{"name": "payment-health", "type": "http", "url": "http://payment-service:8080/health"}],
    },
}


@pytest.fixture
def doc_dict():
    return copy.deepcopy(EXAMPLE)


@pytest.fixture
def settings():
    return Settings(
        toxiproxy_url="http://toxiproxy:8474",
        probe_allowed_hosts=["payment-service"],
        api_keys={hash_key("admin-key"): ApiKeyEntry(user="alice", roles=["admin"]),
                  hash_key("view-key"): ApiKeyEntry(user="bob", roles=["viewer"])},
        environments={"docker-test": EnvironmentConfig(
            engines=["toxiproxy", "pumba", "chaos_toolkit"], max_duration_seconds=60,
            services={"payment-service": ServiceTarget(containers=["payment-service"], toxiproxy_proxies=["payment-db"])})},
    )
