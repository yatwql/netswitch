"""iproute2 命令的 JSON 解析与命令构造、物理网卡识别。"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from . import exec as ex

# 虚拟接口前缀/名称（排除）
VIRTUAL_PREFIXES = (
    "lo", "veth", "br-", "docker", "lzc-", "virbr", "tun", "tap", "heiyu",
)

# 物理网卡常见命名前缀
PHYSICAL_PREFIXES = ("en", "eth", "wl", "wlan", "ww")

# 策略路由的 ip rule 优先级基准（需 < 32766）
RULE_PREF_BASE = 20000


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


def is_physical(name: str, link: Optional[Dict[str, Any]] = None) -> bool:
    """判定是否为物理网卡。"""
    if not name or name in VIRTUAL_PREFIXES:
        return False
    if name.startswith(VIRTUAL_PREFIXES):
        return False
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


def nft_set_name(rule_name: str) -> str:
    """规则名 → nft set 名（仅保留合法字符）。"""
    return re.sub(r"[^A-Za-z0-9_]", "_", rule_name) + "_v4"


def rule_pref(index: int) -> int:
    """第 index 条规则/条目的 ip rule 优先级（唯一）。"""
    return RULE_PREF_BASE + index


def subnet_of(ip_with_prefix: str) -> str:
    """192.168.1.7/24 → 192.168.1.0/24"""
    import ipaddress

    net = ipaddress.ip_interface(ip_with_prefix).network
    return str(net)
