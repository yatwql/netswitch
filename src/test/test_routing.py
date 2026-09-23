import pytest

from netswitch import routing
from netswitch.model import CidrsCfg, RuleCfg


def test_resolve_backend(monkeypatch):
    monkeypatch.setattr(routing.ip, "policy_routing_supported", lambda: True)
    monkeypatch.setattr(routing.shutil, "which", lambda n: None)
    assert routing.resolve_backend("auto") == "iprule"
    monkeypatch.setattr(routing.shutil, "which", lambda n: "/usr/sbin/nft")
    assert routing.resolve_backend("auto") == "nftables"
    assert routing.resolve_backend("iprule") == "iprule"
    assert routing.resolve_backend("nftables") == "nftables"


def test_resolve_backend_mainroute_fallback(monkeypatch):
    monkeypatch.setattr(routing.ip, "policy_routing_supported", lambda: False)
    assert routing.resolve_backend("auto") == "mainroute"
    assert routing.resolve_backend("mainroute") == "mainroute"


def test_apply_rules_mainroute_commands(monkeypatch):
    from netswitch.model import CidrsCfg, Config, RoutingCfg, RuleCfg

    calls = []
    monkeypatch.setattr(routing, "clear_rules", lambda cfg, dry_run=False: None)
    monkeypatch.setattr(routing.cidrs, "fetch_cidrs", lambda c: ["140.82.112.0/20"])
    monkeypatch.setattr(routing, "_gw_src",
                        lambda cfg, iface: ("192.168.1.1", "192.168.1.7", "192.168.1.0/24"))
    monkeypatch.setattr(routing.ex, "run", lambda cmd, **kw: calls.append(cmd))

    cfg = Config(
        routing=RoutingCfg(backend="mainroute"),
        rules=[RuleCfg(name="github", interface="wlp129s0",
                       cidrs=CidrsCfg(source="manual", extra=["140.82.112.0/20"]))],
    )
    routing.apply_rules(cfg)
    cmds = [" ".join(c) for c in calls]
    assert "ip route replace 140.82.112.0/20 via 192.168.1.1 dev wlp129s0" in cmds
    # mainroute 不使用 ip rule / nft
    assert not any(c.startswith("ip rule") for c in cmds)
    assert not any(c.startswith("nft") for c in cmds)


def test_build_nft_script(monkeypatch):
    monkeypatch.setattr(routing.cidrs, "fetch_cidrs", lambda c: ["140.82.112.0/20"])
    rule = RuleCfg(name="github", fwmark=1, cidrs=CidrsCfg())
    script = routing._build_nft_script("netswitch", [rule])
    assert "table inet netswitch" in script
    assert "set github_v4" in script
    assert "140.82.112.0/20" in script
    assert "meta mark set 0x1" in script
    assert "type route hook output" in script


def test_select_rules():
    from netswitch.model import Config

    config = Config(rules=[
        RuleCfg(name="a", enabled=True, interface="eth0"),
        RuleCfg(name="b", enabled=False, interface="eth0"),
        RuleCfg(name="c", enabled=True, interface=None),
    ])
    assert [r.name for r in routing._select_rules(config)] == ["a"]
    assert [r.name for r in routing._select_rules(config, only={"a"})] == ["a"]
    assert routing._select_rules(config, exclude={"a"}) == []


class _P:
    def __init__(self, out=""):
        self.stdout = out


def test_rule_applied(monkeypatch):
    rule = RuleCfg(name="r", table_id=200)
    monkeypatch.setattr(routing.ex, "run",
                        lambda cmd, **kw: _P("default via 1.2.3.4 dev eth0\n"))
    assert routing.rule_applied(rule) is True
    monkeypatch.setattr(routing.ex, "run", lambda cmd, **kw: _P(""))
    assert routing.rule_applied(rule) is False


def test_run_or_hint_eopnotsupp(monkeypatch):
    def boom(cmd, **kw):
        raise routing.ex.ExecError(cmd, 2, "RTNETLINK answers: Operation not supported")

    monkeypatch.setattr(routing.ex, "run", boom)
    with pytest.raises(RuntimeError) as ei:
        routing._run_or_hint(["ip", "route", "add", "x"])
    assert "Operation not supported" in str(ei.value)


def test_run_or_hint_other_error_passthrough(monkeypatch):
    def boom(cmd, **kw):
        raise routing.ex.ExecError(cmd, 2, "Nexthop has invalid gateway")

    monkeypatch.setattr(routing.ex, "run", boom)
    with pytest.raises(routing.ex.ExecError):
        routing._run_or_hint(["ip", "route", "add", "x"])
