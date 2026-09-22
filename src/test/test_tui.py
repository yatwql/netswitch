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
