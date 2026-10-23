import pytest

from luxstatus.hotkeys import MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, MOD_SHIFT, MOD_WIN, parse_hotkey


def test_parse_config_style():
    assert parse_hotkey("<ctrl>+<alt>+d") == (MOD_NOREPEAT | MOD_CONTROL | MOD_ALT, ord("D"))


def test_parse_plain_style_and_function_keys():
    assert parse_hotkey("Ctrl + Shift + F9") == (MOD_NOREPEAT | MOD_CONTROL | MOD_SHIFT, 0x78)
    assert parse_hotkey("win+1") == (MOD_NOREPEAT | MOD_WIN, ord("1"))


@pytest.mark.parametrize("bad", ["", "d", "<ctrl>+<hyper>+d", "ctrl+space", "ctrl+f25", "ctrl+é"])
def test_parse_rejects(bad):
    with pytest.raises(ValueError):
        parse_hotkey(bad)
