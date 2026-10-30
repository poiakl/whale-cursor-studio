import json
import sys
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem
from PySide6.QtCore import Qt
import pytest
from whale_cursor.ui import Window, STYLE
from whale_cursor.project import Project, user_dir
from whale_cursor import preferences, native


def data(prompt):
    return json.loads(prompt[prompt.index("{") :])


def test_each_prompt_row_has_isolated_instructions_and_persists(app, tmp_path):
    w = Window(Project.defaults())
    try:
        page = w.prompt_page
        page.leaves["effects.click"].setCheckState(0, Qt.CheckState.Checked)
        page.set_item_instruction("slots.Arrow", "眨眼两次\n不要箭头")
        page.set_item_instruction("effects.click", "向右弹跳三次")
        page.extra.setPlainText("全部保持同一个角色")
        page.copy_text()
        copied = data(QApplication.clipboard().text())
        assert copied["user_instructions"] == "全部保持同一个角色"
        items = {item["id"]: item for item in copied["items"]}
        assert items["slots.Arrow"]["extra_prompt"] == "眨眼两次\n不要箭头"
        assert items["effects.click"]["extra_prompt"] == "向右弹跳三次"
        for task in page.generation_tasks():
            item = data(task["prompt"])["items"][0]
            assert item["extra_prompt"] == page.item_instructions[item["id"]]
            assert len(data(task["prompt"])["items"]) == 1
        assert page.tree.columnCount() == 3
        assert page.tree.indentation() <= 14
        assert (
            page.tree.itemDelegate()
            .sizeHint(QStyleOptionViewItem(), page.tree.model().index(0, 0))
            .height()
            <= 24
        )
        filename = tmp_path / "rows.whalepack"
        w.project.save(filename)
        restored = Project.load(filename)
        assert (
            restored.settings["ai_prompt"]["item_instructions"]["effects.click"]
            == "向右弹跳三次"
        )
        page.reload()
        assert page.leaves["slots.Arrow"].toolTip(2) == "眨眼两次\n不要箭头"
        page.set_item_instruction("effects.click", "")
        assert "effects.click" not in page.item_instructions
    finally:
        w.save_timer.stop()
        w.close()
        w.deleteLater()
        app.processEvents()


def test_theme_applies_immediately_and_is_app_preference(app):
    w = Window(Project.defaults())
    try:
        before = w.frame.image.tobytes()
        w.change_theme("dark")
        assert "#1e242b" in app.styleSheet().lower()
        assert "#E4ECE8" in app.styleSheet()
        assert preferences.load()["theme"] == "dark"
        assert "theme" not in w.project.settings
        assert w.frame.image.tobytes() == before
        w.change_theme("ocean")
        assert "#edf5fa" in app.styleSheet().lower()
        w.preferences["background"] = "#172839"
        w.change_theme("custom")
        assert "#172839" in app.styleSheet().lower()
    finally:
        w.close()
        w.deleteLater()
        app.setStyleSheet(STYLE)
        app.processEvents()


def test_corrupt_preferences_do_not_crash_theme(app):
    (user_dir() / "preferences.json").write_text(
        '{"theme":[],"background":4,"startup_interaction":"false"}'
    )
    assert preferences.load() == {}
    for name in preferences.THEMES:
        assert preferences.stylesheet(STYLE, name, "#172839")


def test_startup_command_quotes_paths_and_setting_failure_rolls_back(app, monkeypatch):
    monkeypatch.setattr(native.sys, "executable", "/tmp/Program Files/Whale Cursor.exe")
    monkeypatch.setattr(native.sys, "frozen", True, raising=False)
    command = native.startup_command()
    assert command.endswith(" --startup") and '"' in command
    monkeypatch.setattr(native, "WINDOWS", True)
    monkeypatch.setattr(native, "startup_enabled", lambda: False)
    monkeypatch.setattr(native, "recover_runtime", lambda: None)
    monkeypatch.setattr(
        native,
        "set_startup",
        lambda enabled: (_ for _ in ()).throw(OSError("测试失败")),
    )
    w = Window(Project.defaults())
    errors = []
    monkeypatch.setattr(w, "error", errors.append)
    try:
        w.startup_box.setChecked(True)
        assert errors and not w.startup_box.isChecked()
        assert not w.startup_interaction.isEnabled()
    finally:
        w.close()
        w.deleteLater()
        app.processEvents()


def test_login_launch_hides_in_tray_without_starting_unselected_interaction(
    app, monkeypatch
):
    from whale_cursor.ui import QSystemTrayIcon

    w = Window(Project.defaults())
    started = []
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: True)
    monkeypatch.setattr(w, "toggle_engine", lambda: started.append(True))
    try:
        w.show()
        w.launch_at_login()
        assert not w.isVisible() and not started
        w.change_startup_interaction(True)
        w.launch_at_login()
        assert started == [True]
        assert preferences.load()["startup_interaction"] is True
    finally:
        w.close()
        w.deleteLater()
        app.processEvents()


@pytest.mark.skipif(sys.platform != "win32", reason="HKCU Run integration check")
def test_windows_autostart_registers_and_removes_only_its_own_value(monkeypatch):
    import uuid

    monkeypatch.setattr(native, "RUN_NAME", "WhaleCursorStudio-CI-" + uuid.uuid4().hex)
    try:
        native.set_startup(True)
        assert native.startup_enabled()
        native.set_startup(False)
        assert not native.startup_enabled()
    finally:
        native.set_startup(False)
