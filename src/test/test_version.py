from netswitch import version


def test_version_value():
    assert version.__version__ == "0.1-dev"


def test_program_mtime_str_format():
    s = version.program_mtime_str()
    # 形如 "20260922 19:22:45 +0800"；取不到时为 "-"
    assert s == "-" or (len(s) >= 20 and s[8] == " ")


def test_info_line_contains_version():
    assert version.__version__ in version.info_line()
