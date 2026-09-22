#!/usr/bin/env bash
# tui-netswitch.sh —— TUI 入口（调用 src/python 中的 netswitch.tui；curses，零依赖）
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$REPO_ROOT/src/python${PYTHONPATH:+:$PYTHONPATH}"
cd "$REPO_ROOT"
exec python3 -m netswitch.tui "$@"
