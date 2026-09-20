#!/usr/bin/env bash
# Whoogle unit test runner (manual testing helper).
#
# Usage:
#   ./test.sh                 Run the entire test suite
#   ./test.sh -m              Interactive menu: pick a single test file
#   ./test.sh -l              List available unit test files
#   ./test.sh <test_file>     Run one test file, e.g. ./test.sh test/test_misc.py
#   ./test.sh <node> -k ...   Any extra args are forwarded to pytest, e.g.
#                             ./test.sh test/test_fileio.py -k atomic -v
#
# A virtualenv is used automatically when ./.venv exists.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# --- Pick a Python environment that actually has pytest ---------------------
PYTEST=()
CANDIDATES=()
[[ -x ".venv/bin/python" ]] && CANDIDATES+=(".venv/bin/python -m pytest")
CANDIDATES+=("python3 -m pytest" "pytest")
for candidate in "${CANDIDATES[@]}"; do
    if $candidate --version >/dev/null 2>&1; then
        # shellcheck disable=SC2206
        PYTEST=( $candidate )
        break
    fi
done
if [[ ${#PYTEST[@]} -eq 0 ]]; then
    echo "error: pytest was not found in the venv or system Python." >&2
    echo "Install the test dependencies first, e.g.:" >&2
    echo "  python3 -m pip install -r requirements.txt pytest" >&2
    echo "or create the venv:  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest" >&2
    exit 1
fi
echo "Using test runner: ${PYTEST[*]}"

TEST_FILES=(test/test_*.py)

list_tests() {
    local i=1
    for file in "${TEST_FILES[@]}"; do
        printf '  %2d) %s\n' "$i" "$file"
        i=$((i + 1))
    done
}

choose_test() {
    # Interactive UI goes to stderr so command substitution only captures
    # the selected file path on stdout.
    echo "Available unit test files:" >&2
    list_tests >&2
    echo >&2
    local count=${#TEST_FILES[@]}
    local choice
    while true; do
        read -r -p "Select a number [1-${count}] (q to quit): " choice
        if [[ "$choice" == "q" || "$choice" == "Q" ]]; then
            exit 0
        fi
        if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= count )); then
            echo "${TEST_FILES[$((choice - 1))]}"
            return
        fi
        echo "Invalid selection, please try again." >&2
    done
}

case "${1:-}" in
    -h|--help)
        sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
        exit 0
        ;;
    -l|--list)
        list_tests
        exit 0
        ;;
    -m|--menu)
        shift
        selected="$(choose_test)"
        echo ">>> ${PYTEST[*]} $selected $*"
        "${PYTEST[@]}" "$selected" "$@"
        ;;
    *)
        if [[ $# -eq 0 ]]; then
            echo ">>> ${PYTEST[*]}"
            "${PYTEST[@]}"
        else
            echo ">>> ${PYTEST[*]} $*"
            "${PYTEST[@]}" "$@"
        fi
        ;;
esac
