import pytest

from netswitch import detect, iface


def test_set_metric_commands(monkeypatch):
    calls = []
    monkeypatch.setattr(iface.ip, "route_list", lambda dry_run=False: [
        {"dst": "default", "dev": "enp2s0", "gateway": "192.168.2.1", "metric": 100},
    ])
    monkeypatch.setattr(iface.ex, "run", lambda cmd, **kw: calls.append(cmd))
    iface.set_metric("enp2s0", 200, dry_run=True)
    cmds = [" ".join(c) for c in calls]
    assert any("route del default" in c and "192.168.2.1" in c for c in cmds)
    assert any("route add default" in c and "metric 200" in c for c in cmds)


def test_set_link_guard_last_effective(monkeypatch):
    calls = []
    monkeypatch.setattr(detect, "effective_interface_names", lambda: ["enp2s0"])
    monkeypatch.setattr(iface.ex, "run", lambda cmd, **kw: calls.append(cmd))
    with pytest.raises(RuntimeError):
        iface.set_link("enp2s0", up=False)
    # force 可显式覆盖
    iface.set_link("enp2s0", up=False, force=True)
    assert ["ip", "link", "set", "enp2s0", "down"] in calls


def test_set_link_allows_when_two_effective(monkeypatch):
    calls = []
    monkeypatch.setattr(detect, "effective_interface_names",
                        lambda: ["enp2s0", "wlp129s0"])
    monkeypatch.setattr(iface.ex, "run", lambda cmd, **kw: calls.append(cmd))
    iface.set_link("enp2s0", up=False)
    assert ["ip", "link", "set", "enp2s0", "down"] in calls


def test_set_link_up(monkeypatch):
    calls = []
    monkeypatch.setattr(iface.ex, "run", lambda cmd, **kw: calls.append(cmd))
    iface.set_link("wlp129s0", up=True)
    assert ["ip", "link", "set", "wlp129s0", "up"] in calls
