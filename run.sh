#!/usr/bin/env bash
# Runs Snipping Tool from this folder. Options are passed on, e.g. ./run.sh --snip
DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
# The system Python, which is where apt puts the GTK bindings.
PYTHON=/usr/bin/python3
[[ -x "$PYTHON" ]] || PYTHON=python3
exec "$PYTHON" "$DIR/snipping_tool.py" "$@"
