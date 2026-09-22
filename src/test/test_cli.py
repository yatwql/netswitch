from netswitch import cli


def test_parser_status():
    a = cli.build_parser().parse_args(["status"])
    assert a.command == "status"


def test_parser_metric_primary():
    a = cli.build_parser().parse_args(["metric", "primary", "wlp129s0"])
    assert a.action == "primary"
    assert a.name == "wlp129s0"


def test_parser_metric_set():
    a = cli.build_parser().parse_args(["metric", "set", "enp2s0", "200"])
    assert a.action == "set"
    assert a.value == 200


def test_parser_rule():
    a = cli.build_parser().parse_args(
        ["rule", "apply", "github", "--interface", "enp2s0"]
    )
    assert a.action == "apply"
    assert a.rule == "github"
    assert a.interface == "enp2s0"


def test_need_confirm_non_interactive(monkeypatch):
    args = cli.build_parser().parse_args(["iface", "down", "eth0"])
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    assert cli._need_confirm(args, "x") is False


def test_need_confirm_yes():
    args = cli.build_parser().parse_args(["iface", "down", "eth0", "--yes"])
    assert cli._need_confirm(args, "x") is True


def test_need_confirm_dry_run():
    args = cli.build_parser().parse_args(["apply", "--dry-run"])
    assert cli._need_confirm(args, "x") is True


def test_need_confirm_env_yes(monkeypatch):
    args = cli.build_parser().parse_args(["iface", "down", "eth0"])
    monkeypatch.setenv("NETSWITCH_YES", "1")
    assert cli._need_confirm(args, "x") is True


def test_need_confirm_env_yes_noninteractive(monkeypatch):
    args = cli.build_parser().parse_args(["iface", "down", "eth0"])
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    monkeypatch.setenv("NETSWITCH_YES", "true")
    assert cli._need_confirm(args, "x") is True
