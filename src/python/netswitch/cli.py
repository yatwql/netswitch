"""命令行入口（脚本接口）。破坏性操作需确认，可用 -y/--yes 跳过。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import apply as apply_mod
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


def cmd_rule(args) -> int:
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
        if not _need_confirm(args, f"确认应用规则 {target.name}?"):
            print("已取消", file=sys.stderr)
            return 1
        routing.apply_rules(config, only={target.name}, dry_run=args.dry_run)
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


def cmd_install_systemd(args) -> int:
    ex.require_root()
    if not _need_confirm(args, "确认安装并启用 systemd 服务 netswitch.service?"):
        print("已取消", file=sys.stderr)
        return 1
    tmpl_path = REPO_ROOT / "data" / "config" / "netswitch.service"
    if not tmpl_path.exists():
        raise RuntimeError(f"缺少服务模板：{tmpl_path}")
    unit = tmpl_path.read_text(encoding="utf-8").replace("__REPO_ROOT__", str(REPO_ROOT))
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
    r.add_argument("action", choices=["apply", "clear"])
    r.add_argument("rule", help="规则名（配置中 rules[].name）")
    r.add_argument("--interface", help="一次性运行时覆盖出口网卡")
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
        "iface": cmd_iface,
        "metric": cmd_metric,
        "rule": cmd_rule,
        "apply": cmd_apply,
        "revert": cmd_revert,
        "install-systemd": cmd_install_systemd,
    }
    try:
        return handlers[args.command](args)
    except (ex.ExecError, PermissionError, RuntimeError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
