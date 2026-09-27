"""JSON 配置加载、默认值填充、探测填充、校验、写回（零第三方依赖）。

安全（见 docs/technical.md 4.7）：
- 配置里的**标识符**（`routing.nft_table`、`rules[].name`）会被拼进 nft 脚本/命令，
  必须白名单校验；表号限定在派生区间（253/254/255 是内核保留表）。
- 配置里的**路径**（`state_file`、`cidrs.cache_file`）会被 root 进程写入，
  必须落在数据目录（默认 `<仓库>/data`，可用 `NETSWITCH_DATA_DIR` 覆盖）之内，
  避免越权写任意文件。
- 相对路径统一按仓库根解析（与 `log.py` 一致），不受当前工作目录影响。
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import detect, ip, log
from . import cidrs as cidrs_mod
from .model import (
    CidrsCfg,
    Config,
    IfaceCfg,
    MetricsCfg,
    RoutingCfg,
    RuleCfg,
)

DEFAULT_CONFIG = "data/config/config.json"
DEFAULT_CONFIG_EXAMPLE = "data/config/config.example.json"

REPO_ROOT = Path(__file__).resolve().parents[3]          # src/python/netswitch -> 仓库根
_BACKENDS = ("auto", "nftables", "iprule", "mainroute")
_CIDR_SOURCES = ("url", "manual")
_RULE_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,32}$")
_IDENT_RE = re.compile(r"^[A-Za-z0-9_]{1,32}$")
_IFACE_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,32}$")

_log = log.get_logger()


def data_dir() -> Path:
    """数据目录（配置、状态、缓存都必须在其中）。"""
    return Path(os.environ.get("NETSWITCH_DATA_DIR") or (REPO_ROOT / "data"))


def _data_path(value: Any, field: str) -> str:
    """校验并归一化数据文件路径：相对路径按仓库根解析，必须位于数据目录内。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 必须是非空字符串")
    raw = value.strip()
    p = Path(raw)
    if not p.is_absolute():
        p = REPO_ROOT / p
    p = Path(os.path.normpath(str(p)))
    root = data_dir()
    if p != root and root not in p.parents:
        raise ValueError(
            f"{field} 必须位于数据目录 {root} 之内（当前 {raw}）；"
            f"如需自定义请设置 NETSWITCH_DATA_DIR"
        )
    return str(p)


def _probe_map(provided: Optional[Dict[str, detect.Interface]] = None
               ) -> Dict[str, detect.Interface]:
    if provided is not None:
        return dict(provided)
    try:
        return {i.name: i for i in detect.detect_interfaces()}
    except Exception:  # noqa: BLE001
        return {}


def _versions(value: Any, field: str) -> Optional[List[str]]:
    """校验地址族列表（v4/v6）；None 表示“未配置”（规则级用全局）。"""
    if value is None:
        return None
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ValueError(f'{field} 必须是数组，例如 ["v4", "v6"]')
    out = [str(v).strip().lower() for v in value]
    if not out:
        raise ValueError(f"{field} 不能为空（至少包含 v4 或 v6）")
    bad = [v for v in out if v not in ip.VERSION_KEYS]
    if bad:
        raise ValueError(
            f"{field} 含非法值 {bad}（可选：{', '.join(ip.VERSION_KEYS)}）"
        )
    if len(set(out)) != len(out):
        raise ValueError(f"{field} 存在重复项：{out}")
    return out


def _ip_addr(value: Any, field: str, version: int) -> Optional[str]:
    """校验网关地址（允许 `fe80::1%eth0` 这类带 zone 的写法）。"""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 必须是非空字符串")
    raw = value.strip()
    core = raw.split("%", 1)[0]          # 去掉 zone id 后再校验地址本身
    try:
        addr = ipaddress.ip_address(core)
    except ValueError:
        raise ValueError(f"{field} 不是合法的 IP 地址：{raw!r}") from None
    if addr.version != version:
        raise ValueError(f"{field} 必须是 IPv{version} 地址：{raw!r}")
    return raw


def _parse_interfaces(raw: Any, probe: bool,
                      probe_map: Optional[Dict[str, detect.Interface]] = None
                      ) -> List[IfaceCfg]:
    pmap = _probe_map(probe_map) if probe or probe_map is not None else {}
    if not raw:
        # 自动探测物理网卡；metric 不填 = 不调整
        return [
            IfaceCfg(name=i.name, gateway=i.gateway, metric=None, type=i.type)
            for i in pmap.values()
        ]
    out: List[IfaceCfg] = []
    for item in raw:
        name = item.get("name")
        if not name:
            raise ValueError("interfaces[].name 不能为空")
        if not _IFACE_RE.match(str(name)):
            raise ValueError(f"interfaces[].name 非法：{name!r}（只允许字母/数字/._:-）")
        gateway = item.get("gateway")
        gateway6 = item.get("gateway6")
        metric = item.get("metric")
        if gateway is None and name in pmap:
            gateway = pmap[name].gateway
        if gateway6 is None and name in pmap:
            gateway6 = pmap[name].gateway6
        out.append(
            IfaceCfg(
                name=name,
                gateway=gateway,
                gateway6=gateway6,
                metric=int(metric) if metric is not None else None,
                type=item.get("type") or (pmap[name].type if name in pmap else None),
            )
        )
    return out


