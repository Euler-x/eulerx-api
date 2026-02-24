"""Shared test fixtures for EulerX backend E2E tests.

Uses SQLite (aiosqlite) for all database operations.
All external APIs (Telegram, ZeptoMail, Hyperliquid) are mocked.
"""

import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# Point to .env.test BEFORE importing app modules
os.environ["ENV_FILE"] = ".env.test"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite://"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-for-e2e-testing-32chars"
os.environ["WALLET_ENCRYPTION_KEY"] = "V9vI8kEq1T21ktsHaHiAS3La84oAGWG5eyQaCLiju2k="
os.environ["CELERY_TASK_ALWAYS_EAGER"] = "true"
os.environ["ZEPTOMAIL_TOKEN"] = "fake-zeptomail-token"
os.environ["OPENROUTER_API_KEY"] = "sk-test-fake"
os.environ["ENVIRONMENT"] = "testing"
os.environ["TELEGRAM_ENCRYPTION_KEY"] = "V9vI8kEq1T21ktsHaHiAS3La84oAGWG5eyQaCLiju2k="
os.environ["SENTRY_DSN"] = ""

from app.config import get_settings  # noqa: E402
from app.db.base import Base, get_db  # noqa: E402
from app.models.database import *  # noqa: E402, F401, F403 — register all models
from app.models.enums import WalletType  # noqa: E402
from app.models.user import User  # noqa: E402
from app.utils.security import create_access_token  # noqa: E402

settings = get_settings()

# ── Test Engine (in-memory SQLite) ──────────────────────────────

TEST_DATABASE_URL = "sqlite+aiosqlite://"

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(test_engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


TestSessionFactory = async_sessionmaker(
    test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


# ── Fixtures ──────────────────────────────────────────────────────


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def setup_db():
    """Create all tables once per test session."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def db_session(setup_db):
    """Provide a fresh DB session per test, rolled back after each test."""
    async with TestSessionFactory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def app(setup_db):
    """FastAPI app instance with DB dependency overridden."""
    from app.main import app as fastapi_app

    async def _override_get_db():
        async with TestSessionFactory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    fastapi_app.dependency_overrides[get_db] = _override_get_db
    yield fastapi_app
    fastapi_app.dependency_overrides.clear()


@pytest_asyncio.fixture(loop_scope="session")
async def client(app):
    """httpx AsyncClient bound to the FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def test_user(setup_db) -> dict:
    """Create a test user and return user data + auth headers."""
    user_id = uuid.uuid4()
    wallet_hash = "a" * 64

    async with TestSessionFactory() as session:
        user = User(
            id=user_id,
            wallet_address_hash=wallet_hash,
            wallet_type=WalletType.CONNECTED,
            email="test@example.com",
            email_verified=True,
        )
        session.add(user)
        await session.commit()

    token = create_access_token(str(user_id), is_admin=False)
    return {
        "id": user_id,
        "wallet_hash": wallet_hash,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
    }


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def admin_user(setup_db) -> dict:
    """Create an admin user."""
    user_id = uuid.uuid4()
    wallet_hash = "b" * 64

    async with TestSessionFactory() as session:
        user = User(
            id=user_id,
            wallet_address_hash=wallet_hash,
            wallet_type=WalletType.CONNECTED,
            is_admin=True,
        )
        session.add(user)
        await session.commit()

    token = create_access_token(str(user_id), is_admin=True)
    return {
        "id": user_id,
        "wallet_hash": wallet_hash,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
    }


# ── Mock Helpers ──────────────────────────────────────────────────


def mock_telegram_response(ok=True, username="test_bot"):
    """Create a mock httpx response for Telegram API calls."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200 if ok else 401
    mock_resp.json.return_value = {
        "ok": ok,
        "result": {"username": username} if ok else None,
        "description": "Unauthorized" if not ok else None,
    }
    mock_resp.text = '{"ok": true}' if ok else '{"ok": false}'
    return mock_resp


@pytest.fixture
def mock_telegram_api():
    """Mock all Telegram Bot API HTTP calls."""
    with (
        patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post,
        patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get,
    ):
        # Default: successful responses
        mock_post.return_value = mock_telegram_response(ok=True)
        mock_get.return_value = mock_telegram_response(ok=True, username="test_bot")
        yield {"post": mock_post, "get": mock_get}


@pytest.fixture
def mock_email_api():
    """Mock ZeptoMail email API."""
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"message": "OK"}'
        mock_post.return_value = mock_resp
        yield mock_post


@pytest.fixture
def mock_hyperliquid_api():
    """Mock Hyperliquid API calls."""
    with (
        patch(
            "app.services.hyperliquid.HyperliquidService.get_all_mids",
            new_callable=AsyncMock,
        ) as mock_mids,
        patch(
            "app.services.hyperliquid.HyperliquidService.place_order",
            new_callable=AsyncMock,
        ) as mock_order,
        patch(
            "app.services.hyperliquid.HyperliquidService.close_position",
            new_callable=AsyncMock,
        ) as mock_close,
    ):
        mock_mids.return_value = {"BTC": "50000.0", "ETH": "3000.0", "SOL": "150.0"}
        mock_order.return_value = {"success": True, "tx_hash": "0x" + "a" * 64}
        mock_close.return_value = {"success": True, "tx_hash": "0x" + "b" * 64}
        yield {
            "get_all_mids": mock_mids,
            "place_order": mock_order,
            "close_position": mock_close,
        }
