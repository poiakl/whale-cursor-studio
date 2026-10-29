from pathlib import Path
from PySide6.QtCore import QPoint
from whale_cursor.runtime import Engine
from whale_cursor.project import Project, user_dir


def test_runtime_click_idle_direction_and_restoration(app, monkeypatch, tmp_path):
    from whale_cursor import runtime, native

    now = [100.0]
    position = [QPoint(100, 100)]
    buttons = {1: False, 2: False}
    calls = []
    monkeypatch.setattr(native, "require", lambda: None)
    monkeypatch.setattr(native, "recover_runtime", lambda: False)
    monkeypatch.setattr(native, "validate_file", lambda path, size: None)
    initial = {"values": {}, "scheme": None}
    monkeypatch.setattr(native, "snapshot", lambda: initial)
    monkeypatch.setattr(
        native, "write_snapshot", lambda snap: calls.append(("restore", snap))
    )
    monkeypatch.setattr(native, "reload", lambda: calls.append(("reload",)))
    monkeypatch.setattr(
        native,
        "set_live_cursor",
        lambda role, path, size: calls.append(("arrow", Path(path).name, size)) or True,
    )
    monkeypatch.setattr(native, "current_role", lambda: "Arrow")
    monkeypatch.setattr(native, "foreground_exe", lambda: "")
    monkeypatch.setattr(native, "button_down", lambda vk: buttons[vk])
    monkeypatch.setattr(runtime.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(runtime.QCursor, "pos", lambda: position[0])
    p = Project.defaults()
    p.events = {
        key: [p.slots["Arrow"][0].clone(), p.slots["Hand"][0].clone()]
        for key in ("click", "idle", "right")
    }
    p.settings["direction"] = True
    p.settings["idle_seconds"] = 2
    e = Engine()
    e.start(p)
    e.timer.stop()
    folder = e.temp
    assert (user_dir() / "runtime-recovery.json").exists()
    now[0] += 0.1
    buttons[1] = True
    e._tick()
    assert e.state == "click"
    now[0] += 0.3
    buttons[1] = False
    position[0] = QPoint(110, 100)
    e._tick()
    assert e.state == "right"
    now[0] += 3
    e._tick()
    assert e.state == "idle"
    now[0] += 0.1
    position[0] = QPoint(100, 100)
    e._tick()
    assert e.state == "base"
    assert ("reload",) in calls
    e.stop()
    assert calls[-1] == ("restore", initial)
    assert not folder.exists()
    assert not (user_dir() / "runtime-recovery.json").exists()


def test_runtime_keeps_recovery_when_restore_fails(app, monkeypatch):
    from whale_cursor import native

    monkeypatch.setattr(native, "require", lambda: None)
    monkeypatch.setattr(native, "recover_runtime", lambda: False)
    monkeypatch.setattr(native, "validate_file", lambda path, size: None)
    monkeypatch.setattr(native, "snapshot", lambda: {"values": {}, "scheme": None})
    monkeypatch.setattr(native, "button_down", lambda _: False)
    monkeypatch.setattr(native, "write_snapshot", lambda snap: None)
    e = Engine()
    e.start(Project.defaults())
    e.timer.stop()

    def fail(_):
        raise OSError("simulated restore failure")

    monkeypatch.setattr(native, "write_snapshot", fail)
    import pytest

    with pytest.raises(OSError):
        e.stop()
    assert e.temp.exists()
    assert (user_dir() / "runtime-recovery.json").exists()
