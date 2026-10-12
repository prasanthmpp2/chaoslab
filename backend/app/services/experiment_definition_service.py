"""Canonical experiment-definition construction helpers."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from app.schemas.experiment import ExperimentDocument


def build_pipeline_experiment(
    *,
    pipeline_id: str,
    project_name: str,
    primary_service: str,
    target_service: str,
    engine: str,
    fault_type: str,
    duration_seconds: int,
    fault_parameters: dict[str, Any],
    max_error_rate: float,
    probe_url: str,
) -> tuple[dict[str, Any], str]:
    """Build and fingerprint the deterministic experiment created by a pipeline."""
    document = {
        "apiVersion": "chaos.example.io/v1",
        "kind": "Experiment",
        "metadata": {
            "name": f"pipe-exp-{pipeline_id[-8:]}",
            "description": f"CI/CD multi-service resilience test for {project_name} (target: {primary_service})",
        },
        "spec": {
            "target": {"environment": "docker-test", "service": target_service},
            "fault": {
                "engine": engine,
                "type": fault_type,
                "durationSeconds": duration_seconds,
                "parameters": fault_parameters,
            },
            "hypothesis": {"maxErrorRate": max_error_rate, "requireRecovery": True},
            "probes": [{
                "name": f"{primary_service}-http-probe",
                "type": "http",
                "phases": ["baseline", "during", "recovery"],
                "url": probe_url,
                "expectStatus": 200,
                "timeoutSeconds": 2.5,
                "intervalSeconds": 1.0,
                "samples": 3,
            }],
            "safety": {
                "environmentAllowlist": ["docker-test"],
                "maxDurationSeconds": duration_seconds + 60,
            },
        },
    }
    normalized = ExperimentDocument.model_validate(document).model_dump(by_alias=True)
    fingerprint = hashlib.sha256(
        json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return normalized, fingerprint
