from __future__ import annotations

import base64
import asyncio
import io
import json
import tarfile
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from app.core.config import get_settings
from app.core.exceptions import Conflict
from app.core.security import Principal, Role, require
from app.workers.jobs import enqueue_pipeline, enqueue_pipeline_teardown
from app.services.pipeline_service import (
    PipelineService,
    _save_pipeline_state,
    download_github_repo,
    get_pipeline,
    list_pipelines,
)
from app.services.safety_service import list_retained_pipeline_targets

router = APIRouter(prefix="/api/v1/pipeline", tags=["pipeline"])
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def _start_pipeline(pipeline_id: str) -> dict[str, str]:
    settings = get_settings()
    pipeline = get_pipeline(settings, pipeline_id)
    if pipeline is None:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    if pipeline.get("status") == "QUEUED":
        return {"pipeline_id": pipeline_id, "status": "QUEUED"}
    if pipeline.get("status") != "PENDING_APPROVAL":
        raise Conflict("pipeline is not ready to start")

    pipeline["status"] = "QUEUED"
    pipeline["current_stage"] = "Pipeline queued for container build"
    pipeline["confirmed_by"] = pipeline.get("config", {}).get("requested_by") or "pipeline-user"
    pipeline["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_pipeline_state(settings, pipeline)
    try:
        enqueue_pipeline(pipeline_id)
    except Exception as exc:
        pipeline["status"] = "FAILED"
        pipeline["error"] = "Pipeline queue unavailable"
        _save_pipeline_state(settings, pipeline)
        raise HTTPException(status_code=503, detail="Pipeline queue unavailable") from exc
    return {"pipeline_id": pipeline_id, "status": "QUEUED"}


async def _read_upload_body(request: Request) -> bytes:
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Project archive exceeds 50 MiB")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/github")
async def run_github_pipeline(
    repo_url: str = Query(..., description="GitHub repository URL or owner/repo"),
    branch: str = Query("", description="Optional branch or ref (default: HEAD)"),
    github_token: str = Header("", alias="X-GitHub-Token", description="Optional GitHub access token"),
    project_name: str = Query("", description="Optional project name (defaults to repo name)"),
    target_service: str = Query("", description="Optional target microservice to inject faults into"),
    container_port: int = Query(80, description="Default service port if not specified in compose/dockerfile"),
    health_path: str = Query("/", description="Readiness health check path"),
    fault_engine: str = Query("pumba"),
    fault_type: str = Query("container-pause"),
    fault_duration: int = Query(15),
    auto_cleanup: bool = Query(True),
    max_error_rate: float = Query(0.10),
    principal: Principal = Depends(require(Role.OPERATOR)),
):
    """Clone a GitHub repository, discover and build containers for every microservice, and run chaos resilience tests."""
    try:
        archive_bytes, derived_repo = await asyncio.to_thread(
            download_github_repo, repo_url=repo_url, branch=branch, token=github_token
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    p_name = project_name.strip() if project_name.strip() else derived_repo

    config = {
        "container_port": container_port,
        "health_path": health_path,
        "target_service": target_service.strip(),
        "fault_engine": fault_engine,
        "fault_type": fault_type,
        "fault_duration": fault_duration,
        "fault_params": {},
        "auto_cleanup": auto_cleanup,
        "max_error_rate": max_error_rate,
        "repo_url": repo_url,
        "branch": branch,
        "requested_by": principal.user,
    }

    svc = PipelineService()
    state = svc.create_pipeline(project_name=p_name, config=config)
    pipeline_id = state["id"]

    # Unpack and discover services
    try:
        svc.unpack_archive(pipeline_id, archive_bytes)
    except (ValueError, zipfile.BadZipFile, tarfile.ReadError) as exc:
        raise HTTPException(status_code=400, detail="Invalid project archive") from exc

    _start_pipeline(pipeline_id)

    return {
        "pipeline_id": pipeline_id,
        "status": "QUEUED",
        "project_name": state["project_name"],
        "services_count": len(state.get("services", [])),
        "message": f"Cloned GitHub repository '{derived_repo}' and initiated multi-service CI/CD Chaos Pipeline",
    }


@router.post("/upload")
async def upload_and_run(
    request: Request,
    project_name: str = Query("my-service"),
    target_service: str = Query(""),
    container_port: int = Query(80),
    health_path: str = Query("/"),
    fault_engine: str = Query("pumba"),
    fault_type: str = Query("container-pause"),
    fault_duration: int = Query(15),
    fault_params: str = Query("{}"),
    auto_cleanup: bool = Query(True),
    max_error_rate: float = Query(0.10),
    principal: Principal = Depends(require(Role.OPERATOR)),
):
    """Upload project repository archive (zip/tar) and launch multi-service containerization + chaos test pipeline."""
    content_type = request.headers.get("content-type", "")
    body = await _read_upload_body(request)
    parsed_params = {}
    try:
        parsed_params = json.loads(fault_params) if fault_params else {}
    except Exception:
        pass

    archive_bytes = b""
    if "application/json" in content_type:
        try:
            payload = json.loads(body.decode("utf-8"))
            project_name = payload.get("project_name", project_name)
            target_service = payload.get("target_service", target_service)
            container_port = payload.get("container_port", container_port)
            health_path = payload.get("health_path", health_path)
            fault_engine = payload.get("fault_engine", fault_engine)
            fault_type = payload.get("fault_type", fault_type)
            fault_duration = payload.get("fault_duration", fault_duration)
            auto_cleanup = payload.get("auto_cleanup", auto_cleanup)
            max_error_rate = payload.get("max_error_rate", max_error_rate)
            if "fault_params" in payload:
                parsed_params = payload["fault_params"]

            if "archive_b64" in payload:
                archive_bytes = base64.b64decode(payload["archive_b64"])
            elif "files" in payload:
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                    for fname, fcontent in payload["files"].items():
                        z.writestr(fname, fcontent)
                archive_bytes = buf.getvalue()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {e}")
    else:
        # Raw binary archive
        archive_bytes = body

    if not archive_bytes:
        raise HTTPException(status_code=400, detail="Empty project archive payload received")
    if len(archive_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Project archive exceeds 50 MiB")

    config = {
        "container_port": container_port,
        "health_path": health_path,
        "target_service": target_service.strip(),
        "fault_engine": fault_engine,
        "fault_type": fault_type,
        "fault_duration": fault_duration,
        "fault_params": parsed_params,
        "auto_cleanup": auto_cleanup,
        "max_error_rate": max_error_rate,
        "requested_by": principal.user,
    }

    svc = PipelineService()
    state = svc.create_pipeline(project_name=project_name, config=config)
    pipeline_id = state["id"]

    try:
        svc.unpack_archive(pipeline_id, archive_bytes)
    except (ValueError, zipfile.BadZipFile, tarfile.ReadError) as exc:
        raise HTTPException(status_code=400, detail="Invalid project archive") from exc

    _start_pipeline(pipeline_id)

    return {
        "pipeline_id": pipeline_id,
        "status": "QUEUED",
        "project_name": state["project_name"],
        "services_count": len(state.get("services", [])),
        "message": f"Project unpacked ({len(state.get('services', []))} services detected) and CI/CD Chaos Pipeline initiated",
    }


@router.post("/{pipeline_id}/start", status_code=status.HTTP_202_ACCEPTED)
def start_pipeline(pipeline_id: str, _: Principal = Depends(require(Role.OPERATOR))):
    """Start a prepared pipeline after the user confirms in the frontend."""
    return _start_pipeline(pipeline_id)


@router.post("/{pipeline_id}/approve", status_code=status.HTTP_202_ACCEPTED, include_in_schema=False)
def approve_pipeline_legacy(pipeline_id: str, _: Principal = Depends(require(Role.OPERATOR))):
    """Compatibility route for clients that still call the former approval endpoint."""
    return _start_pipeline(pipeline_id)


@router.get("")
def list_pipeline_runs(_: Principal = Depends(require(Role.VIEWER))):
    """List all pipeline runs with current status and summary."""
    settings = get_settings()
    return list_pipelines(settings)


@router.get("/retained-targets")
def retained_pipeline_targets(_: Principal = Depends(require(Role.VIEWER))):
    """Return pipeline services configured to remain available after their run."""
    return list_retained_pipeline_targets(get_settings())


@router.get("/{pipeline_id}")
def get_pipeline_details(pipeline_id: str, _: Principal = Depends(require(Role.VIEWER))):
    """Retrieve real-time pipeline status, multi-service topology, logs, and chaos results."""
    settings = get_settings()
    pipeline = get_pipeline(settings, pipeline_id)
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    return pipeline


@router.post("/{pipeline_id}/teardown", status_code=status.HTTP_202_ACCEPTED)
def teardown_pipeline_container(pipeline_id: str, _: Principal = Depends(require(Role.OPERATOR))):
    """Queue cleanup of test containers and proxies for a pipeline."""
    settings = get_settings()
    pipeline = get_pipeline(settings, pipeline_id)
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    if pipeline.get("status") == "TEARING_DOWN":
        return {"pipeline_id": pipeline_id, "status": "teardown_queued"}
    pipeline["status"] = "TEARING_DOWN"
    pipeline["current_stage"] = "Pipeline teardown queued"
    pipeline["updated_at"] = datetime.now(timezone.utc).isoformat()
    from app.services.pipeline_service import _save_pipeline_state

    _save_pipeline_state(settings, pipeline)
    try:
        enqueue_pipeline_teardown(pipeline_id)
    except Exception as exc:
        pipeline["status"] = "TEARDOWN_FAILED"
        pipeline["error"] = "Pipeline queue unavailable"
        _save_pipeline_state(settings, pipeline)
        raise HTTPException(status_code=503, detail="Pipeline queue unavailable") from exc
    return {"pipeline_id": pipeline_id, "status": "teardown_queued"}
