import base64
import io
import json
import pytest
from PIL import Image
from PySide6.QtCore import QObject, Signal, QTimer, Qt
from PySide6.QtNetwork import QNetworkReply
from PySide6.QtWidgets import QMessageBox
from whale_cursor.ai_api import (
    ApiConfig,
    OpenAIImagesAdapter,
    GenerationQueue,
    result_frames,
)
from whale_cursor.project import Project, user_dir
from whale_cursor.prompts import make_brief
from whale_cursor.ui import Window


def task(project, key="slots.Arrow", count=2):
    prompt = make_brief(project, {key: count}, 256, 2, "像素鲸鱼")
    spec = json.loads(prompt[prompt.index("{") :])["items"][0]
    return dict(prompt=prompt, spec=spec, project=project, pixel=project.pixel)


def png(color="red"):
    image = Image.new("RGBA", (512, 256), color)
    data = io.BytesIO()
    image.save(data, format="PNG")
    return data.getvalue()


def test_adapter_json_and_multiple_reference_multipart():
    adapter = OpenAIImagesAdapter()
    t = task(Project.defaults())
    config = ApiConfig(model="test-image", key="secret-api-key")
    url, kind, body = adapter.build(config, t["prompt"], t["spec"], [])
    assert url.endswith("/v1/images/generations") and kind == "application/json"
    payload = json.loads(body)
    assert payload["size"] == "512x256" and payload["background"] == "transparent"
    assert payload["model"] == "test-image" and "secret-api-key" not in body.decode()
    url, kind, body = adapter.build(
        config, t["prompt"], t["spec"], [png(), png("blue")]
    )
    assert url.endswith("/images/edits") and kind.startswith("multipart/form-data")
    assert body.count(b'name="image[]"') == 2
    assert b"reference-1.png" in body and "像素鲸鱼".encode() in body
    assert b"secret-api-key" not in body
    with pytest.raises(ValueError):
        adapter.build(
            ApiConfig("http://untrusted.example/v1", "test", key="x"), "", t["spec"], []
        )


def test_api_key_never_persisted_with_settings_or_scheme(tmp_path):
    config = ApiConfig(model="test", key="secret-api-key")
    config.save()
    assert "secret-api-key" not in (user_dir() / "ai-api.json").read_text()
    assert ApiConfig.load().key == ""
    assert "secret-api-key" not in repr(config)
    p = Project.defaults()
    p.save(tmp_path / "scheme.whalepack")
    assert b"secret-api-key" not in (tmp_path / "scheme.whalepack").read_bytes()


def test_response_and_sheet_validation_preserve_hotspots_and_duration():
    adapter = OpenAIImagesAdapter()
    image, url = adapter.response(
        json.dumps({"data": [{"b64_json": base64.b64encode(png()).decode()}]}).encode()
    )
    assert image.size == (512, 256) and url is None
    p = Project.defaults()
    p.slots["Arrow"][0].x = 70
    p.slots["Arrow"][0].y = 93
    p.slots["Arrow"][0].duration = 170
    t = task(p)
    frames = result_frames(image, t["spec"], True)
    assert len(frames) == 2 and all(f.image.size == (256, 256) for f in frames)
    assert [(f.x, f.y, f.duration) for f in frames] == [(70, 93, 170)] * 2
    assert frames[0].image.getpixel((128, 128)) == (255, 0, 0, 255)
    for size in ((511, 256), (512, 512)):
        with pytest.raises(ValueError):
            result_frames(Image.new("RGBA", size), t["spec"], True)
    with pytest.raises(ValueError):
        adapter.response(b'{"data":[]}')
    with pytest.raises(ValueError):
        adapter.response(b'{"data":[{"url":"http://bad.example/image.png"}]}')


class Reply(QObject):
    readyRead = Signal()
    finished = Signal()

    def __init__(self, data, status=200):
        super().__init__()
        self.data = data
        self.status = status
        self.running = True

    def readAll(self):
        data, self.data = self.data, b""
        return data

    def attribute(self, key):
        return self.status

    def error(self):
        return QNetworkReply.NetworkError.NoError

    def isRunning(self):
        return self.running

    def complete(self):
        self.running = False
        self.readyRead.emit()
        self.finished.emit()

    def abort(self):
        self.running = False
        self.finished.emit()


