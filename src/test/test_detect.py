from netswitch import detect
from netswitch.model import Interface


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
    monkeypatch.setattr(detect.ip, "link_list", lambda dry_run=False: [
        {"ifname": "enp2s0", "operstate": "DOWN",
         "flags": ["NO-CARRIER", "UP"], "link_type": "ether"},
    ])
    monkeypatch.setattr(detect.ip, "addr_list", lambda family=4, dry_run=False: [])
    monkeypatch.setattr(detect.ip, "route_list", lambda dry_run=False: [])
    monkeypatch.setattr(detect.ip, "interface_type", lambda n: "wired")
    ifaces = detect.detect_interfaces()
    assert ifaces[0].admin_up is True
    assert ifaces[0].state == "no-carrier"


def test_effective_interface_names(monkeypatch):
    monkeypatch.setattr(detect.ip, "link_list", lambda dry_run=False: [
        {"ifname": "enp2s0", "operstate": "UP",
         "flags": ["UP", "LOWER_UP"], "link_type": "ether"},
        {"ifname": "wlp129s0", "operstate": "UP",
         "flags": ["UP", "LOWER_UP"], "link_type": "ether"},
    ])
    monkeypatch.setattr(detect.ip, "addr_list", lambda family=4, dry_run=False: [])
    monkeypatch.setattr(detect.ip, "route_list", lambda dry_run=False: [
        {"dst": "default", "dev": "enp2s0", "gateway": "192.168.2.1", "metric": 100},
    ])
    monkeypatch.setattr(detect.ip, "interface_type", lambda n: "wired")
    assert detect.effective_interface_names() == ["enp2s0"]
