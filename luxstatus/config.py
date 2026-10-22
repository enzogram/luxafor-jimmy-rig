"""Load config.toml into typed, frozen dataclasses. Missing keys fall back to defaults."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields, replace
from datetime import time
from pathlib import Path
from typing import Any

RGB = tuple[int, int, int]

DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Colours:
    available: RGB = (0, 255, 0)
    tentative: RGB = (255, 180, 0)
    busy: RGB = (255, 0, 0)
    call: RGB = (255, 0, 0)
    call_camera: RGB = (255, 0, 0)
    dnd: RGB = (255, 0, 255)


@dataclass(frozen=True)
class WorkHours:
    enabled: bool = True
    days: frozenset[int] = frozenset(range(5))  # datetime.weekday(): Mon=0
    start: time = time(7, 30)
    end: time = time(17, 0)


@dataclass(frozen=True)
class OutlookConfig:
    enabled: bool = True
    poll_seconds: float = 30
    lookahead_minutes: int = 240


@dataclass(frozen=True)
class SessionConfig:
    enabled: bool = True
    poll_seconds: float = 2


@dataclass(frozen=True)
class DevicesConfig:
    enabled: bool = True
    poll_seconds: float = 2
    on_debounce_reads: int = 2
    off_debounce_seconds: float = 10
    camera_distinct: bool = False
    include: tuple[str, ...] = ("MSTeams", "zoom", "webex", "chrome", "msedge", "firefox")
    exclude: tuple[str, ...] = ()


@dataclass(frozen=True)
class Hotkeys:
    dnd: str = "<ctrl>+<alt>+d"
    auto: str = "<ctrl>+<alt>+a"


@dataclass(frozen=True)
class ResolverConfig:
    tick_seconds: float = 5
    stale_factor: float = 3


@dataclass(frozen=True)
class Config:
    colours: Colours = field(default_factory=Colours)
    work_hours: WorkHours = field(default_factory=WorkHours)
    outlook: OutlookConfig = field(default_factory=OutlookConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    devices: DevicesConfig = field(default_factory=DevicesConfig)
    hotkeys: Hotkeys = field(default_factory=Hotkeys)
    resolver: ResolverConfig = field(default_factory=ResolverConfig)


def load(path: Path | None) -> Config:
    """Load config from ``path``; a missing or ``None`` path gives the defaults."""
    if path is None or not path.exists():
        return Config()
    with path.open("rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"{path}: {e}") from e
    return from_dict(data)


def from_dict(data: dict[str, Any]) -> Config:
    cfg = Config()
    sections = {f.name for f in fields(Config)}
    for name in data:
        if name not in sections:
            raise ConfigError(f"unknown section [{name}]")
    return replace(
        cfg,
        colours=_section(cfg.colours, data.get("colours", {}), "colours", _colour),
        work_hours=_work_hours(data.get("work_hours", {})),
        outlook=_section(cfg.outlook, data.get("outlook", {}), "outlook"),
        session=_section(cfg.session, data.get("session", {}), "session"),
        devices=_section(cfg.devices, data.get("devices", {}), "devices"),
        hotkeys=_section(cfg.hotkeys, data.get("hotkeys", {}), "hotkeys"),
        resolver=_section(cfg.resolver, data.get("resolver", {}), "resolver"),
    )


def _section(default: Any, raw: dict[str, Any], name: str, convert=None) -> Any:
    known = {f.name: f for f in fields(default)}
    values = {}
    for key, value in raw.items():
        if key not in known:
            raise ConfigError(f"unknown key [{name}].{key}")
        if convert is not None:
            value = convert(value, f"[{name}].{key}")
        elif isinstance(getattr(default, key), tuple):
            value = tuple(str(v) for v in value)
        values[key] = value
    return replace(default, **values)


def _colour(value: Any, where: str) -> RGB:
    if (
        not isinstance(value, list)
        or len(value) != 3
        or not all(isinstance(c, int) and 0 <= c <= 255 for c in value)
    ):
        raise ConfigError(f"{where} must be [r, g, b] with 0-255 integers, got {value!r}")
    return (value[0], value[1], value[2])


def _work_hours(raw: dict[str, Any]) -> WorkHours:
    wh = WorkHours()
    values: dict[str, Any] = {}
    for key, value in raw.items():
        if key == "enabled":
            values["enabled"] = bool(value)
        elif key == "days":
            try:
                values["days"] = frozenset(DAY_NAMES.index(d[:3].title()) for d in value)
            except ValueError as e:
                raise ConfigError(f"[work_hours].days must use {DAY_NAMES}, got {value!r}") from e
        elif key in ("start", "end"):
            values[key] = _time(value, f"[work_hours].{key}")
        else:
            raise ConfigError(f"unknown key [work_hours].{key}")
    wh = replace(wh, **values)
    if wh.start >= wh.end:
        raise ConfigError("[work_hours].start must be before end")
    return wh


def _time(value: Any, where: str) -> time:
    if isinstance(value, time):  # unquoted TOML local time
        return value
    try:
        return time.fromisoformat(value)
    except (TypeError, ValueError) as e:
        raise ConfigError(f"{where} must be HH:MM, got {value!r}") from e
