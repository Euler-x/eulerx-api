"""Utility for running async functions inside synchronous Celery tasks.

Celery prefork workers have no pre-existing event loop, so we create
a fresh loop per task and dispose the SQLAlchemy engine pool afterward
to prevent "Future attached to a different loop" errors.
"""

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def run_async(coro: Coroutine[Any, Any, T]) -> T:
    """Run an async coroutine in a new event loop.

    Creates a fresh event loop per call and disposes the database
    engine's connection pool afterward, ensuring connections created
    on this loop don't leak into subsequent tasks (which get their
    own fresh loop).
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        # Dispose the async engine pool so the next task (on a new loop)
        # gets fresh connections instead of reusing ones bound to this loop.
        try:
            from app.db.base import engine

            loop.run_until_complete(engine.dispose())
        except Exception:
            logger.debug("Engine dispose skipped (not initialized)")
        loop.close()
