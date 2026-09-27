import json

import pytest

from netswitch import routing
from netswitch.model import (
    CidrsCfg,
    Config,
    Interface,
    RoutingCfg,
    RuleCfg,
)


class _P:
    def __init__(self, out="", rc=0, err=""):
        self.stdout = out
        self.returncode = rc
        self.stderr = err


def _patch_env(monkeypatch, calls, *, ifaces=("wlp129s0",), cidrs=("140.82.112.0/20",),
               nft=None):
    """把 apply_rules 依赖的外部调用都替换成记录器。"""
    monkeypatch.setattr(routing, "clear_rules", lambda cfg, **kw: calls.append("clear"))
    monkeypatch.setattr(routing.cidrs, "fetch_cidrs",
                        lambda c, warn=None: list(cidrs))
    monkeypatch.setattr(routing, "_gw_src",
                        lambda cfg, iface, ifaces=None: ("192.168.1.1",
                                                         "192.168.1.7",
                                                         "192.168.1.0/24"))
    monkeypatch.setattr(routing.detect, "detect_interfaces",
                        lambda: [Interface(name=n, gateway="192.168.1.1") for n in ifaces])
    monkeypatch.setattr(routing.ip, "link_list",
                        lambda dry_run=False: [{"ifname": n} for n in ifaces])
    monkeypatch.setattr(routing.shutil, "which", lambda n: nft)
    monkeypatch.setattr(routing.ex, "run", lambda cmd, **kw: calls.append(cmd))


def _config(backend="mainroute", **rule_kw):
    rule_kw.setdefault("name", "github")
    rule_kw.setdefault("interface", "wlp129s0")
    rule_kw.setdefault("cidrs", CidrsCfg(source="manual", extra=["140.82.112.0/20"]))
    return Config(routing=RoutingCfg(backend=backend), rules=[RuleCfg(**rule_kw)])


# ---------- 后端解析 ----------

def test_resolve_backend(monkeypatch):
    monkeypatch.setattr(routing.ip, "policy_routing_supported", lambda: True)
    monkeypatch.setattr(routing.shutil, "which", lambda n: None)
    assert routing.resolve_backend("auto") == "iprule"
    monkeypatch.setattr(routing.shutil, "which", lambda n: "/usr/sbin/nft")
    assert routing.resolve_backend("auto") == "nftables"
    assert routing.resolve_backend("iprule") == "iprule"
    assert routing.resolve_backend("nftables") == "nftables"
    assert routing.resolve_backend("mainroute") == "mainroute"


def test_resolve_backend_mainroute_fallback(monkeypatch):
    monkeypatch.setattr(routing.ip, "policy_routing_supported", lambda: False)
    assert routing.resolve_backend("auto") == "mainroute"
    assert routing.resolve_backend("mainroute") == "mainroute"


def test_resolve_backend_unknown_capability(monkeypatch):
    """非 root 时能力未知（None）：不回退 mainroute，按"支持"处理。"""
    monkeypatch.setattr(routing.ip, "policy_routing_supported", lambda: None)
    monkeypatch.setattr(routing.shutil, "which", lambda n: "/usr/sbin/nft")
    assert routing.resolve_backend("auto") == "nftables"


# ---------- 应用 ----------

def test_apply_rules_mainroute_commands(monkeypatch):
    calls = []
    _patch_env(monkeypatch, calls)
    cfg = _config(backend="mainroute")
    assert routing.apply_rules(cfg) == 1
    cmds = [" ".join(c) for c in calls]
    assert "ip route replace 140.82.112.0/20 via 192.168.1.1 dev wlp129s0 proto 200" in cmds
    # mainroute 不使用 ip rule / nft
    assert not any(c.startswith("ip rule") for c in cmds)
    assert not any(c.startswith("nft") for c in cmds)


def test_apply_fetches_each_source_once(monkeypatch):
    """同一次 apply 里每个 CIDR 源只拉取一次（此前每条规则会拉 3~4 次）。"""
    fetches = []

    def fake_fetch(cidrs_cfg, warn=None):
        fetches.append(cidrs_cfg)
        return ["140.82.112.0/20"]

    calls = []
    _patch_env(monkeypatch, calls, ifaces=("eth0", "eth1"))
    monkeypatch.setattr(routing.cidrs, "fetch_cidrs", fake_fetch)
    cfg = Config(routing=RoutingCfg(backend="mainroute"), rules=[
        RuleCfg(name="a", interface="eth0", cidrs=CidrsCfg(source="manual")),
        RuleCfg(name="b", interface="eth1", cidrs=CidrsCfg(source="manual")),
    ])
    assert routing.apply_rules(cfg) == 2
    assert len(fetches) == 2


