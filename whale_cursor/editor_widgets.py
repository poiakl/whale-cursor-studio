"""Small editor tools that stay beside the canvas without consuming its width."""

from PySide6.QtCore import Qt, QEvent, QPoint, Signal
from PySide6.QtWidgets import (
    QFrame,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
)


class EditorDrawer(QFrame):
    active_changed = Signal(int)

    def __init__(self, owner, anchor):
        super().__init__(owner, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.owner = owner
        self.anchor = anchor
        self.setObjectName("card")
        self.setFixedWidth(310)
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.title = QLabel()
        header.addWidget(self.title, 1)
        close = QPushButton("×")
        close.setFixedWidth(32)
        close.setToolTip("收起面板")
        close.clicked.connect(self.hide)
        header.addWidget(close)
        layout.addLayout(header)
        self.pages = QStackedWidget()
        self.pages.currentChanged.connect(self.page_changed)
        layout.addWidget(self.pages, 1)
        owner.installEventFilter(self)

    def reveal(self, index):
        if self.isVisible() and self.pages.currentIndex() == index:
            self.hide()
            return
        self.pages.setCurrentIndex(index)
        self.title.setText(("图像", "热点", "试用")[index])
        self.place()
        self.show()
        self.raise_()

    def page_changed(self, index):
        self.title.setText(("图像", "热点", "试用")[index])
        if self.isVisible():
            self.active_changed.emit(index)

    def showEvent(self, event):
        super().showEvent(event)
        self.active_changed.emit(self.pages.currentIndex())

    def place(self):
        area = self.owner.screen().availableGeometry()
        origin = self.anchor.mapToGlobal(QPoint(0, 0))
        height = min(max(480, self.anchor.height()), area.height() - 24)
        self.resize(310, height)
        outside = self.owner.frameGeometry().right() + 8
        inward = (
            self.anchor.mapToGlobal(QPoint(self.anchor.width(), 0)).x()
            - self.width()
            - 8
        )
        x = (
            outside
            if not self.owner.isMaximized() and outside + self.width() <= area.right()
            else inward
        )
        self.move(
            max(area.left(), min(x, area.right() - self.width() + 1)),
            max(area.top(), min(origin.y(), area.bottom() - height + 1)),
        )

    def eventFilter(self, obj, event):
        if self.isVisible():
            if event.type() in (
                QEvent.Type.Move,
                QEvent.Type.Resize,
                QEvent.Type.WindowStateChange,
            ):
                if self.owner.isMinimized():
                    self.hide()
                else:
                    self.place()
            elif event.type() in (QEvent.Type.Hide, QEvent.Type.Close):
                self.hide()
        return super().eventFilter(obj, event)

    def hideEvent(self, event):
        self.active_changed.emit(-1)
        callback = getattr(self, "hidden_callback", None)
        if callback:
            callback()
        super().hideEvent(event)
