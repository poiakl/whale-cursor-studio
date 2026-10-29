from types import SimpleNamespace
from whale_cursor.conditions import Inputs, Context, Resolver, ORDER
from whale_cursor.project import Project
from whale_cursor.codec import encode_cur
from whale_cursor.runtime import Engine


def rule(ident, trigger="hold", button="any", motion="any", role="any", lock=False):
    return dict(
        id=ident, trigger=trigger, button=button, motion=motion, role=role, lock=lock
    )


def resolve(resolver, rules, context, order=None):
    result = resolver.select(
        rules, lambda r: [SimpleNamespace(duration=2000)], context, order
    )
    return result["id"] if result else None


def test_independent_right_hold_left_press_and_text_fallback():
    rules = [
        rule("hold", button="right", lock=True),
        rule("move", button="right", motion="moving", lock=True),
        rule("text", button="right", role="IBeam", lock=True),
        rule("left", "pressed", "left", role="IBeam"),
    ]
    inputs = Inputs(0, (0, 0), dict(left=False, right=False, middle=False))
    resolver = Resolver()
    buttons = dict(left=False, right=True, middle=False)

    def step(t, pos, role="Arrow"):
        return resolve(resolver, rules, inputs.update(t, pos, buttons, role))

    assert step(0, (0, 0)) is None
    assert step(0.6, (0, 0)) == "hold"
    assert step(0.7, (20, 0)) == "move"
    assert step(0.9, (20, 0)) == "hold"
    buttons["left"] = True
    assert step(1, (20, 0), "IBeam") == "text"
    buttons["right"] = False
    assert step(1.1, (20, 0), "IBeam") == "left"
    buttons["left"] = False
    assert step(1.2, (20, 0), "IBeam") is None


def test_left_can_independently_mature_to_long_press():
    inputs = Inputs(0, (0, 0), dict(left=False, right=False, middle=False))
    buttons = dict(left=False, right=True, middle=False)
    inputs.update(0, (0, 0), buttons, "Arrow")
    buttons["left"] = True
    c = inputs.update(0.6, (0, 0), buttons, "Arrow")
    assert c.long == {"right"}
    buttons["right"] = False
    assert inputs.update(0.7, (0, 0), buttons, "Arrow").long == set()
    assert inputs.update(1.2, (0, 0), buttons, "Arrow").long == {"left"}


def test_specific_conditions_then_explicit_order():
    rules = [
        rule("general"),
        rule("text", role="IBeam"),
        rule("left-text", motion="left", role="IBeam"),
        rule("tie", motion="left", role="IBeam"),
    ]
    c = Context(1, "IBeam", True, "left", {"right"}, {"right"})
    assert resolve(Resolver(), rules, c) == "left-text"
    rules[2], rules[3] = rules[3], rules[2]
    assert resolve(Resolver(), rules, c) == "tie"


def test_active_lock_ignores_higher_priority_until_own_button_released():
    resolver = Resolver()
    rules = [rule("hold", button="right", lock=True), rule("left", "pressed", "left")]
    c = Context(1, "Arrow", False, "", {"right"}, {"right"})
    assert resolve(resolver, rules, c) == "hold"
    c.held.add("left")
    assert resolve(resolver, rules, c, ["pressed", *ORDER]) == "hold"
    c.held.remove("right")
    c.long.clear()
    assert resolve(resolver, rules, c, ["pressed", *ORDER]) == "left"


def test_release_does_not_resurrect_old_click_after_hold():
    resolver = Resolver()
    rules = [
        rule("click", "right_click", "right"),
        rule("hold", button="right", lock=True),
    ]
    c = Context(0, "Arrow", False, "", {"right"}, pulses={"right_click": {"right"}})
    assert resolve(resolver, rules, c) == "click"
    c.now = 0.6
    c.pulses = {}
    c.long = {"right"}
    assert resolve(resolver, rules, c) == "hold"
    c.now = 0.7
    c.long = set()
    c.held = set()
    assert resolve(resolver, rules, c) is None


def test_click_uses_recent_direction_and_continuous_rule_requires_movement():
    inputs = Inputs(0, (0, 0), dict(left=False, right=False, middle=False))
    buttons = dict(left=False, right=False, middle=False)
    inputs.update(0.1, (-20, 0), buttons, "Arrow")
    buttons["left"] = True
    c = inputs.update(0.3, (-20, 0), buttons, "Arrow")
    assert not c.moving and c.direction == "left"
    assert resolve(Resolver(), [rule("click", "click", motion="left")], c) == "click"
    assert (
        resolve(
            Resolver(),
            [rule("hold", motion="left")],
            Context(1, "Arrow", False, "left", {"left"}, {"left"}),
        )
        is None
    )


def test_rule_project_roundtrip_and_history(tmp_path):
    from whale_cursor.editing import snapshot

    p = Project.defaults()
    p.settings["animation_rules"] = [
        rule("test", motion="left", role="Wait", lock=True)
    ]
    p.animations["test"] = [p.slots["Arrow"][0].clone()]
    p.settings["sound_modes"] = {"click": "sequence"}
    p.settings["trigger_order"] = ["pressed", *ORDER]
    before = snapshot(p)
    p.animations["test"][0].x = 99
    assert before.animations["test"][0].x != 99
    path = tmp_path / "conditions.whalepack"
    p.save(path)
    q = Project.load(path)
    assert q.settings["animation_rules"] == p.settings["animation_rules"]
    assert q.animations["test"][0].x == 99
    assert q.settings["sound_modes"]["click"] == "sequence"
    assert any(row["group"] == "animations" for row in q.inspect())