def test_apply_preflight_blocks_before_any_change(monkeypatch):
    """预检失败必须"零改动"：不能先清空旧规则再报错。"""
    calls = []
    _patch_env(monkeypatch, calls, ifaces=())
    monkeypatch.setattr(routing, "clear_rules", lambda cfg, **kw: calls.append("clear"))
    cfg = _config(backend="mainroute", interface="eth9")
    with pytest.raises(RuntimeError, match="预检未通过"):
        routing.apply_rules(cfg)
    assert calls == []


def test_apply_preflight_reports_missing_gateway(monkeypatch):
    calls = []
    _patch_env(monkeypatch, calls)
    monkeypatch.setattr(routing, "_gw_src",
                        lambda cfg, iface, ifaces=None: (_ for _ in ()).throw(
                            RuntimeError("无法确定 eth0 的网关")))
    with pytest.raises(RuntimeError, match="网关"):
        routing.apply_rules(_config(backend="mainroute"))


def test_apply_warns_when_egress_not_connected(monkeypatch):
    """出口网卡未连接：告警但仍按配置应用（无线可能稍后才关联上）。"""
    calls = []
    msgs = []
    _patch_env(monkeypatch, calls)
    monkeypatch.setattr(routing.detect, "detect_interfaces",
                        lambda: [Interface(name="wlp129s0", gateway="192.168.1.1",
                                           state="no-carrier", admin_up=True)])
    assert routing.apply_rules(_config(backend="mainroute"), warn=msgs.append) == 1
    assert any("当前未连接" in m for m in msgs)


def test_apply_warns_when_egress_not_physical(monkeypatch):
    """网卡存在但不在物理网卡快照里（如 bond/vlan）：告警不阻断。"""
    calls = []
    msgs = []
    _patch_env(monkeypatch, calls)
    monkeypatch.setattr(routing.ip, "link_list",
                        lambda dry_run=False: [{"ifname": "other0"}])
    monkeypatch.setattr(routing.detect, "detect_interfaces",
                        lambda: [Interface(name="enp2s0", gateway="1.1.1.1")])
    assert routing.apply_rules(_config(backend="mainroute", interface="other0"),
                               warn=msgs.append) == 1
    assert any("不是探测到的物理网卡" in m for m in msgs)


def test_apply_rolls_back_on_failure(monkeypatch):
    cleared = []
    calls = []
    _patch_env(monkeypatch, calls)

    def fake_clear(cfg, **kw):
        cleared.append(kw.get("keep_mainroute"))

    monkeypatch.setattr(routing, "clear_rules", fake_clear)

    def boom(cmd, **kw):
        raise routing.ex.ExecError(cmd, 2, "boom")

    monkeypatch.setattr(routing.ex, "run", boom)
    with pytest.raises(routing.ex.ExecError):
        routing.apply_rules(_config(backend="iprule"))
    # 一次正常清理 + 一次失败回滚清理
    assert len(cleared) == 2


def test_apply_skips_rule_without_cidrs(monkeypatch):
    calls = []
    msgs = []
    _patch_env(monkeypatch, calls)
    monkeypatch.setattr(routing.cidrs, "fetch_cidrs", lambda c, warn=None: [])
    assert routing.apply_rules(_config(backend="mainroute"), warn=msgs.append) == 0
    assert any("无可用 CIDR" in m for m in msgs)


def test_apply_iprule_passes_cidr_list(monkeypatch):
    calls = []
    _patch_env(monkeypatch, calls, nft=None)
    monkeypatch.setattr(routing.cidrs, "fetch_cidrs",
                        lambda c, warn=None: ["140.82.112.0/20", "140.82.113.0/24"])
    cfg = _config(backend="iprule", table_id=200)
    assert routing.apply_rules(cfg) == 1
    cmds = [" ".join(c) for c in calls]
    assert "ip rule add to 140.82.112.0/20 lookup 200 pref 20000" in cmds
    assert "ip rule add to 140.82.113.0/24 lookup 200 pref 20001" in cmds


def test_apply_iprule_rejects_too_many_rules(monkeypatch):
    """ip rule 优先级不得撞上内核默认规则（pref 32766）。"""
    calls = []
    _patch_env(monkeypatch, calls, nft=None)
    many = [f"10.{i // 256}.{i % 256}.0/24" for i in range(routing.ip.RULE_PREF_MAX_COUNT + 1)]
    monkeypatch.setattr(routing.cidrs, "fetch_cidrs", lambda c, warn=None: many)
    with pytest.raises(RuntimeError, match="超过上限"):
        routing.apply_rules(_config(backend="iprule"))


