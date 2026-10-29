"""Independent button state, declarative conditions and latched trigger ownership."""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from .project import EVENTS, SLOTS

BUTTONS = {"any": "不限", "left": "左键", "right": "右键", "middle": "中键"}
MOTIONS = {
    "any": "不限",
    "still": "静止",
    "moving": "移动",
    "left": "向左",
    "right": "向右",
    "up": "向上",
    "down": "向下",
}
TRIGGERS = {"hold": "长按", "drag": "拖动", "pressed": "按住", **EVENTS}
ORDER = [
    "hold",
    "drag",
    "pressed",
    "double_click",
    "left_click",
    "right_click",
    "middle_click",
    "click",
    "release",
    "role_change",
    "move_start",
    "move_stop",
    "idle",
    "left",
    "right",
    "up",
    "down",
    "move",
]
CONTINUOUS = {"hold", "drag", "pressed", "idle", "move", "left", "right", "up", "down"}


def describe(rule):
    parts = [TRIGGERS[rule["trigger"]]]
    if rule.get("button", "any") != "any":
        parts.insert(0, BUTTONS[rule["button"]])
    if rule.get("motion", "any") != "any":
        parts.append(MOTIONS[rule["motion"]])
    if rule.get("role", "any") != "any":
        parts.append(SLOTS[rule["role"]])
    return " + ".join(parts)


def validate_rule(rule):
    if not isinstance(rule, dict) or rule.get("trigger") not in TRIGGERS:
        raise ValueError("动画规则的触发条件无效")
    if (
        rule.get("button", "any") not in BUTTONS
        or rule.get("motion", "any") not in MOTIONS
        or rule.get("role", "any") not in {"any", *SLOTS}
    ):
        raise ValueError("动画规则的附加条件无效")
    ident = rule.get("id", "")
    if (
        not ident
        or len(ident) > 100
        or not all(c.isalnum() or c in "_-" for c in ident)
    ):
        raise ValueError("动画规则标识无效")


@dataclass
class Context:
    now: float
    role: str | None
    moving: bool
    direction: str
    held: set = field(default_factory=set)
    long: set = field(default_factory=set)
    dragging: set = field(default_factory=set)
    pulses: dict = field(default_factory=dict)
    idle: bool = False


class Inputs:
    def __init__(self, now, pos, buttons):
        self.previous = dict(buttons)
        self.down_at = {b: now for b, v in buttons.items() if v}
        self.distance = {b: 0 for b, v in buttons.items() if v}
        self.last_position = tuple(pos)
        self.last_activity = now
        self.last_move = -1e9
        self.last_left = -1e9
        self.direction = ""
        self.moving = False
        self.role = "Arrow"

    def update(self, now, pos, buttons, role, idle_seconds=5):
        dx, dy = pos[0] - self.last_position[0], pos[1] - self.last_position[1]
        distance = math.hypot(dx, dy)
        pulses = {}
        pressed = {b for b, v in buttons.items() if v and not self.previous.get(b)}
        released = {b for b, v in self.previous.items() if v and not buttons.get(b)}
        for button in pressed:
            self.down_at[button] = now
            self.distance[button] = 0
        for button in released:
            self.down_at.pop(button, None)
            self.distance.pop(button, None)
        held = {b for b, v in buttons.items() if v}
        for button in held:
            self.distance[button] = self.distance.get(button, 0) + distance
        if distance or held or released:
            self.last_activity = now
        if distance >= 1:
            # Keep the previous axis around diagonals to avoid frame-to-frame flicker.
            horizontal = abs(dx) >= abs(dy)
            if self.direction in ("left", "right") and abs(dx) * 1.2 >= abs(dy):
                horizontal = True
            elif self.direction in ("up", "down") and abs(dy) * 1.2 >= abs(dx):
                horizontal = False
            self.direction = (
                ("right" if dx > 0 else "left")
                if horizontal
                else ("down" if dy > 0 else "up")
            )
            self.last_move = now
        moving = now - self.last_move < 0.15
        for button in pressed:
            pulses.setdefault("click", set()).add(button)
            pulses.setdefault(button + "_click", set()).add(button)
        if "left" in pressed:
            if now - self.last_left <= 0.5:
                pulses["double_click"] = {"left"}
            self.last_left = now
        if released:
            pulses["release"] = released
        if moving and not self.moving:
            pulses["move_start"] = {"any"}
        if not moving and self.moving:
            pulses["move_stop"] = {"any"}
        if role != self.role:
            pulses["role_change"] = {"any"}
        context = Context(
            now,
            role,
            moving,
            self.direction if now - self.last_move < 0.25 else "",
            held,
            {b for b in held if now - self.down_at[b] >= 0.5},
            {b for b in held if self.distance[b] >= 3 and moving},
            pulses,
            now - self.last_activity >= idle_seconds,
        )
        self.previous, self.role, self.moving, self.last_position = (
            dict(buttons),
            role,
            moving,
            tuple(pos),
        )
        return context


