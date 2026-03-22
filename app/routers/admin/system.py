"""Admin system router -- health, pipeline status, logs, and task management."""

import os
import subprocess
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.middleware.audit import log_audit
from app.middleware.permissions import RequireAdmin, UserPermissions
from app.models.admin_config import AdminConfig
from app.models.enums import ExecutionStatus
from app.models.execution import Execution
from app.models.signal import Signal
from app.models.strategy import Strategy
from app.utils.helpers import utc_now

router = APIRouter()


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    database: str
    timestamp: datetime
    version: str
    uptime_info: dict


class PipelineStatusResponse(BaseModel):
    last_signal_at: Optional[datetime] = None
    last_execution_at: Optional[datetime] = None
    total_signals_today: int
    total_executions_today: int
    active_strategies: int
    pending_executions: int


class LogsResponse(BaseModel):
    service: str
    lines: list[str]
    total_lines: int


class TaskInfo(BaseModel):
    name: str
    task: str
    schedule: str
    queue: str
    enabled: bool
    description: str


class TaskToggleRequest(BaseModel):
    enabled: bool


# ---------------------------------------------------------------------------
# Log file paths (match deploy/*.service logfile settings)
# ---------------------------------------------------------------------------

LOG_PATHS = {
    "api": "/var/log/eulerx/error.log",
    "api-access": "/var/log/eulerx/access.log",
    "worker": "/var/log/eulerx/celery-worker.log",
    "beat": "/var/log/eulerx/celery-beat.log",
}

# Task definitions matching celery_app.py beat_schedule
TASK_DEFINITIONS = [
    {
        "name": "analysis-pipeline",
        "task": "app.worker.tasks.run_analysis_pipeline",
        "schedule": "Every 2 hours",
        "queue": "analysis",
        "description": "Fetch market data, generate AI signals, execute across strategies",
    },
    {
        "name": "monitor-positions",
        "task": "app.worker.tasks.monitor_open_positions",
        "schedule": "Every 1 minute",
        "queue": "execution",
        "description": "Check open positions for TP/SL hits and reconcile with exchanges",
    },
    {
        "name": "expire-stale-signals",
        "task": "app.worker.tasks.expire_stale_signals",
        "schedule": "Every 30 minutes",
        "queue": "maintenance",
        "description": "Mark expired signals that passed their expiry timestamp",
    },
    {
        "name": "check-expiring-subscriptions",
        "task": "app.worker.tasks.check_expiring_subscriptions",
        "schedule": "Daily at 9:00 AM UTC",
        "queue": "maintenance",
        "description": "Notify users whose subscriptions expire within 3 days",
    },
    {
        "name": "cleanup-old-data",
        "task": "app.worker.tasks.cleanup_old_data",
        "schedule": "Weekly (Sunday 3:00 AM UTC)",
        "queue": "maintenance",
        "description": "Remove expired signals older than 90 days",
    },
]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/system/health", response_model=HealthResponse)
async def system_health(db: AsyncSession = Depends(get_db)):
    """Return basic system health information."""
    now = utc_now()

    try:
        await db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db_status = "error"

    return HealthResponse(
        database=db_status,
        timestamp=now,
        version="1.0.0",
        uptime_info={"server_started": now.isoformat()},
    )


@router.get("/system/pipeline", response_model=PipelineStatusResponse)
async def pipeline_status(db: AsyncSession = Depends(get_db)):
    """Return current pipeline run status and daily counters."""
    now = utc_now()

    last_signal_at = (
        await db.execute(
            select(Signal.created_at).order_by(Signal.created_at.desc()).limit(1)
        )
    ).scalar()

    last_execution_at = (
        await db.execute(
            select(Execution.created_at).order_by(Execution.created_at.desc()).limit(1)
        )
    ).scalar()

    total_signals_today = (
        await db.execute(
            select(func.count(Signal.id)).where(
                func.date(Signal.created_at) == func.date(now)
            )
        )
    ).scalar() or 0

    total_executions_today = (
        await db.execute(
            select(func.count(Execution.id)).where(
                func.date(Execution.created_at) == func.date(now)
            )
        )
    ).scalar() or 0

    active_strategies = (
        await db.execute(
            select(func.count(Strategy.id)).where(
                Strategy.is_active == True  # noqa: E712
            )
        )
    ).scalar() or 0

    pending_executions = (
        await db.execute(
            select(func.count(Execution.id)).where(
                Execution.status == ExecutionStatus.PENDING
            )
        )
    ).scalar() or 0

    return PipelineStatusResponse(
        last_signal_at=last_signal_at,
        last_execution_at=last_execution_at,
        total_signals_today=total_signals_today,
        total_executions_today=total_executions_today,
        active_strategies=active_strategies,
        pending_executions=pending_executions,
    )


