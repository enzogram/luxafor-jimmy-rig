from dataclasses import replace

import pytest

from luxstatus.config import Config
from luxstatus.light import (
    HidLight,
    colour_for,
    fade_report,
    parse_target,
    static_report,
    strobe_report,
)
from luxstatus.state import Resolution, Status
from tests.fakes import FakeOpener

CFG = Config()


def test_reports_are_nine_bytes_with_report_id():
    assert static_report((1, 2, 3)) == bytes([0, 1, 0xFF, 1, 2, 3, 0, 0, 0])
    assert fade_report((1, 2, 3), 20) == bytes([0, 2, 0xFF, 1, 2, 3, 20, 0, 0])
    assert strobe_report((1, 2, 3), 10, 3) == bytes([0, 3, 0xFF, 1, 2, 3, 10, 0, 3])


def test_colours():
    assert colour_for(Resolution(Status.OFF), CFG) == (0, 0, 0)
    assert colour_for(Resolution(Status.AVAILABLE), CFG) == CFG.colours.available
    assert colour_for(Resolution(Status.DND), CFG) == CFG.colours.dnd
    for status in Status:
        colour_for(Resolution(status), CFG)  # every status maps to something


def test_camera_colour_only_when_distinct():
    cfg = replace(CFG, colours=replace(CFG.colours, call_camera=(0, 0, 255)))
    cam = Resolution(Status.CALL, camera=True)
    assert colour_for(cam, cfg) == cfg.colours.call
    distinct = replace(cfg, devices=replace(cfg.devices, camera_distinct=True))
    assert colour_for(cam, distinct) == (0, 0, 255)
    assert colour_for(Resolution(Status.CALL), distinct) == cfg.colours.call


def test_show_writes_static_colour():
    opener = FakeOpener()
    light = HidLight(CFG, opener)
    light.show(Resolution(Status.BUSY))
    assert opener.opened[0].writes == [static_report(CFG.colours.busy)]


def test_tick_rewrites_current_colour_and_does_nothing_before_first_show():
    opener = FakeOpener()
    light = HidLight(CFG, opener)
    light.tick()
    assert opener.opened == []
    light.show(Resolution(Status.DND))
    light.tick()
    assert opener.opened[0].writes == [static_report(CFG.colours.dnd)] * 2


def test_replug_recovers_on_next_write_with_fresh_handle():
    opener = FakeOpener()
    light = HidLight(CFG, opener)
    light.show(Resolution(Status.CALL))
    old = opener.opened[0]
    old.fail = True  # unplugged and replugged: the old handle is dead
    light.tick()
    assert old.closed
    assert len(opener.opened) == 2
    assert opener.opened[1].writes == [static_report(CFG.colours.call)]
    assert light.connected


def test_missing_flag_never_raises_and_connects_later():
    opener = FakeOpener()
    opener.present = False
    light = HidLight(CFG, opener)
    assert not light.set_rgb((1, 2, 3))
    light.tick()
    assert not light.connected
    opener.present = True
    light.tick()
    assert opener.opened[0].writes == [static_report((1, 2, 3))]


def test_unplugged_mid_session_keeps_retrying_quietly():
    opener = FakeOpener()
    light = HidLight(CFG, opener)
    light.show(Resolution(Status.BUSY))
    opener.opened[0].fail = True
    opener.present = False
    light.tick()
    light.tick()
    assert not light.connected
    opener.present = True
    light.tick()
    assert opener.opened[-1].writes == [static_report(CFG.colours.busy)]


def test_write_exception_is_handled():
    opener = FakeOpener()
    light = HidLight(CFG, opener)
    light.show(Resolution(Status.BUSY))

    def boom(data):
        raise OSError("device gone")

    opener.opened[0].write = boom
    light.tick()
    assert len(opener.opened) == 2


def test_parse_target():
    assert parse_target("busy", CFG) == CFG.colours.busy
    assert parse_target("OFF", CFG) == (0, 0, 0)
    assert parse_target("0, 0,255", CFG) == (0, 0, 255)
    for bad in ["purple", "1,2", "1,2,256", "a,b,c"]:
        with pytest.raises(ValueError):
            parse_target(bad, CFG)
