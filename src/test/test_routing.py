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


GW4, SRC4, NET4 = "192.168.1.1", "192.168.1.7", "192.168.1.0/24"
GW6, SRC6, NET6 = "2001:db8:1::1", "2001:db8:1::7", "2001:db8:1::/64"


def _patch_env(monkeypatch, calls, *, ifaces=("wlp129s0",),
               cidrs=("140.82.112.0/20", "2001:db8:99::/48"), nft=None, v6=True,
               v6_policy=True):
    """把 apply_rules/preflight 依赖的外部调用都替换成记录器。"""
    monkeypatch.setattr(routing, "clear_rules", lambda cfg, **kw: calls.append("clear"))
    monkeypatch.setattr(routing.cidrs, "fetch_cidrs",
                        lambda c, warn=None: list(cidrs))
    monkeypatch.setattr(
        routing, "_gw_src",
        lambda cfg, iface, family=4, ifaces=None: (
            (GW6, SRC6, NET6) if family == 6 else (GW4, SRC4, NET4)))
    monkeypatch.setattr(
        routing.detect, "detect_interfaces",
        lambda: [Interface(name=n, gateway=GW4, gateway6=GW6) for n in ifaces])
    monkeypatch.setattr(routing.ip, "link_list",
                        lambda dry_run=False: [{"ifname": n} for n in ifaces])
    monkeypatch.setattr(routing.ip, "family_available",
                        lambda family=4: True if family == 4 else v6)
    monkeypatch.setattr(routing.ip, "policy_routing_supported",
                        lambda family=4: v6_policy if family == 6 else True)
    monkeypatch.setattr(routing.shutil, "which", lambda n: nft)
    monkeypatch.setattr(routing.ex, "run", lambda cmd, **kw: calls.append(cmd))


def _config(backend="mainroute", *, v6=False, rule_v6=None, **rule_kw):
    rule_kw.setdefault("name", "github")
    rule_kw.setdefault("interface", "wlp129s0")
    rule_kw.setdefault("cidrs", CidrsCfg(source="manual", extra=["140.82.112.0/20"]))
    return Config(
        routing=RoutingCfg(backend=backend,
                           ip_versions=["v4", "v6"] if v6 else ["v4"]),
        rules=[RuleCfg(**rule_kw)],
    )


def _cmds(calls):
    return [" ".join(c) for c in calls if isinstance(c, list)]


# ---------- 后端解析 / 地址族 ----------

def test_resolve_backend(monkeypatch):
    monkeypatch.setattr(routing.ip, "policy_routing_supported", lambda family=4: True)
    monkeypatch.setattr(routing.shutil, "which", lambda n: None)
    assert routing.resolve_backend("auto") == "iprule"
    monkeypatch.setattr(routing.shutil, "which", lambda n: "/usr/sbin/nft")
    assert routing.resolve_backend("auto") == "nftables"
    assert routing.resolve_backend("iprule") == "iprule"
    assert routing.resolve_backend("nftables") == "nftables"
    assert routing.resolve_backend("mainroute") == "mainroute"


def test_resolve_backend_mainroute_fallback(monkeypatch):
    monkeypatch.setattr(routing.ip, "policy_routing_supported", lambda family=4: False)
    assert routing.resolve_backend("auto") == "mainroute"


def test_resolve_backend_unknown_capability(monkeypatch):
    """非 root 时能力未知（None）：不回退 mainroute，按"支持"处理。"""
    monkeypatch.setattr(routing.ip, "policy_routing_supported", lambda family=4: None)
    monkeypatch.setattr(routing.shutil, "which", lambda n: "/usr/sbin/nft")
    assert routing.resolve_backend("auto") == "nftables"


def test_rule_families_default_v4():
    cfg = _config()
    assert routing.rule_families(cfg, cfg.rules[0]) == (4,)


def test_rule_families_global_v6():
    cfg = _config(v6=True)
    assert routing.rule_families(cfg, cfg.rules[0]) == (4, 6)


