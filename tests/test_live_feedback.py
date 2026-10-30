from types import SimpleNamespace
import pytest
from PySide6.QtCore import QPoint, QRect
from whale_cursor import native, runtime
from whale_cursor.project import Project
from whale_cursor.runtime import Engine, Overlay


@pytest.fixture
def live(app, monkeypatch):
    clock, position, role = [100.0], [QPoint(250, 250)], ["Arrow"]
    buttons = {1: False, 2: False, 4: False}
    for name in ("require", "recover_runtime", "reload"):
        monkeypatch.setattr(native, name, lambda: None)
    monkeypatch.setattr(native, "snapshot", lambda: {"values": {}, "scheme": None})
    monkeypatch.setattr(native, "write_snapshot", lambda *a: None)
    monkeypatch.setattr(native, "validate_file", lambda *a: None)
    monkeypatch.setattr(native, "set_live_cursor", lambda *a: True)
    monkeypatch.setattr(native, "shell_surface_active", lambda: False)
    monkeypatch.setattr(native, "cursor_bitmap_size", lambda: None, raising=False)
    monkeypatch.setattr(native, "current_role", lambda: role[0])
    monkeypatch.setattr(native, "button_down", lambda button: buttons[button])
    monkeypatch.setattr(native, "foreground_exe", lambda: "")
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runtime.QCursor, "pos", lambda: position[0])
    engine = Engine()
    yield engine, clock, position, role, buttons
    if engine.active:
        engine.stop()
    engine.overlay.deleteLater()
    engine.effect_overlay.deleteLater()
    app.processEvents()


def test_disabling_click_effect_takes_effect_immediately_and_burst_stays_off(live):
    engine, clock, pos, role, buttons = live
    p = Project.defaults()
    p.settings.update(click_effect="custom", effect_motion="burst")
    p.effects["click"] = [p.slots["Arrow"][0].clone()]
    engine.start(p)
    engine.timer.stop()
    engine._tick()
    clock[0] += 0.1
    buttons[1] = True
    engine._tick()
    assert len(engine.effect_overlay.particles) == 8
    p.settings["click_effect"] = "none"
    engine.sync_feedback(p)
    assert not engine.effect_overlay.particles
    for down in (False, True, False, True):
        buttons[1] = down
        clock[0] += 0.1
        engine._tick()
        assert not engine.effect_overlay.particles
    p.settings["click_effect"] = "ripple"
    engine.sync_feedback(p)
    buttons[1] = False
    clock[0] += 0.1
    engine._tick()
    buttons[1] = True
    clock[0] += 0.1
    engine._tick()
    assert engine.effect_overlay.particles


def test_trail_and_companion_persist_across_app_switch_and_unknown_role(
    live, monkeypatch
):
    engine, clock, position, role, buttons = live
    p = Project.defaults()
    p.settings.update(click_effect="none", trail=True, companion=True)
    p.settings["app_rules"] = [dict(exe="msedge.exe", scheme="edge.whalepack")]
    engine.start(p)
    engine.timer.stop()
    other = (
        Project.defaults()
    )  # its default effect switches must not disable global feedback
    engine.prepare_frames(other)
    engine.rule_cache["edge.whalepack"] = other
    monkeypatch.setattr(native, "foreground_exe", lambda: "msedge.exe")
    clock[0] += 1
    engine._tick()
    assert engine.project is other
    for i in range(20):
        clock[0] += 0.25
        position[0] = QPoint(274 + i * 14, 250)
        role[0] = None if i % 2 else "Hand"
        engine._tick()
        assert engine.overlay.sprite is not None
        assert any(
            particle[2] == "trail" for particle in engine.effect_overlay.particles
        )
    p.settings.update(trail=False, companion=False)
    engine.sync_feedback(p)
    engine._tick()
    assert engine.overlay.sprite is None
    assert not engine.effect_overlay.particles


@pytest.mark.parametrize(
    "physical,ratio,expected",
    [(32, 1, 64), (80, 1.25, 51.2), (96, 1.5, 64 / 1.5), (128, 2, 32)],
)
def test_interaction_matches_project_physical_pixels(
    live, monkeypatch, physical, ratio, expected
):
    engine, clock, position, role, buttons = live
    screen = SimpleNamespace(
        devicePixelRatio=lambda: ratio, geometry=lambda: QRect(0, 0, 1000, 800)
    )
    monkeypatch.setattr(native, "cursor_bitmap_size", lambda: physical, raising=False)
    monkeypatch.setattr(runtime.QApplication, "screenAt", lambda *a: screen)
    p = Project.defaults()
    p.size = 64
    engine.start(p)
    engine.timer.stop()
    engine._tick()
    assert engine.overlay.cursor_size == pytest.approx(expected)
    assert engine.overlay.cursor_size * ratio == pytest.approx(p.size)
    p.settings.update(click_size=256, effect_size=256)
    engine.sync_feedback(p)
    engine._tick()
    assert engine.overlay.cursor_size == pytest.approx(expected)
    assert engine.overlay.cursor_size * ratio == pytest.approx(p.size)


def test_off_emitter_cannot_create_fallback_rings(app):
    layer = Overlay()
    try:
        layer.settings["effect_motion"] = "burst"
        for mode in ("none", "off", None, False):
            layer.emit_at(QPoint(250, 250), mode)
        assert not layer.particles
    finally:
        layer.deleteLater()
