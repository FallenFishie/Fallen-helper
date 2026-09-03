import json
from pathlib import Path

import pytest

from lola_helper.config import Settings
from lola_helper.tools import ComputerTools, ToolError


@pytest.mark.asyncio
async def test_reads_text_inside_allowed_root(settings: Settings) -> None:
    path = settings.allowed_roots[0] / "hello.txt"
    path.write_text("hello from Lola", encoding="utf-8")
    tools = ComputerTools(settings)

    result = await tools.execute("read_text_file", {"path": str(path)})

    assert result.output["content"] == "hello from Lola"
    assert result.output["truncated"] is False


@pytest.mark.asyncio
async def test_rejects_path_outside_allowed_root(settings: Settings, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    tools = ComputerTools(settings)

    with pytest.raises(ToolError, match="outside the allowed roots"):
        await tools.execute("read_text_file", {"path": str(outside)})


@pytest.mark.asyncio
async def test_rejects_sensitive_path(settings: Settings) -> None:
    ssh_dir = settings.allowed_roots[0] / ".ssh"
    ssh_dir.mkdir()
    secret = ssh_dir / "config"
    secret.write_text("secret", encoding="utf-8")
    tools = ComputerTools(settings)

    with pytest.raises(ToolError, match="credentials or secrets"):
        await tools.execute("read_text_file", {"path": str(secret)})


@pytest.mark.asyncio
async def test_task_lifecycle_is_persisted(settings: Settings) -> None:
    tools = ComputerTools(settings)
    added = await tools.execute("add_task", {"title": "Back up photos", "due": "Saturday"})
    task_id = added.output["task"]["id"]

    current = await tools.execute("list_tasks", {})
    assert [task["title"] for task in current.output["tasks"]] == ["Back up photos"]

    await tools.execute("complete_task", {"task_id": task_id})
    assert tools.list_tasks_data() == []
    persisted = json.loads((settings.data_dir / "tasks.json").read_text(encoding="utf-8"))
    assert persisted[0]["completed"] is True


@pytest.mark.asyncio
async def test_notes_can_be_created_listed_and_read(settings: Settings) -> None:
    tools = ComputerTools(settings)
    await tools.execute("create_note", {"title": "Router setup", "content": "Use the blue cable."})

    listed = await tools.execute("list_notes", {})
    note_id = listed.output["notes"][0]["id"]
    read = await tools.execute("read_note", {"note_id": note_id})

    assert listed.output["notes"][0]["title"] == "Router setup"
    assert "Use the blue cable." in read.output["content"]


@pytest.mark.asyncio
async def test_write_requires_registry_confirmation_and_no_overwrite(settings: Settings) -> None:
    tools = ComputerTools(settings)
    target = settings.allowed_roots[0] / "draft.txt"
    assert tools.needs_confirmation("write_text_file") is True

    await tools.execute("write_text_file", {"path": str(target), "content": "draft"})
    assert target.read_text(encoding="utf-8") == "draft"

    with pytest.raises(ToolError, match="already exists"):
        await tools.execute("write_text_file", {"path": str(target), "content": "replacement"})


@pytest.mark.asyncio
async def test_catastrophic_command_is_blocked(settings: Settings) -> None:
    tools = ComputerTools(settings)

    with pytest.raises(ToolError, match="damaging the system"):
        await tools.execute(
            "run_command",
            {"command": "rm -rf /", "working_directory": str(settings.allowed_roots[0])},
        )
    with pytest.raises(ToolError, match="bidirectional control"):
        await tools.execute(
            "run_command",
            {
                "command": "echo safe\u202erm -rf",
                "working_directory": str(settings.allowed_roots[0]),
            },
        )


@pytest.mark.asyncio
async def test_command_output_is_captured(settings: Settings) -> None:
    tools = ComputerTools(settings)
    result = await tools.execute(
        "run_command",
        {"command": "printf lola", "working_directory": str(settings.allowed_roots[0])},
    )

    assert result.output["exit_code"] == 0
    assert result.output["stdout"] == "lola"
