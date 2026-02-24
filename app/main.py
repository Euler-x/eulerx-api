import logging
import logging.config
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings
from app.db.init_db import check_db_connection, dispose_engine
from app.routers import (
    admin,
    ambassador,
    auth,
    billing,
    execution,
    learning,
    market,
    signals,
    strategies,
    support,
    telegram,
    transactions,
    worker_health,
)
from app.utils.rate_limiter import rate_limit_dependency

settings = get_settings()

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

if settings.debug:
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
else:
    # Structured JSON-like format for production log aggregation
    logging.basicConfig(
        level=logging.INFO,
        format=(
            '{"time": "%(asctime)s", "logger": "%(name)s", '
            '"level": "%(levelname)s", "message": "%(message)s"}'
        ),
    )

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sentry integration
# ---------------------------------------------------------------------------

if settings.sentry_dsn:
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.environment,
            traces_sample_rate=1.0 if settings.debug else 0.2,
        )
        logger.info("Sentry initialized (environment=%s)", settings.environment)
    except ImportError:
        logger.warning("sentry-sdk not installed, skipping Sentry initialization")


# ---------------------------------------------------------------------------
# Request correlation-ID middleware
# ---------------------------------------------------------------------------

class CorrelationIDMiddleware(BaseHTTPMiddleware):
    """Attach a unique X-Request-ID to every request/response pair."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        # Make the ID available to downstream code via request.state
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting EulerX Network Backend...")
    # Hard failure: if DB is unreachable the process must not start.
    await check_db_connection()
    logger.info("Database connection verified")

    # Start the Hyperliquid market data WebSocket stream
    from app.services.market_ws import market_data_manager
    await market_data_manager.start()

    yield

    # Shutdown
    logger.info("Shutting down EulerX Network Backend...")
    await market_data_manager.stop()
    await dispose_engine()


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="AI-driven non-custodial automated trading infrastructure",
    lifespan=lifespan,
    # Hide interactive API docs outside of debug/development mode
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
    openapi_url="/openapi.json" if settings.debug else None,
)

# ---------------------------------------------------------------------------
# Middleware (order matters — added last runs outermost)
# ---------------------------------------------------------------------------

# Correlation-ID middleware
app.add_middleware(CorrelationIDMiddleware)

# Hardened CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)

# ---------------------------------------------------------------------------
# Global exception handlers
# ---------------------------------------------------------------------------

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    request_id = getattr(request.state, "request_id", None)
    logger.warning(
        "HTTPException status=%s detail=%s request_id=%s path=%s",
        exc.status_code,
        exc.detail,
        request_id,
        request.url.path,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.detail,
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id} if request_id else {},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    request_id = getattr(request.state, "request_id", None)
    logger.warning(
        "RequestValidationError errors=%s request_id=%s path=%s",
        exc.errors(),
        request_id,
        request.url.path,
    )
    return JSONResponse(
        status_code=422,
        content={
            "detail": exc.errors(),
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id} if request_id else {},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", None)
    logger.exception(
        "Unhandled exception request_id=%s path=%s",
        request_id,
        request.url.path,
        exc_info=exc,
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id} if request_id else {},
    )


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

prefix = settings.api_v1_prefix

# Auth router gets rate limiting as a global dependency
app.include_router(
    auth.router,
    prefix=prefix,
    dependencies=[Depends(rate_limit_dependency)],
)

app.include_router(strategies.router, prefix=prefix)
app.include_router(signals.router, prefix=prefix)
app.include_router(execution.router, prefix=prefix)
app.include_router(market.router, prefix=prefix)
app.include_router(transactions.router, prefix=prefix)
app.include_router(billing.router, prefix=prefix)
app.include_router(ambassador.router, prefix=prefix)
app.include_router(admin.router, prefix=prefix)
app.include_router(support.router, prefix=prefix)
app.include_router(telegram.router, prefix=prefix)
app.include_router(learning.router, prefix=prefix)
app.include_router(worker_health.router, prefix=prefix)


# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

try:
    from prometheus_fastapi_instrumentator import Instrumentator
    Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        excluded_handlers=["/health", "/metrics"],
    ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
    logger.info("Prometheus metrics enabled at /metrics")
except ImportError:
    logger.info("prometheus-fastapi-instrumentator not installed, metrics disabled")


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

@app.get("/health")
async def health_check():
    db_ok = True
    try:
        await check_db_connection()
    except Exception:
        db_ok = False

    redis_ok = True
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(settings.redis_url, socket_connect_timeout=2)
        await r.ping()
        await r.aclose()
    except Exception:
        redis_ok = False

    return {
        "status": "ok",
        "version": settings.app_version,
        "db_status": "ok" if db_ok else "unavailable",
        "redis_status": "ok" if redis_ok else "unavailable",
    }
