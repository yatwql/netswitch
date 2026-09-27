"""命令行入口（脚本接口）。破坏性操作需确认，可用 -y/--yes 跳过。"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path

from . import apply as apply_mod
from . import cidrs as cidrs_mod
from . import config as config_mod
from . import detect, exec as ex, iface, log, routing, status
from .model import Config

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load(args) -> Config:
    return config_mod.load(args.config)


_YES_ENV = ("1", "true", "yes", "on")


def _need_confirm(args, prompt: str) -> bool:
    """破坏性操作确认（默认确认）。

    - 默认：交互提示 `[y/N]`。
    - `-y/--yes` 或环境变量 `NETSWITCH_YES=1`：跳过确认（脚本/批量场景）。
    - `--dry-run`：不实际执行，无需确认。
    - 非交互（stdin 非 TTY）且未跳过：拒绝执行，避免脚本误改网络。
    """
    if getattr(args, "dry_run", False):
        return True
    if getattr(args, "yes", False) or os.environ.get("NETSWITCH_YES", "").lower() in _YES_ENV:
        return True
    if not sys.stdin.isatty():
        print(f"非交互环境，拒绝执行：{prompt}"
              f"（加 -y/--yes 或 NETSWITCH_YES=1 跳过）", file=sys.stderr)
        return False
    return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")


def cmd_status(args) -> int:
    status.print_status(_load(args))
    return 0


def cmd_detect(args) -> int:
    frag = detect.interfaces_config_fragment()
    print("# 拟生成的 interfaces 段：")
    print(json.dumps({"interfaces": frag}, ensure_ascii=False, indent=2))
    if args.write:
        config_mod.update_interfaces(args.config, frag)
        print(f"# 已写入 {args.config}（仅更新 interfaces，其余字段保留）")
    else:
        print("# 提示：加 --write 才写回配置文件")
    return 0


def cmd_seed_defaults(args) -> int:
    if config_mod.seed_default_rules(args.config):
        print(f"已写入缺省分流规则到 {args.config}")
        print("提示：在 TUI 中选中规则按 e 选择出口网卡，或编辑 config.json 的 rules[].interface")
    else:
        print("无需写入（已有规则，或 config.example.json 中无规则）")
    return 0


def cmd_iface(args) -> int:
    ex.require_root()
    up = args.action == "up"
    action = "开启" if up else "关闭"
    if not _need_confirm(args, f"确认{action}网卡 {args.name}?"):
        print("已取消", file=sys.stderr)
        return 1
    iface.set_link(args.name, up, force=args.force, dry_run=args.dry_run)
    return 0


def cmd_metric(args) -> int:
    ex.require_root()
    config = _load(args)
    if args.action == "set":
        if args.value is None:
            print("错误：metric set 需要指定数值，例如 `metric set enp2s0 200`",
                  file=sys.stderr)
            return 2
        if not _need_confirm(args, f"确认把 {args.name} 的 metric 改为 {args.value}?"):
            print("已取消", file=sys.stderr)
            return 1
        iface.set_metric(args.name, args.value, dry_run=args.dry_run)
    else:  # primary
        if not _need_confirm(args, f"确认把 {args.name} 设为优先网卡（调整 metric）?"):
            print("已取消", file=sys.stderr)
            return 1
        iface.set_primary(args.name, config, dry_run=args.dry_run)
    return 0


def _cmd_rule_add(args) -> int:
    """新增分流规则：**只写配置**，不触碰网络（等 apply 才实施）。"""
    text = " ".join([*( [args.rule] if args.rule else [] ), *args.targets])
    if not text.strip():
        print("错误：请给出至少一个 IP/CIDR 或域名，"
              "例如 `rule add 10.0.0.0/8 '*.github.com'`", file=sys.stderr)
        return 2
    try:
        extra, domains = cidrs_mod.parse_targets(text)
        name = args.name or config_mod.next_rule_name(args.config)
        if getattr(args, "dry_run", False):
            print(f"[dry-run] 将新增规则 {name}："
                  f"IP/网段={extra or []} 域名={domains or []} "
                  f"出口={args.interface or '(未指定，需之后用 e 选或 apply 前补齐)'}")
            return 0
        config_mod.add_rule(args.config, name, extra=extra, domains=domains,
                            interface=args.interface)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    print(f"已写入配置：{name}（IP/网段 {len(extra)}，域名 {len(domains)}）")
    if not args.interface:
        print("提示：未指定出口网卡，该规则在 apply 时会被跳过；"
              "可在 TUI 选中后按 e 选择。")
    print("提示：仅修改配置，尚未生效；执行 apply 后才会作用到网卡。")
    return 0


def _cmd_rule_remove(args) -> int:
    """删除分流规则：**只写配置**，不触碰网络（等 apply 才清理）。"""
    if not args.rule:
        print("错误：请给出要删除的规则名（`rule remove <规则名>`）", file=sys.stderr)
        return 2
    if getattr(args, "dry_run", False):
        print(f"[dry-run] 将从配置删除规则 {args.rule}（不触碰网络）")
        return 0
    if not config_mod.remove_rule(args.config, args.rule):
        print(f"未找到规则：{args.rule}", file=sys.stderr)
        return 1
    print(f"已从配置删除规则 {args.rule}")
    print("提示：仅修改配置，尚未生效；执行 apply 后其分流产物会被清理。")
    return 0


def cmd_rule(args) -> int:
    # add/remove 只改配置文件，不需要 root（写入失败会给出权限错误）
    if args.action == "add":
        return _cmd_rule_add(args)
    if args.action == "remove":
        return _cmd_rule_remove(args)

    ex.require_root()
    config = _load(args)
    target = None
    for r in config.rules:
        if r.name == args.rule:
            target = r
            break
    if target is None:
        print(f"未知规则名：{args.rule}；可用规则："
              f"{', '.join(r.name for r in config.rules) or '(无)'}",
              file=sys.stderr)
        return 1

    if args.interface:
        target.interface = args.interface

    if args.action == "apply":
        if not _need_confirm(
            args, f"确认重新应用配置（含规则 {target.name}）?"
        ):
            print("已取消", file=sys.stderr)
            return 1
        # 声明式：始终按配置重建全部规则（整表重建），不会误伤其它规则
        routing.apply_rules(config, dry_run=args.dry_run)
    else:  # clear
        if not _need_confirm(args, f"确认撤销规则 {target.name} 的分流?"):
            print("已取消", file=sys.stderr)
            return 1
        if not args.dry_run:
            config_mod.update_rule_interface(args.config, target.name, None)
        target.interface = None
        routing.apply_rules(config, dry_run=args.dry_run)
    return 0


def cmd_apply(args) -> int:
    if not _need_confirm(args, "确认按配置应用全部（metric + 规则）?"):
        print("已取消", file=sys.stderr)
        return 1
    errors = apply_mod.apply(_load(args), dry_run=args.dry_run, force=args.force)
    return 1 if errors else 0


def cmd_revert(args) -> int:
    if not _need_confirm(args, "确认撤销全部改动（恢复默认路由并清理规则）?"):
        print("已取消", file=sys.stderr)
        return 1
    errors = apply_mod.revert(_load(args), dry_run=args.dry_run)
    return 1 if errors else 0


def _validate_unit(unit: str) -> None:
    """校验渲染后的 ExecStart 能被 argparse 接受（避免生成"装得上、跑不起来"的单元）。"""
    line = next((l for l in unit.splitlines() if l.startswith("ExecStart=")), "")
    if not line:
        raise RuntimeError("服务模板缺少 ExecStart")
    argv = shlex.split(line.split("=", 1)[1])
    if "-m" in argv:
        argv = argv[argv.index("-m") + 2:]
    try:
        build_parser().parse_args(argv)
    except SystemExit as exc:
        raise RuntimeError(
            f"systemd 单元命令行无法被 netswitch 解析：{line}\n"
            f"（顶层 --config 必须放在子命令之前）"
        ) from exc


def cmd_install_systemd(args) -> int:
    ex.require_root()
    if not _need_confirm(args, "确认安装并启用 systemd 服务 netswitch.service?"):
        print("已取消", file=sys.stderr)
        return 1
    tmpl_path = REPO_ROOT / "data" / "config" / "netswitch.service"
    if not tmpl_path.exists():
        raise RuntimeError(f"缺少服务模板：{tmpl_path}")
    unit = tmpl_path.read_text(encoding="utf-8").replace("__REPO_ROOT__", str(REPO_ROOT))
    _validate_unit(unit)
    path = Path("/etc/systemd/system/netswitch.service")
    path.write_text(unit, encoding="utf-8")
    ex.run(["systemctl", "daemon-reload"], check=True)
    ex.run(["systemctl", "enable", "netswitch.service"], check=True)
    print(f"已安装并启用 {path}")
    return 0


def _common(sp, *, force: bool = False) -> None:
    sp.add_argument("--dry-run", action="store_true", help="只打印将执行的命令，不执行")
    sp.add_argument("-y", "--yes", action="store_true", help="跳过确认")
    if force:
        sp.add_argument("--force", action="store_true", help="强制（如关闭最后一张网卡）")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="netswitch", description="多物理网卡切换与流量分流工具")
    p.add_argument("--config", default=config_mod.DEFAULT_CONFIG,
                   help="配置文件路径（默认 data/config/config.json）")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="显示状态")

    d = sub.add_parser("detect", help="重新探测网络并生成配置")
    d.add_argument("--write", action="store_true", help="写回配置（仅更新 interfaces）")
    d.add_argument("--dry-run", action="store_true")

    sub.add_parser("seed-defaults", help="rules 为空时写入缺省规则")

    i = sub.add_parser("iface", help="网卡开关")
    i.add_argument("action", choices=["up", "down"])
    i.add_argument("name")
    _common(i, force=True)

    m = sub.add_parser("metric", help="调整 metric")
    m.add_argument("action", choices=["set", "primary"])
    m.add_argument("name")
    m.add_argument("value", nargs="?", type=int, help="metric set 时的值")
    _common(m)

    r = sub.add_parser("rule", help="分流规则")
    r.add_argument("action", choices=["apply", "clear", "add", "remove"],
                   help="apply/clear=应用或撤销（立即生效）；add/remove=只改配置")
    r.add_argument("rule", nargs="?",
                   help="apply/clear/remove：规则名；add：可放第一个目标（可选）")
    r.add_argument("targets", nargs="*",
                   help="add：IP/CIDR 或域名（支持 *.example.com），可多个")
    r.add_argument("--interface", help="出口网卡（apply 时为一次性覆盖；add 时写入配置）")
    r.add_argument("--name", help="add：规则名（默认 custom-N）")
    _common(r)

    a = sub.add_parser("apply", help="按配置应用全部")
    _common(a, force=True)

    rv = sub.add_parser("revert", help="撤销全部改动")
    _common(rv)

    s = sub.add_parser("install-systemd", help="生成并启用 systemd 服务")
    s.add_argument("-y", "--yes", action="store_true", help="跳过确认")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    log.setup()
    log.get_logger().info("cli: %s",
                          " ".join(argv if argv is not None else sys.argv[1:]))
    handlers = {
        "status": cmd_status,
        "detect": cmd_detect,
        "seed-defaults": cmd_seed_defaults,
        "iface": cmd_iface,
        "metric": cmd_metric,
        "rule": cmd_rule,
        "apply": cmd_apply,
        "revert": cmd_revert,
        "install-systemd": cmd_install_systemd,
    }
    try:
        return handlers[args.command](args)
    except KeyboardInterrupt:
        print("已中断", file=sys.stderr)
        return 130
    except (ex.ExecError, PermissionError, RuntimeError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        # 兜底：任何未预期异常都给一句人话 + 完整 traceback 进日志，不甩给用户
        log.get_logger().exception("cli: 未预期的错误")
        print(f"错误：{type(exc).__name__}: {exc}（详见 logs/netswitch.log）",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
