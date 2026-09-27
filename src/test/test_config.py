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
    # 路径被归一化为绝对路径且落在数据目录内
    assert c.rules[0].cidrs.cache_file.endswith("data/config/cache-github.json")
    assert cfg.data_dir() in __import__("pathlib").Path(c.rules[0].cidrs.cache_file).parents
    assert c.state_file.endswith("data/config/state.json")


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
    # 非法网卡名拒绝写回
    with pytest.raises(ValueError):
        cfg.update_rule_interface(p, "r1", "bad name")


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


# ---------- 安全校验（P0-3） ----------

def test_backend_typo_rejected(tmp_path):
    """backend 拼错必须报错，而不是静默回退 auto。"""
    p = _write(tmp_path, {"routing": {"backend": "nftable"}})
    with pytest.raises(ValueError, match="routing.backend"):
        cfg.load(p, probe=False)


def test_backend_mainroute_accepted(tmp_path):
    p = _write(tmp_path, {"routing": {"backend": "mainroute"}})
    assert cfg.load(p, probe=False).routing.backend == "mainroute"


@pytest.mark.parametrize("table", ["ns; include \"/x\"", "bad name", "", "a" * 40])
def test_nft_table_injection_rejected(tmp_path, table):
    p = _write(tmp_path, {"routing": {"backend": "nftables", "nft_table": table}})
    with pytest.raises(ValueError, match="nft_table"):
        cfg.load(p, probe=False)


@pytest.mark.parametrize("table_id", [0, 1, 199, 253, 254, 255, 999])
def test_reserved_table_id_rejected(tmp_path, table_id):
    """253/254/255 是内核保留表；误用会让 `ip route flush table` 清空主表。"""
    p = _write(tmp_path, {"rules": [{"name": "a", "table_id": table_id}]})
    with pytest.raises(ValueError, match="table_id"):
        cfg.load(p, probe=False)


@pytest.mark.parametrize("mark", [0, -1, 0x100000000])
def test_invalid_fwmark_rejected(tmp_path, mark):
    p = _write(tmp_path, {"rules": [{"name": "a", "fwmark": mark}]})
    with pytest.raises(ValueError, match="fwmark"):
        cfg.load(p, probe=False)


@pytest.mark.parametrize("name", ["../x", "a/b", "a b", "", "a" * 33])
def test_invalid_rule_name_rejected(tmp_path, name):
    p = _write(tmp_path, {"rules": [{"name": name}]})
    with pytest.raises(ValueError):
        cfg.load(p, probe=False)


def test_sanitized_set_name_collision_rejected(tmp_path):
    """a-b 与 a.b 归一化后同为 a_b_v4，会让 nft -f 整表失败。"""
    p = _write(tmp_path, {"rules": [{"name": "a-b"}, {"name": "a.b"}]})
    with pytest.raises(ValueError, match="重名"):
        cfg.load(p, probe=False)


def test_cidr_source_validated(tmp_path):
    p = _write(tmp_path, {"rules": [{"name": "a", "cidrs": {"source": "ftp"}}]})
    with pytest.raises(ValueError, match="source"):
        cfg.load(p, probe=False)


def test_negative_ttl_rejected(tmp_path):
    p = _write(tmp_path, {"rules": [{"name": "a", "cidrs": {"ttl_hours": -1}}]})
    with pytest.raises(ValueError, match="ttl_hours"):
        cfg.load(p, probe=False)


@pytest.mark.parametrize("field,value", [
    ("state_file", "/etc/passwd"),
    ("state_file", "../../../etc/netswitch.json"),
    ("cache", "/etc/cron.d/x.json"),
    ("cache", "../../notes.json"),
])
def test_data_path_escape_rejected(tmp_path, field, value):
    """配置里的路径会被 root 写入，必须限制在数据目录内。"""
    if field == "state_file":
        data = {"state_file": value}
    else:
        data = {"rules": [{"name": "a", "cidrs": {"cache_file": value}}]}
    p = _write(tmp_path, data)
    with pytest.raises(ValueError, match="数据目录"):
        cfg.load(p, probe=False)


