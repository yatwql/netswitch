from netswitch import ip


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


def test_subnet_of():
    assert ip.subnet_of("192.168.1.7/24") == "192.168.1.0/24"
    assert ip.subnet_of("10.0.0.1/8") == "10.0.0.0/8"


def test_nft_set_name():
    assert ip.nft_set_name("github") == "github_v4"
    assert ip.nft_set_name("my-rule.x") == "my_rule_x_v4"


def test_link_state():
    assert ip.link_state("x", {"operstate": "UP", "flags": []}) == "connected"
    assert ip.link_state("x", {"operstate": "DOWN", "flags": ["NO-CARRIER"]}) == "no-carrier"
    assert ip.link_state("x", {"operstate": "DOWN", "flags": []}) == "down"


def test_admin_up():
    assert ip.admin_up({"flags": ["BROADCAST", "MULTICAST", "UP", "LOWER_UP"]}) is True
    assert ip.admin_up({"flags": ["NO-CARRIER", "UP"]}) is True
    assert ip.admin_up({"flags": ["BROADCAST", "MULTICAST"]}) is False
    assert ip.admin_up({}) is False
