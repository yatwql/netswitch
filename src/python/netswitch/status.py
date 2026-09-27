"""状态汇总与展示（FR6）。"""
from __future__ import annotations

import socket

from . import detect, ip, log, routing, version
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
    log_file = log.current_log_file()

    print(f"主机: {socket.gethostname()}   版本: {version.__version__}")
    print(f"程序更新: {version.program_mtime_str()}")
    print(version.user_line())
    print(f"日志: {log_file or '(不可写，已禁用)'}")
    if ip.policy_routing_supported() is None:
        print("提示: 非 root，策略路由能力未确认（实际以 root 运行 apply 时为准）")
    if not ip.family_available(6):
        print("IPv6: 不可用（内核未启用或不支持）—— 声明 v6 的分流会被跳过")

    print("== 物理网卡 ==")
    inferred = False
    for i in interfaces:
        ssid = f" SSID={i.ssid or '-'}" if i.type == "wireless" else ""
        v6 = (f"  IPv6={i.ip6 or '-'} 网关6={i.gateway6 or '-'}"
              if (i.ip6 or i.gateway6) else "")
        mark = "" if i.device_confirmed else "*"
        inferred = inferred or not i.device_confirmed
        print(f"  {i.name:<12}{mark} {('无线' if i.type == 'wireless' else '有线'):<4} "
              f"状态={state_label(i.state):<5} IP={i.ip or '-':<18} "
              f"metric={i.metric if i.metric is not None else '-'} "
              f"网关={i.gateway or '-'}{v6}{ssid}")
    if inferred:
        print("  * 无 sysfs device 节点（容器/受限环境，或 bond 等聚合设备）：按命名推断为物理网卡")

    print("\n== 默认路由 ==")
    for r in ip.route_list():
        if r.get("dst") == "default":
            print(f"  default via {r.get('gateway')} dev {r.get('dev')} "
                  f"metric {r.get('metric')}")

    print(f"\n== 分流规则（后端: {routing.backend_note(backend)}）==")
    if not config.rules:
        print("  （无）")
    main_routes = routing.main_table_routes_all() if backend == "mainroute" else None
    for r in config.rules:
        try:
            applied = routing.rule_applied(config, r, main_routes=main_routes)
        except Exception as exc:  # noqa: BLE001 - 查询失败不应让 status 整体崩掉
            applied = False
            print(f"  （规则 {r.name} 状态查询失败：{exc}）")
        if not r.enabled:
            mark = "禁用"
        else:
            mark = "已应用" if applied else "未应用"
        iface = r.interface or "-"
        fams = "+".join(f"v{f}" for f in routing.rule_families(config, r))
        print(f"  {r.name:<12} 出口={iface:<12} 族={fams:<5} 状态={mark}")
