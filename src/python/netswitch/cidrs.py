"""通用 CIDR 来源：URL+JSON 字段拉取、缓存、extra 合并、规范化。

所有对外结果都经过 `ipaddress` 规范化（v1 仅 IPv4）：
- 非法项（域名、IPv6、`1.2.3.4/999` 等）一律丢弃并告警，绝不把垃圾喂给
  `nft` / `ip rule`（真实命令失败会留下半成品状态）；
- 同一网段的不同写法（`192.168.1.5/24`）归一化为网络地址（`192.168.1.0/24`）；
- 下载体量有上限，响应必须是 JSON 对象。
"""
from __future__ import annotations

import ipaddress
import json
import os
import time
import urllib.request
from typing import Iterable, List, Optional

from . import log
from .model import CidrsCfg

_log = log.get_logger()

# 单个 CIDR 源的响应上限（GitHub meta 约 20KB；给足余量同时避免误拉大文件）
MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024


def _norm(value) -> Optional[str]:
    """规范化为 IPv4 CIDR 字符串；非 IPv4/非法时返回 None。"""
    try:
        net = ipaddress.ip_network(str(value).strip(), strict=False)
    except (TypeError, ValueError):
        return None
    return str(net) if net.version == 4 else None


def _download(url: str, fields: List[str]) -> List[str]:
    if not fields:
        raise ValueError("cidrs.fields 为空，无法从 JSON 中取字段")
    req = urllib.request.Request(url, headers={"User-Agent": "netswitch"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read(MAX_DOWNLOAD_BYTES + 1)
    if len(raw) > MAX_DOWNLOAD_BYTES:
        raise ValueError(f"响应超过 {MAX_DOWNLOAD_BYTES} 字节上限，已拒绝")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("响应不是 JSON 对象")
    out: List[str] = []
    for f in fields:
        values = data.get(f, [])
        if isinstance(values, list):
            out.extend(str(v) for v in values)
    return out


def _load_cache(cache_file: str):
    if not os.path.exists(cache_file):
        return None
    try:
        with open(cache_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _save_cache(cache_file: str, cidrs: List[str]) -> None:
    os.makedirs(os.path.dirname(cache_file) or ".", exist_ok=True)
    with open(cache_file, "w", encoding="utf-8") as fh:
        json.dump({"fetched_at": time.time(), "cidrs": cidrs}, fh, indent=2)


def _finish(cidrs: Iterable[str], warn=None) -> List[str]:
    """规范化、过滤为 IPv4 CIDR 并去重（非法项告警后丢弃）。"""
    ok: set = set()
    bad: List[str] = []
    for c in cidrs:
        n = _norm(c)
        if n:
            ok.add(n)
        else:
            bad.append(str(c))
    if bad and warn:
        preview = "、".join(bad[:5]) + ("…" if len(bad) > 5 else "")
        warn(f"忽略 {len(bad)} 条非法 CIDR（{preview}）")
    return sorted(ok)


def fetch_cidrs(cfg: CidrsCfg, *, warn=None) -> List[str]:
    """按配置获取 CIDR 列表（含缓存与 extra 合并）。warn 为告警回调。"""
    _log.info("cidrs: source=%s url=%s", cfg.source, cfg.url)
    cidrs: List[str] = list(cfg.extra or [])

    if cfg.source == "manual":
        return _finish(cidrs, warn)

    if not cfg.url:
        return _finish(cidrs, warn)

    cached = _load_cache(cfg.cache_file) if cfg.cache_file else None
    ttl = (cfg.ttl_hours or 0) * 3600
    fresh = bool(cached and (time.time() - cached.get("fetched_at", 0)) < ttl)

    if fresh:
        cidrs.extend(cached.get("cidrs", []))
    else:
        try:
            fetched = _download(cfg.url, cfg.fields)
            if cfg.cache_file:
                _save_cache(cfg.cache_file, _finish(fetched, warn))
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

    return _finish(cidrs, warn)
