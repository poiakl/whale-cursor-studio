"""OpenAI-compatible Images adapter and cancellable asynchronous request queue."""

from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from PIL import Image
from PySide6.QtCore import QObject, Signal, QTimer, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from .codec import split_sheet, validate
from .project import user_dir

MAX_RESPONSE = 48 * 1024 * 1024
MAX_IMAGE = 24 * 1024 * 1024


@dataclass
class ApiConfig:
    base_url: str = "https://api.openai.com/v1"
    model: str = ""
    size: str = "跟随序列图"
    transparent: bool = True
    key: str = field(default="", repr=False)

    def validate(self):
        checked_url(self.base_url)
        if not self.model.strip() or not self.key.strip():
            raise ValueError("请在 API 设置中填写模型名和 API Key。")
        if "\n" in self.key or "\r" in self.key:
            raise ValueError("API Key 格式有误。")
        if self.size != "跟随序列图":
            parts = self.size.split("x")
            if len(parts) != 2 or not all(
                p.isdigit() and 32 <= int(p) <= 8192 for p in parts
            ):
                raise ValueError("请求尺寸填写为 WIDTHxHEIGHT，例如 1024x1024。")

    def save(self):
        path = user_dir() / "ai-api.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(
            json.dumps(
                dict(
                    base_url=self.base_url,
                    model=self.model,
                    size=self.size,
                    transparent=self.transparent,
                )
            ),
            encoding="utf-8",
        )
        temp.replace(path)

    @classmethod
    def load(cls):
        try:
            data = json.loads((user_dir() / "ai-api.json").read_text(encoding="utf-8"))
            return cls(
                **{
                    k: data[k]
                    for k in ("base_url", "model", "size", "transparent")
                    if k in data
                }
            )
        except (OSError, ValueError, TypeError):
            return cls()


def checked_url(url):
    parsed = urlsplit(url)
    local = parsed.hostname in ("127.0.0.1", "localhost", "::1")
    if (
        (parsed.scheme != "https" and not (parsed.scheme == "http" and local))
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "接口地址须使用 HTTPS；本机服务可使用 HTTP。地址不要带 Key、查询参数或账号密码。"
        )
    return url.rstrip("/")


def decode_image(raw):
    if not raw or len(raw) > MAX_IMAGE:
        raise ValueError("返回图片为空或超过 24 MB。")
    with Image.open(io.BytesIO(raw)) as image:
        if image.width * image.height > 16_000_000:
            raise ValueError("返回图片超过 1600 万像素，请减小 API 请求尺寸。")
        return image.convert("RGBA")


def reference_image(image):
    if image.width * image.height > 16_000_000:
        raise ValueError("参考图超过 1600 万像素。")
    data = io.BytesIO()
    image.convert("RGBA").save(data, format="PNG")
    raw = data.getvalue()
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("单张参考图转换后不能超过 8 MB。")
    return raw


class OpenAIImagesAdapter:
    """Swap this adapter to add providers with different request/response schemas."""

    def build(self, config, prompt, spec, references):
        config.validate()
        if len(references) > 8 or sum(map(len, references)) > 32 * 1024 * 1024:
            raise ValueError("参考图最多 8 张，合计不超过 32 MB。")
        payload = dict(
            model=config.model.strip(),
            prompt=prompt,
            n=1,
            size=(
                f"{spec['sheet_px'][0]}x{spec['sheet_px'][1]}"
                if config.size == "跟随序列图"
                else config.size
            ),
        )
        if config.transparent:
            payload.update(background="transparent", output_format="png")
        base = checked_url(config.base_url)
        if not references:
            return (
                base + "/images/generations",
                "application/json",
                json.dumps(payload).encode(),
            )
        import uuid

        boundary = "whale-" + uuid.uuid4().hex
        parts = []
        for key, value in payload.items():
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
            )
        for index, raw in enumerate(references):
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="image[]"; filename="reference-{index + 1}.png"\r\nContent-Type: image/png\r\n\r\n'.encode()
                + raw
                + b"\r\n"
            )
        parts.append(f"--{boundary}--\r\n".encode())
        return (
            base + "/images/edits",
            "multipart/form-data; boundary=" + boundary,
            b"".join(parts),
        )

    def response(self, raw):
        data = json.loads(raw)
        images = data.get("data")
        if not isinstance(images, list) or not images:
            raise ValueError(
                "接口没有返回 data 图片数组；请检查是否为 Images 生图接口。"
            )
        item = images[0]
        if item.get("b64_json"):
            return decode_image(base64.b64decode(item["b64_json"], validate=True)), None
        if item.get("url"):
            url = item["url"]
            parsed = urlsplit(url)
            # Signed download URLs may contain a query, but never receive the API key.
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ValueError("返回图片下载地址须使用 HTTPS。")
            return None, url
        raise ValueError("接口未返回 b64_json 或图片 URL。")


