"""Logging to a rotating file in %LOCALAPPDATA%\\luxstatus, plus the console when there is one."""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import threading
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s"


def log_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) / "luxstatus" if base else Path.home() / ".luxstatus"
    return root / "luxstatus.log"


def setup(console: bool, verbose: bool = False, path: Path | None = None) -> Path:
    path = path or log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    file_handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(FORMAT))
    root.addHandler(file_handler)
    # A windowed exe has no stderr; only log to the console when there is one.
    if console and sys.stderr is not None:
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
        root.addHandler(stream)

    def excepthook(exc_type, exc, tb):
        logging.getLogger("luxstatus").critical("unhandled exception", exc_info=(exc_type, exc, tb))

    def thread_excepthook(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is not SystemExit and args.exc_value is not None:
            logging.getLogger("luxstatus").critical(
                "unhandled exception in thread %s",
                args.thread.name if args.thread else "?",
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )

    sys.excepthook = excepthook
    threading.excepthook = thread_excepthook
    return path
