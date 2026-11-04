"""Local, deterministic image-generation briefs; no remote AI service is used."""

from __future__ import annotations

import json
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
    QGridLayout,
)
from .codec import MAX_FRAMES
from .generation_brief import entries, make_brief, legacy_default_instruction
from .prompt_hierarchy import (
    default_instruction,
    group_defaults,
    ROLE_FEATURES,
    prompt_sections,
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
        self.character_button.toggled.connect(
            lambda checked: self.character_button.setText(
                "角色配置 ▴" if checked else "角色配置 ▾"
            )
        )
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
            if config.get("prompt_schema", 1) < 3:
                for key, path, title, frames, role in entries(project):
                    if self.item_instructions.get(key) in (
                        legacy_default_instruction(key, role, title),
                        ROLE_FEATURES.get(role) if key.startswith("events.") else None,
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
            prompt_sections(fields)
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
                prompt_schema=3,
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
        self.keyword_scroll.setFixedHeight(116)
        self.keyword_body = QWidget()
        self.keyword_layout = QGridLayout(self.keyword_body)
        self.keyword_layout.setContentsMargins(0, 0, 0, 0)
        self.keyword_layout.setSpacing(4)
        self.keyword_scroll.setWidget(self.keyword_body)
        fields.addWidget(self.keyword_scroll)
        row = QHBoxLayout()
        add_keyword = QPushButton("添加提示词分类")
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
            dict(key=key.text(), value=value.toPlainText())
            for widget, key, value in self.keyword_rows
        ]

    def add_keyword(self, key="", value=""):
        widget = QFrame()
        box = QVBoxLayout(widget)
        box.setContentsMargins(2, 2, 2, 2)
        box.setSpacing(3)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        name = QLineEdit(key)
        name.setPlaceholderText("分类名称，例如衣服、动作")
        name.setStyleSheet("padding:2px 5px;min-height:18px;")
        name.setFixedHeight(26)
        content = QPlainTextEdit(value)
        content.setPlaceholderText((key or "本分类") + "提示词；将合并到全局提示词")
        content.setFixedHeight(58)
        content.setStyleSheet("padding:3px;")
        remove = QPushButton("×")
        remove.setFixedSize(25, 25)
        remove.setStyleSheet("padding:2px;")
        remove.setToolTip("删除这个提示词分类")
        row.addWidget(name, 1)
        row.addWidget(remove)
        box.addLayout(row)
        box.addWidget(content)
        record = (widget, name, content)
        self.keyword_rows.append(record)
        self.relayout_prompt_sections()

        def delete():
            self.keyword_rows.remove(record)
            self.keyword_layout.removeWidget(widget)
            widget.deleteLater()
            self.relayout_prompt_sections()
            self.edited()

        remove.clicked.connect(delete)
        name.textChanged.connect(
            lambda text: content.setPlaceholderText(
                (text or "本分类") + "提示词；将合并到全局提示词"
            )
        )
        name.textChanged.connect(self.edited)
        content.textChanged.connect(self.edited)
        if not self.loading:
            name.setFocus()
            self.edited()
        return name, content

    def relayout_prompt_sections(self):
        for index, (widget, name, content) in enumerate(self.keyword_rows):
            self.keyword_layout.addWidget(widget, index // 2, index % 2)

    def restore_keyword_rows(self, fields):
        for widget, key, value in self.keyword_rows:
            widget.setParent(None)
            widget.deleteLater()
        self.keyword_rows.clear()
        for field in fields:
            self.add_keyword(str(field.get("key", "")), str(field.get("value", "")))
