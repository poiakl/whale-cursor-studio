import json
from PIL import Image
from PySide6.QtWidgets import QInputDialog, QFileDialog
from whale_cursor.project import Project
from whale_cursor.codec import Frame
from whale_cursor.ui import Window
from whale_cursor.prompts import make_brief, legacy_default_instruction, entries
from whale_cursor.prompt_hierarchy import default_instruction
from whale_cursor.sequence_controls import set_option, state
from whale_cursor.animation_package import extract, merge
from whale_cursor import preferences


def payload(text):
    return json.loads(text[text.index("{") :])


def source_project():
    p = Project.defaults()
    p.slots["Arrow"] = [
        Frame(Image.new("RGBA", (256, 256), color), 40 + i, 50, 100 + 100 * i)
        for i, color in enumerate(("red", "green", "blue", "orange"))
    ]
    return p


def test_prompt_layers_are_referenced_once_and_leaf_is_specific():
    p = Project.defaults()
    brief = make_brief(
        p,
        {"events.Arrow.idle": 4, "events.Hand.idle": 4},
        256,
        4,
        "共同画风",
        item_instructions={"events.Arrow.idle": "父级规定。仅伸出指尖。"},
        group_instructions={"互动指针/闲置动作": "父级规定。"},
    )
    data = payload(brief)
    assert data["character"]["style"] == "共同画风"
    assert data["items"][0]["extra_prompt"] == "仅伸出指尖。"
    assert all(item["parent_id"] == "互动指针/闲置动作" for item in data["items"])
    groups = {group["id"]: group for group in data["groups"]}
    assert len(groups) == 2 and groups["互动指针/闲置动作"]["parent_id"] == "互动指针"
    assert groups["互动指针"]["parent_id"] == "character"
    assert brief.count("父级规定") == 1
    assert all("匹配参考图" not in item["extra_prompt"] for item in data["items"])


def test_prompt_categories_are_global_prose_saved_and_validated(app, tmp_path):
    w = Window(Project.defaults())
    try:
        page = w.prompt_page
        key, value = page.add_keyword()
        assert "分类名称" in key.placeholderText()
        key.setText("衣服")
        value.setPlainText("深蓝色长裙")
        page.copy_text()
        data = payload(page.output.toPlainText())
        assert "衣服" not in data["character"]
        assert "【衣服】深蓝色长裙" in data["character"]["extra_prompt"]
        assert all("衣服" not in item for item in data["items"])
        filename = tmp_path / "keywords.whalepack"
        w.project.save(filename)
        w.set_project(Project.load(filename))
        page.reload()
        assert page.keyword_fields() == [{"key": "衣服", "value": "深蓝色长裙"}]
        other, _ = page.add_keyword("衣服", "不同面料")
        assert page.regenerate()
        other.setText("id")
        assert page.regenerate()
        data = payload(page.output.toPlainText())
        assert data["character"]["id"] == w.project.name
        assert "【id】不同面料" in data["character"]["extra_prompt"]
        other.setText("")
        assert not page.regenerate() and not page.copy.isEnabled()
        assert "分类名称" in page.keyword_error.text()
        other.setText("动作")
        assert page.regenerate()
    finally:
        w.close()
        w.deleteLater()
        app.processEvents()


def test_auto_defaults_migrate_but_user_text_is_preserved(app):
    p = Project.defaults()
    legacy = {}
    for key, path, title, frames, role in entries(p):
        legacy[key] = legacy_default_instruction(key, role, title)
    legacy["slots.Hand"] = "自定义手势，不能改掉。"
    p.settings["ai_prompt"] = dict(item_instructions=legacy, extra="原来的全局要求")
    w = Window(p)
    try:
        page = w.prompt_page
        assert page.item_instructions["slots.Arrow"] == default_instruction(
            "slots.Arrow", "Arrow"
        )
        assert page.item_instructions["slots.Hand"] == "自定义手势，不能改掉。"
        assert any(
            field["value"] == "原来的全局要求" for field in page.keyword_fields()
        )
    finally:
        w.close()
        w.deleteLater()
        app.processEvents()


def test_pingpong_switch_is_idempotent_reversible_and_persistent(tmp_path):
    p = source_project()
    target = "slots.Arrow"
    before = [frame.duration for frame in p.slots["Arrow"]]
    set_option(p, target, "pingpong", True)
    assert [frame.duration for frame in p.slots["Arrow"]] == [
        100,
        200,
        300,
        400,
        300,
        200,
    ]
    set_option(p, target, "pingpong", True)
    assert len(p.slots["Arrow"]) == 6
    set_option(p, target, "reverse", True)
    assert p.slots["Arrow"][0].duration == 400
    set_option(p, target, "uniform_ms", 120)
    set_option(p, target, "speed", 2)
    assert all(frame.duration == 60 for frame in p.slots["Arrow"])
    filename = tmp_path / "controls.whalepack"
    p.save(filename)
    p = Project.load(filename)
    assert state(p, target)["pingpong"] is True
    set_option(p, target, "speed", None)
    set_option(p, target, "uniform_ms", None)
    set_option(p, target, "reverse", False)
    set_option(p, target, "pingpong", False)
    assert [frame.duration for frame in p.slots["Arrow"]] == before
    assert target not in p.sequence_sources


