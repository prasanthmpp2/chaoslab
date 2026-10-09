# Chaos Platform Backend

Unified backend for defining, running, observing and cleaning up chaos experiments across **Toxiproxy**,
**Pumba**, **Chaos Toolkit** and (optionally) **Chaos Mesh**, behind one REST API and one persisted run lifecycle.

> ## Verification status — READ FIRST
> This codebase was written in a sandbox with **no network, no Docker, and no Python packages installed**.
> Only `python -m py_compile` was run (syntax). **Nothing else has been executed**: no tests, no migrations,
> no linting, no live Toxiproxy/Pumba/Chaos Toolkit/Chaos Mesh run. Dependency pins in `pyproject.toml` were
> written from memory and have not been resolved. Treat every "Definition of Done" item as *unverified* until you
> run the commands in [Verifying the build](#verifying-the-build) and fix what surfaces.

## Architecture
```
client ─► API (FastAPI, no docker/k8s access) ─► Postgres (experiments, runs, faults, events, probes, audit, leases)
                   └─► Redis/RQ ─► Worker ─► engine adapters ─► Toxiproxy API | Pumba CLI + Docker SDK |
                                     │                          Chaos Toolkit CLI | Kubernetes (Chaos Mesh CRDs)
Reconciler (loop) ─► expired/orphaned faults, stale runs, unclaimed QUEUED runs
```
* **State machine** (`app/schemas/run.py`): `VALIDATING → QUEUED → RUNNING_BASELINE → INJECTING → OBSERVING →
  CLEANING_UP → VERIFYING_RECOVERY → SUCCEEDED|FAILED|ABORTED|CLEANUP_FAILED` (+ `REJECTED`, `ABORTING`).
  Every transition is persisted as a `run_events` row. Execution **status**, experiment **outcome**
  (`PASSED|FAILED_HYPOTHESIS|INCONCLUSIVE|DRY_RUN|ERROR`) and **cleanup status** are separate columns.
* **Fault registry**: a `fault_instances` row (with `expires_at`) is written *before* injection. The reconciler
  removes expired/orphaned faults independent of any `finally` block.
* **Leases**: `target_leases` (primary key = `environment:service`) prevents concurrent experiments on one target.
  A run in `CLEANUP_FAILED` keeps its lease until cleanup succeeds.
* **Cleanup is never assumed**: removal is followed by `verify_recovery()`. A run cannot be `SUCCEEDED` if a fault is `REMOVAL_FAILED`.
* **Dry-run by default**: `CHAOS_EXECUTION_ENABLED=false` runs go through validation/queue/worker but inject nothing and record `DRY_RUN`.

## Engines — what is and isn't supported
| Engine | Faults | Notes |
|---|---|---|
| toxiproxy | network-latency, bandwidth-limit, connection-timeout, connection-reset, data-limit | **No packet-loss** (Toxiproxy has no such toxic; it is deliberately not offered). Toxic names are `chaos-<run8>-<fault8>`; only those are ever deleted. |
| pumba | container-kill/stop/pause, network-delay/loss, cpu-stress | Argv arrays only, no shell. `capabilities()` probes `pumba <sub> --help` on the installed binary. Network/stress faults are refused unless `CHAOS_PUMBA_PRIVILEGED_OK=true` (you attest prerequisites). Syntax is unverified against any specific Pumba release — check `pumba <sub> --help` and adjust `build_argv`. netem removal is only independently verifiable if `tc` exists in the target container; otherwise the fault ends `UNVERIFIED` and says so. |
| chaos_toolkit | native-experiment | Native experiment goes in `fault.parameters.native`. `process` providers, `secrets`, `controls`, `extensions` and python modules outside `CHAOS_CHAOSTOOLKIT_ALLOWED_MODULES` are rejected. Outcome comes from the **journal** (`status`, `deviated`, rollbacks), never from the exit code alone. `chaos validate` runs in the worker before execution. Cleanup of side effects can't be independently verified (reported as `UNVERIFIED`). |
| chaos_mesh | pod-kill, network-delay | Optional; disabled unless `CHAOS_CHAOS_MESH_ENABLED=true` and a kubeconfig/in-cluster config exist. CRDs are discovered at runtime. Manifests use `chaos-mesh.org/v1alpha1` and are **unverified against your installed CRD version**. Install Chaos Mesh separately (<https://chaos-mesh.org/docs/>); the platform never installs it. |

## Quick start (Toxiproxy test bed)
```bash
cp .env.example .env                       # set POSTGRES_PASSWORD
python scripts/make_api_key.py alice admin # merge the printed JSON into CHAOS_API_KEYS in .env
docker compose --profile testbed up -d --build
curl localhost:8000/health/ready
```
Then use `examples/requests.http`: create → `approve` → `runs`. With `CHAOS_EXECUTION_ENABLED=false` you get a dry run.
To execute against the disposable test bed set `CHAOS_EXECUTION_ENABLED=true` in `.env` and recreate `api`, `worker`, `reconciler`.
Stop: `docker compose --profile testbed down` (add `-v` to drop data).

Pumba (opt-in, **mounts the Docker socket** — see warnings): `docker compose -f docker-compose.yml -f docker-compose.pumba.yml up -d`
and build with `--build-arg PUMBA_URL=<verified release asset URL>`.

## Configuration (env vars, prefix `CHAOS_`)
`DATABASE_URL`, `REDIS_URL`, `EXECUTION_ENABLED` (false), `API_KEYS` (JSON: sha256(key) → {user, roles}),
`ENVIRONMENTS` (JSON allowlist: engines, services → containers / toxiproxy_proxies / k8s_namespace+k8s_labels, max durations),
`PROBE_ALLOWED_HOSTS`, `GLOBAL_MAX_DURATION_SECONDS`, `MAX_REQUEST_BYTES`, `RATE_LIMIT_PER_MINUTE`,
`TOXIPROXY_URL`, `TOXIPROXY_DATA_HOST`, `PUMBA_PATH`, `PUMBA_TC_IMAGE`, `DOCKER_ENABLED`, `PUMBA_PRIVILEGED_OK`,
`CHAOSTOOLKIT_PATH`, `CHAOSTOOLKIT_ALLOWED_MODULES`, `CHAOS_MESH_ENABLED`, `KUBECONFIG_PATH`, `KUBE_IN_CLUSTER`,
`CHAOS_MESH_ALLOWED_KINDS`, `HEARTBEAT_STALE_SECONDS`, `RECONCILE_INTERVAL_SECONDS`, timeouts (see `app/core/config.py`).

Roles: `viewer` (read), `author` (create/update/delete experiments), `approver` (approve a version),
`operator` (submit/cancel runs, retry cleanup, inspect faults), `admin` (all). Run submission requires the *current*
version to be approved; editing an experiment resets approval.

## API
Versioned under `/api/v1`; `X-API-Key` header on everything except `/health/*`. OpenAPI: `/docs`, or
`python scripts/export_openapi.py > openapi.json`. Endpoints match the brief, plus `POST /experiments/{id}/approve`,
`GET /runs/{id}/report`, and `GET /metrics` (Prometheus, authenticated).
`POST /runs/{id}/cancel` only *requests* cancellation (or atomically aborts a still-queued run); poll `cleanup_status`.

## Cleanup & reliability
1. Fault row (+expiry) persisted → inject → mark `ACTIVE`.
2. Always: remove (up to 3 idempotent attempts, per-call timeout) → `verify_recovery()` → `REMOVED | UNVERIFIED | REMOVAL_FAILED`.
3. `REMOVAL_FAILED` ⇒ fault `requires_attention`, run `CLEANUP_FAILED`, `alert` event + ERROR log + `chaos_cleanup_failures_total`.
   Remediate manually (e.g. `curl -X DELETE <toxiproxy>/proxies/<proxy>/toxics/<name>`; native IDs are in `GET /faults/active`),
   then `POST /runs/{id}/retry-cleanup`.
4. Worker death: the run heartbeat goes stale → reconciler finalizes cleanup and marks the run `FAILED` ("worker lost").
   Expired faults are cleaned regardless. Unclaimed `QUEUED` runs are re-enqueued (claim is an atomic compare-and-swap).
5. Disruptive jobs are never auto-retried by RQ.

## Security notes
* `docker-compose.pumba.yml` mounts `/var/run/docker.sock` into the worker/reconciler: that is effectively root on the Docker host.
  It is **not** an isolation boundary. For anything beyond a disposable dev machine, run engine workers on dedicated test hosts or agents.
* The API image never gets the socket or kube credentials. Toxiproxy's management port is unpublished, but it listens on every
  network the container joins (including `app-net`) — don't run untrusted workloads there.
* Allowlists come from `CHAOS_ENVIRONMENTS`, not from Docker labels. Probe URLs must match `CHAOS_PROBE_ALLOWED_HOSTS`, can't carry credentials,
  and are re-checked at request time against link-local/metadata addresses (DNS-rebinding between check and connect is not fully closed).
* Rate limiting is in-process per API instance (not shared across replicas). API keys are static; there is no key rotation or SSO.

## Known limitations
* `0001_initial` migration calls `Base.metadata.create_all` (not a frozen DDL snapshot). Generate later revisions with `alembic revision --autogenerate`.
* The API process reports Pumba as disabled by design (it has no Docker access); real Pumba capability checks only happen in processes with `CHAOS_DOCKER_ENABLED=true`.
* Pumba/Chaos Toolkit child processes are tracked in worker memory; after worker death the reconciler can restore container state but cannot terminate an orphaned child.
* Worker health and queue-depth metrics are only exported from the process that records them; the API `/metrics` has in-process counters, not a multiprocess aggregate.
* Pumba target resolution assumes Pumba accepts container names; container IDs are recorded for evidence only.

## Verifying the build
```bash
pip install -e '.[dev]'
ruff check . && ruff format --check .
pytest tests/unit tests/contract                     # no Docker needed
docker compose --profile testbed up -d postgres redis && alembic upgrade head
# live Toxiproxy round-trip (disposable Toxiproxy only):
CHAOS_INTEGRATION=1 CHAOS_TOXIPROXY_URL=http://localhost:8474 pytest tests/integration
```
Not yet written: integration tests for Pumba, Chaos Toolkit, Chaos Mesh, worker-crash reconciliation, and API-restart persistence.

## Troubleshooting
* `503` on `/health/ready`: check `checks` in the body (database / redis).
* Run stays `QUEUED`: is the `worker` up and on queue `chaos`? The reconciler re-enqueues after `QUEUED_REQUEUE_SECONDS`.
* `422 safety_violation`: target/engine/proxy/probe host not in `CHAOS_ENVIRONMENTS` / `CHAOS_PROBE_ALLOWED_HOSTS`.
* Run is `SUCCEEDED` with `DRY_RUN`: execution is disabled by design.
