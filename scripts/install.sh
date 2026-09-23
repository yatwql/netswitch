#!/usr/bin/env bash
# install.sh —— 一次性安装：检查环境 → 生成配置并自动探测 → 预检（可选 systemd）
# 零第三方运行时依赖（仅需 python3 标准库）
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$REPO_ROOT/src/python${PYTHONPATH:+:$PYTHONPATH}"
cd "$REPO_ROOT"

AS_ROOT=0
SUDO=""
if [ "$(id -u)" -eq 0 ]; then
  AS_ROOT=1
elif command -v sudo >/dev/null 2>&1; then
  SUDO="sudo"
fi

priv() {
  if [ "$AS_ROOT" = 1 ]; then
    "$@"
  elif [ -n "$SUDO" ]; then
    "$SUDO" "$@"
  else
    echo "[warn] 非 root 且无 sudo，跳过需提权操作：$*"
    return 1
  fi
}

WITH_SYSTEMD=0
[ "${1:-}" = "--with-systemd" ] && WITH_SYSTEMD=1

echo "==> [1/3] 检查运行环境（仅需 python3，无第三方依赖）"
command -v python3 >/dev/null 2>&1 || { echo "[error] 缺少 python3"; exit 1; }
echo "    python3 OK（$(python3 --version 2>&1)）"

echo "==> [2/3] 生成配置文件并自动探测本机网卡"
if [ ! -f data/config/config.json ]; then
  cp data/config/config.example.json data/config/config.json
  echo "    已生成 data/config/config.json"
else
  echo "    data/config/config.json 已存在，跳过复制"
fi
python3 -m netswitch.cli detect --write
python3 -m netswitch.cli seed-defaults   # rules 为空时写入缺省规则（如 github）

echo "==> [3/3] 高危操作前预检"
bash scripts/preflight.sh || true

if [ "$WITH_SYSTEMD" = 1 ]; then
  echo "==> 启用 systemd 开机自恢复"
  priv python3 -m netswitch.cli install-systemd --yes
fi

echo
echo "安装完成。日常使用二选一："
echo "  CLI : scripts/cli-netswitch.sh apply（写操作需 root 或 sudo）"
echo "  TUI : scripts/tui-netswitch.sh"
