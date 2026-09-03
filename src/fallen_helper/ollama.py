"""Small asynchronous client for Ollama's local HTTP API."""

from __future__ import annotations

from typing import Any

import httpx


class OllamaError(RuntimeError):
    """A friendly Ollama connectivity or inference error."""


class OllamaClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(connect=4.0, read=300.0, write=30.0, pool=5.0),
            headers={"User-Agent": "Fallen-Helper/0.1"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def version(self) -> str | None:
        try:
            response = await self._client.get("/api/version", timeout=4.0)
            response.raise_for_status()
            value = response.json().get("version")
            return str(value) if value else None
        except (httpx.HTTPError, ValueError):
            return None

    async def list_models(self) -> list[dict[str, Any]]:
        try:
            response = await self._client.get("/api/tags", timeout=6.0)
            response.raise_for_status()
            models = response.json().get("models", [])
        except httpx.ConnectError as exc:
            raise OllamaError(
                f"Cannot reach Ollama at {self.base_url}. Start Ollama, then try again."
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise OllamaError(f"Could not read Ollama's model list: {exc}") from exc
        if not isinstance(models, list):
            return []
        return [model for model in models if isinstance(model, dict)]

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "stream": False,
            "keep_alive": "10m",
            "options": {"temperature": 0.35},
        }
        try:
            response = await self._client.post("/api/chat", json=payload)
        except httpx.ConnectError as exc:
            raise OllamaError(
                f"Cannot reach Ollama at {self.base_url}. Make sure the Ollama app is running."
            ) from exc
        except httpx.TimeoutException as exc:
            raise OllamaError(
                "Ollama took too long to respond. Try a smaller model or a shorter request."
            ) from exc
        except httpx.HTTPError as exc:
            raise OllamaError(f"The Ollama request failed: {exc}") from exc

        if response.is_error:
            try:
                detail = response.json().get("error", response.text)
            except ValueError:
                detail = response.text
            if response.status_code == 404:
                raise OllamaError(f"Model {model!r} is not installed. Run: ollama pull {model}")
            raise OllamaError(f"Ollama returned {response.status_code}: {detail}")
        try:
            data = response.json()
        except ValueError as exc:
            raise OllamaError("Ollama returned an invalid response") from exc
        message = data.get("message")
        if not isinstance(message, dict):
            raise OllamaError("Ollama's response did not include a chat message")
        return data
