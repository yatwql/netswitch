#!/usr/bin/env bash
#
# preflight.sh — 网卡高危操作前的可行性预检（只读，不修改任何系统状态）
#
# 用途：在真正执行 iface down / metric / rule apply / apply 等会改动网络的高危操作之前，
#       先确认环境与安全性是否满足要求，避免误操作导致断网。
#
# 用法：
#   sudo scripts/preflight.sh                    # 自动探测物理网卡
#   sudo scripts/preflight.sh enp2s0 wlp129s0    # 显式指定网卡（空格分隔）
#
# 退出码：
#   0 = 通过（无 FAIL）
#   1 = 存在 FAIL（阻断项，建议不要继续高危操作）
#   2 = 无 FAIL 但存在 WARN（可继续，但请关注告警）
#
set -u

# ---------- 颜色与计数 ----------
if [ -t 1 ]; then
  C_GREEN=$'\e[32m'; C_RED=$'\e[31m'; C_YELLOW=$'\e[33m'; C_BOLD=$'\e[1m'; C_RESET=$'\e[0m'
else
  C_GREEN=""; C_RED=""; C_YELLOW=""; C_BOLD=""; C_RESET=""
fi
N_PASS=0; N_WARN=0; N_FAIL=0

ok()   { printf '  %s[PASS]%s %s\n' "$C_GREEN" "$C_RESET" "$*"; N_PASS=$((N_PASS+1)); }
warn() { printf '  %s[WARN]%s %s\n' "$C_YELLOW" "$C_RESET" "$*"; N_WARN=$((N_WARN+1)); }
fail() { printf '  %s[FAIL]%s %s\n' "$C_RED" "$C_RESET" "$*"; N_FAIL=$((N_FAIL+1)); }
sec()  { printf '\n%s== %s ==%s\n' "$C_BOLD" "$*" "$C_RESET"; }

# 仓库根目录（脚本位于 scripts/ 下）
REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
CONFIG="$REPO_ROOT/data/config/config.json"

# ---------- 1. 基础环境 ----------
sec "1. 基础环境"

if [ "$(id -u)" -eq 0 ]; then
  ok "以 root 运行"
else
  warn "非 root：预检只读可运行，但实际网卡/路由操作需 root"
fi

for cmd in ip python3; do
  if command -v "$cmd" >/dev/null 2>&1; then
    ok "命令可用: $cmd ($(command -v "$cmd"))"
  else
    fail "缺少必需命令: $cmd"
  fi
done

if command -v nft >/dev/null 2>&1; then
  if nft list ruleset >/dev/null 2>&1; then
    ok "nft 可用（策略路由将使用 nftables 后端）"
  else
    warn "nft 存在但无法读取 ruleset（可能无权限/未初始化），将回退 iprule 后端"
  fi
else
  warn "无 nft 命令，策略路由将回退 iprule 后端"
fi

# ---------- 2. 物理网卡探测 ----------
sec "2. 物理网卡探测"

if [ "$#" -gt 0 ]; then
  IFACES="$*"
  ok "使用显式指定网卡: $IFACES"
else
  IFACES=$(ip -o link show 2>/dev/null | awk -F': ' '{print $2}' \
             | grep -E '^(en|eth|wl|wlan|ww)' | sort -u | tr '\n' ' ')
  IFACES=$(echo "$IFACES" | xargs)
  if [ -n "$IFACES" ]; then
    ok "自动探测到物理网卡: $IFACES"
  else
    fail "未探测到物理网卡（请显式传入网卡名，如: $0 enp2s0 wlp129s0）"
  fi
fi

# ---------- 3. 网卡状态 / 网关 / metric ----------
sec "3. 网卡状态 / 网关 / metric"

IFACE_COUNT=0
UP_WITH_DEFAULT=0

for if in $IFACES; do
  [ -z "$if" ] && continue
  if ! ip link show "$if" >/dev/null 2>&1; then
    fail "网卡 $if 不存在"
    continue
  fi
  IFACE_COUNT=$((IFACE_COUNT+1))

  state=$(ip -o link show "$if" 2>/dev/null | grep -oE 'state (UP|DOWN|UNKNOWN)' | awk '{print $2}')
  state=${state:-UNKNOWN}
  ip4=$(ip -o -4 addr show "$if" 2>/dev/null | awk '{print $4}' | head -1)
  ip4=${ip4:-无IPv4}

  rt=$(ip route show default dev "$if" 2>/dev/null | head -1)
  gw=""; metric=""
  if [ -n "$rt" ]; then
    gw=$(echo "$rt" | awk '{for(i=1;i<=NF;i++) if($i=="via"){print $(i+1); exit}}')
    metric=$(echo "$rt" | awk '{for(i=1;i<=NF;i++) if($i=="metric"){print $(i+1); exit}}')
  fi

  printf '  [网卡] %-12s 状态=%s  IPv4=%s\n' "$if" "$state" "$ip4"

  if [ "$state" = "UP" ]; then
    ok "$if: 链路 UP"
  else
    warn "$if: 链路非 UP（state=$state）"
  fi

  if [ -n "$gw" ]; then
    ok "$if: 默认路由网关=$gw metric=${metric:-无}"
    UP_WITH_DEFAULT=$((UP_WITH_DEFAULT+1))
  else
    warn "$if: 无默认路由（该网卡当前不承载外网流量）"
  fi
