import pytest

from netswitch import cli
from netswitch.model import Config


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


def test_config_option_must_precede_subcommand():
    """顶层 --config 必须放在子命令之前（systemd 单元曾因此装不上跑不起来）。"""
    a = cli.build_parser().parse_args(["--config", "/x/c.json", "apply", "--dry-run"])
    assert a.config == "/x/c.json" and a.command == "apply"
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["apply", "--yes", "--config", "/x/c.json"])


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


# ---------- 参数错误 / 兜底（P2-10） ----------

def test_metric_set_without_value(monkeypatch, capsys):
    args = cli.build_parser().parse_args(["metric", "set", "enp2s0"])
    monkeypatch.setattr(cli.ex, "require_root", lambda: None)
    monkeypatch.setattr(cli.config_mod, "load", lambda *a, **k: Config())
    assert cli.cmd_metric(args) == 2
    assert "需要指定数值" in capsys.readouterr().err


def test_main_wraps_unexpected_exception(monkeypatch, capsys):
    def boom(cfg):
        raise TypeError("boom")

    monkeypatch.setattr(cli.config_mod, "load", lambda *a, **k: Config())
    monkeypatch.setattr(cli.status, "print_status", boom)
    assert cli.main(["status"]) == 1
    err = capsys.readouterr().err
    assert "TypeError" in err and "boom" in err


def test_main_reports_known_errors(monkeypatch, capsys):
    def boom(cfg):
        raise RuntimeError("配置预检未通过")

    monkeypatch.setattr(cli.config_mod, "load", lambda *a, **k: Config())
    monkeypatch.setattr(cli.status, "print_status", boom)
    assert cli.main(["status"]) == 1
    assert "配置预检未通过" in capsys.readouterr().err


# ---------- systemd 单元模板（P0-2） ----------

def test_service_template_exec_start_parses():
    unit = (cli.REPO_ROOT / "data" / "config" / "netswitch.service").read_text(
        encoding="utf-8").replace("__REPO_ROOT__", str(cli.REPO_ROOT))
    cli._validate_unit(unit)          # 不抛异常即通过
    assert "WorkingDirectory=" in unit
    assert "data/config/config.json" in unit


def test_service_template_detects_bad_arg_order():
    bad = "ExecStart=/usr/bin/python3 -m netswitch.cli apply --yes --config /x/c.json\n"
    with pytest.raises(RuntimeError, match="无法被 netswitch 解析"):
        cli._validate_unit(bad)


def test_service_template_requires_exec_start():
    with pytest.raises(RuntimeError, match="ExecStart"):
        cli._validate_unit("[Service]\nType=oneshot\n")


# ---------- rule add / rule remove（只写配置，不触碰网络） ----------

def _config_file(tmp_path, rules=None):
    import json
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"version": 1, "rules": rules or []}), encoding="utf-8")
    return p


def _no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("add/remove 不应触碰网络（apply_rules 被调用）")

    monkeypatch.setattr(cli.routing, "apply_rules", boom)


def test_parser_rule_add():
    a = cli.build_parser().parse_args(
        ["rule", "add", "10.0.0.0/8", "*.github.com", "--name", "myrule"])
    assert a.action == "add"
    assert a.rule == "10.0.0.0/8"
    assert a.targets == ["*.github.com"]
    assert a.name == "myrule"


def test_parser_rule_remove():
    a = cli.build_parser().parse_args(["rule", "remove", "github"])
    assert a.action == "remove"
    assert a.rule == "github"


def test_rule_add_writes_config_only(tmp_path, monkeypatch, capsys):
    from netswitch import config as config_mod
    p = _config_file(tmp_path)
    _no_network(monkeypatch)
    args = cli.build_parser().parse_args(
        ["--config", str(p), "rule", "add", "10.0.0.0/8", "1.2.3.4", "*.github.com"])
    assert cli.cmd_rule(args) == 0
    out = capsys.readouterr().out
    assert "已写入配置" in out and "尚未生效" in out
    c = config_mod.load(str(p), probe=False)
    assert [r.name for r in c.rules] == ["custom-1"]
    assert c.rules[0].cidrs.extra == ["10.0.0.0/8", "1.2.3.4/32"]
    assert c.rules[0].cidrs.domains == ["*.github.com"]