def _parse_rules(raw: Any) -> List[RuleCfg]:
    out: List[RuleCfg] = []
    for idx, item in enumerate(raw or []):
        name = item.get("name")
        if not name:
            raise ValueError(f"rules[{idx}].name 不能为空")
        c = item.get("cidrs") or {}
        out.append(
            RuleCfg(
                name=name,
                enabled=bool(item.get("enabled", True)),
                interface=item.get("interface"),
                cidrs=CidrsCfg(
                    source=c.get("source", "url"),
                    url=c.get("url"),
                    fields=list(c.get("fields") or []),
                    ttl_hours=int(c.get("ttl_hours", 24)),
                    extra=list(c.get("extra") or []),
                    cache_file=c.get("cache_file"),
                    ip_versions=_versions(c.get("ip_versions"),
                                         f"rules[{idx}].cidrs.ip_versions"),
                    domains=[str(d) for d in (c.get("domains") or [])],
                    wildcard_probe=bool(c.get("wildcard_probe", True)),
                ),
                table_id=item.get("table_id"),
                fwmark=item.get("fwmark"),
            )
        )
    return out


def _fill_rule_defaults(config: Config) -> None:
    """自动分配 table_id / fwmark / cache_file，保证唯一。"""
    for idx, r in enumerate(config.rules):
        if r.table_id is None:
            r.table_id = ip.TABLE_ID_MIN + idx
        if r.fwmark is None:
            r.fwmark = 1 + idx
        if r.cidrs.cache_file is None:
            r.cidrs.cache_file = f"data/config/cache-{r.name}.json"

    tables = [r.table_id for r in config.rules]
    if len(tables) != len(set(tables)):
        raise ValueError("rules[].table_id 存在冲突，请为每条规则设置不同值")
    marks = [r.fwmark for r in config.rules]
    if len(marks) != len(set(marks)):
        raise ValueError("rules[].fwmark 存在冲突，请为每条规则设置不同值")
    names = [r.name for r in config.rules]
    if len(names) != len(set(names)):
        raise ValueError("rules[].name 必须唯一")


def _validate(config: Config) -> None:
    """配置语义与安全校验：不安全/非法的值直接拒绝（宁可不启动，也不要带病改动网络）。"""
    if config.routing.backend not in _BACKENDS:
        raise ValueError(
            f"routing.backend 非法：{config.routing.backend!r}（可选：{', '.join(_BACKENDS)}）"
        )
    if not _IDENT_RE.match(config.routing.nft_table or ""):
        raise ValueError("routing.nft_table 只允许字母/数字/下划线，长度 1..32")
    _versions(config.routing.ip_versions, "routing.ip_versions")
    if config.metrics.preferred < 0 or config.metrics.fallback < 0:
        raise ValueError("metrics.preferred / metrics.fallback 不能为负数")

    for idx, ic in enumerate(config.interfaces):
        where = f"interfaces[{idx}]（{ic.name}）"
        _ip_addr(ic.gateway, f"{where} gateway", 4)
        _ip_addr(ic.gateway6, f"{where} gateway6", 6)

    for idx, r in enumerate(config.rules):
        where = f"rules[{idx}]（{r.name}）"
        if not _RULE_NAME_RE.match(str(r.name)):
            raise ValueError(
                f"{where} 名称非法：只允许字母/数字/下划线/短横线/点，长度 1..32"
            )
        if r.interface is not None and not _IFACE_RE.match(str(r.interface)):
            raise ValueError(f"{where} interface 非法：{r.interface!r}")
        _versions(r.cidrs.ip_versions, f"{where} cidrs.ip_versions")
        if r.table_id is None or not (
            ip.TABLE_ID_MIN <= int(r.table_id) <= ip.TABLE_ID_MAX
        ):
            raise ValueError(
                f"{where} table_id 必须在 {ip.TABLE_ID_MIN}..{ip.TABLE_ID_MAX} 之间"
                f"（253/254/255 为内核保留表：default/main/local）"
            )
        if r.fwmark is None or not (1 <= int(r.fwmark) <= 0xFFFFFFFF):
            raise ValueError(f"{where} fwmark 必须在 1..0xFFFFFFFF 之间（0 无法用于打标）")
        if r.cidrs.source not in _CIDR_SOURCES:
            raise ValueError(
                f"{where} cidrs.source 非法：{r.cidrs.source!r}"
                f"（可选：{', '.join(_CIDR_SOURCES)}）"
            )
        if r.cidrs.ttl_hours < 0:
            raise ValueError(f"{where} cidrs.ttl_hours 不能为负数")
        if len(r.cidrs.domains) > cidrs_mod.MAX_DOMAINS:
            raise ValueError(
                f"{where} cidrs.domains 最多 {cidrs_mod.MAX_DOMAINS} 条"
                f"（当前 {len(r.cidrs.domains)}）"
            )
        for d in r.cidrs.domains:
            try:
                cidrs_mod.validate_domain(d)
            except ValueError as exc:
                raise ValueError(f"{where} {exc}") from None

    set_names = [ip.nft_set_name(r.name) for r in config.rules]
    dup = {n for n in set_names if set_names.count(n) > 1}
    if dup:
        raise ValueError(
            f"规则名归一化后在 nft 中重名（{', '.join(sorted(dup))}）："
            f"请修改规则名，避免只用 -/. 区分"
        )


