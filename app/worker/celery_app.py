"""Celery application configuration for EulerX.

Provides the Celery app instance with enterprise-grade configuration:
- Redis broker and result backend
- 5 dedicated queues (analysis, signals, execution, maintenance, notifications)
- Beat schedule for periodic pipeline runs
- Signal handlers for monitoring and logging
"""

import logging

from celery import Celery
from celery.schedules import crontab
from celery.signals import (
    after_setup_logger,
    task_failure,
    task_postrun,
    task_prerun,
    task_retry,
    worker_ready,
    worker_shutting_down,
)

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# ── Celery App ───────────────────────────────────────────────────

celery_app = Celery("eulerx")

celery_app.conf.update(
    # Broker & Backend
    broker_url=settings.redis_url,
    result_backend=settings.redis_url,
    broker_connection_retry_on_startup=True,
    # Serialization
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    # Time & timezone
    timezone="UTC",
    enable_utc=True,
    # Task execution
    task_always_eager=settings.celery_task_always_eager,
    task_time_limit=settings.celery_task_time_limit,
    task_soft_time_limit=settings.celery_task_soft_time_limit,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # Result management
    result_expires=3600,
    result_extended=True,
    # Retry & reliability
    task_reject_on_worker_lost=True,
    task_default_retry_delay=settings.analysis_retry_backoff,
    task_max_retries=settings.analysis_max_retries,
    # Queue routing
    task_routes={
        "app.worker.tasks.run_analysis_pipeline": {"queue": "analysis"},
        "app.worker.tasks.fetch_market_data": {"queue": "analysis"},
        "app.worker.tasks.generate_signals": {"queue": "signals"},
        "app.worker.tasks.execute_signal_task": {"queue": "execution"},
        "app.worker.tasks.expire_stale_signals": {"queue": "maintenance"},
        "app.worker.tasks.send_notification_email": {"queue": "notifications"},
        "app.worker.tasks.send_notification_telegram": {"queue": "notifications"},
        "app.worker.tasks.check_expiring_subscriptions": {"queue": "maintenance"},
        "app.worker.tasks.monitor_open_positions": {"queue": "execution"},
        "app.worker.tasks.cleanup_old_data": {"queue": "maintenance"},
        "app.worker.tasks.capture_portfolio_snapshots": {"queue": "maintenance"},
    },
    # Worker lifecycle
    worker_max_tasks_per_child=50,
    worker_max_memory_per_child=512_000,
    # Beat schedule
    beat_schedule={
        "analysis-pipeline-every-2h": {
            "task": "app.worker.tasks.run_analysis_pipeline",
            "schedule": crontab(
                minute="0",
                hour=f"*/{settings.analysis_schedule_hours}",
            ),
            "options": {
                "queue": "analysis",
                "expires": settings.analysis_schedule_hours * 3600,
            },
        },
        "expire-stale-signals-every-30m": {
            "task": "app.worker.tasks.expire_stale_signals",
            "schedule": crontab(minute="*/30"),
            "options": {
                "queue": "maintenance",
            },
        },
        "check-expiring-subscriptions-daily": {
            "task": "app.worker.tasks.check_expiring_subscriptions",
            "schedule": crontab(minute="0", hour="9"),
            "options": {
                "queue": "maintenance",
            },
        },
        "monitor-open-positions-every-1m": {
            "task": "app.worker.tasks.monitor_open_positions",
            "schedule": crontab(minute="*/1"),
            "options": {
                "queue": "execution",
                "expires": 90,
            },
        },
        "cleanup-old-data-weekly": {
            "task": "app.worker.tasks.cleanup_old_data",
            "schedule": crontab(minute="0", hour="3", day_of_week="0"),
            "options": {"queue": "maintenance"},
        },
        "capture-portfolio-snapshots-every-4h": {
            "task": "app.worker.tasks.capture_portfolio_snapshots",
            "schedule": crontab(minute="0", hour="*/4"),
            "options": {"queue": "maintenance"},
        },
    },
)

# Auto-discover tasks
celery_app.autodiscover_tasks(["app.worker"])


# ── Signal Handlers (Monitoring Hooks) ───────────────────────────


@after_setup_logger.connect
def setup_celery_logging(logger, *args, **kwargs):
    """Configure Celery to use the same logging format as FastAPI."""
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    for handler in logger.handlers:
        handler.setFormatter(formatter)


@worker_ready.connect
def on_worker_ready(sender, **kwargs):
    logger.info("EulerX Celery worker ready: %s", sender)


@worker_shutting_down.connect
def on_worker_shutdown(sig, how, exitcode, **kwargs):
    logger.info("EulerX Celery worker shutting down: signal=%s, how=%s", sig, how)


@task_prerun.connect
def on_task_prerun(sender, task_id, task, args, kwargs, **kw):
    logger.info("Task starting: %s[%s]", task.name, task_id)


@task_postrun.connect
def on_task_postrun(sender, task_id, task, args, kwargs, retval, state, **kw):
    logger.info("Task completed: %s[%s] state=%s", task.name, task_id, state)


@task_failure.connect
def on_task_failure(sender, task_id, exception, args, kwargs, traceback, einfo, **kw):
    logger.error(
        "Task FAILED: %s[%s] exception=%s",
        sender.name,
        task_id,
        exception,
        exc_info=True,
    )


@task_retry.connect
def on_task_retry(sender, request, reason, einfo, **kwargs):
    logger.warning(
        "Task retrying: %s[%s] reason=%s",
        sender.name,
        request.id,
        reason,
    )
