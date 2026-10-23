from luxstatus.winapp import ShortcutSpec, shortcut_spec


def test_frozen_exe_starts_itself(tmp_path):
    exe = tmp_path / "Apps" / "luxstatus.exe"
    spec = shortcut_spec(True, str(exe), "/home/me")
    assert spec == ShortcutSpec(str(exe), "", str(exe.parent))


def test_dev_checkout_uses_pythonw_from_venv(tmp_path):
    scripts = tmp_path / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "pythonw.exe").write_text("")
    spec = shortcut_spec(False, str(scripts / "python.exe"), str(tmp_path))
    assert spec.target == str(scripts / "pythonw.exe")
    assert spec.arguments == "-m luxstatus"
    assert spec.working_dir == str(tmp_path)


def test_dev_checkout_falls_back_to_python_without_pythonw(tmp_path):
    spec = shortcut_spec(False, str(tmp_path / "python"), "/src")
    assert spec.target == str(tmp_path / "python")
