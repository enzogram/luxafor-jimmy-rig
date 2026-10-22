from datetime import time

import pytest

from luxstatus import config
from luxstatus.config import Config, ConfigError, from_dict, load


def test_missing_file_gives_defaults(tmp_path):
    assert load(tmp_path / "nope.toml") == Config()
    assert load(None) == Config()


def test_repo_config_matches_defaults():
    from pathlib import Path

    assert load(Path(__file__).parent.parent / "config.toml") == Config()


def test_partial_sections_keep_other_defaults(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(
        "[colours]\nbusy = [1, 2, 3]\n[devices]\npoll_seconds = 3\ninclude = []\n"
        '[work_hours]\ndays = ["Mon", "sat"]\nstart = 08:00:00\nend = "18:30"\n'
    )
    cfg = load(p)
    assert cfg.colours.busy == (1, 2, 3)
    assert cfg.colours.available == (0, 255, 0)
    assert cfg.devices.poll_seconds == 3
    assert cfg.devices.include == ()
    assert cfg.devices.on_debounce_reads == 2
    assert cfg.work_hours.days == frozenset({0, 5})
    assert cfg.work_hours.start == time(8, 0)
    assert cfg.work_hours.end == time(18, 30)


@pytest.mark.parametrize(
    "data",
    [
        {"bogus": {}},
        {"outlook": {"bogus": 1}},
        {"colours": {"busy": [256, 0, 0]}},
        {"colours": {"busy": [1, 2]}},
        {"work_hours": {"days": ["Funday"]}},
        {"work_hours": {"start": "late"}},
        {"work_hours": {"start": "18:00", "end": "09:00"}},
    ],
)
def test_invalid_config_raises(data):
    with pytest.raises(ConfigError):
        from_dict(data)


def test_bad_toml_raises(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("[colours\n")
    with pytest.raises(config.ConfigError):
        load(p)
