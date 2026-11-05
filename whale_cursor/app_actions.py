"""Whitelisted app-native actions executed on the Qt UI thread."""

import base64
import copy
import json
from pathlib import Path
import uuid
import weakref
from PySide6.QtCore import Qt
from .audio import pcm_files, playback_options, validate_playback
from .fx import MOTIONS, EXAMPLE, parse_keyframes, pose
from .project import EVENTS


class AppActions:
    def __init__(self, window):
        self.window = window
        self.requests = {}

    def dispatch(self, operation, params):
        actions = {
            "get_generation_request": self.get_generation_request,
            "apply_generated_image": self.apply_generated_image,
            "get_interaction_settings": self.get_interaction_settings,
            "set_click_effect": self.set_click_effect,
            "preview_click_effect": self.preview_click_effect,
            "configure_interaction_audio": self.configure_interaction_audio,
        }
        if operation not in actions:
            raise ValueError("未知的工坊操作")
        if not isinstance(params, dict):
            raise ValueError("操作参数必须为对象")
        return actions[operation](**params)

    def fingerprint(self, target):
        group, key = target.split(".", 1)
        return tuple(
            (id(f.image), f.x, f.y, f.duration)
            for f in getattr(self.window.project, group).get(key, [])
        )

    def get_generation_request(self, targets=None, include_references=True):
        page = self.window.prompt_page
        tasks = page.generation_tasks()
        if targets is not None:
            if not isinstance(targets, list) or set(targets) - {
                task["spec"]["id"] for task in tasks
            }:
                raise ValueError("只能读取 AI 生图页面当前已勾选的条目")
            tasks = [task for task in tasks if task["spec"]["id"] in targets]
        if not tasks:
            raise ValueError("请先在 AI 生图页面勾选需要生成的动画")
        request_id = uuid.uuid4().hex
        self.requests[request_id] = dict(
            project=weakref.ref(self.window.project),
            tasks=copy.deepcopy(tasks),
            signatures={
                task["spec"]["id"]: self.fingerprint(task["spec"]["id"])
                for task in tasks
            },
        )
        while len(self.requests) > 8:
            del self.requests[next(iter(self.requests))]
        references = list(page.ai.references) if page.ai else []
        return dict(
            request_id=request_id,
            tasks=tasks,
            reference_count=len(references),
            references=[base64.b64encode(raw).decode("ascii") for raw in references]
            if include_references
            else [],
            workflow="使用你自己的生图能力和这里的参考图生成各任务的透明序列图，然后调用 apply_generated_image 放回对应动画。无需填写工坊 API 密钥。",
        )

    def apply_generated_image(
        self, request_id, target, image_path=None, image_base64=None, hotspots=None
    ):
        request = self.requests.get(request_id)
        if not request or request["project"]() is not self.window.project:
            raise ValueError("生成请求已失效或当前方案已切换，请重新读取提示词")
        task = next(
            (task for task in request["tasks"] if task["spec"]["id"] == target), None
        )
        if task is None:
            raise ValueError("此目标不属于该生成请求")
        if self.fingerprint(target) != request["signatures"][target]:
            raise ValueError("该动画已被用户修改，请重新读取提示词，避免覆盖新编辑")
        if bool(image_path) == bool(image_base64):
            raise ValueError("请提供本地图片路径或图片 base64，两者选一")
        if image_path:
            path = Path(image_path).expanduser().resolve()
            if not path.is_file() or path.stat().st_size > 24 * 1024 * 1024:
                raise ValueError("生成图片不存在或超过 24 MB")
            raw = path.read_bytes()
        else:
            if len(image_base64) > 32 * 1024 * 1024:
                raise ValueError("图片 base64 过长")
            raw = base64.b64decode(image_base64, validate=True)
        from .ai_api import decode_image, result_frames
        from .hotspots import parse_hotspots

        image = decode_image(raw)
        frames = result_frames(image, task["spec"], self.window.project.pixel)
        if hotspots is not None:
            from .project import Project

            group, key = target.split(".", 1)
            validation = Project()
            getattr(validation, group)[key] = frames
            changes = parse_hotspots(hotspots, validation, (group, key))
            for change in changes:
                frame = getattr(validation, change.group)[change.key][change.frame]
                frame.x, frame.y = change.x, change.y
                if change.duration is not None:
                    frame.duration = change.duration
        self.window.apply_ai_frames(self.window.project, target, frames)
        request["signatures"][target] = self.fingerprint(target)
        return dict(
            target=target, frame_count=len(frames), applied=True, undo_available=True
        )

    def get_interaction_settings(self):
        project = self.window.project
        return dict(
            project=project.name,
            click_effect={
                key: copy.deepcopy(value)
                for key, value in project.settings.items()
                if key.startswith(("click_", "effect_"))
            },
            click_material_frames=len(project.effects.get("click", [])),
            triggers=EVENTS,
            audio={
                trigger: dict(
                    files=self.window.audio_files(trigger),
                    mode=project.settings.get("sound_modes", {}).get(trigger, "random"),
                    **playback_options(project.settings, trigger),
                )
                for trigger in EVENTS
            },
            volume=project.settings.get("volume", 0.5),
            keyframe_help=dict(
                fields="t:0～1; x/y:点击位置偏移像素，右/下为正; scale:0～8; rotation:角度; opacity:0～1。2～64帧，首尾t=0/1，中间自动插值。",
                example=json.loads(EXAMPLE),
                motions=MOTIONS,
            ),
        )

    def set_click_effect(
        self,
        keyframes=None,
        effect=None,
        motion=None,
        duration_ms=None,
        size=None,
        opacity=None,
    ):
        values = {}
        if keyframes is not None:
            code = json.dumps(keyframes, ensure_ascii=False)
            parse_keyframes(code)
            values.update(effect_keyframes=code, effect_motion="keyframes")
        if effect is not None:
            if effect not in ("ripple", "petals", "none", "custom"):
                raise ValueError("点击特效可选 ripple / petals / none / custom")
            values["click_effect"] = effect
        if motion is not None:
            if motion not in MOTIONS:
                raise ValueError("未知运动方式")
            values["effect_motion"] = motion
        for value, key, low, high in (
            (duration_ms, "effect_lifetime", 100, 10000),
            (size, "click_size", 8, 256),
            (opacity, "click_opacity", 0, 1),
        ):
            if value is not None:
                if type(value) not in (int, float) or not low <= value <= high:
                    raise ValueError("特效参数范围无效：" + key)
                if key != "click_opacity" and type(value) is not int:
                    raise ValueError("尺寸和时长必须为整数")
                values[key] = value
        if not values:
            raise ValueError("请提供至少一项特效参数")
        window = self.window
        window.remember("AI 配置点击特效")
        window.project.settings.update(values)
        for field, value in (
            (
                window.click_effect,
                {"ripple": 0, "petals": 1, "none": 2, "custom": 3}.get(
                    window.project.settings.get("click_effect"), 0
                ),
            ),
            (
                window.effect_motion,
                window.effect_motion.findData(
                    window.project.settings.get("effect_motion", "fade")
                ),
            ),
        ):
            field.blockSignals(True)
            field.setCurrentIndex(value)
            field.blockSignals(False)
        for key in ("click_size", "effect_lifetime", "click_opacity"):
            field = window.effect_fields[key]
            field.blockSignals(True)
            value = window.project.settings.get(key, window.effect_defaults[key])
            field.setValue(round(value * 100) if key.endswith("opacity") else value)
            field.blockSignals(False)
        window.touch()
        return self.get_interaction_settings()["click_effect"]

    def preview_click_effect(self, progress=None):
        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QImage
        from .runtime import Overlay

        times = [0, 0.2, 0.4, 0.6, 0.8, 0.99] if progress is None else progress
        if (
            not isinstance(times, list)
            or not 1 <= len(times) <= 12
            or any(type(t) not in (int, float) or not 0 <= t <= 1 for t in times)
        ):
            raise ValueError("预览进度应为 0～1，最多12张")
        scene = Overlay()
        scene.setGeometry(0, 0, 640, 480)
        scene.settings = dict(
            self.window.project.settings, pixel=self.window.project.pixel
        )
        scene.effects = {
            key: [(f.image, f.duration) for f in frames]
            for key, frames in self.window.project.effects.items()
        }
        scene.keyframes = parse_keyframes(
            scene.settings.get("effect_keyframes", EXAMPLE)
        )
        output = []
        try:
            import time

            scene.emit_at(
                QPoint(210, 180), scene.settings.get("click_effect", "ripple")
            )
            birth = scene.particles[0][1] if scene.particles else time.monotonic()
            particles = list(scene.particles)
            for t in times:
                scene.particles = list(particles)
                scene.render_time = (
                    birth + t * scene.settings.get("effect_lifetime", 850) / 1000
                )
                canvas = QImage(640, 480, QImage.Format.Format_ARGB32_Premultiplied)
                canvas.fill(Qt.GlobalColor.transparent)
                scene.render(canvas)
                from PySide6.QtCore import QBuffer, QIODevice

                buffer = QBuffer()
                buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                canvas.save(buffer, "PNG")
                output.append(
                    dict(
                        progress=t,
                        pose=pose(
                            scene.settings.get("effect_motion", "fade"),
                            t,
                            scene.keyframes,
                        ),
                        image=base64.b64encode(bytes(buffer.data())).decode("ascii"),
                    )
                )
        finally:
            scene.deleteLater()
        return output

    def configure_interaction_audio(
        self,
        trigger,
        files=None,
        mode=None,
        wait_finish=None,
        interval_ms=None,
        volume=None,
    ):
        if trigger not in EVENTS:
            raise ValueError("未知音效触发项")
        if mode is not None and mode not in ("random", "sequence"):
            raise ValueError("选曲方式应为 random 或 sequence")
        options = playback_options(self.window.project.settings, trigger)
        if wait_finish is not None:
            options["wait_finish"] = wait_finish
        if interval_ms is not None:
            options["interval_ms"] = interval_ms
        validate_playback(options)
        paths = pcm_files(files) if files is not None else None
        if volume is not None and (
            type(volume) not in (int, float) or not 0 <= volume <= 1
        ):
            raise ValueError("音量应为 0～1")
        window = self.window
        window.remember("AI 配置互动音效")
        settings = window.project.settings
        settings.setdefault("sound_playback", {})[trigger] = options
        if paths is not None:
            settings.setdefault("sounds", {})[trigger] = paths
            if trigger == "click":
                settings["sound"] = ""
        if mode is not None:
            settings.setdefault("sound_modes", {})[trigger] = mode
        if volume is not None:
            settings["volume"] = volume
            window.volume.blockSignals(True)
            window.volume.setValue(volume)
            window.volume.blockSignals(False)
        window.sound_trigger.setCurrentIndex(list(EVENTS).index(trigger))
        window.refresh_sound_list()
        window.touch()
        return self.get_interaction_settings()["audio"][trigger]
