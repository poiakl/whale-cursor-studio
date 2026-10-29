from __future__ import annotations
import time
from pathlib import Path
from PIL import Image
from PySide6.QtCore import Qt, Signal, QTimer, QSize, QPointF, QRectF, QUrl, QMimeData
from PySide6.QtGui import (
    QColor,
    QPainter,
    QPen,
    QCursor,
    QDrag,
    QIcon,
    QAction,
    QDesktopServices,
)
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QAbstractItemView,
    QComboBox,
    QSpinBox,
    QDoubleSpinBox,
    QCheckBox,
    QTabWidget,
    QFrame,
    QFileDialog,
    QMessageBox,
    QInputDialog,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QSystemTrayIcon,
    QMenu,
    QScrollArea,
    QLayout,
    QLineEdit,
)
from .codec import import_images, split_sheet, transform, SIZES
from .project import Project, SLOTS, EVENTS, user_dir, event_key
from .runtime import Engine, pixmap
from . import native
from .editing import History, encode_clipboard, decode_clipboard
from .widgets import AnimatedThumb, MatchDialog, InspectionDialog

STYLE = """
QWidget {font-family:"Microsoft YaHei UI","Noto Sans CJK SC","Segoe UI";font-size:13px;color:#24413D;}
QMainWindow,QDialog {background:#F4F7F5;}
QLabel#brand {font-size:25px;font-weight:650;}
QLabel#muted {color:#7A8D85;font-size:12px;}
QLabel#title {font-size:19px;font-weight:600;}
QFrame#card,QGroupBox {background:white;border:1px solid #E1E8E2;border-radius:12px;}
QGroupBox {margin-top:15px;padding:18px;}QGroupBox::title {subcontrol-origin:margin;left:14px;padding:0 5px;}
QPushButton {background:#FFF;border:1px solid #DCE5DE;border-radius:7px;padding:8px 13px;min-height:18px;}
QPushButton:hover {background:#EAF4EE;border-color:#A2CDBB;}
QPushButton:pressed {background:#D5EADD;}QPushButton:disabled {color:#A7B2AB;background:#F5F6F5;}
QPushButton#primary {background:#347D68;color:white;border:1px solid #347D68;}QPushButton#primary:hover {background:#276C58;}
QTabWidget::pane {border:0;}QTabBar::tab {padding:11px 23px;border-bottom:2px solid transparent;color:#7B8C83;}
QTabBar::tab:selected {color:#286C55;border-bottom:2px solid #347D68;font-weight:600;}
QListWidget {background:white;border:0;outline:0;border-radius:8px;}QListWidget::item {padding:6px;border-radius:7px;margin:2px;}
QListWidget::item:selected {background:#E4F1E9;color:#215C48;}QListWidget::item:hover {background:#F0F6F2;}
QSpinBox,QDoubleSpinBox,QComboBox,QLineEdit,QTextEdit {background:white;border:1px solid #DCE5DE;border-radius:6px;padding:6px;min-height:20px;}
QCheckBox {spacing:8px;}QCheckBox::indicator {width:16px;height:16px;}
QScrollArea {background:transparent;border:0;}QStatusBar {background:#EAF1EC;color:#61786A;}
QToolTip {background:#24413D;color:white;border:0;padding:6px;}
"""
FILTER = (
    "图片与指针 (*.png *.gif *.webp *.bmp *.jpg *.jpeg *.cur *.ani *.ico);;所有文件 (*)"
)


def label(text, kind=None):
    w = QLabel(text)
    if kind:
        w.setObjectName(kind)
    w.setWordWrap(True)
    return w


def card():
    w = QFrame()
    w.setObjectName("card")
    layout = QVBoxLayout(w)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(12)
    return w, layout


class FrameList(QListWidget):
    """Move the logical sequence, rather than only repositioning icons on the viewport."""

    move_requested = Signal(int, int)
    mime_type = "application/x-whale-frame"

    def startDrag(self, actions):
        if self.currentRow() < 0:
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(self.mime_type, str(self.currentRow()).encode())
        drag.setMimeData(mime)
        drag.setPixmap(self.currentItem().icon().pixmap(52, 52))
        drag.exec(Qt.DropAction.MoveAction)

    def dragEnterEvent(self, event):
        if event.source() is self and event.mimeData().hasFormat(self.mime_type):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)
        index = self.indexAt(event.position().toPoint())
        row = index.row()
        self._drop_boundary = (
            self.count()
            if row < 0
            else row + (event.position().x() > self.visualRect(index).center().x())
        )
        self.viewport().update()

    def dragLeaveEvent(self, event):
        self._drop_boundary = None
        self.viewport().update()
        event.accept()

    def paintEvent(self, event):
        super().paintEvent(event)
        boundary = getattr(self, "_drop_boundary", None)
        if boundary is None or self.count() == 0:
            return
        rect = self.visualItemRect(self.item(min(boundary, self.count() - 1)))
        x = rect.left() if boundary < self.count() else rect.right() + 2
        painter = QPainter(self.viewport())
        painter.setPen(QPen(QColor("#347D68"), 3))
        painter.drawLine(x, rect.top() + 4, x, rect.bottom() - 4)
        painter.end()

    def dropEvent(self, event):
        if event.source() is not self or not event.mimeData().hasFormat(self.mime_type):
            event.ignore()
            return
        source = int(bytes(event.mimeData().data(self.mime_type)))
        point = event.position().toPoint()
        index = self.indexAt(point)
        target = index.row()
        if target < 0:
            target = self.count()
        elif point.x() > self.visualRect(index).center().x():
            target += 1
        event.acceptProposedAction()
        self._drop_boundary = None
        self.viewport().update()
        self.move_requested.emit(source, target)


