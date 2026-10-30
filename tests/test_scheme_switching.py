from threading import Event, get_ident
from types import SimpleNamespace
from PySide6.QtCore import QPoint
from whale_cursor.project import Project
from whale_cursor.runtime import Engine, Overlay
from whale_cursor import native, runtime
from test_overlay_stability import capture


def test_first_scheme_load_keeps_gui_responsive_and_clears_old_effects(
    app, monkeypatch
):
    clock = [100.0]
    exe = [""]
    entered, release = Event(), Event()
    worker_ids = []
    p = Project.defaults()
    other = p.clone()
    p.settings["app_rules"] = [dict(exe="msedge.exe", scheme="edge.whalepack")]
    for name in ("require", "recover_runtime", "reload"):
        monkeypatch.setattr(native, name, lambda: None)
    monkeypatch.setattr(native, "snapshot", lambda: {"values": {}, "scheme": None})
    monkeypatch.setattr(native, "write_snapshot", lambda *a: None)
    monkeypatch.setattr(native, "validate_file", lambda *a: None)
    monkeypatch.setattr(native, "set_live_cursor", lambda *a: True)
    monkeypatch.setattr(native, "current_role", lambda: "Arrow")
    monkeypatch.setattr(native, "button_down", lambda *a: False)
    monkeypatch.setattr(native, "foreground_exe", lambda: exe[0])
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runtime.QCursor, "pos", lambda: QPoint(250, 250))

    def load(path):
        worker_ids.append(get_ident())
        entered.set()
        assert release.wait(10)
        return other

    monkeypatch.setattr(Project, "load", load)
    engine = Engine()
    try:
        engine.start(p)
        engine.timer.stop()
        engine._tick()
        engine.effect_overlay.emit_at(QPoint(250, 250), "ripple")
        engine.present_layers(clock[0])
        assert engine.effect_overlay.isVisible()
        exe[0] = "msedge.exe"
        clock[0] += 0.7
        engine._tick()  # must return without waiting for release
        assert entered.wait(3)
        assert worker_ids[0] != get_ident()
        assert not engine.effect_overlay.particles
        assert not any(capture(engine.effect_overlay, 1))
        assert engine.project is engine.base_project
        # Returning to base before loading finishes must not apply the stale result.
        exe[0] = ""
        clock[0] += 0.7
        engine._tick()
        release.set()
        engine.rule_futures["edge.whalepack"].result(timeout=5)
        clock[0] += 0.02
        engine._tick()
        assert engine.project is engine.base_project
        # Revisiting the application uses the completed result, not a new load.
        exe[0] = "msedge.exe"
        clock[0] += 0.7
        engine._tick()
        assert engine.project is other
        assert len(worker_ids) == 1
        assert not engine.effect_overlay.particles
    finally:
        release.set()
        engine.stop()


def test_effect_surface_is_painted_synchronously_on_first_and_repeated_show(
    app, monkeypatch
):
    clock = [100.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
    layer = Overlay()
    paints = []
    original = layer.paintEvent

    def paint(event):
        original(event)
        paints.append(layer.render_time)

    layer.paintEvent = paint
    try:
        layer.settings = {"effect_lifetime": 500}
        layer.keep_visible = True
        layer.fit_screens()
        layer.present(clock[0])
        app.processEvents()
        for i in range(4):
            clock[0] = 100 + i
            layer.emit_at(QPoint(200 + i * 25, 250), "ripple")
            before = len(paints)
            layer.present(clock[0])
            assert len(paints) > before and paints[-1] == clock[0]
            layer.present(clock[0] + 0.6)
            assert layer.isVisible()
            assert not any(capture(layer, 1))
        layer.particles = []
        image = capture(layer, 1)
        assert not any(image)  # no old colored pixels survive transparent repaint
    finally:
        layer.hide()
        layer.deleteLater()
        app.processEvents()


def test_every_frame_restacks_effect_then_cursor_without_editor(app, monkeypatch):
    engine = Engine()
    p = Project.defaults()
    calls = []
    monkeypatch.setattr(
        native, "restack_overlays", lambda handles: calls.append(handles)
    )
    try:
        engine.overlay.cursor_frame = p.slots["Arrow"][0]
        engine.overlay.cursor_pos = QPoint(250, 250)
        engine.effect_overlay.emit_at(QPoint(250, 250), "ripple")
        engine.present_layers(runtime.time.monotonic())
        engine.present_layers(runtime.time.monotonic())
        assert (
            calls
            == [[int(engine.effect_overlay.winId()), int(engine.overlay.winId())]] * 2
        )
    finally:
        engine.overlay.hide()
        engine.effect_overlay.hide()
        engine.overlay.deleteLater()
        engine.effect_overlay.deleteLater()
        app.processEvents()


def test_windows_restack_never_activates_or_moves_window(monkeypatch):
    calls = []
    monkeypatch.setattr(native, "WINDOWS", True)
    monkeypatch.setattr(
        native,
        "user32",
        SimpleNamespace(SetWindowPos=lambda *a: calls.append(a)),
        raising=False,
    )
    native.restack_overlays([100, 200])
    assert [args[0] for args in calls] == [100, 200]
    assert all(args[1].value == -1 % (1 << 64) or args[1].value == -1 for args in calls)
    assert all(args[-1] & 0x10 and args[-1] & 3 == 3 for args in calls)