def load(path: str = DEFAULT_CONFIG, *, probe: bool = True,
         probe_map: Optional[Dict[str, detect.Interface]] = None) -> Config:
    """加载并校验配置。path 不存在时返回空配置（全部默认/探测）。

    `probe_map` 可传入调用方已探测到的网卡快照，避免重复执行 `ip` 命令
    （TUI 每次刷新都会调用本函数）。
    """
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    else:
        raw = {}

    metrics = raw.get("metrics") or {}
    routing = raw.get("routing") or {}

    config = Config(
        version=int(raw.get("version", 1)),
        interfaces=_parse_interfaces(raw.get("interfaces"), probe=probe,
                                    probe_map=probe_map),
        metrics=MetricsCfg(
            preferred=int(metrics.get("preferred", 100)),
            fallback=int(metrics.get("fallback", 600)),
        ),
        routing=RoutingCfg(
            backend=routing.get("backend", "auto"),
            nft_table=routing.get("nft_table", "netswitch"),
            ip_versions=_versions(routing.get("ip_versions"),
                                  "routing.ip_versions") or ["v4"],
        ),
        rules=_parse_rules(raw.get("rules")),
        state_file=raw.get("state_file", "data/config/state.json"),
    )
    _fill_rule_defaults(config)
    _validate(config)

    # 路径统一归一化（相对仓库根）并限制在数据目录内
    config.state_file = _data_path(config.state_file, "state_file")
    for idx, r in enumerate(config.rules):
        r.cidrs.cache_file = _data_path(
            r.cidrs.cache_file, f"rules[{idx}].cidrs.cache_file"
        )
    return config


def update_interfaces(path: str, fragment: List[dict]) -> None:
    """仅更新配置的 interfaces 段（保留 rules 等其它字段）。"""
    _log.info("config: 更新 interfaces（%d 项）-> %s", len(fragment), path)
    for item in fragment:
        name = item.get("name")
        if not name or not _IFACE_RE.match(str(name)):
            raise ValueError(f"interfaces[].name 非法：{name!r}")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    else:
        raw = {}
    raw["version"] = raw.get("version", 1)
    raw["interfaces"] = fragment
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(raw, fh, ensure_ascii=False, indent=2)


