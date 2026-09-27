import pytest

from netswitch import detect, iface
from netswitch.model import Config, IfaceCfg, Interface, MetricsCfg


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


def test_set_metric_deletes_every_default_route(monkeypatch):
    """同一网卡多条默认路由时必须都清掉，避免残留重复默认路由。"""
    calls = []
    monkeypatch.setattr(iface.ip, "route_list", lambda dry_run=False: [
        {"dst": "default", "dev": "enp2s0", "gateway": "192.168.2.1", "metric": 100},
        {"dst": "default", "dev": "enp2s0", "gateway": "192.168.2.2", "metric": 900},
    ])
    monkeypatch.setattr(iface.ex, "run", lambda cmd, **kw: calls.append(cmd))
    iface.set_metric("enp2s0", 200)
    dels = [" ".join(c) for c in calls if "del" in c]
    assert len(dels) == 2
    assert any("192.168.2.1" in c for c in dels) and any("192.168.2.2" in c for c in dels)


def test_set_metric_none_restores_metricless_route(monkeypatch):
    """revert 恢复原始状态时，原路由没有 metric，就不能硬塞一个。"""
    calls = []
    monkeypatch.setattr(iface.ip, "route_list", lambda dry_run=False: [
        {"dst": "default", "dev": "enp2s0", "gateway": "192.168.2.1"},
    ])
    monkeypatch.setattr(iface.ex, "run", lambda cmd, **kw: calls.append(cmd))
    iface.set_metric("enp2s0", None)
    add = [" ".join(c) for c in calls if "add" in c][0]
    assert "metric" not in add


def test_set_metric_without_gateway_raises(monkeypatch):
    monkeypatch.setattr(iface.ip, "route_list", lambda dry_run=False: [])
    with pytest.raises(RuntimeError):
        iface.set_metric("enp2s0", 200)


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


# ---------- set_primary（P1-9） ----------

def test_set_primary_target_missing_from_config(monkeypatch):
    """目标网卡不在 config.interfaces 时也必须被设为 preferred（此前会静默失效）。"""
    calls = []
    monkeypatch.setattr(iface, "set_metric",
                        lambda name, metric, gw=None, dry_run=False:
                        calls.append((name, metric)))
    monkeypatch.setattr(iface.detect, "detect_interfaces",
                        lambda: [Interface(name="wlp129s0", gateway="192.168.1.1")])
    config = Config(interfaces=[IfaceCfg(name="enp2s0", gateway="1.1.1.1")],
                    metrics=MetricsCfg(preferred=100, fallback=600))
    iface.set_primary("wlp129s0", config)
    assert ("wlp129s0", 100) in calls
    assert ("enp2s0", 600) in calls


def test_set_primary_known_interface(monkeypatch):
    calls = []
    monkeypatch.setattr(iface, "set_metric",
                        lambda name, metric, gw=None, dry_run=False:
                        calls.append((name, metric)))
    config = Config(interfaces=[
        IfaceCfg(name="enp2s0", gateway="1.1.1.1"),
        IfaceCfg(name="wlp129s0", gateway="192.168.1.1"),
    ], metrics=MetricsCfg(preferred=100, fallback=600))
    iface.set_primary("wlp129s0", config)
    assert ("wlp129s0", 100) in calls and ("enp2s0", 600) in calls


def test_set_primary_unknown_interface_raises(monkeypatch):
    monkeypatch.setattr(iface.detect, "detect_interfaces", lambda: [])
    config = Config(interfaces=[], metrics=MetricsCfg())
    with pytest.raises(RuntimeError, match="未探测到网卡"):
        iface.set_primary("eth9", config)
