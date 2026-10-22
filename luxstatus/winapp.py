"""Windows process plumbing: single-instance mutex, message box, start-at-login shortcut."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

MUTEX_NAME = "Local\\luxstatus-single-instance"
ERROR_ALREADY_EXISTS = 183
SHORTCUT_NAME = "luxstatus.lnk"

_mutex = None  # held for the life of the process


def acquire_single_instance() -> bool:
    """True if this is the only running luxstatus in this logon session."""
    global _mutex
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        return True  # cannot tell; better to run than to refuse
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        return False
    _mutex = handle
    return True


def message_box(text: str, title: str = "luxstatus") -> None:
    import ctypes

    MB_ICONINFORMATION = 0x40
    ctypes.windll.user32.MessageBoxW(None, text, title, MB_ICONINFORMATION)


@dataclass(frozen=True)
class ShortcutSpec:
    target: str
    arguments: str
    working_dir: str


def shortcut_spec(frozen: bool, executable: str, cwd: str) -> ShortcutSpec:
    """The packaged exe starts itself; a dev checkout starts ``pythonw -m luxstatus`` from the
    checkout so its config.toml is found."""
    exe = Path(executable)
    if frozen:
        return ShortcutSpec(str(exe), "", str(exe.parent))
    pythonw = exe.with_name("pythonw.exe")
    return ShortcutSpec(str(pythonw if pythonw.exists() else exe), "-m luxstatus", cwd)


def startup_dir() -> Path:
    return (
        Path(os.environ["APPDATA"])
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
    )


def startup_shortcut() -> Path:
    return startup_dir() / SHORTCUT_NAME


def startup_installed() -> bool:
    return startup_shortcut().exists()


def install_startup() -> Path:
    import win32com.client

    spec = shortcut_spec(getattr(sys, "frozen", False), sys.executable, os.getcwd())
    path = startup_shortcut()
    shell = win32com.client.Dispatch("WScript.Shell")
    link = shell.CreateShortcut(str(path))
    link.TargetPath = spec.target
    link.Arguments = spec.arguments
    link.WorkingDirectory = spec.working_dir
    link.Description = "luxstatus status light"
    link.Save()
    return path


def uninstall_startup() -> None:
    startup_shortcut().unlink(missing_ok=True)
