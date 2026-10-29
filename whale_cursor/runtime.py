"""Opt-in desktop interactions. Poll mouse state only; no keyboard hooks or input logging."""

from __future__ import annotations
import math
import copy
import os
import random
import uuid
import shutil
import json
import time
from collections import OrderedDict
from PySide6.QtCore import Qt, QTimer, QPoint, QRectF
from PySide6.QtGui import QCursor, QPainter, QColor, QPen
from PySide6.QtWidgets import QWidget, QApplication
from PySide6.QtMultimedia import QSoundEffect
from PySide6.QtCore import QUrl
from .codec import encode_cur
from .project import Project, user_dir
from . import native
from .conditions import Inputs, Resolver, ORDER, owners


_pixmap_cache = OrderedDict()


def pixmap(image):
    key = id(image)
    cached = _pixmap_cache.get(key)
    if cached is not None and cached[0] is image:
        _pixmap_cache.move_to_end(key)
        return cached[1]
    from PySide6.QtGui import QImage, QPixmap

    raw = image.convert("RGBA").tobytes()
    result = QPixmap.fromImage(
        QImage(
            raw,
            image.width,
            image.height,
            image.width * 4,
            QImage.Format.Format_RGBA8888,
        ).copy()
    )
    _pixmap_cache[key] = (image, result)
    while len(_pixmap_cache) > 256:
        _pixmap_cache.popitem(last=False)
    return result


class Overlay(QWidget):
    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.particles = []
        self.sprite = None
        self.sprite_pos = QPoint()
        self.sprite_size = 64
        self.settings = {}
        self.effects = {}

    def fit_screens(self):
        rect = QApplication.screens()[0].geometry()
        for screen in QApplication.screens()[1:]:
            rect = rect.united(screen.geometry())
        self.setGeometry(rect)

    def emit_at(self, pos, kind):
        now = time.monotonic()
        if kind == "petals":
            for _ in range(9):
                self.particles.append(
                    (
                        QPoint(pos),
                        now,
                        kind,
                        random.uniform(-70, 70),
                        random.uniform(-95, -25),
                    )
                )
        elif kind != "none":
            self.particles.append((QPoint(pos), now, kind, 0, 0))
        self.particles = self.particles[-200:]

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(
            QPainter.RenderHint.SmoothPixmapTransform,
            not self.settings.get("pixel", True),
        )
        now = time.monotonic()
        origin = self.geometry().topLeft()
        lifetime = max(0.1, self.settings.get("effect_lifetime", 850) / 1000)
        self.particles = [
            v
            for v in self.particles
            if now - v[1]
            < (
                self.settings.get(
                    "trail_lifetime", self.settings.get("effect_lifetime", 850)
                )
                / 1000
                if v[2] == "trail"
                else lifetime
            )
        ]
        for pos, born, kind, vx, vy in self.particles:
            age = now - born
            lifetime = (
                max(
                    0.1,
                    self.settings.get(
                        "trail_lifetime", self.settings.get("effect_lifetime", 850)
                    )
                    / 1000,
                )
                if kind == "trail"
                else max(0.1, self.settings.get("effect_lifetime", 850) / 1000)
            )
            opacity = self.settings.get(
                "trail_opacity" if kind == "trail" else "click_opacity",
                self.settings.get("effect_opacity", 1.0),
            )
            scale = self.settings.get(
                "trail_size" if kind == "trail" else "click_size",
                24 if kind == "trail" else 64,
            ) / (24 if kind == "trail" else 64)
            local = pos - origin
            p.setOpacity(max(0, 1 - age / lifetime) * opacity)
            custom = self.effects.get("trail" if kind == "trail" else "click", [])
            if custom and kind in ("custom", "trail"):
                total = sum(duration for _, duration in custom)
                elapsed = (age * 1000) % total
                index = 0
                while index < len(custom) - 1 and elapsed >= custom[index][1]:
                    elapsed -= custom[index][1]
                    index += 1
                size = self.settings.get(
                    "trail_size" if kind == "trail" else "click_size",
                    self.settings.get("effect_size", 64),
                )
                p.drawPixmap(
                    local.x() - size // 2,
                    local.y() - size // 2,
                    size,
                    size,
                    custom[index][0],
                )
                continue
            if kind == "petals":
                x = local.x() + vx * age
                y = local.y() + vy * age + 100 * age * age
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor("#F3A8C0"))
                p.drawEllipse(QRectF(x, y, 7 * scale, 12 * scale))
            elif kind == "trail":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor("#6BBEAA"))
                p.drawEllipse(local, 3 * scale, 3 * scale)
            else:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor("#4FAF9C"), 2))
                p.drawEllipse(local, (8 + age * 48) * scale, (8 + age * 48) * scale)
        p.setOpacity(
            self.settings.get(
                "companion_opacity", self.settings.get("effect_opacity", 1.0)
            )
        )
        if self.sprite is not None:
            point = self.sprite_pos - origin
            p.drawPixmap(
                point.x(), point.y(), self.sprite_size, self.sprite_size, self.sprite
            )
        p.end()


