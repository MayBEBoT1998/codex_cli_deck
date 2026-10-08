#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

if [[ "$(uname -s)" != Darwin ]]; then
  echo '此脚本仅用于 macOS；Ubuntu 安装步骤见 INSTALL.md。' >&2
  exit 1
fi
if ! command -v brew >/dev/null 2>&1; then
  echo '请先从 https://brew.sh 安装 Homebrew，再重新运行此脚本。' >&2
  exit 1
fi

brew install python3 pygobject3 gtk+3 vte3 librsvg bash
deck_brew_prefix="$(brew --prefix)"
"$deck_brew_prefix/bin/python3" -m venv --system-site-packages .venv
.venv/bin/python -m pip install -r requirements-macos.txt
.venv/bin/python -m codex_deck --check-deps
echo '安装完成。执行 bash launch.sh 启动，或 bash launch.sh --shell 只打开终端。'
