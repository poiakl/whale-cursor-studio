from __future__ import annotations
import copy
import io
import json
import os
import sys
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from PIL import Image
from .codec import Frame, MAX_FRAMES, SIZES, encode_ani, encode_cur, validate

SLOTS = dict(
    zip(
        (
            "Arrow",
            "Help",
            "AppStarting",
            "Wait",
            "Crosshair",
            "IBeam",
            "NWPen",
            "No",
            "SizeNS",
            "SizeWE",
            "SizeNWSE",
            "SizeNESW",
            "SizeAll",
            "UpArrow",
            "Hand",
            "Pin",
            "Person",
        ),
        (
            "正常选择",
            "帮助选择",
            "后台运行",
            "忙",
            "精确选择",
            "文本选择",
            "手写",
            "不可用",
            "垂直调整",
            "水平调整",
            "对角线调整 1",
            "对角线调整 2",
            "移动",
            "候选选择",
            "链接选择",
            "位置选择",
            "人员选择",
        ),
    )
)
EVENTS = {
    "click": "点击动作",
    "idle": "闲置动作",
    "move": "移动动作",
    "left": "向左移动",
    "right": "向右移动",
    "up": "向上移动",
    "down": "向下移动",
}


def assets_dir():
    return (
        Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
        / "assets"
    )


def user_dir():
    path = (
        Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share"))
        / "WhaleCursorStudio2"
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Project:
    name: str = "鲸鱼娘"
    size: int = 64
    pixel: bool = True
    multi_size: bool = False
    slots: dict[str, list[Frame]] = field(default_factory=dict)
    events: dict[str, list[Frame]] = field(default_factory=dict)
    settings: dict = field(
        default_factory=lambda: {
            "click_effect": "ripple",
            "trail": False,
            "companion": False,
            "direction": False,
            "speed": False,
            "idle_seconds": 5,
            "sound": "",
            "volume": 0.5,
            "app_rules": [],
        }
    )

    @classmethod
    def defaults(cls):
        root = assets_dir()
        hot = json.loads((root / "hotspots.json").read_text("utf-8-sig"))
        p = cls()
        for key in SLOTS:
            x, y = hot.get(key) or (128, 128)
            with Image.open(root / (key + ".png")) as image:
                p.slots[key] = [Frame(image.convert("RGBA"), x, y)]
        return p

    def clone(self):
        return copy.deepcopy(self)

    def save(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        manifest = {
            "version": 2,
            "name": self.name,
            "size": self.size,
            "pixel": self.pixel,
            "multi_size": self.multi_size,
            "settings": self.settings,
            "slots": {},
            "events": {},
        }
        temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as z:
                for group in ("slots", "events"):
                    for key, frames in getattr(self, group).items():
                        records = []
                        if not 1 <= len(frames) <= MAX_FRAMES:
                            raise ValueError("每组需要 1～512 帧")
                        for i, f in enumerate(frames):
                            validate(f)
                            filename = f"{group}/{key}/{i:04}.png"
                            b = io.BytesIO()
                            f.image.save(b, format="PNG")
                            z.writestr(filename, b.getvalue())
                            records.append(
                                {
                                    "file": filename,
                                    "x": f.x,
                                    "y": f.y,
                                    "duration": f.duration,
                                }
                            )
                        manifest[group][key] = records
                sound = self.settings.get("sound", "")
                if sound and Path(sound).is_file():
                    if Path(sound).stat().st_size > 16 * 1024 * 1024:
                        raise ValueError("音频文件不能超过 16 MB")
                    z.write(sound, "audio/click.wav")
                    manifest["settings"] = dict(self.settings, sound="@embedded")
                z.writestr("project.json", json.dumps(manifest, ensure_ascii=False))
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)

    @classmethod
    def load(cls, path):
        with zipfile.ZipFile(path) as z:
            if sum(i.file_size for i in z.infolist()) > 256 * 1024 * 1024:
                raise ValueError("方案解压后超过 256 MB")
            if z.getinfo("project.json").file_size > 2 * 1024 * 1024:
                raise ValueError("方案索引过大")
            m = json.loads(z.read("project.json"))
            if m.get("version") != 2 or m.get("size") not in SIZES:
                raise ValueError("不支持的方案版本 / 尺寸")
            p = cls.defaults()
            p.name = str(m.get("name", "未命名"))[:120]
            p.size = m["size"]
            p.pixel = bool(m.get("pixel", True))
            p.multi_size = bool(m.get("multi_size", False))
            p.settings.update(m.get("settings", {}))
            for group, allowed in (("slots", SLOTS), ("events", EVENTS)):
                for key, records in m.get(group, {}).items():
                    if key not in allowed:
                        continue
                    if not 1 <= len(records) <= MAX_FRAMES:
                        raise ValueError("方案帧数无效")
                    frames = []
                    for r in records:
                        with Image.open(io.BytesIO(z.read(r["file"]))) as im:
                            if im.size != (256, 256):
                                raise ValueError("方案图片画布无效")
                            f = Frame(
                                im.convert("RGBA"),
                                int(r["x"]),
                                int(r["y"]),
                                int(r["duration"]),
                            )
                            validate(f)
                            frames.append(f)
                    getattr(p, group)[key] = frames
            if p.settings.get("sound") == "@embedded":
                audio = z.read("audio/click.wav")
                dest = user_dir() / "audio"
                dest.mkdir(exist_ok=True)
                import hashlib

                sound = dest / (hashlib.sha256(audio).hexdigest() + ".wav")
                sound.write_bytes(audio)
                p.settings["sound"] = str(sound)
            return p

    def export(self, folder, only=None):
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        paths = {}
        for key, frames in self.slots.items():
            if only and key != only:
                continue
            # The chosen size is authoritative unless the user explicitly requests multi-resolution.
            sizes = SIZES if self.multi_size else (self.size,)
            is_animation = len(frames) > 1
            path = folder / (key + (".ani" if is_animation else ".cur"))
            path.write_bytes(
                encode_ani(frames, sizes, self.pixel)
                if is_animation
                else encode_cur(frames[0], sizes, self.pixel)
            )
            paths[key] = str(path.resolve())
        return paths

    def problems(self):
        issues = []
        for key, frames in self.slots.items():
            for i, f in enumerate(frames):
                try:
                    validate(f)
                except ValueError as e:
                    issues.append(f"{SLOTS[key]} 第 {i + 1} 帧：{e}")
                if f.image.getbbox() is None:
                    issues.append(f"{SLOTS[key]} 第 {i + 1} 帧完全透明")
            if len({(f.x, f.y) for f in frames}) > 1:
                issues.append(f"{SLOTS[key]} 各帧热点不同，请确认指向点稳定（提示）")
        return issues