class Engine:
    def __init__(self, on_error=lambda message: None):
        self.on_error = on_error
        self.timer = QTimer()
        self.timer.timeout.connect(self.tick)
        self.active = False
        self.overlay = Overlay()
        self.sound = QSoundEffect()
        self.sounds = {}
        self.sound_indices = {}
        self.temp = None
        self.initial = None
        self.frame_cache = {}
        self.rule_cache = {}

    def start(self, project):
        native.require()
        if self.active:
            self.stop()
        native.recover_runtime()
        self.project = project.clone()
        self.project.migrate_events()
        self.base_project = self.project
        self.initial = native.snapshot()
        self.active = True
        self.temp = user_dir() / "runtime" / uuid.uuid4().hex
        self.temp.mkdir(parents=True)
        self.frame_cache = {}
        self.rule_cache = {}
        recovery = user_dir() / "runtime-recovery.json"
        tmp = recovery.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"snapshot": self.initial, "folder": str(self.temp)}),
            encoding="utf-8",
        )
        os.replace(tmp, recovery)
        try:
            self.prepare_frames(self.project)
            export_project = copy.copy(self.project)
            export_project.multi_size = True
            paths = (
                export_project.export(self.temp / "base")
                if any(self.project.slots.values())
                else {}
            )
            for path in paths.values():
                native.validate_file(path, self.project.size)
            self.base_snapshot = self.project_snapshot(self.project, paths)
            native.write_snapshot(self.base_snapshot)
        except Exception:
            self.stop()
            raise
        self.last_position = QCursor.pos()
        self.inputs = Inputs(
            time.monotonic(),
            (self.last_position.x(), self.last_position.y()),
            self.read_buttons(),
        )
        self.resolver = Resolver()
        self.active_audio = set()
        self.last_move = time.monotonic()
        self.last_tick = self.last_move
        self.last_activity = self.last_move
        self.click_until = 0
        self.prev_left = native.button_down(1)
        self.prev_right = native.button_down(2)
        self.prev_middle = native.button_down(4)
        self.press_time = self.last_move
        self.last_left_click = -100
        self.was_moving = False
        self.held_fired = False
        self.last_sound_trigger = ""
        self.last_trail_pos = self.last_position
        self.shot_state = ""
        self.shot_until = 0
        self.state = ""
        self.index = 0
        self.elapsed = 0
        self.last_frame = None
        self.dirty_roles = set()
        self.role = "Arrow"
        self.last_rule = ""
        self.last_app_check = 0
        self.prepare_sounds(project)
        self.overlay.settings = dict(self.project.settings, pixel=self.project.pixel)
        self.overlay.effects = {
            key: [(pixmap(frame.image), frame.duration) for frame in frames]
            for key, frames in self.project.effects.items()
        }
        self.overlay.fit_screens()
        self.overlay.show()
        self.timer.start(16)

    def prepare_frames(self, project):
        project.migrate_events()
        key = id(project)
        groups = {
            **{"slots." + role: frames for role, frames in project.slots.items()},
            **project.events,
            **{
                "animations." + key: frames
                for key, frames in project.animations.items()
            },
        }
        for name, frames in groups.items():
            for index, frame in enumerate(frames):
                for size in self.render_sizes(project):
                    path = self.temp / f"{key}-{name}-{index}-{size}.cur"
                    path.write_bytes(encode_cur(frame, (size,), project.pixel))
                    self.frame_cache[(key, name, index, size)] = path
        project._runtime_pixmaps = {
            name: [pixmap(f.image) for f in frames] for name, frames in groups.items()
        }

    def stop(self):
        if not self.active:
            return
        self.timer.stop()
        self.overlay.hide()
        self.overlay.particles = []
        self.sound.stop()
        for pool in self.sounds.values():
            for sound in pool:
                sound.stop()
                sound.deleteLater()
        self.sounds = {}
        self.active = False
        # Keep recovery information and files if Windows refuses restoration.
        if self.initial:
            native.write_snapshot(self.initial)
        (user_dir() / "runtime-recovery.json").unlink(missing_ok=True)
        self.initial = None
        if self.temp:
            shutil.rmtree(self.temp, ignore_errors=True)
            self.temp = None

    def project_snapshot(self, project, paths):
        snap = {
            "values": dict(self.initial["values"]),
            "scheme": self.initial["scheme"],
        }
        for key, path in paths.items():
            snap["values"][key] = [path, 1]
        if paths:
            snap["values"]["CursorBaseSize"] = [project.size, 4]
            snap["values"][""] = ["", 1]
        return snap

    def switch_rule(self, now):
        if now - self.last_app_check < 0.6:
            return
        self.last_app_check = now
        exe = native.foreground_exe()
        rule_path = ""
        for rule in self.base_project.settings.get("app_rules", []):
            if str(rule.get("exe", "")).lower() == exe:
                rule_path = rule.get("scheme", "")
                break
        if rule_path == self.last_rule:
            return
        # Export each application scheme once, then switch only when the matched rule changes.
        if rule_path:
            if rule_path not in self.rule_cache:
                p = Project.load(rule_path)
                folder = self.temp / ("scheme-" + str(len(self.rule_cache)))
                export_project = copy.copy(p)
                export_project.multi_size = True
                paths = export_project.export(folder) if any(p.slots.values()) else {}
                for path in paths.values():
                    native.validate_file(path, p.size)
                self.prepare_frames(p)
                self.rule_cache[rule_path] = (p, paths)
            project, paths = self.rule_cache[rule_path]
            native.write_snapshot(self.project_snapshot(project, paths))
            self.project = project
        else:
            native.write_snapshot(self.base_snapshot)
            self.project = self.base_project
        self.prepare_sounds(self.project)
        self.overlay.settings = dict(self.project.settings, pixel=self.project.pixel)
        self.overlay.effects = {
            key: [(pixmap(f.image), f.duration) for f in frames]
            for key, frames in self.project.effects.items()
        }
        self.resolver = Resolver()
        self.active_audio = set()
        self.last_sound_trigger = ""
        self.last_rule = rule_path
        self.dirty_roles.clear()
        self.last_frame = None
        self.state = ""
        self.index = 0
        self.elapsed = 0

    def tick(self):
        if not self.active:
            return
        try:
            self._tick()
        except Exception as e:
            try:
                self.stop()
            finally:
                self.on_error(str(e))

    @staticmethod
    def read_buttons():
        return {
            name: native.button_down(vk)
            for name, vk in (("left", 1), ("right", 2), ("middle", 4))
        }

    @staticmethod
    def pixel_size(project, ratio):
        return max(16, min(256, round(project.size * ratio)))

    def render_sizes(self, project):
        return {
            project.size,
            *(
                self.pixel_size(project, screen.devicePixelRatio())
                for screen in QApplication.screens()
            ),
        }

    def cursor_pixels(self, project, pos):
        screen = QApplication.screenAt(pos) or QApplication.primaryScreen()
        return self.pixel_size(project, screen.devicePixelRatio() if screen else 1)

    def rule_frames(self, rule):
        if rule.get("source"):
            return self.project.events.get(rule["source"], [])
        return self.project.animations.get(rule["id"], [])

    def animation_rules(self):
        rules = list(self.project.settings.get("animation_rules", []))
        common = self.project.settings.get("interaction_scope") == "common"
        for key, frames in self.project.events.items():
            if not frames:
                continue
            role, trigger = key.split(".")
            if common and role != "Arrow":
                continue
            if trigger in (
                "left",
                "right",
                "up",
                "down",
            ) and not self.project.settings.get("direction"):
                continue
            rules.append(
                {
                    "id": "legacy_" + key.replace(".", "_"),
                    "trigger": trigger,
                    "button": "any",
                    "motion": "any",
                    "role": "any" if common else role,
                    "source": key,
                    "lock": trigger == "hold",
                }
            )
        return rules

    def play_sound(self, trigger):
        pool = self.sounds.get(trigger, [])
        if not pool:
            return
        mode = self.project.settings.get("sound_modes", {}).get(trigger, "random")
        if mode == "sequence":
            index = self.sound_indices.get(trigger, 0) % len(pool)
            sound = pool[index]
            if sound.status() != QSoundEffect.Status.Ready:
                return
            self.sound_indices[trigger] = (index + 1) % len(pool)
        else:
            ready = [
                sound for sound in pool if sound.status() == QSoundEffect.Status.Ready
            ]
            if not ready:
                return
            sound = random.choice(ready)
        sound.play()

    def _tick(self):
        now = time.monotonic()
        dt = min(0.2, now - self.last_tick)
        self.last_tick = now
        self.switch_rule(now)
        pos = QCursor.pos()
        delta = pos - self.last_position
        distance = math.hypot(delta.x(), delta.y())
        settings = self.project.settings
        detected = native.current_role()
        context = self.inputs.update(
            now,
            (pos.x(), pos.y()),
            self.read_buttons(),
            detected,
            float(settings.get("idle_seconds", 5)),
        )
        role = detected if detected in self.project.slots else "Arrow"
        if role != self.role and self.dirty_roles:
            native.reload()
            self.dirty_roles.clear()
        self.role = role
        if "click" in context.pulses:
            self.overlay.emit_at(pos, settings.get("click_effect", "ripple"))
        if (
            distance
            and settings.get("trail")
            and math.hypot(
                pos.x() - self.last_trail_pos.x(), pos.y() - self.last_trail_pos.y()
            )
            >= settings.get("trail_spacing", 12)
        ):
            self.overlay.emit_at(pos, "trail")
            self.last_trail_pos = QPoint(pos)
        continuous_audio = {
            trigger
            for trigger in (
                "hold",
                "drag",
                "idle",
                "move",
                "left",
                "right",
                "up",
                "down",
            )
            if owners(trigger, context)
        }
        # Direction and generic movement audio share one feedback channel; specific clicks override generic clicks.
        for button in sorted(context.pulses.get("click", set())):
            if (
                button == "left"
                and "double_click" in context.pulses
                and self.sounds.get("double_click")
            ):
                self.play_sound("double_click")
            else:
                specific = button + "_click"
                self.play_sound(specific if self.sounds.get(specific) else "click")
        for trigger in context.pulses:
            if trigger not in (
                "click",
                "left_click",
                "right_click",
                "middle_click",
                "double_click",
            ):
                self.play_sound(trigger)
        for trigger in continuous_audio - self.active_audio:
            if trigger == "move" and any(
                self.sounds.get(k)
                for k in ("left", "right", "up", "down")
                if k in continuous_audio
            ):
                continue
            self.play_sound(trigger)
        self.active_audio = continuous_audio
        rule = (
            self.resolver.select(
                self.animation_rules(),
                self.rule_frames,
                context,
                settings.get("trigger_order", ORDER),
            )
            if detected is not None
            else None
        )
        state = rule["trigger"] if rule else "base"
        group = (
            (rule.get("source") or "animations." + rule["id"])
            if rule
            else "slots." + role
        )
        frames = self.rule_frames(rule) if rule else self.project.slots.get(role, [])
        identity = (id(self.project), group)
        if getattr(self, "animation_identity", None) != identity or (
            rule
            and state in context.pulses
            and state not in ("hold", "drag", "pressed")
        ):
            self.animation_identity = identity
            self.index = 0
            self.elapsed = 0
            self.last_frame = None
        self.state = state
        if not frames:
            if self.dirty_roles:
                native.reload()
                self.dirty_roles.clear()
            self.update_companion(pos, now)
            self.overlay.update()
            self.last_position = pos
            return
        factor = (
            max(0.5, min(3, distance / max(0.001, dt) / 350))
            if settings.get("speed")
            and context.moving
            and state not in ("click", "idle")
            else 1
        )
        self.elapsed += dt * 1000 * factor
        while self.elapsed >= frames[self.index].duration:
            self.elapsed -= frames[self.index].duration
            self.index = (self.index + 1) % len(frames)
        size = self.cursor_pixels(self.project, pos)
        frame_key = (id(self.project), group, self.index, size)
        if rule:
            if (
                frame_key != self.last_frame
                or getattr(self, "last_target_role", None) != role
            ):
                if frame_key not in self.frame_cache:
                    path = (
                        self.temp
                        / f"{id(self.project)}-{group}-{self.index}-{size}.cur"
                    )
                    path.write_bytes(
                        encode_cur(frames[self.index], (size,), self.project.pixel)
                    )
                    self.frame_cache[frame_key] = path
                if native.set_live_cursor(role, self.frame_cache[frame_key], size):
                    self.dirty_roles.add(role)
        elif self.dirty_roles:
            native.reload()
            self.dirty_roles.clear()
        self.last_frame, self.last_target_role = frame_key, role
        self.update_companion(pos, now, group)
        self.overlay.update()
        self.last_position = pos

    def prepare_sounds(self, project):
        for pool in self.sounds.values():
            for sound in pool:
                sound.stop()
                sound.deleteLater()
        self.sound_indices = {}
        files = dict(project.settings.get("sounds", {}))
        if project.settings.get("sound") and not files.get("click"):
            files["click"] = [project.settings["sound"]]
        self.sounds = {}
        for trigger, paths in files.items():
            self.sounds[trigger] = []
            for path in paths:
                sound = QSoundEffect(self.sound)
                sound.setVolume(float(project.settings.get("volume", 0.5)))
                sound.setSource(QUrl.fromLocalFile(path))
                self.sounds[trigger].append(sound)

    def update_companion(self, pos, now, group=None):
        settings = self.project.settings
        companion = self.project.effects.get("companion", [])
        self.overlay.sprite = None
        if settings.get("companion"):
            if companion:
                elapsed = now * 1000 % sum(f.duration for f in companion)
                index = 0
                while (
                    index < len(companion) - 1 and elapsed >= companion[index].duration
                ):
                    elapsed -= companion[index].duration
                    index += 1
                self.overlay.sprite = pixmap(companion[index].image)
            elif group:
                self.overlay.sprite = self.project._runtime_pixmaps[group][self.index]
        self.overlay.sprite_size = settings.get(
            "companion_size", settings.get("effect_size", self.project.size)
        )
        self.overlay.sprite_pos = pos + QPoint(
            settings.get("companion_x", 20), settings.get("companion_y", 20)
        )
