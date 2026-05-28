"""Test strategy CRUD endpoints."""

import pytest


@pytest.mark.asyncio
async def test_create_strategy(client, test_user):
    """POST /strategies creates a new strategy."""
    response = await client.post(
        "/api/v1/strategies",
        headers=test_user["headers"],
        json={
            "name": "My Test Strategy",
            "strategy_type": "moderate",
            "risk_profile": "medium",
            "allocation_pct": 50.0,
            "leverage_limit": 2.0,
            "max_positions": 5,
            "max_drawdown_percent": 10.0,
            "target_exchange": "binance",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "My Test Strategy"
    assert data["strategy_type"] == "moderate"
    assert data["target_exchange"] == "binance"
    assert data["is_active"] is False


@pytest.mark.asyncio
async def test_list_strategies(client, test_user):
    """GET /strategies returns user's strategies."""
    response = await client.get(
        "/api/v1/strategies",
        headers=test_user["headers"],
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1


@pytest.mark.asyncio
async def test_get_strategy_by_id(client, test_user):
    """GET /strategies/{id} returns a specific strategy."""
    # First create one
    create_resp = await client.post(
        "/api/v1/strategies",
        headers=test_user["headers"],
        json={
            "name": "Get By ID Strategy",
            "strategy_type": "conservative",
            "risk_profile": "low",
            "allocation_pct": 25.0,
        },
    )
    strategy_id = create_resp.json()["id"]

    response = await client.get(
        f"/api/v1/strategies/{strategy_id}",
        headers=test_user["headers"],
    )
    assert response.status_code == 200
    assert response.json()["id"] == strategy_id


@pytest.mark.asyncio
async def test_update_strategy(client, test_user):
    """PATCH /strategies/{id} updates strategy fields."""
    create_resp = await client.post(
        "/api/v1/strategies",
        headers=test_user["headers"],
        json={
            "name": "Update Me",
            "strategy_type": "aggressive",
            "risk_profile": "high",
            "allocation_pct": 75.0,
        },
    )
    strategy_id = create_resp.json()["id"]

    response = await client.put(
        f"/api/v1/strategies/{strategy_id}",
        headers=test_user["headers"],
        json={"name": "Updated Name", "max_positions": 10},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Updated Name"
    assert response.json()["max_positions"] == 10


@pytest.mark.asyncio
async def test_delete_strategy(client, test_user):
    """DELETE /strategies/{id} removes a strategy."""
    create_resp = await client.post(
        "/api/v1/strategies",
        headers=test_user["headers"],
        json={
            "name": "Delete Me",
            "strategy_type": "custom",
            "risk_profile": "low",
            "allocation_pct": 100.0,
        },
    )
    strategy_id = create_resp.json()["id"]

    response = await client.delete(
        f"/api/v1/strategies/{strategy_id}",
        headers=test_user["headers"],
    )
    assert response.status_code == 200

    # Verify it's gone
    get_resp = await client.get(
        f"/api/v1/strategies/{strategy_id}",
        headers=test_user["headers"],
    )
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_strategy_unauthorized(client):
    """Strategy endpoints require authentication."""
    response = await client.get("/api/v1/strategies")
    assert response.status_code == 401
