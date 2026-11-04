import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest


@pytest.fixture(autouse=True)
def isolated_user(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "user"))
    yield
    # processEvents alone does not drain DeferredDelete outside app.exec().
    # Dispose closed test windows before the next global theme repolishes them.
    from PySide6.QtCore import QCoreApplication, QEvent

    if QCoreApplication.instance() is not None:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture(scope="session")
def app():
    from PySide6.QtWidgets import QApplication
    from whale_cursor.ui import STYLE

    application = QApplication.instance() or QApplication([])
    application.setStyle("Fusion")
    application.setStyleSheet(STYLE)
    yield application
    from PySide6.QtCore import QCoreApplication, QEvent

    application.clipboard().clear()
    for widget in application.topLevelWidgets():
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    application.processEvents()
