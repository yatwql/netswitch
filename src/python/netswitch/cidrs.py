"""通用 CIDR 来源：URL+JSON 字段拉取、缓存、extra 合并。"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import List

from . import log
from .model import CidrsCfg

_log = log.get_logger()


def _is_ipv4(cidr: str) -> bool:
    return ":" not in cidr and "/" in cidr


def _download(url: str, fields: List[str]) -> List[str]:
    req = urllib.request.Request(url, headers={"User-Agent": "netswitch"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    out: List[str] = []
    for f in fields:
        values = data.get(f, [])
        if isinstance(values, list):
            out.extend(str(v) for v in values)
    return sorted({c for c in out if _is_ipv4(c)})


def _load_cache(cache_file: str):
    if not os.path.exists(cache_file):
        return None
    try:
        with open(cache_file, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _save_cache(cache_file: str, cidrs: List[str]) -> None:
    os.makedirs(os.path.dirname(cache_file) or ".", exist_ok=True)
    with open(cache_file, "w", encoding="utf-8") as fh:
        json.dump({"fetched_at": time.time(), "cidrs": cidrs}, fh, indent=2)


def _finish(cidrs: List[str]) -> List[str]:
    """去重并过滤为 IPv4 CIDR（v1 仅 IPv4）。"""
    return sorted({c for c in cidrs if _is_ipv4(c)})


def fetch_cidrs(cfg: CidrsCfg, *, warn=None) -> List[str]:
    """按配置获取 CIDR 列表（含缓存与 extra 合并）。warn 为告警回调。"""
    _log.info("cidrs: source=%s url=%s", cfg.source, cfg.url)
    cidrs: List[str] = list(cfg.extra or [])

    if cfg.source == "manual":
        return _finish(cidrs)

    if not cfg.url:
        return _finish(cidrs)

    cached = _load_cache(cfg.cache_file) if cfg.cache_file else None
    ttl = (cfg.ttl_hours or 0) * 3600
    fresh = bool(cached and (time.time() - cached.get("fetched_at", 0)) < ttl)

    if fresh:
        cidrs.extend(cached.get("cidrs", []))
    else:
        try:
            fetched = _download(cfg.url, cfg.fields)
            if cfg.cache_file:
                _save_cache(cfg.cache_file, fetched)
            cidrs.extend(fetched)
            _log.info("cidrs: 拉取 %d 条", len(fetched))
        except Exception as exc:  # noqa: BLE001
            _log.warning("cidrs: 拉取失败 %s", exc)
            if cached:
                cidrs.extend(cached.get("cidrs", []))
                if warn:
                    warn(f"CIDR 源拉取失败，已回退缓存：{exc}")
            elif warn:
                warn(f"CIDR 源拉取失败且无缓存：{exc}")

    return _finish(cidrs)
