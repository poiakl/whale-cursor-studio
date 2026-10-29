import sys
import pytest
from whale_cursor import native
from whale_cursor.project import Project
from whale_cursor.codec import encode_cur, encode_ani, SIZES


@pytest.mark.skipif(
    sys.platform != "win32", reason="Windows loader only; no registry changes"
)
def test_windows_loads_cur_and_ani(tmp_path):
    p = Project.defaults()
    f = p.slots["Arrow"][0]
    for size in (*SIZES, 80, 112, 160, 192):
        path = tmp_path / f"{size}.cur"
        path.write_bytes(encode_cur(f, (size,)))
        native.validate_file(path, size)
        path = tmp_path / f"{size}.ani"
        path.write_bytes(encode_ani([f, f.clone()], (size,)))
        native.validate_file(path, size)


@pytest.mark.skipif(
    sys.platform != "win32", reason="Windows runner installation round trip"
)
def test_windows_install_restore_roundtrip(tmp_path):
    # CI owns the Windows runner; restore the exact starting state even if a check fails.
    before = native.snapshot()
    try:
        p = Project.defaults()
        paths = native.install(p)
        assert len(paths) == 17
        paths2 = native.install(p)
        assert paths2["Arrow"] != paths["Arrow"]
        assert native.snapshot()["values"]["Arrow"][0] == paths2["Arrow"]
        native.restore()
        assert native.snapshot() == before
    finally:
        native.write_snapshot(before)


@pytest.mark.skipif(
    sys.platform != "win32", reason="Real Windows overlay and cursor restoration"
)
def test_windows_interactions_play_all_animated_slots_without_loading_ani(
    app, monkeypatch
):
    from ctypes import wintypes as W
    from whale_cursor.runtime import Engine

    p = Project.defaults()
    for key in p.slots:
        p.slots[key] = [p.slots[key][0].clone() for _ in range(24)]
    before = native.snapshot()
    engine = Engine()
    monkeypatch.setattr(native, "foreground_exe", lambda: "")
    monkeypatch.setattr(native, "current_role", lambda: "Arrow")
    monkeypatch.setattr(native, "button_down", lambda button: False)
    try:
        engine.start(p)
        engine.timer.stop()
        engine._tick()
        app.processEvents()
        assert (
            engine.overlay.cursor_frame is engine.project.slots["Arrow"][engine.index]
        )
        assert not list(engine.temp.rglob("*.ani"))
        get_window = native.user32.GetWindow
        get_window.argtypes = [W.HWND, W.UINT]
        get_window.restype = W.HWND
        assert not get_window(int(engine.overlay.winId()), 4)
        assert native.snapshot() == before
    finally:
        if engine.active:
            engine.stop()
        native.write_snapshot(before)
        engine.overlay.deleteLater()
        app.processEvents()