def test_rule_families_rule_override():
    """规则级 ip_versions 覆盖全局（如只有这条规则走 IPv6）。"""
    cfg = _config(v6=False, rule_v6=True, cidrs=CidrsCfg(
        source="manual", extra=["2001:db8:99::/48"], ip_versions=["v6"]))
    assert routing.rule_families(cfg, cfg.rules[0]) == (6,)


# ---------- 应用（IPv4） ----------

def test_apply_rules_mainroute_commands(monkeypatch):
    calls = []
    _patch_env(monkeypatch, calls)
    assert routing.apply_rules(_config(backend="mainroute")) == 1
    cmds = _cmds(calls)
    assert "ip route replace 140.82.112.0/20 via 192.168.1.1 dev wlp129s0 proto 200" in cmds
    assert not any(c.startswith("ip rule") for c in cmds)
    assert not any(c.startswith("nft") for c in cmds)


def test_apply_fetches_each_source_once(monkeypatch):
    """同一次 apply 里每个 CIDR 源只拉取一次。"""
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
    with pytest.raises(RuntimeError, match="预检未通过"):
        routing.apply_rules(_config(backend="mainroute", interface="eth9"))
    assert calls == []


def test_apply_preflight_reports_missing_gateway(monkeypatch):
    calls = []
    _patch_env(monkeypatch, calls)

    def boom(cfg, iface, family=4, ifaces=None):
        raise RuntimeError("无法确定 wlp129s0 的网关")

    monkeypatch.setattr(routing, "_gw_src", boom)
    with pytest.raises(RuntimeError, match="网关"):
        routing.apply_rules(_config(backend="mainroute"))


def test_apply_warns_when_egress_not_connected(monkeypatch):
    calls = []
    msgs = []
    _patch_env(monkeypatch, calls)
    monkeypatch.setattr(routing.detect, "detect_interfaces",
                        lambda: [Interface(name="wlp129s0", gateway=GW4,
                                           state="no-carrier", admin_up=True)])
    assert routing.apply_rules(_config(backend="mainroute"), warn=msgs.append) == 1
    assert any("当前未连接" in m for m in msgs)


def test_apply_warns_when_egress_not_physical(monkeypatch):
    calls = []
    msgs = []
    _patch_env(monkeypatch, calls)
    monkeypatch.setattr(routing.ip, "link_list",
                        lambda dry_run=False: [{"ifname": "other0"}])
    monkeypatch.setattr(routing.detect, "detect_interfaces",
                        lambda: [Interface(name="enp2s0", gateway=GW4)])
    assert routing.apply_rules(_config(backend="mainroute", interface="other0"),
                               warn=msgs.append) == 1
    assert any("不是探测到的物理网卡" in m for m in msgs)


def test_apply_rolls_back_on_failure(monkeypatch):
    cleared = []
    calls = []
    _patch_env(monkeypatch, calls, nft=None)

    def fake_clear(cfg, **kw):
        cleared.append(kw.get("keep_mainroute"))

    monkeypatch.setattr(routing, "clear_rules", fake_clear)

    def boom(cmd, **kw):
        raise routing.ex.ExecError(cmd, 2, "boom")

    monkeypatch.setattr(routing.ex, "run", boom)
    with pytest.raises(routing.ex.ExecError):
        routing.apply_rules(_config(backend="iprule", table_id=200, fwmark=1))
    assert len(cleared) == 2          # 一次正常清理 + 一次失败回滚清理


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
    cfg = _config(backend="iprule", table_id=200, fwmark=1)
    assert routing.apply_rules(cfg) == 1
    cmds = _cmds(calls)
    assert "ip rule add to 140.82.112.0/20 lookup 200 pref 20000" in cmds
    assert "ip rule add to 140.82.113.0/24 lookup 200 pref 20001" in cmds


