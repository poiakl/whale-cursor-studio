"""Local, deterministic image-generation briefs; no remote AI service is used."""

from __future__ import annotations

import json
import math
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QSplitter,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QStyledItemDelegate,
    QHeaderView,
    QLineEdit,
    QApplication,
    QTabWidget,
)
from .project import SLOTS, EVENTS, EFFECTS
from .codec import MAX_FRAMES
from .conditions import describe


class FrameDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        if index.column() != 1 or not index.data(Qt.ItemDataRole.UserRole):
            return None
        editor = QSpinBox(parent)
        editor.setRange(1, MAX_FRAMES)
        editor.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        editor.installEventFilter(self)
        return editor

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent

        if event.type() == QEvent.Type.Wheel:
            return True
        return super().eventFilter(obj, event)

    def setEditorData(self, editor, index):
        editor.setValue(int(index.data()))

    def setModelData(self, editor, model, index):
        model.setData(index, str(editor.value()))


def entries(project):
    for role, title in SLOTS.items():
        yield ("slots." + role, ("系统指针",), title, project.slots.get(role, []), role)
    for trigger, title in EVENTS.items():
        for role, role_title in SLOTS.items():
            yield (
                "events." + role + "." + trigger,
                ("互动指针", title),
                role_title,
                project.event_frames(role, trigger),
                role,
            )
    for key, title in EFFECTS.items():
        yield ("effects." + key, ("装饰",), title, project.effects.get(key, []), None)
    for rule in project.settings.get("animation_rules", []):
        key = rule["id"]
        yield (
            "animations." + key,
            ("条件动画",),
            describe(rule),
            project.animations.get(key, []),
            rule.get("role"),
        )