done

# ---------- 4. 安全校验 ----------
sec "4. 安全校验"

if [ "$IFACE_COUNT" -ge 2 ]; then
  ok "物理网卡数量 = $IFACE_COUNT（≥2，可安全切换）"
else
  fail "物理网卡数量 = $IFACE_COUNT（<2，无法保证切换后的连通性）"
fi

if [ "$UP_WITH_DEFAULT" -ge 1 ]; then
  ok "至少一张网卡 UP 且带默认路由（外网可达）"
else
  fail "没有任何一张网卡 UP 且带默认路由（外网将不可达）"
fi

# ---------- 5. 逐网卡关闭风险评估 ----------
sec "5. 逐网卡关闭风险评估"

for if in $IFACES; do
  [ -z "$if" ] && continue
  this_rt=$(ip route show default dev "$if" 2>/dev/null | head -1)
  if [ -z "$this_rt" ]; then
    ok "关闭 $if 风险低（其本身无默认路由）"
    continue
  fi
  others_ok=0
  for o in $IFACES; do
    [ "$o" = "$if" ] && continue
    o_state=$(ip -o link show "$o" 2>/dev/null | grep -oE 'state (UP|DOWN|UNKNOWN)' | awk '{print $2}')
    o_rt=$(ip route show default dev "$o" 2>/dev/null | head -1)
    if [ "$o_state" = "UP" ] && [ -n "$o_rt" ]; then
      others_ok=$((others_ok+1))
    fi
  done
  if [ "$others_ok" -ge 1 ]; then
    ok "关闭 $if 后仍有 $others_ok 张网卡可承载外网，安全"
  else
    fail "关闭 $if 会导致外网不可达（无其它可用网卡），属高危操作"
  fi
done

# ---------- 6. 策略路由后端与内核参数 ----------
sec "6. 策略路由后端与内核参数"

if command -v nft >/dev/null 2>&1 && nft list ruleset >/dev/null 2>&1; then
  ok "策略路由后端：nftables 可用"
else
  warn "策略路由后端：将回退 iprule（纯 ip rule，无需 nft）"
fi

fwd=$(sysctl -n net.ipv4.ip_forward 2>/dev/null)
case "$fwd" in
  1) ok "ip_forward=1（容器转发已开启）" ;;
  0) warn "ip_forward=0（容器对外转发可能被禁用）" ;;
  *) warn "无法读取 ip_forward" ;;
esac

rp=$(sysctl -n net.ipv4.conf.all.rp_filter 2>/dev/null)
case "$rp" in
  0|2) ok "rp_filter=$rp（宽松，容器跨网卡转发安全）" ;;
  1) warn "rp_filter=1（严格模式，容器经网桥进/他卡出可能被丢弃）" ;;
  *) warn "无法读取 rp_filter" ;;
esac

# ---------- 7. 流量目标源（CIDR 源）可达性 ----------
sec "7. 流量目标源（CIDR 源）可达性"

# 从 JSON 配置中读取所有 "url": "https://..."（通用，不硬编码）；无则跳过
URLS=$(grep -oE '"url"[[:space:]]*:[[:space:]]*"https?://[^"]+"' "$CONFIG" 2>/dev/null \
         | grep -oE 'https?://[^"]+' | sort -u)

if command -v curl >/dev/null 2>&1; then
  if [ -n "$URLS" ]; then
    for u in $URLS; do
      code=$(curl -sS --max-time 8 -o /dev/null -w '%{http_code}' "$u" 2>/dev/null)
      case "$code" in
        200) ok "CIDR 源可达: $u" ;;
        *)   warn "CIDR 源不可达: $u（HTTP ${code:-超时}）；可依赖缓存或手动 extra" ;;
      esac
    done
  else
    warn "配置中未发现 CIDR 源 url，跳过可达性检查"
  fi
else
  warn "无 curl，跳过 CIDR 源可达性检查"
fi

# ---------- 汇总 ----------
sec "汇总"

echo "  PASS=$N_PASS  WARN=$N_WARN  FAIL=$N_FAIL"
echo
if [ "$N_FAIL" -gt 0 ]; then
  echo "  结论：存在阻断项（FAIL），建议先处理后再执行高危网卡操作。"
  exit 1
elif [ "$N_WARN" -gt 0 ]; then
  echo "  结论：无阻断项，但有告警（WARN），请确认后可继续。"
  exit 2
else
  echo "  结论：预检全部通过，可以安全执行。"
  exit 0
fi
