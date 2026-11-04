"""Reversible, composable frame switches with a single preserved source sequence."""

from .codec import MAX_FRAMES, Frame

DEFAULTS = dict(reverse=False, pingpong=False, uniform_ms=None, speed=None)


def state(project, target):
    return dict(DEFAULTS, **project.settings.get("frame_controls", {}).get(target, {}))


def render(source, options):
    if options["pingpong"] and 2 * len(source) - 2 > MAX_FRAMES:
        raise ValueError("往返后超过 512 帧")
    frames = [Frame(frame.image, frame.x, frame.y, frame.duration) for frame in source]
    if options["reverse"]:
        frames.reverse()
    if options["pingpong"] and len(frames) > 2:
        frames.extend(
            Frame(frame.image, frame.x, frame.y, frame.duration)
            for frame in frames[-2:0:-1]
        )
    duration, speed = options["uniform_ms"], options["speed"]
    if duration is not None and (
        type(duration) is not int or not 17 <= duration <= 60000
    ):
        raise ValueError("统一时长应为 17～60000 毫秒")
    if speed is not None and (
        not isinstance(speed, (int, float)) or not 0.1 <= speed <= 10
    ):
        raise ValueError("速度应为 0.1～10 倍")
    for frame in frames:
        value = duration if duration is not None else frame.duration
        frame.duration = max(17, min(60000, round(value / (speed or 1))))
    return frames


def set_option(project, target, option, value, prepared=None):
    if option not in DEFAULTS:
        raise ValueError("未知帧控制")
    group, key = target.split(".", 1)
    source = project.sequence_sources.get(target, getattr(project, group).get(key, []))
    if not source:
        return False
    options = state(project, target)
    options[option] = value
    frames = render(source, options) if prepared is None else prepared
    if any(value is not None and value is not False for value in options.values()):
        project.sequence_sources.setdefault(
            target,
            [Frame(frame.image, frame.x, frame.y, frame.duration) for frame in source],
        )
        project.settings.setdefault("frame_controls", {})[target] = options
    else:
        project.sequence_sources.pop(target, None)
        project.settings.get("frame_controls", {}).pop(target, None)
    getattr(project, group)[key] = frames
    return True


def commit(project, target):
    """A direct frame edit makes the currently visible result the editable sequence."""
    project.sequence_sources.pop(target, None)
    project.settings.get("frame_controls", {}).pop(target, None)
