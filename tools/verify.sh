#!/usr/bin/env bash
# amber's verification pass. Everything that can be checked without a human
# looking at Resolume.
set -euo pipefail

# `set -o pipefail` plus `grep -q` in a pipeline exits non-zero when grep closes
# the pipe early, so no `| grep -q` below. See the fleet's pipefail note.

cd "$(dirname "$0")/.."

PYTHON=".venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
    echo "creating .venv (homebrew python is PEP 668 managed)"
    python3 -m venv .venv
    .venv/bin/pip install --quiet pytest
fi

echo "== capability report =="
./tools/amber doctor || echo "(doctor reported gaps -- tests will skip accordingly)"

echo
echo "== fixtures =="
"$PYTHON" tests/make_fixtures.py

echo
echo "== tests =="
"$PYTHON" -m pytest tests/ -q

echo
echo "verify.sh: OK"
