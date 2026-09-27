"""TUI 纯逻辑单测（不启动 curses 界面）。"""
from netswitch import tui


def test_norm_key_uppercase_to_lower():
    assert tui.norm_key(ord("Q")) == ord("q")
    assert tui.norm_key(ord("A")) == ord("a")
    assert tui.norm_key(ord("Z")) == ord("z")


def test_norm_key_lowercase_unchanged():
    assert tui.norm_key(ord("q")) == ord("q")


def test_norm_key_digits_and_ctrl():
    assert tui.norm_key(ord("1")) == ord("1")
    assert tui.norm_key(3) == 3      # Ctrl+C
    assert tui.norm_key(27) == 27    # Esc


def test_norm_key_special_codes():
    assert tui.norm_key(258) == 258  # curses.KEY_DOWN 等特殊键码


def test_move_index():
    assert tui.move_index(-1, 3, 1) == 0
    assert tui.move_index(-1, 3, -1) == 2
    assert tui.move_index(0, 3, 1) == 1
    assert tui.move_index(2, 3, 1) == 2      # 底部边界
    assert tui.move_index(0, 3, -1) == 0     # 顶部边界
    assert tui.move_index(0, 0, 1) == -1     # 无条目


def test_wrap_text_ascii():
    assert tui.wrap_text("abcdef", 3) == ["abc", "def"]


def test_wrap_text_cjk_width():
    # CJK 按 2 列估算：一行 4 列只能放 2 个汉字
    assert tui.wrap_text("汉字汉字", 4) == ["汉字", "汉字"]


def test_wrap_text_keeps_explicit_lines():
    assert tui.wrap_text("a\nb", 10) == ["a", "b"]


def test_wrap_text_empty():
    assert tui.wrap_text("", 10) == [""]


def test_state_label():
    assert tui.STATE_LABEL["connected"] == "已连接"
