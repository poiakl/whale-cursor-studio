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
from .codec import (
    Frame,
    MAX_FRAMES,
    SIZES,
    encode_ani,
    encode_cur,
    validate,
    decode_ani,
    decode_cur,
)

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
    "left_click": "左键按下",
    "right_click": "右键按下",
    "middle_click": "中键按下",
    "double_click": "左键双击",
    "drag": "按住拖动",
    "release": "松开按键",
    "hold": "长按",
    "move_start": "开始移动",
    "move_stop": "停止移动",
    "role_change": "指针状态改变",
}


EFFECTS = {"click": "点击特效", "trail": "拖尾", "companion": "跟随角色"}


def event_key(role, trigger):
    if role not in SLOTS or trigger not in EVENTS:
        raise ValueError("未知的指针用途或触发动作")
    return role + "." + trigger


def event_parts(key):
    if key in EVENTS:
        return "Arrow", key
    role, sep, trigger = key.partition(".")
    if sep and role in SLOTS and trigger in EVENTS:
        return role, trigger
    raise ValueError("未知的互动素材：" + key)


def event_name(key):
    role, trigger = event_parts(key)
    return SLOTS[role] + " / " + EVENTS[trigger]


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
    effects: dict[str, list[Frame]] = field(default_factory=dict)
    animations: dict[str, list[Frame]] = field(default_factory=dict)
    settings: dict = field(
        default_factory=lambda: {
            "click_effect": "ripple",
            "effect_motion": "fade",
            "trail": False,
            "companion": False,
            "direction": False,
            "speed": False,
            "idle_seconds": 5,
            "sound": "",
            "volume": 0.5,
            "app_rules": [],
            "interaction_scope": "common",
            "sounds": {},
            "sound_modes": {},
            "animation_rules": [],
            "history_limit": 30,
            "effect_size": 64,
            "click_size": 64,
            "trail_size": 24,
            "companion_size": 64,
            "effect_opacity": 1.0,
            "click_opacity": 1.0,
            "trail_opacity": 1.0,
            "companion_opacity": 1.0,
            "trail_lifetime": 850,
            "effect_lifetime": 850,
            "trail_spacing": 12,
            "companion_x": 20,
            "companion_y": 20,
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
            "version": 5,
            "name": self.name,
            "size": self.size,
            "pixel": self.pixel,
            "multi_size": self.multi_size,
            "settings": self.settings,
            "slots": {},
            "events": {},
            "effects": {},
            "animations": {},
        }
        temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            with zipfile.ZipFile(temp, "w", zipfile.ZIP_STORED) as z:
                for group in ("slots", "events", "effects", "animations"):
                    for key, frames in getattr(self, group).items():
                        records = []
                        if not 0 <= len(frames) <= MAX_FRAMES:
                            raise ValueError("每组需要 1～512 帧")
                        for i, f in enumerate(frames):
                            validate(f)
                            filename = f"{group}/{key}/{i:04}.png"
                            b = io.BytesIO()
                            f.image.save(b, format="PNG", compress_level=1)
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
                settings = copy.deepcopy(self.settings)
                sounds = dict(settings.get("sounds", {}))
                if settings.get("sound") and not sounds.get("click"):
                    sounds["click"] = [settings["sound"]]
                embedded = {}
                for trigger, files in sounds.items():
                    if trigger not in EVENTS:
                        continue
                    embedded[trigger] = []
                    for i, filename in enumerate(files):
                        source = Path(filename)
                        if not source.is_file():
                            raise ValueError("找不到音频：" + str(source))
                        if source.stat().st_size > 16 * 1024 * 1024:
                            raise ValueError("音频文件不能超过 16 MB")
                        target = f"audio/{trigger}/{i}.wav"
                        z.write(source, target)
                        embedded[trigger].append("@" + target)
                settings["sounds"] = embedded
                settings["sound"] = ""
                manifest["settings"] = settings
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
            if m.get("version") not in (2, 3, 4, 5) or m.get("size") not in SIZES:
                raise ValueError("不支持的方案版本 / 尺寸")
            p = cls.defaults()
            p.name = str(m.get("name", "未命名"))[:120]
            p.size = m["size"]
            p.pixel = bool(m.get("pixel", True))
            p.multi_size = bool(m.get("multi_size", False))
            p.settings.update(m.get("settings", {}))
            if m.get("version", 2) < 5:
                previous = m.get("settings", {})
                for key, shared in (
                    ("click_opacity", "effect_opacity"),
                    ("trail_opacity", "effect_opacity"),
                    ("companion_opacity", "effect_opacity"),
                    ("trail_lifetime", "effect_lifetime"),
                ):
                    if key not in previous:
                        p.settings[key] = p.settings[shared]
            from .fx import parse_keyframes

            if "effect_keyframes" in p.settings:
                parse_keyframes(p.settings["effect_keyframes"])
            from .conditions import validate_rule

            rules = p.settings.get("animation_rules", [])
            if not isinstance(rules, list) or len(rules) > 1024:
                raise ValueError("动画规则数量无效")
            ids = set()
            for rule in rules:
                validate_rule(rule)
                if rule["id"] in ids:
                    raise ValueError("动画规则标识重复")
                ids.add(rule["id"])
            if m.get("version", 2) < 4:
                p.settings["interaction_scope"] = "role"
            for group, allowed in (
                ("slots", SLOTS),
                ("events", EVENTS),
                ("effects", EFFECTS),
                ("animations", set(m.get("animations", {}))),
            ):
                for key, records in m.get(group, {}).items():
                    if group == "events":
                        try:
                            role, trigger = event_parts(key)
                            target = event_key(role, trigger)
                        except ValueError:
                            continue
                    else:
                        if key not in allowed:
                            continue
                        target = key
                        if group == "animations" and (
                            len(key) > 100
                            or not key
                            or not all(c.isalnum() or c in "_-" for c in key)
                        ):
                            raise ValueError("动画素材标识无效")
                    if not 0 <= len(records) <= MAX_FRAMES:
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
                    getattr(p, group)[target] = frames
            if p.settings.get("sound") == "@embedded":
                audio = z.read("audio/click.wav")
                dest = user_dir() / "audio"
                dest.mkdir(exist_ok=True)
                import hashlib

                sound = dest / (hashlib.sha256(audio).hexdigest() + ".wav")
                sound.write_bytes(audio)
                p.settings["sound"] = str(sound)
            import hashlib

            sounds = p.settings.get("sounds", {})
            for trigger, files in sounds.items():
                if trigger not in EVENTS or not isinstance(files, list):
                    raise ValueError("音频触发配置无效")
                for i, name in enumerate(files):
                    if name.startswith("@audio/"):
                        audio = z.read(name[1:])
                        if len(audio) > 16 * 1024 * 1024:
                            raise ValueError("音频文件过大")
                        dest = user_dir() / "audio"
                        dest.mkdir(exist_ok=True)
                        target = dest / (hashlib.sha256(audio).hexdigest() + ".wav")
                        target.write_bytes(audio)
                        files[i] = str(target)
            return p

    def export(self, folder, only=None):
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        paths = {}
        for key, frames in self.slots.items():
            if (only and key != only) or not frames:
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
        if not paths:
            raise ValueError("没有可生成的指针素材，请先导入图片或动画")
        return paths

    def event_frames(self, role, trigger):
        return self.events.get(
            event_key(role, trigger),
            self.events.get(trigger, []) if role == "Arrow" else [],
        )

    def migrate_events(self):
        for key in list(self.events):
            role, trigger = event_parts(key)
            target = event_key(role, trigger)
            if target != key:
                self.events.setdefault(target, self.events[key])
                del self.events[key]

    def inspect(self):
        rows = []
        for group in ("slots", "events", "effects", "animations"):
            for key, frames in getattr(self, group).items():
                name = (
                    ("系统 · " + SLOTS[key])
                    if group == "slots"
                    else (
                        ("效果 · " + EFFECTS[key])
                        if group == "effects"
                        else (
                            ("条件 · " + self.animation_name(key))
                            if group == "animations"
                            else ("互动 · " + event_name(key))
                        )
                    )
                )
                issues = []
                if not frames:
                    issues.append("未配置素材（安装时保留原系统用途）")
                for i, f in enumerate(frames):
                    try:
                        validate(f)
                    except ValueError as e:
                        issues.append(f"第 {i + 1} 帧：{e}")
                    if f.image.getbbox() is None:
                        issues.append(f"第 {i + 1} 帧完全透明")
                if len({(f.x, f.y) for f in frames}) > 1:
                    issues.append("各帧热点不同，请确认指向点稳定（提示）")
                if frames and not any(
                    "第 " in issue and "完全透明" not in issue for issue in issues
                ):
                    try:
                        if len(frames) > 1:
                            decoded = decode_ani(
                                encode_ani(frames, (self.size,), self.pixel)
                            )
                            if len(decoded) != len(frames):
                                issues.append("ANI 导出后帧数不一致")
                            if any(
                                abs(a.duration - b.duration) > 9
                                for a, b in zip(frames, decoded)
                            ):
                                issues.append("ANI 时长换算异常")
                        else:
                            decode_cur(encode_cur(frames[0], (self.size,), self.pixel))
                    except Exception as e:
                        issues.append("格式 / 动画时序检查失败：" + str(e))
                rows.append(
                    {
                        "group": group,
                        "key": key,
                        "name": name,
                        "frames": frames,
                        "count": len(frames),
                        "duration": sum(f.duration for f in frames),
                        "issues": issues,
                    }
                )
        return rows

    def problems(self):
        return [
            row["name"] + "：" + issue
            for row in self.inspect()
            for issue in row["issues"]
        ]

    def animation_name(self, key):
        from .conditions import describe

        rule = next(
            (r for r in self.settings.get("animation_rules", []) if r["id"] == key),
            None,
        )
        return describe(rule) if rule else "未关联动画"
