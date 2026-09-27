"""网卡开关与 metric 调整（FR1/FR2）。"""
from __future__ import annotations

from typing import List, Optional

from . import detect, exec as ex, ip, log
from .model import Config, IfaceCfg

_log = log.get_logger()


def _default_routes(iface: str) -> List[dict]:
    """该网卡的所有默认路由（可能有多条：不同 metric / 不同来源）。"""
    return [r for r in ip.route_list()
            if r.get("dev") == iface and r.get("dst") == "default"]


def _default_route(iface: str):
    routes = _default_routes(iface)
    return routes[0] if routes else None


def set_link(iface: str, up: bool, *, force: bool = False, dry_run: bool = False) -> None:
    """关闭/开启网卡。

    安全保护：若目标网卡是**仅剩的生效物理网卡**（唯一承载默认路由者），
    则**禁止关闭**，避免主机断网；仅当显式 `force=True` 时才允许。
    """
    if not up:
        eff = detect.effective_interface_names()
        if iface in eff and len(eff) <= 1 and not force:
            _log.warning("set_link: 拒绝关闭最后一张生效网卡 %s", iface)
            raise RuntimeError(
                f"仅有一张物理网卡生效（{iface} 承载默认路由），禁止关闭；"
                f"如确需关闭请显式使用 --force（可能导致断网）"
            )
    _log.info("set_link: %s -> %s", iface, "up" if up else "down")
    ex.run(["ip", "link", "set", iface, "up" if up else "down"],
           dry_run=dry_run, check=True)


def set_metric(iface: str, metric: Optional[int], gateway: Optional[str] = None, *,
               dry_run: bool = False) -> None:
    """重建默认路由以设置 metric。

    `metric=None` 表示恢复"不带 metric 的默认路由"（revert 恢复原始状态时用）。
    先删掉该网卡**所有**已知默认路由再添加，避免残留重复默认路由。
    """
    routes = _default_routes(iface)
    gw = gateway or (routes[0].get("gateway") if routes else None)
    if not gw:
        raise RuntimeError(
            f"无法确定 {iface} 的网关（网卡无默认路由且配置未指定 gateway）"
        )
    _log.info("set_metric: %s metric=%s gw=%s", iface, metric, gw)
    for rt in routes:
        cmd = ["ip", "route", "del", "default"]
        if rt.get("gateway"):
            cmd += ["via", rt["gateway"]]
        cmd += ["dev", iface]
        if rt.get("metric") is not None:
            cmd += ["metric", str(int(rt["metric"]))]
        ex.run(cmd, dry_run=dry_run, check=False)

    add = ["ip", "route", "add", "default", "via", gw, "dev", iface]
    if metric is not None:
        add += ["metric", str(int(metric))]
    ex.run(add, dry_run=dry_run, check=True)


def set_primary(iface: str, config: Config, *, dry_run: bool = False) -> None:
    """把 iface 设为优先网卡，其余物理网卡设为 fallback。"""
    _log.info("set_primary: %s（preferred=%s fallback=%s）",
              iface, config.metrics.preferred, config.metrics.fallback)
    targets = list(config.interfaces)
    if not any(ic.name == iface for ic in targets):
        # 目标网卡不在配置里（CLI/TUI 允许选任意探测到的网卡）：
        # 用探测结果补齐，否则会把其它网卡全压成 fallback 而目标网卡毫无变化。
        probe = next((i for i in detect.detect_interfaces() if i.name == iface), None)
        if probe is None:
            raise RuntimeError(f"未探测到网卡 {iface}，无法设为优先网卡")
        _log.info("set_primary: %s 不在配置的 interfaces 中，按探测结果补齐", iface)
        targets.append(IfaceCfg(name=iface, gateway=probe.gateway, type=probe.type))

    for ic in targets:
        target = config.metrics.preferred if ic.name == iface else config.metrics.fallback
        if ic.gateway or ic.name == iface:
            set_metric(ic.name, target, ic.gateway, dry_run=dry_run)