# ---------- 清理（P0-1：绝不误删） ----------

def test_clear_rules_never_deletes_foreign_routes(monkeypatch):
    """主表里没有本程序的 proto 200 路由时，不得删除任何东西。"""
    calls = []
    monkeypatch.setattr(routing, "main_table_routes", lambda: [])
    monkeypatch.setattr(routing.ip, "policy_routing_supported", lambda: False)
    monkeypatch.setattr(routing.shutil, "which", lambda n: None)
    monkeypatch.setattr(routing.ex, "run", lambda cmd, **kw: calls.append(cmd))
    cfg = _config(backend="mainroute",
                  cidrs=CidrsCfg(source="manual", extra=["192.168.77.0/24"]))
    routing.clear_rules(cfg)
    assert not any("del" in " ".join(c) for c in calls)


def test_clear_rules_deletes_only_own_mainroute(monkeypatch):
    calls = []
    own = [{"dst": "140.82.112.0/20", "dev": "wlp129s0", "protocol": "200"}]
    monkeypatch.setattr(routing, "main_table_routes", lambda: own)
    monkeypatch.setattr(routing.ip, "policy_routing_supported", lambda: False)
    monkeypatch.setattr(routing.shutil, "which", lambda n: None)
    monkeypatch.setattr(routing.ex, "run", lambda cmd, **kw: calls.append(cmd))
    cfg = _config(backend="mainroute",
                  cidrs=CidrsCfg(source="manual",
                                 extra=["140.82.112.0/20", "10.9.9.0/24"]))
    routing.clear_rules(cfg)
    cmds = [" ".join(c) for c in calls]
    assert "ip route del 140.82.112.0/20 proto 200 dev wlp129s0 table main" in cmds
    # 主表里没有的网段不动
    assert not any("10.9.9.0/24" in c for c in cmds)


def test_clear_rules_skips_other_interface(monkeypatch):
    calls = []
    own = [{"dst": "140.82.112.0/20", "dev": "enp2s0", "protocol": "200"}]
    monkeypatch.setattr(routing, "main_table_routes", lambda: own)
    monkeypatch.setattr(routing.ip, "policy_routing_supported", lambda: False)
    monkeypatch.setattr(routing.shutil, "which", lambda n: None)
    monkeypatch.setattr(routing.ex, "run", lambda cmd, **kw: calls.append(cmd))
    routing.clear_rules(_config(backend="mainroute"))     # 规则出口是 wlp129s0
    assert not any("del" in " ".join(c) for c in calls)


def test_clear_rules_keeps_routes_to_rebuild(monkeypatch):
    calls = []
    own = [{"dst": "140.82.112.0/20", "dev": "wlp129s0", "protocol": "200"}]
    monkeypatch.setattr(routing, "main_table_routes", lambda: own)
    monkeypatch.setattr(routing.ip, "policy_routing_supported", lambda: False)
    monkeypatch.setattr(routing.shutil, "which", lambda n: None)
    monkeypatch.setattr(routing.ex, "run", lambda cmd, **kw: calls.append(cmd))
    routing.clear_rules(_config(backend="mainroute"),
                        keep_mainroute={("140.82.112.0/20", "wlp129s0")})
    assert not any("del" in " ".join(c) for c in calls)


def test_clear_rules_dry_run_prints_planned_deletes(monkeypatch):
    calls = []
    monkeypatch.setattr(routing, "main_table_routes", lambda: [])
    monkeypatch.setattr(routing.ip, "policy_routing_supported", lambda: False)
    monkeypatch.setattr(routing.shutil, "which", lambda n: None)
    monkeypatch.setattr(routing.ex, "run", lambda cmd, **kw: calls.append(cmd))
    routing.clear_rules(_config(backend="mainroute"), dry_run=True)
    assert any("proto 200" in " ".join(c) for c in calls)


def test_clear_rules_sweeps_stale_ip_rules_and_tables(monkeypatch):
    """改配置/改表号后遗留的 ip rule 与路由表也要清掉，但不碰保留表。"""
    calls = []
    base = routing.ip.RULE_PREF_BASE
    monkeypatch.setattr(routing, "main_table_routes", lambda: [])
    monkeypatch.setattr(routing.shutil, "which", lambda n: None)
    monkeypatch.setattr(routing.ip, "policy_routing_supported", lambda: True)
    monkeypatch.setattr(routing.ip, "rule_list", lambda *a, **k: [
        {"priority": base, "table": 200},       # 本程序（历史表）
        {"priority": base + 1, "table": 254},   # 保留表 -> 不动
        {"priority": 100, "table": 200},        # 非本程序优先级 -> 不动
        {"priority": base + 2, "table": 999},   # 派生区间外 -> 不动
    ])
    monkeypatch.setattr(routing.ex, "run", lambda cmd, **kw: calls.append(cmd))
    cfg = _config(backend="iprule", table_id=201)
    routing.clear_rules(cfg, tables=[200])
    cmds = [" ".join(c) for c in calls]
    assert "ip rule del pref 20000" in cmds
    assert not any("pref 20001" in c or "pref 20002" in c or "pref 100" == c for c in cmds)
    assert "ip route flush table 200" in cmds
    assert "ip route flush table 201" in cmds
    assert not any("254" in c for c in cmds)


