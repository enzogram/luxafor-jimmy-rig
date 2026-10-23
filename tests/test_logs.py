from pathlib import Path

from luxstatus.logs import log_path


def test_log_path_uses_localappdata(monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", "/x/AppData/Local")
    assert log_path() == Path("/x/AppData/Local/luxstatus/luxstatus.log")
    monkeypatch.delenv("LOCALAPPDATA")
    assert log_path() == Path.home() / ".luxstatus" / "luxstatus.log"