def test_apply_iprule_rejects_too_many_rules(monkeypatch):
    """ip rule 优先级不得撞上内核默认规则（pref 32766）。"""
    calls = []
    _patch_env(monkeypatch, calls, nft=None)
    many = [f"10.{i // 256}.{i % 256}.0/24"
            for i in range(routing.ip.RULE_PREF_MAX_COUNT + 1)]
    monkeypatch.setattr(routing.cidrs, "fetch_cidrs", lambda c, warn=None: many)
    with pytest.raises(RuntimeError, match="超过上限"):
        routing.apply_rules(_config(backend="iprule", table_id=200, fwmark=1))


# ---------- 应用（IPv6） ----------

def _v6_config(backend="mainroute", **kw):
    return _config(backend=backend, v6=True, cidrs=CidrsCfg(
        source="manual", extra=["140.82.112.0/20", "2001:db8:99::/48"]), **kw)


def test_apply_mainroute_v6_commands(monkeypatch):
    calls = []
    _patch_env(monkeypatch, calls)
    assert routing.apply_rules(_v6_config()) == 1
    cmds = _cmds(calls)
    assert "ip -6 route replace 2001:db8:99::/48 via 2001:db8:1::1 dev wlp129s0 proto 200" in cmds
    assert "ip route replace 140.82.112.0/20 via 192.168.1.1 dev wlp129s0 proto 200" in cmds


def test_apply_iprule_v6_commands(monkeypatch):
    """IPv6 走 iprule 后端时必须用 `ip -6`，并单独建立 v6 路由表内容。"""
    calls = []
    _patch_env(monkeypatch, calls, nft=None)
    cfg = _v6_config(backend="iprule", table_id=200, fwmark=1)
    assert routing.apply_rules(cfg) == 1
    cmds = _cmds(calls)
    assert "ip -6 route add default via 2001:db8:1::1 dev wlp129s0 table 200" in cmds
    assert "ip -6 route add 2001:db8:1::/64 dev wlp129s0 proto kernel scope link " \
           "src 2001:db8:1::7 table 200" in cmds
    assert "ip -6 rule add to 2001:db8:99::/48 lookup 200 pref 20001" in cmds
    # v4 仍按原样执行
    assert "ip rule add to 140.82.112.0/20 lookup 200 pref 20000" in cmds


def test_apply_nftables_adds_rule_per_family(monkeypatch):
    calls = []
    _patch_env(monkeypatch, calls, nft="/usr/sbin/nft")
    cfg = _v6_config(backend="nftables", table_id=200, fwmark=1)
    assert routing.apply_rules(cfg) == 1
    cmds = _cmds(calls)
    assert "ip rule add fwmark 0x1 lookup 200 pref 20000" in cmds
    assert "ip -6 rule add fwmark 0x1 lookup 200 pref 20001" in cmds
    assert ["nft", "-f", "-"] in calls


def test_apply_rejects_rule_without_table_or_fwmark(monkeypatch):
    """手工构造的 Config 没走 config.load（不会自动分配 table_id/fwmark）时给出清晰错误。"""
    calls = []
    _patch_env(monkeypatch, calls, nft=None)
    cfg = _v6_config(backend="iprule")          # 未指定 table_id/fwmark
    with pytest.raises(RuntimeError, match="缺少 table_id/fwmark"):
        routing.apply_rules(cfg)


def test_build_nft_script_dual_stack():
    rule = RuleCfg(name="github", fwmark=3)
    plan = {"github": [
        routing.FamilyPlan(family=4, cidrs=["140.82.112.0/20"], gateway="1.1.1.1"),
        routing.FamilyPlan(family=6, cidrs=["2001:db8:99::/48"], gateway="2001:db8::1"),
    ]}
    script = routing._build_nft_script("netswitch", [rule], plan)
    assert "set github_v4 { type ipv4_addr" in script
    assert "set github_v6 { type ipv6_addr" in script
    assert "ip daddr @github_v4 meta mark set 0x3" in script
    assert "ip6 daddr @github_v6 meta mark set 0x3" in script
    assert script.count("chain") == 2


