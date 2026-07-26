"""FastAPI application factory.

Framework choice, briefly, since the brief asked for it to be justified rather
than inherited: FastAPI gives typed request validation and a Pydantic model
layer that the normalized result schema already needs for its JSON contract,
and its TestClient makes route tests ordinary function calls. Pages are
server-rendered Jinja2 with no build step and no client framework — this is a
results table, not an application. Legacy EnzymeX runs on Pyramid; that is not
a reason to pick Pyramid here, and it costs nothing later because everything
under `app/search` and `app/references` is framework-free.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.web.app_paths import STATIC_DIR
from app.web.routes import router, templates

log = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    app = FastAPI(
        title="EnzymeX BLAST/HMMER test server",
        description="Standalone test environment. Not the EnzymeX production service.",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.middleware("http")
    async def limit_body_size(request: Request, call_next):
        """Reject an oversized upload before it is read into memory."""
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > settings.max_upload_bytes:
            return PlainTextResponse(
                f"Submission exceeds the {settings.max_upload_bytes // 1000} kB limit.",
                status_code=413,
            )
        return await call_next(request)

    @app.exception_handler(500)
    async def internal_error(request: Request, exc: Exception):
        # The traceback goes to the log; the user gets a job-free apology with
        # no path, stack frame or configuration detail in it.
        log.exception("unhandled error on %s", request.url.path)
        if request.url.path.startswith("/api") or request.url.path.endswith(".json"):
            return JSONResponse({"error": "internal server error"}, status_code=500)
        return templates.TemplateResponse(
            request, "error.html",
            {"title": "Something went wrong",
             "message": "The server hit an unexpected error. Nothing was saved."},
            status_code=500,
        )

    log.info("reference directory: %s", settings.reference_dir)
    log.info("job directory: %s", settings.job_dir)
    return app


app = create_app()
