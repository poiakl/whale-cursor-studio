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
from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import Qt, QTimer, QPoint, QRectF, QRect
from PySide6.QtGui import QCursor, QPainter, QColor, QPen
from PySide6.QtWidgets import QWidget, QApplication
from PySide6.QtMultimedia import QSoundEffect
from PySide6.QtCore import QUrl
from .codec import encode_cur, Frame
from PIL import Image
from .project import Project, user_dir
from . import native
from .conditions import Inputs, Resolver, ORDER, owners
from .fx import pose, parse_keyframes, EXAMPLE


FEEDBACK_KEYS = {
    "click_effect",
    "effect_motion",
    "effect_keyframes",
    "effect_size",
    "effect_lifetime",
    "effect_opacity",
    "click_size",
    "click_opacity",
    "trail",
    "trail_size",
    "trail_opacity",
    "trail_lifetime",
    "trail_spacing",
    "companion",
    "companion_size",
    "companion_opacity",
    "companion_x",
    "companion_y",
}


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
        self.cursor_frame = None
        self.cursor_pos = QPoint()
        self.cursor_size = 64
        self.settings = {}
        self.effects = {}
        self.keyframes = parse_keyframes(EXAMPLE)
        self.render_time = None
        self.keep_visible = False
        self._idle_clean = False
        self._effect_bounds = QRect()
        self._particle_bounds = {}

    def fit_screens(self):
        # Never create a desktop-sized topmost window or give it an editor owner.
        pos = QCursor.pos()
        self.setGeometry(QRect(pos.x(), pos.y(), 2, 2))

    def particle_bounds(self, particle):
        pos, _, kind, vx, vy = particle
        trail = kind == "trail"
        size = (
            self.settings.get("trail_size", 24)
            if trail
            else self.settings.get("click_size", 64)
        )
        lifetime = max(
            0.1,
            self.settings.get("trail_lifetime" if trail else "effect_lifetime", 850)
            / 1000,
        )
        mode = "fade" if trail else self.settings.get("effect_motion", "fade")
        times = sorted(
            {*(i / 64 for i in range(65)), *(frame["t"] for frame in self.keyframes)}
        )
        rect = QRect()
        for t in times:
            motion = pose(mode, t, self.keyframes, vx, vy)
            x, y = pos.x() + motion["x"], pos.y() + motion["y"]
            margin = (
                max(size * motion["scale"], 3 if trail else 8 + t * lifetime * 48) + 4
            )
            if kind == "petals":
                age = t * lifetime
                x += vx * age
                y += vy * age + 100 * age * age
                margin = max(margin, 20)
            rect = rect.united(
                QRect(
                    math.floor(x - margin),
                    math.floor(y - margin),
                    math.ceil(margin * 2) + 2,
                    math.ceil(margin * 2) + 2,
                )
            )
        return rect

    def present(self, now=None):
        now = time.monotonic() if now is None else now
        self.render_time = now
        self.particles = [
            v
            for v in self.particles
            if now - v[1]
            < (
                self.settings.get("trail_lifetime", 850)
                if v[2] == "trail"
                else self.settings.get("effect_lifetime", 850)
            )
            / 1000
        ]
        rect = QRect()
        if self.cursor_frame is not None:
            # Reserve the whole hotspot range once. Individual animation frames
            # must not move or resize the native window at a stationary pointer.
            size = self.cursor_size
            rect = QRect(
                self.cursor_pos.x() - size - 2,
                self.cursor_pos.y() - size - 2,
                size * 2 + 4,
                size * 2 + 4,
            )
        if self.sprite is not None:
            rect = rect.united(
                QRect(
                    self.sprite_pos.x(),
                    self.sprite_pos.y(),
                    self.sprite_size + 2,
                    self.sprite_size + 2,
                )
            )
        if self.particles:
            active_ids = {id(particle) for particle in self.particles}
            self._particle_bounds = {
                key: value
                for key, value in self._particle_bounds.items()
                if key in active_ids
            }
            for particle in self.particles:
                key = id(particle)
                if key not in self._particle_bounds:
                    bounds = self.particle_bounds(particle)
                    self._particle_bounds[key] = (particle, bounds)
                    self._effect_bounds = self._effect_bounds.united(bounds)
            rect = rect.united(self._effect_bounds)
        else:
            self._effect_bounds = QRect()
            self._particle_bounds.clear()
        if rect.isNull():
            if self.keep_visible:
                # A transparent idle surface stays mapped: repeated clicks never
                # unmap/remap a layered HWND or expose its previous backing store.
                if not self.isVisible():
                    self.show()
                    self._idle_clean = False
                if not self._idle_clean:
                    self.repaint()
                    self._idle_clean = True
            else:
                self.hide()
            return
        anchor = (
            self.cursor_pos
            if self.cursor_frame is not None or self.sprite is not None
            else (self.particles[0][0] if self.particles else self.cursor_pos)
        )
        screen = QApplication.screenAt(anchor) or QApplication.primaryScreen()
        if screen:
            # The one-pixel inset also prevents the shell treating the decoration as full-screen.
            rect = rect.intersected(screen.geometry().adjusted(1, 1, -1, -1))
        if rect.isEmpty():
            self.hide()
            return
        self._idle_clean = False
        changed = self.geometry() != rect
        hidden = not self.isVisible()
        if changed:
            self.setGeometry(rect)
        if hidden:
            self.show()
        if hidden or changed:
            self.repaint()
        else:
            self.update()

    def emit_at(self, pos, kind):
        if kind in (None, "none", "off", False):
            return
        now = time.monotonic()
        if self.settings.get("effect_motion") == "burst" and kind not in (
            "trail",
            "none",
        ):
            for i in range(8):
                angle = i * math.tau / 8
                self.particles.append(
                    (
                        QPoint(pos),
                        now,
                        kind,
                        math.cos(angle) * 120,
                        math.sin(angle) * 120,
                    )
                )
        elif kind == "petals":
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
        # Explicitly replace every pixel, including transparent ones. Windows may
        # reuse a layered window's backing store after show/resize.
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.GlobalColor.transparent)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(
            QPainter.RenderHint.SmoothPixmapTransform,
            not self.settings.get("pixel", True),
        )
        now = time.monotonic() if self.render_time is None else self.render_time
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
            motion = pose(
                self.settings.get("effect_motion", "fade")
                if kind != "trail"
                else "fade",
                age / lifetime,
                self.keyframes,
                vx,
                vy,
            )
            p.save()
            p.translate(local.x() + motion["x"], local.y() + motion["y"])
            p.rotate(motion["rotation"])
            p.scale(max(0.001, motion["scale"]), max(0.001, motion["scale"]))
            local = QPoint(0, 0)
            p.setOpacity(motion["opacity"] * opacity)
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
                    (
                        pixmap(custom[index][0])
                        if isinstance(custom[index][0], Image.Image)
                        else custom[index][0]
                    ),
                )
                p.restore()
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
            p.restore()
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
        if self.cursor_frame is not None:
            frame = self.cursor_frame
            size = self.cursor_size
            p.setOpacity(1)
            x = self.cursor_pos.x() - origin.x() - frame.x * size / 256
            y = self.cursor_pos.y() - origin.y() - frame.y * size / 256
            p.drawPixmap(
                QRectF(x, y, size, size), pixmap(frame.image), QRectF(0, 0, 256, 256)
            )
        p.end()


