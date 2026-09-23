import json

import pytest

from netswitch import config as cfg


def _write(tmp_path, data):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


def test_defaults(tmp_path):
    p = _write(tmp_path, {"rules": [{"name": "github", "interface": "eth0"}]})
    c = cfg.load(p, probe=False)
    assert c.routing.backend == "auto"
    assert c.routing.nft_table == "netswitch"
    assert c.metrics.preferred == 100
    assert c.metrics.fallback == 600
    assert c.rules[0].table_id == 200
    assert c.rules[0].fwmark == 1
    assert c.rules[0].cidrs.cache_file == "data/config/cache-github.json"


def test_auto_unique_ids(tmp_path):
    p = _write(tmp_path, {"rules": [
        {"name": "a"}, {"name": "b"}, {"name": "c"},
    ]})
    c = cfg.load(p, probe=False)
    tables = [r.table_id for r in c.rules]
    marks = [r.fwmark for r in c.rules]
    assert tables == [200, 201, 202]
    assert marks == [1, 2, 3]


def test_duplicate_name(tmp_path):
    p = _write(tmp_path, {"rules": [{"name": "a"}, {"name": "a"}]})
    with pytest.raises(ValueError):
        cfg.load(p, probe=False)


def test_table_conflict(tmp_path):
    p = _write(tmp_path, {"rules": [
        {"name": "a", "table_id": 200}, {"name": "b", "table_id": 200},
    ]})
    with pytest.raises(ValueError):
        cfg.load(p, probe=False)


def test_missing_name(tmp_path):
    p = _write(tmp_path, {"rules": [{"interface": "eth0"}]})
    with pytest.raises(ValueError):
        cfg.load(p, probe=False)


def test_update_rule_interface(tmp_path):
    p = _write(tmp_path, {"rules": [{"name": "r1", "interface": "eth0"}]})
    assert cfg.update_rule_interface(p, "r1", "eth1") is True
    assert cfg.load(p, probe=False).rules[0].interface == "eth1"
    # 空 = 不分流（删除字段）
    assert cfg.update_rule_interface(p, "r1", None) is True
    assert cfg.load(p, probe=False).rules[0].interface is None
    # 未找到规则
    assert cfg.update_rule_interface(p, "nope", "eth0") is False


def test_seed_default_rules(tmp_path):
    # 同目录下的 config.example.json 作为缺省规则来源
    (tmp_path / "config.example.json").write_text(
        json.dumps({"rules": [{"name": "github", "cidrs": {}}]}), encoding="utf-8")
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"version": 1, "rules": []}), encoding="utf-8")

    assert cfg.seed_default_rules(str(p)) is True
    c = cfg.load(str(p), probe=False)
    assert [r.name for r in c.rules] == ["github"]
    # 已有规则 -> 不覆盖
    assert cfg.seed_default_rules(str(p)) is False
