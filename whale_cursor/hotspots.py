"""Validate hotspot JSON completely before changing any frames."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
from .project import SLOTS, EVENTS


@dataclass(frozen=True)
class HotspotEdit:
    group: str
    key: str
    frame: int
    x: int
    y: int
    duration: int | None = None


def parse_hotspots(data, project, current):
    edits = {}
    metadata = {
        "version",
        "size",
        "canvas_size",
        "cell_px",
        "image_size",
        "width",
        "height",
        "project",
        "style",
        "coordinate_origin",
        "normalized",
        "coordinate_space",
    }

    def target(value):
        if value is None:
            return current
        name = Path(str(value).replace("\\", "/")).name
        if name.lower().endswith((".png", ".cur", ".ani", ".webp")):
            name = name.rsplit(".", 1)[0]
        for role, title in SLOTS.items():
            if name.casefold() in (role.casefold(), title.casefold()):
                return "slots", role
        group, sep, key = name.partition(".")
        if sep and group in ("slots", "events", "effects", "animations"):
            if group == "slots" and key not in SLOTS:
                raise ValueError("未知系统用途：" + key)
            if group == "events":
                role, _, trigger = key.partition(".")
                if role not in SLOTS or trigger not in EVENTS:
                    raise ValueError("未知互动用途：" + key)
            if key not in getattr(project, group):
                raise ValueError("方案中没有目标素材：" + name)
            return group, key
        if name in EVENTS:
            return "events", "Arrow." + name
        raise ValueError("无法匹配热点目标：" + name)

    def identity(record):
        for name in ("id", "role", "name", "target"):
            if name in record:
                return target(record[name])
        for name in ("filename", "file"):
            if name in record:
                try:
                    return target(record[name])
                except ValueError:
                    pass
        return None

    def dimensions(record, inherited):
        size = record.get(
            "cell_px", record.get("canvas_size", record.get("size", inherited))
        )
        if isinstance(size, dict):
            size = [size.get("width"), size.get("height")]
        if isinstance(size, (int, float)):
            size = [size, size]
        if (
            not isinstance(size, (list, tuple))
            or len(size) != 2
            or not all(
                isinstance(v, (int, float))
                and not isinstance(v, bool)
                and math.isfinite(v)
                and v > 0
                for v in size
            )
        ):
            raise ValueError("热点画布尺寸应为 [宽, 高] 或正整数。")
        return size

    def point(value):
        if isinstance(value, dict):
            if "hotspot_x" in value or "hotspot_y" in value:
                value = {"x": value.get("hotspot_x"), "y": value.get("hotspot_y")}
            value = value.get(
                "hotspot_px", value.get("hotspot", value.get("hotspot_xy", value))
            )
        if isinstance(value, dict):
            value = [value.get("x"), value.get("y")]
        if (
            not isinstance(value, (list, tuple))
            or len(value) != 2
            or not all(
                isinstance(v, (int, float))
                and not isinstance(v, bool)
                and math.isfinite(v)
                for v in value
            )
        ):
            raise ValueError("热点须为 [x, y]、hotspot_px 或 x/y 坐标。")
        return value

    def add(value, dst, size, index=None, normalized=False):
        group, key = dst
        frames = getattr(project, group).get(key, [])
        if not frames:
            raise ValueError("请先导入对应图片 / 帧：" + group + "." + key)
        x, y = point(value)
        width, height = size
        if normalized:
            if not 0 <= x <= 1 or not 0 <= y <= 1:
                raise ValueError("归一化热点必须在 0～1。")
            x, y = round(x * 255), round(y * 255)
        else:
            if not 0 <= x < width or not 0 <= y < height:
                raise ValueError(
                    f"{group}.{key} 热点 ({x}, {y}) 超出 {width}×{height} 画布。"
                )
            x, y = min(255, round(x * 256 / width)), min(255, round(y * 256 / height))
        indices = range(len(frames)) if index is None else [index]
        duration = (
            value.get("duration_ms", value.get("duration"))
            if isinstance(value, dict)
            else None
        )
        if duration is not None and (
            not isinstance(duration, int)
            or isinstance(duration, bool)
            or not 17 <= duration <= 60000
        ):
            raise ValueError("帧时长须为 17～60000 ms。")
        for i in indices:
            if (
                not isinstance(i, int)
                or isinstance(i, bool)
                or not 0 <= i < len(frames)
            ):
                raise ValueError(
                    f"{group}.{key} 的第 {i + 1} 帧不存在，请先导入完整动画。"
                )
            identity = (group, key, i)
            if identity in edits:
                raise ValueError(f"{group}.{key} 第 {i + 1} 帧有重复热点记录。")
            edits[identity] = HotspotEdit(group, key, i, x, y, duration)

    def visit(value, dst=current, size=(256, 256), normalized=False):
        if isinstance(value, (list, tuple)):
            if len(value) == 2 and all(isinstance(v, (int, float)) for v in value):
                add(value, dst, size, normalized=normalized)
                return
            for index, record in enumerate(value):
                if isinstance(record, dict) and identity(record) is not None:
                    visit(record, dst, size, normalized)
                else:
                    record = record if isinstance(record, dict) else {"hotspot": record}
                    index = record.get(
                        "frame_index",
                        record.get("index", record.get("frame", index + 1) - 1),
                    )
                    add(
                        record,
                        dst,
                        dimensions(record, size),
                        index,
                        normalized or record.get("normalized", False),
                    )
            return
        if not isinstance(value, dict):
            raise ValueError("热点 JSON 应为对象或数组。")
        size = dimensions(value, size)
        normalized = (
            normalized
            or value.get("normalized", False)
            or value.get("coordinate_space") == "normalized"
        )
        resolved = identity(value)
        dst = resolved if resolved is not None else dst
        if any(k in value for k in ("items", "images", "cursors")):
            records = value.get("items", value.get("images", value.get("cursors")))
            if isinstance(records, dict):
                visit(records, dst, size, normalized)
                return
            for item in records:
                visit(item, dst, size, normalized)
        elif "timeline" in value or "frames" in value:
            sequence = value.get("timeline", value.get("frames"))
            if not isinstance(sequence, list):
                raise ValueError("timeline / frames 应为逐帧数组。")
            visit(sequence, dst, size, normalized)
        elif "hotspots" in value:
            visit(value["hotspots"], dst, size, normalized)
        elif any(
            k in value
            for k in (
                "hotspot_px",
                "hotspot",
                "hotspot_xy",
                "hotspot_x",
                "hotspot_y",
                "x",
                "y",
            )
        ):
            index = value.get("frame_index", value.get("index"))
            if "frame" in value:
                index = value["frame"] - 1
            add(value, dst, size, index, normalized)
        else:
            for key, record in value.items():
                if key in metadata:
                    continue
                if key in ("slots", "events", "effects", "animations") and isinstance(
                    record, dict
                ):
                    for name, entry in record.items():
                        visit(entry, target(key + "." + name), size, normalized)
                else:
                    visit(record, target(key), size, normalized)

    try:
        visit(data)
    except (TypeError, KeyError, AttributeError) as error:
        raise ValueError(
            "热点 JSON 结构有误，请检查用途标识、帧编号及坐标。"
        ) from error
    if not edits:
        raise ValueError("文件中没有可导入的热点。")
    return list(edits.values())