def make_brief(project, selected, size, columns, style):
    items = []
    for key, path, title, frames, role in entries(project):
        if key not in selected:
            continue
        count = max(1, min(MAX_FRAMES, int(selected[key])))
        decorative = key.startswith("effects.")
        reference = frames or project.slots.get(role, [])
        timeline = []
        for i in range(count):
            frame = reference[i % len(reference)] if reference else None
            hotspot = (
                [
                    min(size - 1, round(frame.x * size / 256)),
                    min(size - 1, round(frame.y * size / 256)),
                ]
                if frame and not decorative
                else [size // 2, size // 2]
            )
            timeline.append(
                dict(
                    frame=i + 1,
                    hotspot_px=hotspot,
                    duration_ms=frame.duration if frame else 100,
                )
            )
        cols = min(columns, count)
        rows = math.ceil(count / cols)
        items.append(
            dict(
                id=key,
                purpose=" / ".join((*path, title)),
                frame_count=count,
                cell_px=[size, size],
                sheet_grid=[cols, rows],
                sheet_px=[cols * size, rows * size],
                unused_cells=cols * rows - count,
                loop=(
                    count > 1
                    and not (
                        key == "effects.click"
                        or key.split(".")[-1]
                        in (
                            "click",
                            "left_click",
                            "right_click",
                            "middle_click",
                            "double_click",
                            "release",
                            "move_start",
                            "move_stop",
                            "role_change",
                        )
                    )
                ),
                anchor_source="装饰画布中心"
                if decorative
                else "现有动画"
                if frames
                else ("对应系统指针参考" if reference else "画布中心"),
                timeline=timeline,
            )
        )
        if decorative:
            settings = project.settings
            effect = key.split(".")[-1]
            items[-1]["effect_playback"] = dict(
                motion=settings.get("effect_motion", "fade")
                if effect == "click"
                else "跟随鼠标",
                display_size_px=settings.get(
                    {
                        "click": "click_size",
                        "trail": "trail_size",
                        "companion": "companion_size",
                    }[effect],
                    64,
                ),
                lifetime_ms=settings.get(
                    "trail_lifetime" if effect == "trail" else "effect_lifetime", 850
                ),
                note="画布中心为构图锚点；点击和拖尾按中心绘制，跟随角色按左上角加偏移绘制；运行时不使用素材点击热点",
            )
    if not items:
        return "请先在左侧勾选需要生成的内容。"
    request = dict(
        project=project.name,
        style=style.strip() or "保持同一角色、配色和画风",
        coordinate_origin="每个单帧左上角；x 向右，y 向下；单位像素",
        items=items,
    )
    return (
        "请为 Windows 鼠标指针与桌面互动制作透明 PNG 动画素材。\n"
        "每个 items 条目单独生成一张序列图，文件名使用 id；不同用途不要混在同一张图。\n"
        "严格按 sheet_grid 的列数、行数排列，从左到右、从上到下；每格大小为 cell_px，"
        "保留完整画布，不裁边、不留格间距，unused_cells 对应末尾透明空格。\n"
        "不要文字、编号、水印、网格线、棋盘底或阴影背景；透明区域必须是真正的 alpha。\n"
        "同一动画中角色大小、镜头、朝向与锚点连续稳定，避免意外抖动。循环项首尾自然衔接；"
        "点击装饰从出现到消散。duration_ms 是播放参考，不要画进图片。\n"
        "hotspot_px 是该格内的目标点击/对齐锚点，请让相应部位对齐；不要画热点标记。"
        "它来自当前方案参考，生成后仍需在制作页核对热点。\n"
        "生成器若无法精确满足大图尺寸，请分条目或分批生成，保持单格尺寸与帧序。"
        "导入时选择对应格尺寸切分；保持每格为正方形，尺寸限制不允许时按比例等比缩放整张序列图。\n\n"
        + json.dumps(request, ensure_ascii=False, indent=2)
    )


class PromptPage(QWidget):
    def __init__(
        self,
        project_getter,
        changed,
        parent=None,
        apply_frames=None,
        frame_getter=lambda: None,
    ):
        super().__init__(parent)
        self.project_getter = project_getter
        self.changed = changed
        self.loading = False
        self.leaves = {}
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("单帧尺寸"))
        self.size = QSpinBox()
        self.size.setRange(32, 2048)
        self.size.setSingleStep(32)
        self.size.setSuffix(" px")
        controls.addWidget(self.size)
        controls.addWidget(QLabel("每行帧数"))
        self.columns = QSpinBox()
        self.columns.setRange(1, 16)
        controls.addWidget(self.columns)
        controls.addStretch()
        self.copy = QPushButton("一键复制")
        controls.addWidget(self.copy)
        layout.addLayout(controls)
        self.style = QLineEdit()
        self.style.setPlaceholderText("画风、角色、配色或其他生图要求…")
        layout.addWidget(self.style)
        split = QSplitter()
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["生成内容", "帧数"])
        self.tree.setMinimumWidth(330)
        self.tree.setColumnWidth(0, 265)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.tree.setColumnWidth(1, 55)
        self.tree.setItemDelegate(FrameDelegate(self.tree))
        split.addWidget(self.tree)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.right_tabs = QTabWidget()
        self.right_tabs.addTab(self.output, "提示词")
        self.ai = None
        if apply_frames is not None:
            from .ai_ui import GenerationPanel

            self.ai = GenerationPanel(
                project_getter, self.generation_tasks, apply_frames, frame_getter
            )
            self.right_tabs.addTab(self.ai, "生成与回传")
        split.addWidget(self.right_tabs)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        layout.addWidget(split, 1)
        layout.addWidget(
            QLabel(
                "勾选分类可批量选择；双击帧数分别修改。尺寸指每一帧，序列图尺寸会自动计算。"
            )
        )
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.regenerate)
        self.tree.itemChanged.connect(self.edited)
        self.style.textChanged.connect(self.edited)
        self.size.valueChanged.connect(self.edited)
        self.columns.valueChanged.connect(self.edited)
        # Number inputs deliberately don't react to the wheel.
        self.size.installEventFilter(self)
        self.columns.installEventFilter(self)
        self.copy.clicked.connect(self.copy_text)
        self.reload()

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent

        if event.type() == QEvent.Type.Wheel:
            return True
        return super().eventFilter(obj, event)

    def reload(self):
        self.loading = True
        try:
            project = self.project_getter()
            config = project.settings.get("ai_prompt", {})
            selected = config.get("selected", {"slots.Arrow": 1})
            counts = config.get("counts", {})
            self.size.setValue(config.get("size", 256))
            self.columns.setValue(config.get("columns", 4))
            self.style.setText(config.get("style", ""))
            self.tree.clear()
            self.leaves.clear()
            groups = {}
            for key, path, title, frames, role in entries(project):
                parent = None
                for depth in range(1, len(path) + 1):
                    prefix = path[:depth]
                    if prefix not in groups:
                        group = QTreeWidgetItem([prefix[-1]])
                        group.setFlags(
                            group.flags()
                            | Qt.ItemFlag.ItemIsAutoTristate
                            | Qt.ItemFlag.ItemIsUserCheckable
                        )
                        group.setCheckState(0, Qt.CheckState.Unchecked)
                        (
                            parent.addChild(group)
                            if parent
                            else self.tree.addTopLevelItem(group)
                        )
                        groups[prefix] = group
                    parent = groups[prefix]
                count = counts.get(
                    key,
                    selected.get(
                        key, len(frames) or (1 if key.startswith("slots.") else 8)
                    ),
                )
                item = QTreeWidgetItem([title, str(count)])
                item.setData(0, Qt.ItemDataRole.UserRole, key)
                item.setData(1, Qt.ItemDataRole.UserRole, key)
                item.setFlags(
                    item.flags()
                    | Qt.ItemFlag.ItemIsUserCheckable
                    | Qt.ItemFlag.ItemIsEditable
                )
                parent.addChild(item)
                item.setCheckState(
                    0,
                    Qt.CheckState.Checked
                    if key in selected
                    else Qt.CheckState.Unchecked,
                )
                self.leaves[key] = item
            for path, item in groups.items():
                item.setExpanded(len(path) == 1)
        finally:
            self.loading = False
        self.regenerate(save=False)

    def edited(self, *args):
        if not self.loading:
            self.timer.start()

    def regenerate(self, save=True):
        selected = {
            key: int(item.text(1))
            for key, item in self.leaves.items()
            if item.checkState(0) == Qt.CheckState.Checked
        }
        self.output.setPlainText(
            make_brief(
                self.project_getter(),
                selected,
                self.size.value(),
                self.columns.value(),
                self.style.text(),
            )
        )
        self.copy.setEnabled(bool(selected))
        if save:
            self.project_getter().settings["ai_prompt"] = dict(
                selected=selected,
                counts={key: int(item.text(1)) for key, item in self.leaves.items()},
                size=self.size.value(),
                columns=self.columns.value(),
                style=self.style.text(),
            )
            self.changed()

    def copy_text(self):
        self.timer.stop()
        self.regenerate()
        QApplication.clipboard().setText(self.output.toPlainText())

    def generation_tasks(self):
        self.timer.stop()
        self.regenerate()
        tasks = []
        for key, item in self.leaves.items():
            if item.checkState(0) != Qt.CheckState.Checked:
                continue
            prompt = make_brief(
                self.project_getter(),
                {key: int(item.text(1))},
                self.size.value(),
                self.columns.value(),
                self.style.text(),
            )
            spec = json.loads(prompt[prompt.index("{") :])["items"][0]
            tasks.append(
                dict(
                    prompt="若附有参考图，请保持参考角色、画风与配色，按下面要求重新绘制完整序列图，不要照搬参考图的排版。\n"
                    + prompt,
                    spec=spec,
                )
            )
        return tasks
