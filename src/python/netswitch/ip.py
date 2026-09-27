"""iproute2 命令的 JSON 解析与命令构造、物理网卡识别。"""
from __future__ import annotations

import functools
import json
import os
import re
import shutil
from typing import Any, Dict, List, Optional

from . import exec as ex

# 虚拟接口前缀/名称（排除）
VIRTUAL_PREFIXES = (
    "lo", "veth", "br-", "docker", "lzc-", "virbr", "tun", "tap", "heiyu",
    "wg", "ppp", "dummy", "macvlan", "macvtap", "tailscale", "zt", "nordlynx",
    "vnet", "ifb", "nflog", "gre", "sit", "ip6tnl",
)

# 物理网卡常见命名前缀
PHYSICAL_PREFIXES = ("en", "eth", "wl", "wlan", "ww")

# 策略路由的 ip rule 优先级基准（需 < 32766，见 RULE_PREF_MAX_COUNT）
RULE_PREF_BASE = 20000
# 单次运行最多分配的 ip rule 优先级个数（保证 base + count < 32766）
RULE_PREF_MAX_COUNT = 12000

# 本程序允许使用的自定义路由表号区间。
# 1..252 可用；253/254/255 是内核保留的 default/main/local，
# 误用会导致 `ip route flush table 254` 清空主路由表。
TABLE_ID_MIN = 200
TABLE_ID_MAX = 252

# mainroute 后端给主表明细路由打的 proto 标记（便于判定/清理）。
# 注意：iproute2 只接受 1..255 的数值 proto，200 在合法范围内。
MAINROUTE_PROTO = 200


def _j(args: List[str]) -> List[str]:
    return ["ip", "-j", *args]


def link_list(dry_run: bool = False) -> List[Dict[str, Any]]:
    out = ex.run(_j(["link", "show"]), dry_run=dry_run, readonly=True).stdout
    return json.loads(out or "[]")


def addr_list(family: int = 4, dry_run: bool = False) -> List[Dict[str, Any]]:
    fam = ["-4"] if family == 4 else ["-6"]
    out = ex.run(_j(fam + ["addr", "show"]), dry_run=dry_run, readonly=True).stdout
    return json.loads(out or "[]")


def route_list(dry_run: bool = False) -> List[Dict[str, Any]]:
    out = ex.run(_j(["route", "show"]), dry_run=dry_run, readonly=True).stdout
    return json.loads(out or "[]")


def rule_list(dry_run: bool = False) -> List[Dict[str, Any]]:
    out = ex.run(_j(["rule", "show"]), dry_run=dry_run, readonly=True).stdout
    return json.loads(out or "[]")


def has_device(name: str) -> bool:
    """sysfs 中是否存在 `device` 链接：真实 PCI/USB 网卡才有。

    比命名前缀可靠得多（VLAN 子接口、bond、bridge 等都没有）。
    容器/受限环境中读不到 sysfs 时返回 False（调用方回退到命名前缀判断）。
    """
    try:
        return os.path.exists(f"/sys/class/net/{name}/device")
    except OSError:  # pragma: no cover - 极端路径长度等
        return False


def is_physical(name: str, link: Optional[Dict[str, Any]] = None) -> bool:
    """判定是否为物理网卡。

    顺序：虚拟前缀/名称排除 → VLAN 子接口排除 → sysfs 确认为真实设备 →
    命名前缀 → `link_type == ether` 且无 master/link-netns 兜底。
    bond0 之类没有 `device` 链接的聚合设备会走兜底分支（仍视为可用出口），
    但其 slave 因带 master 被排除。
    """
    if not name:
        return False
    if name in VIRTUAL_PREFIXES or name.startswith(VIRTUAL_PREFIXES):
        return False
    if "." in name:            # enp2s0.100 等 VLAN / 别名子接口
        return False
    if has_device(name):
        return True
    if name.startswith(PHYSICAL_PREFIXES):
        return True
    # 兜底：命名前缀不匹配但 link 类型为 ether 且无 master / link-netns
    if link:
        if link.get("link_type") != "ether":
            return False
        if link.get("master") or link.get("link_netnsid") is not None or link.get("link"):
            return False
        return True
    return False


