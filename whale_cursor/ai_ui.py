"""Reference images, API settings, generation previews, explicit frame replacement."""

from __future__ import annotations

import copy
import uuid
import shutil
from pathlib import Path
from PIL import Image
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon, QPainter, QColor
from PySide6.QtWidgets import (
    QWidget,
    QTabWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFormLayout,
    QDialogButtonBox,
    QLineEdit,
    QCheckBox,
    QFileDialog,
    QMessageBox,
    QSizePolicy,
)
from .ai_api import ApiConfig, GenerationQueue, reference_image, result_frames
from .runtime import pixmap
from .project import user_dir


class ImagePreview(QLabel):
    def __init__(self):
        super().__init__()
        self.picture = None

    def setPixmap(self, picture):
        self.picture = picture
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#EDF3EF"))
        if self.picture is None:
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "返回图片预览")
            return
        image = self.picture.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        painter.drawPixmap(
            (self.width() - image.width()) // 2,
            (self.height() - image.height()) // 2,
            image,
        )


class GenerationPanel(QWidget):
    def __init__(
        self, project_getter, task_getter, apply_frames, frame_getter, parent=None
    ):
        super().__init__(parent)
        self.project_getter = project_getter
        self.task_getter = task_getter
        self.apply_frames = apply_frames
        self.frame_getter = frame_getter
        self.config = ApiConfig.load()
        self.references = []
        self.results = []
        self.queue = None
        layout = QVBoxLayout(self)
        self.sections = QTabWidget()
        references_page = QWidget()
        reference_layout = QVBoxLayout(references_page)
        results_page = QWidget()
        result_layout = QVBoxLayout(results_page)
        self.sections.addTab(references_page, "参考与接口")
        self.sections.addTab(results_page, "返回结果")
        layout.addWidget(self.sections, 1)
        row = QHBoxLayout()
        self.config_button = QPushButton("API 设置")
        self.config_button.clicked.connect(self.configure)
        row.addWidget(self.config_button)
        self.destination = QLabel()
        self.destination.setWordWrap(True)
        row.addWidget(self.destination, 1)
        reference_layout.addLayout(row)
        self.update_destination()
        row = QHBoxLayout()
        self.add_reference = QPushButton("添加参考图…")
        self.add_reference.clicked.connect(self.choose_references)
        row.addWidget(self.add_reference)
        self.current_reference = QPushButton("用当前帧")
        self.current_reference.clicked.connect(self.use_current_frame)
        row.addWidget(self.current_reference)
        self.remove_reference = QPushButton("移除")
        self.remove_reference.clicked.connect(self.remove_references)
        row.addWidget(self.remove_reference)
        reference_layout.addLayout(row)
        self.reference_list = QListWidget()
        self.reference_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.reference_list.setIconSize(QSize(56, 56))
        self.reference_list.setMinimumHeight(80)
        self.reference_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        reference_layout.addWidget(self.reference_list, 1)
        row = QHBoxLayout()
        self.send = QPushButton("发送选中项给 AI")
        self.send.setObjectName("primary")
        self.send.clicked.connect(self.generate)
        row.addWidget(self.send)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        row.addWidget(self.cancel_button)
        layout.addLayout(row)
        self.status = QLabel(
            "点击发送才会将勾选项的文字和全部参考图发送到所设接口。每项单独请求，可能产生 API 费用。"
        )
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.result_list = QComboBox()
        self.result_list.currentIndexChanged.connect(self.show_result)
        result_layout.addWidget(self.result_list)
        self.preview = ImagePreview()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(100, 80)
        self.preview.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        self.preview.setStyleSheet("background:#EDF3EF;border-radius:8px;")
        result_layout.addWidget(self.preview, 1)
        self.details = QLabel("返回后选择结果，再覆盖对应的整组帧。支持撤销。")
        self.details.setWordWrap(True)
        self.details.setMinimumHeight(48)
        result_layout.addWidget(self.details)
        row = QHBoxLayout()
        self.save_result = QPushButton("保存返回图…")
        self.save_result.clicked.connect(self.save_image)
        row.addWidget(self.save_result)
        self.apply = QPushButton("覆盖对应帧")
        self.apply.setObjectName("primary")
        self.apply.clicked.connect(self.replace_frames)
        row.addWidget(self.apply)
        result_layout.addLayout(row)
        self.apply.setEnabled(False)
        self.save_result.setEnabled(False)

    def update_destination(self):
        self.destination.setText(
            (self.config.base_url + "\n" + self.config.model)
            if self.config.model
            else "配置支持 Images 的生图服务；Key 仅在本次运行中使用。"
        )

    def configure(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("AI 图片 API · OpenAI 兼容接口")
        dialog.resize(580, 350)
        form = QFormLayout(dialog)
        address = QLineEdit(self.config.base_url)
        address.setPlaceholderText("https://服务地址/v1")
        model = QLineEdit(self.config.model)
        model.setPlaceholderText("填写服务商提供的生图模型 ID")
        key = QLineEdit(self.config.key)
        key.setEchoMode(QLineEdit.EchoMode.Password)
        size = QComboBox()
        size.setEditable(True)
        size.addItems(["跟随序列图", "1024x1024", "1536x1024", "1024x1536"])
        size.setCurrentText(self.config.size)
        transparent = QCheckBox("请求透明 PNG（模型需支持）")
        transparent.setChecked(self.config.transparent)
        form.addRow("Base URL", address)
        form.addRow("模型", model)
        form.addRow("API Key", key)
        form.addRow("API 请求尺寸", size)
        form.addRow(transparent)
        note = QLabel(
            "Base URL 填到 /v1，不填完整方法路径。无参考图使用 images/generations，有参考图使用 images/edits。请求尺寸需满足模型限制并与序列图比例一致。Key 不保存到文件或完整方案。"
        )
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )

        def save():
            candidate = ApiConfig(
                address.text().strip().rstrip("/"),
                model.text().strip(),
                size.currentText().strip(),
                transparent.isChecked(),
                key.text().strip(),
            )
            try:
                candidate.validate()
                candidate.save()
            except Exception as error:
                QMessageBox.warning(dialog, "配置有误", str(error))
                return
            self.config = candidate
            self.update_destination()
            dialog.accept()

        buttons.accepted.connect(save)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        dialog.exec()

    def append_reference(self, image, name):
        if len(self.references) >= 8:
            raise ValueError("参考图最多 8 张。")
        raw = reference_image(image)
        if sum(len(data) for data in self.references) + len(raw) > 32 * 1024 * 1024:
            raise ValueError("参考图合计不能超过 32 MB。")
        self.references.append(raw)
        self.reference_list.addItem(QListWidgetItem(QIcon(pixmap(image)), name))

    def choose_references(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "添加参考图", "", "图片 (*.png *.jpg *.jpeg *.webp *.gif)"
        )
        try:
            for path in paths:
                with Image.open(path) as image:
                    self.append_reference(image, Path(path).name)
        except Exception as error:
            QMessageBox.warning(self, "参考图", str(error))

    def use_current_frame(self):
        try:
            frame = self.frame_getter()
            if frame is None:
                raise ValueError("制作页当前没有可用帧。")
            self.append_reference(frame.image, "当前帧")
        except Exception as error:
            QMessageBox.warning(self, "参考图", str(error))

    def remove_references(self):
        for index in sorted(
            (
                self.reference_list.row(item)
                for item in self.reference_list.selectedItems()
            ),
            reverse=True,
        ):
            self.reference_list.takeItem(index)
            del self.references[index]

    def generate(self):
        if self.queue is not None:
            return
        try:
            self.config.validate()
            tasks = self.task_getter()
            if not tasks:
                raise ValueError("请先勾选需要生成的内容。")
        except Exception as error:
            QMessageBox.warning(self, "尚未发送", str(error))
            return
        project = self.project_getter()
        for task in tasks:
            task["project"] = project
            task["pixel"] = project.pixel
        queue = GenerationQueue(copy.copy(self.config), tasks, self.references, self)
        self.queue = queue
        self._request_failed = False
        self._request_cancelled = False
        queue.progress.connect(self.status.setText)
        queue.result.connect(self.received)
        queue.failed.connect(self.failed)
        queue.done.connect(self.finished)
        for control in (
            self.send,
            self.config_button,
            self.add_reference,
            self.current_reference,
            self.remove_reference,
        ):
            control.setEnabled(False)
        self.cancel_button.setEnabled(True)
        queue.start()

    def received(self, task, image):
        try:
            folder = user_dir() / "ai-results"
            folder.mkdir(exist_ok=True)
            path = folder / (uuid.uuid4().hex + ".png")
            image.save(path, format="PNG", compress_level=1)
        except Exception:
            self.failed("无法保存返回图，未改动原素材。请检查磁盘空间。")
            return
        thumbnail = image.copy()
        thumbnail.thumbnail((640, 640))
        result = dict(
            task=task,
            image=thumbnail,
            image_path=path,
            image_size=image.size,
            applied=False,
            problem="",
        )
        try:
            cols, rows = task["spec"]["sheet_grid"]
            if (
                image.width % cols
                or image.height % rows
                or image.width // cols != image.height // rows
            ):
                result["problem"] = (
                    "图片与序列图行列比例不符，不能直接覆盖。请调整 API 请求尺寸或重新生成。"
                )
        except Exception:
            result["problem"] = "返回数据不完整。"
        self.results.append(result)
        self.result_list.addItem(task["spec"]["purpose"])
        self.result_list.setCurrentIndex(len(self.results) - 1)
        self.show_result()
        self.sections.setCurrentIndex(1)

    def failed(self, message):
        self._request_failed = True
        self.status.setText(message)

    def finished(self):
        queue = self.queue
        self.queue = None
        for control in (
            self.send,
            self.config_button,
            self.add_reference,
            self.current_reference,
            self.remove_reference,
        ):
            control.setEnabled(True)
        self.cancel_button.setEnabled(False)
        if not self._request_failed:
            self.status.setText(
                "已取消；已经提交的请求仍可能计费，之前返回的结果可以继续使用。"
                if self._request_cancelled
                else "生成完成。请预览后覆盖对应帧。"
            )
        if queue is not None:
            queue.deleteLater()

    def cancel(self):
        if self.queue is not None:
            self._request_cancelled = True
            self.queue.cancel()

    def selected_result(self):
        index = self.result_list.currentIndex()
        return self.results[index] if 0 <= index < len(self.results) else None

    def show_result(self, *args):
        result = self.selected_result()
        if result is None:
            return
        image, spec = result["image"], result["task"]["spec"]
        self.preview.setPixmap(pixmap(image))
        self.details.setText(
            result["problem"]
            or f"{spec['purpose']} · 返回 {result['image_size'][0]}×{result['image_size'][1]} · 按 {spec['sheet_grid'][0]}×{spec['sheet_grid'][1]} 切分 {spec['frame_count']} 帧；保留发送时的热点和时长。"
        )
        self.save_result.setEnabled(True)
        self.apply.setEnabled(not result["problem"] and not result["applied"])

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.show_result()

    def replace_frames(self):
        result = self.selected_result()
        if result is None or result["problem"] or result["applied"]:
            return
        try:
            task = result["task"]
            if self.project_getter() is not task["project"]:
                raise ValueError(
                    "方案已切换或撤销，此结果不再自动覆盖。可先保存返回图，再在制作页导入。"
                )
            with Image.open(result["image_path"]) as image:
                frames = result_frames(
                    image.convert("RGBA"), task["spec"], task["pixel"]
                )
            self.apply_frames(task["project"], task["spec"]["id"], frames)
            result["applied"] = True
            self.show_result()
            self.status.setText("已覆盖：" + task["spec"]["purpose"] + "。可撤销。")
        except Exception as error:
            QMessageBox.warning(self, "未覆盖原素材", str(error))

    def save_image(self):
        result = self.selected_result()
        if result is None:
            return
        name = result["task"]["spec"]["id"] + ".png"
        path, _ = QFileDialog.getSaveFileName(
            self, "保存 AI 返回图", name, "PNG (*.png)"
        )
        if path:
            try:
                if Path(path).resolve() != result["image_path"].resolve():
                    shutil.copyfile(result["image_path"], path)
            except Exception as error:
                QMessageBox.warning(self, "保存失败", str(error))