class Engine:
    def __init__(self, on_error=lambda message: None):
        self.on_error = on_error
        self.timer = QTimer()
        self.timer.timeout.connect(self.tick)
        self.active = False
        self.overlay = Overlay()
        self.effect_overlay = Overlay()
        self.effect_overlay.keep_visible = True
        self.sound = QSoundEffect()
        self.sounds = {}
        self.sound_indices = {}
        self.temp = None
        self.initial = None
        self.frame_cache = {}
        self.rule_cache = {}
        self.rule_loader = None
        self.rule_futures = {}
        self.requested_rule = ""

    def start(self, project):
        native.require()
        if self.active:
            self.stop()
        native.recover_runtime()
        self.project = project.clone()
        self.project.migrate_events()
        self.base_project = self.project
        self.initial = native.snapshot()
        actual_size = native.cursor_bitmap_size()
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        ratio = screen.devicePixelRatio() if screen else 1
        self.cursor_scale = (
            actual_size / ratio / self.project.size if actual_size else 1
        )
        self.feedback_stamp = None
        self.active = True
        self.temp = user_dir() / "runtime" / uuid.uuid4().hex
        self.temp.mkdir(parents=True)
        self.frame_cache = {}
        self.rule_cache = {}
        self.rule_futures = {}
        self.requested_rule = ""
        self.rule_loader = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="scheme"
        )
        recovery = user_dir() / "runtime-recovery.json"
        tmp = recovery.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"snapshot": self.initial, "folder": str(self.temp)}),
            encoding="utf-8",
        )
        os.replace(tmp, recovery)
        try:
            self.prepare_frames(self.project)
            self.base_snapshot = self.initial
            self.blank_cursor = self.temp / "transparent.cur"
            self.blank_cursor.write_bytes(
                encode_cur(Frame(Image.new("RGBA", (256, 256)), 0, 0), (32,), True)
            )
            native.validate_file(self.blank_cursor, 32)
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
        self.apply_feedback(clear=True)
        self.overlay.fit_screens()
        self.effect_overlay.fit_screens()
        self.timer.start(16)

    def prepare_frames(self, project):
        project.migrate_events()
        groups = {
            **{"slots." + role: frames for role, frames in project.slots.items()},
            **project.events,
            **{
                "animations." + key: frames
                for key, frames in project.animations.items()
            },
        }
        project._runtime_frames = groups

    def stop(self):
        if not self.active:
            return
        self.timer.stop()
        if self.rule_loader is not None:
            self.rule_loader.shutdown(wait=False, cancel_futures=True)
            self.rule_loader = None
        self.rule_futures.clear()
        self.overlay.hide()
        self.effect_overlay.hide()
        self.effect_overlay.particles = []
        self.effect_overlay._effect_bounds = QRect()
        self.effect_overlay._particle_bounds.clear()
        self.overlay.particles = []
        self.overlay.cursor_frame = None
        self.dirty_roles = set()
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

    def switch_rule(self, now):
        if now - self.last_app_check >= 0.6:
            self.last_app_check = now
            exe = native.foreground_exe()
            rule_path = ""
            for rule in self.base_project.settings.get("app_rules", []):
                if str(rule.get("exe", "")).lower() == exe:
                    rule_path = rule.get("scheme", "")
                    break
            if rule_path != self.requested_rule:
                self.requested_rule = rule_path
                # Remove the old effect immediately, before any disk decoding.
                self.clear_effect_layer()
                for path, future in list(self.rule_futures.items()):
                    if path != rule_path and future.cancel():
                        del self.rule_futures[path]
        rule_path = self.requested_rule
        if rule_path == self.last_rule:
            return
        if rule_path:
            if rule_path not in self.rule_cache:
                future = self.rule_futures.get(rule_path)
                if future is None:
                    self.rule_futures[rule_path] = self.rule_loader.submit(
                        Project.load, rule_path
                    )
                    return
                if not future.done():
                    return
                project = future.result()
                self.prepare_frames(project)
                self.rule_cache[rule_path] = project
                del self.rule_futures[rule_path]
            self.project = self.rule_cache[rule_path]
        else:
            self.project = self.base_project
        self.prepare_sounds(self.project)
        self.apply_feedback(clear=True)
        self.resolver = Resolver()
        self.active_audio = set()
        self.last_sound_trigger = ""
        self.last_rule = rule_path
        if self.dirty_roles:
            native.reload()
        self.dirty_roles.clear()
        self.last_frame = None
        self.state = ""
        self.index = 0
        self.elapsed = 0
        self.animation_identity = None
        # A switch is not a mouse action; don't replay presses from the old scheme.
        self.inputs = Inputs(
            now, (QCursor.pos().x(), QCursor.pos().y()), self.read_buttons()
        )

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
        settings = self.feedback_settings()
        detected = native.current_role()
        shell_surface = native.shell_surface_active()
        context = self.inputs.update(
            now,
            (pos.x(), pos.y()),
            self.read_buttons(),
            detected,
            float(settings.get("idle_seconds", 5)),
        )
        role = detected if detected in self.project.slots else "Arrow"
        self.role = role
        self.overlay.cursor_pos = QPoint(pos)
        if "click" in context.pulses:
            self.effect_overlay.emit_at(pos, settings.get("click_effect", "ripple"))
        if (
            distance
            and settings.get("trail")
            and math.hypot(
                pos.x() - self.last_trail_pos.x(), pos.y() - self.last_trail_pos.y()
            )
            >= settings.get("trail_spacing", 12)
        ):
            self.effect_overlay.emit_at(pos, "trail")
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
        frames = (
            (self.rule_frames(rule) if rule else self.project.slots.get(role, []))
            if detected is not None
            else []
        )
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
        if not frames or shell_surface:
            if self.dirty_roles:
                native.reload()
                self.dirty_roles.clear()
            self.overlay.cursor_frame = None
            self.update_companion(pos, now)
            self.present_layers(now)
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
        frame_key = (id(self.project), group, self.index)
        self.overlay.cursor_frame = frames[self.index]
        self.overlay.cursor_size = max(
            8, min(512, round(self.project.size * self.cursor_scale))
        )
        if role not in self.dirty_roles:
            if native.set_live_cursor(role, self.blank_cursor, 32):
                self.dirty_roles.add(role)
            else:
                self.overlay.cursor_frame = None
        self.last_frame, self.last_target_role = frame_key, role
        self.update_companion(pos, now, group)
        self.present_layers(now)
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
        settings = self.feedback_settings()
        companion = self.base_project.effects.get("companion", [])
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
            else:
                fallback = self.project._runtime_frames.get(group, []) if group else []
                fallback = (
                    fallback
                    or self.project.slots.get(self.role, [])
                    or self.base_project.slots.get("Arrow", [])
                )
                if fallback:
                    self.overlay.sprite = pixmap(
                        fallback[self.index % len(fallback)].image
                    )
        self.overlay.sprite_size = settings.get(
            "companion_size", settings.get("effect_size", self.project.size)
        )
        self.overlay.sprite_pos = pos + QPoint(
            settings.get("companion_x", 20), settings.get("companion_y", 20)
        )

    def feedback_settings(self):
        return dict(
            self.project.settings,
            **{
                key: value
                for key, value in self.base_project.settings.items()
                if key in FEEDBACK_KEYS
            },
        )

    def apply_feedback(self, clear=False):
        settings = self.feedback_settings()
        self.overlay.settings = dict(settings, pixel=self.project.pixel)
        self.overlay.keyframes = parse_keyframes(
            settings.get("effect_keyframes", EXAMPLE)
        )
        self.overlay.effects = {
            key: [(f.image, f.duration) for f in frames]
            for key, frames in self.base_project.effects.items()
        }
        self.effect_overlay.settings = self.overlay.settings
        self.effect_overlay.effects = self.overlay.effects
        self.effect_overlay.keyframes = self.overlay.keyframes
        if clear:
            self.clear_effect_layer()
        else:
            self.effect_overlay.particles = [
                p
                for p in self.effect_overlay.particles
                if (p[2] == "trail" and settings.get("trail"))
                or (
                    p[2] != "trail"
                    and settings.get("click_effect") not in (None, "none", "off", False)
                )
            ]
            self.effect_overlay._particle_bounds.clear()
            self.effect_overlay._effect_bounds = QRect()
            if not settings.get("companion"):
                self.overlay.sprite = None
            self.present_layers(time.monotonic())

    def sync_feedback(self, project):
        if not self.active:
            return False
        values = {
            key: copy.deepcopy(value)
            for key, value in project.settings.items()
            if key in FEEDBACK_KEYS
        }
        stamp = (
            repr(values),
            tuple(
                (key, tuple((id(f.image), f.x, f.y, f.duration) for f in frames))
                for key, frames in project.effects.items()
            ),
        )
        if stamp == self.feedback_stamp:
            return False
        self.feedback_stamp = stamp
        self.base_project.settings.update(values)
        self.base_project.effects = {
            key: [Frame(f.image, f.x, f.y, f.duration) for f in frames]
            for key, frames in project.effects.items()
        }
        self.apply_feedback()
        return True

    def configure_effect_layer(self):
        self.effect_overlay.settings = self.overlay.settings
        self.effect_overlay.effects = self.overlay.effects
        self.effect_overlay.keyframes = self.overlay.keyframes
        self.clear_effect_layer()

    def clear_effect_layer(self):
        self.effect_overlay.particles = []
        self.effect_overlay._effect_bounds = QRect()
        self.effect_overlay._particle_bounds.clear()
        self.effect_overlay.render_time = None
        if self.effect_overlay.isVisible():
            self.effect_overlay.repaint()
            self.effect_overlay._idle_clean = True

    def present_layers(self, now):
        self.effect_overlay.present(now)
        self.overlay.present(now)
        # Native popups (combo lists/menus) can enter the topmost band after us.
        # Restore only these unowned, nonactivating layers, never the main window.
        native.restack_overlays(
            [
                int(layer.winId())
                for layer in (self.effect_overlay, self.overlay)
                if layer.isVisible()
            ]
        )
