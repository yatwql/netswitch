from netswitch import detect
from netswitch.model import Interface


def _patch(monkeypatch, *, links, v4_addrs=(), v6_addrs=(), v4_routes=(), v6_routes=(),
           v6=True, itype="wired"):
    monkeypatch.setattr(detect.ip, "link_list", lambda dry_run=False: links)
    monkeypatch.setattr(detect.ip, "addr_list",
                        lambda family=4, dry_run=False:
                        list(v6_addrs if family == 6 else v4_addrs))
    monkeypatch.setattr(detect.ip, "route_list",
                        lambda family=4, dry_run=False:
                        list(v6_routes if family == 6 else v4_routes))
    monkeypatch.setattr(detect.ip, "family_available", lambda family=4: v6 if family == 6 else True)
    monkeypatch.setattr(detect.ip, "interface_type", lambda n: itype)
    monkeypatch.setattr(detect.ip, "wireless_ssid", lambda n: None)


def _link(name="enp2s0", operstate="UP", flags=("UP", "LOWER_UP"), link_type="ether"):
    return {"ifname": name, "operstate": operstate, "flags": list(flags),
            "link_type": link_type}


def _addr(ifname, local, prefix, family="inet", scope="global"):
    return {"ifname": ifname, "addr_info": [
        {"family": family, "local": local, "prefixlen": prefix, "scope": scope}]}


def test_primary_interface():
    ifaces = [
        Interface(name="enp2s0", metric=100, state="connected"),
        Interface(name="wlp129s0", metric=600, state="connected"),
        Interface(name="usb0", metric=None, state="down"),
    ]
    assert detect.primary_interface(ifaces) == "enp2s0"


def test_primary_interface_none():
    assert detect.primary_interface([Interface(name="x", metric=None)]) is None


def test_detect_admin_up(monkeypatch):
    _patch(monkeypatch, links=[_link(operstate="DOWN", flags=("NO-CARRIER", "UP"))])
    ifaces = detect.detect_interfaces()
    assert ifaces[0].admin_up is True
    assert ifaces[0].state == "no-carrier"


def test_effective_interface_names(monkeypatch):
    _patch(monkeypatch,
           links=[_link("enp2s0"), _link("wlp129s0")],
           v4_routes=[{"dst": "default", "dev": "enp2s0",
                       "gateway": "192.168.2.1", "metric": 100}])
    assert detect.effective_interface_names() == ["enp2s0"]


def test_detect_fills_v4_and_v6(monkeypatch):
    _patch(monkeypatch,
           links=[_link("enp2s0"), _link("wlp129s0")],
           v4_addrs=[_addr("enp2s0", "192.168.2.7", 24)],
           v6_addrs=[_addr("wlp129s0", "2001:db8:1::7", 64, family="inet6")],
           v4_routes=[{"dst": "default", "dev": "enp2s0",
                       "gateway": "192.168.2.1", "metric": 100}],
           v6_routes=[{"dst": "default", "dev": "wlp129s0",
                       "gateway": "fe80::1", "metric": 1024}])
    ifaces = {i.name: i for i in detect.detect_interfaces()}
    assert ifaces["enp2s0"].ip == "192.168.2.7/24"
    assert ifaces["wlp129s0"].ip6 == "2001:db8:1::7/64"
    assert ifaces["wlp129s0"].gateway6 == "fe80::1"
    assert ifaces["enp2s0"].gateway == "192.168.2.1"


def test_detect_prefers_global_over_link_local_v6(monkeypatch):
    """链路本地地址不能作为分流源地址，只取全局作用域的那个。"""
    _patch(monkeypatch, links=[_link("enp2s0")],
           v6_addrs=[_addr("enp2s0", "fe80::1:2", 64, family="inet6", scope="link"),
                     _addr("enp2s0", "2001:db8:1::7", 64, family="inet6")])
    assert detect.detect_interfaces()[0].ip6 == "2001:db8:1::7/64"


def test_detect_link_local_only_is_none(monkeypatch):
    _patch(monkeypatch, links=[_link("enp2s0")],
           v6_addrs=[_addr("enp2s0", "fe80::1:2", 64, family="inet6", scope="link")])
    assert detect.detect_interfaces()[0].ip6 is None


def test_detect_skips_v6_when_unavailable(monkeypatch):
    calls = []
    _patch(monkeypatch, links=[_link("enp2s0")], v6=False)
    monkeypatch.setattr(detect.ip, "addr_list",
                        lambda family=4, dry_run=False: calls.append(family) or [])
    ifaces = detect.detect_interfaces()
    assert ifaces[0].ip6 is None
    assert 6 not in calls                 # 不查 v6，避免在禁用 IPv6 的主机上刷错误


def test_default_route_picks_min_metric(monkeypatch):
    """同一网卡多条默认路由：取 metric 最小者（v4/v6 同理）。"""
    _patch(monkeypatch, links=[_link("enp2s0")],
           v4_routes=[{"dst": "default", "dev": "enp2s0", "gateway": "1.1.1.1",
                       "metric": 900},
                      {"dst": "default", "dev": "enp2s0", "gateway": "2.2.2.2",
                       "metric": 100}])
    assert detect.detect_interfaces()[0].gateway == "2.2.2.2"


def test_interfaces_config_fragment_includes_gateway6(monkeypatch):
    _patch(monkeypatch, links=[_link("enp2s0")],
           v4_routes=[{"dst": "default", "dev": "enp2s0",
                       "gateway": "192.168.2.1", "metric": 100}],
           v6_routes=[{"dst": "default", "dev": "enp2s0",
                       "gateway": "fe80::1", "metric": 1024}])
    frag = detect.interfaces_config_fragment()
    assert frag[0]["gateway"] == "192.168.2.1"
    assert frag[0]["gateway6"] == "fe80::1"
    assert frag[0]["metric"] == 100


def test_wireless_type_and_ssid(monkeypatch):
    _patch(monkeypatch, links=[_link("wlp129s0")], itype="wireless")
    monkeypatch.setattr(detect.ip, "wireless_ssid", lambda n: "MyWiFi")
    assert detect.detect_interfaces()[0].ssid == "MyWiFi"
