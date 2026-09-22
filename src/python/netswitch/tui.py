"""curses TUI（FR5/FR6）。零第三方依赖，纯键盘操作。

导航：↑/↓ 移动选中项；数字 1..N 选网卡；g 切换规则。
操作：o 开关网卡、m 设为主网卡、t 改 metric；e 选规则生效网卡、p 应用、c 撤销。
全局：d 探测、a 应用全部、r 撤销全部、f/F5 刷新、q/Ctrl+C 退出。
"""
from __future__ import annotations

import curses
import time
from typing import Dict, List, Optional, Tuple

from . import apply as apply_mod
from . import config as config_mod
from . import detect, iface, log, routing
from .model import Config, RuleCfg

STATE_LABEL = {"connected": "已连接", "no-carrier": "未插网线", "down": "未有连接"}


def norm_key(key: int) -> int:
    """字母键大小写不敏感：统一转小写；其它按键（含特殊键码）原样返回。"""
    if 0 < key < 256 and chr(key).isalpha():
        return ord(chr(key).lower())
    return key


def move_index(cur: int, length: int, delta: int) -> int:
    """在 [0, length) 内移动索引；越界停在边界；cur<0 时从端点开始。"""
    if length <= 0:
        return -1
    if cur < 0:
        return 0 if delta >= 0 else length - 1
    return max(0, min(length - 1, cur + delta))


