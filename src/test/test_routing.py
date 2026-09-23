import pytest

from netswitch import routing
from netswitch.model import CidrsCfg, RuleCfg


def test_resolve_backend(monkeypatch):
    monkeypatch.setattr(routing.shutil, "which", lambda n: None)
    assert routing.resolve_backend("auto") == "iprule"
    monkeypatch.setattr(routing.shutil, "which", lambda n: "/usr/sbin/nft")
    assert routing.resolve_backend("auto") == "nftables"
    assert routing.resolve_backend("iprule") == "iprule"
    assert routing.resolve_backend("nftables") == "nftables"


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