def _load_raw(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    return raw if isinstance(raw, dict) else {}


def _write_raw(path: str, raw: dict) -> None:
    """写回配置，并**重新加载校验**；失败则回滚为原内容（不让 apply 读到坏配置）。"""
    old = None
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            old = fh.read()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(raw, fh, ensure_ascii=False, indent=2)
    try:
        load(path, probe=False)
    except Exception:
        if old is None:
            try:
                os.remove(path)
            except OSError:  # pragma: no cover
                pass
        else:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(old)
        raise


def next_rule_name(path: str, prefix: str = "custom-") -> str:
    """生成下一个可用规则名（`custom-1`、`custom-2`…）。"""
    raw = _load_raw(path)
    used = {str(r.get("name")) for r in (raw.get("rules") or [])
            if isinstance(r, dict)}
    idx = 1
    while f"{prefix}{idx}" in used:
        idx += 1
    return f"{prefix}{idx}"


def add_rule(path: str, name: str, *, extra=None, domains=None, interface=None,
             ip_versions=None) -> str:
    """在配置里新增一条分流规则（**只写文件，不触碰网络**）。返回规则名。

    新规则在**下一次 `apply`** 时才生效；`interface` 留空表示暂不指定出口网卡
    （可在 TUI 选中后按 `e` 选择）。参数会在写入前校验/规范化。
    """
    name = (name or "").strip()
    if not _RULE_NAME_RE.match(name):
        raise ValueError("规则名非法：只允许字母/数字/下划线/短横线/点，长度 1..32")
    if interface is not None and not _IFACE_RE.match(str(interface)):
        raise ValueError(f"interface 非法：{interface!r}")
    versions = _versions(list(ip_versions) if ip_versions else None,
                         "cidrs.ip_versions")

    cidr_list: List[str] = []
    for item in (extra or []):
        norm = cidrs_mod.normalize_cidr(item)
        if not norm:
            raise ValueError(f"既不是合法 IP/CIDR：{item!r}")
        if norm not in cidr_list:
            cidr_list.append(norm)
    domain_list: List[str] = []
    for item in (domains or []):
        norm = cidrs_mod.validate_domain(str(item))
        if norm not in domain_list:
            domain_list.append(norm)
    if len(domain_list) > cidrs_mod.MAX_DOMAINS:
        raise ValueError(f"域名数量 {len(domain_list)} 超过上限 {cidrs_mod.MAX_DOMAINS}")
    if not cidr_list and not domain_list:
        raise ValueError("至少需要一个 IP/CIDR 或一个域名")

    raw = _load_raw(path)
    raw.setdefault("version", 1)
    rules = raw.get("rules")
    if not isinstance(rules, list):
        rules = []
        raw["rules"] = rules
    if any(isinstance(r, dict) and r.get("name") == name for r in rules):
        raise ValueError(f"规则名已存在：{name}")

    cidrs_cfg: dict = {"source": "manual"}
    if cidr_list:
        cidrs_cfg["extra"] = cidr_list
    if domain_list:
        cidrs_cfg["domains"] = domain_list
    if versions:
        cidrs_cfg["ip_versions"] = versions
    rule: dict = {"name": name, "enabled": True, "cidrs": cidrs_cfg}
    if interface:
        rule["interface"] = interface
    rules.append(rule)
    _write_raw(path, raw)
    _log.info("config: 新增规则 %s（网段 %d，域名 %s，出口 %s，仅写配置未生效）",
              name, len(cidr_list), domain_list or "-", interface or "-")
    return name


def remove_rule(path: str, name: str) -> bool:
    """从配置里删除一条分流规则（**只写文件，不触碰网络**）。返回是否删除。

    当前已生效的分流产物会在下一次 `apply` 时按签名清理掉。
    """
    raw = _load_raw(path)
    rules = raw.get("rules")
    if not isinstance(rules, list) or not rules:
        return False
    kept = [r for r in rules if not (isinstance(r, dict) and r.get("name") == name)]
    if len(kept) == len(rules):
        return False
    raw["rules"] = kept
    _write_raw(path, raw)
    _log.info("config: 删除规则 %s（仅写配置；下次 apply 会清理其分流产物）", name)
    return True


def update_rule_interface(path: str, rule_name: str, interface) -> bool:
    """写回某条规则的 interface（保留其它字段）。interface 为空 = 不分流（删除该字段）。

    返回是否找到并更新成功。
    """
    if interface is not None and not _IFACE_RE.match(str(interface)):
        raise ValueError(f"interface 非法：{interface!r}")
    if not os.path.exists(path):
        return False
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    found = False
    for r in raw.get("rules", []):
        if r.get("name") == rule_name:
            if interface:
                r["interface"] = interface
            else:
                r.pop("interface", None)
            found = True
            break
    if not found:
        return False
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(raw, fh, ensure_ascii=False, indent=2)
    _log.info("config: 规则 %s 生效网卡 -> %s（%s）", rule_name, interface, path)
    return True


def seed_default_rules(path: str = DEFAULT_CONFIG) -> bool:
    """若配置的 rules 为空，则从同目录 config.example.json 拷贝缺省规则。

    返回是否写入。缺省规则定义在 `config.example.json`（数据，非代码写死）。
    """
    if not os.path.exists(path):
        return False
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    if raw.get("rules"):
        return False
    example = os.path.join(os.path.dirname(path) or ".",
                           os.path.basename(DEFAULT_CONFIG_EXAMPLE))
    if not os.path.exists(example):
        return False
    with open(example, "r", encoding="utf-8") as fh:
        ex = json.load(fh)
    rules = ex.get("rules") or []
    if not rules:
        return False
    raw["rules"] = rules
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(raw, fh, ensure_ascii=False, indent=2)
    _log.info("config: 写入缺省规则 %d 条 -> %s", len(rules), path)
    return True