class _App:
    def __init__(self, stdscr, config_path: Optional[str]):
        self.stdscr = stdscr
        self.config_path = config_path or config_mod.DEFAULT_CONFIG
        self.config: Config = config_mod.load(self.config_path)
        self.ifaces = []
        self.primary: Optional[str] = None
        self.applied: Dict[str, bool] = {}
        self.sel: Optional[Tuple[str, str]] = None   # ("iface"|"rule", name)
        self.msg = ""
        self.last_refresh = ""
        self._refresh_ms = 1500          # 自动刷新间隔（毫秒）
        curses.curs_set(0)
        self.has_color = False
        try:
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_GREEN, -1)    # 主网卡
            curses.init_pair(2, curses.COLOR_BLUE, -1)     # 其他网卡
            curses.init_pair(3, curses.COLOR_RED, -1)      # 被禁止 / 不可用
            self.has_color = True
        except Exception:  # noqa: BLE001
            pass

    # ---------- 工具 ----------
    def _cp(self, n: int) -> int:
        return curses.color_pair(n) if self.has_color else 0

    def _add(self, y: int, x: int, text: str, attr: int = 0) -> None:
        h, w = self.stdscr.getmaxyx()
        if y < 0 or y >= h:
            return
        text = text[: max(0, w - x)]
        try:
            self.stdscr.addstr(y, x, text, attr)
        except curses.error:
            pass

    def _prompt(self, msg: str) -> Optional[str]:
        """底部输入行（逐字符读取，便于拦截 Ctrl+C / Esc）。

        返回输入串；按 Esc 取消返回 None；按 Ctrl+C 抛 KeyboardInterrupt（退出）。
        """
        h, w = self.stdscr.getmaxyx()
        start = len(msg) + 1
        self.stdscr.timeout(-1)          # 输入期间阻塞
        curses.curs_set(1)
        buf = ""
        try:
            while True:
                self._add(h - 1, 0, " " * (w - 1))
                self._add(h - 1, 0, msg + " " + buf, curses.A_REVERSE)
                try:
                    self.stdscr.move(h - 1, min(w - 2, start + len(buf)))
                except curses.error:
                    pass
                self.stdscr.refresh()
                ch = self.stdscr.getch()
                if ch in (10, 13, curses.KEY_ENTER):
                    return buf.strip()
                if ch == 3:              # Ctrl+C -> 退出
                    raise KeyboardInterrupt
                if ch == 27:             # Esc -> 取消
                    return None
                if ch in (curses.KEY_BACKSPACE, 127, 8):
                    buf = buf[:-1]
                    continue
                if 32 <= ch < 127 and start + len(buf) < w - 1:
                    buf += chr(ch)
        finally:
            curses.curs_set(0)
            self.stdscr.timeout(self._refresh_ms)

    def _confirm(self, msg: str) -> bool:
        ans = self._prompt(msg + " [y/N]")
        return ans is not None and ans.lower() in ("y", "yes")

    def _guard(self, fn) -> None:
        try:
            msg = fn()
            self.msg = msg if isinstance(msg, str) and msg else "完成"
        except Exception as exc:  # noqa: BLE001
            self.msg = f"错误: {exc}"

    # ---------- 选中 / 导航 ----------
    def _sel_iface(self) -> Optional[str]:
        return self.sel[1] if self.sel and self.sel[0] == "iface" else None

    def _sel_rule(self) -> Optional[str]:
        return self.sel[1] if self.sel and self.sel[0] == "rule" else None

    def _items(self) -> List[Tuple[str, str]]:
        return ([("iface", i.name) for i in self.ifaces]
                + [("rule", r.name) for r in self.config.rules])

    def _cursor_index(self) -> int:
        items = self._items()
        return items.index(self.sel) if self.sel in items else -1

    def move_cursor(self, delta: int) -> None:
        items = self._items()
        idx = move_index(self._cursor_index(), len(items), delta)
        if 0 <= idx < len(items):
            self.sel = items[idx]

    def select(self, n: int) -> None:
        """数字键：仅选网卡（不进入规则命名空间）。"""
        if 0 <= n < len(self.ifaces):
            self.sel = ("iface", self.ifaces[n].name)

    def cycle_rule(self) -> None:
        names = [r.name for r in self.config.rules]
        if not names:
            self.msg = "无规则"
            return
        cur = self._sel_rule()
        if cur in names:
            self.sel = ("rule", names[(names.index(cur) + 1) % len(names)])
        else:
            self.sel = ("rule", names[0])

    def _rule(self) -> Optional[RuleCfg]:
        name = self._sel_rule()
        if name:
            return next((r for r in self.config.rules if r.name == name), None)
        return self.config.rules[0] if self.config.rules else None

    def do_default(self) -> None:
        """对当前选中项执行默认动作：网卡→设为主网卡；规则→仅提示（用 e/p/c）。"""
        if not self.sel:
            self.msg = "无选中项"
            return
        if self.sel[0] == "iface":
            self.do_primary()
        else:
            self.msg = "规则操作：e 设置生效网卡 · p 应用 · c 撤销"

    # ---------- 数据 ----------
    def refresh(self) -> None:
        self.config = config_mod.load(self.config_path)
        self.ifaces = detect.detect_interfaces()
        self.primary = detect.primary_interface(self.ifaces)
        items = self._items()
        if self.sel not in items:
            self.sel = items[0] if items else None
        self.applied = {}
        for r in self.config.rules:
            try:
                self.applied[r.name] = routing.rule_applied(r)
            except Exception:  # noqa: BLE001
                self.applied[r.name] = False
        self.last_refresh = time.strftime("%H:%M:%S")

    # ---------- 绘制 ----------
    def draw(self) -> None:
        self.stdscr.erase()
        h, _ = self.stdscr.getmaxyx()
        self._add(0, 0, "netswitch · 网卡切换控制台（q/Ctrl+C 退出，f/F5 刷新）",
                  curses.A_BOLD)

        y = 2
        self._add(y, 0, "── 物理网卡（↑/↓ 导航，数字 1..N 选中；o 开关 / m 主网卡 / t metric）──")
        y += 1
        if not self.ifaces:
            self._add(y, 0, "  （未探测到物理网卡）")
            y += 1
        for idx, i in enumerate(self.ifaces, 1):
            kind = "无线" if i.type == "wireless" else "有线"
            if not i.admin_up:
                state = "已关闭"
            elif i.state == "connected":
                state = "已连接"
            elif i.state == "no-carrier":
                state = "未插网线"
            else:
                state = "未有连接"
            metric = i.metric if i.metric is not None else "-"
            tag = "[主网卡]" if i.name == self.primary else ""
            line = (f"  [{idx}] {i.name:<12} {kind}  {state}  "
                    f"IP={i.ip or '-'}  metric={metric}  网关={i.gateway or '-'}  {tag}")
            # 颜色：主网卡绿 / 其他网卡蓝 / 被禁止(admin down)或不可用红
            if not i.admin_up or i.state == "no-carrier":
                attr = self._cp(3)
            elif i.name == self.primary:
                attr = self._cp(1)
            else:
                attr = self._cp(2)
            if self.sel == ("iface", i.name):
                attr |= curses.A_REVERSE
            self._add(y, 0, line, attr)
            y += 1

        y += 1
        self._add(y, 0, "── 分流规则（g 切换 / e 选生效网卡 / p 应用 / c 撤销）──")
        y += 1
        if not self.config.rules:
            self._add(y, 0, "  （无规则；在 data/config/config.json 的 rules 中添加）")
            y += 1
        for r in self.config.rules:
            if r.interface:
                eff = f"生效网卡: {r.interface}"
            else:
                eff = "生效网卡: 未分流"
            applied = "已应用" if self.applied.get(r.name) else "未应用"
            line = f"  {r.name:<12} {eff}  [{applied}]"
            attr = curses.A_REVERSE if self.sel == ("rule", r.name) else 0
            self._add(y, 0, line, attr)
            y += 1

        y += 1
        self._add(y, 0, "── 导航 ── ↑/↓ 移动 · 1..N 选网卡 · g 切规则 · Enter 执行(网卡=主网卡)")
        y += 1
        self._add(y, 0, "── 网卡/规则 ── o 开关 · m 主网卡 · t metric · e 出口 · p 应用 · c 撤销")
        y += 1
        self._add(y, 0, "── 全局 ── d 探测 · a 应用全部 · r 撤销 · f/F5 刷新 · Esc 返回 · q/Ctrl+C 退出")
        y += 1
        if y < h:
            self._add(y, 0,
                      f"状态: {self.msg}    [最后刷新 {self.last_refresh}]",
                      curses.A_BOLD)

        self.stdscr.noutrefresh()
        curses.doupdate()

    def _show_box(self, title: str, lines: List[str]) -> None:
        self.stdscr.erase()
        self._add(0, 0, title, curses.A_BOLD)
        for i, ln in enumerate(lines, 1):
            self._add(i, 0, "  " + ln)
        self.stdscr.refresh()

    # ---------- 网卡操作 ----------
    def do_toggle(self) -> None:
        name = self._sel_iface()
        if not name:
            self.msg = "请先选中网卡（↑/↓ 或数字键）"
            return
        cur = next((i for i in self.ifaces if i.name == name), None)
        is_up = bool(cur and cur.admin_up)
        action = "关闭" if is_up else "开启"
        if not self._confirm(f"确认{action}网卡 {name}?"):
            self.msg = "已取消"
            return
        self._guard(lambda: iface.set_link(name, not is_up, force=False))
        self.refresh()

    def do_primary(self) -> None:
        name = self._sel_iface()
        if not name:
            self.msg = "请先选中网卡（↑/↓ 或数字键）"
            return
        if not self._confirm(f"确认把 {name} 设为优先网卡（调整 metric）?"):
            self.msg = "已取消"
            return
        self._guard(lambda: iface.set_primary(name, self.config))
        self.refresh()

    def do_metric(self) -> None:
        name = self._sel_iface()
        if not name:
            self.msg = "请先选中网卡（↑/↓ 或数字键）"
            return
        v = self._prompt(f"设置 {name} 的 metric（越小越优先）:")
        if v is None or v == "":
            self.msg = "已取消"
            return
        try:
            metric = int(v)
        except ValueError:
            self.msg = "metric 必须是整数"
            return
        if not self._confirm(f"确认把 {name} 的 metric 改为 {metric}?"):
            self.msg = "已取消"
            return
        self._guard(lambda: iface.set_metric(name, metric))
        self.refresh()

    # ---------- 规则操作 ----------
    def do_egress(self) -> None:
        rule = self._rule()
        if not rule:
            self.msg = "无规则可操作（请在 config.json 的 rules 中添加）"
            return
        if not self.ifaces:
            self.msg = "未探测到物理网卡"
            return
        lines = [f"为规则「{rule.name}」选择生效网卡（输入序号）："]
        for i, nic in enumerate(self.ifaces, 1):
            kind = "无线" if nic.type == "wireless" else "有线"
            lines.append(f"{i}) {nic.name}  {kind}  {STATE_LABEL.get(nic.state, nic.state)}  "
                         f"IP={nic.ip or '-'}")
        lines.append("0) 不分流（撤销该规则）")
        self._show_box("选择生效网卡", lines)
        v = self._prompt("序号（0=不分流，回车=取消）:")
        if v is None or v == "":
            self.msg = "已取消"
            return
        try:
            idx = int(v)
        except ValueError:
            self.msg = "请输入序号"
            return
        if idx == 0:
            target = None
        elif 1 <= idx <= len(self.ifaces):
            target = self.ifaces[idx - 1].name
        else:
            self.msg = "序号超出范围"
            return

        if not self._confirm(
            f"确认将规则 {rule.name} 的生效网卡设为 {target or '不分流'} 并写入配置?"
        ):
            self.msg = "已取消"
            return
        rule.interface = target

        def do():
            config_mod.update_rule_interface(self.config_path, rule.name, target)
            routing.apply_rules(self.config)   # 整表重建，保留其它规则
            return f"规则 {rule.name} 生效网卡: {target or '未分流'}（已写入配置）"

        self._guard(do)
        self.refresh()

    def do_rule_apply(self) -> None:
        rule = self._rule()
        if not rule:
            self.msg = "无规则可操作"
            return
        if not self._confirm(f"确认应用规则 {rule.name}?"):
            self.msg = "已取消"
            return
        self._guard(lambda: routing.apply_rules(self.config))
        self.refresh()

    def do_rule_clear(self) -> None:
        rule = self._rule()
        if not rule:
            self.msg = "无规则可操作"
            return
        if not self._confirm(f"确认撤销规则 {rule.name} 的分流（写入配置）?"):
            self.msg = "已取消"
            return
        name = rule.name
        rule.interface = None

        def do():
            config_mod.update_rule_interface(self.config_path, name, None)
            routing.apply_rules(self.config)   # 重建；已清空 interface 的规则自动不再生效
            return f"已撤销规则 {name} 的分流（已写入配置）"

        self._guard(do)
        self.refresh()

    # ---------- 全局 ----------
    def do_detect(self) -> None:
        self.ifaces = detect.detect_interfaces()
        lines = [
            f"{i.name:<12} {'无线' if i.type == 'wireless' else '有线'}  "
            f"{STATE_LABEL.get(i.state, i.state)}  IP={i.ip or '-'}  "
            f"metric={i.metric if i.metric is not None else '-'}  网关={i.gateway or '-'}"
            for i in self.ifaces
        ] or ["（未探测到物理网卡）"]
        self._show_box("重新探测网络", lines)
        if self._confirm("写入配置?（仅更新 interfaces，保留 rules）"):
            frag = detect.interfaces_config_fragment()
            config_mod.update_interfaces(self.config_path, frag)
            self.msg = f"已写入 {self.config_path}"
        self.refresh()

    def do_apply_all(self) -> None:
        if not self._confirm("确认按配置应用全部（metric + 规则）?"):
            self.msg = "已取消"
            return
        self._guard(lambda: apply_mod.apply(self.config))
        self.refresh()

    def do_revert(self) -> None:
        if not self._confirm("确认撤销全部改动（恢复默认路由并清理规则）?"):
            self.msg = "已取消"
            return
        self._guard(lambda: apply_mod.revert(self.config))
        self.refresh()

    def _action(self, key: int) -> None:
        if key in (ord("f"), curses.KEY_F5):
            self.refresh()
            self.msg = "已刷新"
        elif key == 27:                      # Esc：返回主页面并刷新
            self.msg = ""
            self.refresh()
        elif key == curses.KEY_UP:
            self.move_cursor(-1)
        elif key == curses.KEY_DOWN:
            self.move_cursor(1)
        elif key in (10, 13, curses.KEY_ENTER):
            self.do_default()
        elif key == ord("d"):
            self.do_detect()
        elif key == ord("a"):
            self.do_apply_all()
        elif key == ord("r"):
            self.do_revert()
        elif key == ord("o"):
            self.do_toggle()
        elif key == ord("m"):
            self.do_primary()
        elif key == ord("t"):
            self.do_metric()
        elif key == ord("g"):
            self.cycle_rule()
        elif key == ord("e"):
            self.do_egress()
        elif key == ord("p"):
            self.do_rule_apply()
        elif key == ord("c"):
            self.do_rule_clear()
        elif ord("1") <= key <= ord("9"):
            self.select(key - ord("1"))
        elif 32 <= key < 127:
            self.msg = f"未绑定按键: {chr(key)}"
        # 其它控制键静默忽略

    def run(self) -> None:
        self.refresh()
        self.stdscr.keypad(True)                 # 使 ↑/↓、F5 等特殊键可用
        self.stdscr.timeout(self._refresh_ms)   # 定期自动刷新
        try:
            while True:
                try:
                    self.draw()
                    key = self.stdscr.getch()
                except KeyboardInterrupt:
                    break
                if key == -1:                    # 超时：自动刷新并重绘
                    try:
                        self.refresh()
                    except Exception as exc:     # noqa: BLE001
                        self.msg = f"错误: {exc}"
                    continue
                key = norm_key(key)
                if key in (ord("q"), 3):         # q / Ctrl+C 退出
                    break
                try:
                    self._action(key)
                except KeyboardInterrupt:
                    break
                except Exception as exc:         # noqa: BLE001
                    self.msg = f"错误: {exc}"
        finally:
            self.stdscr.timeout(-1)


def run(config_path: Optional[str] = None) -> None:
    log.setup()
    log.get_logger().info("tui: 启动")
    curses.wrapper(lambda stdscr: _App(stdscr, config_path).run())


if __name__ == "__main__":
    run()
