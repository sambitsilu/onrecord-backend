"""Onrecord API."""
from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .config import get_settings
from .db import SessionLocal
from .jobs import fail_stale_runs
from .llm import LLM, AnthropicLLM
from .routers import clients, extras, orgs, public
from .transcription import Transcriber, default_transcriber

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("onrecord")


def create_app(llm_factory: Callable[[], LLM] | None = None,
               transcriber_factory: Callable[[], Transcriber | None] | None = None) -> FastAPI:
    settings = get_settings()
    settings.check_production_safety()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            db = SessionLocal()
            n = fail_stale_runs(db)
            db.close()
            if n:
                log.warning("marked %d unfinished extraction runs as failed", n)
        except Exception:  # noqa: BLE001 - don't block startup on this
            log.exception("could not check for unfinished runs")
        yield

    app = FastAPI(title="Onrecord API", version="1.0.0", lifespan=lifespan,
                  docs_url=None if settings.env == "production" else "/docs",
                  redoc_url=None, openapi_url=None if settings.env == "production" else "/openapi.json")
    app.state.llm_factory = llm_factory or (lambda: AnthropicLLM())
    app.state.llm_model = settings.llm_model
    app.state.transcriber_factory = transcriber_factory or default_transcriber

    if settings.origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.origins, allow_credentials=False,
                           allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cache-Control", "no-store")
        if settings.env == "production":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        d = exc.detail
        body = d if isinstance(d, dict) else {"message": str(d)}
        body.setdefault("code", {401: "unauthenticated", 403: "forbidden", 404: "not_found", 409: "conflict",
                                 413: "too_large", 422: "invalid"}.get(exc.status_code, "error"))
        return JSONResponse({"error": body}, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
        return JSONResponse({"error": {"code": "invalid", "message": "Some fields are invalid.", "details": details}}, status_code=422)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"error": {"code": "internal", "message": "Something went wrong. Try again."}}, status_code=500)

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"ok": True}

    @app.get("/readyz", include_in_schema=False)
    def readyz():
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
        return {"ok": True}

    app.include_router(orgs.router)
    app.include_router(clients.router)
    app.include_router(extras.router)
    app.include_router(public.router)
    return app


app = create_app()
