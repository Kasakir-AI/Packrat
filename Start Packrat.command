#!/bin/bash
# macOS / Linux: start Packrat from the Python download (double-click it in Finder, or run it in Terminal).
# The first start makes a private .venv next to this file and installs Packrat's packages into it.
cd "$(dirname "$0")" || exit 1

PY=""
for c in python3.14 python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    PY="$c"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "Packrat needs Python 3.11 or newer."
  echo "Get it from https://www.python.org/downloads/macos/ (or: brew install python), then start Packrat again."
  read -r -p "Press Enter to close."
  exit 9
fi

if [ ! -x .venv/bin/python ] || [ app/requirements.txt -nt .venv/.packrat-installed ]; then
  echo "Installing Packrat's packages (the first time about 250 MB, a few minutes)..."
  { [ -x .venv/bin/python ] || "$PY" -m venv .venv; } \
    && .venv/bin/python -m pip install -q --upgrade pip \
    && .venv/bin/python -m pip install -q -r app/requirements.txt \
    && touch .venv/.packrat-installed \
    || { echo "The install failed (see above). Check the internet connection and try again."; read -r -p "Press Enter to close."; exit 1; }
fi

cd app && exec ../.venv/bin/python -m packrat gui "$@"