def interface_type(name: str) -> str:
    """有线/无线：存在 /sys/class/net/<if>/wireless 视为无线。"""
    if os.path.isdir(f"/sys/class/net/{name}/wireless"):
        return "wireless"
    return "wired"


def parse_ssid(iw_link_output: str) -> Optional[str]:
    """从 `iw dev <if> link` 输出解析 SSID（未连接返回 None）。"""
    for line in (iw_link_output or "").splitlines():
        s = line.strip()
        if s.startswith("SSID:"):
            return s.split(":", 1)[1].strip() or None
    return None


def wireless_ssid(name: str) -> Optional[str]:
    """无线网卡当前 SSID；未连接或工具缺失时返回 None。

    优先 `iw dev <if> link`，回退 `iwgetid -r <if>`；两者都无则不报错。
    """
    if shutil.which("iw"):
        out = ex.run(["iw", "dev", name, "link"], check=False, readonly=True).stdout
        ssid = parse_ssid(out)
        if ssid:
            return ssid
    if shutil.which("iwgetid"):
        out = ex.run(["iwgetid", "-r", name], check=False, readonly=True).stdout
        return (out or "").strip() or None
    return None


def link_state(name: str, link: Dict[str, Any]) -> str:
    """连接状态：connected / no-carrier / down。"""
    operstate = str(link.get("operstate", "UNKNOWN")).upper()
    flags = link.get("flags", []) or []
    if "NO-CARRIER" in flags:
        return "no-carrier"
    if operstate == "UP":
        return "connected"
    return "down"


def admin_up(link: Dict[str, Any]) -> bool:
    """管理状态：flags 含 `UP`（IFF_UP）表示接口未被关闭。"""
    return "UP" in (link.get("flags") or [])


@functools.lru_cache(maxsize=1)
def policy_routing_supported() -> Optional[bool]:
    """策略路由（自定义路由表 + ip rule）是否可用。

    - True：可用；False：内核/环境不支持；None：无法判断（非 root）。
    - **只读探测**：不支持多路由表的内核连 `ip rule show` 都会返回
      `RTNETLINK answers: Operation not supported`（内核缺
      CONFIG_IP_MULTIPLE_TABLES，或受限容器/gVisor）；探测不再增删路由，
      因此 dry-run 也不会改动系统，并发运行也不会互相干扰。
    - 非 root 时返回 None：调用方按“未知”处理（不据此做破坏性操作）。
    """
    if os.geteuid() != 0:
        return None
    try:
        r = ex.run(["ip", "-j", "rule", "show"], check=False, readonly=True)
    except Exception:  # noqa: BLE001
        return True
    if r.returncode == 0:
        return True
    if "not supported" in (r.stderr or "").lower():
        return False
    # 其它错误（权限、临时故障）保守视为支持：真正的写操作失败时
    # routing._run_or_hint 会给出明确提示。
    return True


def nft_set_name(rule_name: str) -> str:
    """规则名 → nft set 名（仅保留合法字符）。"""
    return re.sub(r"[^A-Za-z0-9_]", "_", rule_name) + "_v4"


def rule_pref(index: int) -> int:
    """第 index 条规则/条目的 ip rule 优先级（唯一）。

    超出 RULE_PREF_MAX_COUNT 会撞上内核默认规则（pref 32766），必须提前拒绝。
    """
    if not 0 <= index < RULE_PREF_MAX_COUNT:
        raise ValueError(
            f"ip rule 数量超出上限（{RULE_PREF_MAX_COUNT} 条）："
            f"请减少规则/CIDR 数量，或改用 nftables 后端"
        )
    return RULE_PREF_BASE + index


def subnet_of(ip_with_prefix: str) -> str:
    """192.168.1.7/24 → 192.168.1.0/24"""
    import ipaddress

    net = ipaddress.ip_interface(ip_with_prefix).network
    return str(net)
