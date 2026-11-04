"""Local, deterministic image-generation briefs; no remote AI service is used."""

from __future__ import annotations

import json
import math
from pathlib import Path
from PIL import Image
from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QColor, QPen, QBrush
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
    QDialog,
    QDialogButtonBox,
    QFrame,
    QComboBox,
    QMessageBox,
    QStyleOptionViewItem,
    QScrollArea,
)
from .project import SLOTS, EVENTS, EFFECTS
from .codec import MAX_FRAMES
from .conditions import describe
from .prompt_hierarchy import (
    default_instruction,
    group_defaults,
    keywords_dict,
    STYLE as CHARACTER_STYLE,
    SHARED,
    without_inherited,
)


class FrameDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        option = QStyleOptionViewItem(option)
        state = index.siblingAtColumn(0).data(Qt.ItemDataRole.CheckStateRole)
        if state in (
            Qt.CheckState.Checked,
            Qt.CheckState.Checked.value,
            Qt.CheckState.PartiallyChecked,
            Qt.CheckState.PartiallyChecked.value,
        ):
            color = QColor(option.palette.highlight().color())
            color.setAlpha(38)
            option.backgroundBrush = QBrush(color)
        super().paint(painter, option, index)
        painter.save()
        color = QColor(option.palette.mid().color())
        color.setAlpha(80)
        painter.setPen(QPen(color, 1))
        painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(super().sizeHint(option, index).width(), 20)

    def createEditor(self, parent, option, index):
        if index.column() != 1 or not index.data(Qt.ItemDataRole.UserRole):
            return None
        editor = QSpinBox(parent)
        editor.setRange(1, MAX_FRAMES)
        editor.setStyleSheet(
            "QSpinBox {padding:1px;min-height:16px;} QSpinBox::up-button,QSpinBox::down-button {height:9px;}"
        )
        editor.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        editor.installEventFilter(self)
        return editor

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent

        if event.type() == QEvent.Type.Wheel:
            return True
        return super().eventFilter(obj, event)

    def setEditorData(self, editor, index):
        value = str(index.data())
        editor.setValue(int(value) if value.isdigit() else 8)

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


ROLE_PROMPTS = {
    "Arrow": "正常选择：轻微呼吸或眨眼，指向位置明确，锚点稳定。",
    "Help": "帮助选择：好奇、询问或思考的小动作，体现帮助含义。",
    "AppStarting": "后台运行：保持可操作姿态，配合轻微等待动作，区别于完全忙碌。",
    "Wait": "忙碌等待：连贯的循环等待动作，节奏均匀，首尾衔接。",
    "Crosshair": "精确选择：围绕固定中心做克制的小动作，精确定位中心不漂移。",
    "IBeam": "文本选择：构图纤细、竖向，文本插入锚点清晰，不遮住阅读区域。",
    "NWPen": "手写：做书写或持笔动作，笔尖始终对齐热点。",
    "No": "不可用：明确的停止或拒绝姿态，动作简洁，不依赖文字。",
    "SizeNS": "垂直调整：上下方向的对称伸缩动作，中心锚点固定。",
    "SizeWE": "水平调整：左右方向的对称伸缩动作，中心锚点固定。",
    "SizeNWSE": "对角调整：体现左上到右下方向，沿该对角线小幅伸展。",
    "SizeNESW": "对角调整：体现右上到左下方向，沿该对角线小幅伸展。",
    "SizeAll": "移动：四向搬动或展开姿态，体现可移动含义，中心稳定。",
    "UpArrow": "候选选择：保持明确朝上的指向性，动作轻巧。",
    "Hand": "链接选择：轻触或伸手示意可点击，指尖对齐热点。",
    "Pin": "位置选择：定位或标记地点的动作，定位尖端对齐热点。",
    "Person": "人员选择：友好的招呼或人物识别姿态，轮廓清晰。",
}
TRIGGER_PROMPTS = {
    "click": "点击瞬间轻压再回弹，完整播放一次。",
    "idle": "停留时自然呼吸、眨眼或轻微摆动，循环平稳。",
    "move": "移动时呈现轻微前进姿态，可无缝循环。",
    "left": "向左移动时朝左轻倾，动作沿左方向，可无缝循环。",
    "right": "向右移动时朝右轻倾，动作沿右方向，可无缝循环。",
    "up": "向上移动时向上轻抬或伸展，可无缝循环。",
    "down": "向下移动时向下轻倾或收拢，可无缝循环。",
    "left_click": "左键按下时做一次轻触压下及回弹动作。",
    "right_click": "右键按下时做一次侧向示意或轻敲，区别于左键。",
    "middle_click": "中键按下时做一次居中的按压与回弹动作。",
    "double_click": "连续两次短促按压与回弹，双击节奏清楚。",
    "drag": "按住拖动时做抓握、牵引或用力姿态，循环时锚点稳定。",
    "release": "松开时由紧绷放松，轻微回弹后自然停稳，播放一次。",
    "hold": "长按时保持压住或蓄力姿态，以微小动作循环，不反复松开。",
    "move_start": "从静止过渡到运动，短促起步动作，播放一次。",
    "move_stop": "从运动减速停稳，小幅缓冲回正，播放一次。",
    "role_change": "切换指针用途时做一次简短过渡，结尾匹配新用途姿态。",
}


