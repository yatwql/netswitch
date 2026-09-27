"""通用 CIDR 来源：URL+JSON 字段拉取、**域名解析**、缓存、extra 合并、规范化（IPv4+IPv6）。

数据来源有三种，可同时使用，最终合并去重：

1. `cidrs.extra`：字面 IP / CIDR；
2. `cidrs.url` + `fields`：远程 JSON 中的 CIDR 列表（带 TTL 缓存与失败回退）；
3. `cidrs.domains`：域名/通配符（**apply 时解析**，带独立 TTL 缓存与失败回退）。

域名语义（见 docs/technical.md 4.9）：
- `example.com` → 解析 A/AAAA 全部地址（→ `/32`、`/128`）；
- `*.example.com` → 解析 apex，并做一次 **DNS 通配符探测**（对随机标签发查询，
  只有该域真有通配记录时才会返回）；**通配符 ≠ 所有子域**（DNS 无法枚举），
  需要精确覆盖时请把具体子域也写进 `domains`。

所有对外结果都经过 `ipaddress` 规范化，非法项一律丢弃并告警，绝不把垃圾喂给
`nft` / `ip rule`；下载体量有上限，响应必须是 JSON 对象。
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import time
import urllib.parse
import urllib.request
import uuid
from typing import Iterable, List, Optional, Tuple

from . import log
from .model import CidrsCfg

_log = log.get_logger()

# 单个 CIDR 源的响应上限（GitHub meta 约 20KB；给足余量同时避免误拉大文件）
MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024
# 单条规则最多允许的域名数量（限制 apply 时的 DNS 查询量）
MAX_DOMAINS = 64
# DNS 通配符探测用的随机标签前缀
_PROBE_LABEL = "netswitch-wildcard-probe"

_LABEL_RE = re.compile(r"^[A-Za-z0-9_-]{1,63}$")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")
_SPLIT_RE = re.compile(r"[\s,;，、]+")


# ---------- 规范化 / 校验 ----------

def normalize_cidr(value) -> Optional[str]:
    """规范化为 CIDR 字符串（IPv4/IPv6 均支持）；非法时返回 None。"""
    try:
        net = ipaddress.ip_network(str(value).strip(), strict=False)
    except (TypeError, ValueError):
        return None
    return str(net)


def normalize_addr(value) -> Optional[str]:
    """单个地址 → 主机 CIDR（`/32` 或 `/128`）；非地址返回 None。"""
    try:
        addr = ipaddress.ip_address(str(value).strip())
    except (TypeError, ValueError):
        return None
    return f"{addr}/{32 if addr.version == 4 else 128}"


def family_of(cidr: str) -> int:
    """CIDR 的地址族：6 = IPv6，4 = IPv4。"""
    return 6 if ":" in str(cidr) else 4


def split_families(cidrs: Iterable[str]) -> Tuple[List[str], List[str]]:
    """拆分为 (IPv4 列表, IPv6 列表)，各自去重排序。"""
    v4, v6 = [], []
    for c in cidrs:
        (v6 if family_of(c) == 6 else v4).append(str(c))
    return sorted(set(v4)), sorted(set(v6))


def validate_domain(pattern: str) -> str:
    """校验并规范化域名/通配符写法，非法时抛 ValueError。"""
    raw = (pattern or "").strip().rstrip(".")
    if not raw:
        raise ValueError("域名不能为空")
    if len(raw) > 253:
        raise ValueError(f"域名过长（>{253}）：{raw[:40]}…")
    if normalize_addr(raw):
        raise ValueError(f"{raw} 看起来是 IP 地址，请作为 CIDR 输入（extra）")
    wildcard = raw.startswith("*.")
    body = raw[2:] if wildcard else raw
    if not body:
        raise ValueError(f"域名写法非法：{pattern}（`*.` 后必须有域名）")
    if "*" in body:
        raise ValueError(f"通配符只能出现在最左侧，例如 *.example.com：{pattern}")
    for label in body.split("."):
        if not _LABEL_RE.match(label):
            raise ValueError(
                f"域名写法非法：{pattern}（每段只允许字母/数字/-/_，长度 1..63）"
            )
    return ("*." if wildcard else "") + body.lower()


def classify_target(text: str) -> Tuple[str, str]:
    """把单个用户输入归类为 `("cidr", 规范化值)` 或 `("domain", 规范化值)`。

    支持直接粘贴 URL（自动取主机名）。无法识别时抛 ValueError。
    """
    raw = (text or "").strip()
    if not raw:
        raise ValueError("空的输入项")
    if _SCHEME_RE.match(raw):
        host = urllib.parse.urlsplit(raw).hostname or ""
        if not host:
            raise ValueError(f"无法从 URL 提取主机名：{raw}")
        raw = host
    if "/" in raw and not raw.startswith("*."):
        net = normalize_cidr(raw)
        if net:
            return "cidr", net
        raise ValueError(f"既不是合法 CIDR 也不是域名：{raw}")
    addr = normalize_addr(raw)
    if addr:
        return "cidr", addr
    return "domain", validate_domain(raw)


def parse_targets(text: str) -> Tuple[List[str], List[str]]:
    """解析交互输入（逗号/空格/分号分隔）→ (CIDR 列表, 域名列表)。

    任一项无法识别就抛 ValueError（交互场景宁可直接报错，也不要静默丢弃）。
    """
    tokens = [t for t in _SPLIT_RE.split(text or "") if t]
    if not tokens:
        raise ValueError("没有可解析的内容")
    cidr_list: List[str] = []
    domains: List[str] = []
    errors: List[str] = []
    for tok in tokens:
        try:
            kind, value = classify_target(tok)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        (cidr_list if kind == "cidr" else domains).append(value)
    if errors:
        raise ValueError("；".join(errors))
    cidr_list = list(dict.fromkeys(cidr_list))
    domains = list(dict.fromkeys(domains))
    if len(domains) > MAX_DOMAINS:
        raise ValueError(f"域名数量 {len(domains)} 超过上限 {MAX_DOMAINS}")
    return cidr_list, domains


# ---------- 拉取 / 缓存 ----------

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
    if not cache_file or not os.path.exists(cache_file):
        return None
    try:
        with open(cache_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _save_cache(cache_file: str, *, url_cidrs: List[str], domains: List[str],
                domain_cidrs: List[str], merged: List[str]) -> None:
    if not cache_file:
        return
    os.makedirs(os.path.dirname(cache_file) or ".", exist_ok=True)
    now = time.time()
    payload = {
        "fetched_at": now,                 # URL 部分的时间戳
        "url_cidrs": url_cidrs,            # URL 来源的 CIDR
        "domains_at": now,                 # 域名部分的时间戳
        "domain_patterns": domains,        # 解析时的域名列表（变了就重解析）
        "domain_cidrs": domain_cidrs,      # 域名解析结果
        "cidrs": merged,                   # 最终合并结果（便于人工查看）
    }
    with open(cache_file, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


def _finish(cidrs: Iterable[str], warn=None) -> List[str]:
    """规范化（IPv4/IPv6 均保留）、去重并排序（非法项告警后丢弃）。"""
    ok: set = set()
    bad: List[str] = []
    for c in cidrs:
        n = normalize_cidr(c)
        if n:
            ok.add(n)
        else:
            bad.append(str(c))
    if bad and warn:
        preview = "、".join(bad[:5]) + ("…" if len(bad) > 5 else "")
        warn(f"忽略 {len(bad)} 条非法 CIDR（{preview}）")
    return sorted(ok)


# ---------- 域名解析 ----------

def _resolve_host(host: str, *, warn=None) -> List[str]:
    """解析单个主机名（A + AAAA）→ 主机 CIDR 列表；失败时告警并返回 []。"""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        if warn:
            warn(f"域名解析失败：{host}（{exc}）")
        return []
    out: List[str] = []
    for info in infos:
        addr = normalize_addr(info[4][0])
        if addr:
            out.append(addr)
    return sorted(set(out))


def resolve_domains(patterns: Iterable[str], *, warn=None,
                    wildcard_probe: bool = True) -> List[str]:
    """域名/通配符 → CIDR 列表（v4+v6）。

    `*.example.com` 会解析 apex，并在 `wildcard_probe=True` 时对随机子域做一次
    探测查询：只有该域**真的配置了 DNS 通配符**时才会返回地址（DNS 无法枚举子域，
    所以这是唯一可行的近似；需要精确覆盖请显式列出子域）。
    """
    out: List[str] = []
    for pattern in patterns:
        if pattern.startswith("*."):
            apex = pattern[2:]
            out += _resolve_host(apex, warn=warn)
            if wildcard_probe:
                probe = f"{_PROBE_LABEL}-{uuid.uuid4().hex[:8]}.{apex}"
                hits = _resolve_host(probe, warn=None)
                if hits:
                    out += hits
                    _log.info("域名 %s：检测到 DNS 通配符记录，并入 %d 个地址",
                              pattern, len(hits))
        else:
            out += _resolve_host(pattern, warn=warn)
    return _finish(out, warn)


# ---------- 对外入口 ----------

def fetch_cidrs(cfg: CidrsCfg, *, warn=None) -> List[str]:
    """按配置获取 CIDR 列表（extra + URL + 域名，含缓存与失败回退）。

    缓存结构（`cache-<规则名>.json`）分成 URL 与域名两部分，各自带时间戳：
    域名列表变了会重新解析；只有 DNS 失败时才回退到缓存（并告警）。
    """
    extra = list(cfg.extra or [])
    domains = list(cfg.domains or [])
    if cfg.source == "manual" and not domains:
        return _finish(extra, warn)

    ttl = (cfg.ttl_hours or 0) * 3600
    now = time.time()
    cached = _load_cache(cfg.cache_file) if cfg.cache_file else None
    # 兼容旧缓存结构（只有 fetched_at + cidrs，视作 URL 部分）
    cached_url = list(cached.get("url_cidrs", cached.get("cidrs", []))) if cached else []
    cached_domain_cidrs = list(cached.get("domain_cidrs", [])) if cached else []
    cached_patterns = list(cached.get("domain_patterns", [])) if cached else []
    url_fresh = bool(cached and (now - cached.get("fetched_at", 0)) < ttl)
    domains_fresh = bool(cached and cached_patterns == domains
                         and (now - cached.get("domains_at", 0)) < ttl)

    # 1) URL 部分
    url_cidrs: List[str] = []
    if cfg.source == "url" and cfg.url:
        _log.info("cidrs: source=%s url=%s", cfg.source, cfg.url)
        if url_fresh:
            url_cidrs = cached_url
        else:
            try:
                url_cidrs = _download(cfg.url, cfg.fields)
                _log.info("cidrs: 拉取 %d 条", len(url_cidrs))
            except Exception as exc:  # noqa: BLE001
                _log.warning("cidrs: 拉取失败 %s", exc)
                if cached_url:
                    url_cidrs = cached_url
                    if warn:
                        warn(f"CIDR 源拉取失败，已回退缓存：{exc}")
                elif warn:
                    warn(f"CIDR 源拉取失败且无缓存：{exc}")

    # 2) 域名部分
    domain_cidrs: List[str] = []
    if domains:
        try:
            parsed = [validate_domain(d) for d in domains]
        except ValueError as exc:
            if warn:
                warn(str(exc))
            parsed = []
        if domains_fresh:
            domain_cidrs = cached_domain_cidrs
        elif parsed:
            _log.info("cidrs: 解析域名 %s", parsed)
            resolved = resolve_domains(parsed, warn=warn,
                                      wildcard_probe=cfg.wildcard_probe)
            if resolved or not cached_domain_cidrs:
                domain_cidrs = resolved
            else:
                domain_cidrs = cached_domain_cidrs
                if warn:
                    warn("域名解析结果为空，已回退缓存")
        domains = parsed

    merged = _finish(extra + url_cidrs + domain_cidrs, warn)
    if cfg.cache_file and (cfg.source == "url" or domains):
        try:
            _save_cache(cfg.cache_file, url_cidrs=_finish(url_cidrs),
                        domains=domains, domain_cidrs=_finish(domain_cidrs),
                        merged=merged)
        except OSError as exc:  # pragma: no cover - 权限/磁盘
            _log.warning("cidrs: 写缓存失败 %s", exc)
    return merged
