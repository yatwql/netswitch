#!/usr/bin/env bash
# check-docs.sh —— 文档一致性核对（提交门槛的一部分）
#   1) README 与 docs/ 必备文件存在
#   2) README 索引里的 docs/ 链接存在
#   3) docs/folder.md 覆盖 src/、scripts/、data/config/ 与工程文件
#   4) 本次变更含 src/ 或 scripts/ 时，必须同时更新 docs/changelog.md（AGENTS.md）
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

fail=0

# 1) 仓库根 README 必须存在
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

# 2) README 索引里的 docs/ 链接必须存在
for f in $(grep -oE '\(docs/[a-z-]+\.md\)' README.md | tr -d '()'); do
  if [ ! -f "$f" ]; then
    echo "[FAIL] README 索引指向缺失文件: $f"
    fail=1
  fi
done

# 3) docs/folder.md 必须覆盖代码/脚本/配置/工程文件（新增文件时同步更新）
while IFS= read -r f; do
  if ! grep -qF -- "$(basename "$f")" docs/folder.md; then
    echo "[FAIL] docs/folder.md 未提及: $f"
    fail=1
  fi
done < <(find src scripts data/config -type f \
           \( -name '*.py' -o -name '*.sh' -o -name '*.json' -o -name '*.service' \
              -o -path 'scripts/git-hooks/*' \) \
           -not -path '*/__pycache__/*' \
           -not -name 'config.json' -not -name 'state.json' \
           -not -name 'cache-*.json' | sort)

for f in pyproject.toml Makefile pytest.ini requirements-dev.txt \
         .pre-commit-config.yaml .github/workflows/ci.yml AGENTS.md README.md; do
  if ! grep -qF -- "$(basename "$f")" docs/folder.md; then
    echo "[FAIL] docs/folder.md 未提及: $f"
    fail=1
  fi
done

# 4) 代码变更必须同步 changelog（AGENTS.md 提交门槛）
if git rev-parse --git-dir >/dev/null 2>&1; then
  changed=$({ git diff --cached --name-only; git diff --name-only; } | sort -u || true)
  code_changed=$(printf '%s\n' "$changed" | grep -E '^(src|scripts)/' || true)
  if [ -n "$code_changed" ] \
     && ! printf '%s\n' "$changed" | grep -qx 'docs/changelog.md'; then
    echo "[FAIL] 有 src/ 或 scripts/ 变更，但 docs/changelog.md 未同步更新"
    echo "       涉及：$(printf '%s ' $code_changed)"
    fail=1
  fi
fi

if [ "$fail" -eq 0 ]; then
  echo "[PASS] 文档核对通过"
else
  echo "[FAIL] 文档核对未通过，请更新 README / docs"
fi
exit $fail
