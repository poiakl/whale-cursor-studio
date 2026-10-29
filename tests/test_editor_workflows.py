import time
import pytest
from PIL import Image
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from whale_cursor.codec import Frame
from whale_cursor.project import Project, SLOTS, event_key
from whale_cursor.ui import Window
from whale_cursor.widgets import MatchDialog, guess_role


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


def test_seventeen_roles_for_every_trigger(window):
    window.group_box.setCurrentIndex(1)
    assert window.slot_list.count() == 17 and len(window.event_status) == 17
    for role in SLOTS:
        window.edit_event(role, "idle")
        assert window.key == role and window.storage_key == event_key(role, "idle")


def test_unified_undo_redo_hotspot_and_frames(window, app):
    x, y = window.frame.x, window.frame.y
    window.set_hot(40, 70)
    window.duplicate_frame()
    window.set_hot(80, 90)
    window.undo()
    assert (window.frame.x, window.frame.y) == (40, 70) and len(window.frames) == 2
    window.undo()
    assert len(window.frames) == 1
    window.undo()
    assert (window.frame.x, window.frame.y) == (x, y)
    window.redo()
    assert (window.frame.x, window.frame.y) == (40, 70)
    window.redo()
    assert len(window.frames) == 2


def test_one_mouse_drag_is_one_history_entry(window, app):
    canvas = window.canvas
    area = canvas.area()
    a = QPoint(int(area.x() + 20), int(area.y() + 20))
    b = QPoint(int(area.x() + 80), int(area.y() + 80))
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=a)
    QTest.mouseMove(canvas, b)
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=b)
    assert len(window.history.past) == 1


def test_thirty_entries_and_redo_invalidated(window):
    for i in range(35):
        window.remember()
        window.project.name = str(i)
    assert len(window.history.past) == 30
    window.undo()
    assert window.history.future
    window.duplicate_frame()
    assert not window.history.future


def test_clipboard_shortcuts_insert_and_delete(window, app):
    original = window.frame.image.tobytes()
    window.frame_list.setFocus()
    QTest.keyClick(window.frame_list, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(window.frame_list, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert len(window.frames) == 2 and window.frames[1].image.tobytes() == original
    QTest.keyClick(window.frame_list, Qt.Key.Key_Delete)
    assert len(window.frames) == 1
    window.undo()
    assert len(window.frames) == 2


def test_batch_matches_named_and_unknown_with_manual_swap(window, app):
    a = Frame(Image.new("RGBA", (256, 256), "red"))
    b = Frame(Image.new("RGBA", (256, 256), "blue"))
    assets = [
        {"path": "指针_Hand.gif", "frames": [a, a.clone()]},
        {"path": "陌生文件.png", "frames": [b]},
    ]
    dialog = MatchDialog(assets, window)
    mapping = dialog.result_mapping()
    assert len(mapping["Hand"]) == 2
    arrow = list(SLOTS).index("Arrow")
    dialog.assign(arrow, 0)
    mapping = dialog.result_mapping()
    assert mapping["Arrow"][0].image.getpixel((0, 0))[:3] == (255, 0, 0)
    assert mapping["Hand"][0].image.getpixel((0, 0))[:3] == (0, 0, 255)
    window.apply_matching(mapping)
    assert len(window.project.slots["Arrow"]) == 2
    window.undo()
    assert len(window.project.slots["Arrow"]) == 1
    dialog.reject()


def test_batch_interactions_match_selected_trigger(window):
    window.edit_event("Hand", "idle")
    frames = [Frame(Image.new("RGBA", (256, 256), "red"))]
    window.apply_matching({"Hand": frames, "IBeam": frames})
    assert window.project.event_frames("Hand", "idle") and window.project.event_frames(
        "IBeam", "idle"
    )
    assert not window.project.event_frames("Hand", "click")


def test_small_inspector_controls_do_not_overlap(window, app):
    window.resize(1000, 680)
    app.processEvents()
    # Compare control positions in one common coordinate system, including scrolled content.
    for first, second in (
        (window.hot_x, window.hot_y),
        (window.size_combo, window.duration),
    ):
        a = first.mapTo(window, QPoint(0, 0))
        b = second.mapTo(window, QPoint(0, 0))
        assert a.y() + first.height() <= b.y()
        assert first.height() >= 34


def test_delete_slot_keeps_role_and_is_undoable(window):
    window.clear_slot()
    assert not window.frames and window.slot_list.count() == 17
    window.undo()
    assert window.frames


def test_interaction_preview_advances_and_status_is_explicit(window, app):
    frames = [
        Frame(Image.new("RGBA", (256, 256), c), duration=40) for c in ("red", "blue")
    ]
    window.project.events["Hand.click"] = frames
    window.refresh_events()
    window.tabs.setCurrentIndex(1)
    app.processEvents()
    thumb = window.event_thumbs["Hand"]
    thumb.epoch = time.monotonic() - 0.05
    thumb.advance(force=True)
    assert thumb.frame_index == 1
    window.set_runtime_state("running")
    assert (
        "正在运行" in window.runtime_status.text() and window.runtime_button.isChecked()
    )
    window.set_runtime_state("stopped")
    assert (
        "已停止" in window.runtime_status.text()
        and not window.runtime_button.isChecked()
    )


def test_inspection_includes_invalid_later_interaction_frame(window):
    good = window.frame.clone()
    bad = good.clone()
    bad.x = 999
    window.project.events["Hand.idle"] = [good, bad]
    row = next(r for r in window.project.inspect() if r["key"] == "Hand.idle")
    assert row["count"] == 2 and any("第 2 帧" in text for text in row["issues"])
    bad.x = 128


def test_old_event_migrates_without_loss(tmp_path):
    p = Project.defaults()
    p.events["click"] = [p.slots["Hand"][0].clone()]
    path = tmp_path / "old.whalepack"
    p.save(path)
    q = Project.load(path)
    assert (
        q.event_frames("Arrow", "click")[0].image.tobytes()
        == p.events["click"][0].image.tobytes()
    )


@pytest.mark.parametrize(
    "name,expected",
    [
        ("正常选择_Arrow.gif", "Arrow"),
        ("文本选择.gif", "IBeam"),
        ("正常选择_Arrow_animated.ani", "Arrow"),
        ("01.png", "Arrow"),
        ("links_Hand.gif", "Hand"),
    ],
)
def test_matching_name_aliases(name, expected):
    assert guess_role(name) == expected
