"""Command-line launcher for Fallen Helper."""

from __future__ import annotations

import argparse
import threading
import webbrowser
from dataclasses import replace

import uvicorn

from .app import create_app
from .config import Settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fallen-helper",
        description="Start the local Fallen AI assistant control panel.",
    )
    parser.add_argument("--host", help="Listening host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, help="Listening port (default: 7331)")
    parser.add_argument("--model", help="Default Ollama model")
    parser.add_argument("--ollama-url", help="Ollama API base URL")
    parser.add_argument(
        "--no-browser", action="store_true", help="Do not open the control panel automatically"
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    settings = Settings.load()
    settings = replace(
        settings,
        host=args.host or settings.host,
        port=args.port or settings.port,
        model=args.model or settings.model,
        ollama_url=(args.ollama_url or settings.ollama_url).rstrip("/"),
        open_browser=False if args.no_browser else settings.open_browser,
    )
    if not (1 <= settings.port <= 65535):
        raise SystemExit("Port must be between 1 and 65535")

    browser_host = "127.0.0.1" if settings.host in {"0.0.0.0", "::"} else settings.host
    local_url = f"http://{browser_host}:{settings.port}"
    if settings.open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(local_url, new=2)).start()

    print("\n  FALLEN // Local intelligence online")
    print(f"  Control panel: {local_url}")
    print(f"  Ollama:       {settings.ollama_url}")
    print(f"  Model:        {settings.model}")
    print("  Press Ctrl+C to stop.\n")

    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
