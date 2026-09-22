#!/usr/bin/env bash
# check.sh —— 提交前完整核对：测试全部通过 + 文档一致性
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

bash scripts/run-test.sh
bash scripts/check-docs.sh

echo
echo "[PASS] 提交门槛核对通过：测试全绿 + 文档一致"
