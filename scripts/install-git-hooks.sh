#!/usr/bin/env bash
# install-git-hooks.sh —— 启用版本化的 git 钩子（scripts/git-hooks/）
#   设置 core.hooksPath 指向仓库内的 git-hooks 目录，钩子随代码一起版本化。
#   卸载：git config --unset core.hooksPath
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

chmod +x scripts/git-hooks/* scripts/check-*.sh 2>/dev/null || true
git config core.hooksPath scripts/git-hooks
echo "已启用 git 钩子：core.hooksPath = scripts/git-hooks"
echo "钩子："
ls -1 scripts/git-hooks
echo
echo "提示：如需改用 pre-commit 框架，请先执行 git config --unset core.hooksPath"
