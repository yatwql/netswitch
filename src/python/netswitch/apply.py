"""apply / revert 编排（FR7）。

状态（`data/config/state.json`）语义：
- `original_defaults` **只在首次 apply 时记录**，之后不再覆盖 —— 否则第二次 apply
  抓到的"原始值"已被自己改过，revert 会恢复不回去（见 docs/review-findings.md）。
- `tables` / `backend` 记录本程序创建的路由表与后端，供 revert 清理改配置前的残留。
- revert 成功后删除状态文件，下一次 apply 重新建立基线。
"""
from __future__ import annotations

import json
import os
from typing import List, Optional

from . import exec as ex, iface, ip, log, routing
from .model import Config

STATE_VERSION = 1

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


def _load_state(config: Config) -> dict:
    if not os.path.exists(config.state_file):
        return {"original_defaults": [], "applied_rules": []}
    try:
        with open(config.state_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        _log.warning("state: 读取 %s 失败（%s），按无记录处理", config.state_file, exc)
        return {"original_defaults": [], "applied_rules": []}
    if not isinstance(data, dict):
        return {"original_defaults": [], "applied_rules": []}
    data.setdefault("original_defaults", [])
    data.setdefault("applied_rules", [])
    return data


def _save_state(config: Config, defaults: List[dict], *,
                applied_rules: Optional[List[str]] = None) -> None:
    """写入状态文件；已存在的 `original_defaults` 优先保留（revert 基线不漂移）。"""
    path = config.state_file
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    old = _load_state(config)
    state = {
        "version": STATE_VERSION,
        "original_defaults": old.get("original_defaults") or defaults,
        "applied_rules": ([r.name for r in config.rules if r.enabled]
                          if applied_rules is None else applied_rules),
        "backend": routing.resolve_backend(config.routing.backend),
        "tables": sorted({r.table_id for r in config.rules if r.table_id}),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)


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
    """撤销全部改动：清理本程序产物 + 恢复原始默认路由。"""
    ex.require_root()
    _log.info("revert: 开始（dry_run=%s）", dry_run)
    state = _load_state(config)
    original = [d for d in (state.get("original_defaults") or []) if d.get("dev")]
    hist_tables = {t for t in (state.get("tables") or []) if isinstance(t, int)}
    errors = 0

    if not original and not state.get("applied_rules"):
        print("[warn] 未找到应用记录（state.json 缺失或为空）："
              "将只清理本程序的分流产物")

    try:
        routing.clear_rules(config, dry_run=dry_run, tables=hist_tables)
    except Exception as exc:  # noqa: BLE001
        print(f"[error] 清理分流规则失败: {exc}")
        errors += 1

    for d in original:
        try:
            iface.set_metric(d["dev"], d.get("metric"), d.get("gateway"),
                             dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001
            print(f"[error] 恢复 {d['dev']} 默认路由失败: {exc}")
            errors += 1

    if not dry_run and errors == 0 and os.path.exists(config.state_file):
        try:
            os.remove(config.state_file)
            _log.info("revert: 已删除状态文件 %s", config.state_file)
        except OSError as exc:  # pragma: no cover - 权限异常
            _log.warning("revert: 删除状态文件失败：%s", exc)

    _log.info("revert: 完成，失败 %d 项", errors)
    return errors
