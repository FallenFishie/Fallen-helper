import httpx
import pytest

from lola_helper.app import create_app
from lola_helper.config import Settings


@pytest.mark.asyncio
async def test_control_panel_and_health_are_available(settings: Settings) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        page = await client.get("/")
        health = await client.get("/api/health")
        rebound = await client.get("/", headers={"Host": "unexpected.example"})
    await app.state.ollama.close()

    assert page.status_code == 200
    assert "LOLA" in page.text
    assert rebound.status_code == 400
    assert health.status_code == 200
    assert health.json()["app"] == "ready"
    assert health.json()["ollama"]["connected"] is False


@pytest.mark.asyncio
async def test_state_changing_api_requires_local_client_header(settings: Settings) -> None:
    app = create_app(settings)
    task = app.state.tools.tasks.add("Test the control panel")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        blocked = await client.post(
            "/api/reset",
            json={"session_id": "browser-session"},
        )
        allowed = await client.post(
            "/api/reset",
            json={"session_id": "browser-session"},
            headers={"X-Lola-Client": "control-panel"},
        )
        completed = await client.post(
            f"/api/tasks/{task['id']}/complete",
            headers={"X-Lola-Client": "control-panel"},
        )
    await app.state.ollama.close()

    assert blocked.status_code == 403
    assert allowed.status_code == 204
    assert completed.status_code == 200
    assert completed.json()["task"]["completed"] is True
