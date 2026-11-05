import wave
import pytest
from whale_cursor.runtime import Engine, QSoundEffect
from whale_cursor.project import Project
from whale_cursor.ui import Window
from whale_cursor.animation_package import extract, merge


def wav(path):
    with wave.open(str(path), "wb") as f:
        f.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        f.writeframes(b"\x00\x00" * 800)
    return str(path)


class Sound:
    def __init__(self, name, log):
        self.name, self.log, self.playing = name, log, False

    def status(self):
        return QSoundEffect.Status.Ready

    def isPlaying(self):
        return self.playing

    def play(self):
        self.playing = True
        self.log.append(self.name)


def test_wait_finish_has_one_pending_trigger_and_sequence_does_not_skip(
    app, monkeypatch
):
    log = []
    engine = Engine()
    engine.project = Project()
    engine.project.settings["sound_modes"] = {"click": "sequence"}
    a, b, c = [Sound(name, log) for name in "abc"]
    engine.sounds = {"click": [a, b], "right_click": [c]}
    engine.play_sound("click")
    for _ in range(20):
        engine.play_sound("click")
    assert log == ["a"] and engine.sound_indices["click"] == 1
    engine.play_sound("right_click")
    engine.flush_pending_sound()
    assert log == ["a"]
    a.playing = False
    engine.flush_pending_sound()
    assert log == ["a", "c"] and engine.pending_sound is None
    c.playing = False
    engine.play_sound("click")
    assert log == ["a", "c", "b"]


def test_interval_is_per_trigger_and_busy_requests_do_not_advance_sequence(
    app, monkeypatch
):
    from whale_cursor import runtime

    now = [10.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: now[0])
    engine = Engine()
    engine.project = Project()
    log = []
    a, b = Sound("a", log), Sound("b", log)
    engine.sounds = {"click": [a, b]}
    engine.project.settings["sound_modes"] = {"click": "sequence"}
    engine.project.settings["sound_playback"] = {
        "click": {"wait_finish": False, "interval_ms": 500}
    }
    engine.play_sound("click")
    now[0] += 0.2
    engine.play_sound("click")
    assert log == ["a"] and engine.sound_indices["click"] == 1
    now[0] += 0.31
    engine.play_sound("click")
    assert log == ["a", "b"]


def test_audio_options_ui_undo_and_scheme_animation_roundtrip(app, tmp_path):
    w = Window(Project.defaults())
    try:
        w.sound_wait.setChecked(False)
        w.sound_interval.setValue(750)
        assert w.project.settings["sound_playback"]["click"] == {
            "wait_finish": False,
            "interval_ms": 750,
        }
        w.sound_trigger.setCurrentIndex(1)
        assert w.sound_wait.isChecked() and w.sound_interval.value() == 0
        w.sound_trigger.setCurrentIndex(0)
        assert not w.sound_wait.isChecked() and w.sound_interval.value() == 750
        w.undo()
        assert w.sound_interval.value() == 0
        w.redo()
        clip = (
            extract(w.project, "effects.click")
            if w.project.effects.get("click")
            else None
        )
        w.project.effects["click"] = [w.project.slots["Arrow"][0].clone()]
        clip = extract(w.project, "effects.click")
        name = tmp_path / "sound.whaleanim"
        clip.save(name)
        restored = Project.load(name)
        destination = Project.defaults()
        merge(destination, restored, "events.Hand.hold")
        assert destination.settings["sound_playback"]["hold"]["interval_ms"] == 750
    finally:
        w.close()
        w.deleteLater()
        app.processEvents()


def test_invalid_saved_audio_options_rejected(tmp_path):
    p = Project()
    p.settings["sound_playback"] = {"click": {"wait_finish": "yes"}}
    path = tmp_path / "bad.whalepack"
    p.save(path)
    with pytest.raises(ValueError, match="播完再播"):
        Project.load(path)


def test_continuous_audio_repeats_at_interval_and_drops_pending_on_stop(
    app, monkeypatch
):
    from PySide6.QtCore import QPoint
    from whale_cursor import runtime, native

    now = [20.0]
    position = [QPoint(100, 100)]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(runtime.QCursor, "pos", lambda: position[0])
    for name in ("require", "reload"):
        monkeypatch.setattr(native, name, lambda: None)
    monkeypatch.setattr(native, "recover_runtime", lambda: False)
    monkeypatch.setattr(native, "validate_file", lambda *args: None)
    monkeypatch.setattr(native, "snapshot", lambda: {"values": {}, "scheme": None})
    monkeypatch.setattr(native, "write_snapshot", lambda *args: None)
    monkeypatch.setattr(native, "button_down", lambda *args: False)
    monkeypatch.setattr(native, "current_role", lambda: "Arrow")
    monkeypatch.setattr(native, "shell_surface_active", lambda: False)
    monkeypatch.setattr(native, "foreground_exe", lambda: "")
    monkeypatch.setattr(native, "set_live_cursor", lambda *args: True)
    project = Project.defaults()
    project.settings["sound_playback"] = {
        "move": {"wait_finish": True, "interval_ms": 200}
    }
    engine = Engine()
    engine.start(project)
    engine.timer.stop()
    log = []
    sound = Sound("move", log)
    sound.stop = lambda: None
    sound.deleteLater = lambda: None
    engine.sounds = {"move": [sound]}

    def step(t, moving):
        now[0] = t
        if moving:
            position[0] += QPoint(3, 0)
        engine._tick()

    try:
        step(20.01, True)
        sound.playing = False
        step(20.10, True)
        assert log == ["move"]
        step(20.25, True)
        assert log == ["move", "move"]
        step(20.50, True)
        assert engine.pending_sound == "move"
        step(21.0, False)
        assert engine.pending_sound is None
        sound.playing = False
        engine.flush_pending_sound()
        assert len(log) == 2
    finally:
        engine.stop()


def test_clear_legacy_click_audio_updates_live_pool_and_can_undo(
    app, tmp_path, monkeypatch
):
    w = Window(Project.defaults())
    try:
        source = wav(tmp_path / "legacy.wav")
        w.project.settings["sound"] = source
        engine = w.engine
        engine.base_project = w.project.clone()
        engine.project = engine.base_project
        engine.prepare_sounds(engine.project)
        engine.active = True
        monkeypatch.setattr(engine, "sync_feedback", lambda project: False)
        w.clear_sounds()
        assert engine.project.settings["sound"] == ""
        assert not engine.sounds["click"]
        engine.active = False
        w.undo()
        assert w.project.settings["sound"] == source
    finally:
        w.engine.active = False
        w.close()
        w.deleteLater()
        app.processEvents()
