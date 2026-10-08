#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ -n "${CODEX_DECK_PYTHON:-}" ]]; then
  deck_python="$CODEX_DECK_PYTHON"
elif [[ "$(uname -s)" == Darwin ]]; then
  deck_python="$PWD/.venv/bin/python"
  if [[ ! -x "$deck_python" ]]; then
    echo '请先执行 bash scripts/setup_macos.sh 安装 macOS 依赖。' >&2
    exit 1
  fi
else
  # Ubuntu's GI packages belong to the system Python, not a random PATH/venv.
  deck_python=/usr/bin/python3
fi
exec "$deck_python" -m codex_deck "$@"