class Manager:
    def __init__(self, replies):
        self.replies = replies
        self.calls = []

    def post(self, request, body):
        self.calls.append((request, body))
        reply = self.replies.pop(0)
        QTimer.singleShot(0, reply.complete)
        return reply

    def get(self, request):
        self.calls.append((request, None))
        reply = self.replies.pop(0)
        QTimer.singleShot(0, reply.complete)
        return reply


def drain(app, condition):
    from time import monotonic

    deadline = monotonic() + 3
    while not condition() and monotonic() < deadline:
        app.processEvents()
    assert condition()


def test_async_queue_download_key_isolation_batch_and_cancel(app):
    p = Project.defaults()
    config = ApiConfig(model="test", key="secret-key")
    queue = GenerationQueue(config, [task(p), task(p, "effects.click")], [])
    queue.manager = Manager(
        [
            Reply(
                b'{"data":[{"url":"https://images.example/image.png?signature=abc"}]}'
            ),
            Reply(png()),
            Reply(b'{"data":[]}'),
        ]
    )
    results, errors, done = [], [], []
    queue.result.connect(lambda t, image: results.append((t, image)))
    queue.failed.connect(errors.append)
    queue.done.connect(lambda: done.append(True))
    queue.start()
    assert not results  # post returns immediately
    drain(app, lambda: done)
    assert len(results) == 1 and len(errors) == 1
    assert queue.manager.calls[0][0].rawHeader("Authorization") == b"Bearer secret-key"
    assert not queue.manager.calls[1][0].rawHeader("Authorization")
    assert config.key == ""
    assert all("secret-key" not in e for e in errors)
    queue.deleteLater()
    cancelled = GenerationQueue(ApiConfig(model="test", key="s"), [task(p)], [])
    cancelled.cancel()
    cancelled.start()
    assert cancelled._finished and not cancelled.tasks
    cancelled.deleteLater()


def test_generated_result_covers_bound_target_once_and_is_undoable(app, monkeypatch):
    w = Window(Project.defaults())
    try:
        panel = w.prompt_page.ai
        p = w.project
        original = p.slots["Arrow"][0].image.tobytes()
        t = task(p)
        panel.received(t, Image.open(io.BytesIO(png())).convert("RGBA"))
        w.key = "Hand"  # a later editor selection must not change the target
        before_hand = p.slots["Hand"][0].image.tobytes()
        panel.replace_frames()
        assert len(p.slots["Arrow"]) == 2
        assert p.slots["Arrow"][0].image.tobytes() != original
        assert p.slots["Hand"][0].image.tobytes() == before_hand
        assert not panel.apply.isEnabled()
        w.undo()
        assert w.project.slots["Arrow"][0].image.tobytes() == original
        messages = []
        monkeypatch.setattr(QMessageBox, "warning", lambda *args: messages.append(args))
        panel.results[0]["applied"] = False
        panel.replace_frames()
        assert messages and len(w.project.slots["Arrow"]) == 1
    finally:
        w.save_timer.stop()
        w.close()
        w.deleteLater()
        app.processEvents()


def test_page_tasks_are_individually_bound_to_selected_purposes(app):
    w = Window(Project.defaults())
    try:
        page = w.prompt_page
        page.leaves["events.Arrow.idle"].setCheckState(0, Qt.CheckState.Checked)
        page.leaves["effects.click"].setCheckState(0, Qt.CheckState.Checked)
        tasks = page.generation_tasks()
        assert {t["spec"]["id"] for t in tasks} == {
            "slots.Arrow",
            "events.Arrow.idle",
            "effects.click",
        }
        assert all(
            len(json.loads(t["prompt"][t["prompt"].index("{") :])["items"]) == 1
            for t in tasks
        )
        page.ai.use_current_frame()
        assert len(page.ai.references) == 1
    finally:
        w.save_timer.stop()
        w.close()
        w.deleteLater()
        app.processEvents()


def test_cancel_inflight_never_sends_remaining_jobs(app):
    config = ApiConfig(model="test", key="secret")
    queue = GenerationQueue(config, [task(Project.defaults())] * 3, [])
    reply = Reply(b"")
    manager = type(
        "PendingManager",
        (),
        {
            "post": lambda self, req, body: reply,
        },
    )()
    queue.manager = manager
    results, done = [], []
    queue.result.connect(lambda *args: results.append(args))
    queue.done.connect(lambda: done.append(True))
    queue.start()
    assert queue.reply is reply and len(queue.tasks) == 2
    queue.cancel()
    assert not results and done == [True] and queue.tasks == []
    queue.start()
    assert done == [True]
    queue.deleteLater()