def test_toolbar_switches_show_state_and_cancel_restores_it(app, monkeypatch):
    w = Window(source_project())
    try:
        button = w.sequence_buttons["pingpong"]
        assert button.isCheckable() and not button.isChecked()
        button.click()
        assert button.isChecked() and len(w.frames) == 6
        w.undo()
        assert not button.isChecked() and len(w.frames) == 4
        w.redo()
        assert button.isChecked() and len(w.frames) == 6
        button.click()
        assert not button.isChecked() and len(w.frames) == 4
        monkeypatch.setattr(QInputDialog, "getInt", lambda *a, **k: (150, False))
        w.sequence_buttons["uniform_ms"].click()
        assert not w.sequence_buttons["uniform_ms"].isChecked()
        monkeypatch.setattr(QInputDialog, "getInt", lambda *a, **k: (150, True))
        w.sequence_buttons["uniform_ms"].click()
        assert w.sequence_buttons["uniform_ms"].isChecked()
        assert "已开启" in w.sequence_buttons["uniform_ms"].toolTip()
        w.sequence_buttons["uniform_ms"].click()
        assert [frame.duration for frame in w.frames] == [100, 200, 300, 400]
    finally:
        w.close()
        w.deleteLater()
        app.processEvents()


def test_single_animation_roundtrip_does_not_contain_or_replace_other_frames(tmp_path):
    p = source_project()
    p.events["Hand.idle"] = [p.slots["Arrow"][0].clone()]
    p.settings["app_rules"] = [{"exe": "private.exe", "scheme": "local-only.whalepack"}]
    set_option(p, "slots.Arrow", "pingpong", True)
    clip = extract(p, "slots.Arrow")
    assert set(clip.slots) == {"Arrow"} and not clip.events and not clip.effects
    assert not clip.settings["app_rules"]
    filename = tmp_path / "one.whaleanim"
    clip.save(filename)
    clip = Project.load(filename)
    destination = Project.defaults()
    unchanged = destination.slots["Arrow"][0].image.tobytes()
    merge(destination, clip, "slots.Hand")
    assert destination.slots["Arrow"][0].image.tobytes() == unchanged
    assert len(destination.slots["Hand"]) == 6
    assert state(destination, "slots.Hand")["pingpong"]
    set_option(destination, "slots.Hand", "pingpong", False)
    assert len(destination.slots["Hand"]) == 4


def test_io_labels_and_last_operation_button_are_persistent(app, monkeypatch):
    w = Window(Project.defaults())
    invoked = []
    try:
        monkeypatch.setattr(w, "batch_import", lambda: invoked.append("match"))
        menu = w.import_menu()
        names = [action.text() for action in menu.actions()]
        assert "批量匹配动画" in names and not any("…" in name for name in names)
        assert not any(
            "完整方案" in action.text() for action in w.export_menu().actions()
        )
        next(
            action for action in menu.actions() if action.text() == "批量匹配动画"
        ).trigger()
        w.last_io_button.click()
        assert invoked == ["match", "match"]
        assert w.last_io_button.text() == "批量匹配动画"
        assert preferences.load()["last_io_action"] == "match"
        assert [
            b.text()
            for b in w.tabs.widget(0).findChildren(type(w.last_io_button))
            if b.menu()
        ] == ["导入", "导出"]
    finally:
        w.close()
        w.deleteLater()
        app.processEvents()


def test_rejected_pingpong_does_not_leave_a_false_enabled_indicator(app):
    w = Window(source_project())
    errors = []
    w.error = errors.append
    try:
        w.project.slots["Arrow"] = [w.frame.clone() for _ in range(300)]
        w.rebuild_frames()
        before = len(w.frames)
        w.sequence_buttons["pingpong"].click()
        assert errors and len(w.frames) == before
        assert not w.sequence_buttons["pingpong"].isChecked()
        assert not state(w.project, "slots.Arrow")["pingpong"]
    finally:
        w.close()
        w.deleteLater()
        app.processEvents()


def test_existing_single_animation_package_import_through_ui(
    app, monkeypatch, tmp_path
):
    w = Window(source_project())
    filename = tmp_path / "single.whaleanim"
    try:
        w.pingpong()
        assert "6 帧" in w.slot_list.currentItem().text()
        from whale_cursor.animation_package import extract

        extract(w.project, "slots.Arrow").save(filename)
        saved = Project.load(filename)
        assert set(saved.slots) == {"Arrow"} and len(saved.slots["Arrow"]) == 6
        w.key = "Hand"
        w.rebuild_slots()
        arrow_pixels = w.project.slots["Arrow"][0].image.tobytes()
        monkeypatch.setattr(
            QFileDialog, "getOpenFileName", lambda *a, **k: (str(filename), "")
        )
        w.import_animation()
        assert w.key == "Hand" and len(w.frames) == 6
        assert w.sequence_buttons["pingpong"].isChecked()
        assert w.project.slots["Arrow"][0].image.tobytes() == arrow_pixels
        w.pingpong()
        assert len(w.frames) == 4
    finally:
        w.close()
        w.deleteLater()
        app.processEvents()


def test_quick_close_flushes_pending_keyword_fields(app):
    w = Window(Project.defaults())
    try:
        key, value = w.prompt_page.add_keyword("新关键字", "刚填写的内容")
        assert w.prompt_page.timer.isActive()
        w.close()
        saved = Project.load(w.autosave_path)
        assert {"key": "新关键字", "value": "刚填写的内容"} in saved.settings[
            "ai_prompt"
        ]["keyword_fields"]
        assert not w.prompt_page.timer.isActive()
        assert not w.save_timer.isActive()
    finally:
        w.close()
        w.deleteLater()
        app.processEvents()
