import math
from PIL import Image
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage
from whale_cursor.codec import Frame
from whale_cursor.runtime import EffectLayer, Overlay
from whale_cursor.fx import MOTIONS


def rgba(widget, ratio=1):
    result = QImage(
        math.ceil(widget.width() * ratio),
        math.ceil(widget.height() * ratio),
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    result.setDevicePixelRatio(ratio)
    result.fill(Qt.GlobalColor.transparent)
    widget.render(result)
    return Image.frombytes(
        "RGBA",
        (result.width(), result.height()),
        bytes(result.constBits()),
        "raw",
        "BGRA",
    )


@pytest.mark.parametrize("motion", list(MOTIONS))
def test_real_effect_layer_never_moves_or_resizes_tiles_for_repeated_clicks(
    app, monkeypatch, motion
):
    from whale_cursor import runtime

    clock = [100.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
    layer = EffectLayer()
    layer.settings = {"effect_motion": motion, "effect_lifetime": 1000}
    try:
        layer.fit_screens()
        app.processEvents()
        original = [(int(tile.winId()), tile.geometry()) for tile in layer._tiles]
        for index, point in enumerate(
            (QPoint(150, 180), QPoint(510, 250), QPoint(600, 500), QPoint(150, 180))
        ):
            clock[0] = 100 + index * 2
            layer.emit_at(point, "custom")
            for progress in (0.01, 0.1, 0.3, 0.6, 0.99, 1.1):
                layer.present(clock[0] + progress)
                app.processEvents()
                assert [
                    (int(tile.winId()), tile.geometry()) for tile in layer._tiles
                ] == original
                assert all(tile.isVisible() for tile in layer._tiles)
            assert not layer.particles
            assert all(
                rgba(tile).getchannel("A").getbbox() is None for tile in layer._tiles
            )
    finally:
        layer.hide()
        layer.deleteLater()
        app.processEvents()


def test_finished_click_clip_does_not_wrap_to_opaque_first_frame(app, monkeypatch):
    from whale_cursor import runtime

    clock = [100.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
    layer = EffectLayer()
    layer.settings = {
        "effect_lifetime": 1200,
        "click_size": 80,
        "effect_motion": "fade",
    }
    layer.effects = {
        "click": [
            (Image.new("RGBA", (256, 256), "red"), 100),
            (Image.new("RGBA", (256, 256)), 100),
        ]
    }
    try:
        layer.emit_at(QPoint(250, 250), "custom")
        layer.present(100.05)
        app.processEvents()
        assert any(
            rgba(tile).getchannel("A").getbbox() is not None for tile in layer._tiles
        )
        for t in (0.15, 0.21, 0.4, 0.61, 0.8, 1.01, 1.19):
            layer.present(100 + t)
            app.processEvents()
            assert all(
                rgba(tile).getchannel("A").getbbox() is None for tile in layer._tiles
            )
    finally:
        layer.hide()
        layer.deleteLater()
        app.processEvents()


def test_mixed_trail_and_click_expiry_clear_every_affected_tile(app, monkeypatch):
    from whale_cursor import runtime

    clock = [100.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
    layer = EffectLayer()
    layer.settings = {"effect_lifetime": 1500, "trail_lifetime": 300, "trail_size": 64}
    try:
        layer.emit_at(QPoint(510, 250), "ripple")
        layer.emit_at(QPoint(510, 250), "trail")
        layer.present(100.1)
        assert len(layer.active_handles()) >= 2
        layer.present(100.4)
        assert [p[2] for p in layer.particles] == ["ripple"]
        layer.clear()
        app.processEvents()
        assert not layer.active_handles()
        assert all(
            rgba(tile).getchannel("A").getbbox() is None for tile in layer._tiles
        )
    finally:
        layer.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("ratio", [1, 1.25, 1.5, 2])
def test_cursor_render_matches_export_pixel_dimensions(app, ratio):
    layer = Overlay()
    layer.cursor_frame = Frame(Image.new("RGBA", (256, 256), "red"), 0, 0)
    layer.cursor_pos = QPoint(256, 256)
    layer.cursor_size = 64 / ratio
    try:
        layer.present(100)
        output = rgba(layer, ratio)
        box = output.getchannel("A").getbbox()
        assert box is not None
        assert abs((box[2] - box[0]) - 64) <= 1
        assert abs((box[3] - box[1]) - 64) <= 1
    finally:
        layer.hide()
        layer.deleteLater()
        app.processEvents()
