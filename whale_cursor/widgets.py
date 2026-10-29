from __future__ import annotations
import re
import time
from pathlib import Path
from PySide6.QtCore import Qt, QTimer, Signal, QMimeData, QSize
from PySide6.QtGui import QDrag, QIcon
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QDialogButtonBox,
    QAbstractItemView,
)
from .runtime import pixmap
from .project import SLOTS


class AnimatedThumb(QLabel):
    def __init__(self, frames=None, size=48, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.size_px = size
        self.frames = []
        self.frame_index = -1
        self.epoch = time.monotonic()
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self.advance)
        self.timer.start()
        self.set_frames(frames or [])

    def set_frames(self, frames):
        if len(self.frames) == len(frames) and all(
            a is b for a, b in zip(self.frames, frames)
        ):
            return
        self.frames = frames
        self.epoch = time.monotonic()
        self.frame_index = -1
        self.advance(force=True)

    def advance(self, force=False):
        if not self.frames:
            self.clear()
            self.setText("—")
            return
        if not force and not self.isVisible():
            return
        total = sum(f.duration for f in self.frames)
        elapsed = (time.monotonic() - self.epoch) * 1000 % max(1, total)
        index = 0
        while index < len(self.frames) - 1 and elapsed >= self.frames[index].duration:
            elapsed -= self.frames[index].duration
            index += 1
        if force or index != self.frame_index:
            self.frame_index = index
            self.setPixmap(
                pixmap(self.frames[index].image).scaled(
                    self.size_px,
                    self.size_px,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )


def guess_role(filename):
    name = Path(filename).stem.lower()
    tokens = re.split(r"[^a-z0-9]+", name)
    for key in sorted(SLOTS, key=len, reverse=True):
        if (
            key.lower() in tokens
            or name == key.lower()
            or name.endswith("_" + key.lower())
        ):
            return key
    aliases = {
        "Arrow": ["正常选择", "普通选择", "normal", "default"],
        "Help": ["帮助选择", "help"],
        "AppStarting": ["后台工作", "后台运行", "working"],
        "Wait": ["忙", "忙碌", "busy"],
        "Crosshair": ["精确选择", "cross"],
        "IBeam": ["文本选择", "text"],
        "NWPen": ["手写", "笔", "pen"],
        "No": ["不可用", "unavailable"],
        "SizeNS": ["垂直调整大小", "垂直调整", "vertical"],
        "SizeWE": ["水平调整大小", "水平调整", "horizontal"],
        "SizeNWSE": ["斜对角调整大小1", "对角线调整1"],
        "SizeNESW": ["斜对角调整大小2", "对角线调整2"],
        "SizeAll": ["移动", "move"],
        "UpArrow": ["备用选择", "候选选择", "alternate"],
        "Hand": ["链接选择", "link"],
        "Pin": ["位置选择", "location"],
        "Person": ["个人选择", "人员选择", "person"],
    }
    for key, values in aliases.items():
        if name in values or any(
            value in name for value in values if not value.isascii()
        ):
            return key
    number = re.match(r"^(\d{1,2})(?:\D|$)", name)
    if number and 1 <= int(number[1]) <= 17:
        return list(SLOTS)[int(number[1]) - 1]
    return None


ASSET_MIME = "application/x-whale-match-file"


class AssetPool(QListWidget):
    returned = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setIconSize(QSize(44, 44))

    def startDrag(self, actions):
        item = self.currentItem()
        if not item:
            return
        mime = QMimeData()
        mime.setData(ASSET_MIME, str(item.data(Qt.ItemDataRole.UserRole)).encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(item.icon().pixmap(44, 44))
        drag.exec(Qt.DropAction.MoveAction)

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(ASSET_MIME):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        self.dragEnterEvent(e)

    def dropEvent(self, e):
        if e.mimeData().hasFormat(ASSET_MIME):
            self.returned.emit(int(bytes(e.mimeData().data(ASSET_MIME))))
            e.acceptProposedAction()


class MatchTable(QTableWidget):
    assigned = Signal(int, int)

    def startDrag(self, actions):
        item = self.item(self.currentRow(), 1)
        if item is None or item.data(Qt.ItemDataRole.UserRole) is None:
            return
        mime = QMimeData()
        mime.setData(ASSET_MIME, str(item.data(Qt.ItemDataRole.UserRole)).encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(ASSET_MIME):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        self.dragEnterEvent(e)

    def dropEvent(self, e):
        row = self.rowAt(e.position().toPoint().y())
        if row >= 0 and e.mimeData().hasFormat(ASSET_MIME):
            self.assigned.emit(row, int(bytes(e.mimeData().data(ASSET_MIME))))
            e.acceptProposedAction()


class MatchDialog(QDialog):
    def __init__(self, assets, parent=None):
        super().__init__(parent)
        self.assets = assets
        self.assignments = [None] * 17
        self.setWindowTitle("批量匹配 · 自动分配后可拖动调整")
        self.resize(940, 740)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "左侧用途固定；把文件拖到对应行。行与行之间拖动可交换文件，拖回右侧可取消匹配。"
            )
        )
        row = QHBoxLayout()
        layout.addLayout(row, 1)
        self.table = MatchTable(17, 3)
        self.table.setHorizontalHeaderLabels(
            ["匹配用途", "匹配文件（拖动调整）", "动画预览"]
        )
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.table.setDragEnabled(True)
        self.table.setAcceptDrops(True)
        self.table.setDropIndicatorShown(True)
        self.table.assigned.connect(self.assign)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.table.setColumnWidth(0, 150)
        self.table.setColumnWidth(2, 80)
        row.addWidget(self.table, 3)
        right = QVBoxLayout()
        right.addWidget(QLabel("未分配文件（可拖入左侧）"))
        self.pool = AssetPool()
        self.pool.returned.connect(self.unassign)
        right.addWidget(self.pool, 1)
        self.selected_preview = AnimatedThumb(size=112)
        right.addWidget(self.selected_preview)
        self.pool.currentRowChanged.connect(self.preview_pool)
        row.addLayout(right, 1)
        footer = QHBoxLayout()
        auto = QPushButton("重新自动匹配")
        auto.clicked.connect(self.auto_assign)
        footer.addWidget(auto)
        clear = QPushButton("取消当前行匹配")
        clear.clicked.connect(lambda: self.clear_row(self.table.currentRow()))
        footer.addWidget(clear)
        footer.addStretch()
        self.count_label = QLabel()
        footer.addWidget(self.count_label)
        layout.addLayout(footer)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确认匹配并读取")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.auto_assign()

    def auto_assign(self):
        self.assignments = [None] * 17
        remaining = []
        keys = list(SLOTS)
        for i, asset in enumerate(self.assets):
            key = guess_role(asset["path"])
            if key is not None and self.assignments[keys.index(key)] is None:
                self.assignments[keys.index(key)] = i
            else:
                remaining.append(i)
        # Unrecognised names get an initial position too, always visible for manual correction.
        for row, index in zip(
            [r for r, v in enumerate(self.assignments) if v is None], remaining
        ):
            self.assignments[row] = index
        self.render()

    def assign(self, row, index):
        if not 0 <= row < 17 or not 0 <= index < len(self.assets):
            return
        source = next((r for r, v in enumerate(self.assignments) if v == index), None)
        old = self.assignments[row]
        if source == row:
            return
        if source is not None:
            self.assignments[source] = old
        self.assignments[row] = index
        self.render()

    def unassign(self, index):
        for row, value in enumerate(self.assignments):
            if value == index:
                self.assignments[row] = None
        self.render()

    def clear_row(self, row):
        if row >= 0:
            self.assignments[row] = None
            self.render()

    def render(self):
        for row, (key, name) in enumerate(SLOTS.items()):
            self.table.setRowHeight(row, 58)
            self.table.setItem(row, 0, QTableWidgetItem(name + "\n" + key))
            index = self.assignments[row]
            item = QTableWidgetItem(
                "未分配（保留当前素材）"
                if index is None
                else Path(self.assets[index]["path"]).name
                + "\n"
                + str(len(self.assets[index]["frames"]))
                + " 帧"
            )
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.table.setItem(row, 1, item)
            old = self.table.cellWidget(row, 2)
            if old:
                old.deleteLater()
            self.table.setCellWidget(
                row,
                2,
                AnimatedThumb([] if index is None else self.assets[index]["frames"]),
            )
        self.pool.clear()
        used = set(self.assignments)
        for index, asset in enumerate(self.assets):
            if index not in used:
                item = QListWidgetItem(
                    QIcon(pixmap(asset["frames"][0].image)),
                    Path(asset["path"]).name + "\n" + str(len(asset["frames"])) + " 帧",
                )
                item.setData(Qt.ItemDataRole.UserRole, index)
                self.pool.addItem(item)
        self.count_label.setText(
            f"已匹配 {sum(v is not None for v in self.assignments)} / 17 项"
        )

    def preview_pool(self, row):
        item = self.pool.item(row)
        self.selected_preview.set_frames(
            self.assets[item.data(Qt.ItemDataRole.UserRole)]["frames"] if item else []
        )

    def result_mapping(self):
        return {
            role: self.assets[index]["frames"]
            for role, index in zip(SLOTS, self.assignments)
            if index is not None
        }


class InspectionDialog(QDialog):
    def __init__(self, rows, parent=None):
        super().__init__(parent)
        self.setWindowTitle("方案检查 · 系统与互动全部帧")
        self.resize(900, 640)
        layout = QVBoxLayout(self)
        total = sum(row["count"] for row in rows)
        animations = sum(row["count"] > 1 for row in rows)
        layout.addWidget(
            QLabel(
                f"已检查 {len(rows)} 组素材 / {total} 帧 / {animations} 组动画；包含系统指针和互动素材，已验证动画导出与时序。"
            )
        )
        table = QTableWidget(len(rows), 5)
        table.setHorizontalHeaderLabels(
            ["用途 / 动作", "帧数", "总时长", "动态预览", "检查结果"]
        )
        table.verticalHeader().hide()
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(table)
        for i, row in enumerate(rows):
            table.setRowHeight(i, 58)
            for j, text in [
                (0, row["name"]),
                (1, str(row["count"])),
                (2, f"{row['duration'] / 1000:.2f}s"),
                (4, "；".join(row["issues"]) or "通过"),
            ]:
                table.setItem(i, j, QTableWidgetItem(text))
            table.setCellWidget(i, 3, AnimatedThumb(row["frames"]))
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
