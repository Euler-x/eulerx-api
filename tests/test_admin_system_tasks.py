from sqlalchemy import delete, select

from app.models.admin_config import AdminConfig
from tests.conftest import TestSessionFactory


async def test_task_toggle_persists_after_fresh_list_request(client, admin_user):
    """Toggling a task should survive the next page refresh/list request."""
    task_name = "analysis-pipeline-hyperliquid"

    async with TestSessionFactory() as session:
        await session.execute(
            delete(AdminConfig).where(AdminConfig.key == "disabled_tasks")
        )
        await session.commit()

    disable_response = await client.put(
        f"/api/v1/admin/system/tasks/{task_name}",
        json={"enabled": False},
        headers=admin_user["headers"],
    )
    assert disable_response.status_code == 200

    list_disabled_response = await client.get(
        "/api/v1/admin/system/tasks",
        headers=admin_user["headers"],
    )
    assert list_disabled_response.status_code == 200
    disabled_task = next(
        task for task in list_disabled_response.json() if task["name"] == task_name
    )
    assert disabled_task["enabled"] is False

    enable_response = await client.put(
        f"/api/v1/admin/system/tasks/{task_name}",
        json={"enabled": True},
        headers=admin_user["headers"],
    )
    assert enable_response.status_code == 200

    list_enabled_response = await client.get(
        "/api/v1/admin/system/tasks",
        headers=admin_user["headers"],
    )
    assert list_enabled_response.status_code == 200
    enabled_task = next(
        task for task in list_enabled_response.json() if task["name"] == task_name
    )
    assert enabled_task["enabled"] is True

    async with TestSessionFactory() as session:
        config = (
            await session.execute(
                select(AdminConfig).where(AdminConfig.key == "disabled_tasks")
            )
        ).scalar_one()
        assert task_name not in config.value["tasks"]


async def test_enabling_exchange_pipeline_preserves_other_legacy_disabled_pipelines(
    client, admin_user
):
    """Enabling selected exchanges should not re-enable every legacy-disabled exchange."""
    async with TestSessionFactory() as session:
        await session.execute(
            delete(AdminConfig).where(AdminConfig.key == "disabled_tasks")
        )
        session.add(
            AdminConfig(
                key="disabled_tasks",
                value={"tasks": ["analysis-pipeline"]},
                description="List of disabled scheduled task names",
            )
        )
        await session.commit()

    for task_name in (
        "analysis-pipeline-hyperliquid",
        "analysis-pipeline-binance",
    ):
        response = await client.put(
            f"/api/v1/admin/system/tasks/{task_name}",
            json={"enabled": True},
            headers=admin_user["headers"],
        )
        assert response.status_code == 200

    list_response = await client.get(
        "/api/v1/admin/system/tasks",
        headers=admin_user["headers"],
    )
    assert list_response.status_code == 200
    tasks = {task["name"]: task for task in list_response.json()}

    assert tasks["analysis-pipeline-hyperliquid"]["enabled"] is True
    assert tasks["analysis-pipeline-binance"]["enabled"] is True
    assert tasks["analysis-pipeline-bybit"]["enabled"] is False

    async with TestSessionFactory() as session:
        config = (
            await session.execute(
                select(AdminConfig).where(AdminConfig.key == "disabled_tasks")
            )
        ).scalar_one()
        assert config.value["tasks"] == ["analysis-pipeline-bybit"]
