# Lola Helper

A private, local-first desktop AI helper inspired by cinematic assistants. Lola uses an [Ollama](https://ollama.com/) model for conversation and tool selection, then performs narrowly scoped actions on your computer.

It includes a responsive control panel, push-to-talk input, spoken replies, app launching, file search, notes, tasks, system information, and an approval gate for shell commands and file writes.

> **Status:** usable MVP for Windows, macOS, and Linux. It deliberately does not include unrestricted autonomous control, background surveillance, or silent command execution.

## What it can do

- Chat with any installed Ollama model that supports tool calling
- Open known desktop applications, URLs, browser searches, files, and folders
- List folders, search filenames, and read bounded plain-text files
- Keep local notes and a small task list
- Report the current time and basic system information
- Run non-interactive terminal commands **only after showing the exact command for approval**
- Write text files **only after approval**
- Accept voice input and read answers aloud using browser/operating-system speech features

All model traffic goes directly from Lola's local server to the configured Ollama endpoint. There is no Lola cloud account or telemetry.

## Quick start

### Prerequisites

1. Install [Python 3.10 or newer](https://www.python.org/downloads/).
2. Install and start [Ollama](https://ollama.com/download).
3. Pull a tool-capable model. The default is:

```bash
ollama pull qwen3:4b
```

A larger tool-capable model will generally choose computer actions more reliably. You can select any installed model in the control panel.

### Windows

Open PowerShell in this folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
.\scripts\start.bat
```

### macOS / Linux

```bash
chmod +x scripts/install.sh scripts/start.sh
./scripts/install.sh
./scripts/start.sh
```

The launcher opens `http://127.0.0.1:7331` in your browser. Keep the terminal window running while using Lola.

### Manual installation

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -e .
lola-helper
```

Useful launcher options:

```text
lola-helper --model qwen3:8b
lola-helper --port 7444
lola-helper --ollama-url http://127.0.0.1:11434
lola-helper --no-browser
```

## Example requests

- “Open the calculator.”
- “Find files named invoice in Downloads.”
- “Open my Projects folder.”
- “Add a task to back up my photos this weekend.”
- “Save a note called Router setup with this text…”
- “What operating system is this?”
- “Run `git status` in my Projects/demo folder.”
- “Create a text file on my Desktop with this shopping list.”

For the final two examples, Lola pauses and displays the exact action. Nothing runs until **Approve once** is selected.

## Configuration

On first launch Lola creates its private data directory:

- Windows: `%USERPROFILE%\.lola-helper`
- macOS/Linux: `~/.lola-helper`

Copy [`config.example.json`](config.example.json) to `~/.lola-helper/config.json` and edit it as needed:

```json
{
  "model": "qwen3:4b",
  "ollama_url": "http://127.0.0.1:11434",
  "host": "127.0.0.1",
  "port": 7331,
  "allow_commands": true,
  "command_timeout_seconds": 30,
  "allowed_roots": ["~"],
  "app_aliases": {
    "spotify": ["spotify"],
    "visual studio code": ["code"]
  }
}
```

Custom application values are executable argument lists. For an executable path containing spaces, keep the path as one JSON list item:

```json
"my editor": ["C:\\Program Files\\My Editor\\editor.exe"]
```

Environment variables override JSON settings:

| Variable | Purpose |
| --- | --- |
| `LOLA_MODEL` | Default Ollama model |
| `LOLA_OLLAMA_URL` | Ollama API URL |
| `LOLA_HOST` / `LOLA_PORT` | Local web server address |
| `LOLA_DATA_DIR` | Notes, tasks, and config directory |
| `LOLA_ALLOWED_ROOTS` | Allowed file roots, separated by the OS path separator |
| `LOLA_ALLOW_COMMANDS` | `true` or `false`; removes the command tool when false |
| `LOLA_COMMAND_TIMEOUT` | Maximum command runtime in seconds (up to 300) |
| `LOLA_OPEN_BROWSER` | Open the control panel automatically |

After changing configuration, restart Lola.

## Safety model

A computer assistant should not silently have unlimited access. Lola uses several layers:

1. **Local binding:** the server listens on `127.0.0.1` by default.
2. **No CORS:** unrelated websites cannot call its action API through a normal browser.
3. **Purpose-built tools:** opening an app or adding a task does not require a shell.
4. **File boundaries:** model-initiated file access is restricted to `allowed_roots`; common credential locations and secret filenames are blocked.
5. **Visible approval:** every shell command and arbitrary file write pauses before execution.
6. **Hard stops:** a small set of obviously catastrophic disk, shutdown, and recursive root-deletion commands is rejected even if requested.
7. **Bounded output:** reads, searches, command duration, and returned output are capped.

These controls reduce risk; they are not a security sandbox. An approved shell command runs with your normal user permissions. Read it carefully. Keep `allow_commands` false if you do not need terminal actions, and do not expose the Lola server directly to a network or the internet.

## Voice privacy

Spoken replies use the browser/operating system's speech synthesis. Push-to-talk uses the browser's Web Speech Recognition API when available. Depending on the browser and OS, speech recognition may use an online provider. Text chat and Ollama inference remain local, but use typed input if speech-provider privacy is important to you.

## Built-in app aliases

Lola ships with conservative aliases rather than launching arbitrary executables:

- **Windows:** Calculator, Notepad, Terminal, File Explorer, Task Manager, Settings, Paint
- **macOS:** Calculator, TextEdit, Terminal, Finder, Activity Monitor, System Settings, Calendar
- **Linux:** common calculators, editors, terminals, file managers, monitors, and settings panels when installed

Add other trusted applications through `app_aliases` in the config file.

## Development

```bash
python -m pip install -e ".[dev]"
pytest
ruff check .
```

Project layout:

```text
src/lola_helper/
├── agent.py       # Ollama tool loop and approvals
├── app.py         # local API and control-panel server
├── config.py      # JSON/environment configuration
├── ollama.py      # Ollama HTTP client
├── tools.py       # computer, file, note, task, and command tools
└── static/        # browser UI
```

Run without opening a browser during development:

```bash
lola-helper --no-browser
```

## Troubleshooting

### “Cannot reach Ollama”

Start the Ollama application or run `ollama serve`, then verify:

```bash
ollama list
```

### “Model is not installed”

Pull the model shown in the error, for example:

```bash
ollama pull qwen3:4b
```

### The model chats but does not operate tools well

Use a model that explicitly supports tool/function calling. Small models may choose the wrong alias or produce invalid arguments; a larger model usually improves reliability.

### An application is not known

Add a trusted executable under `app_aliases`, restart Lola, and ask it to list known applications.

### A path is blocked

The path must be inside one of `allowed_roots`. Credential-oriented paths remain blocked intentionally.

## License

MIT — see [LICENSE](LICENSE).
