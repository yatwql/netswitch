import json
import socket

import pytest

from netswitch import cidrs
from netswitch.model import CidrsCfg


class _Resp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self, n: int = -1) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_urlopen(body: bytes):
    def _open(req, timeout=0):
        return _Resp(body)
    return _open


# ---------- 规范化 / 过滤（P1-6） ----------

def test_normalize_and_drop_invalid():
    cfg = CidrsCfg(source="manual", extra=[
        "192.168.1.5/24",      # 归一化为网络地址
        "1.2.3.4/999",         # 非法前缀
        "example.com",         # 非 IP
        "2001:db8:99::5/48",   # v6 归一化后保留（双栈支持）
        "10.0.0.0/8",
        "10.0.0.0/8",          # 重复
        "8.8.8.8",             # 裸地址 -> /32
    ])
    assert cidrs.fetch_cidrs(cfg) == [
        "10.0.0.0/8", "192.168.1.0/24", "2001:db8:99::/48", "8.8.8.8/32"]


def test_invalid_cidrs_warn():
    msgs = []
    cfg = CidrsCfg(source="manual", extra=["1.2.3.4/999", "not-a-cidr"])
    cidrs.fetch_cidrs(cfg, warn=msgs.append)
    assert len(msgs) == 1 and "2 条非法 CIDR" in msgs[0]


def test_manual_source_dedup():
    cfg = CidrsCfg(source="manual", extra=["10.0.0.0/8", "10.0.0.0/8", "2001:db8::/32"])
    assert cidrs.fetch_cidrs(cfg) == ["10.0.0.0/8", "2001:db8::/32"]


def test_split_families():
    v4, v6 = cidrs.split_families(
        ["10.0.0.0/8", "2001:db8::/32", "10.0.0.0/8", "192.168.0.0/16"])
    assert v4 == ["10.0.0.0/8", "192.168.0.0/16"]
    assert v6 == ["2001:db8::/32"]


def test_url_fetch_merge_extra(monkeypatch):
    monkeypatch.setattr(cidrs, "_download", lambda u, f: ["140.82.112.0/20"])
    cfg = CidrsCfg(source="url", url="http://x", fields=["web"],
                   extra=["1.1.1.0/24"], cache_file=None)
    result = cidrs.fetch_cidrs(cfg)
    assert "140.82.112.0/20" in result
    assert "1.1.1.0/24" in result


def test_url_fetch_fallback_to_cache(monkeypatch, tmp_path):
    cache = tmp_path / "cache.json"
    cache.write_text(
        '{"fetched_at": 0, "cidrs": ["9.9.9.0/24"]}', encoding="utf-8"
    )
    # 拉取失败，回退缓存
    def boom(u, f):
        raise RuntimeError("network down")

    monkeypatch.setattr(cidrs, "_download", boom)
    cfg = CidrsCfg(source="url", url="http://x", fields=["web"],
                   cache_file=str(cache), ttl_hours=24)
    assert "9.9.9.0/24" in cidrs.fetch_cidrs(cfg)


def test_cache_written_normalized(monkeypatch, tmp_path):
    cache = tmp_path / "cache.json"
    monkeypatch.setattr(cidrs, "_download", lambda u, f: ["1.2.3.4/24", "junk"])
    cfg = CidrsCfg(source="url", url="http://x", fields=["web"],
                   cache_file=str(cache), ttl_hours=24)
    cidrs.fetch_cidrs(cfg)
    saved = cache.read_text(encoding="utf-8")
    assert "1.2.3.0/24" in saved and "junk" not in saved


def test_cache_corrupt_is_ignored(monkeypatch, tmp_path):
    cache = tmp_path / "cache.json"
    cache.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(cidrs, "_download", lambda u, f: ["1.1.1.0/24"])
    cfg = CidrsCfg(source="url", url="http://x", fields=["web"],
                   cache_file=str(cache), ttl_hours=24)
    assert cidrs.fetch_cidrs(cfg) == ["1.1.1.0/24"]


# ---------- 下载加固（P0-3 / P1-6） ----------

def test_download_rejects_missing_fields(monkeypatch):
    with pytest.raises(ValueError, match="fields"):
        cidrs._download("http://x", [])


def test_download_rejects_non_object(monkeypatch):
    monkeypatch.setattr(cidrs.urllib.request, "urlopen", _fake_urlopen(b"[1, 2]"))
    with pytest.raises(ValueError, match="JSON 对象"):
        cidrs._download("http://x", ["web"])


def test_download_rejects_oversized(monkeypatch):
    body = b'{"web": []}' + b" " * (cidrs.MAX_DOWNLOAD_BYTES + 10)
    monkeypatch.setattr(cidrs.urllib.request, "urlopen", _fake_urlopen(body))
    with pytest.raises(ValueError, match="上限"):
        cidrs._download("http://x", ["web"])


