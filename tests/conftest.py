from pathlib import Path

import pytest

from fallen_helper.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    root = tmp_path / "home"
    root.mkdir()
    return Settings(
        model="test-model",
        ollama_url="http://127.0.0.1:11434",
        host="127.0.0.1",
        port=7331,
        open_browser=False,
        data_dir=tmp_path / "data",
        allowed_roots=(root.resolve(),),
        allow_commands=True,
        command_timeout_seconds=5,
        max_agent_rounds=5,
        app_aliases={},
    )
