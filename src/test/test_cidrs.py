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