def test_apply_skips_v6_when_family_unavailable(monkeypatch):
    """内核未启用 IPv6：告警跳过 v6，v4 照常应用。"""
    calls = []
    msgs = []
    _patch_env(monkeypatch, calls, v6=False)
    assert routing.apply_rules(_v6_config(), warn=msgs.append) == 1
    assert any("未启用 IPv6" in m for m in msgs)
    assert not any("-6" in c for c in _cmds(calls))


def test_apply_skips_v6_without_gateway(monkeypatch):
    """出口网卡没有 IPv6 网关：告警跳过 v6（不是硬错误），v4 照常应用。"""
    calls = []
    msgs = []
    _patch_env(monkeypatch, calls)

    def gw(cfg, iface, family=4, ifaces=None):
        if family == 6:
            raise RuntimeError("无法确定 wlp129s0 的IPv6 网关")
        return GW4, SRC4, NET4

    monkeypatch.setattr(routing, "_gw_src", gw)
    assert routing.apply_rules(_v6_config(table_id=200, fwmark=1), warn=msgs.append) == 1
    assert any("跳过 IPv6" in m for m in msgs)
    assert not any(" -6 " in f" {c} " for c in _cmds(calls))


def test_apply_v6_only_rule_without_v6_is_skipped(monkeypatch):
    """规则只声明 v6 且环境无 v6 网关：该规则整体跳过（不报错、不影响其它规则）。"""
    calls = []
    msgs = []
    _patch_env(monkeypatch, calls)
    monkeypatch.setattr(routing, "_gw_src",
                        lambda cfg, iface, family=4, ifaces=None: (_ for _ in ()).throw(
                            RuntimeError("无法确定 wlp129s0 的IPv6 网关")))
    cfg = _config(backend="mainroute", cidrs=CidrsCfg(
        source="manual", extra=["2001:db8:99::/48"], ip_versions=["v6"]))
    assert routing.apply_rules(cfg, warn=msgs.append) == 0
    assert any("没有可用的地址族" in m for m in msgs)


def test_apply_iprule_v6_policy_unsupported(monkeypatch):
    """内核不支持 IPv6 策略路由（CONFIG_IPV6_MULTIPLE_TABLES 缺失）：跳过 v6。"""
    calls = []
    msgs = []
    _patch_env(monkeypatch, calls, nft=None, v6_policy=False)
    assert routing.apply_rules(_v6_config(backend="iprule", table_id=200, fwmark=1),
                               warn=msgs.append) == 1
    assert any("不支持 IPv6 策略路由" in m for m in msgs)


def test_apply_mainroute_ignores_v6_policy_support(monkeypatch):
    """mainroute 后端不依赖策略路由，v6 策略路由不可用也应正常分流。"""
    calls = []
    msgs = []
    _patch_env(monkeypatch, calls, v6_policy=False)
    assert routing.apply_rules(_v6_config(), warn=msgs.append) == 1
    assert not any("跳过 IPv6" in m for m in msgs)
    assert any("ip -6 route replace 2001:db8:99::/48" in c for c in _cmds(calls))

# ---------- 清理（只删自己的产物） ----------

def test_clear_rules_never_deletes_foreign_routes(monkeypatch):
    calls = []
    monkeypatch.setattr(routing, "main_table_routes", lambda family=4: [])
    monkeypatch.setattr(routing.ip, "policy_routing_supported", lambda family=4: False)
    monkeypatch.setattr(routing.ip, "family_available", lambda family=4: True)
    monkeypatch.setattr(routing.shutil, "which", lambda n: None)
    monkeypatch.setattr(routing.ex, "run", lambda cmd, **kw: calls.append(cmd))
    cfg = _config(backend="mainroute", cidrs=CidrsCfg(
        source="manual", extra=["192.168.77.0/24", "2001:db8:77::/48"]), v6=True)
    routing.clear_rules(cfg)
    assert not any("del" in " ".join(c) for c in calls)


