"""运行日志：写入 logs/ 目录（文件 + 按大小轮转）。

- 日志文件：`logs/netswitch.log`（默认 INFO；`NETSWITCH_LOG_LEVEL=DEBUG` 可看详细命令）
- 日志目录：仓库根 `logs/`（可用环境变量 `NETSWITCH_LOG_DIR` 覆盖）
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LOG_DIR = Path(os.environ.get("NETSWITCH_LOG_DIR") or (REPO_ROOT / "logs"))
_LOG_NAME = "netswitch"
_configured = False


def _default_level() -> int:
    return logging.DEBUG if os.environ.get("NETSWITCH_LOG_LEVEL", "").upper() == "DEBUG" \
        else logging.INFO


def setup(log_dir: Optional[str] = None, level: Optional[int] = None) -> logging.Logger:
    """初始化日志（幂等）。无法写日志时静默降级，不影响主流程。"""
    global _configured
    logger = logging.getLogger(_LOG_NAME)
    if _configured:
        return logger

    d = Path(log_dir) if log_dir else DEFAULT_LOG_DIR
    try:
        d.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = RotatingFileHandler(
            d / "netswitch.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
        logger.addHandler(handler)
    except OSError:
        logger.addHandler(logging.NullHandler())

    logger.setLevel(level if level is not None else _default_level())
    logger.propagate = False
    _configured = True
    return logger


def get_logger() -> logging.Logger:
    return setup()


def reset() -> None:
    """测试用：清空已配置的 handler。"""
    global _configured
    logger = logging.getLogger(_LOG_NAME)
    for h in list(logger.handlers):
        logger.removeHandler(h)
        try:
            h.close()
        except Exception:  # noqa: BLE001
            pass
    _configured = False
