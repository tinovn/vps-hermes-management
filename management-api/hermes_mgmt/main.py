from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from hermes_mgmt import __version__
from hermes_mgmt.config import get_settings
from hermes_mgmt.deps import get_rate_limiter
from hermes_mgmt.models import ApiResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class StripServerHeaderMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        # MutableHeaders does not have pop(); use __delitem__ guarded by presence check
        if "server" in response.headers:
            del response.headers["server"]
        if "Server" in response.headers:
            del response.headers["Server"]
        return response


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        key_preview = settings.mgmt_api_key[:8] if len(settings.mgmt_api_key) >= 8 else "***"
        logger.info(
            "Hermes Management API v%s starting on port %d (API key prefix: %s...)",
            __version__,
            settings.mgmt_port,
            key_preview,
        )
        yield
        get_rate_limiter().cleanup()
        logger.info("Hermes Management API shut down.")

    app = FastAPI(
        title="Hermes Management API",
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(StripServerHeaderMiddleware)

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception on %s", request.url)
        return JSONResponse(
            status_code=500,
            content=ApiResponse(ok=False, error="Internal server error").model_dump(),
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=ApiResponse(ok=False, error=str(exc.detail)).model_dump(),
            headers=getattr(exc, "headers", None),
        )

    @app.get("/health", tags=["meta"])
    async def health() -> dict:
        return {"ok": True, "version": __version__}

    # Register all routers
    from hermes_mgmt.routes import all_routers

    for router in all_routers:
        app.include_router(router)

    return app


app = create_app()
