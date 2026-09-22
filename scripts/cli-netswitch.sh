#!/usr/bin/env bash
# cli-netswitch.sh —— CLI 统一入口（调用 src/python 中的 netswitch.cli）
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$REPO_ROOT/src/python${PYTHONPATH:+:$PYTHONPATH}"
cd "$REPO_ROOT"
exec python3 -m netswitch.cli "$@"
