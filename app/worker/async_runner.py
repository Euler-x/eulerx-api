"""Utility for running async functions inside synchronous Celery tasks.

Celery prefork workers have no pre-existing event loop, so asyncio.run()
is safe and provides clean isolation per task execution.
"""

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def run_async(coro: Coroutine[Any, Any, T]) -> T:
    """Run an async coroutine in a new event loop.

    Creates a fresh event loop per call, ensuring clean isolation
    between task executions and proper cleanup of async resources
    (DB connections, HTTP clients, etc.).
    """
    try:
        return asyncio.run(coro)
    except RuntimeError as e:
        # Fallback: if an event loop already exists (e.g., tests with
        # celery_task_always_eager=True in an async test runner),
        # create a new loop explicitly.
        if "cannot be called from a running event loop" in str(e):
            logger.warning("Falling back to new event loop creation")
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()
        raise