def test_sequence_audio_cycles_and_keeps_trigger_positions_separate(app):
    from whale_cursor.runtime import QSoundEffect

    played = []

    class Sound:
        def __init__(self, name):
            self.name = name

        def status(self):
            return QSoundEffect.Status.Ready

        def play(self):
            played.append(self.name)

    e = Engine()
    e.project = Project.defaults()
    e.project.settings["sound_modes"] = {"click": "sequence", "idle": "sequence"}
    e.sounds = {
        "click": [Sound("a"), Sound("b"), Sound("c")],
        "idle": [Sound("x"), Sound("y")],
    }
    for key in ("click", "click", "idle", "click", "click", "idle"):
        e.play_sound(key)
    assert played == ["a", "b", "x", "c", "a", "y"]


def test_high_dpi_frames_are_encoded_directly_at_display_resolution(app):
    import struct

    p = Project.defaults()
    p.size = 64
    size = Engine.pixel_size(p, 1.25)
    assert size == 80
    data = encode_cur(p.slots["Arrow"][0], (size,), pixel=True)
    assert struct.unpack_from("<BB", data, 6) == (80, 80)
    assert Engine.pixel_size(p, 2) == 128
    assert Engine.pixel_size(p, 5) == 256


def test_rule_dialog_import_edit_undo_and_audio_mode(app, monkeypatch, tmp_path):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QDialog, QComboBox, QFileDialog
    from whale_cursor.ui import Window

    w = Window(Project.defaults())
    w.show()
    app.processEvents()
    try:

        def fill():
            dialog = QApplication.activeModalWidget()
            assert isinstance(dialog, QDialog)
            boxes = dialog.findChildren(QComboBox)
            for box, value in zip(boxes, ("hold", "right", "moving", "IBeam")):
                box.setCurrentIndex(box.findData(value))
            dialog.accept()

        QTimer.singleShot(0, fill)
        w.add_condition_rule()
        selected = w.selected_condition()
        assert (
            selected["button"] == "right"
            and selected["motion"] == "moving"
            and selected["role"] == "IBeam"
        )
        filename = tmp_path / "animation.png"
        w.project.slots["Hand"][0].image.save(filename)
        monkeypatch.setattr(
            QFileDialog, "getOpenFileNames", lambda *a, **k: ([str(filename)], "")
        )
        w.import_condition_animation()
        assert len(w.project.animations[selected["id"]]) == 1
        w.edit_condition_animation()
        assert w.group == "animations" and w.frame is not None
        w.duplicate_frame()
        assert len(w.frames) == 2
        w.undo()
        assert w.group == "animations" and len(w.frames) == 1
        w.sound_mode.setCurrentIndex(1)
        assert w.project.settings["sound_modes"]["click"] == "sequence"
        w.delete_condition_rule()
        assert not w.project.settings["animation_rules"]
        w.undo()
        assert w.project.settings["animation_rules"]
    finally:
        w.close()
        w.deleteLater()
        app.processEvents()


def test_one_shot_uses_selected_duration_and_does_not_fall_back_to_old_click():
    rules = [rule("generic", "click"), rule("specific", "click", role="IBeam")]
    resolver = Resolver()
    frames = lambda r: [
        SimpleNamespace(duration=100 if r["id"] == "specific" else 2000)
    ]
    c = Context(0, "IBeam", False, "", pulses={"click": {"left"}})
    assert resolver.select(rules, frames, c)["id"] == "specific"
    c.now = 0.05
    c.pulses = {}
    c.role = "Arrow"
    assert resolver.select(rules, frames, c)["id"] == "specific"
    c.now = 0.2
    assert resolver.select(rules, frames, c) is None


def test_effect_groups_are_independent_and_old_values_migrate(tmp_path):
    import json, zipfile

    p = Project.defaults()
    p.settings["effect_lifetime"] = 2000
    p.settings["click_opacity"] = 0.3
    assert p.settings["trail_lifetime"] == 850
    assert p.settings["trail_opacity"] == 1.0
    original = tmp_path / "new.whalepack"
    p.save(original)
    old = tmp_path / "old.whalepack"
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(old, "w") as dest:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == "project.json":
                manifest = json.loads(data)
                manifest["version"] = 4
                for key in (
                    "click_opacity",
                    "trail_opacity",
                    "companion_opacity",
                    "trail_lifetime",
                ):
                    manifest["settings"].pop(key)
                manifest["settings"]["effect_opacity"] = 0.6
                data = json.dumps(manifest).encode()
            dest.writestr(info.filename, data)
    q = Project.load(old)
    assert q.settings["trail_lifetime"] == 2000
    assert q.settings["trail_opacity"] == 0.6 and q.settings["companion_opacity"] == 0.6
