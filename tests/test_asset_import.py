import json

from PIL import Image
from PySide6.QtWidgets import QFileDialog

from whale_cursor.asset_import import read_manifest
from whale_cursor.codec import import_images
from whale_cursor.import_panel import ImportPanel
from whale_cursor.project import Project
from whale_cursor.ui import Window
from whale_cursor.widgets import MatchDialog


def test_batch_cross_category_uses_config_and_edited_frame_count(
    app, tmp_path, monkeypatch
):
    project = Project.defaults()
    window = Window(project)
    try:
        assets, items = [], []
        for index, (target, filename) in enumerate(
            (
                ("slots.Arrow", "slots.Arrow.png"),
                ("events.Arrow.idle", "events.Arrow.idle.png"),
                ("effects.trail", "导入图片.png"),
            )
        ):
            path = tmp_path / filename
            Image.new("RGBA", (128, 128), "red").save(path)
            assets.append({"path": str(path), "frames": import_images(path)})
            items.append(
                dict(
                    id=target,
                    filename=filename,
                    frame_count=4,
                    sheet_grid=[2, 2],
                    cell_px=[64, 64],
                    hotspot_px=[11 + index, 20],
                    duration_ms=100,
                    frame_overrides=[dict(frame=2, duration_ms=150)],
                )
            )
        config = tmp_path / "import-settings.json"
        config.write_text(json.dumps({"items": items}), encoding="utf-8")
        dialog = MatchDialog(assets, window)
        monkeypatch.setattr(
            QFileDialog, "getOpenFileName", lambda *a, **k: (str(config), "")
        )
        dialog.choose_config()
        assert len(dialog.mapping) == 3
        dialog.tabs.setCurrentIndex(1)
        dialog.trigger.setCurrentIndex(dialog.trigger.findData("idle"))
        row = dialog.visible_targets.index("events.Arrow.idle")
        dialog.table.item(row, 3).setText("2")
        dialog.tabs.setCurrentIndex(2)
        dialog.accept_import()
        assert len(dialog.result["events.Arrow.idle"]) == 2
        assert dialog.result["events.Arrow.idle"][1].duration == 150
        assert dialog.result["effects.trail"][0].x == 52
        window.apply_matching(dialog.result)
        assert len(project.slots["Arrow"]) == 4 and len(project.effects["trail"]) == 4
        window.undo()
        assert len(
            window.project.slots["Arrow"]
        ) == 1 and not window.project.effects.get("trail")
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_single_panel_and_png_roundtrip(app, tmp_path, monkeypatch):
    window = Window(Project.defaults())
    try:
        path = tmp_path / "slots.Arrow.png"
        Image.new("RGBA", (128, 64), (12, 34, 56, 170)).save(path)
        config = tmp_path / "import-settings.json"
        config.write_text(
            json.dumps(
                {
                    "items": [
                        dict(
                            id="slots.Arrow",
                            frame_count=2,
                            sheet_grid=[2, 1],
                            cell_px=[64, 64],
                            hotspot_px=[8, 12],
                            duration_ms=130,
                        )
                    ]
                }
            )
        )
        dialog = ImportPanel("slots.Arrow", "正常选择", True, window)
        dialog.paths = [str(path)]
        dialog.manifest = read_manifest(config)
        dialog.configure()
        dialog.read_selection()
        assert len(dialog.frames) == 2
        assert dialog.frames[0].x == 32 and dialog.frames[0].duration == 130
        window.apply_import_frames(dialog.frames)
        output = tmp_path / "出图.png"
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName", lambda *a, **k: (str(output), "")
        )
        window.export_png()
        with Image.open(output) as png:
            assert png.size == (512, 256)
            assert png.getpixel((256, 0)) == (12, 34, 56, 170)
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
