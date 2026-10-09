#!/usr/bin/env bash
set -euo pipefail
deck_source_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec /usr/bin/python3 "$deck_source_root/scripts/install.py" "$@"
