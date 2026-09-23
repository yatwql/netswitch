import re

from netswitch import version


def test_version_format():
    # 正式版 X.Y 或开发版 X.Y-dev（不硬编码具体版本，避免发布时失败）
    assert re.match(r"^\d+\.\d+(-dev)?$", version.__version__)


def test_program_mtime_str_format():
    s = version.program_mtime_str()
    # 形如 "20260922 19:22:45 +0800"；取不到时为 "-"
    assert s == "-" or (len(s) >= 20 and s[8] == " ")


def test_info_line_contains_version():
    assert version.__version__ in version.info_line()


def test_user_line():
    assert "运行身份" in version.user_line()
    assert isinstance(version.login_name(), str) and version.login_name()


def test_release_and_next_dev():
    assert version.release_version("0.1-dev") == "0.1"
    assert version.release_version("0.1") == "0.1"
    assert version.next_dev_version("0.1-dev") == "0.2-dev"
    assert version.next_dev_version("0.1") == "0.2-dev"
    assert version.next_dev_version("0.10-dev") == "0.11-dev"
