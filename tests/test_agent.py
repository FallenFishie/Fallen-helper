from collections.abc import Iterable
from typing import Any

import pytest

from lola_helper.agent import AgentStateError, LolaAgent
from lola_helper.config import Settings
from lola_helper.tools import ComputerTools


class FakeOllama:
    def __init__(self, messages: Iterable[dict[str, Any]]) -> None:
        self.responses = list(messages)
        self.requests: list[dict[str, Any]] = []

    async def chat(self, **kwargs: Any) -> dict[str, Any]:
        self.requests.append(kwargs)
        return {"message": self.responses.pop(0)}


@pytest.mark.asyncio
async def test_safe_tool_runs_and_returns_final_answer(settings: Settings) -> None:
    ollama = FakeOllama(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": "get_system_info", "arguments": {}}}],
            },
            {"role": "assistant", "content": "You are running a local test system."},
        ]
    )
    tools = ComputerTools(settings)
    agent = LolaAgent(settings, ollama, tools)  # type: ignore[arg-type]

    result = await agent.chat("session-1", "What system is this?")

    assert result["type"] == "message"
    assert result["message"] == "You are running a local test system."
    assert result["actions"][0]["tool"] == "get_system_info"
    second_messages = ollama.requests[1]["messages"]
    assert any(message.get("role") == "tool" for message in second_messages)


@pytest.mark.asyncio
async def test_file_write_waits_for_explicit_approval(settings: Settings) -> None:
    target = settings.allowed_roots[0] / "approved.txt"
    ollama = FakeOllama(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "write_text_file",
                            "arguments": {"path": str(target), "content": "approved content"},
                        }
                    }
                ],
            },
            {"role": "assistant", "content": "The file is ready."},
        ]
    )
    agent = LolaAgent(settings, ollama, ComputerTools(settings))  # type: ignore[arg-type]

    pending = await agent.chat("session-2", "Write a file")
    assert pending["type"] == "approval_required"
    assert pending["approval"]["tool"] == "write_text_file"
    assert not target.exists()

    result = await agent.resolve_approval("session-2", pending["approval"]["id"], approved=True)
    assert target.read_text(encoding="utf-8") == "approved content"
    assert result["message"] == "The file is ready."


@pytest.mark.asyncio
async def test_denied_action_is_reported_to_model(settings: Settings) -> None:
    target = settings.allowed_roots[0] / "denied.txt"
    ollama = FakeOllama(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "function": {
                            "name": "write_text_file",
                            "arguments": {"path": str(target), "content": "no"},
                        }
                    }
                ],
            },
            {"role": "assistant", "content": "No problem; I did not write it."},
        ]
    )
    agent = LolaAgent(settings, ollama, ComputerTools(settings))  # type: ignore[arg-type]

    pending = await agent.chat("session-3", "Write a file")
    result = await agent.resolve_approval("session-3", pending["approval"]["id"], approved=False)

    assert not target.exists()
    assert result["actions"][0]["status"] == "denied"
    assert "did not write" in result["message"]
    tool_messages = [
        message for message in ollama.requests[1]["messages"] if message.get("role") == "tool"
    ]
    assert "denied" in tool_messages[-1]["content"]


@pytest.mark.asyncio
async def test_new_message_is_blocked_while_approval_pending(settings: Settings) -> None:
    ollama = FakeOllama(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "function": {
                            "name": "run_command",
                            "arguments": {"command": "echo hello"},
                        }
                    }
                ],
            }
        ]
    )
    agent = LolaAgent(settings, ollama, ComputerTools(settings))  # type: ignore[arg-type]
    await agent.chat("session-4", "Run a command")

    with pytest.raises(AgentStateError, match="pending action"):
        await agent.chat("session-4", "Are you there?")
