"""FastAPI application.

The in-process worker is started on application startup unless
``AGENTBENCH_WORKER_IN_API_PROCESS=false``, in which case runs are executed by
``agentbench worker`` elsewhere.  Both modes share the same lease table, so
mixing them is safe.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from agentbench import __version__
from agentbench.adapters.base import AgentConfigError
from agentbench.api.routers import agents, experiments, metrics, runs, system, tasks
from agentbench.config import get_settings
from agentbench.db.session import create_all
from agentbench.logging_setup import get_logger, setup_logging
from agentbench.orchestrator.worker import get_worker, shutdown_worker
from agentbench.registry.loader import BenchmarkLoadError
from agentbench.sandbox.base import SandboxError

log = get_logger(__name__)

DESCRIPTION = """
Reproducible evaluation, benchmarking and failure analysis for AI coding agents.

**Reading the numbers.** Every rate in this API carries its numerator and
denominator. `success_rate_attributable` counts timeouts and agent errors as
failures; `success_rate_graded` counts only runs that reached a verdict.
Infrastructure failures, broken task environments and cancellations are excluded
from both and reported separately, because they say nothing about agent
capability.

**Simulated agents.** Results produced by a deterministic, non-model adapter are
flagged `is_simulated`. They validate the evaluation pipeline and must not be
quoted as model benchmark results.

**Security.** This service executes untrusted code in containers. Set
`AGENTBENCH_API_TOKEN` for any deployment that is not loopback-only.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_format, settings.secret_values())
    create_all()

    worker = None
    if settings.worker_in_api_process:
        worker = get_worker()
        worker.start()
        log.info("api.worker_started", extra={"worker_id": worker.worker_id})
    else:
        log.info("api.worker_external", extra={"note": "run `agentbench worker` separately"})

    app.state.worker = worker
    try:
        yield
    finally:
        if worker is not None:
            shutdown_worker()


app = FastAPI(
    title="AgentBench Lab",
    version=__version__,
    description=DESCRIPTION,
    lifespan=lifespan,
    openapi_tags=[
        {"name": "system", "description": "Health, settings and capabilities."},
        {"name": "benchmarks", "description": "Task registry, validation, import and export."},
        {"name": "agents", "description": "Agent adapters and configurations."},
        {"name": "experiments", "description": "Create, launch, cancel, resume and report."},
        {"name": "runs", "description": "Run detail, event timeline, patch and grading."},
        {"name": "metrics", "description": "Overview, comparison and failure analysis."},
    ],
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

api_prefix = "/api/v1"
app.include_router(system.router, prefix=api_prefix)
app.include_router(tasks.router, prefix=api_prefix)
app.include_router(agents.router, prefix=api_prefix)
app.include_router(experiments.router, prefix=api_prefix)
app.include_router(runs.router, prefix=api_prefix)
app.include_router(metrics.router, prefix=api_prefix)


def _error(status_code: int, code: str, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, **extra}},
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    return _error(exc.status_code, f"http_{exc.status_code}", str(exc.detail))


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return _error(
        422,
        "validation_error",
        "the request body or query parameters are invalid",
        fields=[
            {
                "location": ".".join(str(part) for part in error.get("loc", [])),
                "message": error.get("msg"),
                "type": error.get("type"),
            }
            for error in exc.errors()
        ],
    )


@app.exception_handler(BenchmarkLoadError)
async def benchmark_error_handler(_: Request, exc: BenchmarkLoadError) -> JSONResponse:
    return _error(400, "benchmark_invalid", str(exc), errors=exc.errors)


@app.exception_handler(AgentConfigError)
async def agent_config_error_handler(_: Request, exc: AgentConfigError) -> JSONResponse:
    return _error(400, "agent_config_invalid", str(exc))


@app.exception_handler(SandboxError)
async def sandbox_error_handler(_: Request, exc: SandboxError) -> JSONResponse:
    return _error(503, "sandbox_unavailable", str(exc))


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Never leak internals.

    The message and traceback go to the structured log (which is redacted); the
    client gets a stable error code only, so host paths and configuration are
    not exposed through the API.
    """
    log.exception(
        "api.unhandled_exception",
        extra={"path": request.url.path, "method": request.method, "error": str(exc)},
    )
    return _error(
        500,
        "internal_error",
        "an unexpected error occurred; see the server log for details",
    )


@app.get("/", include_in_schema=False)
def root() -> dict[str, Any]:
    return {
        "name": "AgentBench Lab",
        "version": __version__,
        "api": api_prefix,
        "docs": "/docs",
        "openapi": "/openapi.json",
    }
