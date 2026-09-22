#!/usr/bin/env bash
# run-test.sh —— 运行测试（无需 root；pytest.ini 已配置 testpaths=src/test）
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$REPO_ROOT/src/python${PYTHONPATH:+:$PYTHONPATH}"
cd "$REPO_ROOT"
exec python3 -m pytest -q "$@"
