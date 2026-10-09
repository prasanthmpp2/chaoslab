from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServiceTarget(BaseModel):
    """Maps a logical service name to the concrete resources chaos may touch."""

    containers: list[str] = Field(default_factory=list)
    toxiproxy_proxies: list[str] = Field(default_factory=list)
    k8s_namespace: str | None = None
    k8s_labels: dict[str, str] = Field(default_factory=dict)


class EnvironmentConfig(BaseModel):
    description: str = ""
    engines: list[str]
    services: dict[str, ServiceTarget]
    max_duration_seconds: int = 60
    max_targets: int = 3


class ApiKeyEntry(BaseModel):
    user: str
    roles: list[str]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="CHAOS_", extra="ignore")

    # Infrastructure
    database_url: str = "postgresql+psycopg://chaos:chaos@postgres:5432/chaos"
    redis_url: str = "redis://redis:6379/0"
    queue_name: str = "chaos"
    artifact_dir: str = "/var/lib/chaos/artifacts"
    worker_id: str = "worker-local"

    # Safety. Dry-run is the default until an administrator opts in.
    execution_enabled: bool = False
    global_max_duration_seconds: int = 300
    max_request_bytes: int = 262_144
    rate_limit_per_minute: int = 120
    max_output_bytes: int = 65_536

    # Auth: map of sha256(api_key) hex -> {user, roles}
    api_keys: dict[str, ApiKeyEntry] = Field(default_factory=dict)

    # Environments / allowlists
    environments: dict[str, EnvironmentConfig] = Field(default_factory=dict)

    # Probes
    probe_allowed_hosts: list[str] = Field(default_factory=list)
    probe_timeout_seconds: float = 5.0
    recovery_timeout_seconds: int = 60

    # Reliability
    run_grace_seconds: int = 30
    fault_expiry_grace_seconds: int = 30
    heartbeat_stale_seconds: int = 45
    reconcile_interval_seconds: int = 10
    queued_requeue_seconds: int = 60
    cleanup_attempt_timeout_seconds: int = 30

    # Toxiproxy
    toxiproxy_url: str | None = None
    toxiproxy_data_host: str | None = None  # host used to TCP-check proxy listeners
    toxiproxy_connect_timeout: float = 3.0
    toxiproxy_request_timeout: float = 10.0

    # Pumba
    pumba_path: str = "pumba"
    pumba_tc_image: str | None = None
    docker_enabled: bool = False
    pumba_privileged_ok: bool = False  # admin attests prerequisites were verified

    # Chaos Toolkit
    chaostoolkit_path: str = "chaos"
    chaostoolkit_allowed_modules: list[str] = Field(default_factory=list)

    # Chaos Mesh (optional)
    chaos_mesh_enabled: bool = False
    kubeconfig_path: str | None = None
    kube_in_cluster: bool = False
    chaos_mesh_allowed_kinds: list[str] = Field(default_factory=lambda: ["PodChaos", "NetworkChaos"])


@lru_cache
def get_settings() -> Settings:
    return Settings()