def legacy_default_instruction(key, role=None, title=""):
    reference = "匹配参考图中的角色、服装、配色、材质与画风；不改角色设计。"
    if key.startswith("effects."):
        action = {
            "click": "点击装饰：从出现、扩散到完全消散，只播放一次；末帧透明，不重新出现。",
            "trail": "拖尾装饰：柔和的小粒子或轨迹，从显现到淡出，边缘透明，不画入鼠标位移。",
            "companion": "跟随角色：完整身体或参考图对应主体，自然呼吸眨眼，循环连贯，中心稳定。",
        }[key.split(".")[-1]]
    elif key.startswith("events."):
        action = ROLE_PROMPTS.get(role, "") + TRIGGER_PROMPTS[key.split(".")[-1]]
    elif key.startswith("animations."):
        action = "根据条件「" + title + "」绘制相应动作，条件持续时稳定保持，动作连贯。"
    else:
        action = ROLE_PROMPTS.get(
            role or key.split(".")[-1], "保持对应指针的用途清晰。"
        )
    return action + reference + "主体大小和热点锚点全程一致，完整入格，背景真透明。"


def make_brief(
    project,
    selected,
    size,
    columns,
    style,
    extra="",
    item_instructions=None,
    group_instructions=None,
    character_name=None,
    keyword_fields=None,
):
    items = []
    groups = {}
    common = [
        style.strip() or CHARACTER_STYLE,
        SHARED,
        extra.strip(),
        *keywords_dict(keyword_fields).values(),
    ]
    inherited = dict(group_defaults(), **(group_instructions or {}))
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
                parent_id="/".join(path),
                extra_prompt=without_inherited(
                    (item_instructions or {})
                    .get(key, default_instruction(key, role, title))
                    .strip(),
                    common
                    + [
                        inherited.get("/".join(path[:depth]), "")
                        for depth in range(1, len(path) + 1)
                    ],
                ),
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
        for depth in range(1, len(path) + 1):
            group_id = "/".join(path[:depth])
            groups.setdefault(
                group_id,
                dict(
                    id=group_id,
                    parent_id="/".join(path[: depth - 1]) if depth > 1 else "character",
                    extra_prompt=without_inherited(
                        inherited.get(group_id, "").strip(),
                        common
                        + [
                            inherited.get("/".join(path[:d]), "")
                            for d in range(1, depth)
                        ],
                    ),
                ),
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
        character=dict(
            id=character_name or project.name,
            style=style.strip() or CHARACTER_STYLE,
            extra_prompt="\n".join(filter(None, [SHARED, extra.strip()])),
            **keywords_dict(keyword_fields),
        ),
        coordinate_origin="每个单帧左上角；x 向右，y 向下；单位像素",
        groups=list(groups.values()),
        items=items,
    )
    return (
        "请为 Windows 鼠标指针与桌面互动制作透明 PNG 动画素材。\n"
        "character 是全局角色要求；groups 通过 parent_id 逐层继承父级，items 继承所属分组。父级要求仅写一次，子项只写自身差异；character 的自定义关键字与 id、extra_prompt 并列，对全部动画生效。不要把指令文字画进图片。\n"
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
        self.item_instructions = {}
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
        self.character_button = QPushButton("角色配置 ▾")
        self.character_button.setCheckable(True)
        controls.addWidget(self.character_button)
        controls.addStretch()
        self.copy = QPushButton("一键复制")
        controls.addWidget(self.copy)
        layout.addLayout(controls)
        self.style = QLineEdit()
        self.style.setPlaceholderText("画风、配色、材质…")
        self.extra = QPlainTextEdit()
        self.extra.setPlaceholderText("共享指令与额外条例，适用于全部勾选项…")
        self.character_name = QLineEdit()
        self.reference_paths = []
        self.keyword_rows = []
        self.groups = {}
        self.group_instructions = {}
        split = QSplitter()
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["生成内容", "帧数", "额外提示词"])
        self.tree.setMinimumWidth(420)
        self.tree.setIndentation(13)
        self.tree.setUniformRowHeights(True)
        self.tree.setStyleSheet(
            "QTreeWidget::item {padding:1px 0;} QHeaderView::section {padding:4px;} QTreeWidget {font-size:12px;}"
        )
        self.tree.setColumnWidth(0, 145)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.tree.setColumnWidth(1, 42)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tree.itemDoubleClicked.connect(self.edit_item_instruction)
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
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([540, 420])
        layout.addWidget(split, 1)
        self.build_character_panel(layout)
        self.character_button.toggled.connect(self.character_panel.setVisible)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.regenerate)
        self.tree.itemChanged.connect(self.edited)
        self.character_name.textChanged.connect(self.edited)
        self.style.textChanged.connect(self.edited)
        self.extra.textChanged.connect(self.extra_changed)
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
            self.item_instructions = dict(config.get("item_instructions", {}))
            self.group_instructions = dict(
                group_defaults(), **config.get("group_instructions", {})
            )
            if config.get("prompt_schema", 1) < 2:
                for key, path, title, frames, role in entries(project):
                    if self.item_instructions.get(key) == legacy_default_instruction(
                        key, role, title
                    ):
                        self.item_instructions[key] = default_instruction(
                            key, role, title
                        )
            fields = list(config.get("keyword_fields", []))
            legacy_extra = config.get("extra", "")
            if legacy_extra:
                fields.append(dict(key="additional_requirements", value=legacy_extra))
            self.restore_keyword_rows(fields)
            self.character_name.setText(config.get("character_name", project.name))
            self.reference_paths = list(config.get("references", []))
            self.restore_character_references()
            self.size.setValue(config.get("size", 256))
            self.columns.setValue(config.get("columns", 4))
            self.style.setText(config.get("style", ""))
            self.extra.setPlainText("")
            self.tree.clear()
            self.leaves.clear()
            groups = {}
            self.groups = groups
            for key, path, title, frames, role in entries(project):
                parent = None
                for depth in range(1, len(path) + 1):
                    prefix = path[:depth]
                    if prefix not in groups:
                        group_key = "/".join(prefix)
                        prefix_text = self.group_instructions.get(group_key, "")
                        group = QTreeWidgetItem(
                            [prefix[-1], "—", prefix_text.replace("\n", " / ")]
                        )
                        for column in (1, 2):
                            group.setData(
                                column, Qt.ItemDataRole.UserRole, "@group:" + group_key
                            )
                        group.setToolTip(1, "设置本组所有子项的帧数，不改变勾选状态")
                        group.setToolTip(
                            2,
                            prefix_text or "本组子项的公用前缀；保留各条目自己的提示词",
                        )
                        group.setFlags(
                            group.flags()
                            | Qt.ItemFlag.ItemIsAutoTristate
                            | Qt.ItemFlag.ItemIsUserCheckable
                            | Qt.ItemFlag.ItemIsEditable
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
                instruction = self.item_instructions.setdefault(
                    key, default_instruction(key, role, title)
                )
                item = QTreeWidgetItem(
                    [title, str(count), instruction.replace("\n", " / ")]
                )
                item.setToolTip(2, instruction)
                item.setData(2, Qt.ItemDataRole.UserRole, key)
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
        if self.loading:
            return
        if len(args) == 2 and isinstance(args[0], QTreeWidgetItem):
            item, column = args
            key = item.data(column, Qt.ItemDataRole.UserRole)
            if (
                column == 1
                and key
                and key.startswith("@group:")
                and item.text(1).isdigit()
            ):
                self.set_group_count(key[7:], int(item.text(1)))
        self.timer.start()

    def regenerate(self, save=True):
        self.timer.stop()
        self.refresh_group_counts()
        self.sync_row_backgrounds()
        try:
            fields = self.keyword_fields()
            keywords_dict(fields)
        except ValueError as error:
            self.keyword_error.setText(str(error))
            self.copy.setToolTip(str(error))
            self.character_button.setToolTip("请检查角色配置：" + str(error))
            self.copy.setEnabled(False)
            return False
        self.keyword_error.clear()
        self.copy.setToolTip("")
        self.character_button.setToolTip("")
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
                self.extra.toPlainText(),
                self.item_instructions,
                self.group_instructions,
                self.character_name.text().strip(),
                self.keyword_fields(),
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
                extra=self.extra.toPlainText(),
                item_instructions=dict(self.item_instructions),
                group_instructions=dict(self.group_instructions),
                character_name=self.character_name.text().strip(),
                references=list(self.reference_paths),
                keyword_fields=fields,
                prompt_schema=2,
            )
            self.changed()
        return True

    def copy_text(self):
        self.timer.stop()
        if not self.regenerate():
            return
        QApplication.clipboard().setText(self.output.toPlainText())

    def extra_changed(self):
        self.edited()

    def edit_extra(self):
        self.character_button.setChecked(True)

    def set_item_instruction(self, key, text):
        text = text.strip()
        if text:
            self.item_instructions[key] = text
        else:
            self.item_instructions.pop(key, None)
        item = self.leaves[key]
        item.setText(2, text.replace("\n", " / "))
        item.setToolTip(2, text)
        self.regenerate()

    def edit_item_instruction(self, item, column):
        key = item.data(2, Qt.ItemDataRole.UserRole)
        if column != 2 or not key:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("条目提示词 · " + item.text(0))
        dialog.resize(560, 360)
        layout = QVBoxLayout(dialog)
        editor = QPlainTextEdit()
        editor.setPlaceholderText(
            "仅用于这个条目，例如：向右弹跳三次，最后眨眼；不要箭头。"
        )
        editor.setPlainText(
            self.group_instructions.get(key[7:], "")
            if key.startswith("@group:")
            else self.item_instructions.get(key, "")
        )
        layout.addWidget(editor, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if key.startswith("@group:"):
                self.set_group_instruction(key[7:], editor.toPlainText())
            else:
                self.set_item_instruction(key, editor.toPlainText())

    def generation_tasks(self):
        self.timer.stop()
        if not self.regenerate():
            raise ValueError(self.keyword_error.text())
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
                self.extra.toPlainText(),
                self.item_instructions,
                self.group_instructions,
                self.character_name.text().strip(),
                self.keyword_fields(),
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

    def build_character_panel(self, layout):
        self.character_panel = QFrame()
        self.character_panel.setObjectName("card")
        outer = QVBoxLayout(self.character_panel)
        top = QHBoxLayout()
        top.addWidget(QLabel("项目名"))
        self.character_name.setPlaceholderText("例如：鲸鱼娘 · 像素风")
        top.addWidget(self.character_name, 1)
        self.character_profiles = QComboBox()
        self.character_profiles.setMinimumWidth(180)
        top.addWidget(self.character_profiles)
        self.save_character = QPushButton("保存角色")
        self.save_character.clicked.connect(self.save_character_profile)
        top.addWidget(self.save_character)
        outer.addLayout(top)
        body = QHBoxLayout()
        fields = QVBoxLayout()
        fields.addWidget(self.style)
        self.extra.hide()
        self.keyword_scroll = QScrollArea()
        self.keyword_scroll.setWidgetResizable(True)
        self.keyword_scroll.setFixedHeight(92)
        self.keyword_body = QWidget()
        self.keyword_layout = QVBoxLayout(self.keyword_body)
        self.keyword_layout.setContentsMargins(0, 0, 0, 0)
        self.keyword_layout.setSpacing(4)
        self.keyword_scroll.setWidget(self.keyword_body)
        fields.addWidget(self.keyword_scroll)
        row = QHBoxLayout()
        add_keyword = QPushButton("添加关键字")
        add_keyword.clicked.connect(lambda: self.add_keyword())
        row.addWidget(add_keyword)
        self.keyword_error = QLabel()
        self.keyword_error.setWordWrap(True)
        row.addWidget(self.keyword_error, 1)
        fields.addLayout(row)
        body.addLayout(fields, 3)
        if self.ai is not None:
            self.ai.sections.setTabText(0, "接口")
            self.ai.reference_controls.setMaximumHeight(132)
            self.ai.reference_list.setMinimumHeight(60)
            body.addWidget(self.ai.reference_controls, 2)
            self.ai.references_changed.connect(self.character_references_changed)
        outer.addLayout(body)
        layout.insertWidget(1, self.character_panel)
        self.character_panel.hide()
        self.refresh_character_profiles()
        self.character_profiles.activated.connect(self.load_character_profile)

    def refresh_character_profiles(self, selected=None):
        from .characters import profiles
        import zipfile

        self.character_profiles.blockSignals(True)
        self.character_profiles.clear()
        self.character_profiles.addItem("载入已存角色…", None)
        for path in profiles():
            try:
                with zipfile.ZipFile(path) as archive:
                    name = json.loads(archive.read("project.json"))["name"]
                self.character_profiles.addItem(name, str(path))
                if selected and str(path) == str(selected):
                    self.character_profiles.setCurrentIndex(
                        self.character_profiles.count() - 1
                    )
            except (OSError, ValueError, KeyError, zipfile.BadZipFile):
                continue
        self.character_profiles.blockSignals(False)

    def character_references_changed(self):
        if self.loading or self.ai is None:
            return
        from .characters import cache_reference

        self.reference_paths = [cache_reference(raw) for raw in self.ai.references]
        self.edited()

    def restore_character_references(self):
        if self.ai is None:
            return
        self.ai.references.clear()
        self.ai.reference_list.clear()
        valid = []
        for filename in self.reference_paths[:8]:
            try:
                with Image.open(filename) as image:
                    self.ai.append_reference(image, Path(filename).name)
                valid.append(filename)
            except (OSError, ValueError):
                continue
        self.reference_paths = valid

    def save_character_profile(self):
        from .characters import save_profile

        try:
            if not self.regenerate():
                return
            path = save_profile(
                self.character_name.text(), self.project_getter().settings["ai_prompt"]
            )
            self.refresh_character_profiles(path)
            self.save_character.setToolTip(
                "角色、参考图、共享指令、分组前缀、选择、帧数和尺寸已保存"
            )
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "保存角色", str(error))

    def load_character_profile(self, index):
        from .characters import load_profile

        path = self.character_profiles.itemData(index)
        if not path:
            return
        try:
            name, config = load_profile(path)
            config["character_name"] = name
            self.project_getter().settings["ai_prompt"] = config
            self.reload()
            self.regenerate()
            self.refresh_character_profiles(path)
        except (OSError, ValueError, KeyError) as error:
            QMessageBox.warning(self, "载入角色", str(error))

    def group_leaves(self, group):
        result = []
        for index in range(group.childCount()):
            child = group.child(index)
            if child.childCount():
                result.extend(self.group_leaves(child))
            else:
                result.append(child)
        return result

    def set_group_count(self, key, count):
        count = max(1, min(MAX_FRAMES, count))
        group = next(
            (item for path, item in self.groups.items() if "/".join(path) == key), None
        )
        if group is None:
            return
        was_loading = self.loading
        self.loading = True
        try:
            for leaf in self.group_leaves(group):
                leaf.setText(1, str(count))
        finally:
            self.loading = was_loading
        self.timer.start()

    def refresh_group_counts(self):
        was_loading = self.loading
        self.loading = True
        try:
            for item in self.groups.values():
                values = {leaf.text(1) for leaf in self.group_leaves(item)}
                item.setText(1, next(iter(values)) if len(values) == 1 else "—")
        finally:
            self.loading = was_loading

    def set_group_instruction(self, key, text):
        text = text.strip()
        if text:
            self.group_instructions[key] = text
        else:
            self.group_instructions.pop(key, None)
        item = next(
            (item for path, item in self.groups.items() if "/".join(path) == key), None
        )
        if item:
            item.setText(2, text.replace("\n", " / "))
            item.setToolTip(2, text)
        self.regenerate()

    def sync_row_backgrounds(self):
        was_loading = self.loading
        self.loading = True
        try:
            dark = self.tree.palette().base().color().lightness() < 128
            enabled = QBrush(QColor("#294D41" if dark else "#E1F0E8"))
            for item in [*self.leaves.values(), *self.groups.values()]:
                brush = (
                    enabled
                    if item.checkState(0) != Qt.CheckState.Unchecked
                    else QBrush()
                )
                for column in range(3):
                    if item.background(column) != brush:
                        item.setBackground(column, brush)
        finally:
            self.loading = was_loading

    def keyword_fields(self):
        return [
            dict(key=key.text(), value=value.text())
            for widget, key, value in self.keyword_rows
        ]

    def add_keyword(self, key="", value=""):
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(5)
        name = QLineEdit(key)
        name.setPlaceholderText("关键字")
        name.setFixedWidth(125)
        content = QLineEdit(value)
        content.setPlaceholderText("关键字描述")
        remove = QPushButton("×")
        remove.setFixedSize(28, 32)
        remove.setStyleSheet("padding:2px;")
        remove.setToolTip("删除关键字")
        row.addWidget(name)
        row.addWidget(content, 1)
        row.addWidget(remove)
        record = (widget, name, content)
        self.keyword_rows.append(record)
        self.keyword_layout.addWidget(widget)

        def delete():
            self.keyword_rows.remove(record)
            widget.deleteLater()
            self.edited()

        remove.clicked.connect(delete)
        name.textChanged.connect(self.edited)
        content.textChanged.connect(self.edited)
        if not self.loading:
            name.setFocus()
            self.edited()
        return name, content

    def restore_keyword_rows(self, fields):
        for widget, key, value in self.keyword_rows:
            widget.setParent(None)
            widget.deleteLater()
        self.keyword_rows.clear()
        for field in fields:
            self.add_keyword(str(field.get("key", "")), str(field.get("value", "")))
