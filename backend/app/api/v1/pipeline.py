from __future__ import annotations

import base64
import io
import json
import zipfile
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request

from app.core.config import get_settings
from app.core.security import Principal, Role, require
from app.services.pipeline_service import PipelineService, get_pipeline, list_pipelines

router = APIRouter(prefix="/api/v1/pipeline", tags=["pipeline"])


def _create_sample_zip(sample_type: str) -> tuple[bytes, int, str]:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if sample_type == "python":
            main_code = (
                "from http.server import HTTPServer, BaseHTTPRequestHandler\n"
                "import os\n\n"
                "class Handler(BaseHTTPRequestHandler):\n"
                "    def do_GET(self):\n"
                "        self.send_response(200)\n"
                "        self.send_header('Content-Type', 'application/json')\n"
                "        self.end_headers()\n"
                "        self.wfile.write(b'{\"status\": \"healthy\", \"service\": \"python-microservice\"}')\n\n"
                "if __name__ == '__main__':\n"
                "    port = int(os.environ.get('PORT', 8000))\n"
                "    server = HTTPServer(('0.0.0.0', port), Handler)\n"
                "    print(f'Listening on port {port}...')\n"
                "    server.serve_forever()\n"
            )
            df_code = (
                "FROM chaos-api:latest\n"
                "WORKDIR /app\n"
                "COPY main.py /app/main.py\n"
                "ENV PORT=8000\n"
                "EXPOSE 8000\n"
                'CMD ["python", "main.py"]\n'
            )
            z.writestr("main.py", main_code)
            z.writestr("Dockerfile", df_code)
            return buf.getvalue(), 8000, "/"
        else:
            # Default Nginx static web app
            html_code = (
                "<!DOCTYPE html>\n"
                "<html>\n"
                "<head><title>ChaosLab Resilient Service</title></head>\n"
                "<body style='font-family:sans-serif;background:#0f172a;color:#f8fafc;padding:40px;'>\n"
                "  <h1>🚀 ChaosLab Target Service</h1>\n"
                "  <p>Status: <span style='color:#22c55e;font-weight:bold;'>HEALTHY</span></p>\n"
                "  <p>Automated Containerization &amp; Chaos Engineering Active</p>\n"
                "</body>\n"
                "</html>\n"
            )
            df_code = (
                "FROM nginx:1.27-alpine\n"
                "COPY index.html /usr/share/nginx/html/index.html\n"
                "EXPOSE 80\n"
                'CMD ["nginx", "-g", "daemon off;"]\n'
            )
            z.writestr("index.html", html_code)
            z.writestr("Dockerfile", df_code)
            return buf.getvalue(), 80, "/"


@router.post("/upload")
async def upload_and_run(
    request: Request,
    background_tasks: BackgroundTasks,
    project_name: str = Query("my-service"),
    container_port: int = Query(80),
    health_path: str = Query("/"),
    fault_engine: str = Query("pumba"),
    fault_type: str = Query("container-pause"),
    fault_duration: int = Query(15),
    fault_params: str = Query("{}"),
    auto_cleanup: bool = Query(True),
    max_error_rate: float = Query(0.10),
    _: Principal = Depends(require(Role.OPERATOR)),
):
    """Upload project archive (zip/tar or JSON base64) and launch the automated containerization + chaos test pipeline."""
    content_type = request.headers.get("content-type", "")
    body = await request.body()
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
        raise HTTPException(status_code=400, detail="Empty project payload received")

    config = {
        "container_port": container_port,
        "health_path": health_path,
        "fault_engine": fault_engine,
        "fault_type": fault_type,
        "fault_duration": fault_duration,
        "fault_params": parsed_params,
        "auto_cleanup": auto_cleanup,
        "max_error_rate": max_error_rate,
    }

    svc = PipelineService()
    state = svc.create_pipeline(project_name=project_name, config=config)
    pipeline_id = state["id"]

    # Unpack synchronously
    svc.unpack_archive(pipeline_id, archive_bytes)

    # Launch execution asynchronously in background task
    background_tasks.add_task(svc.execute_pipeline, pipeline_id)

    return {
        "pipeline_id": pipeline_id,
        "status": "QUEUED",
        "project_name": state["project_name"],
        "message": "Project unpacked and CI/CD Chaos Pipeline initiated",
    }


@router.post("/sample")
async def run_sample_pipeline(
    background_tasks: BackgroundTasks,
    sample_type: str = Query("nginx", regex="^(nginx|python)$"),
    fault_engine: str = Query("pumba"),
    fault_type: str = Query("container-pause"),
    fault_duration: int = Query(15),
    auto_cleanup: bool = Query(True),
    _: Principal = Depends(require(Role.OPERATOR)),
):
    """Launch an automated CI/CD pipeline run with a built-in sample microservice."""
    archive_bytes, port, path = _create_sample_zip(sample_type)

    config = {
        "container_port": port,
        "health_path": path,
        "fault_engine": fault_engine,
        "fault_type": fault_type,
        "fault_duration": fault_duration,
        "fault_params": {},
        "auto_cleanup": auto_cleanup,
        "max_error_rate": 0.10,
    }

    svc = PipelineService()
    state = svc.create_pipeline(project_name=f"sample-{sample_type}", config=config)
    pipeline_id = state["id"]

    svc.unpack_archive(pipeline_id, archive_bytes)
    background_tasks.add_task(svc.execute_pipeline, pipeline_id)

    return {
        "pipeline_id": pipeline_id,
        "status": "QUEUED",
        "project_name": state["project_name"],
        "message": f"Sample '{sample_type}' project CI/CD Chaos Pipeline initiated",
    }


@router.get("")
def list_pipeline_runs(_: Principal = Depends(require(Role.VIEWER))):
    """List all pipeline runs with current status and summary."""
    settings = get_settings()
    return list_pipelines(settings)


@router.get("/{pipeline_id}")
def get_pipeline_details(pipeline_id: str, _: Principal = Depends(require(Role.VIEWER))):
    """Retrieve real-time pipeline status, logs, stage progress, and chaos results."""
    settings = get_settings()
    pipeline = get_pipeline(settings, pipeline_id)
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    return pipeline


@router.post("/{pipeline_id}/teardown")
def teardown_pipeline_container(pipeline_id: str, _: Principal = Depends(require(Role.OPERATOR))):
    """Stop and remove test container and proxies for a pipeline."""
    svc = PipelineService()
    ok = svc.teardown_pipeline(pipeline_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    return {"pipeline_id": pipeline_id, "status": "teardown_completed"}
