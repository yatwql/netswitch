import json

from netswitch import apply as apply_mod
from netswitch.model import Config, IfaceCfg, RuleCfg


def test_current_defaults(monkeypatch):
    monkeypatch.setattr(apply_mod.ip, "route_list", lambda dry_run=False: [
        {"dst": "default", "dev": "eth0", "gateway": "1.1.1.1",
         "metric": 100, "prefsrc": "1.1.1.2"},
        {"dst": "192.168.0.0/24", "dev": "eth0"},
    ])
    out = apply_mod._current_defaults()
    assert len(out) == 1
    assert out[0]["dev"] == "eth0"
    assert out[0]["gateway"] == "1.1.1.1"
    assert out[0]["metric"] == 100
    assert out[0]["src"] == "1.1.1.2"


def test_save_and_load_state(tmp_path):
    state = tmp_path / "state.json"
    config = apply_mod.Config(state_file=str(state))
    apply_mod._save_state(config, [{"dev": "eth0", "gateway": "1.1.1.1", "metric": 100}])
    loaded = apply_mod._load_state(config)
    assert loaded["original_defaults"][0]["dev"] == "eth0"


def test_original_defaults_not_overwritten(tmp_path):
    """第二次 apply 不得覆盖 revert 基线（否则 revert 恢复不回原 metric）。"""
    state = tmp_path / "state.json"
    config = apply_mod.Config(state_file=str(state))
    apply_mod._save_state(config, [{"dev": "eth0", "gateway": "1.1.1.1", "metric": 100}])
    apply_mod._save_state(config, [{"dev": "eth0", "gateway": "1.1.1.1", "metric": 600}])
    loaded = apply_mod._load_state(config)
    assert loaded["original_defaults"][0]["metric"] == 100
    assert loaded["applied_rules"] == []


def test_state_records_backend_and_tables(tmp_path):
    state = tmp_path / "state.json"
    config = apply_mod.Config(
        state_file=str(state),
        rules=[RuleCfg(name="a", table_id=200),
               RuleCfg(name="b", table_id=201)],
    )
    apply_mod._save_state(config, [])
    loaded = apply_mod._load_state(config)
    assert loaded["tables"] == [200, 201]
    assert loaded["backend"] in ("auto", "nftables", "iprule", "mainroute")


def test_load_state_tolerates_corrupt_file(tmp_path):
    state = tmp_path / "state.json"
    state.write_text("{not json", encoding="utf-8")
    config = apply_mod.Config(state_file=str(state))
    assert apply_mod._load_state(config)["original_defaults"] == []


def test_load_state_tolerates_non_object(tmp_path):
    state = tmp_path / "state.json"
    state.write_text("[1, 2]", encoding="utf-8")
    config = apply_mod.Config(state_file=str(state))
    assert apply_mod._load_state(config)["original_defaults"] == []


def _prep_revert(monkeypatch, calls):
    monkeypatch.setattr(apply_mod.ex, "require_root", lambda: None)
    monkeypatch.setattr(apply_mod.routing, "clear_rules",
                        lambda cfg, **kw: calls.append(("clear", tuple(sorted(kw.get("tables") or ())))))
    monkeypatch.setattr(apply_mod.iface, "set_metric",
                        lambda name, metric, gw=None, dry_run=False:
                        calls.append((name, metric, gw)))


def test_revert_restores_original_and_cleans_up(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    config = apply_mod.Config(state_file=str(state),
                              rules=[RuleCfg(name="a", table_id=200)])
    apply_mod._save_state(config, [{"dev": "eth0", "gateway": "1.1.1.1", "metric": 100}])
    calls = []
    _prep_revert(monkeypatch, calls)
    assert apply_mod.revert(config) == 0
    assert ("clear", (200,)) in calls
    assert ("eth0", 100, "1.1.1.1") in calls
    assert not state.exists()          # 成功后清掉状态，下次 apply 重建基线


def test_revert_dry_run_keeps_state(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    config = apply_mod.Config(state_file=str(state))
    apply_mod._save_state(config, [{"dev": "eth0", "gateway": "1.1.1.1", "metric": 100}])
    calls = []
    _prep_revert(monkeypatch, calls)
    assert apply_mod.revert(config, dry_run=True) == 0
    assert state.exists()


def test_revert_without_state_warns(tmp_path, monkeypatch, capsys):
    config = apply_mod.Config(state_file=str(tmp_path / "missing.json"))
    calls = []
    _prep_revert(monkeypatch, calls)
    assert apply_mod.revert(config) == 0
    assert "未找到应用记录" in capsys.readouterr().out


def test_apply_uses_metric_and_rules(tmp_path, monkeypatch):
    config = Config(
        state_file=str(tmp_path / "state.json"),
        interfaces=[IfaceCfg(name="eth0", gateway="1.1.1.1", metric=100)],
    )
    metrics = []
    monkeypatch.setattr(apply_mod.ex, "require_root", lambda: None)
    monkeypatch.setattr(apply_mod.ip, "route_list", lambda dry_run=False: [])
    monkeypatch.setattr(apply_mod.iface, "set_metric",
                        lambda name, metric, gw=None, dry_run=False:
                        metrics.append((name, metric)))
    monkeypatch.setattr(apply_mod.routing, "apply_rules",
                        lambda cfg, dry_run=False, warn=None: 0)
    assert apply_mod.apply(config) == 0
    assert metrics == [("eth0", 100)]
    assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))[
        "original_defaults"] == []
