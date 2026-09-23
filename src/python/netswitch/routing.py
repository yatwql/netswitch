"""分流规则的策略路由（nftables / iprule 双后端，整表原子重建）。"""
from __future__ import annotations

import json
import shutil
from typing import Callable, List, Optional, Set

from . import cidrs, detect, exec as ex, ip, log
from .model import Config, RuleCfg

_log = log.get_logger()

# mainroute 后端给主表明细路由打的 proto 标记（便于判断/清理）
MAINROUTE_PROTO = 200


def _run_or_hint(cmd, *, dry_run: bool = False) -> None:
    """执行命令；若报 EOPNOTSUPP，给出“策略路由不受支持”的明确提示。"""
    try:
        ex.run(cmd, dry_run=dry_run)
    except ex.ExecError as e:
        if "Operation not supported" in (e.stderr or ""):
            raise RuntimeError(
                "策略路由不受支持（RTNETLINK: Operation not supported）。\n"
                "可能原因：内核缺少 CONFIG_IP_MULTIPLE_TABLES，或运行在受限容器/网络命名空间中。\n"
                "请用 scripts/preflight.sh 的「策略路由能力」项确认；该环境无法做流量分流。"
            ) from e
        raise


def resolve_backend(pref: str) -> str:
    if pref in ("nftables", "iprule", "mainroute"):
        return pref
    # auto：不支持策略路由 -> mainroute；否则优先 nftables，其次 iprule
    if not ip.policy_routing_supported():
        return "mainroute"
    if shutil.which("nft"):
        return "nftables"
    return "iprule"


def rule_applied(config: Config, rule: RuleCfg) -> bool:
    """该规则是否已在系统生效（按后端判断）。"""
    backend = resolve_backend(config.routing.backend)
    if backend == "mainroute":
        if not rule.interface:
            return False
        try:
            out = ex.run(["ip", "-j", "route", "show", "table", "main"],
                         check=False, readonly=True).stdout
            for rt in json.loads(out or "[]"):
                if rt.get("dst") == "default" or rt.get("dev") != rule.interface:
                    continue
                if str(rt.get("protocol")) == str(MAINROUTE_PROTO):
                    return True
        except Exception:  # noqa: BLE001
            return False
        return False
    out = ex.run(["ip", "route", "show", "table", str(rule.table_id)],
                 check=False, readonly=True).stdout
    return "default" in out


def _iface_cfg(config: Config, name: str):
    for ic in config.interfaces:
        if ic.name == name:
            return ic
    return None


def _gw_src(config: Config, iface: str):
    ic = _iface_cfg(config, iface)
    gw = ic.gateway if ic else None
    src = None
    subnet = None
    for i in detect.detect_interfaces():
        if i.name == iface:
            if i.ip:
                src = i.ip.split("/")[0]
                subnet = ip.subnet_of(i.ip)
            if not gw:
                gw = i.gateway
            break
    if not gw:
        raise RuntimeError(f"无法确定 {iface} 的网关，请在配置中显式指定")
    return gw, src, subnet


def _select_rules(config: Config, only: Optional[Set[str]] = None,
                  exclude: Optional[Set[str]] = None) -> List[RuleCfg]:
    rules = [r for r in config.rules if r.enabled and r.interface]
    if only is not None:
        rules = [r for r in rules if r.name in only]
    if exclude:
        rules = [r for r in rules if r.name not in exclude]
    return rules


