"""netns 集成测试：验证真实 `ip` 命令行为（不需要宿主 root）。

用 `unshare -rn` 起一个临时网络命名空间与 root 身份，在其中跑真实命令：
- P0-1：清理只删本程序 `proto 200` 的路由，内核直连/他人静态路由必须原样保留；
- P1-6：dry-run 不得改动系统状态（能力探测已改为只读）；
- 应用/清理闭环：mainroute 写入后能被自己的清理逻辑删掉。

环境不满足时（无 unshare / 不允许非特权 user namespace）自动跳过。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import textwrap

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _netns_available() -> bool:
    if not (shutil.which("unshare") and shutil.which("ip") and shutil.which("python3")):
        return False
    try:
        return subprocess.run(["unshare", "-rn", "true"],
                              capture_output=True, timeout=10).returncode == 0
    except Exception:  # noqa: BLE001
        return False


needs_netns = pytest.mark.skipif(
    not _netns_available(),
    reason="需要 `unshare -rn`（非特权用户命名空间）与 iproute2",
)


def _run_in_netns(script: str) -> subprocess.CompletedProcess:
    env = dict(os.environ,
               PYTHONPATH=os.path.join(REPO_ROOT, "src", "python"))
    return subprocess.run(
        ["unshare", "-rn", "python3", "-c", textwrap.dedent(script)],
        capture_output=True, text=True, env=env, timeout=180,
    )


def _check(proc: subprocess.CompletedProcess) -> None:
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"


@needs_netns
def test_clear_only_deletes_own_proto200_routes():
    proc = _run_in_netns("""
        import subprocess as sp

        from netswitch import routing
        from netswitch.model import CidrsCfg, Config, RoutingCfg, RuleCfg

        def ip(*args):
            return sp.run(["ip", *args], capture_output=True, text=True)

        ip("link", "set", "lo", "up")
        # 内核/管理员的路由（不允许被删）
        assert ip("route", "add", "198.18.0.0/15", "dev", "lo", "proto", "static").returncode == 0
        assert ip("route", "add", "198.19.0.0/16", "dev", "lo", "proto", "kernel").returncode == 0
        # 本程序的路由（用同一个 proto 签名）
        assert ip("route", "add", "203.0.113.0/24", "dev", "lo",
                  "proto", str(routing.MAINROUTE_PROTO)).returncode == 0

        cfg = Config(routing=RoutingCfg(backend="mainroute"), rules=[RuleCfg(
            name="r", interface="lo", cidrs=CidrsCfg(
                source="manual",
                extra=["198.18.0.0/15", "198.19.0.0/16", "203.0.113.0/24"]))])
        routing.clear_rules(cfg)

        foreign1 = ip("route", "show", "198.18.0.0/15").stdout
        foreign2 = ip("route", "show", "198.19.0.0/16").stdout
        own = ip("route", "show", "203.0.113.0/24").stdout
        assert "198.18.0.0/15" in foreign1, "误删了他人的 static 路由: " + foreign1
        assert "198.19.0.0/16" in foreign2, "误删了内核路由: " + foreign2
        assert own.strip() == "", "本程序的 proto 200 路由没被清掉: " + own
        print("OK")
    """)
    _check(proc)


@needs_netns
def test_apply_then_clear_mainroute_roundtrip():
    proc = _run_in_netns("""
        import subprocess as sp

        from netswitch import routing
        from netswitch.model import (CidrsCfg, Config, IfaceCfg, RoutingCfg, RuleCfg)

        def ip(*args):
            return sp.run(["ip", *args], capture_output=True, text=True)

        # 造一张带网关的"网卡"（veth）用于 mainroute 应用
        ip("link", "add", "veth0", "type", "veth", "peer", "veth1")
        ip("addr", "add", "192.168.99.7/24", "dev", "veth0")
        ip("link", "set", "veth0", "up")

        cfg = Config(
            routing=RoutingCfg(backend="mainroute"),
            interfaces=[IfaceCfg(name="veth0", gateway="192.168.99.1")],
            rules=[RuleCfg(name="r", interface="veth0", cidrs=CidrsCfg(
                source="manual", extra=["203.0.113.0/24"]))],
        )
        warnings = []
        n = routing.apply_rules(cfg, warn=warnings.append)
        assert n == 1, warnings
        out = ip("-j", "route", "show", "203.0.113.0/24").stdout
        assert "veth0" in out and "200" in out, "mainroute 未生效: " + out
        # 清理后必须消失，且不动内核直连路由
        routing.clear_rules(cfg)
        assert ip("route", "show", "203.0.113.0/24").stdout.strip() == ""
        assert "192.168.99.0/24" in ip("route", "show", "192.168.99.0/24").stdout
        print("OK")
    """)
    _check(proc)


@needs_netns
def test_dry_run_does_not_touch_system():
    proc = _run_in_netns("""
        import subprocess as sp

        from netswitch import ip, routing
        from netswitch.model import CidrsCfg, Config, RoutingCfg, RuleCfg

        def snapshot():
            return sp.run(["ip", "-j", "route", "show", "table", "main"],
                          capture_output=True, text=True).stdout

        sp.run(["ip", "link", "set", "lo", "up"], capture_output=True)
        before = snapshot()

        assert ip.policy_routing_supported() in (True, False)   # 只读探测
        assert snapshot() == before, "能力探测改动了系统路由"

        cfg = Config(routing=RoutingCfg(backend="mainroute"), rules=[RuleCfg(
            name="r", interface="lo",
            cidrs=CidrsCfg(source="manual", extra=["203.0.113.0/24"]))])
        routing.clear_rules(cfg, dry_run=True)
        assert snapshot() == before, "dry-run 的清理改动了系统路由"

        sp.run(["ip", "route", "add", "203.0.113.0/24", "dev", "lo",
                "proto", str(routing.MAINROUTE_PROTO)], capture_output=True)
        added = snapshot()
        sp.run(["ip", "route", "del", "203.0.113.0/24", "proto",
                str(routing.MAINROUTE_PROTO), "table", "main"], capture_output=True)
        assert snapshot() == before and added != before
        print("OK")
    """)
    _check(proc)


@needs_netns
def test_clear_only_deletes_own_proto200_routes_v6():
    """IPv6：同样只删本程序 proto 200 的路由，内核直连/他人静态路由保留。"""
    proc = _run_in_netns("""
        import subprocess as sp

        from netswitch import routing
        from netswitch.model import (CidrsCfg, Config, IfaceCfg, RoutingCfg, RuleCfg)

        def ip(*args):
            return sp.run(["ip", *args], capture_output=True, text=True)

        ip("link", "set", "lo", "up")
        ip("link", "add", "dummy0", "type", "dummy")
        ip("link", "set", "dummy0", "up")
        assert ip("-6", "addr", "add", "2001:db8:1::7/64", "dev", "dummy0").returncode == 0

        # 他人的静态路由 + 本程序的路由（proto 200）
        assert ip("-6", "route", "add", "2001:db8:aa::/48", "dev", "dummy0",
                  "proto", "static").returncode == 0
        assert ip("-6", "route", "add", "2001:db8:99::/48", "dev", "dummy0",
                  "proto", str(routing.MAINROUTE_PROTO)).returncode == 0

        cfg = Config(
            routing=RoutingCfg(backend="mainroute", ip_versions=["v4", "v6"]),
            interfaces=[IfaceCfg(name="dummy0", gateway6="2001:db8:1::1")],
            rules=[RuleCfg(name="r", interface="dummy0", cidrs=CidrsCfg(
                source="manual", extra=["2001:db8:99::/48", "2001:db8:aa::/48"]))],
        )
        routing.clear_rules(cfg)

        foreign = ip("-6", "route", "show", "2001:db8:aa::/48").stdout
        own = ip("-6", "route", "show", "2001:db8:99::/48").stdout
        assert "2001:db8:aa::/48" in foreign, "误删了他人的 v6 静态路由: " + foreign
        assert own.strip() == "", "本程序的 v6 proto 200 路由没被清掉: " + own
        print("OK")
    """)
    _check(proc)


@needs_netns
def test_apply_then_clear_v6_mainroute_roundtrip():
    """IPv6：mainroute 应用后能生效，清理后消失，且直连子网路由保留。"""
    proc = _run_in_netns("""
        import subprocess as sp

        from netswitch import routing
        from netswitch.model import (CidrsCfg, Config, IfaceCfg, RoutingCfg, RuleCfg)

        def ip(*args):
            return sp.run(["ip", *args], capture_output=True, text=True)

        ip("link", "add", "dummy0", "type", "dummy")
        ip("link", "set", "dummy0", "up")
        assert ip("-6", "addr", "add", "2001:db8:1::7/64", "dev", "dummy0").returncode == 0

        cfg = Config(
            routing=RoutingCfg(backend="mainroute", ip_versions=["v4", "v6"]),
            interfaces=[IfaceCfg(name="dummy0", gateway6="2001:db8:1::1")],
            rules=[RuleCfg(name="r", interface="dummy0", cidrs=CidrsCfg(
                source="manual", extra=["2001:db8:99::/48"]))],
        )
        warnings = []
        n = routing.apply_rules(cfg, warn=warnings.append)
        assert n == 1, warnings
        out = ip("-6", "-j", "route", "show", "2001:db8:99::/48").stdout
        assert "dummy0" in out and "200" in out, "v6 mainroute 未生效: " + out

        routing.clear_rules(cfg)
        assert ip("-6", "route", "show", "2001:db8:99::/48").stdout.strip() == ""
        assert "2001:db8:1::/64" in ip("-6", "route", "show", "2001:db8:1::/64").stdout
        print("OK")
    """)
    _check(proc)
