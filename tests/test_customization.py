import threading
import wave
from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QPoint, QPointF, QEvent
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtTest import QTest
from whale_cursor.project import Project, EVENTS
from whale_cursor.ui import Window
from whale_cursor.runtime import Engine


@pytest.fixture
def window(app):
    w = Window(Project.defaults())
    w.show()
    app.processEvents()
    yield w
    w.close()
    w.deleteLater()
    app.processEvents()


def test_middle_pan_preserves_hotspot_history_and_hit_testing(window, app):
    canvas = window.canvas
    canvas.zoom = 2
    start = canvas.area().topLeft()
    hotspot = (window.frame.x, window.frame.y)
    QTest.mousePress(canvas, Qt.MouseButton.MiddleButton, pos=QPoint(90, 90))
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(125, 115),
        QPointF(125, 115),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.MiddleButton,
        Qt.KeyboardModifier.NoModifier,
    )
    app.sendEvent(canvas, event)
    QTest.mouseRelease(canvas, Qt.MouseButton.MiddleButton, pos=QPoint(125, 115))
    assert canvas.area().topLeft() == start + QPointF(35, 25)
    assert (window.frame.x, window.frame.y) == hotspot
    assert not window.history.past
    assert canvas.cursor().shape() == Qt.CursorShape.CrossCursor
    area = canvas.area()
    point = area.center().toPoint()
    QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=point)
    assert abs(window.frame.x - 128) <= 1
    assert abs(window.frame.y - 128) <= 1