def test_relative_data_path_anchored_to_repo_root(tmp_path):
    """相对路径按仓库根解析，不受当前工作目录影响。"""
    p = _write(tmp_path, {"state_file": "data/config/mystate.json"})
    c = cfg.load(p, probe=False)
    assert c.state_file == str(cfg.REPO_ROOT / "data" / "config" / "mystate.json")


def test_probe_map_reuses_snapshot(tmp_path, monkeypatch):
    """TUI 每次刷新都调用 load；传入探测快照时不应再执行 ip 命令。"""
    from netswitch.model import Interface

    def boom():
        raise AssertionError("不应重复探测网卡")

    monkeypatch.setattr(cfg.detect, "detect_interfaces", boom)
    p = _write(tmp_path, {})          # 无 interfaces 段 -> 用快照填充
    c = cfg.load(p, probe_map={"enp2s0": Interface(name="enp2s0", gateway="1.1.1.1")})
    assert [i.name for i in c.interfaces] == ["enp2s0"]
    assert c.interfaces[0].gateway == "1.1.1.1"


# ---------- 地址族与网关（IPv6 支持） ----------

def test_ip_versions_defaults_to_v4(tmp_path):
    p = _write(tmp_path, {"rules": [{"name": "a"}]})
    c = cfg.load(p, probe=False)
    assert c.routing.ip_versions == ["v4"]
    assert c.rules[0].cidrs.ip_versions is None      # 未配置 = 跟随全局


def test_ip_versions_global_dual_stack(tmp_path):
    p = _write(tmp_path, {"routing": {"ip_versions": ["v4", "v6"]}})
    assert cfg.load(p, probe=False).routing.ip_versions == ["v4", "v6"]


def test_ip_versions_rule_override(tmp_path):
    p = _write(tmp_path, {"routing": {"ip_versions": ["v4", "v6"]},
                          "rules": [{"name": "a", "cidrs": {"ip_versions": ["v6"]}}]})
    assert cfg.load(p, probe=False).rules[0].cidrs.ip_versions == ["v6"]


@pytest.mark.parametrize("value", [["v5"], [], ["v4", "v4"], "v4", [4]])
def test_ip_versions_invalid_rejected(tmp_path, value):
    p = _write(tmp_path, {"routing": {"ip_versions": value}})
    with pytest.raises(ValueError, match="ip_versions"):
        cfg.load(p, probe=False)


def test_gateway6_accepted(tmp_path):
    p = _write(tmp_path, {"interfaces": [{"name": "enp2s0", "gateway": "192.168.2.1",
                                          "gateway6": "fe80::1"}]})
    c = cfg.load(p, probe=False)
    assert c.interfaces[0].gateway6 == "fe80::1"


def test_gateway6_with_zone_accepted(tmp_path):
    p = _write(tmp_path, {"interfaces": [{"name": "enp2s0", "gateway6": "fe80::1%enp2s0"}]})
    assert cfg.load(p, probe=False).interfaces[0].gateway6 == "fe80::1%enp2s0"


@pytest.mark.parametrize("gw6", ["192.168.2.1", "not-an-ip", "fe80::zz"])
def test_gateway6_invalid_rejected(tmp_path, gw6):
    p = _write(tmp_path, {"interfaces": [{"name": "enp2s0", "gateway6": gw6}]})
    with pytest.raises(ValueError, match="gateway6"):
        cfg.load(p, probe=False)


def test_gateway_must_be_v4(tmp_path):
    p = _write(tmp_path, {"interfaces": [{"name": "enp2s0", "gateway": "2001:db8::1"}]})
    with pytest.raises(ValueError, match="gateway"):
        cfg.load(p, probe=False)


def test_probe_fills_gateway6(tmp_path, monkeypatch):
    from netswitch.model import Interface
    p = _write(tmp_path, {"interfaces": [{"name": "enp2s0"}]})
    c = cfg.load(p, probe_map={"enp2s0": Interface(name="enp2s0", gateway="1.1.1.1",
                                                  gateway6="fe80::1")})
    assert c.interfaces[0].gateway6 == "fe80::1"
