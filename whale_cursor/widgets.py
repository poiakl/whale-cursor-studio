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
    QComboBox,
    QFileDialog,
    QMessageBox,
    QTabBar,
)
from .runtime import pixmap
from .project import SLOTS, EVENTS, EFFECTS
from .conditions import describe
from .asset_import import configured_target, read_frames, read_manifest


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
        self.set_frames(frames or [])

    def set_frames(self, frames):
        if len(self.frames) == len(frames) and all(
            a is b for a, b in zip(self.frames, frames)
        ):
            return
        self.frames = list(frames)
        if self.isVisible() and len(frames) > 1:
            self.timer.start()
        else:
            self.timer.stop()
        self.epoch = time.monotonic()
        self.frame_index = -1
        self.advance(force=True)

    def showEvent(self, event):
        super().showEvent(event)
        if len(self.frames) > 1:
            self.timer.start()
        self.advance(force=True)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

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
    GROUPS = ("slots", "events", "effects", "animations")

    def __init__(self, assets, parent=None):
        super().__init__(parent)
        self.assets = assets
        self.pixel = parent.project.pixel
        self.fallback_trigger = parent.trigger if parent.group == "events" else None
        self.manifest = {}
        self.options = {}
        self.mapping = {}
        self.manual = set()
        self.result = {}
        self.targets = {
            "slots": {f"slots.{key}": name for key, name in SLOTS.items()},
            "events": {
                f"events.{role}.{event}": name
                for event in EVENTS
                for role, name in SLOTS.items()
            },
            "effects": {f"effects.{key}": name for key, name in EFFECTS.items()},
            "animations": {
                f"animations.{rule['id']}": describe(rule)
                for rule in parent.project.settings.get("animation_rules", [])
            },
        }
        self.target_ids = {key for group in self.targets.values() for key in group}
        self.setWindowTitle("批量匹配动画")
        self.resize(1060, 700)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        layout.addLayout(row, 1)
        left = QVBoxLayout()
        row.addLayout(left, 4)
        self.tabs = QTabBar()
        for title in ("系统指针", "互动指针", "装饰效果", "条件动画"):
            self.tabs.addTab(title)
        self.tabs.setCurrentIndex(self.GROUPS.index(parent.group))
        left.addWidget(self.tabs)
        self.trigger = QComboBox()
        for key, title in EVENTS.items():
            self.trigger.addItem(title, key)
        self.trigger.setCurrentIndex(self.trigger.findData(parent.trigger))
        left.addWidget(self.trigger)
        self.table = MatchTable(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["匹配用途", "匹配文件（可拖动）", "列×行", "帧数", "预览"]
        )
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.table.setDragEnabled(True)
        self.table.setAcceptDrops(True)
        self.table.setDropIndicatorShown(True)
        self.table.assigned.connect(self.assign)
        self.table.itemChanged.connect(self.update_options)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        for column, width in ((0, 160), (2, 72), (3, 60), (4, 60)):
            self.table.setColumnWidth(column, width)
        left.addWidget(self.table)
        right = QVBoxLayout()
        right.addWidget(QLabel("未分配文件（拖到左侧）"))
        self.pool = AssetPool()
        self.pool.returned.connect(self.unassign)
        right.addWidget(self.pool, 1)
        self.config_button = QPushButton("导入配置文件…")
        self.config_button.clicked.connect(self.choose_config)
        right.addWidget(self.config_button)
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
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("导入所有已匹配动画")
        buttons.accepted.connect(self.accept_import)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.tabs.currentChanged.connect(self.render)
        self.trigger.currentIndexChanged.connect(self.render)
        self.auto_assign()

    def choose_config(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, "导入配置文件", "", "JSON (*.json)"
        )
        if not filename:
            return
        try:
            self.manifest = read_manifest(filename)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "配置文件有误", str(error))
            return
        self.config_button.setToolTip(Path(filename).name)
        for target in self.manifest:
            self.options.pop(target, None)
        self.auto_assign(preserve=True)

    def auto_assign(self, checked=False, *, preserve=False):
        if preserve:
            self.mapping = {
                key: value for key, value in self.mapping.items() if key in self.manual
            }
        else:
            self.mapping.clear()
            self.manual.clear()
        used = set(self.mapping.values())
        for index, asset in enumerate(self.assets):
            if index in used:
                continue
            target = configured_target(asset["path"], self.target_ids, self.manifest)
            if target and target not in self.mapping and target not in self.manual:
                self.mapping[target] = index
                used.add(index)
        for index, asset in enumerate(self.assets):
            if index in used or "." in Path(asset["path"]).stem:
                continue
            role = guess_role(asset["path"])
            if role:
                target = (
                    f"events.{role}.{self.fallback_trigger}"
                    if self.fallback_trigger
                    else f"slots.{role}"
                )
                if target not in self.mapping and target not in self.manual:
                    self.mapping[target] = index
        self.render()

    def assign(self, row, index):
        if not 0 <= row < len(self.visible_targets) or not 0 <= index < len(
            self.assets
        ):
            return
        target = self.visible_targets[row]
        source = next(
            (key for key, value in self.mapping.items() if value == index), None
        )
        if source == target:
            return
        old = self.mapping.get(target)
        if source is not None:
            self.manual.add(source)
            self.mapping.pop(source)
            if old is not None:
                self.mapping[source] = old
        self.mapping[target] = index
        self.manual.add(target)
        self.render()

    def unassign(self, index):
        self.manual.update(key for key, value in self.mapping.items() if value == index)
        self.mapping = {
            key: value for key, value in self.mapping.items() if value != index
        }
        self.render()

    def clear_row(self, row):
        if 0 <= row < len(self.visible_targets):
            target = self.visible_targets[row]
            self.manual.add(target)
            self.mapping.pop(target, None)
            self.render()

    def update_options(self, item):
        if item.column() not in (2, 3):
            return
        target = self.visible_targets[item.row()]
        self.options[target] = (
            self.table.item(item.row(), 2).text(),
            self.table.item(item.row(), 3).text(),
        )

    def render(self, *_):
        group = self.GROUPS[self.tabs.currentIndex()]
        self.trigger.setVisible(group == "events")
        self.visible_targets = [
            target
            for target in self.targets[group]
            if group != "events" or target.endswith("." + self.trigger.currentData())
        ]
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.visible_targets))
        for row, target in enumerate(self.visible_targets):
            self.table.setRowHeight(row, 50)
            name = QTableWidgetItem(self.targets[group][target] + "\n" + target)
            name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, name)
            index = self.mapping.get(target)
            asset = self.assets[index] if index is not None else None
            item = QTableWidgetItem(Path(asset["path"]).name if asset else "未分配")
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 1, item)
            spec = self.manifest.get(target, {})
            columns, rows = spec.get("sheet_grid", [1, 1])
            count = spec.get("frame_count", len(asset["frames"]) if asset else 1)
            grid, count = self.options.get(target, (f"{columns}×{rows}", str(count)))
            self.table.setItem(row, 2, QTableWidgetItem(grid))
            self.table.setItem(row, 3, QTableWidgetItem(count))
            self.table.setCellWidget(
                row, 4, AnimatedThumb(asset["frames"] if asset else [])
            )
        self.table.blockSignals(False)
        self.pool.clear()
        used = set(self.mapping.values())
        for index, asset in enumerate(self.assets):
            if index not in used:
                item = QListWidgetItem(
                    QIcon(pixmap(asset["frames"][0].image)), Path(asset["path"]).name
                )
                item.setData(Qt.ItemDataRole.UserRole, index)
                self.pool.addItem(item)
        self.count_label.setText(
            f"全部分类已匹配 {len(self.mapping)} 项 · 配置 {len(self.manifest)} 项"
        )

    def result_mapping(self):
        result = {}
        for target, index in self.mapping.items():
            asset = self.assets[index]
            spec = self.manifest.get(target)
            columns, rows = spec["sheet_grid"] if spec else (1, 1)
            default_count = spec["frame_count"] if spec else len(asset["frames"])
            try:
                grid_text, count_text = self.options.get(
                    target, (f"{columns}×{rows}", str(default_count))
                )
                match = re.fullmatch(r"\s*(\d{1,3})\s*[×xX*]\s*(\d{1,3})\s*", grid_text)
                if not match or not all(
                    1 <= int(value) <= 512 for value in match.groups()
                ):
                    raise ValueError("网格请填写列×行，例如 4×4")
                grid = tuple(int(value) for value in match.groups())
                count = int(count_text)
                result[target] = read_frames(
                    [asset["path"]], self.pixel, grid, count, spec, asset["frames"]
                )
            except (OSError, ValueError) as error:
                raise ValueError(
                    f"{target} · {Path(asset['path']).name}：{error}"
                ) from error
        return result

    def accept_import(self):
        try:
            self.result = self.result_mapping()
        except ValueError as error:
            QMessageBox.warning(self, "批量导入失败", str(error))
            return
        if self.result:
            self.accept()


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
