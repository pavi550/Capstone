#!/usr/bin/env bash
(
    script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || exit
    python -X utf8 -u "$script_dir/launch.py" "$@"
)
