"""分流规则的策略路由（nftables / iprule / mainroute 三后端，整表原子重建）。

安全原则（见 docs/technical.md 4.x）：
1. **只删自己的东西**：清理时按"签名"识别本程序产物
   —— nft 表名、主表中 `proto = MAINROUTE_PROTO` 的明细路由、
   优先级落在 `ip.RULE_PREF_BASE` 区间且表号在本程序派生范围内的 ip rule。
2. **先校验后改动**：`preflight` 全部通过才清空重建；失败时原状态保持不动。
3. **失败回滚**：重建过程中出错则 best-effort 清理已建产物，避免半成品状态。
"""
from __future__ import annotations

import ipaddress
import json
import shutil
from typing import Callable, Dict, Iterable, List, Optional

from . import cidrs, detect, exec as ex, ip, log
from .model import Config, RuleCfg

_log = log.get_logger()

# mainroute 后端给主表明细路由打的 proto 标记（便于判定/清理）
MAINROUTE_PROTO = ip.MAINROUTE_PROTO


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
    # auto：不支持策略路由（False）-> mainroute；
    # None（非 root，无法判断）按“未知”处理，走支持策略路由的分支。
    if ip.policy_routing_supported() is False:
        return "mainroute"
    if shutil.which("nft"):
        return "nftables"
    return "iprule"


def main_table_routes() -> List[dict]:
    """主表中本程序添加的明细路由（`proto == MAINROUTE_PROTO`）。

    只读查询：即使 dry-run 也真实读取，便于准确展示"将删除哪些路由"。
    """
    try:
        out = ex.run(["ip", "-j", "route", "show", "table", "main"],
                     check=False, readonly=True).stdout
        routes = json.loads(out or "[]")
    except Exception as exc:  # noqa: BLE001
        _log.debug("main_table_routes: 读取主表失败：%s", exc)
        return []
    if not isinstance(routes, list):
        return []
    return [rt for rt in routes
            if isinstance(rt, dict) and str(rt.get("protocol")) == str(MAINROUTE_PROTO)]


def rule_applied(config: Config, rule: RuleCfg,
                 *, main_routes: Optional[List[dict]] = None) -> bool:
    """该规则是否已在系统生效（按后端判断）。

    `main_routes` 可传入一次查询的结果，避免多条规则重复 dump 主表（TUI/status 用）。
    """
    backend = resolve_backend(config.routing.backend)
    if backend == "mainroute":
        if not rule.interface:
            return False
        routes = main_table_routes() if main_routes is None else main_routes
        return any(rt.get("dev") == rule.interface for rt in routes)
    out = ex.run(["ip", "route", "show", "table", str(rule.table_id)],
                 check=False, readonly=True).stdout
    return "default" in out


def _iface_cfg(config: Config, name: str):
    for ic in config.interfaces:
        if ic.name == name:
            return ic
    return None


def _gw_src(config: Config, iface: str, ifaces=None):
    ic = _iface_cfg(config, iface)
    gw = ic.gateway if ic else None
    src = None
    subnet = None
    for i in (ifaces if ifaces is not None else detect.detect_interfaces()):
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


def _cidr_map(config: Config, rules: Iterable[RuleCfg],
              warn=None) -> Dict[str, List[str]]:
    """一次拉取所有规则的 CIDR（避免同一次 apply 里反复请求同一 URL）。"""
    return {r.name: cidrs.fetch_cidrs(r.cidrs, warn=warn) for r in rules}


