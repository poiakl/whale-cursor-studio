import asyncio
import subprocess
import sys
from pathlib import Path
import pytest
from whale_cursor.mcp_core import Workspace
from whale_cursor.project import Project, SLOTS, EVENTS
from whale_cursor.prompt_hierarchy import default_instruction
from whale_cursor.mcp_config import client_config
from whale_cursor.ui import Window
from mcp_protocol_probe import probe


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


def test_workspace_rejects_escape_and_export_symlink(tmp_path):
    root = tmp_path / "work"
    work = Workspace(root)
    work.create_project("p.whalepack", template="built_in")
    with pytest.raises(ValueError):
        work.inspect_project("../p.whalepack")
    with pytest.raises(ValueError):
        work.create_project("p.whalepack")
    out = root / "out"
    out.mkdir()
    outside = tmp_path / "protected.cur"
    outside.write_bytes(b"unchanged")
    try:
        (out / "Arrow.cur").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks not permitted")
    with pytest.raises(ValueError):
        work.export_cursors("p.whalepack", "out", "Arrow")
    assert outside.read_bytes() == b"unchanged"


def test_failed_mcp_edits_leave_project_unchanged(tmp_path):
    work = Workspace(tmp_path)
    work.create_project("p.whalepack", template="built_in")
    path = tmp_path / "p.whalepack"
    before = path.read_bytes()
    with pytest.raises(ValueError):
        work.configure_sequence(
            "p.whalepack", "slots.Arrow", {"reverse": True, "wrong": 1}
        )
    assert path.read_bytes() == before
    with pytest.raises(ValueError):
        work.set_frame_timing("p.whalepack", "slots.Arrow", [0])
    assert path.read_bytes() == before


def test_editor_launch_only_configured_command(tmp_path, monkeypatch):
    editor = tmp_path / "main.py"
    editor.write_text("")
    work = Workspace(tmp_path / "work", editor)
    work.create_project("p.whalepack")
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: calls.append((a, kw)))
    work.open_in_editor("p.whalepack")
    assert calls[0][0][0] == [
        sys.executable,
        str(editor),
        "--open",
        str(work.root / "p.whalepack"),
    ]
    assert "shell" not in calls[0][1]


def test_interaction_children_inherit_trigger_specific_motion():
    for trigger, title in EVENTS.items():
        for role in SLOTS:
            child = default_instruction(f"events.{role}.{trigger}", role)
            assert child != default_instruction(f"slots.{role}", role)
            assert title in child


def test_mcp_settings_snapshot_and_active_buttons(app, tmp_path):
    w = Window(Project.defaults())
    try:
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
        w.preferences["mcp_workspace"] = str(tmp_path / "AI 工作目录")
        w.prompt_page.add_keyword("衣服", "实时未保存要求")
        path = w.prepare_mcp_project()
        saved = Project.load(path)
        assert (
            saved.settings["ai_prompt"]["keyword_fields"][-1]["value"]
            == "实时未保存要求"
        )
        assert path.parent == tmp_path / "AI 工作目录"
        config = client_config(w.preferences)["mcpServers"]["whale-cursor-studio"]
        assert str(path.parent) in config["args"]
        assert Path(config["args"][0]).name == "mcp_entry.py"
        w.mcp_config_toggle.setChecked(True)
        assert not w.mcp_config_view.isHidden()
        assert w.mcp_config_toggle.text() == "收起配置"
    finally:
        w.close()
        w.deleteLater()
        app.processEvents()


def test_frozen_config_has_companion_and_separate_args(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        sys, "executable", str(tmp_path / "便携 程序/WhaleCursorStudio.exe")
    )
    config = client_config({"mcp_workspace": str(tmp_path / "AI 工作目录")})[
        "mcpServers"
    ]["whale-cursor-studio"]
    assert config["command"].endswith("WhaleCursorMCP.exe")
    assert config["args"] == [
        "--workspace",
        str(tmp_path / "AI 工作目录"),
        "--editor",
        sys.executable,
    ]


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
