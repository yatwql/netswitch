"""分流规则的策略路由（nftables / iprule / mainroute 三后端，v4 + v6 双栈）。

安全原则（见 docs/technical.md 4.x）：
1. **只删自己的东西**：清理时按"签名"识别本程序产物
   —— nft 表名、主表中 `proto = MAINROUTE_PROTO` 的明细路由（v4/v6 各自判定）、
   优先级落在 `ip.RULE_PREF_BASE` 区间且表号在本程序派生范围内的 ip rule。
2. **先校验后改动**：`preflight` 全部通过才清空重建；失败时原状态保持不动。
3. **失败回滚**：重建过程中出错则 best-effort 清理已建产物，避免半成品状态。
4. **双栈按需**：地址族由 `routing.ip_versions`（可用 `rules[].cidrs.ip_versions`
   覆盖）决定，默认仅 IPv4（向后兼容）；某族在环境中不可用（内核未启用 IPv6、
   无 IPv6 网关）时**告警跳过该族**而不影响另一族。
"""
from __future__ import annotations

import ipaddress
import json
import shutil
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from . import cidrs, detect, exec as ex, ip, log
from .model import Config, RuleCfg

_log = log.get_logger()

# mainroute 后端给主表明细路由打的 proto 标记（便于判定/清理）
MAINROUTE_PROTO = ip.MAINROUTE_PROTO


@dataclass(frozen=True)
class FamilyPlan:
    """某条规则在一个地址族上的出口计划（预检阶段解析完成）。"""

    family: int                    # 4 / 6
    cidrs: List[str] = field(default_factory=list)
    gateway: str = ""
    src: Optional[str] = None
    subnet: Optional[str] = None

    @property
    def label(self) -> str:
        return f"IPv{self.family}"