def test_clear_rules_deletes_only_own_mainroute(monkeypatch):
    calls = []
    own = [{"dst": "140.82.112.0/20", "dev": "wlp129s0", "protocol": "200"}]
    monkeypatch.setattr(routing, "main_table_routes", lambda family=4: own)
    monkeypatch.setattr(routing.ip, "policy_routing_supported", lambda family=4: False)
    monkeypatch.setattr(routing.ip, "family_available", lambda family=4: True)
    monkeypatch.setattr(routing.shutil, "which", lambda n: None)
    monkeypatch.setattr(routing.ex, "run", lambda cmd, **kw: calls.append(cmd))
    cfg = _config(backend="mainroute", cidrs=CidrsCfg(
        source="manual", extra=["140.82.112.0/20", "10.9.9.0/24"]))
    routing.clear_rules(cfg)
    cmds = _cmds(calls)
    assert "ip route del 140.82.112.0/20 proto 200 dev wlp129s0 table main" in cmds
    assert not any("10.9.9.0/24" in c for c in cmds)


def test_clear_rules_deletes_v6_mainroute(monkeypatch):
    """IPv6 明细路由用 `ip -6` 删除，且仍受 proto 签名保护。"""
    calls = []
    v4 = [{"dst": "140.82.112.0/20", "dev": "wlp129s0", "protocol": "200"}]
    v6 = [{"dst": "2001:db8:99::/48", "dev": "wlp129s0", "protocol": "200"}]
    monkeypatch.setattr(routing, "main_table_routes",
                        lambda family=4: v4 if family == 4 else v6)
    monkeypatch.setattr(routing.ip, "policy_routing_supported", lambda family=4: False)
    monkeypatch.setattr(routing.ip, "family_available", lambda family=4: True)
    monkeypatch.setattr(routing.shutil, "which", lambda n: None)
    monkeypatch.setattr(routing.ex, "run", lambda cmd, **kw: calls.append(cmd))
    cfg = _config(backend="mainroute", cidrs=CidrsCfg(
        source="manual", extra=["140.82.112.0/20", "2001:db8:99::/48"]), v6=True)
    routing.clear_rules(cfg)
    cmds = _cmds(calls)
    assert "ip -6 route del 2001:db8:99::/48 proto 200 dev wlp129s0 table main" in cmds
    assert "ip route del 140.82.112.0/20 proto 200 dev wlp129s0 table main" in cmds


def test_clear_rules_skips_other_interface(monkeypatch):
    calls = []
    own = [{"dst": "140.82.112.0/20", "dev": "enp2s0", "protocol": "200"}]
    monkeypatch.setattr(routing, "main_table_routes", lambda family=4: own)
    monkeypatch.setattr(routing.ip, "policy_routing_supported", lambda family=4: False)
    monkeypatch.setattr(routing.ip, "family_available", lambda family=4: True)
    monkeypatch.setattr(routing.shutil, "which", lambda n: None)
    monkeypatch.setattr(routing.ex, "run", lambda cmd, **kw: calls.append(cmd))
    routing.clear_rules(_config(backend="mainroute"))     # 规则出口是 wlp129s0
    assert not any("del" in " ".join(c) for c in calls)


def test_clear_rules_keeps_routes_to_rebuild(monkeypatch):
    calls = []
    own = [{"dst": "140.82.112.0/20", "dev": "wlp129s0", "protocol": "200"}]
    monkeypatch.setattr(routing, "main_table_routes", lambda family=4: own)
    monkeypatch.setattr(routing.ip, "policy_routing_supported", lambda family=4: False)
    monkeypatch.setattr(routing.ip, "family_available", lambda family=4: True)
    monkeypatch.setattr(routing.shutil, "which", lambda n: None)
    monkeypatch.setattr(routing.ex, "run", lambda cmd, **kw: calls.append(cmd))
    routing.clear_rules(_config(backend="mainroute"),
                        keep_mainroute={("140.82.112.0/20", "wlp129s0")})
    assert not any("del" in " ".join(c) for c in calls)


