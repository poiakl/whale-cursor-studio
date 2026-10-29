"""Opt-in desktop interactions. Poll mouse state only; no keyboard hooks or input logging."""

from __future__ import annotations
import math
import os
import random
import uuid
import shutil
import json
import time
from PySide6.QtCore import Qt, QTimer, QPoint, QRectF
from PySide6.QtGui import QCursor, QPainter, QColor, QPen
from PySide6.QtWidgets import QWidget, QApplication
from PySide6.QtMultimedia import QSoundEffect
from PySide6.QtCore import QUrl
from .codec import encode_cur
from .project import Project, user_dir, event_key
from . import native


def pixmap(image):
    from PySide6.QtGui import QImage, QPixmap

    raw = image.convert("RGBA").tobytes()
    return QPixmap.fromImage(
        QImage(
            raw,
            image.width,
            image.height,
            image.width * 4,
            QImage.Format.Format_RGBA8888,
        ).copy()
    )


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
        self.particles = [v for v in self.particles if now - v[1] < 0.85]
        for pos, born, kind, vx, vy in self.particles:
            age = now - born
            local = pos - origin
            p.setOpacity(max(0, 1 - age / 0.85))
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
        p.setOpacity(1)
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
            paths = self.project.export(self.temp / "base")
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
        self.click_until = 0
        self.prev_left = native.button_down(1)
        self.prev_right = native.button_down(2)
        self.state = ""
        self.index = 0
        self.elapsed = 0
        self.last_frame = None
        self.dirty_roles = set()
        self.role = "Arrow"
        self.last_rule = ""
        self.last_app_check = 0
        self.sound.setVolume(float(project.settings.get("volume", 0.5)))
        sound = project.settings.get("sound", "")
        if sound:
            self.sound.setSource(QUrl.fromLocalFile(sound))
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
                paths = p.export(folder)
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
        settings = self.base_project.settings
        left, right = native.button_down(1), native.button_down(2)
        clicked = (left and not self.prev_left) or (right and not self.prev_right)
        self.prev_left, self.prev_right = left, right
        if distance > 0:
            self.last_move = now
            if settings.get("trail"):
                self.overlay.emit_at(pos, "trail")
        detected = native.current_role()
        role = detected if detected in self.project.slots else "Arrow"
        if role != self.role:
            if self.dirty_roles:
                native.reload()
                self.dirty_roles.clear()
            self.state = ""
            self.index = 0
            self.elapsed = 0
            self.last_frame = None
        self.role = role
        events = {
            trigger: self.project.event_frames(role, trigger)
            for trigger in ("click", "idle", "move", "left", "right", "up", "down")
        }
        if clicked:
            self.overlay.emit_at(pos, settings.get("click_effect", "ripple"))
            if settings.get("sound"):
                self.sound.play()
            self.click_until = now + sum(f.duration for f in events["click"]) / 1000
            # Repeated clicks restart the one-shot action.
            self.state = ""
            self.elapsed = 0
            self.index = 0
        state = "base"
        if detected is not None:
            if now < self.click_until and events["click"]:
                state = "click"
            elif (
                now - self.last_move >= float(settings.get("idle_seconds", 5))
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
            self.overlay.sprite = None
            self.overlay.update()
            self.last_position = pos
            return
        group = "slots." + role if state == "base" else event_key(role, state)
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
        self.overlay.sprite = (
            self.project._runtime_pixmaps[group][self.index]
            if settings.get("companion")
            else None
        )
        self.overlay.sprite_size = self.project.size
        self.overlay.sprite_pos = pos + QPoint(20, 20)
        self.overlay.update()
        self.last_position = pos
