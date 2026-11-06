from whale_cursor.fx import EXAMPLE
from whale_cursor.interaction_presets import save_preset
from whale_cursor.project import Project
from whale_cursor.ui import Window


def test_effect_presets_switch_independently(app):
    project = Project.defaults()
    frame = project.slots["Arrow"][0].clone()
    project.effects["click"] = [frame]
    project.effects["trail"] = [frame]
    project.settings.update(
        click_effect="custom",
        click_size=92,
        effect_motion="keyframes",
        effect_keyframes=EXAMPLE,
        trail=True,
        trail_size=37,
    )
    paths = {
        kind: save_preset(project, kind, kind) for kind in ("click", "motion", "trail")
    }
    window = Window(Project.defaults())
    try:

        def select(kind):
            combo = window.interaction_presets[kind]
            index = combo.findData({"preset": str(paths[kind])})
            assert index >= 0
            combo.setCurrentIndex(index)

        assert window.effect_save_buttons["click"].isHidden()
        assert window.effect_save_buttons["motion"].isHidden()
        select("motion")
        assert window.project.settings["effect_motion"] == "keyframes"
        assert not window.effect_save_buttons["motion"].isHidden()
        select("click")
        assert window.effect_fields["click_size"].value() == 92
        assert (
            window.project.effects["click"][0].image.tobytes() == frame.image.tobytes()
        )
        assert window.project.settings["effect_motion"] == "keyframes"
        assert not window.effect_save_buttons["click"].isHidden()
        select("trail")
        assert window.project.settings["trail_size"] == 37
        assert window.project.effects["trail"]
        window.undo()
        assert (
            not window.project.effects.get("trail") and window.project.effects["click"]
        )
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