def test_clear_rules_dry_run_prints_planned_deletes(monkeypatch):
    calls = []
    monkeypatch.setattr(routing, "main_table_routes", lambda family=4: [])
    monkeypatch.setattr(routing.ip, "policy_routing_supported", lambda family=4: False)
    monkeypatch.setattr(routing.ip, "family_available", lambda family=4: True)
    monkeypatch.setattr(routing.shutil, "which", lambda n: None)
    monkeypatch.setattr(routing.ex, "run", lambda cmd, **kw: calls.append(cmd))
    routing.clear_rules(_config(backend="mainroute"), dry_run=True)
    assert any("proto 200" in " ".join(c) for c in calls)


def test_clear_rules_dry_run_covers_v6(monkeypatch):
    calls = []
    monkeypatch.setattr(routing, "main_table_routes", lambda family=4: [])
    monkeypatch.setattr(routing.ip, "policy_routing_supported", lambda family=4: False)
    monkeypatch.setattr(routing.ip, "family_available", lambda family=4: True)
    monkeypatch.setattr(routing.shutil, "which", lambda n: None)
    monkeypatch.setattr(routing.ex, "run", lambda cmd, **kw: calls.append(cmd))
    cfg = _config(backend="mainroute", cidrs=CidrsCfg(
        source="manual", extra=["2001:db8:99::/48"]), v6=True)
    routing.clear_rules(cfg, dry_run=True)
    assert any("ip -6 route del 2001:db8:99::/48" in " ".join(c) for c in calls)


def test_clear_rules_sweeps_stale_ip_rules_and_tables(monkeypatch):
    """改配置/改表号后遗留的 ip rule 与路由表也要清掉，但不碰保留表（v4/v6 各自）。"""
    calls = []
    base = routing.ip.RULE_PREF_BASE
    monkeypatch.setattr(routing, "main_table_routes", lambda family=4: [])
    monkeypatch.setattr(routing.shutil, "which", lambda n: None)
    monkeypatch.setattr(routing.ip, "policy_routing_supported", lambda family=4: True)
    monkeypatch.setattr(routing.ip, "family_available", lambda family=4: True)
    monkeypatch.setattr(routing.ip, "rule_list", lambda *a, **k: [
        {"priority": base, "table": 200},       # 本程序（历史表）
        {"priority": base + 1, "table": 254},   # 保留表 -> 不动
        {"priority": 100, "table": 200},        # 非本程序优先级 -> 不动
        {"priority": base + 2, "table": 999},   # 派生区间外 -> 不动
    ])
    monkeypatch.setattr(routing.ex, "run", lambda cmd, **kw: calls.append(cmd))
    cfg = _config(backend="iprule", table_id=201)
    routing.clear_rules(cfg, tables=[200])
    cmds = _cmds(calls)
    assert "ip rule del pref 20000" in cmds
    assert "ip -6 rule del pref 20000" in cmds
    assert not any("pref 20001" in c or "pref 20002" in c for c in cmds)
    assert "ip route flush table 200" in cmds
    assert "ip route flush table 201" in cmds
    assert "ip -6 route flush table 200" in cmds
    assert not any("254" in c for c in cmds)


def test_clear_rules_skips_v6_when_unavailable(monkeypatch):
    calls = []
    monkeypatch.setattr(routing, "main_table_routes", lambda family=4: [])
    monkeypatch.setattr(routing.shutil, "which", lambda n: None)
    monkeypatch.setattr(routing.ip, "policy_routing_supported",
                        lambda family=4: False if family == 6 else True)
    monkeypatch.setattr(routing.ip, "family_available", lambda family=4: False)
    monkeypatch.setattr(routing.ex, "run", lambda cmd, **kw: calls.append(cmd))
    routing.clear_rules(_config(backend="iprule"))
    assert not any("-6" in c for c in _cmds(calls))


