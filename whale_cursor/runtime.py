"""Opt-in desktop interactions. Poll mouse state only; no keyboard hooks or input logging."""

from __future__ import annotations
import math
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
from .project import Project, EVENTS, user_dir, event_key
from . import native


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
        now = time.monotonic()
        origin = self.geometry().topLeft()
        lifetime = max(0.1, self.settings.get("effect_lifetime", 850) / 1000)
        opacity = self.settings.get("effect_opacity", 1.0)
        self.particles = [v for v in self.particles if now - v[1] < lifetime]
        for pos, born, kind, vx, vy in self.particles:
            age = now - born
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
                p.drawEllipse(QRectF(x, y, 7, 12))
            elif kind == "trail":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor("#6BBEAA"))
                p.drawEllipse(local, 3, 3)
            else:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor("#4FAF9C"), 2))
                p.drawEllipse(local, 8 + age * 48, 8 + age * 48)
        p.setOpacity(opacity)
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
            paths = (
                self.project.export(self.temp / "base")
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
        self.overlay.settings = self.project.settings
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
        }
        for name, frames in groups.items():
            for index, frame in enumerate(frames):
                path = self.temp / f"{key}-{name}-{index}.cur"
                path.write_bytes(encode_cur(frame, (project.size,), project.pixel))
                self.frame_cache[(key, name, index)] = path
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
                paths = p.export(folder) if any(p.slots.values()) else {}
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
        self.overlay.settings = self.project.settings
        self.overlay.effects = {
            key: [(pixmap(f.image), f.duration) for f in frames]
            for key, frames in self.project.effects.items()
        }
        self.shot_state = ""
        self.shot_until = 0
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

    def _tick(self):
        now = time.monotonic()
        dt = min(0.2, now - self.last_tick)
        self.last_tick = now
        self.switch_rule(now)
        pos = QCursor.pos()
        delta = pos - self.last_position
        distance = math.hypot(delta.x(), delta.y())
        speed = distance / max(0.001, dt)
        settings = self.project.settings
        left, right = native.button_down(1), native.button_down(2)
        middle = native.button_down(4)
        new_left = left and not self.prev_left
        new_right = right and not self.prev_right
        new_middle = middle and not self.prev_middle
        clicked = new_left or new_right or new_middle
        released = (
            (self.prev_left and not left)
            or (self.prev_right and not right)
            or (self.prev_middle and not middle)
        )
        pressed = left or right or middle
        if distance > 0 or pressed or released:
            self.last_activity = now
        if clicked:
            self.press_time = now
            self.held_fired = False
        double = new_left and now - self.last_left_click <= 0.5
        if new_left:
            self.last_left_click = now
        self.prev_left, self.prev_right, self.prev_middle = left, right, middle
        if distance > 0:
            self.last_move = now
            if settings.get("trail") and math.hypot(
                pos.x() - self.last_trail_pos.x(), pos.y() - self.last_trail_pos.y()
            ) >= settings.get("trail_spacing", 12):
                self.overlay.emit_at(pos, "trail")
                self.last_trail_pos = QPoint(pos)
        moving = distance > 0 or (self.was_moving and now - self.last_move < 0.15)
        detected = native.current_role()
        role = detected if detected in self.project.slots else "Arrow"
        role_changed = role != self.role
        if role_changed:
            if self.dirty_roles:
                native.reload()
                self.dirty_roles.clear()
            self.state = ""
            self.index = 0
            self.elapsed = 0
            self.last_frame = None
            self.shot_state = ""
        self.role = role
        event_role = "Arrow" if settings.get("interaction_scope") == "common" else role
        events = {
            trigger: self.project.event_frames(event_role, trigger)
            for trigger in EVENTS
        }
        candidates = []
        if double:
            candidates.append("double_click")
        if new_left:
            candidates.append("left_click")
        if new_right:
            candidates.append("right_click")
        if new_middle:
            candidates.append("middle_click")
        if clicked:
            candidates.append("click")
            self.overlay.emit_at(pos, settings.get("click_effect", "ripple"))
        if released:
            candidates.append("release")
        if pressed and now - self.press_time >= 0.5 and not self.held_fired:
            candidates.append("hold")
            self.held_fired = True
        if role_changed:
            candidates.append("role_change")
        if moving and not self.was_moving:
            candidates.append("move_start")
        if not moving and self.was_moving:
            candidates.append("move_stop")
        self.was_moving = moving
        # Discrete events are one-shot; configured specific actions override generic clicks.
        shot = next((key for key in candidates if events[key]), None)
        if shot:
            self.shot_state = shot
            self.shot_until = now + sum(frame.duration for frame in events[shot]) / 1000
            self.state = ""
            self.elapsed = 0
            self.index = 0
        state = "base"
        if detected is not None:
            if now < self.shot_until and self.shot_state:
                state = self.shot_state
            elif pressed and distance > 0 and events["drag"]:
                state = "drag"
            elif (
                now - self.last_activity >= float(settings.get("idle_seconds", 5))
                and events["idle"]
            ):
                state = "idle"
            elif distance > 0:
                direction = (
                    ("right" if delta.x() > 0 else "left")
                    if abs(delta.x()) >= abs(delta.y())
                    else ("down" if delta.y() > 0 else "up")
                )
                if settings.get("direction") and events[direction]:
                    state = direction
                elif events["move"]:
                    state = "move"
        # Sounds can be used without a cursor animation. Continuous states sound on entry.
        audio_candidates = list(candidates)
        if pressed and distance > 0:
            audio_candidates.append("drag")
        if now - self.last_activity >= float(settings.get("idle_seconds", 5)):
            audio_candidates.append("idle")
        elif distance > 0:
            direction = (
                ("right" if delta.x() > 0 else "left")
                if abs(delta.x()) >= abs(delta.y())
                else ("down" if delta.y() > 0 else "up")
            )
            audio_candidates.extend([direction, "move"])
        sound_trigger = next(
            (key for key in audio_candidates if self.sounds.get(key)), ""
        )
        if sound_trigger and (
            sound_trigger in candidates or sound_trigger != self.last_sound_trigger
        ):
            pool = [
                sound
                for sound in self.sounds[sound_trigger]
                if sound.status() == QSoundEffect.Status.Ready
            ]
            if pool:
                random.choice(pool).play()
        self.last_sound_trigger = sound_trigger
        if state != self.state:
            self.state = state
            self.index = 0
            self.elapsed = 0
            self.last_frame = None
        frames = self.project.slots.get(role, []) if state == "base" else events[state]
        if not frames:
            if self.dirty_roles:
                native.reload()
                self.dirty_roles.clear()
            self.update_companion(pos, now)
            self.overlay.update()
            self.last_position = pos
            return
        group = "slots." + role if state == "base" else event_key(event_role, state)
        factor = (
            max(0.5, min(3, speed / 350))
            if settings.get("speed") and distance > 0 and state not in ("click", "idle")
            else 1
        )
        self.elapsed += dt * 1000 * factor
        while self.elapsed >= frames[self.index].duration:
            self.elapsed -= frames[self.index].duration
            self.index = (self.index + 1) % len(frames)
        frame_key = (id(self.project), group, self.index)
        if state != "base":
            if frame_key != self.last_frame:
                if native.set_live_cursor(
                    role, self.frame_cache[frame_key], self.project.size
                ):
                    self.dirty_roles.add(role)
        elif self.dirty_roles:
            native.reload()
            self.dirty_roles.clear()
        self.last_frame = frame_key
        self.update_companion(pos, now, group)
        self.overlay.update()
        self.last_position = pos

    def prepare_sounds(self, project):
        for pool in self.sounds.values():
            for sound in pool:
                sound.stop()
                sound.deleteLater()
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
