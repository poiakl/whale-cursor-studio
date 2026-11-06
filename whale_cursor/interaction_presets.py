"""User presets keep individual effect and audio assets in the existing project format."""

import hashlib
import json
from zipfile import BadZipFile, ZipFile

from .fx import EXAMPLE
from .project import Project, user_dir

SETTINGS = {
    "audio": {"sounds", "sound", "sound_modes", "sound_playback", "volume"},
    "click": {
        "click_effect",
        "click_size",
        "click_opacity",
        "effect_size",
        "effect_lifetime",
        "effect_opacity",
    },
    "motion": {"effect_motion", "effect_keyframes"},
    "trail": {
        "trail",
        "trail_size",
        "trail_opacity",
        "trail_lifetime",
        "trail_spacing",
    },
    "companion": {
        "companion",
        "companion_size",
        "companion_opacity",
        "companion_x",
        "companion_y",
    },
}


def apply_preset(project, preset, kind):
    project.settings.update(
        {key: preset.settings[key] for key in SETTINGS[kind] if key in preset.settings}
    )
    if kind == "audio":
        return
    if kind == "motion":
        project.settings["effect_keyframes"] = preset.settings.get(
            "effect_keyframes", EXAMPLE
        )
        return
    project.effects[kind] = preset.effects.get(kind, [])
    target = "effects." + kind
    for current, saved in (
        (project.originals, preset.originals),
        (project.sequence_sources, preset.sequence_sources),
        (
            project.settings.setdefault("frame_controls", {}),
            preset.settings.get("frame_controls", {}),
        ),
    ):
        current.pop(target, None)
        if target in saved:
            current[target] = saved[target]


def save_preset(project, kind, name):
    name = name.strip()[:120]
    if not name:
        raise ValueError("请输入预设名称。")
    preset = Project(name=name)
    apply_preset(preset, project, kind)
    filename = hashlib.sha256(name.encode("utf-8")).hexdigest()[:24] + ".whalepack"
    path = user_dir() / "interaction-presets" / kind / filename
    preset.save(path)
    return path


def list_presets(kind):
    entries = []
    for path in (user_dir() / "interaction-presets" / kind).glob("*.whalepack"):
        try:
            with ZipFile(path) as archive:
                if archive.getinfo("project.json").file_size <= 2 * 1024 * 1024:
                    name = json.loads(archive.read("project.json"))["name"]
                    if isinstance(name, str):
                        entries.append((name, str(path)))
        except (OSError, BadZipFile, ValueError, KeyError):
            continue
    return sorted(entries)
