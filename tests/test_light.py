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
    light.show(Resolution(Status.BUSY))
    old = opener.opened[0]
    old.fail = True  # unplugged and replugged: the old handle is dead
    light.tick()
    assert old.closed
    assert len(opener.opened) == 2
    assert opener.opened[1].writes == [static_report(CFG.colours.busy)]
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


class FakeScheduler:
    def __init__(self):
        self.pending = []

    def __call__(self, delay, fn):
        self.pending.append((delay, fn))

    def fire(self):
        pending, self.pending = self.pending, []
        for _, fn in pending:
            fn()


def flash_light(cfg=CFG):
    opener, sched = FakeOpener(), FakeScheduler()
    return HidLight(cfg, opener, sched), opener, sched


def test_call_start_strobes_then_restores_solid():
    light, opener, sched = flash_light()
    call, fx = CFG.colours.call, CFG.effects
    light.show(Resolution(Status.CALL))
    dev = opener.opened[0]
    assert dev.writes == [strobe_report(call, fx.flash_speed, fx.flash_repeat)]
    assert [d for d, _ in sched.pending] == [CFG.effects.flash_seconds]
    light.tick()  # a tick during the flash must not cut it short
    assert len(dev.writes) == 1
    sched.fire()
    assert dev.writes[-1] == static_report(CFG.colours.call)
    light.tick()
    assert dev.writes[-1] == static_report(CFG.colours.call)


def test_no_flash_when_already_in_a_call_or_disabled():
    light, opener, sched = flash_light()
    light.show(Resolution(Status.CALL))
    sched.fire()
    light.show(Resolution(Status.CALL, camera=True))  # camera toggled mid-call
    assert opener.opened[0].writes[-1] == static_report(CFG.colours.call)
    assert sched.pending == []

    off = replace(CFG, effects=replace(CFG.effects, call_flash=False))
    light, opener, sched = flash_light(off)
    light.show(Resolution(Status.CALL))
    assert opener.opened[0].writes == [static_report(CFG.colours.call)]


def test_status_change_during_flash_wins_over_stale_restore():
    light, opener, sched = flash_light()
    light.show(Resolution(Status.CALL))
    light.show(Resolution(Status.OFF))  # e.g. screen locked mid-flash
    sched.fire()
    assert opener.opened[0].writes[-1] == static_report((0, 0, 0))
    light.tick()
    assert opener.opened[0].writes[-1] == static_report((0, 0, 0))


def test_flash_restore_without_device_does_not_raise():
    light, opener, sched = flash_light()
    light.show(Resolution(Status.CALL))
    opener.opened[0].fail = True
    opener.present = False
    sched.fire()
    assert not light.connected
