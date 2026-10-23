import os

from luxstatus.config import Config
from luxstatus.reload import ConfigWatcher


def touch(path, text, bump):
    path.write_text(text)
    st = path.stat()
    os.utime(path, (st.st_atime, st.st_mtime + bump))  # mtime resolution can be coarse


def test_change_bad_edit_and_fix(tmp_path):
    path = tmp_path / "config.toml"
    touch(path, "", 0)
    applied, errors = [], []
    w = ConfigWatcher(lambda: path, lambda cfg, p: applied.append(cfg), errors.append)
    assert not w.check()  # unchanged since start

    touch(path, "[colours]\nbusy = [1, 2, 3]\n", 10)
    assert w.check()
    assert applied[-1].colours.busy == (1, 2, 3)

    touch(path, "[colours\n", 20)
    assert not w.check()
    assert len(applied) == 1 and "keeping previous settings" in errors[0]
    assert not w.check()  # the same bad file is reported once

    touch(path, "[colours]\nbusy = [4, 5, 6]\n", 30)
    assert w.check()
    assert applied[-1].colours.busy == (4, 5, 6)


def test_new_file_taking_over_from_defaults(tmp_path):
    user = tmp_path / "luxstatus" / "config.toml"
    applied = []

    def locate():
        return user if user.exists() else None

    w = ConfigWatcher(locate, lambda cfg, p: applied.append((cfg, p)))
    assert not w.check()
    user.parent.mkdir()
    user.write_text("[effects]\ncall_flash = false\n")
    assert w.check()
    cfg, p = applied[0]
    assert p == user and not cfg.effects.call_flash


def test_deleting_the_file_returns_to_defaults(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[colours]\nbusy = [1, 2, 3]\n")
    applied = []
    w = ConfigWatcher(lambda: path if path.exists() else None, lambda c, p: applied.append(c))
    path.unlink()
    assert w.check()
    assert applied == [Config()]
