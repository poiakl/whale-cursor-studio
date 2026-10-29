import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from PIL import Image
from whale_cursor.project import Project
from whale_cursor.codec import Frame
from whale_cursor.ui import Window


@pytest.fixture
def window(app, monkeypatch):
    w = Window(Project.defaults())
    monkeypatch.setattr(
        w, "error", lambda message: (_ for _ in ()).throw(AssertionError(message))
    )
    w.show()
    app.processEvents()
    yield w
    w.save_timer.stop()
    w.close()
    w.deleteLater()
    app.processEvents()


def test_editor_hotspot_drag_and_keyboard(window, app):
    canvas = window.canvas
    a = canvas.area()
    point = QPoint(round(a.x() + a.width() * 0.25), round(a.y() + a.height() * 0.5))
    QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=point)
    assert abs(window.frame.x - 64) <= 1 and abs(window.frame.y - 128) <= 1
    before = window.frame.x
    QTest.keyClick(canvas, Qt.Key.Key_Right)
    assert window.frame.x == before + 1
    QTest.keyClick(canvas, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
    assert window.frame.x == max(0, before - 9)


def test_frame_edit_order_and_undo(window):
    window.project.slots["Arrow"] = [
        Frame(Image.new("RGBA", (256, 256), "red"), 10, 10, 100),
        Frame(Image.new("RGBA", (256, 256), "blue"), 20, 20, 200),
    ]
    window.rebuild_frames()
    window.duplicate_frame()
    assert len(window.frames) == 3
    window.undo()
    assert len(window.frames) == 2
    window.reverse_frames()
    assert window.frames[0].x == 20
    window.move_frame(1)
    assert window.frame_index == 1


def test_import_animation_and_events(window, tmp_path):
    path = tmp_path / "a.gif"
    a = Image.new("RGBA", (32, 32), "red")
    b = Image.new("RGBA", (32, 32), "blue")
    a.save(path, save_all=True, append_images=[b], duration=100)
    window.load_files([str(path)])
    assert len(window.frames) == 2
    window.edit_event("click")
    window.load_files([str(path)])
    assert len(window.project.event_frames("Arrow", "click")) == 2
    window.hot_all()
    assert window.frames[0].x == window.frames[1].x


def test_clean_layout_all_tabs(window, app):
    for i in range(4):
        window.tabs.setCurrentIndex(i)
        app.processEvents()
        assert window.tabs.currentWidget().isVisible()
    assert window.minimumWidth() >= 1000


def test_runtime_opt_in_on_non_windows(window):
    from whale_cursor import native

    if native.WINDOWS:
        pytest.skip("Non-Windows boundary only")
    with pytest.raises(RuntimeError):
        window.engine.start(window.project)
    assert not window.engine.active


def test_drag_sequence_changes_exported_animation(window, app, tmp_path):
    from whale_cursor.codec import decode_ani

    window.project.slots["Arrow"] = [
        Frame(Image.new("RGBA", (256, 256), color), 128, 128, duration)
        for color, duration in [("red", 100), ("blue", 200), ("green", 300)]
    ]
    window.rebuild_frames()
    window.frame_list.move_requested.emit(0, 3)
    app.processEvents()
    path = window.project.export(tmp_path / "ordered", "Arrow")["Arrow"]
    frames = decode_ani(open(path, "rb").read())
    assert [f.duration for f in frames] == [200, 300, 100]
    assert frames[-1].image.getpixel((128, 128))[:3] == (255, 0, 0)
