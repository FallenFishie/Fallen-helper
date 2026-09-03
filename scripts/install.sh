#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

if command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON=python
else
  echo "Python 3.10 or newer is required: https://www.python.org/downloads/" >&2
  exit 1
fi

"$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' || {
  echo "Python 3.10 or newer is required." >&2
  exit 1
}

if [ ! -d .venv ]; then
  echo "Creating local Python environment..."
  "$PYTHON" -m venv .venv
fi

. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .

printf '\nFallen Helper is installed.\n'
if ! command -v ollama >/dev/null 2>&1; then
  echo "Ollama was not found. Install it from https://ollama.com/download"
else
  echo "Ollama detected. If needed, run: ollama pull qwen3:4b"
fi
echo "Start Fallen with: ./scripts/start.sh"