class Canvas(QWidget):
    hotspot = Signal(int, int)
    imported = Signal(list)
    edit_started = Signal()
    edit_finished = Signal()

    def __init__(self):
        super().__init__()
        self.frame = None
        self.pixel = True
        self.background = "checker"
        self.zoom = 1.0
        self.setMinimumSize(220, 220)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def area(self):
        n = min(self.width() - 36, self.height() - 36) * self.zoom
        return QRectF((self.width() - n) / 2, (self.height() - n) / 2, n, n)

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#F7F9F7"))
        a = self.area()
        p.save()
        p.setClipRect(a)
        if self.background == "checker":
            p.fillRect(a, QColor("#FFFFFF"))
            step = 16
            for y in range(int(a.top()), int(a.bottom()) + step, step):
                for x in range(int(a.left()), int(a.right()) + step, step):
                    if ((x - int(a.left())) // step + (y - int(a.top())) // step) % 2:
                        p.fillRect(x, y, step, step, QColor("#E8EEE9"))
        else:
            p.fillRect(a, QColor(self.background))
        if self.frame:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, not self.pixel)
            p.drawPixmap(a, pixmap(self.frame.image), QRectF(0, 0, 256, 256))
            x = a.x() + (self.frame.x + 0.5) * a.width() / 256
            y = a.y() + (self.frame.y + 0.5) * a.height() / 256
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(QPen(QColor("white"), 4))
            p.drawLine(QPointF(x - 14, y), QPointF(x + 14, y))
            p.drawLine(QPointF(x, y - 14), QPointF(x, y + 14))
            p.setPen(QPen(QColor("#E65362"), 1.5))
            p.drawLine(QPointF(x - 14, y), QPointF(x + 14, y))
            p.drawLine(QPointF(x, y - 14), QPointF(x, y + 14))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(x, y), 5, 5)
        p.restore()
        p.setPen(QPen(QColor("#DCE5DD"), 1))
        p.drawRect(a)
        p.end()

    def pick(self, pos):
        a = self.area()
        if self.frame and a.contains(pos):
            self.hotspot.emit(
                max(0, min(255, int((pos.x() - a.x()) * 256 / a.width()))),
                max(0, min(255, int((pos.y() - a.y()) * 256 / a.height()))),
            )

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.setFocus()
            self.edit_started.emit()
            self.pick(e.position())

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.edit_finished.emit()
        super().mouseReleaseEvent(e)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self.pick(e.position())

    def keyPressEvent(self, e):
        if not self.frame:
            return
        n = 10 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        dx, dy = {
            Qt.Key.Key_Left: (-n, 0),
            Qt.Key.Key_Right: (n, 0),
            Qt.Key.Key_Up: (0, -n),
            Qt.Key.Key_Down: (0, n),
        }.get(e.key(), (0, 0))
        if dx or dy:
            self.hotspot.emit(
                max(0, min(255, self.frame.x + dx)), max(0, min(255, self.frame.y + dy))
            )
        else:
            super().keyPressEvent(e)

    def wheelEvent(self, e):
        self.zoom = max(
            1, min(6, self.zoom + (0.25 if e.angleDelta().y() > 0 else -0.25))
        )
        self.update()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.imported.emit(
            [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        )


class Preview(QLabel):
    def __init__(self):
        super().__init__("把鼠标移入这里试用\n点击位置以设置的热点为准")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(80)
        self.setStyleSheet("background:#E5F1E9;border-radius:9px;color:#567363;")
        self.setMouseTracking(True)

    def mousePressEvent(self, e):
        self.setText(f"点击：{round(e.position().x())}, {round(e.position().y())}")


class Window(QMainWindow):
    def __init__(self, project=None):
        super().__init__()
        self.setWindowTitle("鲸鱼娘 · 指针工坊")
        self.resize(1240, 880)
        self.setMinimumSize(1000, 680)
        self.autosave_path = user_dir() / "autosave.whalepack"
        self.schemes_dir = user_dir() / "schemes"
        self.schemes_dir.mkdir(exist_ok=True)
        self.project = project or Project.defaults()
        self.group = "slots"
        self.key = "Arrow"
        self.frame_index = 0
        self.trigger = "click"
        self.interaction_trigger = "click"
        self.history = History(30)
        self._hot_gesture = False
        self._history_loading = False
        self.syncing = False
        self.playing = False

        if project is None and self.autosave_path.exists():
            try:
                self.project = Project.load(self.autosave_path)
            except Exception as e:
                QTimer.singleShot(
                    0, lambda msg=str(e): self.error("上次编辑未能恢复：" + msg)
                )
        self.project.migrate_events()
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(700)
        self.save_timer.timeout.connect(
            lambda: self.guard(lambda: self.project.save(self.autosave_path))
        )
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.timeout.connect(self.next_frame)
        self.engine = Engine(self.runtime_error)
        self.engine.overlay.setParent(self, self.engine.overlay.windowFlags())
        self.engine.timer.setParent(self)
        self.engine.sound.setParent(self)
        if native.WINDOWS:
            try:
                native.recover_runtime()
            except Exception as e:
                QTimer.singleShot(
                    0, lambda msg=str(e): self.error("上次互动恢复未完成：" + msg)
                )
        self.build()
        self.build_tray()
        self.rebuild_slots()
        self.refresh()
        self.statusBar().showMessage(
            "修改自动保存 · 保存完整方案用于命名与分享 · 互动总开关控制全部互动功能"
        )

    @property
    def frames(self):
        return getattr(self.project, self.group).get(self.storage_key, [])

    @property
    def frame(self):
        return (
            self.frames[min(self.frame_index, len(self.frames) - 1)]
            if self.frames
            else None
        )

    def error(self, msg):
        QMessageBox.warning(self, "指针工坊", str(msg))

    def guard(self, fn):
        try:
            return fn()
        except Exception as e:
            self.error(str(e))

    def button(self, text, fn, primary=False):
        b = QPushButton(text)
        if primary:
            b.setObjectName("primary")
        b.clicked.connect(lambda _=False: self.guard(fn))
        return b

    def touch(self):
        self.save_timer.start()
        self.refresh_events()

    def remember(self, description="编辑", token=None):
        if self._history_loading:
            return
        self.history.push(self.project, self.selection(), token, time.monotonic())
        self.update_history()

    def undo(self):
        self.stop_play()
        record = self.history.undo(self.project, self.selection())
        if record:
            self.restore_history(record)

    def build(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(24, 20, 24, 12)
        outer.setSpacing(14)
        top = QHBoxLayout()
        brand = QVBoxLayout()
        brand.addWidget(label("鲸鱼娘 / 指针工坊", "brand"))
        brand.addWidget(label("把喜欢的角色，放在每一次点击里。", "muted"))
        top.addLayout(brand)
        top.addStretch()
        self.name_label = label(self.project.name)
        top.addWidget(self.name_label)
        top.addWidget(self.button("保存完整方案…", self.save_scheme))
        top.addWidget(self.button("生成并安装", lambda: self.install(False), True))
        outer.addLayout(top)
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)
        self.build_editor()
        self.build_interactions()
        self.build_schemes()
        self.build_settings()
        bottom = QHBoxLayout()
        bottom.addWidget(label("CUR 静态  /  ANI 动态  /  17 种系统用途", "muted"))
        bottom.addStretch()
        bottom.addWidget(self.button("恢复安装前方案", self.restore))
        outer.addLayout(bottom)
        action = QAction("撤销", self)
        action.setShortcut("Ctrl+Z")
        action.triggered.connect(self.undo)
        self.addAction(action)
        for sequence in ("Ctrl+Y", "Ctrl+Shift+Z"):
            redo = QAction("重做", self)
            redo.setShortcut(sequence)
            redo.triggered.connect(self.redo)
            self.addAction(redo)
        save = QAction("保存方案", self)
        save.setShortcut("Ctrl+S")
        save.triggered.connect(self.save_scheme)
        self.addAction(save)

    def build_editor(self):
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(14)
        self.tabs.addTab(page, "制作指针")
        sidebar, side = card()
        sidebar.setFixedWidth(195)
        self.group_box = QComboBox()
        self.group_box.addItems(["系统指针 · 17 项", "互动指针 · 17 项"])
        self.group_box.currentIndexChanged.connect(self.change_group)
        side.addWidget(self.group_box)
        self.trigger_box = QComboBox()
        self.trigger_box.addItems(list(EVENTS.values()))
        self.trigger_box.currentIndexChanged.connect(self.change_trigger)
        self.trigger_box.hide()
        side.addWidget(self.trigger_box)
        self.slot_list = QListWidget()
        self.slot_list.setIconSize(QSize(32, 32))
        self.slot_list.currentRowChanged.connect(self.select_slot)
        self.slot_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.slot_list.customContextMenuRequested.connect(self.slot_menu)
        side.addWidget(self.slot_list, 1)
        layout.addWidget(sidebar)
        main = QVBoxLayout()
        layout.addLayout(main, 1)
        bar = QHBoxLayout()
        self.edit_title = label("正常选择", "title")
        bar.addWidget(self.edit_title)
        bar.addStretch()
        bar.addWidget(
            self.button("导入图片 / 动画", lambda: self.import_files(False), True)
        )
        bar.addWidget(self.button("追加帧", lambda: self.import_files(True)))
        more = self.button("更多", lambda: None)
        more.setMenu(self.import_menu())
        bar.addWidget(more)
        main.addLayout(bar)
        history_bar = QHBoxLayout()
        frame_ops = self.button("帧操作", lambda: None)
        frame_ops.setMenu(self.frame_menu())
        history_bar.addWidget(frame_ops)
        self.undo_button = self.button("撤销 Ctrl+Z", self.undo)
        self.redo_button = self.button("重做 Ctrl+Y", self.redo)
        history_bar.addWidget(self.undo_button)
        history_bar.addWidget(self.redo_button)
        self.history_label = label("编辑历史 0 / 30", "muted")
        history_bar.addWidget(self.history_label)
        history_bar.addStretch()
        main.addLayout(history_bar)
        self.update_history()
        work = QHBoxLayout()
        main.addLayout(work, 1)
        canvas_card, cc = card()
        cc.setContentsMargins(0, 0, 0, 0)
        self.canvas = Canvas()
        self.canvas.hotspot.connect(self.set_hot)
        self.canvas.edit_started.connect(self.begin_hot_gesture)
        self.canvas.edit_finished.connect(self.end_hot_gesture)
        self.canvas.imported.connect(
            lambda p: self.guard(lambda: self.load_files(p, False))
        )
        cc.addWidget(self.canvas, 1)
        cc.addWidget(label("  点击 / 拖动设热点 · 方向键微调 · 滚轮放大", "muted"))
        work.addWidget(canvas_card, 1)
        props, prop_outer = card()
        props.setFixedWidth(224)
        work.addWidget(props)
        prop_tabs = QTabWidget()
        prop_tabs.setStyleSheet("QTabBar::tab {padding:8px 15px;}")
        prop_outer.addWidget(prop_tabs)
        settings_page = QWidget()
        preview_page = QWidget()
        prop = QVBoxLayout(settings_page)
        pv = QVBoxLayout(preview_page)
        prop.setContentsMargins(0, 8, 0, 0)
        pv.setContentsMargins(0, 8, 0, 0)
        prop.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        pv.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        prop.setSpacing(12)
        pv.setSpacing(12)
        for inner, title in ((settings_page, "参数"), (preview_page, "试用")):
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setWidget(inner)
            area.setMinimumWidth(0)
            prop_tabs.addTab(area, title)
        self.inspector_tabs = prop_tabs
        prop.addWidget(label("点击热点", "title"))
        form = QFormLayout()
        self.hot_x = QSpinBox()
        self.hot_y = QSpinBox()
        for b in (self.hot_x, self.hot_y):
            b.setRange(0, 255)
            b.valueChanged.connect(self.hot_fields)
        form.setVerticalSpacing(12)
        self.hot_x.setMinimumHeight(34)
        self.hot_y.setMinimumHeight(34)
        form.addRow("X", self.hot_x)
        form.addRow("Y", self.hot_y)
        prop.addLayout(form)
        r = QHBoxLayout()
        r.addWidget(self.button("居中", lambda: self.set_hot(128, 128)))
        r.addWidget(self.button("应用到全帧", self.hot_all))
        prop.addLayout(r)
        form = QFormLayout()
        self.size_combo = QComboBox()
        self.size_combo.addItems([str(v) for v in SIZES])
        self.size_combo.setCurrentText(str(self.project.size))
        self.size_combo.currentTextChanged.connect(self.size_changed)
        form.setVerticalSpacing(12)
        self.size_combo.setMinimumHeight(34)
        form.addRow("输出尺寸", self.size_combo)
        self.duration = QSpinBox()
        self.duration.setRange(17, 60000)
        self.duration.setSuffix(" ms")
        self.duration.setMinimumHeight(34)
        self.duration.valueChanged.connect(self.duration_changed)
        form.addRow("本帧停留", self.duration)
        prop.addLayout(form)
        self.pixel = QCheckBox("像素画清晰缩放")
        self.pixel.setChecked(self.project.pixel)
        self.pixel.toggled.connect(self.pixel_changed)
        prop.addWidget(self.pixel)
        self.actual = QLabel()
        self.actual.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.actual.setMinimumHeight(100)
        pv.addWidget(label("输出尺寸预览", "title"))
        pv.addWidget(self.actual)
        self.preview = Preview()
        pv.addWidget(self.preview)
        pv.addWidget(
            label(
                "红色十字只用于标记，不会写入指针。大于 96px 的缩略图会缩小显示。",
                "muted",
            )
        )
        pv.addStretch()
        prop.addWidget(self.button("仅安装当前用途", lambda: self.install(True)))
        prop.addStretch()
        prop.addWidget(self.button("还原内置图片", self.reset_slot))
        frame_card, fc = card()
        fc.setContentsMargins(12, 10, 12, 10)
        main.addWidget(frame_card)
        frame_top = QHBoxLayout()
        self.play = self.button("▶ 播放", self.toggle_play)
        frame_top.addWidget(self.play)
        frame_top.addWidget(self.button("上一帧", lambda: self.step(-1)))
        frame_top.addWidget(self.button("下一帧", lambda: self.step(1)))
        self.frame_info = label("1 帧", "muted")
        frame_top.addWidget(self.frame_info)
        frame_top.addStretch()
        frame_top.addWidget(label("Ctrl+C / V 复制粘贴 · Delete 删除", "muted"))
        fc.addLayout(frame_top)
        self.frame_list = FrameList()
        self.frame_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.frame_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.frame_list.setFlow(QListWidget.Flow.LeftToRight)
        self.frame_list.setWrapping(False)
        self.frame_list.setMovement(QListWidget.Movement.Snap)
        self.frame_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.frame_list.setIconSize(QSize(52, 52))
        self.frame_list.setFixedHeight(99)
        self.frame_list.currentRowChanged.connect(self.select_frame)
        self.frame_list.move_requested.connect(self.reorder_frames)
        fc.addWidget(self.frame_list)
        self.install_frame_shortcuts()

    def import_menu(self):
        menu = QMenu(self)
        for text, fn in [
            ("批量匹配 17 种用途", self.batch_import),
            ("精灵图切帧", self.import_sheet),
            ("图像调整", self.image_tools),
            ("导出当前 CUR / ANI", lambda: self.export(True)),
            ("导出整套 CUR / ANI", lambda: self.export(False)),
            ("检查方案", self.check_project),
            ("撤销最近修改 · Ctrl+Z", self.undo),
        ]:
            menu.addAction(text, lambda checked=False, f=fn: self.guard(f))
        return menu

    def frame_menu(self):
        menu = QMenu(self)
        for text, fn in [
            ("复制帧 · Ctrl+C", self.copy_frames),
            ("粘贴帧 · Ctrl+V", self.paste_frames),
            ("删除帧 · Delete", self.delete_frame),
            ("复制并插入 · Ctrl+D", self.duplicate_frame),
            ("向前移动", lambda: self.move_frame(-1)),
            ("向后移动", lambda: self.move_frame(1)),
            ("反转顺序", self.reverse_frames),
            ("生成往返循环", self.pingpong),
            ("统一帧时长", self.uniform_duration),
            ("整体调速", self.speed_dialog),
        ]:
            menu.addAction(text, lambda checked=False, f=fn: self.guard(f))
        return menu

    def change_group(self, index):
        if self.syncing:
            return
        self.stop_play()
        self.group = "slots" if index == 0 else "events"
        self.trigger_box.setVisible(index == 1)
        self.frame_index = 0
        self.rebuild_slots()

    def rebuild_slots(self):
        self.syncing = True
        self.slot_list.clear()
        for role, name in SLOTS.items():
            frames = (
                self.project.slots.get(role, [])
                if self.group == "slots"
                else self.project.event_frames(role, self.trigger)
            )
            item = QListWidgetItem(
                name + "\n" + (str(len(frames)) + " 帧" if frames else "未设置")
            )
            item.setData(Qt.ItemDataRole.UserRole, role)
            if frames:
                item.setIcon(QIcon(pixmap(frames[0].image)))
            self.slot_list.addItem(item)
        self.slot_list.setCurrentRow(list(SLOTS).index(self.key))
        self.syncing = False
        self.rebuild_frames()

    def select_slot(self, row):
        if self.syncing or row < 0:
            return
        self.stop_play()
        self.key = self.slot_list.item(row).data(Qt.ItemDataRole.UserRole)
        self.frame_index = 0
        self.rebuild_frames()

    def rebuild_frames(self):
        self.syncing = True
        self.frame_list.clear()
        for i, f in enumerate(self.frames):
            item = QListWidgetItem(QIcon(pixmap(f.image)), f"{i + 1} · {f.duration}ms")
            item.setData(Qt.ItemDataRole.UserRole, i)
            item.setSizeHint(QSize(90, 77))
            self.frame_list.addItem(item)
        self.frame_index = max(0, min(self.frame_index, len(self.frames) - 1))
        self.frame_list.setCurrentRow(self.frame_index if self.frames else -1)
        self.syncing = False
        self.refresh()

    def select_frame(self, row):
        if self.syncing or row < 0:
            return
        self.frame_index = row
        self.refresh()

    def reorder_frames(self, source, boundary):
        if (
            self.syncing
            or not 0 <= source < len(self.frames)
            or not 0 <= boundary <= len(self.frames)
        ):
            return
        # The drop position is a gap, so inserting after the source first removes its old slot.
        target = boundary - (1 if source < boundary else 0)
        if target == source:
            return
        self.stop_play()
        self.remember("插入排序")
        self.frames.insert(target, self.frames.pop(source))
        self.frame_index = target
        self.touch()
        QTimer.singleShot(0, self.rebuild_frames)

    def refresh(self):
        self.syncing = True
        f = self.frame
        self.canvas.frame = f
        self.canvas.pixel = self.project.pixel
        self.canvas.update()
        self.edit_title.setText(
            SLOTS[self.key]
            + (" / " + EVENTS[self.trigger] if self.group == "events" else "")
        )
        self.name_label.setText(self.project.name)
        if f:
            self.hot_x.setValue(f.x)
            self.hot_y.setValue(f.y)
            self.duration.setValue(f.duration)
            s = self.project.size
            self.actual.setPixmap(
                pixmap(f.image).scaled(
                    min(s, 96),
                    min(s, 96),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.FastTransformation
                    if self.project.pixel
                    else Qt.TransformationMode.SmoothTransformation,
                )
            )
            scaled = f.image.resize(
                (s, s),
                Image.Resampling.NEAREST
                if self.project.pixel
                else Image.Resampling.LANCZOS,
            )
            self.preview.setCursor(
                QCursor(
                    pixmap(scaled),
                    min(s - 1, round(f.x * s / 256)),
                    min(s - 1, round(f.y * s / 256)),
                )
            )
        else:
            self.actual.clear()
            self.preview.unsetCursor()
        for w in (self.hot_x, self.hot_y, self.duration):
            w.setEnabled(f is not None)
        self.frame_info.setText(
            f"{len(self.frames)} 帧 · 共 {sum(f.duration for f in self.frames) / 1000:.2f} 秒"
        )
        self.syncing = False

    def set_hot(self, x, y):
        if not self.frame or (x, y) == (self.frame.x, self.frame.y):
            return
        if not self._hot_gesture:
            self.remember(
                "热点位置",
                token=("hot", self.group, self.storage_key, self.frame_index),
            )
        self.frame.x = x
        self.frame.y = y
        self.refresh()
        self.touch()

    def hot_fields(self):
        if not self.syncing:
            self.set_hot(self.hot_x.value(), self.hot_y.value())

    def hot_all(self):
        if not self.frame:
            return
        self.remember()
        x, y = self.frame.x, self.frame.y
        for f in self.frames:
            f.x = x
            f.y = y
        self.touch()
        self.refresh()

    def duration_changed(self, value):
        if not self.syncing and self.frame and self.frame.duration != value:
            self.remember(
                "帧时长",
                token=("duration", self.group, self.storage_key, self.frame_index),
            )
            self.frame.duration = value
            self.touch()
            item = self.frame_list.item(self.frame_index)
            if item:
                item.setText(f"{self.frame_index + 1} · {value}ms")
            self.frame_info.setText(
                f"{len(self.frames)} 帧 · 共 {sum(f.duration for f in self.frames) / 1000:.2f} 秒"
            )

    def size_changed(self, value):
        if not self.syncing and self.project.size != int(value):
            self.remember("输出尺寸")
            self.project.size = int(value)
            self.touch()
            self.refresh()

    def pixel_changed(self, value):
        if not self.syncing and self.project.pixel != value:
            self.remember("缩放方式")
            self.project.pixel = value
            self.touch()
            self.refresh()

    def import_files(self, append):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择图片、GIF、连续帧或已有指针", "", FILTER
        )
        if files:
            self.load_files(files, append)

    def load_files(self, files, append=False):
        result = []
        for path in sorted(files):
            result.extend(import_images(Path(path), self.project.pixel))
        if len(result) + (len(self.frames) if append else 0) > 512:
            raise ValueError("每个动作最多 512 帧")
        if not result:
            return
        self.stop_play()
        self.remember()
        getattr(self.project, self.group)[self.storage_key] = (
            self.frames + result if append else result
        )
        self.frame_index = 0
        self.rebuild_slots()
        self.touch()

    def batch_import(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择多张图片或动画，下一步调整 17 项匹配", "", FILTER
        )
        if not files:
            return
        assets = []
        errors = []
        for path in files:
            try:
                frames = import_images(Path(path), self.project.pixel)
                if frames:
                    assets.append({"path": path, "frames": frames})
            except Exception as error:
                errors.append(Path(path).name + "：" + str(error))
        if errors:
            self.error("以下文件无法读取，其他文件仍可匹配：\n" + "\n".join(errors))
        if not assets:
            return
        dialog = MatchDialog(assets, self)
        if dialog.exec():
            self.apply_matching(dialog.result_mapping())

    def import_sheet(self):
        file, _ = QFileDialog.getOpenFileName(
            self, "选择精灵图", "", "图片 (*.png *.webp *.bmp)"
        )
        if not file:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("精灵图切帧")
        form = QFormLayout(dlg)
        cols = QSpinBox()
        rows = QSpinBox()
        count = QSpinBox()
        for b in (cols, rows):
            b.setRange(1, 512)
        count.setRange(1, 512)
        cols.setValue(8)
        count.setValue(8)
        form.addRow("列数", cols)
        form.addRow("行数", rows)
        form.addRow("读取帧数", count)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec():
            with Image.open(file) as image:
                frames = split_sheet(
                    image, cols.value(), rows.value(), count.value(), self.project.pixel
                )
            self.remember()
            getattr(self.project, self.group)[self.storage_key] = frames
            self.frame_index = 0
            self.rebuild_slots()
            self.touch()

    def reset_slot(self):
        self.remember()
        if self.group == "slots":
            self.project.slots[self.key] = Project.defaults().slots[self.key]
        else:
            self.project.events.pop(self.storage_key, None)
        self.rebuild_slots()
        self.touch()

    def duplicate_frame(self):
        if not self.frame:
            return
        if len(self.frames) >= 512:
            raise ValueError("最多 512 帧")
        self.remember()
        self.frames.insert(self.frame_index + 1, self.frame.clone())
        self.frame_index += 1
        self.rebuild_frames()
        self.touch()

    def delete_frame(self):
        indices = self.selected_frame_indices()
        if not indices:
            return
        self.stop_play()
        self.remember("删除帧")
        for index in reversed(indices):
            self.frames.pop(index)
        self.frame_index = min(indices)
        self.rebuild_slots()
        self.touch()

    def move_frame(self, delta):
        j = self.frame_index + delta
        if not 0 <= j < len(self.frames):
            return
        self.remember()
        self.frames[self.frame_index], self.frames[j] = (
            self.frames[j],
            self.frames[self.frame_index],
        )
        self.frame_index = j
        self.rebuild_frames()
        self.touch()

    def reverse_frames(self):
        self.remember()
        self.frames.reverse()
        self.rebuild_frames()
        self.touch()

    def pingpong(self):
        if len(self.frames) < 3:
            return
        if 2 * len(self.frames) - 2 > 512:
            raise ValueError("往返后超过 512 帧")
        self.remember()
        self.frames.extend(f.clone() for f in self.frames[-2:0:-1])
        self.rebuild_frames()
        self.touch()

    def uniform_duration(self):
        if not self.frame:
            return
        value, ok = QInputDialog.getInt(
            self, "统一帧时长", "每帧停留（毫秒）", self.frame.duration, 17, 60000
        )
        if ok:
            self.remember()
            for f in self.frames:
                f.duration = value
            self.rebuild_frames()
            self.touch()

    def speed_dialog(self):
        rate, ok = QInputDialog.getDouble(
            self, "整体调速", "速度倍数（2 表示两倍速）", 1, 0.1, 10, 2
        )
        if ok:
            self.remember()
            for f in self.frames:
                f.duration = max(17, min(60000, round(f.duration / rate)))
            self.rebuild_frames()
            self.touch()

    def toggle_play(self):
        if self.playing:
            self.stop_play()
        elif self.frames:
            self.playing = True
            self.play.setText("Ⅱ 暂停")
            self.preview_timer.start(self.frame.duration)

    def stop_play(self):
        self.playing = False
        self.preview_timer.stop()
        self.play.setText("▶ 播放") if hasattr(self, "play") else None

    def next_frame(self):
        if self.playing and self.frames:
            self.frame_index = (self.frame_index + 1) % len(self.frames)
            self.frame_list.setCurrentRow(self.frame_index)
            self.refresh()
            self.preview_timer.start(self.frame.duration)

    def step(self, n):
        self.stop_play()
        if self.frames:
            self.frame_index = (self.frame_index + n) % len(self.frames)
            self.frame_list.setCurrentRow(self.frame_index)
            self.refresh()

    def image_tools(self):
        if not self.frame:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("图像调整")
        form = QFormLayout(dlg)
        op = QComboBox()
        op.addItems(
            [
                "水平镜像",
                "垂直镜像",
                "顺时针旋转 90°",
                "偏移 / 增加留白",
                "缩放图像",
                "裁剪区域",
            ]
        )
        form.addRow("操作", op)
        all_frames = QCheckBox("应用到当前用途的所有帧")
        all_frames.setChecked(True)
        form.addRow(all_frames)
        dx = QSpinBox()
        dy = QSpinBox()
        dx.setRange(-255, 255)
        dy.setRange(-255, 255)
        form.addRow("水平偏移", dx)
        form.addRow("垂直偏移", dy)
        scale = QDoubleSpinBox()
        scale.setRange(0.1, 4)
        scale.setSingleStep(0.1)
        scale.setValue(1)
        form.addRow("缩放比例", scale)
        crop = []
        for name, value in [
            ("裁剪左", 0),
            ("裁剪上", 0),
            ("裁剪右", 256),
            ("裁剪下", 256),
        ]:
            b = QSpinBox()
            b.setRange(0, 256)
            b.setValue(value)
            crop.append(b)
            form.addRow(name, b)
        note = label(
            "热点随图像一起变换。偏移或裁剪前，请先确认点击位置；移出画布的图像内容会被裁掉。",
            "muted",
        )
        form.addRow(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec():
            name = ["flip_h", "flip_v", "rotate", "offset", "scale", "crop"][
                op.currentIndex()
            ]
            targets = (
                list(range(len(self.frames)))
                if all_frames.isChecked()
                else [self.frame_index]
            )
            staged = {
                i: transform(
                    self.frames[i],
                    name,
                    dx=dx.value(),
                    dy=dy.value(),
                    factor=scale.value(),
                    box=tuple(v.value() for v in crop),
                    pixel=self.project.pixel,
                )
                for i in targets
            }
            self.remember()
            for i, f in staged.items():
                self.frames[i] = f
            self.rebuild_frames()
            self.touch()

    def build_interactions(self):
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 10, 0, 0)
        self.tabs.addTab(page, "桌面互动")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        box.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        banner, b = card()
        line = QHBoxLayout()
        texts = QVBoxLayout()
        texts.addWidget(label("桌面互动 · 总开关", "title"))
        self.runtime_status = label("● 已停止")
        self.runtime_status.setStyleSheet("color:#6C7E74;font-weight:600;")
        texts.addWidget(self.runtime_status)
        texts.addWidget(label("开启后在托盘运行；停止时恢复原指针。", "muted"))
        line.addLayout(texts, 1)
        line.addStretch()
        self.runtime_button = self.button("开启桌面互动", self.toggle_engine, True)
        self.runtime_button.setCheckable(True)
        self.runtime_button.setMinimumWidth(146)
        line.addWidget(self.runtime_button)
        b.addLayout(line)
        layout.addWidget(banner)
        columns = QHBoxLayout()
        layout.addLayout(columns)
        actions, ac = card()
        columns.addWidget(actions, 1)
        ac.addWidget(label("互动素材 · 17 种用途", "title"))
        ac.addWidget(
            label(
                "选择触发动作，为每一种系统用途分别配置动画。旧版 7 类素材已保留在正常选择下。",
                "muted",
            )
        )
        self.interaction_trigger_box = QComboBox()
        self.interaction_trigger_box.addItems(list(EVENTS.values()))
        self.interaction_trigger_box.currentIndexChanged.connect(
            self.change_interaction_trigger
        )
        ac.addWidget(self.interaction_trigger_box)
        action_area = QScrollArea()
        action_area.setWidgetResizable(True)
        action_body = QWidget()
        action_body.setStyleSheet("background:white;")
        action_layout = QVBoxLayout(action_body)
        action_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_area.setWidget(action_body)
        action_area.setMinimumHeight(380)
        ac.addWidget(action_area, 1)
        self.event_status = {}
        self.event_thumbs = {}
        for role, name in SLOTS.items():
            row = QHBoxLayout()
            thumb = AnimatedThumb(size=40)
            self.event_thumbs[role] = thumb
            row.addWidget(thumb)
            text = QVBoxLayout()
            text.addWidget(label(name))
            count = label("未设置", "muted")
            self.event_status[role] = count
            text.addWidget(count)
            row.addLayout(text, 1)
            row.addWidget(self.button("导入", lambda r=role: self.import_event(r)))
            row.addWidget(self.button("编辑", lambda r=role: self.edit_event(r)))
            action_layout.addLayout(row)
        effects, ef = card()
        columns.addWidget(effects, 1)
        ef.addWidget(label("效果与响应", "title"))
        form = QFormLayout()
        self.click_effect = QComboBox()
        self.click_effect.addItems(["水波", "花瓣", "关闭"])
        self.click_effect.setCurrentIndex(
            {"ripple": 0, "petals": 1, "none": 2}.get(
                self.project.settings.get("click_effect"), 0
            )
        )
        self.click_effect.currentIndexChanged.connect(
            lambda i: self.setting("click_effect", ["ripple", "petals", "none"][i])
        )
        form.addRow("点击特效", self.click_effect)
        self.idle = QSpinBox()
        self.idle.setRange(1, 3600)
        self.idle.setSuffix(" 秒")
        self.idle.setValue(int(self.project.settings.get("idle_seconds", 5)))
        self.idle.valueChanged.connect(lambda v: self.setting("idle_seconds", v))
        form.addRow("闲置多久后切换", self.idle)
        ef.addLayout(form)
        self.setting_checks = {}
        for key, text in [
            ("trail", "移动时显示装饰拖尾"),
            ("companion", "显示跟随鼠标的角色"),
            ("direction", "按移动方向切换动作"),
            ("speed", "按移动速度调整动画速度"),
        ]:
            check = QCheckBox(text)
            check.setChecked(bool(self.project.settings.get(key)))
            check.toggled.connect(lambda value, k=key: self.setting(k, value))
            self.setting_checks[key] = check
            ef.addWidget(check)
        ef.addWidget(
            label(
                "根据当前系统用途播放对应互动素材；未设置时使用该用途的原动画。手写等特殊用途由应用自身控制，仍支持原生 ANI 安装。",
                "muted",
            )
        )
        audio = QHBoxLayout()
        audio.addWidget(self.button("选择点击音效 / 语音", self.choose_sound))
        audio.addWidget(self.button("清除", lambda: self.setting("sound", "")))
        ef.addLayout(audio)
        self.sound_label = label(
            Path(self.project.settings.get("sound", "")).name or "未设置音频", "muted"
        )
        ef.addWidget(self.sound_label)
        self.volume = QDoubleSpinBox()
        self.volume.setRange(0, 1)
        self.volume.setSingleStep(0.1)
        self.volume.setValue(float(self.project.settings.get("volume", 0.5)))
        self.volume.valueChanged.connect(lambda v: self.setting("volume", v))
        vf = QFormLayout()
        vf.addRow("音量", self.volume)
        ef.addLayout(vf)
        ef.addStretch()
        rules, rl = card()
        layout.addWidget(rules)
        r = QHBoxLayout()
        r.addWidget(label("按应用切换方案", "title"))
        r.addStretch()
        r.addWidget(self.button("添加规则", self.add_rule))
        r.addWidget(self.button("删除选中", self.remove_rule))
        rl.addLayout(r)
        self.rules = QListWidget()
        self.rules.setMaximumHeight(130)
        rl.addWidget(self.rules)
        rl.addWidget(
            label(
                "匹配前台程序的 exe 文件名。切换是临时的，停止互动会恢复；规则中的方案需保留在原位置。",
                "muted",
            )
        )
        self.refresh_rules()
        self.refresh_events()

    def refresh_events(self):
        if not hasattr(self, "event_status"):
            return
        for role, widget in self.event_status.items():
            frames = self.project.event_frames(role, self.interaction_trigger)
            widget.setText(
                (
                    str(len(frames))
                    + " 帧 · "
                    + str(round(sum(f.duration for f in frames) / 1000, 2))
                    + "s"
                )
                if frames
                else "未设置"
            )
            self.event_thumbs[role].set_frames(frames)

    def import_event(self, role):
        files, _ = QFileDialog.getOpenFileNames(
            self, SLOTS[role] + " / " + EVENTS[self.interaction_trigger], "", FILTER
        )
        if not files:
            return
        old = self.selection()
        self.group = "events"
        self.key = role
        self.trigger = self.interaction_trigger
        try:
            self.load_files(files, False)
        finally:
            self.group, self.key, self.frame_index, self.trigger = old
            self.rebuild_slots()
        self.refresh_events()
        self.statusBar().showMessage(
            SLOTS[role] + " 的 " + EVENTS[self.interaction_trigger] + " 已读取"
        )

    def edit_event(self, role, trigger=None):
        if role in EVENTS:
            trigger = role
            role = "Arrow"
        self.stop_play()
        self.group = "events"
        self.key = role
        self.trigger = trigger or self.interaction_trigger
        self.frame_index = 0
        self.syncing = True
        self.group_box.setCurrentIndex(1)
        self.trigger_box.setCurrentIndex(list(EVENTS).index(self.trigger))
        self.trigger_box.show()
        self.syncing = False
        self.rebuild_slots()
        self.tabs.setCurrentIndex(0)

    def setting(self, key, value):
        if self.project.settings.get(key) == value:
            return
        self.remember("互动设置", token=("setting", key))
        self.project.settings[key] = value
        self.touch()
        if hasattr(self, "sound_label"):
            self.sound_label.setText(
                Path(self.project.settings.get("sound", "")).name or "未设置音频"
            )
        if self.engine.active:
            self.statusBar().showMessage("运行中；设置已修改，停止并重新开启后生效")

    def choose_sound(self):
        file, _ = QFileDialog.getOpenFileName(
            self, "选择点击音效或角色语音（PCM WAV）", "", "WAV 音频 (*.wav)"
        )
        if file:
            import wave

            with wave.open(file, "rb") as w:
                if w.getcomptype() != "NONE":
                    raise ValueError("请选择未压缩 PCM WAV")
            self.setting("sound", file)

    def refresh_rules(self):
        if not hasattr(self, "rules"):
            return
        self.rules.clear()
        for r in self.project.settings.get("app_rules", []):
            self.rules.addItem(r["exe"] + "  →  " + Path(r["scheme"]).stem)

    def add_rule(self):
        exe, ok = QInputDialog.getText(
            self, "按应用切换", "程序文件名，例如 msedge.exe"
        )
        if not ok or not exe.strip():
            return
        exe = Path(exe.strip()).name
        if not exe.lower().endswith(".exe"):
            raise ValueError("请输入 exe 文件名")
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择此程序使用的方案",
            str(self.schemes_dir),
            "指针方案 (*.whalepack)",
        )
        if path:
            Project.load(path)
            self.remember("添加应用规则")
            self.project.settings.setdefault("app_rules", []).append(
                {"exe": exe.lower(), "scheme": path}
            )
            self.refresh_rules()
            self.touch()

    def remove_rule(self):
        i = self.rules.currentRow()
        if i >= 0:
            self.remember("删除应用规则")
            self.project.settings["app_rules"].pop(i)
            self.refresh_rules()
            self.touch()

    def toggle_engine(self):
        running = self.engine.active
        self.set_runtime_state("stopping" if running else "starting")
        QApplication.processEvents()
        try:
            if running:
                self.engine.stop()
                self.set_runtime_state("stopped")
                self.statusBar().showMessage("桌面互动已停止，开启前方案已恢复")
            else:
                self.engine.start(self.project)
                self.set_runtime_state("running")
                self.statusBar().showMessage(
                    "桌面互动正在运行 · 总开关控制下方全部互动功能 · 托盘菜单也可停止"
                )
        except Exception:
            self.set_runtime_state("error")
            raise

    def runtime_error(self, message):
        self.set_runtime_state("error")
        self.error("桌面互动已停止：" + message)

    def build_schemes(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        self.tabs.addTab(page, "我的方案")
        pane, p = card()
        layout.addWidget(pane, 1)
        bar = QHBoxLayout()
        bar.addWidget(label("收藏每一种指针风格", "title"))
        bar.addStretch()
        for text, fn in [
            ("保存当前", self.save_scheme),
            ("导入方案", self.import_scheme),
            ("导出方案", self.export_scheme),
            ("复制当前", self.copy_scheme),
        ]:
            bar.addWidget(self.button(text, fn))
        p.addLayout(bar)
        self.scheme_list = QListWidget()
        self.scheme_list.itemDoubleClicked.connect(
            lambda item: self.guard(self.open_scheme)
        )
        p.addWidget(self.scheme_list, 1)
        row = QHBoxLayout()
        row.addWidget(self.button("打开选中方案", self.open_scheme, True))
        row.addWidget(self.button("删除选中方案", self.delete_scheme))
        row.addStretch()
        row.addWidget(
            self.button(
                "打开方案文件夹",
                lambda: QDesktopServices.openUrl(
                    QUrl.fromLocalFile(str(self.schemes_dir))
                ),
            )
        )
        p.addLayout(row)
        p.addWidget(
            label(
                "每个方案包含全部图片、动画、热点和互动设置；WAV 音频随方案保存。双击打开后可继续编辑。",
                "muted",
            )
        )
        self.refresh_schemes()

    def refresh_schemes(self):
        if not hasattr(self, "scheme_list"):
            return
        self.scheme_list.clear()
        for path in sorted(self.schemes_dir.glob("*.whalepack")):
            item = QListWidgetItem(path.stem)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.scheme_list.addItem(item)

    def save_scheme(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("保存完整方案")
        dlg.resize(540, 300)
        layout = QVBoxLayout(dlg)
        layout.addWidget(label("将保存以下内容", "title"))
        layout.addWidget(label(self.save_summary()))
        name_box = QLineEdit(self.project.name)
        form = QFormLayout()
        form.addRow("方案名称", name_box)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        if not dlg.exec() or not name_box.text().strip():
            return
        import re

        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name_box.text().strip()).strip(
            ". "
        )[:100]
        if not name:
            raise ValueError("方案名称无效")
        path = self.schemes_dir / (name + ".whalepack")
        if (
            path.exists()
            and QMessageBox.question(self, "覆盖方案", f"覆盖已有的「{name}」？")
            != QMessageBox.StandardButton.Yes
        ):
            return
        if name != self.project.name:
            self.remember("方案名称")
            self.project.name = name
        self.project.save(path)
        self.touch()
        self.refresh_schemes()
        self.refresh()
        self.statusBar().showMessage(
            "完整方案已保存：" + name + "（图片、动画、热点和互动设置；未改变系统）"
        )

    def copy_scheme(self):
        self.remember("复制方案")
        self.project.name += " 副本"
        self.save_scheme()

    def import_scheme(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入方案", "", "指针方案 (*.whalepack)"
        )
        if path:
            project = Project.load(path)
            dest = self.schemes_dir / (
                Path(path).stem + "_" + time.strftime("%H%M%S") + ".whalepack"
            )
            project.save(dest)
            self.set_project(project)
            self.refresh_schemes()

    def export_scheme(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出完整方案",
            self.project.name + ".whalepack",
            "指针方案 (*.whalepack)",
        )
        if path:
            self.project.save(path)
            self.statusBar().showMessage("完整方案已导出")

    def open_scheme(self):
        item = self.scheme_list.currentItem()
        if item:
            self.set_project(Project.load(item.data(Qt.ItemDataRole.UserRole)))

    def delete_scheme(self):
        item = self.scheme_list.currentItem()
        if (
            item
            and QMessageBox.question(
                self, "删除方案", "删除选中的方案文件？当前编辑内容仍会保留。"
            )
            == QMessageBox.StandardButton.Yes
        ):
            Path(item.data(Qt.ItemDataRole.UserRole)).unlink()
            self.refresh_schemes()

    def set_project(self, project):
        if not self._history_loading:
            self.remember("切换方案")
        project.migrate_events()
        if self.engine.active:
            self.toggle_engine()
        self.stop_play()
        self.project = project
        self.group = "slots"
        self.key = "Arrow"
        self.frame_index = 0
        self.syncing = True
        self.group_box.setCurrentIndex(0)
        self.trigger_box.hide()
        self.size_combo.setCurrentText(str(project.size))
        self.pixel.setChecked(project.pixel)
        self.syncing = False
        self.click_effect.blockSignals(True)
        self.click_effect.setCurrentIndex(
            {"ripple": 0, "petals": 1, "none": 2}.get(
                project.settings.get("click_effect"), 0
            )
        )
        self.click_effect.blockSignals(False)
        self.idle.blockSignals(True)
        self.idle.setValue(int(project.settings.get("idle_seconds", 5)))
        self.idle.blockSignals(False)
        for key, w in self.setting_checks.items():
            w.blockSignals(True)
            w.setChecked(bool(project.settings.get(key)))
            w.blockSignals(False)
        self.volume.blockSignals(True)
        self.volume.setValue(float(project.settings.get("volume", 0.5)))
        self.volume.blockSignals(False)
        self.multi.blockSignals(True)
        self.multi.setChecked(project.multi_size)
        self.multi.blockSignals(False)
        self.sound_label.setText(
            Path(project.settings.get("sound", "")).name or "未设置音频"
        )
        self.rebuild_slots()
        self.refresh_rules()
        self.refresh_events()
        self.touch()

    def build_settings(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        self.tabs.addTab(page, "设置")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        b = QVBoxLayout(body)
        b.setContentsMargins(0, 0, 0, 0)
        pane, p = card()
        b.addWidget(pane)
        p.addWidget(label("编辑与输出", "title"))
        row = QHBoxLayout()
        row.addWidget(label("预览背景"))
        bg = QComboBox()
        bg.addItems(["透明棋盘格", "白色", "深色", "浅绿色"])
        bg.currentIndexChanged.connect(
            lambda i: self.set_background(
                ["checker", "#FFFFFF", "#22332D", "#E4F1E7"][i]
            )
        )
        row.addWidget(bg)
        row.addStretch()
        p.addLayout(row)
        self.multi = QCheckBox("导出多尺寸文件（32 / 48 / 64 / 96 / 128 / 256）")
        self.multi.setChecked(self.project.multi_size)
        self.multi.toggled.connect(self.multi_changed)
        p.addWidget(self.multi)
        p.addWidget(
            label(
                "默认只输出所选尺寸，大小更可控；多尺寸模式由 Windows 选择最合适的图像。ANI 的帧时长按 1/60 秒换算。",
                "muted",
            )
        )
        p.addWidget(self.button("检查图片、热点和帧对齐", self.check_project))
        system, sy = card()
        b.addWidget(system)
        sy.addWidget(label("Windows 指针选项", "title"))
        sy.addWidget(
            label(
                "系统设置独立于图片方案，仅点击应用时修改。颜色与大小请在 Windows 设置中调整。",
                "muted",
            )
        )
        f = QFormLayout()
        self.trails = QSpinBox()
        self.trails.setRange(0, 16)
        self.trails.setSpecialValueText("关闭")
        f.addRow("系统鼠标轨迹长度", self.trails)
        self.shadow = QCheckBox("指针阴影")
        self.locate = QCheckBox("按 Ctrl 显示指针位置")
        self.hide_typing = QCheckBox("输入时隐藏鼠标指针")
        f.addRow(self.shadow)
        f.addRow(self.locate)
        f.addRow(self.hide_typing)
        sy.addLayout(f)
        row = QHBoxLayout()
        row.addWidget(self.button("应用上述选项", self.apply_system))
        row.addWidget(self.button("鼠标属性", native.system_settings))
        row.addWidget(
            self.button(
                "颜色与大小设置",
                lambda: QDesktopServices.openUrl(
                    QUrl("ms-settings:easeofaccess-mousepointer")
                ),
            )
        )
        sy.addLayout(row)
        about, a = card()
        b.addWidget(about)
        a.addWidget(label("关于", "title"))
        a.addWidget(
            label(
                "鲸鱼娘指针工坊 2.1\n原生 CUR / ANI 安装无需常驻；桌面互动在托盘运行，可随时停止。\n方案与导入素材保存在当前用户目录，程序不上传图片或鼠标操作。\n支持 Windows 10 / 11。游戏、远程桌面和部分自绘指针应用可能使用自己的指针。"
            )
        )
        a.addWidget(
            self.button(
                "打开数据文件夹",
                lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(user_dir()))),
            )
        )
        b.addStretch()

    def set_background(self, value):
        self.canvas.background = value
        self.canvas.update()

    def multi_changed(self, value):
        if self.project.multi_size != value:
            self.remember("多尺寸设置")
        self.project.multi_size = value
        self.touch()

    def apply_system(self):
        for key, value in [
            ("trails", self.trails.value()),
            ("shadow", self.shadow.isChecked()),
            ("locate", self.locate.isChecked()),
            ("hide_typing", self.hide_typing.isChecked()),
        ]:
            native.set_mouse_option(key, value)
        self.statusBar().showMessage("系统鼠标选项已应用")

    def check_project(self):
        self.setCursor(Qt.CursorShape.WaitCursor)
        try:
            rows = self.project.inspect()
        finally:
            self.unsetCursor()
        InspectionDialog(rows, self).exec()

    def install(self, only):
        if only and self.group != "slots":
            raise ValueError("互动动作请在桌面互动中开启，不能作为单独系统用途安装")
        if self.engine.active:
            self.toggle_engine()
        self.save_timer.stop()
        self.project.save(self.autosave_path)
        paths = native.install(self.project, self.key if only else None)
        self.statusBar().showMessage(
            f"已安装 {len(paths)} 种指针 · 支持 CUR / ANI · 原方案已备份"
        )

    def restore(self):
        if self.engine.active:
            self.toggle_engine()
        native.restore()
        self.statusBar().showMessage("已恢复安装前方案，编辑内容仍保留")

    def export(self, only):
        folder = QFileDialog.getExistingDirectory(
            self, "选择导出目录（会新建一个文件夹）"
        )
        if not folder:
            return
        dest = Path(folder) / ("WhaleCursors_" + time.strftime("%Y%m%d_%H%M%S"))
        if self.group == "events" and only:
            from .codec import encode_ani, encode_cur

            if not self.frames:
                raise ValueError("当前动作没有图片")
            dest.mkdir()
            animated = len(self.frames) > 1
            (dest / (self.storage_key + (".ani" if animated else ".cur"))).write_bytes(
                encode_ani(self.frames, (self.project.size,), self.project.pixel)
                if animated
                else encode_cur(self.frame, (self.project.size,), self.project.pixel)
            )
        else:
            self.project.export(dest, self.key if only else None)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(dest)))
        self.statusBar().showMessage("已导出到 " + str(dest))

    def build_tray(self):
        source = (self.project.slots.get("Arrow") or Project.defaults().slots["Arrow"])[
            0
        ]
        icon = QIcon(pixmap(source.image))
        self.setWindowIcon(icon)
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("鲸鱼娘 · 指针工坊")
        menu = QMenu()
        menu.addAction("打开指针工坊", self.show_window)
        menu.addAction("开启 / 停止桌面互动", lambda: self.guard(self.toggle_engine))
        menu.addSeparator()
        menu.addAction("停止互动并退出", self.quit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: (
                self.show_window()
                if reason == QSystemTrayIcon.ActivationReason.DoubleClick
                else None
            )
        )
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_app(self):
        def quit_now():
            if self.engine.active:
                self.engine.stop()
            self.project.save(self.autosave_path)
            self.tray.hide()
            QApplication.instance().quit()

        self.guard(quit_now)

    def closeEvent(self, event):
        if self.engine.active and QSystemTrayIcon.isSystemTrayAvailable():
            self.hide()
            event.ignore()
            self.tray.showMessage(
                "指针工坊仍在运行", "双击托盘图标打开，右键可停止互动或退出。"
            )
            return
        try:
            if self.engine.active:
                self.engine.stop()
            self.project.save(self.autosave_path)
            self.tray.hide()
            event.accept()
        except Exception as e:
            self.error(e)
            event.ignore()

    @property
    def storage_key(self):
        return self.key if self.group == "slots" else event_key(self.key, self.trigger)

    def selection(self):
        return (self.group, self.key, self.frame_index, self.trigger)

    def update_history(self):
        if hasattr(self, "undo_button"):
            self.undo_button.setEnabled(bool(self.history.past))
            self.redo_button.setEnabled(bool(self.history.future))
            self.history_label.setText(f"编辑历史 {len(self.history.past)} / 30")

    def redo(self):
        self.stop_play()
        record = self.history.redo(self.project, self.selection())
        if record:
            self.restore_history(record)

    def restore_history(self, record):
        project, selection = record
        self._history_loading = True
        try:
            self.set_project(project)
            self.group, self.key, self.frame_index, self.trigger = selection
            self.group_box.blockSignals(True)
            self.group_box.setCurrentIndex(0 if self.group == "slots" else 1)
            self.group_box.blockSignals(False)
            self.trigger_box.blockSignals(True)
            self.trigger_box.setCurrentIndex(list(EVENTS).index(self.trigger))
            self.trigger_box.blockSignals(False)
            self.trigger_box.setVisible(self.group == "events")
            self.rebuild_slots()
        finally:
            self._history_loading = False
        self.update_history()
        self.touch()

    def change_trigger(self, index):
        if self.syncing:
            return
        self.stop_play()
        self.trigger = list(EVENTS)[index]
        self.frame_index = 0
        self.rebuild_slots()

    def begin_hot_gesture(self):
        if self.frame:
            self.remember("热点拖动")
            self._hot_gesture = True

    def end_hot_gesture(self):
        self._hot_gesture = False
        self.history.break_group()

    def apply_matching(self, mapping):
        if not mapping:
            return
        self.remember("批量匹配")
        for role, frames in mapping.items():
            target = role if self.group == "slots" else event_key(role, self.trigger)
            getattr(self.project, self.group)[target] = [f.clone() for f in frames]
        self.frame_index = 0
        self.rebuild_slots()
        self.touch()
        self.statusBar().showMessage(f"已读取匹配结果：{len(mapping)} 种用途")

    def slot_menu(self, pos):
        item = self.slot_list.itemAt(pos)
        if item is None:
            return
        self.slot_list.setCurrentItem(item)
        menu = QMenu(self)
        for text, fn in [
            ("导入 / 替换图片或动画", lambda: self.import_files(False)),
            ("追加帧", lambda: self.import_files(True)),
            ("删除当前素材", self.clear_slot),
        ]:
            menu.addAction(text, lambda checked=False, f=fn: self.guard(f))
        menu.exec(self.slot_list.viewport().mapToGlobal(pos))

    def clear_slot(self):
        if not self.frames:
            return
        self.remember("删除素材")
        getattr(self.project, self.group)[self.storage_key] = []
        self.frame_index = 0
        self.rebuild_slots()
        self.touch()

    def selected_frame_indices(self):
        rows = sorted(
            {self.frame_list.row(item) for item in self.frame_list.selectedItems()}
        )
        return rows or ([self.frame_index] if self.frame else [])

    def install_frame_shortcuts(self):
        for widget in (self.frame_list, self.canvas):
            for sequence, name, fn in [
                ("Ctrl+C", "复制帧", self.copy_frames),
                ("Ctrl+V", "粘贴帧", self.paste_frames),
                ("Ctrl+X", "剪切帧", self.cut_frames),
                ("Delete", "删除帧", self.delete_frame),
                ("Ctrl+D", "复制并插入", self.duplicate_frame),
            ]:
                action = QAction(name, widget)
                action.setShortcut(sequence)
                action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                action.triggered.connect(lambda checked=False, f=fn: self.guard(f))
                widget.addAction(action)

    def copy_frames(self):
        indices = self.selected_frame_indices()
        if not indices:
            return
        mime = QMimeData()
        mime.setData(
            "application/x-whale-frames",
            encode_clipboard([self.frames[i] for i in indices]),
        )
        QApplication.clipboard().setMimeData(mime)
        self.statusBar().showMessage(f"已复制 {len(indices)} 帧；Ctrl+V 在当前帧后插入")

    def paste_frames(self):
        mime = QApplication.clipboard().mimeData()
        if not mime or not mime.hasFormat("application/x-whale-frames"):
            return
        frames = decode_clipboard(bytes(mime.data("application/x-whale-frames")))
        if len(frames) + len(self.frames) > 512:
            raise ValueError("粘贴后超过 512 帧")
        self.stop_play()
        self.remember("粘贴帧")
        index = self.frame_index + 1 if self.frames else 0
        target = getattr(self.project, self.group).setdefault(self.storage_key, [])
        target[index:index] = frames
        self.frame_index = index
        self.rebuild_slots()
        self.touch()

    def cut_frames(self):
        self.copy_frames()
        self.delete_frame()

    def change_interaction_trigger(self, index):
        self.interaction_trigger = list(EVENTS)[index]
        self.refresh_events()

    def set_runtime_state(self, state, error=""):
        labels = {
            "stopped": ("● 已停止", "#6C7E74", "开启桌面互动"),
            "starting": ("◷ 正在启动…", "#AD7B1A", "正在启动…"),
            "running": ("● 正在运行", "#24734F", "停止桌面互动"),
            "stopping": ("◷ 正在停止…", "#AD7B1A", "正在停止…"),
            "error": ("● 已停止 · 启动或恢复失败", "#B54444", "重新开启互动"),
        }
        text, color, button = labels[state]
        self.runtime_status.setText(text)
        self.runtime_status.setStyleSheet(
            f"color:{color};font-size:15px;font-weight:600;"
        )
        self.runtime_button.setText(button)
        self.runtime_button.setChecked(state == "running")
        self.runtime_button.setEnabled(state not in ("starting", "stopping"))
        self.runtime_button.setStyleSheet(
            "background:#B44D4D;color:white;" if state == "running" else ""
        )

    def save_summary(self):
        system = sum(bool(frames) for frames in self.project.slots.values())
        events = sum(bool(frames) for frames in self.project.events.values())
        frames = sum(len(v) for v in self.project.slots.values()) + sum(
            len(v) for v in self.project.events.values()
        )
        return f"保存 {system}/17 种系统素材、{events} 组互动素材，共 {frames} 帧；包含图片、动画顺序、每帧时长、热点、尺寸、音效与应用规则。\n保存不会安装指针，也不会开启桌面互动；Windows 系统鼠标选项不包含在方案内。"
