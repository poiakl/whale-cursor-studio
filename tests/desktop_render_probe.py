"""Windows compositor probe; run separately with QT_QPA_PLATFORM=windows."""

from pathlib import Path
import json
import os
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest
from PIL import Image
from whale_cursor.runtime import Engine
from whale_cursor.codec import Frame


def main():
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    screen = app.primaryScreen()
    output = Path("render-diagnostics") / (
        "scale-" + os.environ.get("QT_SCALE_FACTOR", "1")
    )
    output.mkdir(parents=True, exist_ok=True)
    background = QWidget()
    background.setStyleSheet("background:#202020;")
    area = screen.availableGeometry()
    background.setGeometry(area.adjusted(20, 40, -20, -20))
    background.show()
    background.raise_()
    QTest.qWait(150)
    engine = Engine()
    report = {
        "platform": app.platformName(),
        "dpr": screen.devicePixelRatio(),
        "samples": [],
    }
    try:
        cursor = background.geometry().topLeft() + QPoint(60, 60)
        engine.overlay.cursor_frame = Frame(Image.new("RGBA", (256, 256), "red"), 0, 0)
        engine.overlay.cursor_pos = cursor
        engine.overlay.cursor_size = 64 / screen.devicePixelRatio()
        engine.effect_overlay.settings = {
            "effect_lifetime": 1000,
            "effect_motion": "fade",
            "click_size": 64,
        }
        engine.effect_overlay.effects = {
            "click": [
                (Image.new("RGBA", (256, 256), "green"), 100),
                (Image.new("RGBA", (256, 256), "blue"), 100),
            ]
        }
        engine.effect_overlay.fit_screens()
        geometry = [
            (int(tile.winId()), tile.geometry().getRect())
            for tile in engine.effect_overlay._tiles
        ]
        engine.present_layers(time.monotonic())
        QTest.qWait(100)

        def pixel(point):
            shot = screen.grabWindow(0)
            image = shot.toImage()
            if image.isNull():
                return None
            ratio = shot.devicePixelRatio()
            x, y = (
                round((point.x() - screen.geometry().x()) * ratio),
                round((point.y() - screen.geometry().y()) * ratio),
            )
            color = image.pixelColor(x, y)
            return (color.red(), color.green(), color.blue())

        marker = pixel(cursor + QPoint(5, 5))
        if marker is None or not (
            marker[0] > 180 and marker[1] < 90 and marker[2] < 90
        ):
            report.update(
                status="unavailable",
                reason="Desktop capture did not include the layered cursor marker.",
                marker=marker,
            )
            return 0
        rect = background.geometry()
        targets = [
            rect.topLeft() + QPoint(round(rect.width() * x), round(rect.height() * y))
            for x, y in ((0.35, 0.4), (0.65, 0.55), (0.5, 0.72))
        ]
        for index, point in enumerate(targets):
            engine.effect_overlay.emit_at(point, "custom")
            born = engine.effect_overlay.particles[-1][1]
            for progress in (0.05, 0.15, 0.25, 0.45, 0.65):
                engine.present_layers(born + progress)
                # Multiple samples of the composed surface, including the first
                # presentations, rather than rendering QWidget into a QImage.
                for sample in range(3):
                    QTest.qWait(20)
                    color = pixel(point)
                    report["samples"].append(
                        dict(click=index, age=progress, color=color)
                    )
                    assert color is not None
                    if progress < 0.1:
                        assert color[1] > color[2] + 30, ("first-frame-drop", color)
                    else:
                        assert color[2] > color[0] + 30 and color[2] > color[1] + 30, (
                            "clip-flash-or-drop",
                            progress,
                            color,
                        )
                    assert pixel(cursor + QPoint(5, 5))[0] > 180, (
                        "cursor dropped during click"
                    )
                assert [
                    (int(tile.winId()), tile.geometry().getRect())
                    for tile in engine.effect_overlay._tiles
                ] == geometry
            engine.present_layers(born + 1.1)
            QTest.qWait(60)
            color = pixel(point)
            assert max(abs(value - 32) for value in color) < 10, ("stale-pixels", color)
        screen.grabWindow(0).save(str(output / "native-desktop.png"))
        report["status"] = "passed"
        return 0
    except Exception as error:
        screen.grabWindow(0).save(str(output / "native-failure.png"))
        report.update(status="failed", reason=str(error))
        raise
    finally:
        (output / "report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False))
        engine.overlay.hide()
        engine.effect_overlay.hide()
        engine.overlay.deleteLater()
        engine.effect_overlay.deleteLater()
        background.close()
        app.processEvents()


if __name__ == "__main__":
    raise SystemExit(main())
