import json
from PySide6.QtCore import Qt, QPointF, QPoint
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication
from whale_cursor.codec import Frame
from whale_cursor.project import Project, SLOTS
from whale_cursor.prompts import make_brief, PromptPage


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
    assert data["style"] == "像素鲸鱼"
    a, idle, click = data["items"]
    assert a["frame_count"] == 1
    assert idle["frame_count"] == 7 and idle["sheet_px"] == [2048, 1024]
    assert idle["unused_cells"] == 1 and len(idle["timeline"]) == 7
    assert idle["timeline"][0]["hotspot_px"] == [128, 256]
    assert idle["timeline"][1]["duration_ms"] == 200
    assert click["sheet_grid"] == [4, 3] and not click["loop"]
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
        assert copied["user_instructions"] == text
        tasks = page.generation_tasks()
        assert all(payload(t["prompt"])["user_instructions"] == text for t in tasks)
        saved = tmp_path / "instructions.whalepack"
        w.project.save(saved)
        restored = Project.load(saved)
        assert restored.settings["ai_prompt"]["extra"] == text
        page.reload()
        assert page.extra.toPlainText() == text
    finally:
        w.save_timer.stop()
        w.close()
        w.deleteLater()
        app.processEvents()
