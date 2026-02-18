"""Health check utilities for Celery workers and Redis broker."""

import logging

import redis

from app.config import get_settings
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)
settings = get_settings()


def check_redis_connection() -> dict:
    """Check if Redis broker is reachable."""
    try:
        r = redis.from_url(settings.redis_url, socket_timeout=5)
        r.ping()
        return {"redis": "connected"}
    except Exception as e:
        logger.error("Redis health check failed: %s", e)
        return {"redis": f"error: {e}"}


def check_worker_status() -> dict:
    """Check if any Celery workers are active."""
    try:
        inspector = celery_app.control.inspect(timeout=5.0)
        active = inspector.active()
        stats = inspector.stats()

        if active is None:
            return {"workers": "no_workers_responding", "worker_count": 0}

        worker_info = {}
        for worker_name, tasks in active.items():
            worker_info[worker_name] = {
                "active_tasks": len(tasks),
                "stats": stats.get(worker_name, {}).get("total", {}) if stats else {},
            }

        return {"workers": worker_info, "worker_count": len(worker_info)}
    except Exception as e:
        logger.error("Worker health check failed: %s", e)
        return {"workers": f"error: {e}", "worker_count": 0}


def get_worker_health() -> dict:
    """Full health check for the Celery subsystem."""
    redis_status = check_redis_connection()
    worker_status = check_worker_status()

    all_ok = (
        redis_status.get("redis") == "connected"
        and isinstance(worker_status.get("workers"), dict)
        and worker_status.get("worker_count", 0) > 0
    )

    return {
        "healthy": all_ok,
        **redis_status,
        **worker_status,
    }
