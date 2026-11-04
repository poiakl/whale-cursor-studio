"""Portable AI character configurations; reference images are embedded, keys are not."""

import copy
import hashlib
import io
from pathlib import Path
from PIL import Image
from .project import Project, user_dir
from .ai_api import reference_image


def cache_reference(raw):
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("参考图不能超过 8 MB")
    with Image.open(io.BytesIO(raw)) as image:
        raw = reference_image(image)
    folder = user_dir() / "ai-references"
    folder.mkdir(exist_ok=True)
    path = folder / (hashlib.sha256(raw).hexdigest() + ".png")
    if not path.exists():
        path.write_bytes(raw)
    return str(path)


def embed_references(archive, settings):
    config = settings.get("ai_prompt", {})
    paths = config.get("references", [])
    if not isinstance(paths, list) or len(paths) > 8:
        raise ValueError("参考图列表无效")
    embedded, total = [], 0
    for filename in paths:
        path = Path(filename)
        if path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("参考图不能超过 8 MB")
        raw = path.read_bytes()
        total += len(raw)
        if total > 32 * 1024 * 1024:
            raise ValueError("参考图合计不能超过 32 MB")
        name = "references/" + hashlib.sha256(raw).hexdigest() + ".png"
        if name not in archive.namelist():
            archive.writestr(name, raw)
        embedded.append("@" + name)
    if paths:
        config["references"] = embedded


def restore_references(archive, settings):
    config = settings.get("ai_prompt", {})
    paths = config.get("references", [])
    if not isinstance(paths, list) or len(paths) > 8:
        raise ValueError("参考图列表无效")
    result, total = [], 0
    for filename in paths:
        if not isinstance(filename, str):
            raise ValueError("参考图路径无效")
        if filename.startswith("@references/"):
            info = archive.getinfo(filename[1:])
            if info.file_size > 8 * 1024 * 1024:
                raise ValueError("参考图不能超过 8 MB")
            total += info.file_size
            if total > 32 * 1024 * 1024:
                raise ValueError("参考图合计不能超过 32 MB")
            result.append(cache_reference(archive.read(filename[1:])))
        elif Path(filename).is_file():
            result.append(filename)
    config["references"] = result


def profiles():
    folder = user_dir() / "ai-characters"
    folder.mkdir(exist_ok=True)
    return sorted(folder.glob("*.whalepack"))


def save_profile(name, config):
    name = name.strip()
    if not name:
        raise ValueError("请填写项目名")
    name = name[:120]
    path = (
        user_dir()
        / "ai-characters"
        / (hashlib.sha256(name.encode()).hexdigest()[:20] + ".whalepack")
    )
    project = Project(name=name)
    project.settings["ai_prompt"] = copy.deepcopy(config)
    project.save(path)
    return path


def load_profile(path):
    project = Project.load(path)
    return project.name, copy.deepcopy(project.settings.get("ai_prompt", {}))
