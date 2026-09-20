#!/usr/bin/env bash
# Manual test runner for the Whoogle unit test suite.
#
# Usage:
#   ./test.sh                            # run all unit tests
#   ./test.sh test/test_file_utils.py    # run specific test file(s)
#   ./test.sh -k secret_key              # pass extra pytest args
#   PYTHON=python3.14 ./test.sh          # override the interpreter
set -euo pipefail
cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON:-python3}"

if [ "$#" -gt 0 ]; then
    exec "$PYTHON_BIN" -m pytest -v "$@"
else
    exec "$PYTHON_BIN" -m pytest -v test/
fi
