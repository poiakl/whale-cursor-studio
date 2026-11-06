from pathlib import Path

from whale_cursor.project import Project
from whale_cursor.ui import Window
from whale_cursor.interaction_presets import save_preset
from test_audio_playback import wav


def test_preset_switch_restores_assets_and_can_undo(app, tmp_path):
    project = Project.defaults()
    frame = project.slots["Arrow"][0].clone()
    project.effects["click"] = [frame]
    project.settings.update(click_effect="custom", click_size=92)
    effect_path = save_preset(project, "effects", "大特效")
    path = wav(tmp_path / "点击.wav")
    project.settings["sounds"] = {"click": [path]}
    project.settings["sound_playback"] = {
        "click": {"wait_finish": True, "interval_ms": 500}
    }
    audio_path = save_preset(project, "audio", "慢一点")
    Path(path).unlink()

    window = Window(Project.defaults())
    try:
        for kind, preset in (("effects", effect_path), ("audio", audio_path)):
            combo = window.interaction_presets[kind]
            combo.activated.emit(combo.findData(str(preset)))
        assert window.effect_fields["click_size"].value() == 92
        assert (
            window.project.effects["click"][0].image.tobytes() == frame.image.tobytes()
        )
        assert window.sound_interval.value() == 500
        assert Path(window.audio_files("click")[0]).exists()
        window.undo()
        assert not window.audio_files("click") and window.project.effects["click"]
        window.undo()
        assert not window.project.effects.get("click") and window.project.slots["Arrow"]
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
