"""apply / revert 编排（FR7）。"""
from __future__ import annotations

import json
import os
from typing import List

from . import exec as ex, iface, ip, log, routing
from .model import Config

_log = log.get_logger()


def _current_defaults() -> List[dict]:
    out = []
    for r in ip.route_list():
        if r.get("dst") == "default":
            out.append({
                "dev": r.get("dev"),
                "gateway": r.get("gateway"),
                "metric": r.get("metric"),
                "src": r.get("prefsrc") or r.get("src"),
            })
    return out


def _save_state(config: Config, defaults: List[dict]) -> None:
    path = config.state_file
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    state = {
        "version": 1,
        "original_defaults": defaults,
        "applied_rules": [r.name for r in config.rules if r.enabled],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)


def _load_state(config: Config) -> dict:
    if not os.path.exists(config.state_file):
        return {"original_defaults": [], "applied_rules": []}
    with open(config.state_file, "r", encoding="utf-8") as fh:
        return json.load(fh)


def apply(config: Config, *, dry_run: bool = False, force: bool = False) -> int:
    """按配置应用 metric + 分流规则。返回失败项数。"""
    ex.require_root()
    _log.info("apply: 开始（dry_run=%s force=%s）", dry_run, force)
    if not dry_run:
        _save_state(config, _current_defaults())

    errors = 0

    # metric
    for ic in config.interfaces:
        if ic.metric is None:
            continue
        if not ic.gateway:
            print(f"[skip] {ic.name}: 无网关，跳过 metric 调整")
            continue
        try:
            iface.set_metric(ic.name, ic.metric, ic.gateway, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001
            print(f"[error] 设置 {ic.name} metric 失败: {exc}")
            errors += 1

    # 分流规则
    try:
        routing.apply_rules(config, dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001
        print(f"[error] 应用分流规则失败: {exc}")
        errors += 1

    _log.info("apply: 完成，失败 %d 项", errors)
    return errors


def revert(config: Config, *, dry_run: bool = False) -> int:
    """撤销全部改动：清理规则 + 恢复原始默认路由。"""
    ex.require_root()
    _log.info("revert: 开始（dry_run=%s）", dry_run)
    state = _load_state(config)
    errors = 0

    try:
        routing.clear_rules(config, dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001
        print(f"[error] 清理分流规则失败: {exc}")
        errors += 1

    for d in state.get("original_defaults", []):
        try:
            iface.set_metric(d["dev"], d["metric"], d.get("gateway"),
                             dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001
            print(f"[error] 恢复 {d['dev']} 默认路由失败: {exc}")
            errors += 1

    _log.info("revert: 完成，失败 %d 项", errors)
    return errors
