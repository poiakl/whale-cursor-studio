import math
from pathlib import Path
import pytest
from PIL import Image
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QMessageBox
from whale_cursor.codec import Frame
from whale_cursor.fx import MOTIONS, EXAMPLE, parse_keyframes, pose
from whale_cursor.project import Project
from whale_cursor.runtime import Engine, Overlay
from whale_cursor.ui import Window


def test_every_motion_and_keyframe_interpolation():
    frames = parse_keyframes(EXAMPLE)
    for mode in MOTIONS:
        for t in (0, 0.1, 0.5, 1):
            result = pose(mode, t, frames, 90, -90)
            assert all(math.isfinite(v) for v in result.values())
            assert 0 <= result["opacity"] <= 1 and result["scale"] >= 0
    frames = parse_keyframes(
        '[{"t":0,"x":0,"scale":1},{"t":1,"x":100,"scale":2,"opacity":0}]'
    )
    assert pose("keyframes", 0.5, frames)["x"] == 50
    assert pose("keyframes", 0.5, frames)["scale"] == 1.5
    with pytest.raises(ValueError):
        parse_keyframes('[{"t":0},{"t":0}]')
    with pytest.raises(ValueError):
        parse_keyframes('[{"t":0},{"t":1,"code":"anything"}]')


def test_overlay_renders_original_pixels_without_native_cursor_resampling(app):
    image = Image.new("RGBA", (256, 256))
    for x in range(256):
        image.paste("red" if x % 2 == 0 else "black", (x, 0, x + 1, 256))
    overlay = Overlay()
    overlay.setGeometry(0, 0, 256, 256)
    overlay.settings = {"pixel": True}
    overlay.cursor_frame = Frame(image, 0, 0)
    overlay.cursor_size = 256
    overlay.cursor_pos = QPoint(0, 0)
    output = QImage(512, 512, QImage.Format.Format_ARGB32_Premultiplied)
    output.setDevicePixelRatio(2)
    output.fill(Qt.GlobalColor.transparent)
    overlay.render(output)
    assert [output.pixelColor(x, 40).name() for x in range(8)] == [
        "#ff0000",
        "#ff0000",
        "#000000",
        "#000000",
    ] * 2
    overlay.deleteLater()
    app.processEvents()


def test_overlay_has_no_owner_and_editor_is_not_topmost_when_minimized(app):
    w = Window(Project.defaults())
    try:
        w.show()
        app.processEvents()
        overlay = w.engine.overlay
        assert overlay.parentWidget() is None
        assert not w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
        overlay.cursor_frame = w.project.slots["Arrow"][0]
        overlay.cursor_pos = QPoint(100, 100)
        overlay.present()
        before = overlay.geometry()
        assert before.width() < 400 and before.height() < 400
        w.showMinimized()
        app.processEvents()
        assert overlay.isVisible()
        assert not w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    finally:
        w.engine.overlay.hide()
        w.close()
        w.deleteLater()
        app.processEvents()


def test_clear_non_system_requires_warning_and_undo_restores_every_group(
    app, monkeypatch
):
    w = Window(Project.defaults())
    try:
        source = w.frame
        w.project.events["Arrow.click"] = [source.clone()]
        w.project.effects["click"] = [source.clone()]
        w.project.animations["test"] = [source.clone()]
        w.project.settings["animation_rules"] = [dict(id="test", trigger="hold")]
        w.project.settings["sounds"] = {"click": []}
        before = w.frame.image.tobytes()
        questions = []

        def cancel(*args):
            questions.append(args)
            return QMessageBox.StandardButton.Cancel

        monkeypatch.setattr(QMessageBox, "warning", cancel)
        w.clear_non_system()
        assert questions and w.project.effects["click"]
        assert questions[0][-1] == QMessageBox.StandardButton.Cancel
        monkeypatch.setattr(
            QMessageBox, "warning", lambda *args: QMessageBox.StandardButton.Yes
        )
        w.clear_non_system()
        assert (
            not w.project.events and not w.project.effects and not w.project.animations
        )
        assert w.frame.image.tobytes() == before
        assert w.project.settings["animation_rules"] and "sounds" in w.project.settings
        w.undo()
        assert w.project.events and w.project.effects and w.project.animations
    finally:
        w.close()
        w.deleteLater()
        app.processEvents()


def test_all_replaced_animations_and_edge_scheme_need_no_ani_loader(
    app, monkeypatch, tmp_path
):
    from whale_cursor import runtime, native

    now = [100.0]
    exe = [""]
    calls = []
    monkeypatch.setattr(native, "require", lambda: None)
    monkeypatch.setattr(native, "recover_runtime", lambda: None)
    monkeypatch.setattr(native, "snapshot", lambda: {"values": {}, "scheme": None})
    monkeypatch.setattr(native, "write_snapshot", lambda snap: None)
    monkeypatch.setattr(native, "reload", lambda: None)
    monkeypatch.setattr(
        native, "validate_file", lambda path, size: calls.append(Path(path).suffix)
    )
    monkeypatch.setattr(native, "set_live_cursor", lambda *a: True)
    monkeypatch.setattr(native, "current_role", lambda: "Arrow")
    monkeypatch.setattr(native, "foreground_exe", lambda: exe[0])
    monkeypatch.setattr(native, "button_down", lambda button: False)
    monkeypatch.setattr(runtime.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(runtime.QCursor, "pos", lambda: QPoint(100, 100))
    p = Project.defaults()
    for key in p.slots:
        p.slots[key] = [p.slots[key][0].clone() for _ in range(24)]
    edge = p.clone()
    for frame in edge.slots["Arrow"]:
        frame.x = 222
    filename = tmp_path / "edge.whalepack"
    edge.save(filename)
    p.settings["app_rules"] = [dict(exe="msedge.exe", scheme=str(filename))]
    monkeypatch.setattr(
        Project,
        "export",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("Runtime must not use ANI exports")
        ),
    )
    engine = Engine()
    try:
        engine.start(p)
        engine.timer.stop()
        engine._tick()
        assert (
            engine.overlay.cursor_frame is engine.project.slots["Arrow"][engine.index]
        )
        assert not list(engine.temp.rglob("*.ani")) and calls == [".cur"]
        exe[0] = "msedge.exe"
        now[0] += 1
        engine._tick()
        assert engine.overlay.cursor_frame.x == 222
        exe[0] = ""
        now[0] += 1
        engine._tick()
        assert engine.overlay.cursor_frame.x != 222
    finally:
        engine.stop()
