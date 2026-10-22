from dataclasses import replace

from luxstatus.config import Config
from luxstatus.light import colour_for, fade_report, static_report, strobe_report
from luxstatus.state import Resolution, Status

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
