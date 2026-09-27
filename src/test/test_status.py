from netswitch import status
from netswitch.model import Config, RuleCfg


def _no_detect(monkeypatch):
    monkeypatch.setattr(status.detect, "detect_interfaces", lambda: [])
    monkeypatch.setattr(status.ip, "route_list", lambda dry_run=False: [])
    monkeypatch.setattr(status.ip, "policy_routing_supported", lambda: None)
    monkeypatch.setattr(status.log, "current_log_file", lambda: "/tmp/netswitch.log")


def test_print_status_reports_log_path_and_capability(monkeypatch, capsys):
    _no_detect(monkeypatch)
    monkeypatch.setattr(status.routing, "resolve_backend", lambda b: "nftables")
    monkeypatch.setattr(status.routing, "rule_applied", lambda *a, **k: False)
    status.print_status(Config())
    out = capsys.readouterr().out
    assert "/tmp/netswitch.log" in out
    assert "策略路由能力未确认" in out


def test_print_status_survives_rule_query_failure(monkeypatch, capsys):
    """单条规则状态查询失败不应让整个 status 崩掉。"""
    _no_detect(monkeypatch)
    monkeypatch.setattr(status.routing, "resolve_backend", lambda b: "nftables")

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(status.routing, "rule_applied", boom)
    status.print_status(Config(rules=[RuleCfg(name="a", table_id=200)]))
    out = capsys.readouterr().out
    assert "状态查询失败" in out
    assert "未应用" in out


def test_mainroute_snapshot_queried_once(monkeypatch, capsys):
    """mainroute 后端下，多条规则只应取一次主表快照（v4/v6 各自一次）。"""
    _no_detect(monkeypatch)
    monkeypatch.setattr(status.routing, "resolve_backend", lambda b: "mainroute")
    calls = []

    def fake_routes_all():
        calls.append(1)
        return {4: []}

    monkeypatch.setattr(status.routing, "main_table_routes_all", fake_routes_all)
    seen = []
    monkeypatch.setattr(status.routing, "rule_applied",
                        lambda cfg, rule, main_routes=None:
                        seen.append(main_routes) or False)
    status.print_status(Config(rules=[
        RuleCfg(name="a", table_id=200), RuleCfg(name="b", table_id=201),
    ]))
    assert len(calls) == 1
    assert seen == [{4: []}, {4: []}]


def test_status_shows_ipv6_and_rule_families(monkeypatch, capsys):
    from netswitch.model import Interface, RoutingCfg

    monkeypatch.setattr(status.detect, "detect_interfaces", lambda: [
        Interface(name="wlp129s0", ip="192.168.1.7/24", ip6="2001:db8:1::7/64",
                  gateway="192.168.1.1", gateway6="fe80::1", metric=600,
                  state="connected", admin_up=True, device_confirmed=True)])
    monkeypatch.setattr(status.ip, "route_list", lambda family=4, dry_run=False: [])
    monkeypatch.setattr(status.ip, "family_available", lambda family=4: True)
    monkeypatch.setattr(status.ip, "policy_routing_supported", lambda family=4: True)
    monkeypatch.setattr(status.log, "current_log_file", lambda: "/tmp/x.log")
    monkeypatch.setattr(status.routing, "resolve_backend", lambda b: "iprule")

    def run(cmd, **kw):
        class _R:
            stdout = "default via 1.2.3.4 dev eth0\n"
            returncode = 0
            stderr = ""
        return _R()

    monkeypatch.setattr(status.routing.ex, "run", run)
    config = Config(routing=RoutingCfg(backend="iprule", ip_versions=["v4", "v6"]),
                    rules=[RuleCfg(name="github", table_id=200)])
    status.print_status(config)
    out = capsys.readouterr().out
    assert "IPv6=2001:db8:1::7/64" in out
    assert "网关6=fe80::1" in out
    assert "族=v4+v6" in out
    assert "已应用" in out


def test_status_reports_ipv6_unavailable(monkeypatch, capsys):
    _no_detect(monkeypatch)
    monkeypatch.setattr(status.routing, "resolve_backend", lambda b: "iprule")
    monkeypatch.setattr(status.routing, "rule_applied", lambda *a, **k: False)
    monkeypatch.setattr(status.ip, "family_available", lambda family=4: family != 6)
    status.print_status(Config())
    assert "IPv6: 不可用" in capsys.readouterr().out
