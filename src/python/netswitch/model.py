"""数据模型。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Interface:
    """运行时的网卡快照（探测结果）。"""

    name: str
    type: str = "wired"            # wired | wireless
    ip: Optional[str] = None       # 形如 192.168.1.7/24
    ip6: Optional[str] = None      # 形如 2001:db8::7/64（仅全局作用域地址）
    state: str = "down"            # connected | no-carrier | down
    gateway: Optional[str] = None
    gateway6: Optional[str] = None  # IPv6 网关（常为 fe80::1 链路本地）
    metric: Optional[int] = None
    admin_up: bool = False         # 管理状态（IFF_UP）
    ssid: Optional[str] = None     # 无线网卡当前 SSID
    device_confirmed: bool = False  # sysfs 中有 device 节点（真实 PCI/USB 设备）


@dataclass
class IfaceCfg:
    """配置中的单张物理网卡。"""

    name: str
    gateway: Optional[str] = None  # IPv4 网关；缺省自动探测
    gateway6: Optional[str] = None  # IPv6 网关；缺省自动探测
    metric: Optional[int] = None   # None = apply 时不调整
    type: Optional[str] = None     # 展示用


@dataclass
class MetricsCfg:
    preferred: int = 100
    fallback: int = 600


@dataclass
class RoutingCfg:
    backend: str = "auto"          # auto | nftables | iprule | mainroute
    nft_table: str = "netswitch"
    # 参与分流的地址族：默认 ["v4"]（向后兼容）；可设 ["v4","v6"] 或 ["v6"]
    ip_versions: List[str] = field(default_factory=lambda: ["v4"])


@dataclass
class CidrsCfg:
    source: str = "url"            # url | manual
    url: Optional[str] = None
    fields: List[str] = field(default_factory=list)
    ttl_hours: int = 24
    extra: List[str] = field(default_factory=list)
    cache_file: Optional[str] = None
    ip_versions: Optional[List[str]] = None   # 规则级覆盖；None = 用 routing.ip_versions


@dataclass
class RuleCfg:
    name: str
    enabled: bool = True
    interface: Optional[str] = None
    cidrs: CidrsCfg = field(default_factory=CidrsCfg)
    table_id: Optional[int] = None
    fwmark: Optional[int] = None


@dataclass
class Config:
    version: int = 1
    interfaces: List[IfaceCfg] = field(default_factory=list)
    metrics: MetricsCfg = field(default_factory=MetricsCfg)
    routing: RoutingCfg = field(default_factory=RoutingCfg)
    rules: List[RuleCfg] = field(default_factory=list)
    state_file: str = "data/config/state.json"