# ---------------------------------------------------------------------------
# Log streaming
# ---------------------------------------------------------------------------


@router.get("/system/logs/{service}", response_model=LogsResponse)
async def get_service_logs(
    service: str,
    lines: int = Query(100, ge=10, le=1000),
):
    """Read the last N lines from a service log file.

    Available services: api, api-access, worker, beat.
    Falls back to journalctl if log file doesn't exist.
    """
    if service not in LOG_PATHS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown service. Choose from: {', '.join(LOG_PATHS.keys())}",
        )

    log_path = LOG_PATHS[service]
    log_lines: list[str] = []

    # Try reading the log file directly
    if os.path.exists(log_path):
        try:
            result = subprocess.run(
                ["tail", "-n", str(lines), log_path],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                log_lines = result.stdout.strip().split("\n")
        except Exception:
            pass

    # Fallback: try journalctl for systemd service
    if not log_lines:
        service_map = {
            "api": "eulerx-api",
            "api-access": "eulerx-api",
            "worker": "eulerx-celery",
            "beat": "eulerx-beat",
        }
        systemd_name = service_map.get(service, "")
        if systemd_name:
            try:
                result = subprocess.run(
                    [
                        "journalctl",
                        "-u",
                        systemd_name,
                        "-n",
                        str(lines),
                        "--no-pager",
                        "--output=short-iso",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0 and result.stdout.strip():
                    log_lines = result.stdout.strip().split("\n")
            except Exception:
                pass

    if not log_lines:
        log_lines = [f"No logs available for {service}"]

    return LogsResponse(
        service=service,
        lines=log_lines,
        total_lines=len(log_lines),
    )


# ---------------------------------------------------------------------------
# Task management
# ---------------------------------------------------------------------------


@router.get("/system/tasks", response_model=list[TaskInfo])
async def list_tasks(db: AsyncSession = Depends(get_db)):
    """List all scheduled tasks with their enabled/disabled status."""
    # Load disabled tasks from admin_config
    result = await db.execute(
        select(AdminConfig).where(AdminConfig.key == "disabled_tasks")
    )
    config = result.scalar_one_or_none()
    disabled_tasks: list[str] = config.value.get("tasks", []) if config else []

    tasks = []
    for td in TASK_DEFINITIONS:
        tasks.append(
            TaskInfo(
                name=td["name"],
                task=td["task"],
                schedule=td["schedule"],
                queue=td["queue"],
                enabled=td["name"] not in disabled_tasks,
                description=td["description"],
            )
        )
    return tasks


@router.put("/system/tasks/{task_name}")
async def toggle_task(
    task_name: str,
    body: TaskToggleRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    """Enable or disable a scheduled task.

    Disabled tasks are stored in admin_config. The pipeline checks
    this config before executing tasks.
    """
    valid_names = {td["name"] for td in TASK_DEFINITIONS}
    if task_name not in valid_names:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task '{task_name}' not found",
        )

    # Load or create the disabled_tasks config
    result = await db.execute(
        select(AdminConfig).where(AdminConfig.key == "disabled_tasks")
    )
    config = result.scalar_one_or_none()

    if config is None:
        config = AdminConfig(
            key="disabled_tasks",
            value={"tasks": []},
            description="List of disabled scheduled task names",
        )
        db.add(config)

    disabled: list[str] = config.value.get("tasks", [])

    if body.enabled and task_name in disabled:
        disabled.remove(task_name)
    elif not body.enabled and task_name not in disabled:
        disabled.append(task_name)

    # Must reassign to trigger SQLAlchemy dirty tracking on JSON column
    config.value = {"tasks": disabled}

    await log_audit(
        db=db,
        user_id=admin_perms.user.id,
        action="toggle_task",
        resource_type="system",
        resource_id=task_name,
        details={"enabled": body.enabled},
        ip_address=request.client.host if request.client else None,
    )

    await db.flush()

    return {
        "task": task_name,
        "enabled": body.enabled,
        "message": f"Task '{task_name}' {'enabled' if body.enabled else 'disabled'}",
    }


@router.get("/system/services")
async def service_status():
    """Check systemd service status for API, Worker, and Beat."""
    services = {
        "api": "eulerx-api",
        "worker": "eulerx-celery",
        "beat": "eulerx-beat",
        "redis": "redis-server",
    }

    statuses = {}
    for label, svc in services.items():
        try:
            result = subprocess.run(
                ["systemctl", "is-active", svc],
                capture_output=True,
                text=True,
                timeout=5,
            )
            statuses[label] = result.stdout.strip()
        except Exception:
            statuses[label] = "unknown"

    return statuses
