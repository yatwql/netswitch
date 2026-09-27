"""网络自动探测（FR9）：识别物理网卡、类型、连接状态、IP、网关、metric。"""
from __future__ import annotations

from typing import Dict, List, Optional

from . import ip
from .model import Interface


def _ip_map() -> Dict[str, str]:
    m: Dict[str, str] = {}
    for a in ip.addr_list(4):
        ifname = a.get("ifname")
        if not ifname:
            continue
        for info in a.get("addr_info", []):
            if info.get("family") == "inet":
                m[ifname] = f"{info.get('local')}/{info.get('prefixlen')}"
                break
    return m


def _metric_key(metric) -> tuple:
    """metric 缺失（None = 未设置）排在有效值之后。"""
    return (metric is None, metric if metric is not None else 0)


def _default_routes() -> Dict[str, tuple]:
    """dev -> (gateway, metric)；同一网卡多条默认路由时取 metric 最小者。"""
    m: Dict[str, tuple] = {}
    for r in ip.route_list():
        if r.get("dst") != "default":
            continue
        dev = r.get("dev")
        if not dev:
            continue
        cand = (r.get("gateway"), r.get("metric"))
        cur = m.get(dev)
        if cur is None or _metric_key(cand[1]) < _metric_key(cur[1]):
            m[dev] = cand
    return m


def detect_interfaces() -> List[Interface]:
    """探测所有物理网卡及其当前状态。"""
    ips = _ip_map()
    defaults = _default_routes()
    result: List[Interface] = []
    for link in ip.link_list():
        name = link.get("ifname")
        if not ip.is_physical(name, link):
            continue
        gw, metric = defaults.get(name, (None, None))
        itype = ip.interface_type(name)
        result.append(
            Interface(
                name=name,
                type=itype,
                ip=ips.get(name),
                state=ip.link_state(name, link),
                gateway=gw,
                metric=metric,
                admin_up=ip.admin_up(link),
                ssid=ip.wireless_ssid(name) if itype == "wireless" else None,
                device_confirmed=ip.has_device(name),
            )
        )
    return result


def connected_interface_names() -> List[str]:
    return [i.name for i in detect_interfaces() if i.state == "connected"]


def effective_interface_names() -> List[str]:
    """生效的物理网卡：承载默认路由（metric 已知）的网卡。"""
    return [i.name for i in detect_interfaces() if i.metric is not None]


def primary_interface(ifaces: Optional[List[Interface]] = None) -> Optional[str]:
    """当前主网卡：有默认路由且 metric 最小的物理网卡（metric 越小越优先）。"""
    if ifaces is None:
        ifaces = detect_interfaces()
    cands = [i for i in ifaces if i.metric is not None]
    if not cands:
        return None
    return min(cands, key=lambda i: (i.metric, i.name)).name


def interfaces_config_fragment() -> List[dict]:
    """生成拟写入 config 的 interfaces 片段（name/type/gateway/metric）。"""
    frag = []
    for i in detect_interfaces():
        item = {"name": i.name, "type": i.type}
        if i.gateway:
            item["gateway"] = i.gateway
        if i.metric is not None:
            item["metric"] = i.metric
        frag.append(item)
    return frag
