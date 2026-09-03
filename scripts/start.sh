#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

if [ ! -x .venv/bin/python ]; then
  echo "Fallen is not installed yet. Running the installer..."
  "$ROOT/scripts/install.sh"
fi

exec .venv/bin/python -m fallen_helper "$@"
