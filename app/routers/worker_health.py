"""Worker health check and manual trigger endpoints (admin-only)."""

from fastapi import APIRouter, Depends

from app.middleware.auth import get_admin_user
from app.models.user import User

router = APIRouter(prefix="/workers", tags=["Workers"])


@router.get("/health")
async def worker_health(
    current_user: User = Depends(get_admin_user),
):
    """Check Celery worker and Redis broker health."""
    from app.worker.health import get_worker_health

    return get_worker_health()


@router.post("/trigger-analysis")
async def trigger_analysis(
    current_user: User = Depends(get_admin_user),
):
    """Manually trigger the analysis pipeline outside the Beat schedule."""
    from app.worker.tasks import run_analysis_pipeline

    result = run_analysis_pipeline.delay()

    return {
        "message": "Analysis pipeline triggered",
        "task_id": result.id,
    }
