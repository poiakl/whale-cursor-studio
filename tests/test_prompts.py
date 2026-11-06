import json
from PySide6.QtCore import Qt, QPointF, QPoint
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication
from whale_cursor.codec import Frame
from whale_cursor.project import Project, SLOTS
from whale_cursor.prompts import make_brief, PromptPage
from whale_cursor.generation_brief import frame_metadata


def payload(text):
    return json.loads(text[text.index("{") :])


def test_mixed_brief_counts_hotspots_and_sheet_geometry():
    p = Project.defaults()
    f = p.slots["Arrow"][0]
    p.events["Arrow.idle"] = [
        Frame(f.image, 64, 128, 125),
        Frame(f.image, 96, 120, 200),
    ]
    p.effects["click"] = [Frame(f.image, 128, 128, 80)]
    before = p.events["Arrow.idle"][0].x
    brief = make_brief(
        p,
        {"slots.Arrow": 1, "events.Arrow.idle": 7, "effects.click": 9},
        512,
        4,
        "像素鲸鱼",
    )
    data = payload(brief)
    assert data["character"]["style"] == "像素鲸鱼"
    a, idle, click = data["items"]
    assert a["frame_count"] == 1
    assert idle["frame_count"] == 7 and idle["sheet_px"] == [2048, 1024]
    assert idle["unused_cells"] == 1 and "timeline" not in idle
    frames = list(frame_metadata(idle, 7))
    assert frames[0]["hotspot_px"] == [128, 256]
    assert frames[1]["duration_ms"] == 200
    assert idle["frame_overrides"][0]["frame"] == 2
    assert click["sheet_grid"] == [4, 3]
    assert click["duration_ms"] == 80 and "frame_overrides" not in click
    assert "loop" not in click and "effect_playback" not in click
    assert "透明 PNG" in brief and "不要画热点标记" in brief
    assert p.events["Arrow.idle"][0].x == before


def test_page_category_multiselect_copy_and_scheme_persistence(app, tmp_path):
    p = Project.defaults()
    page = PromptPage(lambda: p, lambda: None)
    try:
        systems = page.tree.topLevelItem(0)
        systems.setCheckState(0, Qt.CheckState.Checked)
        page.leaves["events.Arrow.idle"].setCheckState(0, Qt.CheckState.Checked)
        page.leaves["events.Arrow.idle"].setText(1, "12")
        page.leaves["effects.click"].setCheckState(0, Qt.CheckState.Checked)
        page.leaves["effects.click"].setText(1, "6")
        page.size.setValue(256)
        page.copy_text()
        data = payload(QApplication.clipboard().text())
        assert len(data["items"]) == len(SLOTS) + 2
        assert (
            next(i for i in data["items"] if i["id"] == "events.Arrow.idle")[
                "frame_count"
            ]
            == 12
        )
        filename = tmp_path / "ai.whalepack"
        p.save(filename)
        restored = Project.load(filename)
        p.settings = restored.settings
        page.reload()
        assert page.leaves["events.Arrow.idle"].text(1) == "12"
        assert page.leaves["effects.click"].checkState(0) == Qt.CheckState.Checked
        for i in range(page.tree.topLevelItemCount()):
            page.tree.topLevelItem(i).setCheckState(0, Qt.CheckState.Unchecked)
        page.regenerate()
        assert not page.copy.isEnabled()
    finally:
        page.timer.stop()
        page.deleteLater()
        app.processEvents()


def test_prompt_size_ignores_wheel(app):
    p = Project.defaults()
    page = PromptPage(lambda: p, lambda: None)
    try:
        event = QWheelEvent(
            QPointF(10, 10),
            QPointF(10, 10),
            QPoint(),
            QPoint(0, 120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        before = page.size.value()
        QApplication.sendEvent(page.size, event)
        assert page.size.value() == before
    finally:
        page.deleteLater()
        app.processEvents()


def test_extra_user_instructions_are_copied_sent_and_saved(app, tmp_path):
    from whale_cursor.ui import Window

    w = Window(Project.defaults())
    try:
        page = w.prompt_page
        text = "不要箭头，角色向右弹跳三次。\n保留蓝色头饰，最后一次眨眼。"
        page.extra.setPlainText(text)
        page.copy_text()
        copied = payload(QApplication.clipboard().text())
        assert copied["character"]["extra_prompt"].endswith(text)
        tasks = page.generation_tasks()
        assert all(
            payload(t["prompt"])["character"]["extra_prompt"].endswith(text)
            for t in tasks
        )
        saved = tmp_path / "instructions.whalepack"
        w.project.save(saved)
        restored = Project.load(saved)
        assert restored.settings["ai_prompt"]["extra"] == text
        page.reload()
        assert any(field["value"] == text for field in page.keyword_fields())
    finally:
        w.save_timer.stop()
        w.close()
        w.deleteLater()
        app.processEvents()


def test_interaction_scope_limits_bulk_selection_and_keeps_role_choices(app):
    project = Project.defaults()
    project.settings["interaction_scope"] = "role"
    page = PromptPage(lambda: project, lambda: None)
    checked = Qt.CheckState.Checked
    unchecked = Qt.CheckState.Unchecked
    try:
        hand = page.leaves["events.Hand.idle"]
        hand.setCheckState(0, checked)
        parent = page.groups[("互动指针",)]
        assert parent.checkState(0) == Qt.CheckState.PartiallyChecked
        assert page.leaves["events.Arrow.idle"].checkState(0) == unchecked
        page.scope.setCurrentIndex(0)
        assert "events.Hand.idle" not in page.leaves
        parent = page.groups[("互动指针",)]
        parent.setCheckState(0, checked)
        tasks = [
            task
            for task in page.generation_tasks()
            if task["spec"]["id"].startswith("events.")
        ]
        from whale_cursor.project import EVENTS

        assert len(tasks) == len(EVENTS)
        assert all("共用动画" in task["spec"]["purpose"] for task in tasks)
        parent.setCheckState(0, unchecked)
        page.scope.setCurrentIndex(1)
        assert page.leaves["events.Hand.idle"].checkState(0) == checked
        assert page.leaves["events.Arrow.idle"].checkState(0) == unchecked
    finally:
        page.timer.stop()
        page.deleteLater()
        app.processEvents()
