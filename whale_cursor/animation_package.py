"""Export/import one animation with its own timing, switches and related settings."""

import copy
from .project import Project, EVENTS

GROUPS = ("slots", "events", "effects", "animations")


def trigger_for(target):
    group, key = target.split(".", 1)
    return (
        key.rsplit(".", 1)[-1]
        if group == "events"
        else "click"
        if target == "effects.click"
        else None
    )


def extract(project, target):
    group, key = target.split(".", 1)
    if group not in GROUPS or not getattr(project, group).get(key):
        raise ValueError("当前动画没有可导出的帧")
    clip = Project(
        name=target,
        size=project.size,
        pixel=project.pixel,
        multi_size=project.multi_size,
    )
    getattr(clip, group)[key] = [
        frame.clone() for frame in getattr(project, group)[key]
    ]
    clip.settings["animation_package"] = dict(version=1, source=target)
    for field in ("originals", "sequence_sources"):
        if target in getattr(project, field):
            getattr(clip, field)[target] = [
                frame.clone() for frame in getattr(project, field)[target]
            ]
    options = project.settings.get("frame_controls", {}).get(target)
    if options:
        clip.settings["frame_controls"] = {target: copy.deepcopy(options)}
    if group == "events":
        clip.settings["interaction_scope"] = "role"
    if group == "animations":
        clip.settings["animation_rules"] = [
            copy.deepcopy(rule)
            for rule in project.settings.get("animation_rules", [])
            if rule["id"] == key
        ]
    if group == "effects":
        prefixes = {
            "click": ("click_", "effect_"),
            "trail": ("trail",),
            "companion": ("companion",),
        }[key]
        for name, value in project.settings.items():
            if name.startswith(prefixes):
                clip.settings[name] = copy.deepcopy(value)
    trigger = trigger_for(target)
    if trigger:
        clip.settings["sounds"] = {
            trigger: copy.deepcopy(
                project.settings.get("sounds", {}).get(
                    trigger,
                    [project.settings["sound"]]
                    if trigger == "click" and project.settings.get("sound")
                    else [],
                )
            )
        }
        clip.settings["sound_modes"] = {
            trigger: project.settings.get("sound_modes", {}).get(trigger, "random")
        }
        clip.settings["sound_playback"] = {
            trigger: copy.deepcopy(
                project.settings.get("sound_playback", {}).get(trigger, {})
            )
        }
        clip.settings["volume"] = project.settings.get("volume", 0.5)
    ai = copy.deepcopy(project.settings.get("ai_prompt", {}))
    if ai:
        for field in ("selected", "counts", "item_instructions"):
            ai[field] = (
                {target: ai[field][target]} if target in ai.get(field, {}) else {}
            )
        paths = {
            "slots": ["系统指针"],
            "effects": ["装饰"],
            "animations": ["条件动画"],
        }.get(group, ["互动指针", "互动指针/" + EVENTS.get(key.rsplit(".", 1)[-1], "")])
        ai["group_instructions"] = {
            path: value
            for path, value in ai.get("group_instructions", {}).items()
            if path in paths
        }
        clip.settings["ai_prompt"] = ai
    return clip


def merge(project, clip, target):
    source = clip.settings.get("animation_package", {}).get("source", "")
    source_group, _, source_key = source.partition(".")
    group, key = target.split(".", 1)
    if (
        source_group not in GROUPS
        or not source_key
        or not getattr(clip, source_group).get(source_key)
    ):
        raise ValueError("这不是单动画文件，请选择导出的 .whaleanim 文件")
    if group not in GROUPS or key == "__empty":
        raise ValueError("请先选择用途或添加条件动画")
    getattr(project, group)[key] = [
        frame.clone() for frame in getattr(clip, source_group)[source_key]
    ]
    project.size, project.pixel, project.multi_size = (
        clip.size,
        clip.pixel,
        clip.multi_size,
    )
    for field in ("sequence_sources",):
        getattr(project, field).pop(target, None)
        if source in getattr(clip, field):
            getattr(project, field)[target] = [
                frame.clone() for frame in getattr(clip, field)[source]
            ]
    project.originals[target] = [
        frame.clone() for frame in getattr(project, group)[key]
    ]
    project.settings.setdefault("frame_controls", {}).pop(target, None)
    if source in clip.settings.get("frame_controls", {}):
        project.settings["frame_controls"][target] = copy.deepcopy(
            clip.settings["frame_controls"][source]
        )
    if group == source_group == "effects":
        prefixes = {
            "click": ("click_", "effect_"),
            "trail": ("trail",),
            "companion": ("companion",),
        }[key]
        if source_key == key:
            for name, value in clip.settings.items():
                if name.startswith(prefixes):
                    project.settings[name] = copy.deepcopy(value)
    if group == source_group == "animations":
        rules = clip.settings.get("animation_rules", [])
        if rules:
            replacement = dict(copy.deepcopy(rules[0]), id=key)
            project.settings["animation_rules"] = [
                replacement if rule["id"] == key else rule
                for rule in project.settings.get("animation_rules", [])
            ]
    source_trigger, trigger = trigger_for(source), trigger_for(target)
    if source_trigger and trigger:
        project.settings.setdefault("sounds", {})[trigger] = copy.deepcopy(
            clip.settings.get("sounds", {}).get(source_trigger, [])
        )
        project.settings.setdefault("sound_modes", {})[trigger] = clip.settings.get(
            "sound_modes", {}
        ).get(source_trigger, "random")
        project.settings.setdefault("sound_playback", {})[trigger] = copy.deepcopy(
            clip.settings.get("sound_playback", {}).get(source_trigger, {})
        )
        project.settings["volume"] = clip.settings.get("volume", 0.5)
    if clip.settings.get("ai_prompt"):
        project.settings.setdefault("animation_ai_metadata", {})[target] = (
            copy.deepcopy(clip.settings["ai_prompt"])
        )
