"""Configuration loading for Fallen Helper.

Configuration is intentionally small and JSON-based so the application can be
installed without a database or a settings service. Environment variables take
precedence over values in ``~/.fallen-helper/config.json``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read configuration at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Configuration at {path} must contain a JSON object")
    return data


def _normalise_roots(raw: Any, home: Path) -> tuple[Path, ...]:
    if raw is None:
        values: list[str] = [str(home)]
    elif isinstance(raw, str):
        # The environment variable uses the platform path separator. A single
        # path in JSON is also accepted.
        values = raw.split(os.pathsep) if os.pathsep in raw else [raw]
    elif isinstance(raw, list):
        values = [str(value) for value in raw]
    else:
        raise ValueError("allowed_roots must be a path or a list of paths")

    roots: list[Path] = []
    for value in values:
        expanded = Path(value).expanduser()
        if not expanded.is_absolute():
            expanded = home / expanded
        resolved = expanded.resolve(strict=False)
        if resolved not in roots:
            roots.append(resolved)
    if not roots:
        roots.append(home.resolve())
    return tuple(roots)


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings.

    ``allowed_roots`` gates all model-initiated file tools. Shell commands are
    separate: they are always shown to the user for approval and can be fully
    disabled with ``allow_commands``.
    """

    model: str
    ollama_url: str
    host: str
    port: int
    open_browser: bool
    data_dir: Path
    allowed_roots: tuple[Path, ...]
    allow_commands: bool
    command_timeout_seconds: int
    max_agent_rounds: int
    app_aliases: dict[str, list[str]]

    @classmethod
    def load(cls) -> Settings:
        home = Path.home().resolve()
        data_dir = (
            Path(os.environ.get("FALLEN_DATA_DIR", home / ".fallen-helper"))
            .expanduser()
            .resolve(strict=False)
        )
        config = _read_json(data_dir / "config.json")

        env_roots = os.environ.get("FALLEN_ALLOWED_ROOTS")
        roots = _normalise_roots(
            env_roots if env_roots is not None else config.get("allowed_roots"), home
        )

        aliases_raw = config.get("app_aliases", {})
        aliases: dict[str, list[str]] = {}
        if isinstance(aliases_raw, dict):
            for name, command in aliases_raw.items():
                if isinstance(command, str):
                    aliases[str(name).strip().lower()] = [command]
                elif isinstance(command, list) and all(isinstance(part, str) for part in command):
                    aliases[str(name).strip().lower()] = list(command)

        model = os.environ.get("FALLEN_MODEL", str(config.get("model", "qwen3:4b")))
        ollama_url = os.environ.get(
            "FALLEN_OLLAMA_URL", str(config.get("ollama_url", "http://127.0.0.1:11434"))
        ).rstrip("/")

        return cls(
            model=model,
            ollama_url=ollama_url,
            host=os.environ.get("FALLEN_HOST", str(config.get("host", "127.0.0.1"))),
            port=int(os.environ.get("FALLEN_PORT", config.get("port", 7331))),
            open_browser=_as_bool(
                os.environ.get("FALLEN_OPEN_BROWSER", config.get("open_browser")), True
            ),
            data_dir=data_dir,
            allowed_roots=roots,
            allow_commands=_as_bool(
                os.environ.get("FALLEN_ALLOW_COMMANDS", config.get("allow_commands")), True
            ),
            command_timeout_seconds=max(
                1,
                min(
                    300,
                    int(
                        os.environ.get(
                            "FALLEN_COMMAND_TIMEOUT",
                            config.get("command_timeout_seconds", 30),
                        )
                    ),
                ),
            ),
            max_agent_rounds=max(1, min(12, int(config.get("max_agent_rounds", 6)))),
            app_aliases=aliases,
        )

    def ensure_data_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "notes").mkdir(parents=True, exist_ok=True)
