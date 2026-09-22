from netswitch import apply as apply_mod


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


def test_save_and_load_state(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    config = apply_mod.Config(state_file=str(state))
    apply_mod._save_state(config, [{"dev": "eth0", "gateway": "1.1.1.1", "metric": 100}])
    loaded = apply_mod._load_state(config)
    assert loaded["original_defaults"][0]["dev"] == "eth0"
