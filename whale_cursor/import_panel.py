from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from .asset_import import (
    IMAGE_FILTER,
    configured_target,
    read_frames,
    read_config,
    parse_manifest,
)
from .codec import MAX_FRAMES


class ImportPanel(QDialog):
    def __init__(self, target, title, pixel, parent=None):
        super().__init__(parent)
        self.target, self.pixel = target, pixel
        self.paths, self.manifest = [], {}
        self.frames = []
        self.package = None
        self.config_data = None
        self.metadata_only = None
        self.setWindowTitle("导入当前动画")
        self.resize(490, 280)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(title))
        row = QHBoxLayout()
        self.file_label = QLabel("请选择素材")
        self.file_label.setWordWrap(True)
        row.addWidget(self.file_label, 1)
        choose = QPushButton("选择素材…")
        choose.clicked.connect(self.choose_files)
        row.addWidget(choose)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.config_label = QLabel("未选择配置")
        self.config_label.setWordWrap(True)
        row.addWidget(self.config_label, 1)
        config = QPushButton("导入配置文件…")
        config.clicked.connect(self.choose_config)
        row.addWidget(config)
        layout.addLayout(row)
        form = QFormLayout()
        self.split = QCheckBox("切分精灵图")
        form.addRow(self.split)
        from .ui import NumberBox

        self.columns, self.rows, self.count = NumberBox(), NumberBox(), NumberBox()
        for field in (self.columns, self.rows, self.count):
            field.setRange(1, MAX_FRAMES)
        grid = QHBoxLayout()
        grid.addWidget(self.columns)
        grid.addWidget(QLabel("列 ×"))
        grid.addWidget(self.rows)
        grid.addWidget(QLabel("行"))
        form.addRow("网格", grid)
        self.count.setMinimum(0)
        self.count.setSpecialValueText("全部")
        self.count.setValue(0)
        last = parent.preferences.get(
            "sheet_cut", {"columns": 4, "rows": 4, "count": 16}
        )
        self.columns.setValue(last["columns"])
        self.rows.setValue(last["rows"])

        def limit_count():
            if self.split.isChecked():
                self.count.setMaximum(
                    min(MAX_FRAMES, self.columns.value() * self.rows.value())
                )
                if self.count.value() == 0:
                    self.count.setValue(last["count"])
            else:
                self.count.setMaximum(MAX_FRAMES)

        self.split.toggled.connect(limit_count)
        self.columns.valueChanged.connect(limit_count)
        self.rows.valueChanged.connect(limit_count)
        form.addRow("读取帧数", self.count)
        self.split.toggled.connect(self.columns.setEnabled)
        self.split.toggled.connect(self.rows.setEnabled)
        self.columns.setEnabled(False)
        self.rows.setEnabled(False)
        layout.addLayout(form)
        self.append = QCheckBox("追加到当前动画")
        layout.addWidget(self.append)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.read_selection)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择动画或图片", "", IMAGE_FILTER + ";;单动画包 (*.whaleanim)"
        )
        if paths:
            self.paths = paths
            self.file_label.setText(
                Path(paths[0]).name if len(paths) == 1 else f"已选 {len(paths)} 个文件"
            )
            self.configure()

    def choose_config(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入配置文件", "", "JSON (*.json)")
        if not path:
            return
        try:
            data = read_config(path)
            records = data.get("items") if isinstance(data, dict) else None
            manifest = (
                parse_manifest(data)
                if isinstance(records, list)
                and any(
                    isinstance(item, dict) and "frame_count" in item for item in records
                )
                else {}
            )
            self.config_data = data
            self.manifest = manifest
            self.config_label.setText(Path(path).name)
            self.configure()
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "配置文件有误", str(error))

    def configure(self):
        spec = self.manifest.get(self.target)
        if spec is None:
            return
        columns, rows = spec["sheet_grid"]
        self.columns.setValue(columns)
        self.rows.setValue(rows)
        self.split.setChecked(len(self.paths) <= 1 and columns * rows > 1)
        self.count.setValue(spec["frame_count"])

    def read_selection(self):
        if not self.paths and self.config_data is not None:
            if self.manifest:
                spec = self.manifest.get(self.target)
                if not spec:
                    QMessageBox.warning(
                        self, "导入当前动画", f"配置文件缺少当前动画 {self.target}"
                    )
                    return
                self.metadata_only = {"items": [spec]}
            else:
                self.metadata_only = self.config_data
            self.accept()
            return
        if not self.paths:
            QMessageBox.warning(self, "导入当前动画", "请先选择素材。")
            return
        try:
            if (
                len(self.paths) == 1
                and Path(self.paths[0]).suffix.lower() == ".whaleanim"
            ):
                if self.append.isChecked():
                    raise ValueError("单动画包需要以替换方式导入")
                self.package = self.paths[0]
            else:
                if len(self.paths) == 1 and self.manifest:
                    matched = configured_target(
                        self.paths[0], self.manifest, self.manifest
                    )
                    if matched and matched != self.target:
                        raise ValueError(
                            f"该文件对应 {matched}。请切换到对应动画或使用批量匹配。"
                        )
                spec = self.manifest.get(self.target)
                if self.config_data is not None and not self.manifest:
                    raise ValueError(
                        "单独的热点配置请在素材载入后使用，仅选择配置文件即可。"
                    )
                if self.manifest and not spec:
                    raise ValueError(f"配置文件缺少当前动画 {self.target}")
                grid = (
                    (self.columns.value(), self.rows.value())
                    if self.split.isChecked()
                    else (1, 1)
                )
                self.frames = read_frames(
                    self.paths, self.pixel, grid, self.count.value() or None, spec
                )
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "导入失败", str(error))
            return
        self.accept()
