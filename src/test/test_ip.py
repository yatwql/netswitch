import json

import pytest

from netswitch import ip


class _P:
    def __init__(self, out="", rc=0, err=""):
        self.stdout = out
        self.returncode = rc
        self.stderr = err


def test_is_physical():
    assert ip.is_physical("enp2s0")
    assert ip.is_physical("wlp129s0")
    assert ip.is_physical("eth0")
    assert not ip.is_physical("lo")
    assert not ip.is_physical("veth123")
    assert not ip.is_physical("br-abc")
    assert not ip.is_physical("lzc-br-1")
    assert not ip.is_physical("docker0")
    assert not ip.is_physical("heiyu-0")
    assert not ip.is_physical("")
    # P2-13：明确虚拟/子接口不再被当成物理网卡
    assert not ip.is_physical("enp2s0.100")     # VLAN 子接口
    assert not ip.is_physical("dummy0")
    assert not ip.is_physical("wg0")
    assert not ip.is_physical("ppp0")
    assert not ip.is_physical("macvlan0")


def test_is_physical_sysfs_confirmed(monkeypatch):
    """sysfs 有 device 节点 = 真实设备（此时不依赖命名前缀）。"""
    monkeypatch.setattr(ip, "has_device", lambda n: True)
    assert ip.is_physical("fox0")
    monkeypatch.setattr(ip, "has_device", lambda n: False)
    assert not ip.is_physical("fox0")


def test_has_device(monkeypatch):
    monkeypatch.setattr(ip.os.path, "exists", lambda p: p == "/sys/class/net/eth0/device")
    assert ip.has_device("eth0") is True
    assert ip.has_device("bond0") is False


def test_is_physical_fallback_ether(monkeypatch):
    monkeypatch.setattr(ip, "has_device", lambda n: False)
    assert ip.is_physical("bond0", {"link_type": "ether", "flags": ["UP"]})
    assert not ip.is_physical("bond0", {"link_type": "ether", "master": "br0"})
    assert not ip.is_physical("bond0", {"link_type": "none"})


def test_subnet_of():
    assert ip.subnet_of("192.168.1.7/24") == "192.168.1.0/24"
    assert ip.subnet_of("10.0.0.1/8") == "10.0.0.0/8"


def test_nft_set_name():
    assert ip.nft_set_name("github") == "github_v4"
    assert ip.nft_set_name("my-rule.x") == "my_rule_x_v4"


def test_rule_pref_guard():
    assert ip.rule_pref(0) == ip.RULE_PREF_BASE
    assert ip.rule_pref(ip.RULE_PREF_MAX_COUNT - 1) < 32766     # 不撞内核默认规则
    with pytest.raises(ValueError):
        ip.rule_pref(ip.RULE_PREF_MAX_COUNT)


def test_table_id_range_excludes_reserved():
    assert ip.TABLE_ID_MAX < 253
    assert ip.TABLE_ID_MIN > 0
    assert ip.MAINROUTE_PROTO <= 255      # iproute2 只接受 1..255 的数值 proto


def test_link_state():
    assert ip.link_state("x", {"operstate": "UP", "flags": []}) == "connected"
    assert ip.link_state("x", {"operstate": "DOWN", "flags": ["NO-CARRIER"]}) == "no-carrier"
    assert ip.link_state("x", {"operstate": "DOWN", "flags": []}) == "down"


def test_admin_up():
    assert ip.admin_up({"flags": ["BROADCAST", "MULTICAST", "UP", "LOWER_UP"]}) is True
    assert ip.admin_up({"flags": ["NO-CARRIER", "UP"]}) is True
    assert ip.admin_up({"flags": ["BROADCAST", "MULTICAST"]}) is False
    assert ip.admin_up({}) is False


def test_parse_ssid():
    out = "Connected to aa:bb:cc:dd:ee:ff (on wlp129s0)\n\tSSID: MyWiFi\n\tfreq: 2412\n"
    assert ip.parse_ssid(out) == "MyWiFi"
    assert ip.parse_ssid("Not connected.") is None
    assert ip.parse_ssid("\tSSID: \n") is None


def test_wireless_ssid_via_iw(monkeypatch):
    monkeypatch.setattr(ip.shutil, "which",
                        lambda n: "/usr/bin/iw" if n == "iw" else None)

    class _PW:
        stdout = "SSID: HomeNet\n"

    monkeypatch.setattr(ip.ex, "run", lambda *a, **k: _PW())
    assert ip.wireless_ssid("wlan0") == "HomeNet"


def test_wireless_ssid_no_tools(monkeypatch):
    monkeypatch.setattr(ip.shutil, "which", lambda n: None)
    assert ip.wireless_ssid("wlan0") is None


# ---------- 能力探测（P1-8：只读，不写系统） ----------

def test_policy_probe_is_readonly_and_unsupported(monkeypatch):
    cmds = []
    monkeypatch.setattr(ip.os, "geteuid", lambda: 0)
    monkeypatch.setattr(ip.ex, "run", lambda cmd, **kw: cmds.append(cmd) or _P())
    ip.policy_routing_supported.cache_clear()
    assert ip.policy_routing_supported() is True
    assert cmds == [["ip", "-j", "rule", "show"]]      # 只有只读查询，没有增删路由

    monkeypatch.setattr(ip.ex, "run", lambda cmd, **kw: _P(
        rc=2, err="RTNETLINK answers: Operation not supported"))
    ip.policy_routing_supported.cache_clear()
    assert ip.policy_routing_supported() is False
    ip.policy_routing_supported.cache_clear()


def test_policy_probe_other_error_assumes_supported(monkeypatch):
    monkeypatch.setattr(ip.os, "geteuid", lambda: 0)
    monkeypatch.setattr(ip.ex, "run", lambda cmd, **kw: _P(rc=1, err="boom"))
    ip.policy_routing_supported.cache_clear()
    assert ip.policy_routing_supported() is True
    ip.policy_routing_supported.cache_clear()


def test_policy_probe_non_root_is_unknown(monkeypatch):
    monkeypatch.setattr(ip.os, "geteuid", lambda: 1000)
    ip.policy_routing_supported.cache_clear()
    assert ip.policy_routing_supported() is None


def test_route_list_parses_json(monkeypatch):
    monkeypatch.setattr(ip.ex, "run", lambda *a, **k: _P(json.dumps([{"dst": "default"}])))
    assert ip.route_list() == [{"dst": "default"}]
