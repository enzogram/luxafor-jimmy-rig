"""Microphone/camera in-use detection from the Windows privacy ConsentStore registry keys.

The parsing, filtering and debounce logic works on plain dicts so it can be tested anywhere;
only ``read_consent_tree`` and ``running_process_names`` touch Windows.
"""

from __future__ import annotations

import ntpath
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from luxstatus.config import DevicesConfig
from luxstatus.state import Source, SourceUpdate, Status

CONSENT_STORE = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore"
MICROPHONE = "microphone"
WEBCAM = "webcam"
NON_PACKAGED = "NonPackaged"

# A tree is {subkey name: subtree}; a leaf app key maps value names to values.
ConsentTree = Mapping[str, Any]


@dataclass(frozen=True)
class AppEntry:
    key: str
    packaged: bool
    start: int = 0  # FILETIME
    stop: int = 0  # FILETIME

    @property
    def in_use(self) -> bool:
        return self.start > 0 and self.stop == 0

    @property
    def exe_path(self) -> str | None:
        return None if self.packaged else self.key.replace("#", "\\")

    @property
    def name(self) -> str:
        """``MSTeams`` for ``MSTeams_8wekyb3d8bbwe``; ``Zoom.exe`` for a NonPackaged path."""
        if self.packaged:
            return self.key.split("_", 1)[0]
        return ntpath.basename(self.exe_path or self.key)


def parse_consent_tree(tree: ConsentTree) -> list[AppEntry]:
    entries = []
    for key, sub in tree.items():
        if key == NON_PACKAGED and isinstance(sub, Mapping):
            entries += [
                _entry(k, v, packaged=False) for k, v in sub.items() if isinstance(v, Mapping)
            ]
        elif isinstance(sub, Mapping):
            entries.append(_entry(key, sub, packaged=True))
    return entries


def _entry(key: str, values: Mapping[str, Any], packaged: bool) -> AppEntry:
    return AppEntry(
        key,
        packaged,
        int(values.get("LastUsedTimeStart", 0) or 0),
        int(values.get("LastUsedTimeStop", 0) or 0),
    )


def matches(entry: AppEntry, include: Iterable[str], exclude: Iterable[str]) -> bool:
    key = entry.key.lower()
    if any(s.lower() in key for s in exclude):
        return False
    include = list(include)
    return not include or any(s.lower() in key for s in include)


def active_apps(
    tree: ConsentTree,
    include: Iterable[str],
    exclude: Iterable[str],
    running_names: Callable[[], set[str]],
) -> list[AppEntry]:
    """In-use, filtered entries. NonPackaged entries whose exe is not running are dropped:
    an app that crashes mid-call leaves ``LastUsedTimeStop`` at 0 indefinitely."""
    candidates = [e for e in parse_consent_tree(tree) if e.in_use and matches(e, include, exclude)]
    if any(not e.packaged for e in candidates):
        names = running_names()
        candidates = [e for e in candidates if e.packaged or e.name.lower() in names]
    return candidates


class Debouncer:
    """On after ``on_reads`` consecutive in-use reads; off after ``off_seconds`` of not-in-use."""

    def __init__(self, on_reads: int, off_seconds: float) -> None:
        self.on_reads = on_reads
        self.off_after = timedelta(seconds=off_seconds)
        self.state = False
        self._hits = 0
        self._idle_since: datetime | None = None

    def update(self, in_use: bool, now: datetime) -> bool:
        if in_use:
            self._hits += 1
            self._idle_since = None
            if self._hits >= self.on_reads:
                self.state = True
        else:
            self._hits = 0
            if self.state:
                if self._idle_since is None:
                    self._idle_since = now
                if now - self._idle_since >= self.off_after:
                    self.state = False
                    self._idle_since = None
        return self.state


def describe(mic: list[AppEntry], cam: list[AppEntry]) -> str:
    """``MSTeams (mic + camera)``, ``Zoom.exe (mic)``, ``chrome.exe, MSTeams (camera)``."""
    names = sorted({e.name for e in mic} | {e.name for e in cam}, key=str.lower)
    kinds = " + ".join(k for k, apps in (("mic", mic), ("camera", cam)) if apps)
    return f"{', '.join(names)} ({kinds})"


class DevicesSource:
    def __init__(
        self,
        config: DevicesConfig,
        read_tree: Callable[[str], ConsentTree] | None = None,
        running_names: Callable[[], set[str]] | None = None,
    ) -> None:
        self.config = config
        self.read_tree = read_tree or read_consent_tree
        self.running_names = running_names or running_process_names
        self.debouncer = Debouncer(config.on_debounce_reads, config.off_debounce_seconds)
        self._detail = ""
        self._camera = False

    def poll(self, now: datetime) -> SourceUpdate:
        names_cache: list[set[str]] = []

        def names() -> set[str]:
            if not names_cache:
                names_cache.append(self.running_names())
            return names_cache[0]

        cfg = self.config
        mic = active_apps(self.read_tree(MICROPHONE), cfg.include, cfg.exclude, names)
        cam = active_apps(self.read_tree(WEBCAM), cfg.include, cfg.exclude, names)
        in_use = bool(mic or cam)
        if in_use:
            # While debounce holds CALL after the apps let go, keep the last detail.
            self._detail = describe(mic, cam)
            self._camera = bool(cam)
        if not self.debouncer.update(in_use, now):
            return SourceUpdate(Source.DEVICES, now)
        return SourceUpdate(Source.DEVICES, now, Status.CALL, self._detail, camera=self._camera)


def read_consent_tree(capability: str) -> dict[str, Any]:
    """Read HKCU ConsentStore\\<capability> into a nested dict. Missing key → empty dict."""
    import winreg

    def read(key: Any) -> dict[str, Any]:
        node: dict[str, Any] = {}
        i = 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(key, i)
            except OSError:
                break
            node[name] = value
            i += 1
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(key, i)
            except OSError:
                break
            with winreg.OpenKey(key, sub) as child:
                node[sub] = read(child)
            i += 1
        return node

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{CONSENT_STORE}\\{capability}") as root:
            tree = read(root)
    except FileNotFoundError:
        return {}
    # Values on the root key (e.g. "Value": "Allow") are not apps.
    return {k: v for k, v in tree.items() if isinstance(v, dict)}


def running_process_names() -> set[str]:
    import psutil

    return {p.info["name"].lower() for p in psutil.process_iter(["name"]) if p.info.get("name")}