def result_frames(image, spec, pixel):
    cols, rows = spec["sheet_grid"]
    if image.width % cols or image.height % rows:
        raise ValueError(
            f"返回图 {image.width}×{image.height} 无法按 {cols}×{rows} 整齐切片。请调整 API 请求尺寸或重新生成。"
        )
    if image.width // cols != image.height // rows:
        raise ValueError(
            "返回图的单格不是正方形，不能直接覆盖。请让 API 请求尺寸与序列图比例一致后重新生成。"
        )
    frames = split_sheet(image, cols, rows, spec["frame_count"], pixel)
    size = spec["cell_px"][0]
    for frame, metadata in zip(frames, spec["timeline"]):
        frame.x, frame.y = (
            max(0, min(255, round(value * 256 / size)))
            for value in metadata["hotspot_px"]
        )
        frame.duration = metadata["duration_ms"]
        validate(frame)
    return frames


class GenerationQueue(QObject):
    result = Signal(object, object)
    progress = Signal(str)
    failed = Signal(str)
    done = Signal()

    def __init__(self, config, tasks, references, parent=None, adapter=None):
        super().__init__(parent)
        self.config = config
        self.tasks = list(tasks)
        self.references = list(references)
        self.adapter = adapter or OpenAIImagesAdapter()
        self.manager = QNetworkAccessManager(self)
        self.reply = None
        self.cancelled = False
        self.current = None
        self.buffer = bytearray()
        self.mode = "api"
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(180000)
        self.timer.timeout.connect(self.timeout)
        self.error_override = ""
        self._finished = False

    def start(self):
        self.next_request()

    def next_request(self):
        if self._finished:
            return
        if self.cancelled or not self.tasks:
            self.finish()
            return
        self.current = self.tasks.pop(0)
        self.progress.emit("正在生成：" + self.current["spec"]["purpose"])
        try:
            url, content_type, body = self.adapter.build(
                self.config,
                self.current["prompt"],
                self.current["spec"],
                self.references,
            )
            request = self.request(url)
            request.setHeader(
                QNetworkRequest.KnownHeaders.ContentTypeHeader, content_type
            )
            request.setRawHeader(
                b"Authorization", ("Bearer " + self.config.key.strip()).encode()
            )
            self.mode = "api"
            self.begin(self.manager.post(request, body))
        except Exception as error:
            self.fail(str(error))

    @staticmethod
    def request(url):
        request = QNetworkRequest(QUrl(url))
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.ManualRedirectPolicy,
        )
        request.setTransferTimeout(180000)
        return request

    def begin(self, reply):
        self.reply = reply
        self.buffer = bytearray()
        self.error_override = ""
        reply.readyRead.connect(self.read)
        reply.finished.connect(self.completed)
        self.timer.start()

    def read(self):
        self.buffer.extend(bytes(self.reply.readAll()))
        if len(self.buffer) > MAX_RESPONSE:
            self.error_override = "接口响应超过 48 MB，已停止接收。"
            if self.reply.isRunning():
                self.reply.abort()

    def completed(self):
        self.timer.stop()
        reply = self.reply
        self.read()
        self.reply = None
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        error = reply.error()
        reply.deleteLater()
        if self.cancelled:
            self.finish()
            return
        if (
            self.error_override
            or error != QNetworkReply.NetworkError.NoError
            or not status
            or not 200 <= int(status) < 300
        ):
            # Never echo raw provider bodies, URLs, prompts, or credentials to logs.
            self.fail(
                self.error_override
                or f"请求失败（HTTP {status or '无响应'}）。请检查地址、Key、模型、尺寸及账号额度；参考图需要支持 images/edits。"
            )
            return
        try:
            if self.mode == "api":
                image, url = self.adapter.response(bytes(self.buffer))
                if url:
                    self.mode = "download"
                    self.begin(self.manager.get(self.request(url)))
                    return
            else:
                image = decode_image(bytes(self.buffer))
            self.result.emit(self.current, image)
            QTimer.singleShot(0, self.next_request)
        except Exception as error:
            self.fail(
                "无法读取返回图："
                + (
                    str(error)
                    if isinstance(error, ValueError)
                    else "图片或响应格式不正确。"
                )
            )

    def timeout(self):
        self.error_override = "请求超过 180 秒，已停止等待；不会自动重试扣费请求。"
        if self.reply:
            self.reply.abort()

    def cancel(self):
        self.cancelled = True
        self.tasks.clear()
        if self.reply:
            self.reply.abort()
        else:
            self.finish()

    def fail(self, message):
        self.failed.emit(
            message.replace(self.config.key, "[Key]") if self.config.key else message
        )
        self.tasks.clear()
        self.finish()

    def finish(self):
        if self._finished:
            return
        self._finished = True
        self.timer.stop()
        self.config.key = ""
        self.done.emit()