def _build_nft_script(table: str, rules: List[RuleCfg]) -> str:
    lines = [f"table inet {table} {{"]
    for r in rules:
        set_name = ip.nft_set_name(r.name)
        elems = ", ".join(cidrs.fetch_cidrs(r.cidrs))
        if not elems:
            continue
        lines.append(
            f"  set {set_name} {{ type ipv4_addr; flags interval; "
            f"elements = {{ {elems} }} }}"
        )
    lines.append(
        "  chain prerouting { type filter hook prerouting priority mangle; "
        "policy accept;"
    )
    for r in rules:
        if cidrs.fetch_cidrs(r.cidrs):
            lines.append(
                f"    ip daddr @{ip.nft_set_name(r.name)} meta mark set 0x{r.fwmark:x}"
            )
    lines.append("  }")
    lines.append(
        "  chain output { type route hook output priority mangle; policy accept;"
    )
    for r in rules:
        if cidrs.fetch_cidrs(r.cidrs):
            lines.append(
                f"    ip daddr @{ip.nft_set_name(r.name)} meta mark set 0x{r.fwmark:x}"
            )
    lines.append("  }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def clear_rules(config: Config, *, dry_run: bool = False) -> None:
    """清除本程序创建的分流产物（按后端，幂等）。"""
    backend = resolve_backend(config.routing.backend)
    _log.info("clear_rules: 清除分流产物（后端=%s，dry_run=%s）", backend, dry_run)
    tables = {r.table_id for r in config.rules}

    # mainroute：删除主表中本程序添加的明细路由
    for r in config.rules:
        for c in cidrs.fetch_cidrs(r.cidrs):
            ex.run(["ip", "route", "del", c, "table", "main"],
                   dry_run=dry_run, check=False)

    # nft 整表删除（仅 nftables 后端且 nft 可用）
    if backend == "nftables" and shutil.which("nft"):
        ex.run(["nft", "delete", "table", "inet", config.routing.nft_table],
               dry_run=dry_run, check=False)

    # ip rule / 独立路由表：仅在后端使用且内核支持时清理
    # （不支持策略路由的内核连 `ip rule show` 都会 EOPNOTSUPP，必须避开）
    if backend in ("nftables", "iprule") and ip.policy_routing_supported():
        try:
            for ru in ip.rule_list(dry_run=dry_run):
                if ru.get("table") in tables and ru.get("priority", 0) >= ip.RULE_PREF_BASE:
                    ex.run(["ip", "rule", "del", "pref", str(ru["priority"])],
                           dry_run=dry_run, check=False)
        except Exception:  # noqa: BLE001
            pass
        for t in tables:
            ex.run(["ip", "route", "flush", "table", str(t)],
                   dry_run=dry_run, check=False)


def apply_rules(config: Config, *, only: Optional[Set[str]] = None,
                exclude: Optional[Set[str]] = None,
                dry_run: bool = False,
                warn: Optional[Callable[[str], None]] = None) -> int:
    """清空后重建指定规则集（幂等）。返回成功应用的规则数。"""
    _warn = warn or (lambda m: print(f"[warn] {m}"))
    clear_rules(config, dry_run=dry_run)

    rules = _select_rules(config, only=only, exclude=exclude)
    backend = resolve_backend(config.routing.backend)

    pref_counter = 0
    active: List[RuleCfg] = []
    for r in rules:
        rule_cidrs = cidrs.fetch_cidrs(r.cidrs)
        if not rule_cidrs:
            _warn(f"规则 {r.name} 无可用 CIDR，跳过")
            continue
        active.append(r)

        gw, src, subnet = _gw_src(config, r.interface)

        # mainroute 后端：不使用多路由表/ip rule，而是在主表按目标网段加明细路由
        if backend == "mainroute":
            for c in rule_cidrs:
                _run_or_hint(["ip", "route", "replace", c, "via", gw,
                              "dev", r.interface, "proto", str(MAINROUTE_PROTO)],
                             dry_run=dry_run)
            _log.info("mainroute: %s -> %s（%d 个网段）",
                      r.name, r.interface, len(rule_cidrs))
            continue

        table = r.table_id
        # 独立路由表：默认走该网卡网关；补直连子网保证网关可达
        _run_or_hint(["ip", "route", "add", "default", "via", gw, "dev", r.interface,
                      "table", str(table)], dry_run=dry_run)
        if src and subnet:
            _run_or_hint(["ip", "route", "add", subnet, "dev", r.interface,
                          "proto", "kernel", "scope", "link", "src", src,
                          "table", str(table)], dry_run=dry_run)

        if backend == "nftables":
            _run_or_hint(["ip", "rule", "add", "fwmark", hex(r.fwmark), "lookup",
                          str(table), "pref", str(ip.rule_pref(pref_counter))],
                         dry_run=dry_run)
            pref_counter += 1
        else:
            for c in rule_cidrs:
                _run_or_hint(["ip", "rule", "add", "to", c, "lookup", str(table),
                              "pref", str(ip.rule_pref(pref_counter))],
                             dry_run=dry_run)
                pref_counter += 1

    if backend == "nftables" and active:
        script = _build_nft_script(config.routing.nft_table, active)
        ex.run(["nft", "-f", "-"], dry_run=dry_run, input=script)

    _log.info("apply_rules: 后端=%s 规则=%s", backend, [r.name for r in active])
    return len(active)


def backend_note(backend: str) -> str:
    """后端的人话说明（供 status 展示）。"""
    return {
        "nftables": "nftables（多路由表 + fwmark）",
        "iprule": "iprule（多路由表 + ip rule）",
        "mainroute": "mainroute（主表明细路由，无需策略路由）",
    }.get(backend, backend)
