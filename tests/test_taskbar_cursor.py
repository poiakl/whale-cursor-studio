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
        buffer.value = "TaskListThumbnailWnd" if window == 100 else class_name[0]
        return len(buffer.value)

    monkeypatch.setattr(native, "WINDOWS", True)
    monkeypatch.setattr(
        native,
        "user32",
        SimpleNamespace(
            GetTopWindow=lambda parent: 100,
            GetWindow=lambda window, mode: 0,
            IsWindowVisible=lambda window: visible[0],
            GetWindowRect=rect,
            GetCursorPos=lambda ptr: True,
            WindowFromPoint=lambda point: 200,
            GetAncestor=lambda window, mode: 200,
            GetClassNameW=name,
        ),
        raising=False,
    )
    native.reset_shell_scan()
    assert native.shell_surface_active()
    visible[0] = False
    assert not native.shell_surface_active()
    class_name[0] = "Shell_SecondaryTrayWnd"
    assert native.shell_surface_active()


def test_all_preview_windows_and_shell_notifications_are_detected(monkeypatch):
    point = [350, 250]
    visible = {10: False, 20: True, 30: True, 99: True}
    names = {
        10: "TaskListThumbnailWnd",
        20: "TaskListThumbnailWnd",
        30: "Windows.UI.Core.CoreWindow",
        99: "AppWindow",
    }
    bounds = {
        10: (0, 0, 200, 100),
        20: (1200, 100, 1500, 300),
        30: (300, 200, 550, 400),
        99: (0, 0, 1000, 800),
    }

    def rect(window, ptr):
        data = ctypes.cast(ptr, ctypes.POINTER(native.W.RECT)).contents
        data.left, data.top, data.right, data.bottom = bounds[window]
        return True

    def position(ptr):
        data = ctypes.cast(ptr, ctypes.POINTER(native.W.POINT)).contents
        data.x, data.y = point
        return True

    def name(window, buffer, length):
        buffer.value = names[window]
        return len(buffer.value)

    monkeypatch.setattr(native, "WINDOWS", True)
    monkeypatch.setattr(native, "dwmapi", None, raising=False)
    monkeypatch.setattr(
        native,
        "window_exe",
        lambda window: "shellexperiencehost.exe" if window == 30 else "normal.exe",
    )
    monkeypatch.setattr(
        native,
        "user32",
        SimpleNamespace(
            GetTopWindow=lambda parent: 10,
            GetWindow=lambda window, mode: {10: 20, 20: 30, 30: 99, 99: 0}[window],
            GetClassNameW=name,
            IsWindowVisible=lambda window: visible[window],
            GetWindowRect=rect,
            GetCursorPos=position,
            WindowFromPoint=lambda p: 99,
            GetAncestor=lambda window, mode: window,
        ),
        raising=False,
    )
    native.reset_shell_scan()
    assert (
        native.shell_surface_active()
    )  # first preview is hidden, second monitor's is visible
    visible[20] = False
    assert (
        native.shell_surface_active()
    )  # notification overlaps the cursor even if hit-testing passes through
    point[:] = [100, 100]
    assert not native.shell_surface_active()
    point[:] = [350, 250]
    visible[30] = False
    assert not native.shell_surface_active()


def test_native_cursor_refresh_is_bounded_and_does_not_activate(monkeypatch):
    calls = []
    monkeypatch.setattr(native, "WINDOWS", True)
    monkeypatch.setattr(
        native,
        "user32",
        SimpleNamespace(
            GetCursorPos=lambda pointer: True,
            WindowFromPoint=lambda point: 123,
            SendMessageTimeoutW=lambda *args: calls.append(args),
        ),
        raising=False,
    )
    native.refresh_pointer_shape()
    assert len(calls) == 1
    assert calls[0][0:2] == (123, 0x20)
    assert calls[0][5] == 30


def test_empty_fullscreen_shell_host_is_not_treated_as_notification(monkeypatch):
    def rect(window, ptr):
        data = ctypes.cast(ptr, ctypes.POINTER(native.W.RECT)).contents
        data.left, data.top, data.right, data.bottom = 0, 0, 1920, 1080
        return True

    def name(window, buffer, length):
        buffer.value = "Windows.UI.Core.CoreWindow"
        return len(buffer.value)

    monkeypatch.setattr(native, "WINDOWS", True)
    monkeypatch.setattr(native, "dwmapi", None, raising=False)
    monkeypatch.setattr(native, "window_exe", lambda window: "shellexperiencehost.exe")
    monkeypatch.setattr(native, "bounded_shell_host", lambda window, rect: False)
    monkeypatch.setattr(
        native,
        "user32",
        SimpleNamespace(
            GetTopWindow=lambda parent: 1,
            GetWindow=lambda window, flag: 0,
            GetClassNameW=name,
            IsWindowVisible=lambda window: True,
            GetWindowRect=rect,
            GetCursorPos=lambda pointer: True,
            WindowFromPoint=lambda point: 0,
        ),
        raising=False,
    )
    native.reset_shell_scan()
    assert native.scan_shell_windows() == []
    assert not native.shell_surface_active()
