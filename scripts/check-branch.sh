#!/usr/bin/env bash
# check-branch.sh —— 分支策略：禁止在 master/main 上直接提交（开发请在 dev）
#   - 合并提交（release.sh 使用 git merge）放行
#   - 例外：NETSWITCH_ALLOW_MASTER=1
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

git rev-parse --git-dir >/dev/null 2>&1 || { echo "[SKIP] 非 git 仓库"; exit 0; }
branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
[ -n "$branch" ] || { echo "[SKIP] 无法确定当前分支"; exit 0; }

if git rev-parse -q --verify MERGE_HEAD >/dev/null 2>&1; then
  echo "[PASS] 合并提交（放行，$branch）"
  exit 0
fi

if [ "${NETSWITCH_ALLOW_MASTER:-0}" = "1" ]; then
  echo "[PASS] NETSWITCH_ALLOW_MASTER=1（允许在 $branch 提交）"
  exit 0
fi

if [ "$branch" = "master" ] || [ "$branch" = "main" ]; then
  echo "[FAIL] 禁止在 $branch 上直接提交：日常开发请在 dev 分支；发布正式版请用 scripts/release.sh"
  exit 1
fi
echo "[PASS] 分支策略通过（$branch）"
