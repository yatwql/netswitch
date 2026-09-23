"""程序版本与构建信息（版本号唯一来源）。"""
from __future__ import annotations

import functools
import os
import pwd
import re
import subprocess
import time
from pathlib import Path

__version__ = "0.2-dev"

_PKG_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PKG_DIR.parents[2]          # src/python/netswitch -> 仓库根
TZ_FMT = "%Y%m%d %H:%M:%S %z"             # yyyyMMdd HH:mm:ss +ZZZZ
_VER_RE = re.compile(r"^(\d+)\.(\d+)(?:-dev)?$")


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


def release_version(version: str = __version__) -> str:
    """正式版本号（去掉 -dev）：0.1-dev -> 0.1。"""
    m = _VER_RE.match(version)
    return f"{m.group(1)}.{m.group(2)}" if m else version


def next_dev_version(version: str = __version__) -> str:
    """发布后的下一个开发版本（minor+1 并加 -dev）：0.1 / 0.1-dev -> 0.2-dev。"""
    m = _VER_RE.match(version)
    if not m:
        return version
    return f"{m.group(1)}.{int(m.group(2)) + 1}-dev"


def _user_name() -> str:
    try:
        return pwd.getpwuid(os.geteuid()).pw_name
    except Exception:  # noqa: BLE001
        return os.environ.get("USER", "?")


def run_name() -> str:
    """当前运行身份用户名。"""
    return _user_name()


def login_name() -> str:
    """发起操作的用户（sudo 时取 SUDO_USER，否则当前用户）。"""
    return os.environ.get("SUDO_USER") or _user_name()


def is_root() -> bool:
    return os.geteuid() == 0


def user_line() -> str:
    """用户/权限一行说明。"""
    if is_root():
        who = login_name()
        return f"运行身份: root（登录用户 {who}）" if who != "root" else "运行身份: root"
    return f"运行身份: {_user_name()}（非 root，写操作需 sudo）"
