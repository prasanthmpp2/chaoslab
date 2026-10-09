from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.api.v1 import engines, experiments, faults, health, pipeline, runs
from app.core.config import get_settings
from app.core.exceptions import PlatformError
from app.core.logging import configure_logging, get_logger
from app.core.security import Principal, Role, require

from fastapi.middleware.cors import CORSMiddleware

log = get_logger(__name__)


def create_app() -> FastAPI:
    configure_logging()
    s = get_settings()
    app = FastAPI(title="Chaos Engineering Platform", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def limit_body(request: Request, call_next):
        cl = request.headers.get("content-length")
        max_bytes = 52_428_800 if request.url.path.startswith("/api/v1/pipeline") else s.max_request_bytes
        if cl and cl.isdigit() and int(cl) > max_bytes:
            return JSONResponse({"error": {"code": "payload_too_large", "message": "request too large"}}, status_code=413)
        return await call_next(request)

    @app.exception_handler(PlatformError)
    async def platform_error(_: Request, exc: PlatformError):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
                            status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):
        log.exception("unhandled error", extra={"error_category": "internal"})
        return JSONResponse({"error": {"code": "internal_error", "message": "internal error"}}, status_code=500)

    app.include_router(health.router)
    for r in (engines.router, experiments.router, runs.router, faults.router, pipeline.router):
        app.include_router(r)

    from fastapi import Depends

    @app.get("/metrics", include_in_schema=False)
    def metrics(_: Principal = Depends(require(Role.VIEWER))) -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
