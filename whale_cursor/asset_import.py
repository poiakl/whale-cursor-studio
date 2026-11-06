"""Read the import manifest produced alongside AI sprite sheets."""

import json
from pathlib import Path

from PIL import Image

from .codec import MAX_FRAMES, MAX_PIXELS, import_images, split_sheet
from .generation_brief import frame_metadata
from .hotspots import parse_hotspots
from .project import Project

IMAGE_FILTER = "图片与动画 (*.png *.gif *.webp *.bmp *.jpg *.jpeg *.cur *.ani *.ico)"


def read_config(path):
    path = Path(path)
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("导入配置不能超过 2 MB")
    return json.loads(path.read_text("utf-8-sig"))


def parse_manifest(data):
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("配置文件应包含 items 数组")
    result = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ValueError("每条导入配置需要 id")
        target = item["id"]
        count = item.get("frame_count")
        grid = item.get("sheet_grid", [1, 1])
        if (
            type(count) is not int
            or not 1 <= count <= MAX_FRAMES
            or not isinstance(grid, list)
            or len(grid) != 2
            or any(
                type(value) is not int or not 1 <= value <= MAX_FRAMES for value in grid
            )
        ):
            raise ValueError(f"{target} 的帧数或行列配置无效")
        if target in result:
            raise ValueError(f"重复的配置条目 {target}")
        if "filename" in item and not isinstance(item["filename"], str):
            raise ValueError(f"{target} 的文件名无效")
        result[target] = {**item, "sheet_grid": grid}
    return result


def read_manifest(path):
    return parse_manifest(read_config(path))


def asset_stem(name):
    return Path(name.replace("\\", "/")).stem.casefold()


def configured_target(filename, targets, manifest):
    stem = asset_stem(filename)
    candidates = sorted(targets)
    for target in candidates:
        if stem in {target.casefold(), target.replace(".", "_").casefold()}:
            return target
    matches = [
        target
        for target in candidates
        if manifest.get(target, {}).get("filename")
        and stem == asset_stem(manifest[target]["filename"])
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def read_frames(paths, pixel, grid=(1, 1), count=None, spec=None, prepared=None):
    if grid != (1, 1):
        if len(paths) != 1:
            raise ValueError("切分精灵图时请选择一张图片")
        path = Path(paths[0])
        if path.stat().st_size > 128 * 1024 * 1024:
            raise ValueError("图片超过 128 MB")
        with Image.open(path) as image:
            if (
                image.width * image.height > MAX_PIXELS
                or getattr(image, "n_frames", 1) != 1
            ):
                raise ValueError("请使用不超过 1600 万像素的单张精灵图")
            columns, rows = grid
            if spec and image.width * rows != image.height * columns:
                raise ValueError("带热点配置的精灵图需要正方形单格")
            frames = split_sheet(image, columns, rows, count, pixel)
    else:
        frames = []
        if prepared is None:
            for path in sorted(paths):
                frames.extend(import_images(Path(path), pixel))
                if len(frames) > MAX_FRAMES:
                    raise ValueError("每个动画最多支持 512 帧")
        else:
            frames = [frame.clone() for frame in prepared]
        if count is not None:
            if not 1 <= count <= len(frames):
                raise ValueError(f"素材只有 {len(frames)} 帧，无法读取 {count} 帧")
            frames = frames[:count]
    if spec and frames:
        if "timeline" in spec and (
            not isinstance(spec["timeline"], list)
            or any(not isinstance(record, dict) for record in spec["timeline"])
        ):
            raise ValueError("timeline 应为逐帧配置数组")
        staging = Project(slots={"Arrow": frames})
        metadata = list(frame_metadata(spec, max(spec["frame_count"], len(frames))))
        records = []
        for index, frame in enumerate(frames):
            record = metadata[index] if index < len(metadata) else {}
            if "hotspot_px" in record:
                records.append({**record, "frame": index + 1})
            elif "duration_ms" in record:
                records.append(
                    {
                        **record,
                        "frame": index + 1,
                        "hotspot_px": [frame.x, frame.y],
                        "cell_px": [256, 256],
                    }
                )
        if records:
            edits = parse_hotspots(
                {"cell_px": spec.get("cell_px", [256, 256]), "timeline": records},
                staging,
                ("slots", "Arrow"),
            )
            for edit in edits:
                frame = frames[edit.frame]
                frame.x, frame.y = edit.x, edit.y
                if edit.duration is not None:
                    frame.duration = edit.duration
    return frames