def _run_or_hint(cmd, *, dry_run: bool = False) -> None:
    """执行命令；若报 EOPNOTSUPP，给出“策略路由不受支持”的明确提示。"""
    try:
        ex.run(cmd, dry_run=dry_run)
    except ex.ExecError as e:
        if "Operation not supported" in (e.stderr or ""):
            raise RuntimeError(
                "策略路由不受支持（RTNETLINK: Operation not supported）。\n"
                "可能原因：内核缺少 CONFIG_IP_MULTIPLE_TABLES / CONFIG_IPV6_MULTIPLE_TABLES，"
                "或运行在受限容器/网络命名空间中。\n"
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


def rule_families(config: Config, rule: RuleCfg) -> Tuple[int, ...]:
    """该规则实际使用的地址族（规则级 `cidrs.ip_versions` 覆盖全局 `routing.ip_versions`）。"""
    keys = (rule.cidrs.ip_versions if rule.cidrs.ip_versions
            else config.routing.ip_versions) or ["v4"]
    fams: List[int] = []
    for key in keys:
        fam = 6 if str(key).strip().lower() == "v6" else 4
        if fam not in fams:
            fams.append(fam)
    return tuple(fams)


def _family_cidrs(cidr_list: Iterable[str], family: int) -> List[str]:
    return [c for c in cidr_list if ip.family_of(c) == family]


def main_table_routes(family: int = 4) -> List[dict]:
    """主表中本程序添加的明细路由（`proto == MAINROUTE_PROTO`，按地址族）。

    只读查询：即使 dry-run 也真实读取，便于准确展示"将删除哪些路由"。
    """
    try:
        out = ex.run(["ip", *ip.family_args(family), "-j", "route", "show", "table", "main"],
                     check=False, readonly=True).stdout
        routes = json.loads(out or "[]")
    except Exception as exc:  # noqa: BLE001
        _log.debug("main_table_routes: 读取 v%d 主表失败：%s", family, exc)
        return []
    if not isinstance(routes, list):
        return []
    return [rt for rt in routes
            if isinstance(rt, dict) and str(rt.get("protocol")) == str(MAINROUTE_PROTO)]


def main_table_routes_all() -> Dict[int, List[dict]]:
    """一次取出所有可用地址族的主表签名路由（供 status/TUI 复用，避免按规则重复 dump）。"""
    snap: Dict[int, List[dict]] = {}
    for fam in ip.FAMILIES:
        if fam == 6 and not ip.family_available(6):
            continue
        snap[fam] = main_table_routes(fam)
    return snap


def _norm_snapshot(main_routes) -> Optional[Dict[int, List[dict]]]:
    """兼容两种入参：`{family: routes}`（新）或 `[routes]`（旧，仅 v4）。"""
    if main_routes is None:
        return None
    if isinstance(main_routes, dict):
        return main_routes
    return {4: list(main_routes)}


def rule_applied(config: Config, rule: RuleCfg, *, main_routes=None) -> bool:
    """该规则是否已在系统生效（按后端与地址族判断，需所有声明的族都生效）。

    `main_routes` 可传入 `main_table_routes_all()` 的结果，避免多条规则重复 dump 主表。
    """
    backend = resolve_backend(config.routing.backend)
    families = rule_families(config, rule)
    if backend == "mainroute":
        if not rule.interface:
            return False
        snapshot = _norm_snapshot(main_routes)
        for fam in families:
            if fam == 6 and not ip.family_available(6):
                return False
            if snapshot is not None and fam in snapshot:
                routes = snapshot[fam]
            else:
                routes = main_table_routes(fam)
            if not any(rt.get("dev") == rule.interface for rt in routes):
                return False
        return True

    for fam in families:
        if fam == 6 and not ip.family_available(6):
            return False
        out = ex.run(["ip", *ip.family_args(fam), "route", "show", "table",
                      str(rule.table_id)], check=False, readonly=True).stdout
        if "default" not in out:
            return False
    return True


def _iface_cfg(config: Config, name: str):
    for ic in config.interfaces:
        if ic.name == name:
            return ic
    return None


def _gw_src(config: Config, iface: str, family: int = 4, ifaces=None):
    """解析该网卡在某地址族上的 (网关, 源地址, 直连子网)。"""
    ic = _iface_cfg(config, iface)
    if family == 6:
        gw = ic.gateway6 if ic else None
        addr_attr, gw_attr, field_name = "ip6", "gateway6", "gateway6"
        what = "IPv6 网关"
    else:
        gw = ic.gateway if ic else None
        addr_attr, gw_attr, field_name = "ip", "gateway", "gateway"
        what = "网关"

    src = None
    subnet = None
    for i in (ifaces if ifaces is not None else detect.detect_interfaces()):
        if i.name == iface:
            addr = getattr(i, addr_attr, None)
            if addr:
                src = addr.split("/")[0]
                subnet = ip.subnet_of(addr)
            if not gw:
                gw = getattr(i, gw_attr, None)
            break
    if not gw:
        hint = f"（网卡无 IPv{family} 默认路由且配置未指定 {field_name}）" if family == 6 \
            else "（网卡无默认路由且配置未指定 gateway）"
        raise RuntimeError(f"无法确定 {iface} 的{what}{hint}")
    return gw, src, subnet


def _cidr_map(config: Config, rules: Iterable[RuleCfg],
              warn=None) -> Dict[str, List[str]]:
    """一次拉取所有规则的 CIDR（v4+v6 全量，避免同一次 apply 里反复请求同一 URL）。

    注意：这里保留全部地址族 —— 应用时按规则声明的族筛选，
    清理时按签名（proto 200 / ip rule pref）逐族判定，才能清掉“改配置前”的遗留。
    """
    return {r.name: cidrs.fetch_cidrs(r.cidrs, warn=warn) for r in rules}


def _build_nft_script(table: str, rules: List[RuleCfg],
                      plan: Dict[str, List[FamilyPlan]]) -> str:
    """整表生成：每条规则每个地址族一个 set，两个链（prerouting + output）各一条标记规则。"""
    lines = [f"table inet {table} {{"]
    for r in rules:
        for fp in plan.get(r.name, []):
            if not fp.cidrs:
                continue
            set_type = "ipv4_addr" if fp.family == 4 else "ipv6_addr"
            lines.append(
                f"  set {ip.nft_set_name(r.name, fp.family)} {{ type {set_type}; "
                f"flags interval; elements = {{ {', '.join(fp.cidrs)} }} }}"
            )
    lines.append(
        "  chain prerouting { type filter hook prerouting priority mangle; "
        "policy accept;"
    )
    for r in rules:
        for fp in plan.get(r.name, []):
            if fp.cidrs:
                lines.append(
                    f"    {'ip' if fp.family == 4 else 'ip6'} daddr "
                    f"@{ip.nft_set_name(r.name, fp.family)} meta mark set 0x{r.fwmark:x}"
                )
    lines.append("  }")
    lines.append(
        "  chain output { type route hook output priority mangle; policy accept;"
    )
    for r in rules:
        for fp in plan.get(r.name, []):
            if fp.cidrs:
                lines.append(
                    f"    {'ip' if fp.family == 4 else 'ip6'} daddr "
                    f"@{ip.nft_set_name(r.name, fp.family)} meta mark set 0x{r.fwmark:x}"
                )
    lines.append("  }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _mainroute_del_cmd(cidr: str, iface: Optional[str], family: int) -> List[str]:
    """删除本程序在主表添加的明细路由（带 proto 签名 + 地址族，绝不误删他人路由）。"""
    cmd = ["ip", *ip.family_args(family), "route", "del", cidr,
           "proto", str(MAINROUTE_PROTO)]
    if iface:
        cmd += ["dev", iface]
    return cmd + ["table", "main"]


def _clear_mainroute(rules: Iterable[RuleCfg], cidr_map: Dict[str, List[str]],
                     *, dry_run: bool, keep: Optional[set] = None) -> None:
    """清理主表中本程序的明细路由（v4/v6 分别判定）。

    仅当目标 CIDR 在该族主表中确实存在 `proto = MAINROUTE_PROTO` 的路由时才删除，
    因此不会碰到内核直连路由、其它工具的静态路由或 VPN 路由。
    """
    keep = keep or set()
    own: Dict[int, Dict[str, set]] = {}
    for fam in ip.FAMILIES:
        if fam == 6 and not ip.family_available(6):
            continue
        table: Dict[str, set] = {}
        for rt in main_table_routes(fam):
            dst = rt.get("dst")
            if dst:
                table.setdefault(dst, set()).add(rt.get("dev"))
        own[fam] = table

    for r in rules:
        for c in cidr_map.get(r.name, []):
            fam = ip.family_of(c)
            table = own.get(fam)
            if table is None:                        # 该族不可用：不触碰
                continue
            if (c, r.interface) in keep:
                continue
            devs = table.get(c)
            if devs is None:
                if dry_run:
                    ex.run(_mainroute_del_cmd(c, r.interface, fam), dry_run=True, check=False)
                continue
            if r.interface and r.interface not in devs:
                continue                     # 该网段属于其它出口，不在本次清理范围
            ex.run(_mainroute_del_cmd(c, r.interface, fam), dry_run=dry_run, check=False)


def _clear_ip_rules(config: Config, extra_tables: Iterable[int], *, dry_run: bool) -> None:
    """清理本程序创建的 ip rule 与派生路由表（v4/v6 各自处理）。

    识别方式与当前配置无关：优先级落在 [RULE_PREF_BASE, +RULE_PREF_MAX_COUNT)
    且表号属于"本次配置表号 ∪ 派生表区间"。这样即使规则被删除/改名/换表号、
    或某条规则不再使用 IPv6，历史残留也能清掉
    （内核保留表 253/254/255 不在派生区间内，绝不会被 flush）。
    """
    known = {r.table_id for r in config.rules if r.table_id} | {t for t in extra_tables if t}
    recognizable = known | set(range(ip.TABLE_ID_MIN, ip.TABLE_ID_MAX + 1))
    pref_lo = ip.RULE_PREF_BASE
    pref_hi = ip.RULE_PREF_BASE + ip.RULE_PREF_MAX_COUNT

    for family in ip.FAMILIES:
        if ip.policy_routing_supported(family) is False:
            _log.info("clear: IPv%d 不支持策略路由，跳过该族的 ip rule/路由表清理", family)
            continue
        doomed = set(known)
        try:
            entries = ip.rule_list(family)       # 只读：dry-run 下也真实读取
        except Exception as exc:  # noqa: BLE001
            _log.debug("clear: 读取 IPv%d ip rule 失败：%s", family, exc)
            entries = []
        for ru in entries:
            pref = ru.get("priority")
            table = ru.get("table")
            if not isinstance(pref, int) or not (pref_lo <= pref < pref_hi):
                continue
            if table not in recognizable:
                continue
            doomed.add(table)
            ex.run(["ip", *ip.family_args(family), "rule", "del", "pref", str(pref)],
                   dry_run=dry_run, check=False)

        for t in sorted(doomed):
            ex.run(["ip", *ip.family_args(family), "route", "flush", "table", str(t)],
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

    # 1) nft 整表删除（table inet 同时覆盖 v4/v6；仅当 nft 可用）
    if shutil.which("nft"):
        ex.run(["nft", "delete", "table", "inet", config.routing.nft_table],
               dry_run=dry_run, check=False)

    # 2) 主表中本程序添加的明细路由（proto 签名 + 出口网卡 + 地址族三重校验）
    _clear_mainroute(config.rules, cmap, dry_run=dry_run, keep=keep_mainroute)

    # 3) ip rule / 派生路由表（按优先级区间与派生表号识别，v4/v6 各自）
    _clear_ip_rules(config, tables or (), dry_run=dry_run)


def preflight(config: Config, rules: List[RuleCfg],
              cidr_map: Dict[str, List[str]], backend: str, *,
              ifaces=None, warn: Optional[Callable[[str], None]] = None
              ) -> Tuple[List[str], Dict[str, List[FamilyPlan]]]:
    """应用前的纯校验（只读，不修改系统）。返回 (硬错误列表, 每条规则的出口计划)。

    - 硬错误：出口网卡不存在、IPv4 网关不可解、CIDR 非法、ip rule 数超上限；
    - 告警（不阻断）：网卡非物理/未连接、IPv6 不可用（内核未启用或无 IPv6 网关）
      —— 此时跳过该地址族，其余族照常应用。
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
    plan: Dict[str, List[FamilyPlan]] = {}
    for r in rules:
        if backend != "mainroute" and (r.table_id is None or r.fwmark is None):
            problems.append(
                f"规则 {r.name}：缺少 table_id/fwmark（请用 config.load 加载配置，"
                f"不要手工构造 Config）"
            )
            continue
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

        all_cidrs = cidr_map.get(r.name, [])
        for c in all_cidrs:
            try:
                ipaddress.ip_network(c, strict=False)
            except ValueError:
                problems.append(f"规则 {r.name}：CIDR 非法（{c}）")

        plans: List[FamilyPlan] = []
        for fam in rule_families(config, r):
            fam_cidrs = _family_cidrs(all_cidrs, fam)
            if not fam_cidrs:
                continue
            if fam == 6:
                if not ip.family_available(6):
                    _warn(f"规则 {r.name}：系统未启用 IPv6，已跳过 IPv6 分流")
                    continue
                if backend != "mainroute" and ip.policy_routing_supported(6) is False:
                    _warn(f"规则 {r.name}：内核/环境不支持 IPv6 策略路由，已跳过 IPv6 分流")
                    continue
            try:
                gw, src, subnet = _gw_src(config, r.interface, fam, ifaces=ifaces)
            except RuntimeError as exc:
                if fam == 6:
                    _warn(f"规则 {r.name}：{exc} —— 已跳过 IPv6 分流")
                    continue
                problems.append(f"规则 {r.name}：{exc}")
                continue
            plans.append(FamilyPlan(family=fam, cidrs=fam_cidrs, gateway=gw,
                                    src=src, subnet=subnet))
        if plans:
            plan[r.name] = plans
        elif any(ip.family_of(c) in rule_families(config, r) for c in all_cidrs):
            _warn(f"规则 {r.name}：当前环境下没有可用的地址族，跳过")
        else:
            _warn(f"规则 {r.name} 无可用 CIDR，跳过")

    # ip rule 数量上限（v4/v6 是各自独立的规则库，分别校验；nftables 每族只需 1 条）
    for fam in ip.FAMILIES:
        if backend == "iprule":
            total = sum(len(fp.cidrs) for plans in plan.values()
                        for fp in plans if fp.family == fam)
        elif backend == "nftables":
            total = sum(1 for plans in plan.values() for fp in plans if fp.family == fam)
        else:
            continue
        if total > ip.RULE_PREF_MAX_COUNT:
            problems.append(
                f"IPv{fam}：{backend} 后端需要 {total} 条 ip rule，"
                f"超过上限 {ip.RULE_PREF_MAX_COUNT}；请减少 CIDR 或改用其它后端"
            )
    return problems, plan


def _apply_active(config: Config, declared: List[RuleCfg],
                  plan: Dict[str, List[FamilyPlan]], backend: str, *,
                  dry_run: bool) -> None:
    """把 plan 写入系统（调用方已确保校验通过、旧产物已清理）。"""
    by_name = {r.name: r for r in declared}
    pref_counter = 0
    for name, plans in plan.items():
        rule = by_name.get(name)
        if rule is None:            # pragma: no cover - plan 由 declared 生成
            continue
        for fp in plans:
            args = ip.family_args(fp.family)

            # mainroute 后端：不使用多路由表/ip rule，而是在主表按目标网段加明细路由
            if backend == "mainroute":
                for c in fp.cidrs:
                    _run_or_hint(["ip", *args, "route", "replace", c, "via", fp.gateway,
                                  "dev", rule.interface, "proto", str(MAINROUTE_PROTO)],
                                 dry_run=dry_run)
                _log.info("mainroute: %s -> %s（%s，%d 个网段）",
                          rule.name, rule.interface, fp.label, len(fp.cidrs))
                continue

            table = rule.table_id
            # 独立路由表：默认走该网卡网关；补直连子网保证网关可达
            _run_or_hint(["ip", *args, "route", "add", "default", "via", fp.gateway,
                          "dev", rule.interface, "table", str(table)], dry_run=dry_run)
            if fp.src and fp.subnet:
                _run_or_hint(["ip", *args, "route", "add", fp.subnet, "dev", rule.interface,
                              "proto", "kernel", "scope", "link", "src", fp.src,
                              "table", str(table)], dry_run=dry_run)

            if backend == "nftables":
                _run_or_hint(["ip", *args, "rule", "add", "fwmark", hex(rule.fwmark),
                              "lookup", str(table), "pref", str(ip.rule_pref(pref_counter))],
                             dry_run=dry_run)
                pref_counter += 1
            else:
                for c in fp.cidrs:
                    _run_or_hint(["ip", *args, "rule", "add", "to", c, "lookup",
                                  str(table), "pref", str(ip.rule_pref(pref_counter))],
                                 dry_run=dry_run)
                    pref_counter += 1

    if backend == "nftables" and plan:
        script = _build_nft_script(config.routing.nft_table, declared, plan)
        ex.run(["nft", "-f", "-"], dry_run=dry_run, input=script)


def apply_rules(config: Config, *, dry_run: bool = False,
                warn: Optional[Callable[[str], None]] = None) -> int:
    """把配置声明的规则状态整体应用到系统（幂等）。返回生效的规则数。

    流程：拉取 CIDR（每条源一次）→ 预检（含地址族可用性）→ 清理本程序旧产物
    （保留待重建项）→ 重建；任一步失败则回滚清理并抛出，不会留下"半成品"。
    实现上始终按**全部已启用且有出口网卡的规则**重建（声明式），
    因此单独应用某条规则不会误伤其它规则。
    """
    _warn = warn or (lambda m: print(f"[warn] {m}"))
    _log.info("apply_rules: 开始（dry_run=%s）", dry_run)
    backend = resolve_backend(config.routing.backend)
    ifaces = detect.detect_interfaces()
    declared = [r for r in config.rules if r.enabled and r.interface]
    cmap = _cidr_map(config, config.rules, _warn)

    problems, plan = preflight(config, declared, cmap, backend,
                               ifaces=ifaces, warn=_warn)
    if problems:
        raise RuntimeError("配置预检未通过（未做任何改动）：\n  - "
                           + "\n  - ".join(problems))

    active = [r for r in declared if plan.get(r.name)]
    fam_text = {r.name: "+".join(f"v{fp.family}" for fp in plan[r.name]) for r in active}
    if fam_text:
        _log.info("apply_rules: 地址族 %s", fam_text)

    # mainroute 的明细路由用 replace 重建，无需先删：只清理不再需要的，
    # 避免"清空再重建"造成瞬时断流。
    keep = ({(c, r.interface) for r in active for fp in plan[r.name] for c in fp.cidrs}
            if backend == "mainroute" else None)
    clear_rules(config, dry_run=dry_run, cidr_map=cmap, warn=_warn, keep_mainroute=keep)

    try:
        _apply_active(config, declared, plan, backend, dry_run=dry_run)
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
