"""Worker health check and manual trigger endpoints (admin-only)."""

from fastapi import APIRouter, HTTPException, Query, status

from app.middleware.permissions import RequireAdmin, UserPermissions

router = APIRouter(prefix="/workers", tags=["Workers"])


@router.get("/health")
async def worker_health(
    perms: UserPermissions = RequireAdmin,
):
    """Check Celery worker and Redis broker health."""
    from app.worker.health import get_worker_health

    return get_worker_health()


@router.post("/trigger-analysis")
async def trigger_analysis(
    exchange: str = Query(default="all", pattern="^(all|hyperliquid|bybit|binance)$"),
    perms: UserPermissions = RequireAdmin,
):
    """Manually trigger one exchange pipeline, or all exchange pipelines."""
    from app.worker.tasks import (
        run_analysis_pipeline,
        run_binance_analysis_pipeline,
        run_bybit_analysis_pipeline,
        run_hyperliquid_analysis_pipeline,
    )

    task_map = {
        "all": run_analysis_pipeline,
        "hyperliquid": run_hyperliquid_analysis_pipeline,
        "bybit": run_bybit_analysis_pipeline,
        "binance": run_binance_analysis_pipeline,
    }
    task = task_map.get(exchange)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported exchange: {exchange}",
        )
    result = task.delay()

    return {
        "message": f"{exchange} analysis pipeline triggered",
        "task_id": result.id,
    }