def test_download_reads_fields(monkeypatch):
    body = b'{"web": ["140.82.112.0/20"], "other": ["x"], "api": []}'
    monkeypatch.setattr(cidrs.urllib.request, "urlopen", _fake_urlopen(body))
    assert cidrs._download("http://x", ["web", "api"]) == ["140.82.112.0/20"]


# ---------- 目标解析（IP / CIDR / 域名 / 通配符） ----------

def test_classify_target_ip_and_cidr():
    assert cidrs.classify_target("1.2.3.4") == ("cidr", "1.2.3.4/32")
    assert cidrs.classify_target("2001:db8::1") == ("cidr", "2001:db8::1/128")
    assert cidrs.classify_target("10.0.0.0/8") == ("cidr", "10.0.0.0/8")
    assert cidrs.classify_target("192.168.1.5/24") == ("cidr", "192.168.1.0/24")


def test_classify_target_domain_and_wildcard():
    assert cidrs.classify_target("example.com") == ("domain", "example.com")
    assert cidrs.classify_target("*.Example.COM") == ("domain", "*.example.com")
    assert cidrs.classify_target("api.example.com.") == ("domain", "api.example.com")


def test_classify_target_url_extracts_host():
    assert cidrs.classify_target("https://github.com/org/repo") == ("domain", "github.com")
    assert cidrs.classify_target("http://1.2.3.4:8080/x") == ("cidr", "1.2.3.4/32")


@pytest.mark.parametrize("bad", ["", "10.0.0.0/99", "*.", "*..com", "a.*.com", "exa mple.com"])
def test_classify_target_invalid(bad):
    with pytest.raises(ValueError):
        cidrs.classify_target(bad)


def test_validate_domain_rejects_ip():
    with pytest.raises(ValueError, match="IP 地址"):
        cidrs.validate_domain("1.2.3.4")


def test_parse_targets_mixed():
    extra, domains = cidrs.parse_targets(
        "10.0.0.0/8, 1.2.3.4  https://github.com/x *.github.com;example.com")
    assert extra == ["10.0.0.0/8", "1.2.3.4/32"]
    assert domains == ["github.com", "*.github.com", "example.com"]


def test_parse_targets_reports_bad_items():
    with pytest.raises(ValueError, match="既不是合法 CIDR"):
        cidrs.parse_targets("10.0.0.0/8 bad/thing")


def test_parse_targets_rejects_too_many_domains():
    many = " ".join(f"d{i}.example.com" for i in range(cidrs.MAX_DOMAINS + 1))
    with pytest.raises(ValueError, match="超过上限"):
        cidrs.parse_targets(many)


# ---------- 域名解析 ----------

def _addr_info(addr):
    fam = socket.AF_INET6 if ":" in addr else socket.AF_INET
    sockaddr = (addr, 0) if fam == socket.AF_INET else (addr, 0, 0, 0)
    return (fam, socket.SOCK_STREAM, 6, "", sockaddr)


def _fake_dns(table, calls=None):
    def fake(host, port, proto=None):
        if calls is not None:
            calls.append(host)
        if host in table:
            return [_addr_info(a) for a in table[host]]
        raise socket.gaierror(-2, "Name or service not known")
    return fake


def test_resolve_domains_exact_v4_v6(monkeypatch):
    monkeypatch.setattr(cidrs.socket, "getaddrinfo",
                        _fake_dns({"example.com": ["1.2.3.4", "2001:db8::5"]}))
    assert cidrs.resolve_domains(["example.com"]) == ["1.2.3.4/32", "2001:db8::5/128"]


def test_resolve_wildcard_probes_dns(monkeypatch):
    calls = []

    def fake(host, port, proto=None):
        calls.append(host)
        if host == "wild.example":
            return [_addr_info("1.2.3.4")]
        if host.startswith("netswitch-wildcard-probe-"):
            return [_addr_info("9.9.9.9")]
        raise socket.gaierror(-2, "not found")

    monkeypatch.setattr(cidrs.socket, "getaddrinfo", fake)
    out = cidrs.resolve_domains(["*.wild.example"])
    assert out == ["1.2.3.4/32", "9.9.9.9/32"]
    assert any(h.startswith("netswitch-wildcard-probe-") for h in calls)


def test_resolve_wildcard_without_dns_wildcard(monkeypatch):
    monkeypatch.setattr(cidrs.socket, "getaddrinfo",
                        _fake_dns({"github.com": ["1.2.3.4"]}))
    # 探测必然失败（表里没有随机标签）→ 只得到 apex 的地址
    assert cidrs.resolve_domains(["*.github.com"]) == ["1.2.3.4/32"]


