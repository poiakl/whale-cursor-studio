"""Small declarative keyframe language for pointer decorations."""

import json
import math

MOTIONS = {
    "fade": "渐隐",
    "rise": "上浮",
    "fall": "飘落",
    "spin": "旋转",
    "pop": "弹出",
    "orbit": "环绕",
    "burst": "散射",
    "keyframes": "自定义关键帧",
}
EXAMPLE = json.dumps(
    [
        {"t": 0, "x": 0, "y": 0, "scale": 0.3, "rotation": -25, "opacity": 1},
        {"t": 0.3, "x": 0, "y": -25, "scale": 1.3, "rotation": 15, "opacity": 1},
        {"t": 1, "x": 15, "y": -90, "scale": 0.8, "rotation": 70, "opacity": 0},
    ],
    ensure_ascii=False,
    indent=2,
)
DEFAULT = dict(x=0, y=0, scale=1, rotation=0, opacity=1)


def parse_keyframes(code):
    if not isinstance(code, str) or len(code) > 12000:
        raise ValueError("关键帧文本过长")
    frames = json.loads(code)
    if not isinstance(frames, list) or not 2 <= len(frames) <= 64:
        raise ValueError("需要 2～64 个关键帧")
    result = []
    limits = dict(
        t=(0, 1),
        x=(-2000, 2000),
        y=(-2000, 2000),
        scale=(0, 8),
        rotation=(-3600, 3600),
        opacity=(0, 1),
    )
    for frame in frames:
        if not isinstance(frame, dict) or "t" not in frame or set(frame) - set(limits):
            raise ValueError("每帧需要 t，只能使用 t、x、y、scale、rotation、opacity")
        value = dict(DEFAULT, **frame)
        for key, num in value.items():
            if (
                type(num) not in (int, float)
                or not math.isfinite(num)
                or not limits[key][0] <= num <= limits[key][1]
            ):
                raise ValueError("关键帧参数范围无效：" + key)
        result.append(value)
    result.sort(key=lambda frame: frame["t"])
    if (
        result[0]["t"] != 0
        or result[-1]["t"] != 1
        or any(a["t"] >= b["t"] for a, b in zip(result, result[1:]))
    ):
        raise ValueError("时间 t 必须从 0 到 1，且不能重复")
    return result


def pose(mode, t, keyframes=None, vx=0, vy=0):
    t = max(0, min(1, t))
    value = dict(DEFAULT, opacity=1 - t)
    if mode == "keyframes" and keyframes:
        for a, b in zip(keyframes, keyframes[1:]):
            if a["t"] <= t <= b["t"]:
                mix = (t - a["t"]) / (b["t"] - a["t"])
                return {key: a[key] + (b[key] - a[key]) * mix for key in DEFAULT}
    if mode == "rise":
        value.update(y=-100 * t)
    elif mode == "fall":
        value.update(x=25 * math.sin(t * 5), y=100 * t * t, rotation=90 * t)
    elif mode == "spin":
        value.update(rotation=360 * t, scale=1 + 0.2 * math.sin(math.pi * t))
    elif mode == "pop":
        value.update(scale=max(0.05, 1.6 * math.sin(math.pi * t / 2) * (1 - 0.5 * t)))
    elif mode == "orbit":
        value.update(
            x=40 * math.sin(t * math.tau),
            y=40 * (math.cos(t * math.tau) - 1),
            rotation=180 * t,
        )
    elif mode == "burst":
        value.update(
            x=vx * t, y=vy * t + 30 * t * t, rotation=180 * t, scale=1 - 0.5 * t
        )
    return value
