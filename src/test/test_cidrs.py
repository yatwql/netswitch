from netswitch import cidrs
from netswitch.model import CidrsCfg


def test_manual_source_dedup():
    cfg = CidrsCfg(source="manual", extra=["10.0.0.0/8", "10.0.0.0/8", "2001:db8::/32"])
    assert cidrs.fetch_cidrs(cfg) == ["10.0.0.0/8"]


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
