import json
from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QPushButton
from PySide6.QtTest import QTest

from whale_cursor.ui import Window
from whale_cursor.codec import Frame, transform
from whale_cursor.project import Project, user_dir
from whale_cursor.hotspots import parse_hotspots
from whale_cursor import preferences
from whale_cursor.characters import load_profile, save_profile, profiles


def payload(text):
    return json.loads(text[text.index("{") :])


def test_editor_menus_transport_and_drawer_placement(app):
    w = Window(Project.defaults())
    try:
        w.resize(1000, 680)
        w.show()
        app.processEvents()
        labels = [
            button.text() for button in w.tabs.widget(0).findChildren(QPushButton)
        ]
        assert "帧操作" not in labels and "撤销" not in labels and "重做" not in labels
        imports = [a.text() for a in w.import_menu().actions()]
        exports = [a.text() for a in w.export_menu().actions()]
        assert imports == ["导入当前动画", "批量匹配动画"]
        assert "导出为 PNG" in exports and "导出当前动画" not in exports
        assert "检查方案" in exports and not any("撤销" in x for x in exports)
        assert w.play.toolTip() and not w.play.icon().isNull()
        width = w.canvas.width()
        for index in range(3):
            w.open_editor_tool(index)
            app.processEvents()
            assert w.editor_drawer.isVisible() and w.canvas.width() == width
            assert w.screen().availableGeometry().contains(w.editor_drawer.geometry())
        w.showMaximized()
        app.processEvents()
        w.open_editor_tool(0)
        app.processEvents()
        assert w.editor_drawer.geometry().right() <= w.frameGeometry().right()
        w.tabs.setCurrentIndex(4)
        assert not w.editor_drawer.isVisible()
    finally:
        w.close()
        w.deleteLater()
        app.processEvents()


def test_live_image_adjustment_is_non_cumulative_atomic_and_undoable(app):
    p = Project.defaults()
    p.slots["Arrow"] = [
        Frame(Image.new("RGBA", (256, 256), color), 128, 128)
        for color in ("red", "blue")
    ]
    w = Window(p)
    try:
        w.show()
        app.processEvents()
        w.open_editor_tool(0)
        app.processEvents()

        def settle_image():
            for _ in range(100):
                if not w.image_timer.isActive():
                    return
                QTest.qWait(20)
            assert not w.image_timer.isActive()

        before = [f.clone() for f in w.frames]
        w.image_fields["scale"].setValue(0.8)
        settle_image()
        first = w.frame.image.tobytes()
        assert first != before[0].image.tobytes()
        w.image_fields["scale"].setValue(0.6)
        settle_image()
        expected = transform(before[0], "scale", factor=0.6, pixel=p.pixel)
        assert w.frame.image.tobytes() == expected.image.tobytes()
        assert len(w.history.past) == 1
        valid = w.frame.image.tobytes()
        w.image_fields["left"].setValue(200)
        settle_image()
        assert w.image_error.text() and w.frame.image.tobytes() == valid
        w.undo()
        assert w.frame.image.tobytes() == before[0].image.tobytes()
        assert w.image_fields["scale"].value() == 1
        w.redo()
        assert w.frame.image.tobytes() == valid
        w.undo()
        w.image_all.setChecked(False)
        w.image_fields["dx"].setValue(10)
        settle_image()
        assert w.frames[0].x == 138 and w.frames[1].x == 128
    finally:
        w.close()
        w.deleteLater()
        app.processEvents()


def test_imported_originals_and_hotspots_survive_save_load(app, tmp_path):
    source = tmp_path / "sequence.png"
    Image.new("RGBA", (256, 256), "orange").save(source)
    w = Window(Project.defaults())
    try:
        w.load_files([str(source)])
        initial = w.frame.image.tobytes()
        w.apply_image_operation("flip_h")
        changes = parse_hotspots({"hotspot": [20, 30]}, w.project, ("slots", "Arrow"))
        w.apply_hotspot_changes(changes)
        w.set_hot(100, 110)
        w.duplicate_frame()
        filename = tmp_path / "project.whalepack"
        w.project.save(filename)
        w.set_project(Project.load(filename))
        w.reset_slot()
        assert len(w.frames) == 1 and (w.frame.x, w.frame.y) == (20, 30)
        assert w.frame.image.tobytes() == initial
        w.group_box.setCurrentIndex(2)
        w.load_files([str(source)])
        w.duplicate_frame()
        w.reset_slot()
        assert len(w.frames) == 1  # decorations are restored too
    finally:
        w.close()
        w.deleteLater()
        app.processEvents()


def test_sheet_settings_remember_only_successful_import(app, monkeypatch, tmp_path):
    from whale_cursor.import_panel import ImportPanel

    file = tmp_path / "sheet.png"
    Image.new("RGBA", (768, 512), "red").save(file)
    seen = []

    def fill(dialog):
        seen.append([dialog.columns.value(), dialog.rows.value()])
        dialog.paths = [str(file)]
        dialog.split.setChecked(True)
        dialog.columns.setValue(3)
        dialog.rows.setValue(2)
        dialog.count.setValue(6)
        dialog.read_selection()
        return dialog.result()

    monkeypatch.setattr(ImportPanel, "exec", fill)
    w = Window(Project.defaults())
    try:
        w.import_current_animation()
        assert len(w.frames) == 6
        assert preferences.load()["sheet_cut"] == dict(columns=3, rows=2, count=6)
        w.import_current_animation()
        assert seen[1] == [3, 2]
        monkeypatch.setattr(
            ImportPanel, "exec", lambda self: QDialog.DialogCode.Rejected
        )
        w.import_current_animation()
        assert preferences.load()["sheet_cut"]["count"] == 6
    finally:
        w.close()
        w.deleteLater()
        app.processEvents()


