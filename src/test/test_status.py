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
    """mainroute 后端下，多条规则只应 dump 一次主表。"""
    _no_detect(monkeypatch)
    monkeypatch.setattr(status.routing, "resolve_backend", lambda b: "mainroute")
    calls = []

    def fake_routes():
        calls.append(1)
        return []

    monkeypatch.setattr(status.routing, "main_table_routes", fake_routes)
    seen = []
    monkeypatch.setattr(status.routing, "rule_applied",
                        lambda cfg, rule, main_routes=None:
                        seen.append(main_routes) or False)
    status.print_status(Config(rules=[
        RuleCfg(name="a", table_id=200), RuleCfg(name="b", table_id=201),
    ]))
    assert len(calls) == 1
    assert seen == [[], []]
