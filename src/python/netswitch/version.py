"""程序版本与构建信息（版本号唯一来源）。"""
from __future__ import annotations

import functools
import subprocess
import time
from pathlib import Path

__version__ = "0.1-dev"

_PKG_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PKG_DIR.parents[2]          # src/python/netswitch -> 仓库根
TZ_FMT = "%Y%m%d %H:%M:%S %z"             # yyyyMMdd HH:mm:ss +ZZZZ


@functools.lru_cache(maxsize=1)
def program_mtime() -> float:
    """程序最后修改时间（epoch）：优先 git 提交时间，回退源文件 mtime。"""
    try:
        proc = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "log", "-1", "--format=%ct", "--", "src/python"],
            capture_output=True, text=True, timeout=5,
        )
        val = (proc.stdout or "").strip()
        if proc.returncode == 0 and val.isdigit():
            return float(val)
    except Exception:  # noqa: BLE001
        pass
    newest = 0.0
    for p in _PKG_DIR.rglob("*.py"):
        try:
            newest = max(newest, p.stat().st_mtime)
        except OSError:
            pass
    return newest


def program_mtime_str(fmt: str = TZ_FMT) -> str:
    """程序最后修改时间（本地时区，yyyyMMdd HH:mm:ss +ZZZZ）。"""
    ts = program_mtime()
    return time.strftime(fmt, time.localtime(ts)) if ts else "-"


def info_line() -> str:
    """一行版本信息。"""
    return f"netswitch {__version__} · 程序更新 {program_mtime_str()}"