def test_clear_rules_deletes_orphan_nft_table(monkeypatch):
    """即使当前后端不是 nftables，也要清掉可能的历史 nft 表。"""
    calls = []
    monkeypatch.setattr(routing, "main_table_routes", lambda family=4: [])
    monkeypatch.setattr(routing.ip, "policy_routing_supported", lambda family=4: False)
    monkeypatch.setattr(routing.ip, "family_available", lambda family=4: True)
    monkeypatch.setattr(routing.shutil, "which", lambda n: "/usr/sbin/nft")
    monkeypatch.setattr(routing.ex, "run", lambda cmd, **kw: calls.append(cmd))
    routing.clear_rules(_config(backend="mainroute"))
    assert ["nft", "delete", "table", "inet", "netswitch"] in calls


# ---------- 主表读取 / 规则状态 ----------

def test_main_table_routes_filters_by_proto(monkeypatch):
    payload = json.dumps([
        {"dst": "1.1.1.0/24", "dev": "eth0", "protocol": "200"},
        {"dst": "2.2.2.0/24", "dev": "eth0", "protocol": "static"},
        {"dst": "3.3.3.0/24", "dev": "eth0", "protocol": "kernel"},
    ])
    monkeypatch.setattr(routing.ex, "run", lambda *a, **k: _P(payload))
    assert [r["dst"] for r in routing.main_table_routes()] == ["1.1.1.0/24"]


def test_main_table_routes_uses_family_flag(monkeypatch):
    cmds = []
    monkeypatch.setattr(routing.ex, "run",
                        lambda cmd, **kw: cmds.append(cmd) or _P("[]"))
    routing.main_table_routes(6)
    assert cmds == [["ip", "-6", "-j", "route", "show", "table", "main"]]


def test_main_table_routes_tolerates_bad_output(monkeypatch):
    monkeypatch.setattr(routing.ex, "run", lambda *a, **k: _P("not json"))
    assert routing.main_table_routes() == []


def test_main_table_routes_all_skips_unavailable_v6(monkeypatch):
    monkeypatch.setattr(routing.ip, "family_available", lambda family=4: False)
    monkeypatch.setattr(routing, "main_table_routes", lambda family=4: [{"dst": "x"}])
    assert routing.main_table_routes_all() == {4: [{"dst": "x"}]}


def test_rule_applied(monkeypatch):
    monkeypatch.setattr(routing.ip, "policy_routing_supported", lambda family=4: True)
    monkeypatch.setattr(routing.ip, "family_available", lambda family=4: True)
    cfg = Config()
    rule = RuleCfg(name="r", table_id=200)
    monkeypatch.setattr(routing.ex, "run",
                        lambda cmd, **kw: _P("default via 1.2.3.4 dev eth0\n"))
    assert routing.rule_applied(cfg, rule) is True
    monkeypatch.setattr(routing.ex, "run", lambda cmd, **kw: _P(""))
    assert routing.rule_applied(cfg, rule) is False


def test_rule_applied_v6_checks_both_tables(monkeypatch):
    monkeypatch.setattr(routing.ip, "policy_routing_supported", lambda family=4: True)
    monkeypatch.setattr(routing.ip, "family_available", lambda family=4: True)
    cfg = _config(backend="iprule", v6=True, table_id=200)
    seen = []

    def run(cmd, **kw):
        seen.append(cmd)
        return _P("default via 1.2.3.4 dev eth0\n")

    monkeypatch.setattr(routing.ex, "run", run)
    assert routing.rule_applied(cfg, cfg.rules[0]) is True
    assert ["ip", "route", "show", "table", "200"] in seen
    assert ["ip", "-6", "route", "show", "table", "200"] in seen


def test_rule_applied_v6_missing_one_family(monkeypatch):
    monkeypatch.setattr(routing.ip, "policy_routing_supported", lambda family=4: True)
    monkeypatch.setattr(routing.ip, "family_available", lambda family=4: True)
    cfg = _config(backend="iprule", v6=True, table_id=200)

    def run(cmd, **kw):
        return _P("default via 1.2.3.4 dev eth0\n" if "-6" not in cmd else "")

    monkeypatch.setattr(routing.ex, "run", run)
    assert routing.rule_applied(cfg, cfg.rules[0]) is False


