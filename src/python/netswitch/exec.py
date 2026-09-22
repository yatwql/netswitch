"""命令执行封装：root 检查、dry-run、日志、错误统一。"""
from __future__ import annotations

import logging
import os
import shlex
import subprocess
from typing import Optional, Sequence

from . import log

_log = log.get_logger()


class ExecError(RuntimeError):
    """子命令执行失败。"""

    def __init__(self, cmd: Sequence[str], returncode: int, stderr: str):
        self.cmd = list(cmd)
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"命令失败(exit={returncode}): {cmd_str(self.cmd)}\n{stderr.strip()}"
        )


def cmd_str(cmd: Sequence[str]) -> str:
    return " ".join(shlex.quote(c) for c in cmd)


def run(
    cmd: Sequence[str],
    *,
    dry_run: bool = False,
    check: bool = True,
    input: Optional[str] = None,
    readonly: bool = False,
) -> subprocess.CompletedProcess:
    """执行命令并记录日志。

    dry_run=True 时只打印/记录不执行；readonly=True 的命令记为 DEBUG（避免轮询刷屏）。
    """
    level = logging.DEBUG if readonly else logging.INFO

    if dry_run:
        print(f"[dry-run] {cmd_str(cmd)}")
        _log.log(level, "DRY-RUN: %s", cmd_str(cmd))
        return subprocess.CompletedProcess(list(cmd), 0, stdout="", stderr="")

    try:
        proc = subprocess.run(list(cmd), capture_output=True, text=True, input=input)
    except FileNotFoundError as exc:
        _log.error("命令不存在: %s (%s)", cmd_str(cmd), exc)
        raise

    if proc.returncode != 0:
        _log.error("FAIL(%d): %s :: %s", proc.returncode, cmd_str(cmd),
                   (proc.stderr or "").strip())
        if check:
            raise ExecError(cmd, proc.returncode, proc.stderr)
    else:
        _log.log(level, "OK: %s", cmd_str(cmd))
    return proc


def require_root() -> None:
    """写操作需 root；非 root 抛出 PermissionError。"""
    if os.geteuid() != 0:
        _log.warning("拒绝：非 root 执行写操作")
        raise PermissionError("需要 root 权限，请用 sudo 运行")
