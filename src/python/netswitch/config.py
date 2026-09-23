"""JSON 配置加载、默认值填充、探测填充、校验、写回（零第三方依赖）。"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from . import detect, log
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
_log = log.get_logger()


def _probe_map() -> Dict[str, detect.Interface]:
    try:
        return {i.name: i for i in detect.detect_interfaces()}
    except Exception:  # noqa: BLE001
        return {}


def _parse_interfaces(raw: Any, probe: bool) -> List[IfaceCfg]:
    pmap = _probe_map() if probe else {}
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
        gateway = item.get("gateway")
        metric = item.get("metric")
        if gateway is None and name in pmap:
            gateway = pmap[name].gateway
        out.append(
            IfaceCfg(
                name=name,
                gateway=gateway,
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
            r.table_id = 200 + idx
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


def load(path: str = DEFAULT_CONFIG, *, probe: bool = True) -> Config:
    """加载并校验配置。path 不存在时返回空配置（全部默认/探测）。"""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    else:
        raw = {}

    metrics = raw.get("metrics") or {}
    routing = raw.get("routing") or {}

    config = Config(
        version=int(raw.get("version", 1)),
        interfaces=_parse_interfaces(raw.get("interfaces"), probe=probe),
        metrics=MetricsCfg(
            preferred=int(metrics.get("preferred", 100)),
            fallback=int(metrics.get("fallback", 600)),
        ),
        routing=RoutingCfg(
            backend=routing.get("backend", "auto"),
            nft_table=routing.get("nft_table", "netswitch"),
        ),
        rules=_parse_rules(raw.get("rules")),
        state_file=raw.get("state_file", "data/config/state.json"),
    )
    _fill_rule_defaults(config)
    return config


def update_interfaces(path: str, fragment: List[dict]) -> None:
    """仅更新配置的 interfaces 段（保留 rules 等其它字段）。"""
    _log.info("config: 更新 interfaces（%d 项）-> %s", len(fragment), path)
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


def update_rule_interface(path: str, rule_name: str, interface) -> bool:
    """写回某条规则的 interface（保留其它字段）。interface 为空 = 不分流（删除该字段）。

    返回是否找到并更新成功。
    """
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