def test_rule_applied_mainroute(monkeypatch):
    monkeypatch.setattr(routing.ip, "family_available", lambda family=4: True)
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
    snapshot = {4: [{"dst": "x", "dev": "wlp129s0", "protocol": "200"}]}
    assert routing.rule_applied(cfg, rule, main_routes=snapshot) is True


def test_rule_applied_accepts_legacy_v4_snapshot():
    """兼容旧调用：直接传 v4 路由列表（按 v4 处理）。"""
    cfg = Config(routing=RoutingCfg(backend="mainroute"))
    rule = RuleCfg(name="r", interface="wlp129s0")
    assert routing.rule_applied(cfg, rule, main_routes=[
        {"dst": "x", "dev": "wlp129s0", "protocol": "200"}]) is True


def test_rule_applied_v6_mainroute_needs_both_families(monkeypatch):
    monkeypatch.setattr(routing.ip, "family_available", lambda family=4: True)
    cfg = _config(backend="mainroute", v6=True, table_id=200)
    rule = cfg.rules[0]
    only_v4 = {4: [{"dst": "x", "dev": "wlp129s0", "protocol": "200"}], 6: []}
    assert routing.rule_applied(cfg, rule, main_routes=only_v4) is False
    both = {4: [{"dst": "x", "dev": "wlp129s0", "protocol": "200"}],
            6: [{"dst": "y", "dev": "wlp129s0", "protocol": "200"}]}
    assert routing.rule_applied(cfg, rule, main_routes=both) is True


def test_rule_applied_v6_unavailable(monkeypatch):
    monkeypatch.setattr(routing.ip, "family_available", lambda family=4: False)
    cfg = _config(backend="mainroute", v6=True, table_id=200)
    assert routing.rule_applied(cfg, cfg.rules[0], main_routes={4: []}) is False


# ---------- 其它 ----------

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


def test_family_plan_label():
    fp = routing.FamilyPlan(family=6, cidrs=["2001:db8::/32"], gateway="fe80::1")
    assert fp.label == "IPv6"


def test_backend_note():
    assert "nftables" in routing.backend_note("nftables")
    assert "mainroute" in routing.backend_note("mainroute")
    assert routing.backend_note("?") == "?"


# ---------- 域名规则端到端（解析 → 分流，v4+v6） ----------

def _fake_dns(table):
    import socket as _socket

    def fake(host, port, proto=None):
        if host in table:
            fam = _socket.AF_INET6 if ":" in table[host][0] else _socket.AF_INET
            return [(fam, _socket.SOCK_STREAM, 6, "",
                     (a, 0) if fam == _socket.AF_INET else (a, 0, 0, 0))
                    for a in table[host]]
        raise _socket.gaierror(-2, "not found")
    return fake


def test_apply_rule_with_domains_resolves_cidrs(monkeypatch, tmp_path):
    """域名规则：apply 时才解析（A + AAAA），并按地址族分流。"""
    from netswitch import cidrs as cidrs_mod

    calls = []
    real_fetch = cidrs_mod.fetch_cidrs          # 先取真实现（_patch_env 会把它换掉）
    _patch_env(monkeypatch, calls)
    monkeypatch.setattr(routing.cidrs, "fetch_cidrs", real_fetch)
    monkeypatch.setattr(cidrs_mod.socket, "getaddrinfo",
                        _fake_dns({"example.com": ["1.2.3.4", "2001:db8::5"]}))

    cfg = Config(
        routing=RoutingCfg(backend="mainroute", ip_versions=["v4", "v6"]),
        rules=[RuleCfg(name="site", interface="wlp129s0", cidrs=CidrsCfg(
            source="manual", domains=["example.com"],
            cache_file=str(tmp_path / "cache-site.json")))],
    )
    assert routing.apply_rules(cfg) == 1
    cmds = _cmds(calls)
    assert "ip route replace 1.2.3.4/32 via 192.168.1.1 dev wlp129s0 proto 200" in cmds
    assert ("ip -6 route replace 2001:db8::5/128 via 2001:db8:1::1 "
            "dev wlp129s0 proto 200") in cmds
