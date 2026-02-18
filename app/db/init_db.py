from sqlalchemy import text

from app.db.base import engine


async def check_db_connection() -> bool:
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))
    return True


async def dispose_engine() -> None:
    await engine.dispose()