def test_wheel_does_not_change_parameters(window, app):
    for field in (window.hot_x, window.duration, window.size_combo, window.volume):
        field.setFocus()
        before = field.currentText() if hasattr(field, "currentText") else field.value()
        event = QWheelEvent(
            QPointF(10, 10),
            QPointF(10, 10),
            QPoint(),
            QPoint(0, 120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        app.sendEvent(field, event)
        after = field.currentText() if hasattr(field, "currentText") else field.value()
        assert after == before


def test_clear_list_and_effect_frame_edits_are_undoable(window):
    window.clear_list()
    assert not any(window.project.slots.values())
    window.undo()
    assert all(window.project.slots.values())
    window.edit_effect("trail")
    window.project.effects["trail"] = [window.project.slots["Arrow"][0].clone()]
    window.rebuild_slots()
    window.duplicate_frame()
    assert len(window.frames) == 2
    window.clear_slot()
    assert not window.frames
    window.undo()
    assert window.group == "effects" and len(window.frames) == 2


def test_custom_shortcut_and_history_limit(window):
    window.set_history_limit(5)
    for _ in range(8):
        window.remember()
    assert len(window.history.past) == 5
    window.change_shortcut("复制帧", "Alt+C")
    assert all(
        a.shortcut().toString() == "Alt+C" for a in window.shortcut_actions["复制帧"]
    )
    window.reset_shortcuts()
    assert all(
        a.shortcut().toString() == "Ctrl+C" for a in window.shortcut_actions["复制帧"]
    )


def test_autosave_runs_off_ui_thread_and_uses_snapshot(window, app, monkeypatch):
    started, release = threading.Event(), threading.Event()
    original = Project.save
    result = []
    main_thread = threading.get_ident()

    def slow_save(project, path):
        result.append((threading.get_ident(), project.name))
        started.set()
        release.wait(2)
        original(project, path)

    monkeypatch.setattr(Project, "save", slow_save)
    window.project.name = "before"
    window.autosave_background()
    try:
        assert started.wait(1)
        window.project.name = "after"
        window.pixel.setChecked(not window.pixel.isChecked())
        app.processEvents()
        assert window.save_worker.isRunning()
        assert result == [(result[0][0], "before")] and result[0][0] != main_thread
    finally:
        release.set()
        window.save_worker.wait()
    assert Project.load(window.autosave_path).name == "before"
    window.flush_save()
    assert Project.load(window.autosave_path).name == "after"


def test_effects_multiple_sounds_and_shortcuts_travel_with_project(tmp_path):
    project = Project.defaults()
    files = []
    for i in range(2):
        path = tmp_path / f"{i}.wav"
        with wave.open(str(path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(b"\0\0" * 100)
        files.append(str(path))
    project.settings["sounds"] = {"idle": files}
    project.settings["shortcuts"] = {"undo": "Alt+Z"}
    project.effects["click"] = [
        project.slots["Arrow"][0].clone(),
        project.slots["Hand"][0].clone(),
    ]
    dest = tmp_path / "test.whalepack"
    project.save(dest)
    for path in files:
        Path(path).unlink()
    restored = Project.load(dest)
    assert len(restored.effects["click"]) == 2
    assert len(restored.settings["sounds"]["idle"]) == 2
    assert all(Path(path).is_file() for path in restored.settings["sounds"]["idle"])
    assert restored.settings["shortcuts"]["undo"] == "Alt+Z"


@pytest.fixture
def runtime_state(app, monkeypatch):
    from whale_cursor import runtime, native

    now, position, role = [100.0], [QPoint(100, 100)], ["Arrow"]
    buttons = {1: False, 2: False, 4: False}
    calls = []
    for name in ("require", "recover_runtime", "reload"):
        monkeypatch.setattr(native, name, lambda: None)
    monkeypatch.setattr(native, "snapshot", lambda: {"values": {}, "scheme": None})
    monkeypatch.setattr(native, "write_snapshot", lambda snap: None)
    monkeypatch.setattr(native, "validate_file", lambda path, size: None)
    monkeypatch.setattr(native, "foreground_exe", lambda: "")
    monkeypatch.setattr(native, "current_role", lambda: role[0])
    monkeypatch.setattr(native, "button_down", lambda key: buttons[key])
    monkeypatch.setattr(
        native,
        "set_live_cursor",
        lambda role, path, size: calls.append((role, path.name)) or True,
    )
    monkeypatch.setattr(runtime.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(runtime.QCursor, "pos", lambda: QPoint(position[0]))
    engine = Engine()
    yield engine, now, position, role, buttons, calls
    if engine.active:
        engine.stop()


@pytest.mark.parametrize("trigger", list(EVENTS))
def test_each_trigger_can_play_its_animation(runtime_state, trigger):
    engine, now, position, role, buttons, calls = runtime_state
    project = Project.defaults()
    project.events["Arrow." + trigger] = [project.slots["Hand"][0].clone()]
    project.settings["direction"] = True
    engine.start(project)
    engine.timer.stop()
    now[0] += 0.1
    if trigger in ("click", "left_click", "double_click", "release", "hold", "drag"):
        buttons[1] = True
    elif trigger == "right_click":
        buttons[2] = True
    elif trigger == "middle_click":
        buttons[4] = True
    if trigger in ("move", "move_start", "move_stop", "right", "drag"):
        position[0] += QPoint(20, 0)
    elif trigger == "left":
        position[0] -= QPoint(20, 0)
    elif trigger == "up":
        position[0] -= QPoint(0, 20)
    elif trigger == "down":
        position[0] += QPoint(0, 20)
    elif trigger == "role_change":
        role[0] = "Hand"
    elif trigger == "idle":
        now[0] += 6
    engine._tick()
    if trigger == "double_click":
        buttons[1] = False
        engine._tick()
        now[0] += 0.1
        buttons[1] = True
        engine._tick()
    elif trigger == "release":
        buttons[1] = False
        engine._tick()
    elif trigger == "hold":
        now[0] += 0.6
        engine._tick()
    elif trigger == "move_stop":
        now[0] += 0.2
        engine._tick()
    assert engine.state == trigger
    assert (
        engine.overlay.cursor_frame
        is engine.project.event_frames("Arrow", trigger)[engine.index]
    )
    assert any(name == "transparent.cur" for _, name in calls)


def test_random_audio_without_cursor_animation(runtime_state, monkeypatch):
    from whale_cursor import runtime

    engine, now, position, role, buttons, calls = runtime_state
    project = Project.defaults()
    engine.start(project)
    engine.timer.stop()
    played, choices = [], []

    class Sound:
        def __init__(self, name):
            self.name = name

        def status(self):
            return runtime.QSoundEffect.Status.Ready

        def play(self):
            played.append(self.name)

        def stop(self):
            pass

        def deleteLater(self):
            pass

    pool = [Sound("a"), Sound("b")]
    engine.sounds = {"click": pool, "idle": pool}

    def choose(pool):
        choices.append(len(pool))
        return pool[len(choices) % 2]

    monkeypatch.setattr(runtime.random, "choice", choose)
    for _ in range(2):
        now[0] += 0.1
        buttons[1] = True
        engine._tick()
        buttons[1] = False
        engine._tick()
    assert set(played) == {"a", "b"} and choices == [2, 2]
    now[0] += 6
    engine._tick()
    now[0] += 0.1
    engine._tick()
    assert len(played) == 3  # idle sounds on entry, not every timer tick


def test_role_scope_and_custom_companion(runtime_state):
    engine, now, position, role, buttons, calls = runtime_state
    project = Project.defaults()
    project.settings["interaction_scope"] = "role"
    project.events["Hand.idle"] = [project.slots["Hand"][0].clone()]
    project.effects["companion"] = [project.slots["Arrow"][0].clone()]
    project.settings["companion"] = True
    project.settings["companion_x"] = 35
    engine.start(project)
    engine.timer.stop()
    now[0] += 6
    engine._tick()
    assert engine.state == "base"
    role[0] = "Hand"
    engine._tick()
    assert engine.state == "idle"
    assert engine.overlay.sprite is not None
    assert engine.overlay.sprite_pos.x() == position[0].x() + 35


def test_no_spurious_move_start_and_click_resets_idle(runtime_state):
    engine, now, position, role, buttons, calls = runtime_state
    project = Project.defaults()
    project.events["Arrow.move_start"] = [project.slots["Hand"][0].clone()]
    project.events["Arrow.idle"] = [project.slots["Hand"][0].clone()]
    engine.start(project)
    engine.timer.stop()
    now[0] += 0.1
    engine._tick()
    assert engine.state == "base"
    now[0] += 6
    buttons[1] = True
    engine._tick()
    assert engine.state == "base"
    buttons[1] = False
    engine._tick()
    now[0] += 6
    engine._tick()
    assert engine.state == "idle"


def test_companion_can_run_without_system_cursor_materials(runtime_state):
    engine, now, position, role, buttons, calls = runtime_state
    project = Project.defaults()
    project.effects["companion"] = [project.slots["Hand"][0].clone()]
    project.slots = {key: [] for key in project.slots}
    project.settings["companion"] = True
    engine.start(project)
    engine.timer.stop()
    now[0] += 0.1
    engine._tick()
    assert engine.overlay.sprite is not None
    assert not calls


def test_common_mode_lists_only_one_animation(window):
    window.group_box.setCurrentIndex(1)
    assert window.slot_list.count() == 1
    window.scope_box.setCurrentIndex(1)
    assert window.slot_list.count() == 17
    window.scope_box.setCurrentIndex(0)
    assert window.slot_list.count() == 1


def test_clearing_common_animation_preserves_hidden_role_assignments(window):
    source = window.frame.clone()
    window.project.events = {
        "Arrow.click": [source.clone()],
        "Hand.click": [source.clone()],
    }
    window.group_box.setCurrentIndex(1)
    window.clear_list()
    assert not window.project.events["Arrow.click"]
    assert window.project.events["Hand.click"]
    window.undo()
    assert window.project.events["Arrow.click"]


def test_small_trial_panel_does_not_overlap(window, app):
    window.resize(1000, 680)
    window.inspector_tabs.setCurrentIndex(1)
    app.processEvents()
    assert window.actual.geometry().bottom() < window.preview.geometry().top()
