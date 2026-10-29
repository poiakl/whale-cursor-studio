import math
import pytest
from PIL import Image
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage
from whale_cursor.codec import Frame
from whale_cursor.fx import MOTIONS
from whale_cursor.runtime import Engine, Overlay, pixmap


def capture(widget, ratio):
    image = QImage(
        math.ceil(widget.width() * ratio),
        math.ceil(widget.height() * ratio),
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    image.setDevicePixelRatio(ratio)
    image.fill(Qt.GlobalColor.transparent)
    widget.render(image)
    return bytes(image.constBits())


@pytest.mark.parametrize("motion", list(MOTIONS))
def test_effect_viewport_is_fixed_for_whole_playback(app, monkeypatch, motion):
    from whale_cursor import runtime

    clock = [100.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
    layer = Overlay()
    layer.settings = {
        "effect_motion": motion,
        "effect_lifetime": 1000,
        "click_size": 64,
    }
    try:
        layer.emit_at(QPoint(250, 250), "custom")
        layer.present(clock[0])
        bounds = layer.geometry()
        assert not bounds.isEmpty()
        for progress in (0.02, 0.1, 0.25, 0.5, 0.75, 0.95):
            clock[0] = 100 + progress
            layer.present(clock[0])
            app.processEvents()
            assert layer.geometry() == bounds
        layer.present(101.1)
        assert not layer.isVisible()
    finally:
        layer.hide()
        layer.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("ratio", [1, 1.25, 1.5, 2])
def test_click_effect_cannot_shift_or_repaint_cursor_pixels(app, monkeypatch, ratio):
    from whale_cursor import runtime

    clock = [100.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
    engine = Engine()
    image = Image.new("RGBA", (256, 256))
    for x in range(0, 256, 2):
        image.paste("red", (x, 0, x + 1, 256))
    engine.overlay.cursor_frame = Frame(image, 77, 93)
    engine.overlay.cursor_pos = QPoint(250, 250)
    engine.overlay.cursor_size = 64
    engine.overlay.settings = {"pixel": True}
    engine.effect_overlay.settings = {
        "pixel": True,
        "effect_motion": "pop",
        "effect_lifetime": 1000,
        "click_size": 96,
    }
    engine.effect_overlay.effects = {
        "click": [(pixmap(Image.new("RGBA", (256, 256), "blue")), 100)]
    }
    try:
        engine.present_layers(clock[0])
        app.processEvents()
        bounds = engine.overlay.geometry()
        pixels = capture(engine.overlay, ratio)
        engine.effect_overlay.emit_at(QPoint(250, 250), "custom")
        for progress in (0.02, 0.15, 0.4, 0.7, 0.95, 1.1):
            clock[0] = 100 + progress
            engine.present_layers(clock[0])
            app.processEvents()
            assert engine.overlay.geometry() == bounds
            assert capture(engine.overlay, ratio) == pixels
        assert engine.overlay.particles == []
        assert engine.effect_overlay is not engine.overlay
    finally:
        engine.overlay.hide()
        engine.effect_overlay.hide()
        engine.overlay.deleteLater()
        engine.effect_overlay.deleteLater()
        app.processEvents()


def test_changing_frame_hotspot_does_not_move_native_cursor_window(app):
    layer = Overlay()
    layer.cursor_pos = QPoint(250, 250)
    layer.cursor_size = 64
    image = Image.new("RGBA", (256, 256), "red")
    try:
        layer.cursor_frame = Frame(image, 0, 0)
        layer.present(100)
        bounds = layer.geometry()
        for x, y in ((20, 40), (255, 0), (0, 255), (255, 255)):
            layer.cursor_frame = Frame(image, x, y)
            layer.present(100)
            assert layer.geometry() == bounds
    finally:
        layer.hide()
        layer.deleteLater()
        app.processEvents()


def test_effect_viewport_does_not_shrink_when_one_click_expires(app, monkeypatch):
    from whale_cursor import runtime

    clock = [100.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
    layer = Overlay()
    layer.settings = {"effect_motion": "rise", "effect_lifetime": 1000}
    try:
        layer.emit_at(QPoint(150, 250), "custom")
        layer.present(clock[0])
        clock[0] = 100.3
        layer.emit_at(QPoint(300, 250), "custom")
        layer.present(clock[0])
        bounds = layer.geometry()
        layer.present(101.1)
        assert len(layer.particles) == 1 and layer.geometry() == bounds
    finally:
        layer.hide()
        layer.deleteLater()
        app.processEvents()


def test_click_feedback_does_not_restart_base_animation(app, monkeypatch):
    from whale_cursor import runtime, native
    from whale_cursor.project import Project

    clock = [100.0]
    buttons = {1: False, 2: False, 4: False}
    for name in ("require", "recover_runtime", "reload"):
        monkeypatch.setattr(native, name, lambda: None)
    monkeypatch.setattr(native, "snapshot", lambda: {"values": {}, "scheme": None})
    monkeypatch.setattr(native, "write_snapshot", lambda snapshot: None)
    monkeypatch.setattr(native, "validate_file", lambda *args: None)
    monkeypatch.setattr(native, "set_live_cursor", lambda *args: True)
    monkeypatch.setattr(native, "current_role", lambda: "Arrow")
    monkeypatch.setattr(native, "foreground_exe", lambda: "")
    monkeypatch.setattr(native, "button_down", lambda button: buttons[button])
    monkeypatch.setattr(runtime.QCursor, "pos", lambda: QPoint(250, 250))
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
    project = Project.defaults()
    project.slots["Arrow"] = [
        Frame(Image.new("RGBA", (256, 256), color), 70, 80, 100)
        for color in ("red", "blue", "green")
    ]
    engine = Engine()
    try:
        engine.start(project)
        engine.timer.stop()
        engine._tick()
        clock[0] = 100.12
        engine._tick()
        assert engine.index == 1
        geometry = engine.overlay.geometry()
        buttons[1] = True
        clock[0] = 100.16
        engine._tick()
        assert engine.index == 1 and engine.elapsed == pytest.approx(60)
        assert engine.effect_overlay.particles and not engine.overlay.particles
        assert engine.overlay.geometry() == geometry
        clock[0] = 100.25
        buttons[1] = False
        engine._tick()
        assert engine.index == 2
    finally:
        engine.stop()