def test_clear_rules_deletes_orphan_nft_table(monkeypatch):
    """即使当前后端不是 nftables，也要清掉可能的历史 nft 表。"""
    calls = []
    monkeypatch.setattr(routing, "main_table_routes", lambda: [])
    monkeypatch.setattr(routing.ip, "policy_routing_supported", lambda: False)
    monkeypatch.setattr(routing.shutil, "which", lambda n: "/usr/sbin/nft")
    monkeypatch.setattr(routing.ex, "run", lambda cmd, **kw: calls.append(cmd))
    routing.clear_rules(_config(backend="mainroute"))
    assert ["nft", "delete", "table", "inet", "netswitch"] in calls


# ---------- nft 脚本 / 主表读取 ----------

def test_build_nft_script():
    rule = RuleCfg(name="github", fwmark=1)
    script = routing._build_nft_script("netswitch", [rule],
                                       {"github": ["140.82.112.0/20"]})
    assert "table inet netswitch" in script
    assert "set github_v4" in script
    assert "140.82.112.0/20" in script
    assert "meta mark set 0x1" in script
    assert "type route hook output" in script
    # 无 CIDR 的规则不建 set
    assert "set empty_v4" not in routing._build_nft_script(
        "netswitch", [RuleCfg(name="empty")], {"empty": []})


def test_main_table_routes_filters_by_proto(monkeypatch):
    payload = json.dumps([
        {"dst": "1.1.1.0/24", "dev": "eth0", "protocol": "200"},
        {"dst": "2.2.2.0/24", "dev": "eth0", "protocol": "static"},
        {"dst": "3.3.3.0/24", "dev": "eth0", "protocol": "kernel"},
    ])
    monkeypatch.setattr(routing.ex, "run", lambda *a, **k: _P(payload))
    assert [r["dst"] for r in routing.main_table_routes()] == ["1.1.1.0/24"]


def test_main_table_routes_tolerates_bad_output(monkeypatch):
    monkeypatch.setattr(routing.ex, "run", lambda *a, **k: _P("not json"))
    assert routing.main_table_routes() == []


# ---------- 规则状态 ----------

def test_rule_applied(monkeypatch):
    monkeypatch.setattr(routing.ip, "policy_routing_supported", lambda: True)
    cfg = Config()
    rule = RuleCfg(name="r", table_id=200)
    monkeypatch.setattr(routing.ex, "run",
                        lambda cmd, **kw: _P("default via 1.2.3.4 dev eth0\n"))
    assert routing.rule_applied(cfg, rule) is True
    monkeypatch.setattr(routing.ex, "run", lambda cmd, **kw: _P(""))
    assert routing.rule_applied(cfg, rule) is False


def test_rule_applied_mainroute(monkeypatch):
    cfg = Config(routing=RoutingCfg(backend="mainroute"))
    rule = RuleCfg(name="r", interface="wlp129s0")
    ok = '[{"dst":"140.82.112.0/20","dev":"wlp129s0","protocol":"200"}]'
    monkeypatch.setattr(routing.ex, "run", lambda cmd, **kw: _P(ok))
    assert routing.rule_applied(cfg, rule) is True
    other = '[{"dst":"140.82.112.0/20","dev":"enp2s0","protocol":"200"}]'
    monkeypatch.setattr(routing.ex, "run", lambda cmd, **kw: _P(other))
    assert routing.rule_applied(cfg, rule) is False


def test_rule_applied_mainroute_reuses_snapshot():
    cfg = Config(routing=RoutingCfg(backend="mainroute"))
    rule = RuleCfg(name="r", interface="wlp129s0")
    snapshot = [{"dst": "x", "dev": "wlp129s0", "protocol": "200"}]
    assert routing.rule_applied(cfg, rule, main_routes=snapshot) is True


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


def test_backend_note():
    assert "nftables" in routing.backend_note("nftables")
    assert "mainroute" in routing.backend_note("mainroute")
    assert routing.backend_note("?") == "?"
