"""网卡开关与 metric 调整（FR1/FR2）。"""
from __future__ import annotations

from typing import List, Optional

from . import detect, exec as ex, ip, log
from .model import Config, IfaceCfg

_log = log.get_logger()


def _default_route(iface: str):
    for r in ip.route_list():
        if r.get("dev") == iface and r.get("dst") == "default":
            return r
    return None


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


def set_metric(iface: str, metric: int, gateway: Optional[str] = None, *,
               dry_run: bool = False) -> None:
    """重建默认路由以设置 metric。"""
    rt = _default_route(iface)
    gw = gateway or (rt.get("gateway") if rt else None)
    if not gw:
        raise RuntimeError(
            f"无法确定 {iface} 的网关（网卡无默认路由且配置未指定 gateway）"
        )
    _log.info("set_metric: %s metric=%s gw=%s", iface, metric, gw)
    # 先删后加（幂等）
    ex.run(["ip", "route", "del", "default", "via", gw, "dev", iface],
           dry_run=dry_run, check=False)
    ex.run(["ip", "route", "add", "default", "via", gw, "dev", iface,
            "metric", str(int(metric))], dry_run=dry_run, check=True)


def set_primary(iface: str, config: Config, *, dry_run: bool = False) -> None:
    """把 iface 设为优先网卡，其余物理网卡设为 fallback。"""
    _log.info("set_primary: %s（preferred=%s fallback=%s）",
              iface, config.metrics.preferred, config.metrics.fallback)
    for ic in config.interfaces:
        target = config.metrics.preferred if ic.name == iface else config.metrics.fallback
        if ic.gateway:
            set_metric(ic.name, target, ic.gateway, dry_run=dry_run)