def _build_nft_script(table: str, rules: List[RuleCfg],
                      cidr_map: Dict[str, List[str]]) -> str:
    lines = [f"table inet {table} {{"]
    for r in rules:
        set_name = ip.nft_set_name(r.name)
        elems = ", ".join(cidr_map.get(r.name, []))
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
        if cidr_map.get(r.name):
            lines.append(
                f"    ip daddr @{ip.nft_set_name(r.name)} meta mark set 0x{r.fwmark:x}"
            )
    lines.append("  }")
    lines.append(
        "  chain output { type route hook output priority mangle; policy accept;"
    )
    for r in rules:
        if cidr_map.get(r.name):
            lines.append(
                f"    ip daddr @{ip.nft_set_name(r.name)} meta mark set 0x{r.fwmark:x}"
            )
    lines.append("  }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _mainroute_del_cmd(cidr: str, iface: Optional[str]) -> List[str]:
    """删除本程序在主表添加的明细路由（带 proto 签名，绝不误删他人路由）。"""
    cmd = ["ip", "route", "del", cidr, "proto", str(MAINROUTE_PROTO)]
    if iface:
        cmd += ["dev", iface]
    return cmd + ["table", "main"]


def _clear_mainroute(rules: Iterable[RuleCfg], cidr_map: Dict[str, List[str]],
                     *, dry_run: bool, keep: Optional[set] = None) -> None:
    """清理主表中本程序的明细路由。

    仅当目标 CIDR 在主表中确实存在 `proto = MAINROUTE_PROTO` 的路由时才删除，
    因此不会碰到内核直连路由、其它工具的静态路由或 VPN 路由。
    """
    keep = keep or set()
    own: Dict[str, set] = {}
    for rt in main_table_routes():
        dst = rt.get("dst")
        if dst:
            own.setdefault(dst, set()).add(rt.get("dev"))
    for r in rules:
        for c in cidr_map.get(r.name, []):
            if (c, r.interface) in keep:
                continue
            devs = own.get(c)
            if devs is None:
                if dry_run:
                    ex.run(_mainroute_del_cmd(c, r.interface), dry_run=True, check=False)
                continue
            if r.interface and r.interface not in devs:
                continue                     # 该网段属于其它出口，不在本次清理范围
            ex.run(_mainroute_del_cmd(c, r.interface), dry_run=dry_run, check=False)


def _clear_ip_rules(config: Config, extra_tables: Iterable[int], *, dry_run: bool) -> None:
    """清理本程序创建的 ip rule 与派生路由表。

    识别方式与当前配置无关：优先级落在 [RULE_PREF_BASE, +RULE_PREF_MAX_COUNT)
    且表号属于"本次配置表号 ∪ 派生表区间"。这样即使规则被删除/改名/换表号，
    历史残留也能清掉（内核保留表 253/254/255 不在派生区间内，绝不会被 flush）。
    """
    known = {r.table_id for r in config.rules if r.table_id} | {t for t in extra_tables if t}
    recognizable = known | set(range(ip.TABLE_ID_MIN, ip.TABLE_ID_MAX + 1))
    pref_lo = ip.RULE_PREF_BASE
    pref_hi = ip.RULE_PREF_BASE + ip.RULE_PREF_MAX_COUNT
    doomed = set(known)

    if ip.policy_routing_supported() is False:
        _log.info("clear: 内核不支持策略路由，跳过 ip rule / 路由表清理")
        return

    try:
        entries = ip.rule_list()             # 只读：dry-run 下也真实读取
    except Exception as exc:  # noqa: BLE001
        _log.debug("clear: 读取 ip rule 失败：%s", exc)
        entries = []
    for ru in entries:
        pref = ru.get("priority")
        table = ru.get("table")
        if not isinstance(pref, int) or not (pref_lo <= pref < pref_hi):
            continue
        if table not in recognizable:
            continue
        doomed.add(table)
        ex.run(["ip", "rule", "del", "pref", str(pref)], dry_run=dry_run, check=False)

    for t in sorted(doomed):
        ex.run(["ip", "route", "flush", "table", str(t)],
               dry_run=dry_run, check=False)


def clear_rules(config: Config, *, dry_run: bool = False,
                cidr_map: Optional[Dict[str, List[str]]] = None,
                warn=None, tables: Optional[Iterable[int]] = None,
                keep_mainroute: Optional[set] = None) -> None:
    """清除本程序创建的分流产物（幂等，只删自己的产物）。

    - 不管当前后端是什么，都会尝试清理三类产物（切换后端后旧产物也能清掉）；
    - `cidr_map` 可复用调用方已拉取的 CIDR，避免重复请求；
    - `tables` 传入历史表号（来自 state.json），用于清理改配置前遗留的路由表。
    """
    backend = resolve_backend(config.routing.backend)
    _log.info("clear_rules: 后端=%s，dry_run=%s，历史表=%s",
              backend, dry_run, sorted(t for t in (tables or []) if t))
    cmap = cidr_map if cidr_map is not None else _cidr_map(config, config.rules, warn)

    # 1) nft 整表删除（仅当 nft 可用）
    if shutil.which("nft"):
        ex.run(["nft", "delete", "table", "inet", config.routing.nft_table],
               dry_run=dry_run, check=False)

    # 2) 主表中本程序添加的明细路由（proto 签名 + 出口网卡双重校验）
    _clear_mainroute(config.rules, cmap, dry_run=dry_run, keep=keep_mainroute)

    # 3) ip rule / 派生路由表（按优先级区间与派生表号识别）
    _clear_ip_rules(config, tables or (), dry_run=dry_run)


def preflight(config: Config, rules: List[RuleCfg],
              cidr_map: Dict[str, List[str]], backend: str, *,
              ifaces=None, warn: Optional[Callable[[str], None]] = None) -> List[str]:
    """应用前的纯校验（只读，不修改系统）。返回问题列表，空列表表示可以开始。

    警告（非物理网卡、网卡未连接）只提示不阻断——无线网卡可能稍后才关联上；
    网卡不存在、网关不可解、CIDR 非法、ip rule 超上限则属于硬错误。
    """
    problems: List[str] = []
    _warn = warn or (lambda m: _log.warning("%s", m))
    if ifaces is None:
        ifaces = detect.detect_interfaces()
    snapshot = {i.name: i for i in ifaces}
    physical = set(snapshot)
    try:
        links = {l.get("ifname") for l in ip.link_list()}
    except Exception:  # noqa: BLE001
        links = set(physical)

    for r in rules:
        if r.interface not in links:
            problems.append(f"规则 {r.name}：出口网卡 {r.interface} 不存在")
            continue
        nic = snapshot.get(r.interface)
        if nic is None:
            _warn(f"规则 {r.name}：出口网卡 {r.interface} 不是探测到的物理网卡（仍会尝试应用）")
        elif not nic.admin_up or nic.state != "connected":
            _warn(f"规则 {r.name}：出口网卡 {r.interface} 当前未连接"
                  f"（管理状态={'up' if nic.admin_up else 'down'}，链路={nic.state}）"
                  f"——分流可能暂时不通，已按配置继续应用")
        for c in cidr_map.get(r.name, []):
            try:
                ipaddress.ip_network(c, strict=False)
            except ValueError:
                problems.append(f"规则 {r.name}：CIDR 非法（{c}）")
        try:
            _gw_src(config, r.interface, ifaces=ifaces)
        except RuntimeError as exc:
            problems.append(f"规则 {r.name}：{exc}")

    if backend == "iprule":
        total = sum(len(cidr_map.get(r.name, [])) for r in rules)
        if total > ip.RULE_PREF_MAX_COUNT:
            problems.append(
                f"iprule 后端需要 {total} 条 ip rule，超过上限 {ip.RULE_PREF_MAX_COUNT}；"
                f"请减少 CIDR 或改用 nftables 后端"
            )
    return problems


def _apply_active(config: Config, active: List[RuleCfg],
                  cidr_map: Dict[str, List[str]], backend: str, *,
                  dry_run: bool, ifaces=None) -> None:
    """把 active 规则写入系统（调用方已确保校验通过、旧产物已清理）。"""
    pref_counter = 0
    for r in active:
        rule_cidrs = cidr_map[r.name]
        gw, src, subnet = _gw_src(config, r.interface, ifaces=ifaces)

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
        script = _build_nft_script(config.routing.nft_table, active, cidr_map)
        ex.run(["nft", "-f", "-"], dry_run=dry_run, input=script)


def apply_rules(config: Config, *, dry_run: bool = False,
                warn: Optional[Callable[[str], None]] = None) -> int:
    """把配置声明的规则状态整体应用到系统（幂等）。返回生效的规则数。

    流程：拉取 CIDR（每条源一次）→ 预检 → 清理本程序旧产物（保留待重建项）
    → 重建；任一步失败则回滚清理并抛出，不会留下"半成品"。
    实现上始终按**全部已启用且有出口网卡的规则**重建（声明式），
    因此单独应用某条规则不会误伤其它规则。
    """
    _warn = warn or (lambda m: print(f"[warn] {m}"))
    _log.info("apply_rules: 开始（dry_run=%s）", dry_run)
    backend = resolve_backend(config.routing.backend)
    ifaces = detect.detect_interfaces()
    declared = [r for r in config.rules if r.enabled and r.interface]
    cmap = _cidr_map(config, config.rules, _warn)

    problems = preflight(config, declared, cmap, backend, ifaces=ifaces, warn=_warn)
    if problems:
        raise RuntimeError("配置预检未通过（未做任何改动）：\n  - "
                           + "\n  - ".join(problems))

    active: List[RuleCfg] = []
    for r in declared:
        if cmap.get(r.name):
            active.append(r)
        else:
            _warn(f"规则 {r.name} 无可用 CIDR，跳过")

    # mainroute 的明细路由用 replace 重建，无需先删：只清理不再需要的，
    # 避免"清空再重建"造成瞬时断流。
    keep = ({(c, r.interface) for r in active for c in cmap[r.name]}
            if backend == "mainroute" else None)
    clear_rules(config, dry_run=dry_run, cidr_map=cmap, warn=_warn, keep_mainroute=keep)

    try:
        _apply_active(config, active, cmap, backend, dry_run=dry_run, ifaces=ifaces)
    except Exception:
        _log.error("apply_rules: 应用失败，回滚清理本程序产物")
        try:
            clear_rules(config, dry_run=dry_run, cidr_map=cmap, warn=_warn)
        except Exception:  # noqa: BLE001
            _log.warning("apply_rules: 回滚清理亦失败，请手动检查 ip rule / 路由表")
        raise

    _log.info("apply_rules: 后端=%s 规则=%s", backend, [r.name for r in active])
    return len(active)


def backend_note(backend: str) -> str:
    """后端的人话说明（供 status 展示）。"""
    return {
        "nftables": "nftables（多路由表 + fwmark）",
        "iprule": "iprule（多路由表 + ip rule）",
        "mainroute": "mainroute（主表明细路由，无需策略路由）",
    }.get(backend, backend)