def test_defaults_group_prefixes_and_group_counts_do_not_change_selection(app):
    w = Window(Project.defaults())
    try:
        page = w.prompt_page
        assert all(page.item_instructions.values())
        assert "长按" in page.group_instructions["互动指针/长按"]
        assert "透明" in payload(page.output.toPlainText())["character"]["extra_prompt"]
        assert "透明" not in page.item_instructions["effects.click"]
        checked = {
            key
            for key, item in page.leaves.items()
            if item.checkState(0) == Qt.CheckState.Checked
        }
        page.groups[("互动指针", "闲置动作")].setText(1, "12")
        page.set_group_instruction("互动指针", "保持同一角色")
        page.set_group_instruction("互动指针/闲置动作", "只做轻微呼吸")
        page.regenerate()
        assert all(
            item.text(1) == "12"
            for key, item in page.leaves.items()
            if key.endswith(".idle")
        )
        assert checked == {
            key
            for key, item in page.leaves.items()
            if item.checkState(0) == Qt.CheckState.Checked
        }
        page.leaves["events.Arrow.idle"].setCheckState(0, Qt.CheckState.Checked)
        page.leaves["events.Arrow.move"].setCheckState(0, Qt.CheckState.Checked)
        page.regenerate()
        tasks = {task["spec"]["id"]: task for task in page.generation_tasks()}
        idle = tasks["events.Arrow.idle"]["spec"]["extra_prompt"]
        move = tasks["events.Arrow.move"]["spec"]["extra_prompt"]
        assert "保持同一角色" not in idle and "只做轻微呼吸" not in idle
        data = payload(tasks["events.Arrow.idle"]["prompt"])
        groups = {item["id"]: item for item in data["groups"]}
        assert groups["互动指针"]["extra_prompt"] == "保持同一角色"
        assert groups["互动指针/闲置动作"]["extra_prompt"] == "只做轻微呼吸"
        assert "只做轻微呼吸" not in move
        assert (
            page.leaves["events.Arrow.idle"].background(0).style()
            != Qt.BrushStyle.NoBrush
        )
    finally:
        w.close()
        w.deleteLater()
        app.processEvents()


def test_character_profile_and_scheme_embed_refs_without_api_keys(app, tmp_path):
    w = Window(Project.defaults())
    try:
        page = w.prompt_page
        assert page.character_panel.isHidden()
        page.character_button.click()
        assert not page.character_panel.isHidden()
        page.character_name.setText("像素角色 A")
        page.style.setText("像素风")
        page.extra.setPlainText("保持参考图的头饰")
        page.size.setValue(512)
        page.set_group_instruction("系统指针", "角色居中")
        page.ai.config.key = "SESSION-SECRET-NOT-SAVED"
        page.ai.append_reference(Image.new("RGBA", (64, 64), "purple"), "参考图")
        page.regenerate()
        assert payload(page.output.toPlainText())["character"]["id"] == "像素角色 A"
        profile = save_profile("像素角色 A", w.project.settings["ai_prompt"])
        source_paths = list(page.reference_paths)
        for filename in source_paths:
            Path(filename).unlink()
        name, config = load_profile(profile)
        assert name == "像素角色 A" and config["size"] == 512
        assert config["group_instructions"]["系统指针"] == "角色居中"
        assert all(Path(filename).is_file() for filename in config["references"])
        assert b"SESSION-SECRET-NOT-SAVED" not in profile.read_bytes()
        assert profile in profiles()
        saved = tmp_path / "portable.whalepack"
        w.project.save(saved)
        restored = Project.load(saved)
        w.set_project(restored)
        w.prompt_page.reload()
        assert len(w.prompt_page.ai.references) == 1
        assert any(
            field["value"] == "保持参考图的头饰"
            for field in w.prompt_page.keyword_fields()
        )
        assert not (user_dir() / "ai-api.json").exists()
    finally:
        w.close()
        w.deleteLater()
        app.processEvents()


def test_background_save_survives_its_editor_being_deleted(app, tmp_path):
    import threading
    from PySide6.QtWidgets import QWidget
    from PySide6.QtCore import QCoreApplication, QEvent
    from whale_cursor.ui import SaveWorker

    owner = QWidget()
    started, finish = threading.Event(), threading.Event()
    project = Project.defaults()
    original_save = Project.save

    # Replace only this immutable snapshot's save routine through a subclass.
    class SlowProject(Project):
        def save(self, path):
            started.set()
            assert finish.wait(15)
            original_save(self, path)

    project.__class__ = SlowProject
    path = tmp_path / "closed-editor.whalepack"
    worker = SaveWorker(project, path, owner)
    worker.start()
    assert started.wait(2)
    owner.deleteLater()
    QCoreApplication.sendPostedEvents(owner, QEvent.Type.DeferredDelete)
    finish.set()
    for _ in range(500):
        if not worker.isRunning():
            break
        QTest.qWait(20)
    assert not worker.isRunning()
    assert len(Project.load(path).slots) == 17
    app.processEvents()
    worker.deleteLater()
