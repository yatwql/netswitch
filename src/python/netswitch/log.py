"""运行日志：写入日志目录（文件 + 按大小轮转）。

- 首选日志文件：`<仓库>/logs/netswitch.log`（默认 INFO；`NETSWITCH_LOG_LEVEL=DEBUG` 看详细命令）
- 目录选择顺序：`setup(log_dir)` 参数 → `NETSWITCH_LOG_DIR` → `<仓库>/logs`
  → `$XDG_STATE_HOME/netswitch/logs` → 系统临时目录。
  仓库内 `logs/` 若被 root 创建（install/apply 用 sudo 跑过），普通用户不可写，
  此时自动回退（并在 stderr 提示一次），而不是静默丢失日志。
"""
from __future__ import annotations

import logging
import os
import socket
import sys
import tempfile
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import List, Optional

from . import version

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LOG_DIR = Path(os.environ.get("NETSWITCH_LOG_DIR") or (REPO_ROOT / "logs"))
_LOG_NAME = "netswitch"
_configured = False
_active_log_file: Optional[str] = None


def _default_level() -> int:
    return logging.DEBUG if os.environ.get("NETSWITCH_LOG_LEVEL", "").upper() == "DEBUG" \
        else logging.INFO


def _fallback_dirs() -> List[Path]:
    xdg = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state")
    return [
        Path(xdg) / "netswitch" / "logs",
        Path(tempfile.gettempdir()) / f"netswitch-{os.getuid()}" / "logs",
    ]


def _candidate_dirs(log_dir: Optional[str]) -> List[Path]:
    if log_dir:
        return [Path(log_dir)]
    if os.environ.get("NETSWITCH_LOG_DIR"):
        return [DEFAULT_LOG_DIR]
    return [DEFAULT_LOG_DIR, *_fallback_dirs()]


def setup(log_dir: Optional[str] = None, level: Optional[int] = None) -> logging.Logger:
    """初始化日志（幂等）。目录不可写时自动回退，不影响主流程。"""
    global _configured, _active_log_file
    logger = logging.getLogger(_LOG_NAME)
    if _configured:
        return logger

    candidates = _candidate_dirs(log_dir)
    for idx, d in enumerate(candidates):
        try:
            d.mkdir(parents=True, exist_ok=True)
            handler: logging.Handler = RotatingFileHandler(
                d / "netswitch.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
            )
        except OSError:
            continue
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
        logger.addHandler(handler)
        _active_log_file = str(d / "netswitch.log")
        if idx > 0:
            print(f"[warn] 日志目录 {candidates[0]} 不可写，已改用 {d}",
                  file=sys.stderr)
        break

    if _active_log_file is None:
        logger.addHandler(logging.NullHandler())

    logger.setLevel(level if level is not None else _default_level())
    logger.propagate = False
    _configured = True
    logger.info("netswitch %s · host=%s · 用户=%s · 程序更新 %s",
                version.__version__, socket.gethostname(),
                version.login_name(), version.program_mtime_str())
    return logger


def current_log_file() -> Optional[str]:
    """当前日志文件路径（未配置或无可用目录时为 None）。"""
    return _active_log_file


def get_logger() -> logging.Logger:
    return setup()


def reset() -> None:
    """测试用：清空已配置的 handler。"""
    global _configured, _active_log_file
    logger = logging.getLogger(_LOG_NAME)
    for h in list(logger.handlers):
        logger.removeHandler(h)
        try:
            h.close()
        except Exception:  # noqa: BLE001
            pass
    _configured = False
    _active_log_file = None
