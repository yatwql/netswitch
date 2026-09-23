"""状态汇总与展示（FR6）。"""
from __future__ import annotations

import socket

from . import detect, ip, routing, version
from .model import Config

_STATE_LABEL = {
    "connected": "已连接",
    "no-carrier": "未插网线",
    "down": "未有连接",
}


def state_label(state: str) -> str:
    return _STATE_LABEL.get(state, state)


def print_status(config: Config) -> None:
    interfaces = detect.detect_interfaces()
    backend = routing.resolve_backend(config.routing.backend)

    print(f"主机: {socket.gethostname()}   版本: {version.__version__}")
    print(f"程序更新: {version.program_mtime_str()}")
    print("== 物理网卡 ==")
    for i in interfaces:
        ssid = f" SSID={i.ssid or '-'}" if i.type == "wireless" else ""
        print(f"  {i.name:<12} {('无线' if i.type == 'wireless' else '有线'):<4} "
              f"状态={state_label(i.state):<5} IP={i.ip or '-':<18} "
              f"metric={i.metric if i.metric is not None else '-'} "
              f"网关={i.gateway or '-'}{ssid}")

    print("\n== 默认路由 ==")
    for r in ip.route_list():
        if r.get("dst") == "default":
            print(f"  default via {r.get('gateway')} dev {r.get('dev')} "
                  f"metric {r.get('metric')}")

    print(f"\n== 分流规则（后端: {backend}）==")
    if not config.rules:
        print("  （无）")
    for r in config.rules:
        mark = "生效" if r.enabled else "禁用"
        iface = r.interface or "-"
        print(f"  {r.name:<12} 出口={iface:<12} 状态={mark} "
              f"table={r.table_id} fwmark={r.fwmark}")