def owners(trigger, context):
    if trigger == "hold":
        return context.long
    if trigger == "drag":
        return context.dragging
    if trigger == "pressed":
        return context.held
    if trigger == "idle":
        return {"any"} if context.idle else set()
    if trigger == "move":
        return {"any"} if context.moving else set()
    if trigger in ("left", "right", "up", "down"):
        return {"any"} if context.moving and context.direction == trigger else set()
    return context.pulses.get(trigger, set())


class Resolver:
    def __init__(self):
        self.lock = None
        self.shots = {}

    @staticmethod
    def matching(rule, context, active):
        button = rule.get("button", "any")
        eligible = set(active) if button == "any" else set(active) & {button}
        if not eligible or rule.get("role", "any") not in ("any", context.role):
            return set()
        motion = rule.get("motion", "any")
        if (
            motion == "still"
            and context.moving
            or motion == "moving"
            and not context.moving
        ):
            return set()
        if motion in ("left", "right", "up", "down") and (
            context.direction != motion
            or (rule["trigger"] in CONTINUOUS and not context.moving)
        ):
            return set()
        return eligible

    @staticmethod
    def specificity(rule):
        return sum(rule.get(k, "any") != "any" for k in ("button", "motion", "role"))

    def select(self, rules, frames, context, order=None):
        if self.lock and self.lock[1] not in context.held:
            self.lock = None
            self.shots.clear()
        # A discrete occurrence chooses its clip once, at the event's conditions.
        # Its lifetime belongs to that clip, not to another role's longer animation.
        for trigger, buttons in context.pulses.items():
            if trigger in CONTINUOUS or self.lock and trigger != self.lock[0]:
                continue
            matches = [
                (
                    self.specificity(rule),
                    -i,
                    rule,
                    self.matching(rule, context, buttons),
                )
                for i, rule in enumerate(rules)
                if rule["trigger"] == trigger
                and rule.get("enabled", True)
                and frames(rule)
            ]
            matches = [match for match in matches if match[3]]
            if matches:
                _, _, rule, eligible = max(matches, key=lambda item: item[:2])
                self.shots[trigger] = (
                    context.now + sum(frame.duration for frame in frames(rule)) / 1000,
                    eligible,
                    rule,
                )
            else:
                self.shots.pop(trigger, None)
        available = []
        ranks = order or ORDER
        for index, rule in enumerate(rules):
            if not rule.get("enabled", True) or not frames(rule):
                continue
            trigger = rule["trigger"]
            if trigger in CONTINUOUS:
                eligible = self.matching(rule, context, owners(trigger, context))
            else:
                shot = self.shots.get(trigger)
                eligible = (
                    set(shot[1])
                    if shot and context.now < shot[0] and shot[2]["id"] == rule["id"]
                    else set()
                )
            if self.lock:
                if trigger != self.lock[0]:
                    continue
                eligible &= {self.lock[1]}
            if eligible:
                rank = ranks.index(trigger) if trigger in ranks else len(ranks)
                available.append(
                    (rank, -self.specificity(rule), index, rule, sorted(eligible)[0])
                )
        if available:
            _, _, _, rule, owner = min(available, key=lambda item: item[:3])
            if (
                rule.get("lock", rule["trigger"] == "hold")
                and owner != "any"
                and owner in context.held
            ):
                self.lock = (rule["trigger"], owner, rule)
                self.shots = {
                    key: shot
                    for key, shot in self.shots.items()
                    if key == rule["trigger"]
                }
            return rule
        return self.lock[2] if self.lock else None
