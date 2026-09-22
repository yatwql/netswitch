#!/usr/bin/env bash
# check-docs.sh —— 文档一致性基础核对（提交门槛的一部分）
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

fail=0

# 仓库根 README 必须存在
if [ ! -f README.md ]; then
  echo "[FAIL] 缺失 README.md（仓库根）"
  fail=1
fi

# docs/ 下必备文档
required="user-manuals.md requirements.md technical.md plan.md test-plan.md changelog.md review-findings.md folder.md faq.md"
for f in $required; do
  if [ ! -f "docs/$f" ]; then
    echo "[FAIL] 缺失 docs/$f"
    fail=1
  fi
done

# README 索引里的 docs/ 链接必须存在
for f in $(grep -oE '\(docs/[a-z-]+\.md\)' README.md | tr -d '()'); do
  if [ ! -f "$f" ]; then
    echo "[FAIL] README 索引指向缺失文件: $f"
    fail=1
  fi
done

if [ "$fail" -eq 0 ]; then
  echo "[PASS] 文档核对通过"
else
  echo "[FAIL] 文档核对未通过，请更新 README / docs"
fi
exit $fail
