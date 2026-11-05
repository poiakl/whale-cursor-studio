import asyncio
import subprocess
import sys
from pathlib import Path
import pytest
from whale_cursor.project import Project, SLOTS, EVENTS
from whale_cursor.prompt_hierarchy import default_instruction
from whale_cursor.mcp_config import client_config
from whale_cursor.ui import Window
from mcp_protocol_probe import probe


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Live named pipe integration uses Windows; sandbox blocks Unix sockets",
)
def test_mcp_real_stdio(tmp_path):
    assert asyncio.run(probe(tmp_path / "工作目录"))


def test_headless_import_does_not_load_qt():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from whale_cursor.mcp_server import create_server; assert not any(k.startswith('PySide6') for k in sys.modules)",
        ],
        check=True,
    )


def test_interaction_children_inherit_trigger_specific_motion():
    for trigger, title in EVENTS.items():
        for role in SLOTS:
            child = default_instruction(f"events.{role}.{trigger}", role)
            assert child != default_instruction(f"slots.{role}", role)
            assert title in child


def test_mcp_settings_are_live_app_connection(app, tmp_path):
    w = Window(Project.defaults())
    try:
        config = client_config(w.preferences)["mcpServers"]["whale-cursor-studio"]
        assert "--workspace" not in config["args"]
        assert Path(config["args"][0]).name == "mcp_entry.py"
        assert not hasattr(w, "mcp_workspace_field")
        w.mcp_config_toggle.setChecked(True)
        assert not w.mcp_config_view.isHidden()
        assert w.mcp_config_toggle.text() == "收起配置"
        w.show()
        app.processEvents()
        for index in range(3):
            w.open_editor_tool(index)
            app.processEvents()
            assert [b.isChecked() for b in w.editor_tool_buttons] == [
                i == index for i in range(3)
            ]
        w.editor_drawer.hide()
        assert not any(b.isChecked() for b in w.editor_tool_buttons)
    finally:
        w.close()
        w.deleteLater()
        app.processEvents()


def test_frozen_config_points_to_live_companion(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        sys, "executable", str(tmp_path / "便携 程序/WhaleCursorStudio.exe")
    )
    config = client_config({})["mcpServers"]["whale-cursor-studio"]
    assert config["command"].endswith("WhaleCursorMCP.exe")
    assert config["args"] == ["--editor", sys.executable]


def test_open_argument_and_existing_window_message(app, tmp_path):
    import json
    from main import open_argument, apply_open_message

    path = tmp_path / "新方案.whalepack"
    project = Project()
    project.name = "MCP结果"
    project.save(path)
    assert open_argument(["--open", str(path)]) == path
    with pytest.raises(ValueError):
        open_argument(["--open"])
    w = Window(Project.defaults())
    try:
        apply_open_message(w, json.dumps({"open": str(path)}).encode())
        assert w.project.name == "MCP结果"
    finally:
        w.close()
        w.deleteLater()
        app.processEvents()


def test_tab_reload_preserves_pending_prompt_category(app):
    w = Window(Project.defaults())
    try:
        w.tabs.setCurrentIndex(4)
        w.prompt_page.add_keyword("衣服", "尚未等待定时保存")
        w.tabs.setCurrentIndex(0)
        w.tabs.setCurrentIndex(4)
        assert w.prompt_page.keyword_fields()[-1]["value"] == "尚未等待定时保存"
    finally:
        w.close()
        w.deleteLater()
        app.processEvents()


def test_popup_state_survives_real_button_mouse_release(app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    w = Window(Project.defaults())
    try:
        w.show()
        app.processEvents()
        buttons = [
            b for b in w.tabs.widget(0).findChildren(type(w.last_io_button)) if b.menu()
        ]
        assert len(buttons) == 2
        for button in buttons:
            QTest.mouseClick(button, Qt.MouseButton.LeftButton)
            QTest.qWait(20)
            assert button.menu().isVisible()
            assert button.property("menuOpen") is True
            button.menu().hide()
            assert button.property("menuOpen") is False
    finally:
        w.close()
        w.deleteLater()
        app.processEvents()
