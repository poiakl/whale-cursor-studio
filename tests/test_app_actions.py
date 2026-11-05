import asyncio
import base64
import io
import json
from PIL import Image
from PySide6.QtCore import Qt
import pytest
from whale_cursor.app_actions import AppActions
from whale_cursor.project import Project
from whale_cursor.ui import Window
from whale_cursor.mcp_server import create_server
from test_audio_playback import wav


def png(size=(128, 64)):
    data = io.BytesIO()
    Image.new("RGBA", size, "red").save(data, "PNG")
    return data.getvalue()


@pytest.fixture
def studio(app):
    w = Window(Project.defaults())
    w.prompt_page.size.setValue(64)
    w.prompt_page.columns.setValue(2)
    item = w.prompt_page.leaves["slots.Arrow"]
    item.setText(1, "2")
    item.setCheckState(0, Qt.CheckState.Checked)
    yield w, AppActions(w)
    w.close()
    w.deleteLater()
    app.processEvents()


def test_generation_request_has_actual_reference_and_applies_back_with_undo(studio):
    w, actions = studio
    w.prompt_page.ai.append_reference(Image.new("RGBA", (32, 32), "green"), "角色")
    w.prompt_page.ai.config.key = "SECRET"
    request = actions.get_generation_request()
    assert request["reference_count"] == 1
    assert base64.b64decode(request["references"][0]).startswith(b"\x89PNG")
    assert "SECRET" not in json.dumps(request)
    original = w.frames[0].image.tobytes()
    result = actions.apply_generated_image(
        request["request_id"],
        "slots.Arrow",
        image_base64=base64.b64encode(png()).decode(),
        hotspots={"hotspot": [30, 40]},
    )
    assert result["applied"] and len(w.project.slots["Arrow"]) == 2
    assert all((f.x, f.y) == (30, 40) for f in w.project.slots["Arrow"])
    w.undo()
    assert w.frames[0].image.tobytes() == original


def test_user_edits_or_scheme_switch_invalidate_generation_result(studio):
    w, actions = studio
    request = actions.get_generation_request()
    w.frames[0].x += 1
    with pytest.raises(ValueError, match="用户修改"):
        actions.apply_generated_image(
            request["request_id"],
            "slots.Arrow",
            image_base64=base64.b64encode(png()).decode(),
        )
    request = actions.get_generation_request()
    w.set_project(Project.defaults())
    with pytest.raises(ValueError, match="切换"):
        actions.apply_generated_image(
            request["request_id"],
            "slots.Arrow",
            image_base64=base64.b64encode(png()).decode(),
        )


def test_click_effect_is_applied_to_controls_and_real_renderer(studio):
    w, actions = studio
    keys = [
        {"t": 0, "x": 0, "opacity": 1},
        {"t": 0.5, "x": 50, "y": -20, "opacity": 1},
        {"t": 1, "x": 100, "opacity": 0},
    ]
    actions.set_click_effect(keyframes=keys, effect="ripple", duration_ms=1000, size=80)
    assert w.effect_motion.currentData() == "keyframes"
    assert w.effect_fields["effect_lifetime"].value() == 1000
    previews = actions.preview_click_effect([0.1, 0.5])
    assert previews[1]["pose"]["x"] == 50
    assert all(
        Image.open(io.BytesIO(base64.b64decode(p["image"]))).getbbox() for p in previews
    )
    before = w.project.settings.copy()
    with pytest.raises(ValueError):
        actions.set_click_effect(keyframes=[{"t": 0.5}, {"t": 1}])
    assert w.project.settings == before


def test_audio_mcp_updates_current_ui_and_undoes(studio, tmp_path):
    w, actions = studio
    path = wav(tmp_path / "点击.wav")
    actions.configure_interaction_audio(
        "right_click",
        files=[path],
        mode="sequence",
        wait_finish=True,
        interval_ms=900,
        volume=0.3,
    )
    assert w.sound_mode.currentIndex() == 1 and w.sound_wait.isChecked()
    assert w.sound_interval.value() == 900 and w.sound_list.count() == 1
    assert w.volume.value() == 0.3
    before = json.dumps(w.project.settings, sort_keys=True)
    with pytest.raises(ValueError):
        actions.configure_interaction_audio(
            "click", files=["missing.wav"], interval_ms=20
        )
    assert json.dumps(w.project.settings, sort_keys=True) == before
    w.undo()
    assert not w.project.settings["sounds"].get("right_click")


def test_mcp_surface_only_contains_app_native_actions_and_image_content():
    class Client:
        def call(self, operation, **params):
            assert operation == "get_generation_request"
            return {
                "request_id": "request",
                "tasks": [],
                "references": [base64.b64encode(png()).decode()],
            }

    async def check():
        server = create_server(client=Client())
        tools = await server.list_tools()
        assert {tool.name for tool in tools} == {
            "get_generation_request",
            "apply_generated_image",
            "get_interaction_settings",
            "set_click_effect",
            "preview_click_effect",
            "configure_interaction_audio",
        }
        result = await server.call_tool("get_generation_request", {})
        # FastMCP returns content and optional structured data according to its SDK version.
        content = result[0] if isinstance(result, tuple) else result
        assert any(block.type == "image" for block in content)

    asyncio.run(check())