def test_rule_add_with_name_and_interface(tmp_path, monkeypatch):
    from netswitch import config as config_mod
    p = _config_file(tmp_path)
    _no_network(monkeypatch)
    args = cli.build_parser().parse_args(
        ["--config", str(p), "rule", "add", "example.com", "--name", "site",
         "--interface", "enp2s0"])
    assert cli.cmd_rule(args) == 0
    c = config_mod.load(str(p), probe=False)
    assert c.rules[0].name == "site"
    assert c.rules[0].interface == "enp2s0"
    assert c.rules[0].cidrs.domains == ["example.com"]


def test_rule_add_does_not_require_root(tmp_path, monkeypatch):
    p = _config_file(tmp_path)
    _no_network(monkeypatch)
    monkeypatch.setattr(cli.ex, "require_root",
                        lambda: (_ for _ in ()).throw(AssertionError("不应要求 root")))
    args = cli.build_parser().parse_args(["--config", str(p), "rule", "add", "1.1.1.1"])
    assert cli.cmd_rule(args) == 0


def test_rule_add_dry_run_does_not_write(tmp_path, monkeypatch, capsys):
    p = _config_file(tmp_path)
    _no_network(monkeypatch)
    before = p.read_text(encoding="utf-8")
    args = cli.build_parser().parse_args(
        ["--config", str(p), "rule", "add", "1.1.1.1", "--dry-run"])
    assert cli.cmd_rule(args) == 0
    assert "[dry-run]" in capsys.readouterr().out
    assert p.read_text(encoding="utf-8") == before


def test_rule_add_invalid_input(tmp_path, monkeypatch, capsys):
    p = _config_file(tmp_path)
    _no_network(monkeypatch)
    args = cli.build_parser().parse_args(
        ["--config", str(p), "rule", "add", "10.0.0.0/99"])
    assert cli.cmd_rule(args) == 2
    assert "错误" in capsys.readouterr().err


def test_rule_add_without_targets(tmp_path, monkeypatch, capsys):
    p = _config_file(tmp_path)
    _no_network(monkeypatch)
    args = cli.build_parser().parse_args(["--config", str(p), "rule", "add"])
    assert cli.cmd_rule(args) == 2
    assert "至少一个" in capsys.readouterr().err


def test_rule_remove_writes_config_only(tmp_path, monkeypatch, capsys):
    from netswitch import config as config_mod
    p = _config_file(tmp_path, rules=[{"name": "a"}, {"name": "b"}])
    _no_network(monkeypatch)
    args = cli.build_parser().parse_args(["--config", str(p), "rule", "remove", "a"])
    assert cli.cmd_rule(args) == 0
    assert "已从配置删除规则 a" in capsys.readouterr().out
    assert [r.name for r in config_mod.load(str(p), probe=False).rules] == ["b"]


def test_rule_remove_unknown(tmp_path, monkeypatch, capsys):
    p = _config_file(tmp_path, rules=[{"name": "a"}])
    _no_network(monkeypatch)
    args = cli.build_parser().parse_args(["--config", str(p), "rule", "remove", "zzz"])
    assert cli.cmd_rule(args) == 1
    assert "未找到规则" in capsys.readouterr().err


def test_rule_remove_dry_run(tmp_path, monkeypatch, capsys):
    p = _config_file(tmp_path, rules=[{"name": "a"}])
    _no_network(monkeypatch)
    before = p.read_text(encoding="utf-8")
    args = cli.build_parser().parse_args(
        ["--config", str(p), "rule", "remove", "a", "--dry-run"])
    assert cli.cmd_rule(args) == 0
    assert "[dry-run]" in capsys.readouterr().out
    assert p.read_text(encoding="utf-8") == before
