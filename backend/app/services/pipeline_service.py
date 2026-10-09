"""CI/CD Pipeline service: automatically containerizes uploaded projects and executes chaos resilience tests."""
from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import tarfile
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.core.config import ServiceTarget, Settings, get_settings
from app.core.logging import get_logger
from app.db.session import session_scope
from app.engines.registry import EngineRegistry
from app.models import ExperimentRun, ProbeResult, RunEvent
from app.schemas.experiment import ExperimentDocument
from app.services.experiment_service import ExperimentService
from app.workers.jobs import enqueue_run

log = get_logger(__name__)

# In-memory registry for fast polling of active pipelines
_PIPELINES: dict[str, dict[str, Any]] = {}
_NEXT_PROXY_PORT = 8100


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_pipeline_dir(settings: Settings, pipeline_id: str) -> Path:
    p = Path(settings.artifact_dir) / "pipelines" / pipeline_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def _save_pipeline_state(settings: Settings, state: dict[str, Any]) -> None:
    pipeline_id = state["id"]
    _PIPELINES[pipeline_id] = state
    p_dir = _get_pipeline_dir(settings, pipeline_id)
    with open(p_dir / "pipeline.json", "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def get_pipeline(settings: Settings, pipeline_id: str) -> dict[str, Any] | None:
    if pipeline_id in _PIPELINES:
        return _PIPELINES[pipeline_id]
    p_file = Path(settings.artifact_dir) / "pipelines" / pipeline_id / "pipeline.json"
    if p_file.exists():
        try:
            with open(p_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                _PIPELINES[pipeline_id] = data
                return data
        except Exception:
            return None
    return None


def list_pipelines(settings: Settings) -> list[dict[str, Any]]:
    # Merge memory and disk
    results = list(_PIPELINES.values())
    p_base = Path(settings.artifact_dir) / "pipelines"
    if p_base.exists():
        for d in p_base.iterdir():
            if d.is_dir() and d.name not in _PIPELINES:
                p_file = d / "pipeline.json"
                if p_file.exists():
                    try:
                        with open(p_file, "r", encoding="utf-8") as f:
                            results.append(json.load(f))
                    except Exception:
                        pass
    results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return results


class PipelineService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.s = settings or get_settings()
        self.s.execution_enabled = True

    def _log(self, state: dict[str, Any], level: str, msg: str) -> None:
        entry = {
            "timestamp": _utcnow_iso(),
            "level": level.upper(),
            "message": msg,
        }
        state["logs"].append(entry)
        _save_pipeline_state(self.s, state)
        log.info(f"[{state['id']}] {msg}")

    def create_pipeline(
        self,
        project_name: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        pipeline_id = f"pipe-{uuid.uuid4().hex[:8]}"
        slug_name = "".join(c if c.isalnum() or c in ("-", "_") else "-" for c in project_name.lower()).strip("-")
        if not slug_name:
            slug_name = "project"

        state: dict[str, Any] = {
            "id": pipeline_id,
            "project_name": slug_name,
            "created_at": _utcnow_iso(),
            "updated_at": _utcnow_iso(),
            "status": "QUEUED",
            "current_stage": "Extracting Project Archive",
            "stages": [
                {"id": "extract", "name": "Extract Source", "status": "pending", "started_at": None, "ended_at": None},
                {"id": "build", "name": "Docker Containerize", "status": "pending", "started_at": None, "ended_at": None},
                {"id": "deploy", "name": "Deploy & Network", "status": "pending", "started_at": None, "ended_at": None},
                {"id": "health", "name": "Initial Health Check", "status": "pending", "started_at": None, "ended_at": None},
                {"id": "chaos", "name": "Chaos Engine Test", "status": "pending", "started_at": None, "ended_at": None},
                {"id": "verdict", "name": "Resilience Verdict", "status": "pending", "started_at": None, "ended_at": None},
            ],
            "config": {
                "container_port": int(config.get("container_port", 80)),
                "health_path": str(config.get("health_path", "/")),
                "fault_engine": str(config.get("fault_engine", "pumba")),
                "fault_type": str(config.get("fault_type", "container-restart")),
                "fault_duration": int(config.get("fault_duration", 15)),
                "fault_params": config.get("fault_params", {}),
                "auto_cleanup": bool(config.get("auto_cleanup", True)),
                "max_error_rate": float(config.get("max_error_rate", 0.10)),
            },
            "container": {
                "name": f"chaos-pipe-{slug_name}-{pipeline_id[-4:]}",
                "image": f"chaos-pipe-{slug_name}:{pipeline_id[-6:]}",
                "status": "not_started",
                "proxy_port": None,
            },
            "experiment_id": None,
            "experiment_run_id": None,
            "verdict": None,
            "logs": [],
        }
        _save_pipeline_state(self.s, state)
        self._log(state, "info", f"Pipeline {pipeline_id} created for project '{slug_name}'")
        return state

    def unpack_archive(self, pipeline_id: str, archive_bytes: bytes, filename: str = "project.zip") -> Path:
        state = get_pipeline(self.s, pipeline_id)
        if not state:
            raise ValueError(f"Pipeline {pipeline_id} not found")

        work_dir = _get_pipeline_dir(self.s, pipeline_id) / "src"
        if work_dir.exists():
            shutil.rmtree(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)

        self._update_stage(state, "extract", "running")
        self._log(state, "info", f"Unpacking project archive ({len(archive_bytes)} bytes)...")

        # Try zip
        try:
            if zipfile.is_zipfile(io.BytesIO(archive_bytes)):
                with zipfile.ZipFile(io.BytesIO(archive_bytes)) as z:
                    for member in z.infolist():
                        # Prevent Zip Slip directory traversal
                        target = (work_dir / member.filename).resolve()
                        if not str(target).startswith(str(work_dir.resolve())):
                            raise ValueError(f"Zip slip path traversal detected: {member.filename}")
                        z.extract(member, work_dir)
                self._log(state, "info", f"Extracted ZIP archive into workspace.")
            elif tarfile.is_tarfile(io.BytesIO(archive_bytes)):
                with tarfile.open(fileobj=io.BytesIO(archive_bytes)) as t:
                    for member in t.getmembers():
                        target = (work_dir / member.name).resolve()
                        if not str(target).startswith(str(work_dir.resolve())):
                            raise ValueError(f"Tar slip path traversal detected: {member.name}")
                        t.extract(member, work_dir)
                self._log(state, "info", f"Extracted TAR archive into workspace.")
            else:
                # Raw files or single text
                (work_dir / "index.html").write_bytes(archive_bytes)
                self._log(state, "info", "Extracted raw payload as index.html.")
        except Exception as exc:
            self._update_stage(state, "extract", "failed")
            self._log(state, "error", f"Extraction failed: {exc}")
            raise

        # Check / generate Dockerfile
        df_path = work_dir / "Dockerfile"
        if not df_path.exists():
            self._auto_generate_dockerfile(work_dir, state)

        self._update_stage(state, "extract", "completed")
        return work_dir

    def _auto_generate_dockerfile(self, work_dir: Path, state: dict[str, Any]) -> None:
        self._log(state, "info", "No Dockerfile found. Auto-detecting project type...")
        files = [f.name for f in work_dir.iterdir()]

        if any(f.endswith(".html") for f in files) or "index.html" in files:
            self._log(state, "info", "Detected web application. Generating Nginx Dockerfile.")
            df_content = (
                "FROM nginx:1.27-alpine\n"
                "COPY . /usr/share/nginx/html/\n"
                "EXPOSE 80\n"
                'CMD ["nginx", "-g", "daemon off;"]\n'
            )
            (work_dir / "Dockerfile").write_text(df_content, encoding="utf-8")
        elif "requirements.txt" in files or any(f.endswith(".py") for f in files):
            self._log(state, "info", "Detected Python application. Generating Python Dockerfile.")
            main_file = "main.py" if "main.py" in files else ("app.py" if "app.py" in files else files[0])
            df_content = (
                "FROM chaos-api:latest\n"
                "WORKDIR /app\n"
                "COPY . /app\n"
                f'CMD ["python", "{main_file}"]\n'
            )
            (work_dir / "Dockerfile").write_text(df_content, encoding="utf-8")
        else:
            self._log(state, "info", "Defaulting to static web server with nginx:1.27-alpine.")
            df_content = (
                "FROM nginx:1.27-alpine\n"
                "COPY . /usr/share/nginx/html/\n"
                "EXPOSE 80\n"
                'CMD ["nginx", "-g", "daemon off;"]\n'
            )
            (work_dir / "Dockerfile").write_text(df_content, encoding="utf-8")

    def _update_stage(self, state: dict[str, Any], stage_id: str, status: str) -> None:
        for s in state["stages"]:
            if s["id"] == stage_id:
                s["status"] = status
                if status == "running" and not s["started_at"]:
                    s["started_at"] = _utcnow_iso()
                elif status in ("completed", "failed"):
                    s["ended_at"] = _utcnow_iso()
        state["updated_at"] = _utcnow_iso()
        _save_pipeline_state(self.s, state)

    async def execute_pipeline(self, pipeline_id: str) -> dict[str, Any]:
        """Runs the entire CI/CD Chaos Pipeline asynchronously."""
        state = get_pipeline(self.s, pipeline_id)
        if not state:
            raise ValueError(f"Pipeline {pipeline_id} not found")

        state["status"] = "RUNNING"
        _save_pipeline_state(self.s, state)

        import docker
        docker_client = docker.from_env()

        work_dir = _get_pipeline_dir(self.s, pipeline_id) / "src"
        image_tag = state["container"]["image"]
        container_name = state["container"]["name"]
        container_port = state["config"]["container_port"]
        health_path = state["config"]["health_path"]
        fault_engine = state["config"]["fault_engine"]
        fault_type = state["config"]["fault_type"]
        fault_duration = state["config"]["fault_duration"]
        fault_params = state["config"]["fault_params"]
        auto_cleanup = state["config"]["auto_cleanup"]
        max_error_rate = state["config"]["max_error_rate"]

        container = None
        proxy_name = None
        proxy_port = None
        service_name = f"pipe-{state['project_name']}-{pipeline_id[-4:]}"

        try:
            # ----------------------------------------------------
            # 1. BUILD DOCKER CONTAINER
            # ----------------------------------------------------
            state["current_stage"] = "Building Docker Container"
            self._update_stage(state, "build", "running")
            self._log(state, "info", f"Building Docker image '{image_tag}' from {work_dir}...")

            def _build_image():
                return docker_client.images.build(
                    path=str(work_dir),
                    tag=image_tag,
                    rm=True,
                )

            t0 = time.monotonic()
            img, build_logs = await asyncio.to_thread(_build_image)
            build_duration = time.monotonic() - t0
            self._log(state, "info", f"Docker build succeeded in {build_duration:.2f}s! Image ID: {img.short_id}")
            self._update_stage(state, "build", "completed")

            # ----------------------------------------------------
            # 2. DEPLOY CONTAINER TO CHAOS APP NETWORK
            # ----------------------------------------------------
            state["current_stage"] = "Deploying Container"
            self._update_stage(state, "deploy", "running")
            self._log(state, "info", f"Deploying container '{container_name}' on network 'chaos_app-net'...")

            # Clean any old container with identical name
            try:
                old = docker_client.containers.get(container_name)
                old.remove(force=True)
            except Exception:
                pass

            restart_policy = {"Name": "always"} if fault_type in ("container-kill", "container-restart") else None

            def _run_container():
                return docker_client.containers.run(
                    image_tag,
                    name=container_name,
                    network="chaos_app-net",
                    detach=True,
                    restart_policy=restart_policy,
                    labels={"chaoslab.pipeline": pipeline_id, "chaoslab.project": state["project_name"]},
                )

            container = await asyncio.to_thread(_run_container)
            state["container"]["status"] = "running"
            self._log(state, "info", f"Container '{container_name}' is running. Container ID: {container.short_id}")
            self._update_stage(state, "deploy", "completed")

            # ----------------------------------------------------
            # 3. INITIAL HEALTH CHECK
            # ----------------------------------------------------
            state["current_stage"] = "Checking Initial Health"
            self._update_stage(state, "health", "running")
            probe_url = f"http://{container_name}:{container_port}{health_path}"
            self._log(state, "info", f"Verifying container readiness at {probe_url}...")

            healthy = False
            for attempt in range(1, 15):
                await asyncio.sleep(1)
                try:
                    async with httpx.AsyncClient(timeout=2.0) as client:
                        resp = await client.get(probe_url)
                        if resp.status_code == 200:
                            healthy = True
                            self._log(state, "info", f"Health check passed! (HTTP 200 on attempt {attempt})")
                            break
                        else:
                            self._log(state, "warning", f"Attempt {attempt}: returned HTTP {resp.status_code}")
                except Exception as e:
                    self._log(state, "warning", f"Attempt {attempt}: connection pending ({type(e).__name__})")

            if not healthy:
                raise RuntimeError(f"Container failed initial health check at {probe_url} after 15s")

            self._update_stage(state, "health", "completed")

            # ----------------------------------------------------
            # 4. TARGET REGISTRATION & TOXIPROXY (IF APPLICABLE)
            # ----------------------------------------------------
            state["current_stage"] = "Configuring Chaos Target"
            target_proxies = []
            final_probe_url = probe_url

            if fault_engine in ("toxiproxy",):
                global _NEXT_PROXY_PORT
                proxy_port = _NEXT_PROXY_PORT
                _NEXT_PROXY_PORT += 1
                proxy_name = f"pipe-{pipeline_id[-6:]}"
                state["container"]["proxy_port"] = proxy_port
                self._log(state, "info", f"Creating Toxiproxy proxy '{proxy_name}' on port {proxy_port}...")

                async with httpx.AsyncClient(timeout=3.0) as client:
                    tox_resp = await client.post(
                        "http://toxiproxy:8474/proxies",
                        json={
                            "name": proxy_name,
                            "listen": f"0.0.0.0:{proxy_port}",
                            "upstream": f"{container_name}:{container_port}",
                            "enabled": True,
                        },
                    )
                    if tox_resp.status_code not in (200, 201):
                        raise RuntimeError(f"Toxiproxy creation failed: {tox_resp.text}")

                target_proxies.append(proxy_name)
                final_probe_url = f"http://toxiproxy:{proxy_port}{health_path}"
                self._log(state, "info", f"Toxiproxy proxy active! Upstream={container_name}:{container_port}, TestURL={final_probe_url}")

            # Register in Settings environment
            self.s.environments.setdefault("docker-test", None)
            if self.s.environments.get("docker-test"):
                self.s.environments["docker-test"].services[service_name] = ServiceTarget(
                    containers=[container_name],
                    toxiproxy_proxies=target_proxies,
                )

            # Ensure host is allowed in probe hosts
            if container_name not in self.s.probe_allowed_hosts:
                self.s.probe_allowed_hosts.append(container_name)
            if "toxiproxy" not in self.s.probe_allowed_hosts:
                self.s.probe_allowed_hosts.append("toxiproxy")

            # ----------------------------------------------------
            # 5. EXECUTE CHAOS EXPERIMENT
            # ----------------------------------------------------
            state["current_stage"] = "Running Chaos Engine Resilience Test"
            self._update_stage(state, "chaos", "running")
            self._log(state, "info", f"Configuring Chaos Experiment using engine '{fault_engine}', fault '{fault_type}' ({fault_duration}s)...")

            # Normalize fault type and parameters
            fault_doc_params = dict(fault_params)
            if fault_type in ("container-restart", "container-kill"):
                fault_type = "container-kill"
                fault_doc_params.setdefault("signal", "SIGKILL")
            elif fault_type in ("container-pause", "container-stop"):
                pass
            elif fault_type == "cpu-stress":
                fault_doc_params.setdefault("workers", 1)
            elif fault_type == "network-latency":
                fault_doc_params.setdefault("latencyMs", 500)
                if proxy_name:
                    fault_doc_params["proxy"] = proxy_name
            elif fault_engine == "toxiproxy" and proxy_name:
                fault_doc_params["proxy"] = proxy_name

            exp_doc = {
                "apiVersion": "chaos.example.io/v1",
                "kind": "Experiment",
                "metadata": {
                    "name": f"pipe-exp-{pipeline_id[-8:]}",
                    "description": f"CI/CD resilience test for {state['project_name']}",
                },
                "spec": {
                    "target": {
                        "environment": "docker-test",
                        "service": service_name,
                    },
                    "fault": {
                        "engine": fault_engine,
                        "type": fault_type,
                        "durationSeconds": fault_duration,
                        "parameters": fault_doc_params,
                    },
                    "hypothesis": {
                        "maxErrorRate": max_error_rate,
                        "requireRecovery": True,
                    },
                    "probes": [
                        {
                            "name": "service-http-probe",
                            "type": "http",
                            "phases": ["baseline", "during", "recovery"],
                            "url": final_probe_url,
                            "expectStatus": 200,
                            "timeoutSeconds": 2.5,
                            "intervalSeconds": 1.0,
                            "samples": 3,
                        }
                    ],
                    "safety": {
                        "environmentAllowlist": ["docker-test"],
                        "maxDurationSeconds": fault_duration + 60,
                    },
                },
            }

            # Create & Start experiment
            registry = EngineRegistry.from_settings(self.s)
            with session_scope() as db:
                exp_svc = ExperimentService(db, self.s, registry)
                exp = exp_svc.create(exp_doc, actor="ci-cd-pipeline")
                exp_id = exp.id
                state["experiment_id"] = exp_id
                _save_pipeline_state(self.s, state)

                # Ensure approved and start run
                exp_svc.approve(exp_id, actor="ci-cd-pipeline")
                run = exp_svc.start_run(exp_id, actor="ci-cd-pipeline", enqueue=enqueue_run)
                run_id = run.id
                state["experiment_run_id"] = run_id
                _save_pipeline_state(self.s, state)

            self._log(state, "info", f"Started Experiment Run: {run_id}. Waiting for chaos execution and steady-state observation...")

            # Wait for experiment run completion
            terminal = False
            run_status = "QUEUED"
            outcome = "PENDING"
            max_wait = fault_duration + 60
            start_wait = time.monotonic()

            while time.monotonic() - start_wait < max_wait:
                await asyncio.sleep(2)
                with session_scope() as db:
                    r = db.get(ExperimentRun, run_id)
                    if r:
                        run_status = r.status
                        outcome = r.outcome
                        events = db.query(RunEvent).filter(RunEvent.run_id == run_id).order_by(RunEvent.created_at.desc()).limit(1).all()
                        if events:
                            latest_evt = f"{events[0].event_type}: {events[0].message}"
                            self._log(state, "info", f"Run status={run_status} ({latest_evt})")

                        if run_status in ("SUCCEEDED", "FAILED", "ABORTED", "CLEANUP_FAILED"):
                            terminal = True
                            break

            if not terminal:
                self._log(state, "warning", f"Experiment run timed out after {max_wait}s; checking final status")

            self._update_stage(state, "chaos", "completed" if terminal else "failed")

            # ----------------------------------------------------
            # 6. EVALUATE VERDICT
            # ----------------------------------------------------
            state["current_stage"] = "Computing Resilience Verdict"
            self._update_stage(state, "verdict", "running")

            # Retrieve probe results
            baseline_err = 0.0
            during_err = 0.0
            recovery_err = 0.0
            baseline_p95 = None
            during_p95 = None
            recovery_p95 = None

            with session_scope() as db:
                probes = db.query(ProbeResult).filter(ProbeResult.run_id == run_id).all()
                for p in probes:
                    if not p.probe_name.endswith(":p95_ms"):
                        if p.phase == "baseline" and p.measurement is not None:
                            baseline_err = p.measurement
                        elif p.phase == "during" and p.measurement is not None:
                            during_err = p.measurement
                        elif p.phase == "recovery" and p.measurement is not None:
                            recovery_err = p.measurement
                    else:
                        if p.phase == "baseline":
                            baseline_p95 = p.measurement
                        elif p.phase == "during":
                            during_p95 = p.measurement
                        elif p.phase == "recovery":
                            recovery_p95 = p.measurement

            resilience_passed = run_status == "SUCCEEDED" and outcome in ("PASSED", "DRY_RUN")

            state["verdict"] = {
                "passed": resilience_passed,
                "run_status": run_status,
                "run_outcome": outcome,
                "baseline_error_rate": baseline_err,
                "during_error_rate": during_err,
                "recovery_error_rate": recovery_err,
                "baseline_p95_ms": baseline_p95,
                "during_p95_ms": during_p95,
                "recovery_p95_ms": recovery_p95,
                "summary": (
                    f"Resilience test PASSED! Container sustained {fault_type} and recovered successfully."
                    if resilience_passed
                    else f"Resilience test FAILED: Run outcome was {outcome} with during-fault error rate {during_err:.1%}."
                ),
            }

            self._log(state, "info", state["verdict"]["summary"])
            self._update_stage(state, "verdict", "completed" if resilience_passed else "failed")

            state["status"] = "PASSED" if resilience_passed else "FAILED"
            state["current_stage"] = "Pipeline Completed"

        except Exception as exc:
            log.exception("Pipeline failed", extra={"error_category": "pipeline"})
            self._log(state, "error", f"Pipeline aborted due to error: {exc}")
            state["status"] = "FAILED"
            state["current_stage"] = f"Failed: {exc}"
            state["verdict"] = {
                "passed": False,
                "summary": f"Pipeline execution failed: {exc}",
                "baseline_error_rate": 1.0,
                "during_error_rate": 1.0,
                "recovery_error_rate": 1.0,
            }
        finally:
            # ----------------------------------------------------
            # 7. CLEANUP / TEARDOWN (IF ENABLED)
            # ----------------------------------------------------
            if auto_cleanup:
                self._log(state, "info", "Auto-cleanup enabled: tearing down container and test proxies...")
                try:
                    if container:
                        container.stop(timeout=2)
                        container.remove(force=True)
                        state["container"]["status"] = "removed"
                        self._log(state, "info", f"Container '{container_name}' stopped and removed.")
                except Exception as e:
                    self._log(state, "warning", f"Container removal error: {e}")

                if proxy_name:
                    try:
                        async with httpx.AsyncClient(timeout=2.0) as client:
                            await client.delete(f"http://toxiproxy:8474/proxies/{proxy_name}")
                        self._log(state, "info", f"Toxiproxy proxy '{proxy_name}' deleted.")
                    except Exception as e:
                        self._log(state, "warning", f"Proxy removal error: {e}")

                try:
                    docker_client.images.remove(image_tag, force=True)
                    self._log(state, "info", f"Image '{image_tag}' removed.")
                except Exception:
                    pass
            else:
                self._log(state, "info", f"Auto-cleanup disabled. Container '{container_name}' remains active on 'chaos_app-net'.")

            state["updated_at"] = _utcnow_iso()
            _save_pipeline_state(self.s, state)

        return state

    def teardown_pipeline(self, pipeline_id: str) -> bool:
        """Manually stop and remove resources for a pipeline."""
        state = get_pipeline(self.s, pipeline_id)
        if not state:
            return False

        import docker
        docker_client = docker.from_env()

        c_name = state["container"]["name"]
        try:
            c = docker_client.containers.get(c_name)
            c.stop(timeout=2)
            c.remove(force=True)
            state["container"]["status"] = "removed"
            self._log(state, "info", f"Manually stopped and removed container '{c_name}'.")
        except Exception:
            pass

        p_port = state["container"].get("proxy_port")
        if p_port:
            proxy_name = f"pipe-{pipeline_id[-6:]}"
            try:
                import urllib.request
                req = urllib.request.Request(f"http://toxiproxy:8474/proxies/{proxy_name}", method="DELETE")
                urllib.request.urlopen(req, timeout=2)
                self._log(state, "info", f"Manually removed Toxiproxy proxy '{proxy_name}'.")
            except Exception:
                pass

        _save_pipeline_state(self.s, state)
        return True
