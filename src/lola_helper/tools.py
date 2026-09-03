"""Safe, explicit tools that the local language model may request."""

from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import threading
import uuid
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse

from .config import Settings


class ToolError(RuntimeError):
    """A tool request that cannot be completed safely."""


@dataclass(slots=True)
class ToolExecution:
    output: dict[str, Any]
    summary: str


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., ToolExecution]
    requires_confirmation: bool = False
    confirmation: Callable[[dict[str, Any]], str] | None = None

    def as_ollama_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class TaskStore:
    """A tiny, atomic JSON task store used by both the UI and model tools."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def _read_unlocked(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError) as exc:
            raise ToolError(f"Could not read tasks: {exc}") from exc
        if not isinstance(data, list):
            raise ToolError("The task data file is invalid")
        return [item for item in data if isinstance(item, dict)]

    def _write_unlocked(self, tasks: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(tasks, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def list(self, include_completed: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            tasks = self._read_unlocked()
        if not include_completed:
            tasks = [task for task in tasks if not task.get("completed")]
        return sorted(
            tasks, key=lambda task: (bool(task.get("completed")), task.get("created_at", ""))
        )

    def add(self, title: str, due: str | None = None) -> dict[str, Any]:
        clean_title = " ".join(title.split()).strip()
        if not clean_title:
            raise ToolError("A task title is required")
        if len(clean_title) > 240:
            raise ToolError("Task titles are limited to 240 characters")
        task = {
            "id": uuid.uuid4().hex[:10],
            "title": clean_title,
            "due": (due or "").strip() or None,
            "completed": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            tasks = self._read_unlocked()
            tasks.append(task)
            self._write_unlocked(tasks)
        return task

    def complete(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            tasks = self._read_unlocked()
            for task in tasks:
                if task.get("id") == task_id:
                    task["completed"] = True
                    task["completed_at"] = datetime.now(timezone.utc).isoformat()
                    self._write_unlocked(tasks)
                    return task
        raise ToolError(f"No task with id {task_id!r} was found")


class ComputerTools:
    """Implement local computer actions with narrow inputs and clear boundaries."""

    _SENSITIVE_PARTS = {
        ".ssh",
        ".gnupg",
        ".aws",
        ".azure",
        ".kube",
        ".password-store",
        "keychains",
    }
    _SENSITIVE_NAMES = {
        ".env",
        ".netrc",
        "credentials",
        "credentials.json",
        "login data",
        "id_rsa",
        "id_ed25519",
    }
    _SKIP_SEARCH_DIRS = {
        ".git",
        ".cache",
        ".venv",
        "node_modules",
        "__pycache__",
        "library",
        "appdata",
    }
    _BIDI_CONTROLS = {
        "\u061c",
        "\u200e",
        "\u200f",
        "\u202a",
        "\u202b",
        "\u202c",
        "\u202d",
        "\u202e",
        "\u2066",
        "\u2067",
        "\u2068",
        "\u2069",
    }
    _CATASTROPHIC_COMMANDS = (
        re.compile(r"(^|[;&|]\s*)rm\s+(-[^\n]*\s+)*[/~](\s|$)", re.IGNORECASE),
        re.compile(r"\b(mkfs|diskpart|format\s+[a-z]:|bcdedit)\b", re.IGNORECASE),
        re.compile(r"\b(shutdown|reboot|poweroff|halt)\b", re.IGNORECASE),
        re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", re.IGNORECASE),
        re.compile(r"\bdd\s+.*\bof=/dev/", re.IGNORECASE),
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.settings.ensure_data_dirs()
        self.tasks = TaskStore(settings.data_dir / "tasks.json")
        self._specs: dict[str, ToolSpec] = {}
        self._register_tools()

    @property
    def definitions(self) -> list[dict[str, Any]]:
        return [spec.as_ollama_tool() for spec in self._specs.values()]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._specs)

    def needs_confirmation(self, name: str) -> bool:
        spec = self._specs.get(name)
        return bool(spec and spec.requires_confirmation)

    def validate_request(self, name: str, arguments: dict[str, Any]) -> None:
        spec = self._specs.get(name)
        if spec is None:
            raise ToolError(f"Unknown tool: {name}")
        self._validate_arguments(spec, arguments)
        if name == "run_command":
            self._validate_command_text(str(arguments["command"]))
        if name == "write_text_file" and len(str(arguments["content"])) > 200_000:
            raise ToolError("A single write is limited to 200,000 characters")

    def confirmation_text(self, name: str, arguments: dict[str, Any]) -> str:
        spec = self._specs.get(name)
        if spec is None:
            return f"Allow unknown action {name}?"
        if spec.confirmation:
            return spec.confirmation(arguments)
        return f"Allow Lola to run {name}?"

    def public_arguments(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Return approval-dialog arguments without deceptive control characters."""
        public = dict(arguments)
        if name == "write_text_file" and "content" in public:
            content = str(public["content"])
            public["content"] = f"<{len(content)} characters; preview: {content[:160]!r}>"
        return {
            key: self._visible_text(value) if isinstance(value, str) else value
            for key, value in public.items()
        }

    @classmethod
    def _visible_text(cls, value: str) -> str:
        return "".join(
            f"\\u{ord(character):04x}"
            if character in cls._BIDI_CONTROLS
            or (ord(character) < 32 and character not in {"\n", "\r", "\t"})
            else character
            for character in value
        )

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolExecution:
        spec = self._specs.get(name)
        if spec is None:
            raise ToolError(f"Unknown tool: {name}")
        self._validate_arguments(spec, arguments)
        try:
            return await asyncio.to_thread(spec.handler, **arguments)
        except ToolError:
            raise
        except TypeError as exc:
            raise ToolError(f"Invalid arguments for {name}: {exc}") from exc
        except Exception as exc:  # keep platform launch errors useful to the model
            raise ToolError(f"{name} failed: {exc}") from exc

    def list_tasks_data(self, include_completed: bool = False) -> list[dict[str, Any]]:
        return self.tasks.list(include_completed=include_completed)

    def _add_spec(self, spec: ToolSpec) -> None:
        self._specs[spec.name] = spec

    @staticmethod
    def _object_schema(
        properties: dict[str, Any], required: list[str] | None = None
    ) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": properties,
            "required": required or [],
            "additionalProperties": False,
        }

    def _register_tools(self) -> None:
        self._add_spec(
            ToolSpec(
                "get_current_time",
                "Get the computer's current local date, time, and timezone.",
                self._object_schema({}),
                self._get_current_time,
            )
        )
        self._add_spec(
            ToolSpec(
                "get_system_info",
                "Get basic non-sensitive operating system and hardware information.",
                self._object_schema({}),
                self._get_system_info,
            )
        )
        self._add_spec(
            ToolSpec(
                "list_known_applications",
                "List application aliases that can be passed to open_application.",
                self._object_schema({}),
                self._list_known_applications,
            )
        )
        self._add_spec(
            ToolSpec(
                "open_application",
                (
                    "Open a known desktop application by friendly name. Use "
                    "list_known_applications if unsure."
                ),
                self._object_schema(
                    {"application": {"type": "string", "description": "Friendly app name"}},
                    ["application"],
                ),
                self._open_application,
            )
        )
        self._add_spec(
            ToolSpec(
                "open_url",
                "Open an explicit http or https URL in the user's default browser.",
                self._object_schema(
                    {"url": {"type": "string", "description": "Full http(s) URL"}}, ["url"]
                ),
                self._open_url,
            )
        )
        self._add_spec(
            ToolSpec(
                "search_web",
                (
                    "Open a web search in the user's default browser. This does not read "
                    "search results."
                ),
                self._object_schema(
                    {"query": {"type": "string", "description": "Search terms"}}, ["query"]
                ),
                self._search_web,
            )
        )
        self._add_spec(
            ToolSpec(
                "open_path",
                (
                    "Open an existing allowed file or folder with the operating system's "
                    "default application."
                ),
                self._object_schema(
                    {"path": {"type": "string", "description": "File or folder path"}}, ["path"]
                ),
                self._open_path,
            )
        )
        self._add_spec(
            ToolSpec(
                "list_directory",
                "List files and folders in an allowed directory. It does not read file contents.",
                self._object_schema(
                    {
                        "path": {"type": "string", "default": "~"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    }
                ),
                self._list_directory,
            )
        )
        self._add_spec(
            ToolSpec(
                "search_files",
                (
                    "Search file and folder names under an allowed directory; does not search "
                    "file contents."
                ),
                self._object_schema(
                    {
                        "query": {"type": "string"},
                        "directory": {"type": "string", "default": "~"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    },
                    ["query"],
                ),
                self._search_files,
            )
        )
        self._add_spec(
            ToolSpec(
                "read_text_file",
                (
                    "Read a plain-text file from an allowed path. Sensitive credential "
                    "locations are blocked."
                ),
                self._object_schema(
                    {
                        "path": {"type": "string"},
                        "max_characters": {
                            "type": "integer",
                            "minimum": 200,
                            "maximum": 24000,
                        },
                    },
                    ["path"],
                ),
                self._read_text_file,
            )
        )
        self._add_spec(
            ToolSpec(
                "write_text_file",
                "Write a UTF-8 text file inside an allowed root. Always requires user approval.",
                self._object_schema(
                    {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                        "overwrite": {"type": "boolean", "default": False},
                    },
                    ["path", "content"],
                ),
                self._write_text_file,
                requires_confirmation=True,
                confirmation=self._confirm_write,
            )
        )
        self._add_spec(
            ToolSpec(
                "create_note",
                "Save a note in Lola's private notes folder.",
                self._object_schema(
                    {"title": {"type": "string"}, "content": {"type": "string"}},
                    ["title", "content"],
                ),
                self._create_note,
            )
        )
        self._add_spec(
            ToolSpec(
                "list_notes",
                "List notes previously saved by Lola, including the id used by read_note.",
                self._object_schema({}),
                self._list_notes,
            )
        )
        self._add_spec(
            ToolSpec(
                "read_note",
                "Read one of Lola's notes using an id returned by list_notes.",
                self._object_schema({"note_id": {"type": "string"}}, ["note_id"]),
                self._read_note,
            )
        )
        self._add_spec(
            ToolSpec(
                "add_task",
                "Add an item to the user's local Lola task list.",
                self._object_schema(
                    {
                        "title": {"type": "string"},
                        "due": {
                            "type": "string",
                            "description": "Optional human-readable or ISO due date",
                        },
                    },
                    ["title"],
                ),
                self._add_task,
            )
        )
        self._add_spec(
            ToolSpec(
                "list_tasks",
                "List tasks from the user's local Lola task list.",
                self._object_schema({"include_completed": {"type": "boolean", "default": False}}),
                self._list_tasks,
            )
        )
        self._add_spec(
            ToolSpec(
                "complete_task",
                "Mark a Lola task complete using the id returned by list_tasks.",
                self._object_schema({"task_id": {"type": "string"}}, ["task_id"]),
                self._complete_task,
            )
        )
        if self.settings.allow_commands:
            self._add_spec(
                ToolSpec(
                    "run_command",
                    (
                        "Run a non-interactive shell command after the user sees and "
                        "explicitly approves it."
                    ),
                    self._object_schema(
                        {
                            "command": {"type": "string"},
                            "working_directory": {
                                "type": "string",
                                "description": (
                                    "Allowed working directory; defaults to the home folder"
                                ),
                            },
                            "timeout_seconds": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": self.settings.command_timeout_seconds,
                            },
                        },
                        ["command"],
                    ),
                    self._run_command,
                    requires_confirmation=True,
                    confirmation=self._confirm_command,
                )
            )

    def _confirm_write(self, arguments: dict[str, Any]) -> str:
        path = self._visible_text(str(arguments.get("path", "a file")))
        size = len(str(arguments.get("content", "")))
        return f"Write {size} characters to {path}?"

    def _confirm_command(self, arguments: dict[str, Any]) -> str:
        command = self._visible_text(str(arguments.get("command", "")))
        return f"Run this command?\n\n{command}"

    @staticmethod
    def _validate_arguments(spec: ToolSpec, arguments: dict[str, Any]) -> None:
        if not isinstance(arguments, dict):
            raise ToolError("Tool arguments must be a JSON object")
        properties = spec.parameters.get("properties", {})
        unknown = set(arguments) - set(properties)
        if unknown:
            raise ToolError(f"Unexpected argument(s): {', '.join(sorted(unknown))}")
        missing = [name for name in spec.parameters.get("required", []) if name not in arguments]
        if missing:
            raise ToolError(f"Missing argument(s): {', '.join(missing)}")
        for name, value in arguments.items():
            expected = properties.get(name, {}).get("type")
            if expected == "string" and not isinstance(value, str):
                raise ToolError(f"{name} must be a string")
            if expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
                raise ToolError(f"{name} must be an integer")
            if expected == "boolean" and not isinstance(value, bool):
                raise ToolError(f"{name} must be true or false")
            minimum = properties.get(name, {}).get("minimum")
            maximum = properties.get(name, {}).get("maximum")
            if minimum is not None and value < minimum:
                raise ToolError(f"{name} must be at least {minimum}")
            if maximum is not None and value > maximum:
                raise ToolError(f"{name} must be no more than {maximum}")

    def _resolve_path(self, value: str, *, must_exist: bool = True) -> Path:
        if not value or not value.strip():
            raise ToolError("A path is required")
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = Path.home() / path
        try:
            resolved = path.resolve(strict=must_exist)
        except (OSError, RuntimeError) as exc:
            raise ToolError(f"Could not resolve path {value!r}: {exc}") from exc

        if not any(self._is_within(resolved, root) for root in self.settings.allowed_roots):
            roots = ", ".join(str(root) for root in self.settings.allowed_roots)
            raise ToolError(f"Path is outside the allowed roots ({roots})")
        self._reject_sensitive(resolved)
        return resolved

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _reject_sensitive(self, path: Path) -> None:
        lowered_parts = {part.lower() for part in path.parts}
        if lowered_parts & self._SENSITIVE_PARTS or path.name.lower() in self._SENSITIVE_NAMES:
            raise ToolError("That path is blocked because it may contain credentials or secrets")

    @staticmethod
    def _get_current_time() -> ToolExecution:
        now = datetime.now().astimezone()
        data = {
            "iso": now.isoformat(),
            "date": now.strftime("%A, %B %d, %Y"),
            "time": now.strftime("%I:%M:%S %p"),
            "timezone": str(now.tzinfo),
        }
        return ToolExecution(data, f"Checked the time: {data['time']}")

    @staticmethod
    def _get_system_info() -> ToolExecution:
        data = {
            "operating_system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
        }
        return ToolExecution(data, f"Read system information ({data['operating_system']})")

    def _application_commands(self) -> dict[str, list[list[str]]]:
        system = platform.system().lower()
        if system == "windows":
            commands: dict[str, list[list[str]]] = {
                "calculator": [["calc.exe"]],
                "notepad": [["notepad.exe"]],
                "terminal": [["wt.exe"], ["powershell.exe"]],
                "file explorer": [["explorer.exe"]],
                "task manager": [["taskmgr.exe"]],
                "settings": [["cmd.exe", "/c", "start", "", "ms-settings:"]],
                "paint": [["mspaint.exe"]],
            }
        elif system == "darwin":
            commands = {
                "calculator": [["open", "-a", "Calculator"]],
                "text editor": [["open", "-a", "TextEdit"]],
                "terminal": [["open", "-a", "Terminal"]],
                "file manager": [["open", "-a", "Finder"]],
                "activity monitor": [["open", "-a", "Activity Monitor"]],
                "settings": [["open", "-a", "System Settings"]],
                "calendar": [["open", "-a", "Calendar"]],
            }
        else:
            commands = {
                "calculator": [["gnome-calculator"], ["kcalc"], ["galculator"]],
                "text editor": [["gedit"], ["kate"], ["mousepad"]],
                "terminal": [
                    ["x-terminal-emulator"],
                    ["gnome-terminal"],
                    ["konsole"],
                ],
                "file manager": [["xdg-open", str(Path.home())]],
                "system monitor": [["gnome-system-monitor"], ["plasma-systemmonitor"]],
                "settings": [["gnome-control-center"], ["systemsettings"]],
            }
        for alias, command in self.settings.app_aliases.items():
            # Commands are stored as argument lists, preserving paths with spaces.
            if command:
                commands[alias] = [command]
        return commands

    def _list_known_applications(self) -> ToolExecution:
        aliases = sorted(self._application_commands())
        return ToolExecution({"applications": aliases}, "Listed known applications")

    @staticmethod
    def _command_available(command: list[str]) -> bool:
        executable = command[0]
        if Path(executable).is_absolute():
            return Path(executable).exists()
        return shutil.which(executable) is not None

    @staticmethod
    def _spawn(command: list[str]) -> None:
        kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if platform.system().lower() == "windows":
            kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            )
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(command, **kwargs)  # noqa: S603 - commands come from the alias allowlist

    def _open_application(self, application: str) -> ToolExecution:
        requested = " ".join(application.lower().split())
        aliases = self._application_commands()
        # A few natural synonyms do not need separate platform entries.
        synonyms = {
            "calc": "calculator",
            "files": "file explorer" if platform.system().lower() == "windows" else "file manager",
            "explorer": "file explorer",
            "finder": "file manager",
            "taskmanager": "task manager",
            "command prompt": "terminal",
            "powershell": "terminal",
        }
        requested = synonyms.get(requested, requested)
        candidates = aliases.get(requested)
        if not candidates:
            available = ", ".join(sorted(aliases))
            raise ToolError(f"Unknown application {application!r}. Known applications: {available}")
        for command in candidates:
            if self._command_available(command):
                self._spawn(command)
                return ToolExecution({"opened": requested}, f"Opened {requested}")
        raise ToolError(f"{requested.title()} is known but does not appear to be installed")

    @staticmethod
    def _open_url(url: str) -> ToolExecution:
        parsed = urlparse(url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ToolError("Only complete http:// or https:// URLs can be opened")
        opened = webbrowser.open(url, new=2)
        if not opened:
            raise ToolError("The operating system did not accept the browser request")
        return ToolExecution({"opened": url}, f"Opened {parsed.netloc} in the browser")

    @staticmethod
    def _search_web(query: str) -> ToolExecution:
        clean = query.strip()
        if not clean:
            raise ToolError("A search query is required")
        url = f"https://duckduckgo.com/?q={quote_plus(clean)}"
        opened = webbrowser.open(url, new=2)
        if not opened:
            raise ToolError("The operating system did not accept the browser request")
        return ToolExecution({"query": clean, "opened": url}, f"Opened a web search for {clean!r}")

    def _open_path(self, path: str) -> ToolExecution:
        resolved = self._resolve_path(path)
        system = platform.system().lower()
        if system == "windows":
            os.startfile(str(resolved))  # type: ignore[attr-defined]  # noqa: S606
        elif system == "darwin":
            self._spawn(["open", str(resolved)])
        else:
            if shutil.which("xdg-open") is None:
                raise ToolError("xdg-open is not installed")
            self._spawn(["xdg-open", str(resolved)])
        return ToolExecution({"opened": str(resolved)}, f"Opened {resolved.name}")

    def _list_directory(self, path: str = "~", limit: int = 50) -> ToolExecution:
        resolved = self._resolve_path(path)
        if not resolved.is_dir():
            raise ToolError(f"{resolved} is not a directory")
        safe_limit = max(1, min(limit, 100))
        entries: list[dict[str, Any]] = []
        try:
            children = sorted(
                resolved.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())
            )
            for child in children[:safe_limit]:
                try:
                    stat = child.stat()
                    entries.append(
                        {
                            "name": child.name,
                            "type": "directory" if child.is_dir() else "file",
                            "size_bytes": stat.st_size if child.is_file() else None,
                            "modified": datetime.fromtimestamp(stat.st_mtime)
                            .astimezone()
                            .isoformat(),
                        }
                    )
                except OSError:
                    entries.append({"name": child.name, "type": "unavailable"})
        except OSError as exc:
            raise ToolError(f"Could not list {resolved}: {exc}") from exc
        data = {
            "directory": str(resolved),
            "entries": entries,
            "truncated": len(children) > safe_limit,
        }
        return ToolExecution(data, f"Listed {len(entries)} items in {resolved.name or resolved}")

    def _search_files(self, query: str, directory: str = "~", limit: int = 20) -> ToolExecution:
        clean_query = query.strip().casefold()
        if not clean_query:
            raise ToolError("A search query is required")
        root = self._resolve_path(directory)
        if not root.is_dir():
            raise ToolError(f"{root} is not a directory")
        safe_limit = max(1, min(limit, 50))
        matches: list[dict[str, str]] = []
        scanned = 0
        for current, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                name
                for name in dirnames
                if name.casefold() not in self._SKIP_SEARCH_DIRS and not name.startswith(".")
            ]
            for name in [*dirnames, *filenames]:
                scanned += 1
                if clean_query in name.casefold():
                    item = Path(current) / name
                    matches.append(
                        {"path": str(item), "type": "directory" if item.is_dir() else "file"}
                    )
                    if len(matches) >= safe_limit:
                        break
                if scanned >= 10000:
                    break
            if len(matches) >= safe_limit or scanned >= 10000:
                break
        data = {"matches": matches, "scanned": scanned, "truncated": scanned >= 10000}
        return ToolExecution(data, f"Found {len(matches)} matching paths")

    def _read_text_file(self, path: str, max_characters: int = 8000) -> ToolExecution:
        resolved = self._resolve_path(path)
        if not resolved.is_file():
            raise ToolError(f"{resolved} is not a file")
        if resolved.stat().st_size > 2_000_000:
            raise ToolError("Files larger than 2 MB are not read by the assistant")
        safe_max = max(200, min(max_characters, 24000))
        try:
            raw = resolved.read_bytes()
        except OSError as exc:
            raise ToolError(f"Could not read {resolved}: {exc}") from exc
        if b"\x00" in raw[:4096]:
            raise ToolError("The requested file appears to be binary")
        text = raw.decode("utf-8", errors="replace")
        data = {
            "path": str(resolved),
            "content": text[:safe_max],
            "truncated": len(text) > safe_max,
            "characters": len(text),
        }
        return ToolExecution(
            data, f"Read {min(len(text), safe_max)} characters from {resolved.name}"
        )

    def _write_text_file(self, path: str, content: str, overwrite: bool = False) -> ToolExecution:
        if len(content) > 200_000:
            raise ToolError("A single write is limited to 200,000 characters")
        resolved = self._resolve_path(path, must_exist=False)
        if resolved.exists() and not overwrite:
            raise ToolError("The file already exists; ask again with overwrite=true to replace it")
        if resolved.exists() and not resolved.is_file():
            raise ToolError("The destination exists and is not a file")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        try:
            resolved.write_text(content, encoding="utf-8")
        except OSError as exc:
            raise ToolError(f"Could not write {resolved}: {exc}") from exc
        return ToolExecution(
            {"path": str(resolved), "characters_written": len(content)},
            f"Wrote {len(content)} characters to {resolved.name}",
        )

    def _create_note(self, title: str, content: str) -> ToolExecution:
        clean_title = " ".join(title.split()).strip()
        if not clean_title:
            raise ToolError("A note title is required")
        if len(content) > 100_000:
            raise ToolError("A note is limited to 100,000 characters")
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", clean_title).strip("-").lower()[:60] or "note"
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = self.settings.data_dir / "notes" / f"{timestamp}-{slug}.md"
        path.write_text(f"# {clean_title}\n\n{content.strip()}\n", encoding="utf-8")
        return ToolExecution(
            {"title": clean_title, "path": str(path)}, f"Saved note “{clean_title}”"
        )

    def _list_notes(self) -> ToolExecution:
        notes: list[dict[str, Any]] = []
        for path in sorted((self.settings.data_dir / "notes").glob("*.md"), reverse=True)[:50]:
            try:
                first_line = path.read_text(encoding="utf-8").splitlines()[0]
            except (OSError, IndexError):
                first_line = path.stem
            notes.append(
                {
                    "id": path.stem,
                    "title": first_line.removeprefix("# "),
                    "created": datetime.fromtimestamp(path.stat().st_mtime)
                    .astimezone()
                    .isoformat(),
                }
            )
        return ToolExecution({"notes": notes}, f"Listed {len(notes)} notes")

    def _read_note(self, note_id: str) -> ToolExecution:
        clean_id = note_id.strip()
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", clean_id):
            raise ToolError("The note id is invalid; use an id returned by list_notes")
        path = self.settings.data_dir / "notes" / f"{clean_id}.md"
        if not path.is_file():
            raise ToolError(f"No note with id {clean_id!r} was found")
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ToolError(f"Could not read the note: {exc}") from exc
        if len(content) > 24000:
            content = content[:24000] + "\n\n[Note truncated]"
        return ToolExecution({"id": clean_id, "content": content}, f"Read note {clean_id}")

    def _add_task(self, title: str, due: str | None = None) -> ToolExecution:
        task = self.tasks.add(title, due)
        return ToolExecution({"task": task}, f"Added task “{task['title']}”")

    def _list_tasks(self, include_completed: bool = False) -> ToolExecution:
        tasks = self.tasks.list(include_completed)
        return ToolExecution({"tasks": tasks}, f"Listed {len(tasks)} tasks")

    def _complete_task(self, task_id: str) -> ToolExecution:
        task = self.tasks.complete(task_id)
        return ToolExecution({"task": task}, f"Completed task “{task['title']}”")

    @classmethod
    def _validate_command_text(cls, command: str) -> str:
        clean = command.strip()
        if not clean:
            raise ToolError("A command is required")
        if len(clean) > 4000:
            raise ToolError("Commands are limited to 4,000 characters")
        if any(character in cls._BIDI_CONTROLS for character in clean):
            raise ToolError("Commands containing bidirectional control characters are blocked")
        if any(ord(character) < 32 and character not in {"\n", "\r", "\t"} for character in clean):
            raise ToolError("Commands containing hidden control characters are blocked")
        if any(pattern.search(clean) for pattern in cls._CATASTROPHIC_COMMANDS):
            raise ToolError(
                "This command was blocked because it appears capable of damaging the system"
            )
        return clean

    def _run_command(
        self,
        command: str,
        working_directory: str = "~",
        timeout_seconds: int | None = None,
    ) -> ToolExecution:
        clean = self._validate_command_text(command)
        cwd = self._resolve_path(working_directory)
        if not cwd.is_dir():
            raise ToolError("The command working directory must be a directory")
        timeout = max(
            1,
            min(
                timeout_seconds or self.settings.command_timeout_seconds,
                self.settings.command_timeout_seconds,
            ),
        )
        if platform.system().lower() == "windows":
            invocation = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", clean]
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
            start_new_session = False
        else:
            invocation = ["/bin/sh", "-lc", clean]
            creationflags = 0
            start_new_session = True

        process = subprocess.Popen(  # noqa: S603 - explicit user approval gates this shell
            invocation,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            creationflags=creationflags,
            start_new_session=start_new_session,
        )
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if platform.system().lower() == "windows":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()

        output_limit = 12000
        data = {
            "command": clean,
            "working_directory": str(cwd),
            "exit_code": process.returncode,
            "stdout": stdout[-output_limit:],
            "stderr": stderr[-output_limit:],
            "output_truncated": len(stdout) > output_limit or len(stderr) > output_limit,
            "timed_out": timed_out,
        }
        if timed_out:
            summary = f"Command timed out after {timeout} seconds"
        else:
            summary = f"Command finished with exit code {process.returncode}"
        return ToolExecution(data, summary)
