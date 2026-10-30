import ctypes
from types import SimpleNamespace
from PySide6.QtCore import QPoint
from whale_cursor import native, runtime
from whale_cursor.project import Project
from whale_cursor.runtime import Engine


def test_taskbar_preview_restores_native_cursor_until_closed(app, monkeypatch):
    clock = [100.0]
    shell = [False]
    loads, restores = [], []
    for name in ("require", "recover_runtime"):
        monkeypatch.setattr(native, name, lambda: None)
    monkeypatch.setattr(native, "snapshot", lambda: {"values": {}, "scheme": None})
    monkeypatch.setattr(native, "write_snapshot", lambda *a: None)
    monkeypatch.setattr(native, "validate_file", lambda *a: None)
    monkeypatch.setattr(native, "set_live_cursor", lambda *a: loads.append(a) or True)
    monkeypatch.setattr(native, "reload", lambda: restores.append(True))
    monkeypatch.setattr(native, "current_role", lambda: "Arrow")
    monkeypatch.setattr(native, "button_down", lambda *a: False)
    monkeypatch.setattr(native, "foreground_exe", lambda: "")
    monkeypatch.setattr(native, "shell_surface_active", lambda: shell[0])
    monkeypatch.setattr(runtime.QCursor, "pos", lambda: QPoint(250, 250))
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
    engine = Engine()
    try:
        engine.start(Project.defaults())
        engine.timer.stop()
        engine._tick()
        assert engine.overlay.cursor_frame is not None and len(loads) == 1
        shell[0] = True
        for _ in range(3):
            clock[0] += 0.02
            engine._tick()
            assert engine.overlay.cursor_frame is None
            assert not engine.dirty_roles
        assert len(restores) == 1 and len(loads) == 1
        shell[0] = False
        clock[0] += 0.02
        engine._tick()
        assert engine.overlay.cursor_frame is not None
        assert len(loads) == 2
    finally:
        engine.stop()


def test_native_shell_detection_includes_preview_and_secondary_taskbar(monkeypatch):
    visible = [True]
    class_name = ["NormalWindow"]

    def rect(window, ptr):
        result = ctypes.cast(ptr, ctypes.POINTER(native.W.RECT)).contents
        result.left, result.top, result.right, result.bottom = 0, 0, 100, 100
        return True

    def name(window, buffer, length):
        buffer.value = class_name[0]
        return len(buffer.value)

    monkeypatch.setattr(native, "WINDOWS", True)
    monkeypatch.setattr(
        native,
        "user32",
        SimpleNamespace(
            FindWindowW=lambda cls, title: 100 if cls == "TaskListThumbnailWnd" else 0,
            IsWindowVisible=lambda window: visible[0],
            GetWindowRect=rect,
            GetCursorPos=lambda ptr: True,
            WindowFromPoint=lambda point: 200,
            GetAncestor=lambda window, mode: 200,
            GetClassNameW=name,
        ),
        raising=False,
    )
    assert native.shell_surface_active()
    visible[0] = False
    assert not native.shell_surface_active()
    class_name[0] = "Shell_SecondaryTrayWnd"
    assert native.shell_surface_active()
