"""Editor history and portable frame clipboard. Images are immutable between commands."""

from __future__ import annotations
import copy
import io
import json
import zipfile
from PIL import Image
from .codec import Frame, validate


def snapshot(project):
    p = copy.copy(project)
    p.settings = copy.deepcopy(project.settings)
    for group in ("slots", "events"):
        setattr(
            p,
            group,
            {
                k: [Frame(f.image, f.x, f.y, f.duration) for f in frames]
                for k, frames in getattr(project, group).items()
            },
        )
    return p


class History:
    def __init__(self, limit=30):
        self.limit = limit
        self.past = []
        self.future = []
        self.last_token = None
        self.last_time = 0

    def push(self, project, selection, token=None, now=0):
        if (
            token is not None
            and token == self.last_token
            and now - self.last_time < 0.45
        ):
            self.last_time = now
            self.future.clear()
            return
        self.past.append((snapshot(project), selection))
        self.past = self.past[-self.limit :]
        self.future.clear()
        self.last_token = token
        self.last_time = now

    def break_group(self):
        self.last_token = None

    def undo(self, project, selection):
        if not self.past:
            return None
        self.future.append((snapshot(project), selection))
        self.break_group()
        return self.past.pop()

    def redo(self, project, selection):
        if not self.future:
            return None
        self.past.append((snapshot(project), selection))
        self.break_group()
        return self.future.pop()

    def clear(self):
        self.past.clear()
        self.future.clear()
        self.break_group()


def encode_clipboard(frames):
    out = io.BytesIO()
    records = []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for i, f in enumerate(frames):
            validate(f)
            b = io.BytesIO()
            f.image.save(b, format="PNG")
            z.writestr(f"{i}.png", b.getvalue())
            records.append([f.x, f.y, f.duration])
        z.writestr("frames.json", json.dumps(records))
    return out.getvalue()


def decode_clipboard(data):
    if len(data) > 128 * 1024 * 1024:
        raise ValueError("剪贴板动画过大")
    frames = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if sum(i.file_size for i in z.infolist()) > 256 * 1024 * 1024:
            raise ValueError("剪贴板图片过大")
        records = json.loads(z.read("frames.json"))
        if not 1 <= len(records) <= 512:
            raise ValueError("剪贴板帧数无效")
        for i, (x, y, duration) in enumerate(records):
            with Image.open(io.BytesIO(z.read(f"{i}.png"))) as image:
                if image.size != (256, 256):
                    raise ValueError("剪贴板画布无效")
                f = Frame(image.convert("RGBA"), x, y, duration)
                validate(f)
                frames.append(f)
    return frames
