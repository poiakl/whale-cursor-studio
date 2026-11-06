"""User presets keep effect and audio assets in the existing project format."""

import hashlib
import json
from zipfile import BadZipFile, ZipFile

from .fx import EXAMPLE
from .project import Project, user_dir
from .runtime import FEEDBACK_KEYS, RESPONSE_KEYS

SETTINGS = {
    "effects": FEEDBACK_KEYS | RESPONSE_KEYS,
    "audio": {"sounds", "sound", "sound_modes", "sound_playback", "volume"},
}


def effect_entries(mapping):
    return {key: value for key, value in mapping.items() if key.startswith("effects.")}


def apply_preset(project, preset, kind):
    project.settings.update(
        {key: preset.settings[key] for key in SETTINGS[kind] if key in preset.settings}
    )
    if kind == "effects":
        project.effects = preset.effects
        project.settings["effect_keyframes"] = preset.settings.get(
            "effect_keyframes", EXAMPLE
        )
        for attr in ("originals", "sequence_sources"):
            current = getattr(project, attr)
            setattr(
                project,
                attr,
                {
                    **{
                        key: value
                        for key, value in current.items()
                        if not key.startswith("effects.")
                    },
                    **effect_entries(getattr(preset, attr)),
                },
            )
        controls = project.settings.get("frame_controls", {})
        project.settings["frame_controls"] = {
            **{
                key: value
                for key, value in controls.items()
                if not key.startswith("effects.")
            },
            **effect_entries(preset.settings.get("frame_controls", {})),
        }


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
