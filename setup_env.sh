#!/usr/bin/env bash
set -eu
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
for interpreter in python3 python; do
    if command -v "$interpreter" >/dev/null 2>&1 &&
        "$interpreter" -c 'import sys; sys.exit(sys.version_info < (3, 11))' >/dev/null 2>&1; then
        exec "$interpreter" "$root/setup_env.py" "$@"
    fi
done
printf '%s\n' \
    'Install Python 3.11 or newer, then open a new terminal and rerun setup.' \
    'macOS or Windows installer: https://www.python.org/downloads/' \
    'Windows: enable Add Python to PATH in the installer.' \
    'macOS with Homebrew: brew install python' \
    'Debian/Ubuntu: sudo apt update && sudo apt install python3 python3-venv' >&2
exit 1
