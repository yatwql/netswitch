#!/usr/bin/env bash
# check-version.sh —— 版本一致性：version.py 格式 + README 版本号一致
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

VER=$(python3 -c "import sys; sys.path.insert(0,'src/python'); from netswitch import version; print(version.__version__)")
fail=0

# 1) 格式：X.Y 或 X.Y-dev
if ! printf '%s' "$VER" | grep -Eq '^[0-9]+\.[0-9]+(-dev)?$'; then
  echo "[FAIL] version.py 版本号格式非法: '$VER'（应形如 0.1 或 0.2-dev）"
  fail=1
fi

# 2) README 的 “版本：<ver>” 必须与 version.py 一致（后面不能紧跟版本字符）
if ! grep -Eq "^> 版本：${VER}([^-0-9.]|$)" README.md; then
  echo "[FAIL] README.md 的“版本：”与 version.py 不一致（应为 $VER）"
  fail=1
fi

if [ "$fail" -eq 0 ]; then
  echo "[PASS] 版本一致性通过（$VER）"
fi
exit $fail
