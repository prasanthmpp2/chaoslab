"""CI/CD Pipeline service: automatically discovers multi-service architectures,
builds Docker containers for each service, and executes chaos resilience tests.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import shutil
import tarfile
import time
import urllib.parse
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml

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


def download_github_repo(repo_url: str, branch: str = "", token: str = "") -> tuple[bytes, str]:
    """Downloads a GitHub repository archive (ZIP) given its URL or slug."""
    clean_url = repo_url.strip()
    if clean_url.endswith(".git"):
        clean_url = clean_url[:-4]

    # Parse owner and repo from URL or string (e.g. https://github.com/owner/repo or owner/repo)
    if "github.com/" in clean_url:
        parts = clean_url.split("github.com/")[-1].strip("/").split("/")
    else:
        parts = clean_url.strip("/").split("/")

    if len(parts) < 2:
        raise ValueError(f"Invalid GitHub repository format: '{repo_url}'. Expected 'owner/repo' or URL.")

    owner, repo = parts[0], parts[1]
    ref = branch.strip() if branch.strip() else "HEAD"

    # Try downloading via archive URL
    download_urls = [
        f"https://github.com/{owner}/{repo}/archive/{ref}.zip" if ref != "HEAD" else f"https://github.com/{owner}/{repo}/archive/HEAD.zip",
        f"https://api.github.com/repos/{owner}/{repo}/zipball/{ref}",
    ]

    headers = {"User-Agent": "ChaosLab-CI-CD/1.0"}
    if token.strip():
        headers["Authorization"] = f"token {token.strip()}"

    last_error = None
    for d_url in download_urls:
        try:
            req = urllib.request.Request(d_url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                if len(data) > 0:
                    return data, repo
        except Exception as e:
            last_error = e

    raise RuntimeError(f"Failed to download repository '{owner}/{repo}' from GitHub: {last_error}")


def discover_services(
    work_dir: Path,
    slug_name: str,
    pipeline_id: str,
    default_port: int = 80,
    default_health_path: str = "/",
    target_service_pref: str = "",
) -> list[dict[str, Any]]:
    """Discovers all microservices in a repository (via docker-compose or multiple Dockerfiles)."""
    short_pid = pipeline_id[-4:]
    tag_pid = pipeline_id[-6:]
    services: list[dict[str, Any]] = []

    # 1. Check for docker-compose file
    compose_names = ["docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"]
    compose_path = None
    for c_name in compose_names:
        cand = work_dir / c_name
        if cand.exists():
            compose_path = cand
            break

    if compose_path:
        try:
            with open(compose_path, "r", encoding="utf-8") as f:
                c_data = yaml.safe_load(f)
            if isinstance(c_data, dict) and "services" in c_data and isinstance(c_data["services"], dict):
                for s_name, s_conf in c_data["services"].items():
                    s_slug = re.sub(r"[^a-z0-9_-]", "-", str(s_name).lower()).strip("-")
                    build_info = s_conf.get("build") if isinstance(s_conf, dict) else None
                    image_info = s_conf.get("image") if isinstance(s_conf, dict) else None

                    build_dir = None
                    df_name = "Dockerfile"
                    is_prebuilt = False
                    image_tag = f"chaos-pipe-{slug_name}-{s_slug}:{tag_pid}"

                    if isinstance(build_info, str):
                        build_dir = (work_dir / build_info).resolve()
                    elif isinstance(build_info, dict):
                        b_ctx = build_info.get("context", ".")
                        build_dir = (work_dir / b_ctx).resolve()
                        df_name = build_info.get("dockerfile", "Dockerfile")
                    elif image_info:
                        image_tag = image_info
                        is_prebuilt = True

                    # Parse ports
                    s_port = default_port
                    ports = s_conf.get("ports", []) if isinstance(s_conf, dict) else []
                    if ports and isinstance(ports, list):
                        p_str = str(ports[0])
                        if ":" in p_str:
                            p_parts = p_str.split(":")
                            try:
                                s_port = int(p_parts[-1].split("/")[0])
                            except Exception:
                                pass
                        else:
                            try:
                                s_port = int(p_str.split("/")[0])
                            except Exception:
                                pass

                    # Parse environment
                    env_dict = {}
                    raw_env = s_conf.get("environment", []) if isinstance(s_conf, dict) else []
                    if isinstance(raw_env, list):
                        for item in raw_env:
                            if isinstance(item, str) and "=" in item:
                                k, v = item.split("=", 1)
                                env_dict[k.strip()] = v.strip()
                    elif isinstance(raw_env, dict):
                        env_dict = {str(k): str(v) for k, v in raw_env.items()}

                    container_name = f"chaos-pipe-{slug_name}-{s_slug}-{short_pid}"

                    services.append({
                        "name": str(s_name),
                        "slug": s_slug,
                        "build_path": str(build_dir.relative_to(work_dir)) if build_dir and build_dir != work_dir else ".",
                        "dockerfile": df_name,
                        "image": image_tag,
                        "is_prebuilt": is_prebuilt,
                        "container_name": container_name,
                        "port": s_port,
                        "health_path": default_health_path,
                        "environment": env_dict,
                        "status": "pending",
                        "primary": False,
                    })
        except Exception as e:
            log.warning(f"Error parsing compose file {compose_path}: {e}")

    # 2. If no compose services found, scan for Dockerfiles in directories
    if not services:
        ignore_dirs = {".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build"}
        df_matches: list[Path] = []
        for root, dirs, files in os.walk(work_dir):
            dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith(".")]
            for f in files:
                if f == "Dockerfile" or f.startswith("Dockerfile."):
                    df_matches.append(Path(root) / f)

        if len(df_matches) > 1:
            # Multiple services detected via Dockerfiles
            for df_path in df_matches:
                rel_dir = df_path.parent.relative_to(work_dir)
                if df_path.parent == work_dir:
                    s_name = df_path.name.split(".", 1)[-1] if "." in df_path.name else slug_name
                else:
                    s_name = df_path.parent.name

                s_slug = re.sub(r"[^a-z0-9_-]", "-", s_name.lower()).strip("-")
                container_name = f"chaos-pipe-{slug_name}-{s_slug}-{short_pid}"
                image_tag = f"chaos-pipe-{slug_name}-{s_slug}:{tag_pid}"

                # Check EXPOSE in Dockerfile
                s_port = default_port
                try:
                    df_text = df_path.read_text(encoding="utf-8")
                    exp_match = re.search(r"EXPOSE\s+(\d+)", df_text, re.IGNORECASE)
                    if exp_match:
                        s_port = int(exp_match.group(1))
                except Exception:
                    pass

                services.append({
                    "name": s_name,
                    "slug": s_slug,
                    "build_path": str(rel_dir) if str(rel_dir) != "." else ".",
                    "dockerfile": df_path.name,
                    "image": image_tag,
                    "is_prebuilt": False,
                    "container_name": container_name,
                    "port": s_port,
                    "health_path": default_health_path,
                    "environment": {},
                    "status": "pending",
                    "primary": False,
                })

    # 3. If still no services found, auto-generate single root service
    if not services:
        df_path = work_dir / "Dockerfile"
        if not df_path.exists():
            _auto_generate_dockerfile_for_dir(work_dir)

        s_port = default_port
        try:
            df_text = df_path.read_text(encoding="utf-8")
            exp_match = re.search(r"EXPOSE\s+(\d+)", df_text, re.IGNORECASE)
            if exp_match:
                s_port = int(exp_match.group(1))
        except Exception:
            pass

        container_name = f"chaos-pipe-{slug_name}-{short_pid}"
        image_tag = f"chaos-pipe-{slug_name}:{tag_pid}"
        services.append({
            "name": slug_name,
            "slug": slug_name,
            "build_path": ".",
            "dockerfile": "Dockerfile",
            "image": image_tag,
            "is_prebuilt": False,
            "container_name": container_name,
            "port": s_port,
            "health_path": default_health_path,
            "environment": {},
            "status": "pending",
            "primary": True,
        })

    # Designate primary target service
    found_target = False
    if target_service_pref:
        pref_clean = target_service_pref.lower().strip()
        for s in services:
            if s["name"].lower() == pref_clean or s["slug"] == pref_clean:
                s["primary"] = True
                found_target = True
                break

    if not found_target and services:
        # Default to first non-db service or first service
        non_db = [s for s in services if not any(db in s["name"].lower() for db in ("db", "postgres", "mysql", "redis", "mongo"))]
        target = non_db[0] if non_db else services[0]
        target["primary"] = True

    return services


def _auto_generate_dockerfile_for_dir(target_dir: Path) -> None:
    files = [f.name for f in target_dir.iterdir() if f.is_file()]
    if any(f.endswith(".html") for f in files) or "index.html" in files:
        df_content = (
            "FROM nginx:1.27-alpine\n"
            "COPY . /usr/share/nginx/html/\n"
            "EXPOSE 80\n"
            'CMD ["nginx", "-g", "daemon off;"]\n'
        )
    elif "requirements.txt" in files or any(f.endswith(".py") for f in files):
        main_file = "main.py" if "main.py" in files else ("app.py" if "app.py" in files else files[0])
        df_content = (
            "FROM chaos-api:latest\n"
            "WORKDIR /app\n"
            "COPY . /app\n"
            f'CMD ["python", "{main_file}"]\n'
        )
    else:
        df_content = (
            "FROM nginx:1.27-alpine\n"
            "COPY . /usr/share/nginx/html/\n"
            "EXPOSE 80\n"
            'CMD ["nginx", "-g", "daemon off;"]\n'
        )
    (target_dir / "Dockerfile").write_text(df_content, encoding="utf-8")


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
            "current_stage": "Extracting Project Repository",
            "stages": [
                {"id": "extract", "name": "Extract Source", "status": "pending", "started_at": None, "ended_at": None},
                {"id": "build", "name": "Multi-Service Container Build", "status": "pending", "started_at": None, "ended_at": None},
                {"id": "deploy", "name": "Deploy & Network Services", "status": "pending", "started_at": None, "ended_at": None},
                {"id": "health", "name": "Service Readiness Checks", "status": "pending", "started_at": None, "ended_at": None},
                {"id": "chaos", "name": "Chaos Engine Resilience Test", "status": "pending", "started_at": None, "ended_at": None},
                {"id": "verdict", "name": "Resilience Verdict", "status": "pending", "started_at": None, "ended_at": None},
            ],
            "config": {
                "container_port": int(config.get("container_port", 80)),
                "health_path": str(config.get("health_path", "/")),
                "target_service": str(config.get("target_service", "")),
                "fault_engine": str(config.get("fault_engine", "pumba")),
                "fault_type": str(config.get("fault_type", "container-pause")),
                "fault_duration": int(config.get("fault_duration", 15)),
                "fault_params": config.get("fault_params", {}),
                "auto_cleanup": bool(config.get("auto_cleanup", True)),
                "max_error_rate": float(config.get("max_error_rate", 0.10)),
                "repo_url": str(config.get("repo_url", "")),
                "branch": str(config.get("branch", "")),
            },
            "services": [],
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
        self._log(state, "info", f"Pipeline {pipeline_id} initialized for project '{slug_name}'")
        return state

    def unpack_archive(self, pipeline_id: str, archive_bytes: bytes) -> Path:
        state = get_pipeline(self.s, pipeline_id)
        if not state:
            raise ValueError(f"Pipeline {pipeline_id} not found")

        work_dir = _get_pipeline_dir(self.s, pipeline_id) / "src"
        if work_dir.exists():
            shutil.rmtree(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)

        self._update_stage(state, "extract", "running")
        self._log(state, "info", f"Unpacking repository archive ({len(archive_bytes)} bytes)...")

        try:
            if zipfile.is_zipfile(io.BytesIO(archive_bytes)):
                with zipfile.ZipFile(io.BytesIO(archive_bytes)) as z:
                    for member in z.infolist():
                        target = (work_dir / member.filename).resolve()
                        if not str(target).startswith(str(work_dir.resolve())):
                            raise ValueError(f"Zip slip path traversal detected: {member.filename}")
                        z.extract(member, work_dir)
                self._log(state, "info", "Extracted ZIP archive into workspace.")
            elif tarfile.is_tarfile(io.BytesIO(archive_bytes)):
                with tarfile.open(fileobj=io.BytesIO(archive_bytes)) as t:
                    for member in t.getmembers():
                        target = (work_dir / member.name).resolve()
                        if not str(target).startswith(str(work_dir.resolve())):
                            raise ValueError(f"Tar slip path traversal detected: {member.name}")
                        t.extract(member, work_dir)
                self._log(state, "info", "Extracted TAR archive into workspace.")
            else:
                (work_dir / "index.html").write_bytes(archive_bytes)
                self._log(state, "info", "Extracted raw payload as index.html.")

            # If GitHub zip packed everything in a single root folder, flatten it
            subdirs = [p for p in work_dir.iterdir()]
            if len(subdirs) == 1 and subdirs[0].is_dir():
                root_sub = subdirs[0]
                for item in root_sub.iterdir():
                    shutil.move(str(item), str(work_dir / item.name))
                root_sub.rmdir()
                self._log(state, "info", f"Flattened repository directory '{root_sub.name}' to root.")

            # Discover all microservices in the repository
            discovered = discover_services(
                work_dir=work_dir,
                slug_name=state["project_name"],
                pipeline_id=pipeline_id,
                default_port=state["config"]["container_port"],
                default_health_path=state["config"]["health_path"],
                target_service_pref=state["config"].get("target_service", ""),
            )
            state["services"] = discovered

            # Point legacy single-container field to primary target service
            primary = next((s for s in discovered if s["primary"]), discovered[0])
            state["container"]["name"] = primary["container_name"]
            state["container"]["image"] = primary["image"]
            state["config"]["container_port"] = primary["port"]

            svc_names = [f"'{s['name']}' (port {s['port']})" for s in discovered]
            self._log(state, "info", f"Discovered {len(discovered)} service(s) to containerize: {', '.join(svc_names)}")
            self._log(state, "info", f"Primary chaos target service designated: '{primary['name']}'")

        except Exception as exc:
            self._update_stage(state, "extract", "failed")
            self._log(state, "error", f"Extraction & service discovery failed: {exc}")
            raise

        self._update_stage(state, "extract", "completed")
        _save_pipeline_state(self.s, state)
        return work_dir

    async def execute_pipeline(self, pipeline_id: str) -> dict[str, Any]:
        """Runs multi-service container builds, network deployment, and chaos tests."""
        state = get_pipeline(self.s, pipeline_id)
        if not state:
            raise ValueError(f"Pipeline {pipeline_id} not found")

        state["status"] = "RUNNING"
        _save_pipeline_state(self.s, state)

        import docker
        docker_client = docker.from_env()

        work_dir = _get_pipeline_dir(self.s, pipeline_id) / "src"
        services = state.get("services", [])
        if not services:
            services = discover_services(
                work_dir=work_dir,
                slug_name=state["project_name"],
                pipeline_id=pipeline_id,
                default_port=state["config"]["container_port"],
                default_health_path=state["config"]["health_path"],
            )
            state["services"] = services

        primary_svc = next((s for s in services if s.get("primary")), services[0])
        target_container = primary_svc["container_name"]
        target_port = primary_svc["port"]
        health_path = primary_svc.get("health_path", "/")

        fault_engine = state["config"]["fault_engine"]
        fault_type = state["config"]["fault_type"]
        fault_duration = state["config"]["fault_duration"]
        fault_params = state["config"]["fault_params"]
        auto_cleanup = state["config"]["auto_cleanup"]
        max_error_rate = state["config"]["max_error_rate"]

        deployed_containers: list[Any] = []
        proxy_name = None
        proxy_port = None
        service_name = f"pipe-{state['project_name']}-{pipeline_id[-4:]}"

        try:
            # ----------------------------------------------------
            # 1. BUILD CONTAINERS FOR EACH SERVICE
            # ----------------------------------------------------
            state["current_stage"] = "Building Service Containers"
            self._update_stage(state, "build", "running")

            for idx, s in enumerate(services, 1):
                s_name = s["name"]
                s_img = s["image"]
                s_ctx = work_dir / s.get("build_path", ".")
                s_df = s.get("dockerfile", "Dockerfile")

                if s.get("is_prebuilt"):
                    self._log(state, "info", f"[{idx}/{len(services)}] Service '{s_name}' uses pre-built base image '{s_img}'")
                    s["status"] = "built"
                    continue

                self._log(state, "info", f"[{idx}/{len(services)}] Building Docker container for service '{s_name}' (context: {s.get('build_path')}, tag: {s_img})...")

                def _build_single(path_dir: Path, df: str, tag: str):
                    return docker_client.images.build(path=str(path_dir), dockerfile=df, tag=tag, rm=True)

                t0 = time.monotonic()
                img, _ = await asyncio.to_thread(_build_single, s_ctx, s_df, s_img)
                dur = time.monotonic() - t0
                s["status"] = "built"
                self._log(state, "info", f"Built service '{s_name}' container in {dur:.2f}s! Image ID: {img.short_id}")

            self._update_stage(state, "build", "completed")

            # ----------------------------------------------------
            # 2. DEPLOY AND NETWORK ALL SERVICES
            # ----------------------------------------------------
            state["current_stage"] = "Deploying Multi-Service Topology"
            self._update_stage(state, "deploy", "running")

            # Ensure network exists
            try:
                chaos_net = docker_client.networks.get("chaos_app-net")
            except Exception:
                chaos_net = docker_client.networks.create("chaos_app-net", driver="bridge")

            for s in services:
                c_name = s["container_name"]
                s_img = s["image"]
                s_env = s.get("environment", {})

                # Remove any existing container with same name
                try:
                    old_c = docker_client.containers.get(c_name)
                    old_c.remove(force=True)
                except Exception:
                    pass

                restart_policy = {"Name": "always"} if fault_type in ("container-kill", "container-restart") else None

                def _deploy_single(img_tag: str, name: str, envs: dict, svc_alias: str):
                    c = docker_client.containers.create(
                        img_tag,
                        name=name,
                        environment=envs,
                        restart_policy=restart_policy,
                        labels={
                            "chaoslab.pipeline": pipeline_id,
                            "chaoslab.project": state["project_name"],
                            "chaoslab.service": svc_alias,
                        },
                    )
                    chaos_net.connect(c, aliases=[svc_alias, s.get("slug", svc_alias), name])
                    c.start()
                    return c

                cont = await asyncio.to_thread(_deploy_single, s_img, c_name, s_env, s["name"])
                deployed_containers.append(cont)
                s["status"] = "running"
                self._log(state, "info", f"Service '{s['name']}' running in container '{c_name}' on 'chaos_app-net' (alias: {s['name']})")

            state["container"]["status"] = "running"
            self._update_stage(state, "deploy", "completed")

            # ----------------------------------------------------
            # 3. SERVICE READINESS & HEALTH CHECKS
            # ----------------------------------------------------
            state["current_stage"] = "Verifying Services Readiness"
            self._update_stage(state, "health", "running")

            for s in services:
                if not s.get("port"):
                    continue
                s_probe_url = f"http://{s['container_name']}:{s['port']}{s.get('health_path', '/')}"
                self._log(state, "info", f"Verifying readiness for service '{s['name']}' at {s_probe_url}...")

                svc_healthy = False
                for attempt in range(1, 15):
                    await asyncio.sleep(1)
                    try:
                        async with httpx.AsyncClient(timeout=2.0) as client:
                            resp = await client.get(s_probe_url)
                            if resp.status_code == 200:
                                svc_healthy = True
                                self._log(state, "info", f"Service '{s['name']}' healthy! (HTTP 200 on attempt {attempt})")
                                break
                    except Exception:
                        pass

                if not svc_healthy and s["primary"]:
                    raise RuntimeError(f"Primary target service '{s['name']}' failed health checks at {s_probe_url}")

            self._update_stage(state, "health", "completed")

            # ----------------------------------------------------
            # 4. CONFIGURE TARGET & TOXIPROXY
            # ----------------------------------------------------
            state["current_stage"] = f"Configuring Chaos Injection on '{primary_svc['name']}'"
            target_proxies = []
            final_probe_url = f"http://{target_container}:{target_port}{health_path}"

            if fault_engine in ("toxiproxy",):
                global _NEXT_PROXY_PORT
                proxy_port = _NEXT_PROXY_PORT
                _NEXT_PROXY_PORT += 1
                proxy_name = f"pipe-{pipeline_id[-6:]}"
                state["container"]["proxy_port"] = proxy_port
                self._log(state, "info", f"Creating Toxiproxy proxy '{proxy_name}' for target '{primary_svc['name']}' on port {proxy_port}...")

                async with httpx.AsyncClient(timeout=3.0) as client:
                    tox_resp = await client.post(
                        "http://toxiproxy:8474/proxies",
                        json={
                            "name": proxy_name,
                            "listen": f"0.0.0.0:{proxy_port}",
                            "upstream": f"{target_container}:{target_port}",
                            "enabled": True,
                        },
                    )
                    if tox_resp.status_code not in (200, 201):
                        raise RuntimeError(f"Toxiproxy creation failed: {tox_resp.text}")

                target_proxies.append(proxy_name)
                final_probe_url = f"http://toxiproxy:{proxy_port}{health_path}"
                self._log(state, "info", f"Toxiproxy proxy active at {final_probe_url}")

            # Register in Settings environment
            self.s.environments.setdefault("docker-test", None)
            if self.s.environments.get("docker-test"):
                self.s.environments["docker-test"].services[service_name] = ServiceTarget(
                    containers=[target_container],
                    toxiproxy_proxies=target_proxies,
                )

            # Ensure host is allowed in probe hosts
            if target_container not in self.s.probe_allowed_hosts:
                self.s.probe_allowed_hosts.append(target_container)
            if "toxiproxy" not in self.s.probe_allowed_hosts:
                self.s.probe_allowed_hosts.append("toxiproxy")

            # ----------------------------------------------------
            # 5. EXECUTE CHAOS EXPERIMENT
            # ----------------------------------------------------
            state["current_stage"] = f"Injecting {fault_type} Chaos on '{primary_svc['name']}'"
            self._update_stage(state, "chaos", "running")
            self._log(
                state,
                "info",
                f"Injecting chaos fault '{fault_type}' ({fault_duration}s) via engine '{fault_engine}' into microservice '{primary_svc['name']}' (container: {target_container})...",
            )

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
                    "description": f"CI/CD multi-service resilience test for {state['project_name']} (target: {primary_svc['name']})",
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
                            "name": f"{primary_svc['name']}-http-probe",
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

            registry = EngineRegistry.from_settings(self.s)
            with session_scope() as db:
                exp_svc = ExperimentService(db, self.s, registry)
                exp = exp_svc.create(exp_doc, actor="ci-cd-pipeline")
                exp_id = exp.id
                state["experiment_id"] = exp_id
                _save_pipeline_state(self.s, state)

                exp_svc.approve(exp_id, actor="ci-cd-pipeline")
                run = exp_svc.start_run(exp_id, actor="ci-cd-pipeline", enqueue=enqueue_run)
                run_id = run.id
                state["experiment_run_id"] = run_id
                _save_pipeline_state(self.s, state)

            self._log(state, "info", f"Started Experiment Run: {run_id}. Observing multi-service steady state...")

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
                self._log(state, "warning", f"Experiment run timed out after {max_wait}s; assessing final status")

            self._update_stage(state, "chaos", "completed" if terminal else "failed")

            # ----------------------------------------------------
            # 6. EVALUATE VERDICT
            # ----------------------------------------------------
            state["current_stage"] = "Computing Resilience Verdict"
            self._update_stage(state, "verdict", "running")

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
                "target_service": primary_svc["name"],
                "total_services_deployed": len(services),
                "run_status": run_status,
                "run_outcome": outcome,
                "baseline_error_rate": baseline_err,
                "during_error_rate": during_err,
                "recovery_error_rate": recovery_err,
                "baseline_p95_ms": baseline_p95,
                "during_p95_ms": during_p95,
                "recovery_p95_ms": recovery_p95,
                "summary": (
                    f"Resilience test PASSED! Microservice '{primary_svc['name']}' sustained {fault_type} and recovered successfully within error tolerance."
                    if resilience_passed
                    else f"Resilience test FAILED: Target microservice '{primary_svc['name']}' encountered {during_err:.1%} errors during {fault_type} fault injection."
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
            # 7. MULTI-SERVICE TEARDOWN (IF ENABLED)
            # ----------------------------------------------------
            if auto_cleanup:
                self._log(state, "info", f"Auto-cleanup enabled: stopping and removing all {len(services)} service container(s)...")
                for c in deployed_containers:
                    try:
                        c.stop(timeout=2)
                        c.remove(force=True)
                        self._log(state, "info", f"Removed container '{c.name}'")
                    except Exception as e:
                        log.warning(f"Error removing container: {e}")

                for s in services:
                    s["status"] = "removed"
                    if not s.get("is_prebuilt"):
                        try:
                            docker_client.images.remove(s["image"], force=True)
                        except Exception:
                            pass

                state["container"]["status"] = "removed"

                if proxy_name:
                    try:
                        async with httpx.AsyncClient(timeout=2.0) as client:
                            await client.delete(f"http://toxiproxy:8474/proxies/{proxy_name}")
                        self._log(state, "info", f"Toxiproxy proxy '{proxy_name}' deleted.")
                    except Exception:
                        pass
            else:
                self._log(state, "info", f"Auto-cleanup disabled: {len(deployed_containers)} service container(s) remain active on 'chaos_app-net'.")

            state["updated_at"] = _utcnow_iso()
            _save_pipeline_state(self.s, state)

        return state

    def teardown_pipeline(self, pipeline_id: str) -> bool:
        """Manually stop and remove all service containers for a pipeline."""
        state = get_pipeline(self.s, pipeline_id)
        if not state:
            return False

        import docker
        docker_client = docker.from_env()

        services = state.get("services", [])
        if not services and state.get("container", {}).get("name"):
            services = [{"container_name": state["container"]["name"]}]

        for s in services:
            c_name = s.get("container_name")
            if not c_name:
                continue
            try:
                c = docker_client.containers.get(c_name)
                c.stop(timeout=2)
                c.remove(force=True)
                s["status"] = "removed"
                self._log(state, "info", f"Manually stopped and removed container '{c_name}'.")
            except Exception:
                pass

        state["container"]["status"] = "removed"

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