def test_resolve_wildcard_probe_can_be_disabled(monkeypatch):
    calls = []
    monkeypatch.setattr(cidrs.socket, "getaddrinfo",
                        _fake_dns({"wild.example": ["1.2.3.4"]}, calls))
    assert cidrs.resolve_domains(["*.wild.example"], wildcard_probe=False) == ["1.2.3.4/32"]
    assert calls == ["wild.example"]


def test_resolve_domains_warns_on_failure(monkeypatch):
    msgs = []
    monkeypatch.setattr(cidrs.socket, "getaddrinfo", _fake_dns({}))
    assert cidrs.resolve_domains(["nope.example"], warn=msgs.append) == []
    assert any("域名解析失败" in m for m in msgs)


# ---------- 域名缓存 ----------

def test_fetch_cidrs_resolves_domains(monkeypatch, tmp_path):
    cache = tmp_path / "cache.json"
    monkeypatch.setattr(cidrs.socket, "getaddrinfo",
                        _fake_dns({"example.com": ["1.2.3.4"]}))
    cfg = CidrsCfg(source="manual", domains=["example.com"], cache_file=str(cache))
    assert cidrs.fetch_cidrs(cfg) == ["1.2.3.4/32"]
    saved = json.loads(cache.read_text(encoding="utf-8"))
    assert saved["domain_patterns"] == ["example.com"]
    assert saved["domain_cidrs"] == ["1.2.3.4/32"]


def test_fetch_cidrs_domain_cache_avoids_dns(monkeypatch, tmp_path):
    cache = tmp_path / "cache.json"
    calls = []
    monkeypatch.setattr(cidrs.socket, "getaddrinfo",
                        _fake_dns({"example.com": ["1.2.3.4"]}, calls))
    cfg = CidrsCfg(source="manual", domains=["example.com"], cache_file=str(cache),
                   ttl_hours=24)
    cidrs.fetch_cidrs(cfg)
    assert len(calls) == 1
    assert cidrs.fetch_cidrs(cfg) == ["1.2.3.4/32"]
    assert len(calls) == 1                       # 命中缓存，不再查询


def test_fetch_cidrs_domain_change_triggers_resolve(monkeypatch, tmp_path):
    cache = tmp_path / "cache.json"
    calls = []
    table = {"a.example": ["1.1.1.1"], "b.example": ["2.2.2.2"]}
    monkeypatch.setattr(cidrs.socket, "getaddrinfo", _fake_dns(table, calls))
    cfg = CidrsCfg(source="manual", domains=["a.example"], cache_file=str(cache))
    assert cidrs.fetch_cidrs(cfg) == ["1.1.1.1/32"]
    cfg.domains = ["b.example"]
    assert cidrs.fetch_cidrs(cfg) == ["2.2.2.2/32"]     # 域名列表变了 → 重解析


def test_fetch_cidrs_domain_falls_back_to_cache(monkeypatch, tmp_path):
    cache = tmp_path / "cache.json"
    msgs = []
    monkeypatch.setattr(cidrs.socket, "getaddrinfo",
                        _fake_dns({"example.com": ["1.2.3.4"]}))
    cfg = CidrsCfg(source="manual", domains=["example.com"], cache_file=str(cache))
    cidrs.fetch_cidrs(cfg)

    monkeypatch.setattr(cidrs.socket, "getaddrinfo", _fake_dns({}))
    cfg.ttl_hours = 0                            # 缓存过期 -> 重新解析（失败）
    assert cidrs.fetch_cidrs(cfg, warn=msgs.append) == ["1.2.3.4/32"]
    assert any("回退缓存" in m for m in msgs)


def test_fetch_cidrs_warns_on_invalid_domain_in_config(monkeypatch, tmp_path):
    """绕过 config 校验直接构造的非法域名：告警跳过，不影响 extra。"""
    msgs = []
    cfg = CidrsCfg(source="manual", extra=["10.0.0.0/8"], domains=["bad domain"],
                   cache_file=str(tmp_path / "c.json"))
    assert cidrs.fetch_cidrs(cfg, warn=msgs.append) == ["10.0.0.0/8"]
    assert any("域名写法非法" in m for m in msgs)


def test_fetch_cidrs_reads_legacy_cache(monkeypatch, tmp_path):
    """旧缓存结构（只有 cidrs 键）仍能作为 URL 部分回退使用。"""
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"fetched_at": 0, "cidrs": ["9.9.9.0/24"]}),
                     encoding="utf-8")

    def boom(u, f):
        raise RuntimeError("network down")

    monkeypatch.setattr(cidrs, "_download", boom)
    cfg = CidrsCfg(source="url", url="http://x", fields=["web"],
                   cache_file=str(cache), ttl_hours=24)
    assert "9.9.9.0/24" in cidrs.fetch_cidrs(cfg)
