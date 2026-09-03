"""FastAPI application serving Fallen's local control panel."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .agent import AgentStateError, FallenAgent
from .config import Settings
from .ollama import OllamaClient, OllamaError
from .tools import ComputerTools, ToolError


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=20_000)
    model: str | None = Field(default=None, max_length=160)


class ApprovalRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)
    approval_id: str = Field(min_length=1, max_length=100)
    approved: bool


class ResetRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.load()
    settings.ensure_data_dirs()
    ollama = OllamaClient(settings.ollama_url)
    tools = ComputerTools(settings)
    agent = FallenAgent(settings, ollama, tools)
    static_dir = Path(__file__).parent / "static"

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await ollama.close()

    app = FastAPI(
        title="Fallen Helper",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.ollama = ollama
    app.state.tools = tools
    app.state.agent = agent
    if settings.host in {"127.0.0.1", "localhost", "::1"}:
        # Reject DNS-rebinding hostnames while running in the default local-only mode.
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"],
        )

    @app.middleware("http")
    async def local_security(request: Request, call_next):  # type: ignore[no-untyped-def]
        # Requiring a non-simple custom header on state-changing API calls makes
        # browser-based cross-site requests trigger CORS preflight. We do not
        # enable CORS, so unrelated websites cannot quietly operate the helper.
        if (
            request.url.path.startswith("/api/")
            and request.method in {"POST", "PUT", "DELETE"}
            and request.headers.get("X-Fallen-Client") != "control-panel"
        ):
            return JSONResponse({"detail": "Missing local client header"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), geolocation=(), payment=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; media-src 'self'; object-src 'none'; "
            "base-uri 'none'; form-action 'self'"
        )
        return response

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(static_dir / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        version_result, model_result = await asyncio.gather(
            ollama.version(), ollama.list_models(), return_exceptions=True
        )
        connected = not isinstance(model_result, Exception)
        model_items = [] if isinstance(model_result, Exception) else model_result
        model_names = [
            str(item.get("name") or item.get("model"))
            for item in model_items
            if item.get("name") or item.get("model")
        ]
        error = str(model_result) if isinstance(model_result, Exception) else None
        return {
            "app": "ready",
            "version": __version__,
            "ollama": {
                "connected": connected,
                "version": version_result if isinstance(version_result, str) else None,
                "url": settings.ollama_url,
                "error": error,
            },
            "models": model_names,
            "default_model": settings.model,
            "commands_enabled": settings.allow_commands,
            "allowed_roots": [str(root) for root in settings.allowed_roots],
        }

    @app.get("/api/models")
    async def models() -> dict[str, Any]:
        try:
            items = await ollama.list_models()
        except OllamaError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {
            "models": [
                {
                    "name": str(item.get("name") or item.get("model")),
                    "size": item.get("size"),
                    "modified_at": item.get("modified_at"),
                }
                for item in items
                if item.get("name") or item.get("model")
            ]
        }

    @app.post("/api/chat")
    async def chat(body: ChatRequest) -> dict[str, Any]:
        try:
            return await agent.chat(body.session_id, body.message, body.model)
        except AgentStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except OllamaError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/approval")
    async def approval(body: ApprovalRequest) -> dict[str, Any]:
        try:
            return await agent.resolve_approval(body.session_id, body.approval_id, body.approved)
        except AgentStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except OllamaError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/reset", status_code=204)
    async def reset(body: ResetRequest) -> None:
        await agent.reset(body.session_id)

    @app.get("/api/tasks")
    async def tasks_endpoint(include_completed: bool = False) -> dict[str, Any]:
        try:
            tasks = await asyncio.to_thread(tools.list_tasks_data, include_completed)
        except ToolError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"tasks": tasks}

    @app.post("/api/tasks/{task_id}/complete")
    async def complete_task_endpoint(task_id: str) -> dict[str, Any]:
        try:
            task = await asyncio.to_thread(tools.tasks.complete, task_id)
        except ToolError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"task": task}

    app.mount(
        "/assets",
        StaticFiles(directory=static_dir, check_dir=True),
        name="assets",
    )
    return app
