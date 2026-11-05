"""Shared desktop sound options and PCM validation."""

from pathlib import Path
import wave
from .project import EVENTS


def playback_options(settings, trigger):
    value = settings.get("sound_playback", {}).get(trigger, {})
    return dict(
        wait_finish=value.get("wait_finish", True),
        interval_ms=value.get("interval_ms", 0),
    )


def validate_playback(value):
    if not isinstance(value, dict) or set(value) - {"wait_finish", "interval_ms"}:
        raise ValueError("音效播放参数无效")
    if type(value.get("wait_finish", True)) is not bool:
        raise ValueError("播完再播必须为开关值")
    interval = value.get("interval_ms", 0)
    if type(interval) is not int or not 0 <= interval <= 600000:
        raise ValueError("触发间隔应为 0～600000 毫秒")


def validate_audio_settings(settings):
    values = settings.get("sound_playback", {})
    if not isinstance(values, dict) or set(values) - set(EVENTS):
        raise ValueError("音效触发项无效")
    for value in values.values():
        validate_playback(value)


def pcm_files(files):
    if not isinstance(files, list) or len(files) > 64:
        raise ValueError("每个触发最多 64 个音频")
    result = []
    for filename in files:
        path = Path(filename).expanduser().resolve()
        if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("音频不存在或超过 16 MB：" + str(path))
        with wave.open(str(path), "rb") as audio:
            if audio.getcomptype() != "NONE" or not audio.getnframes():
                raise ValueError("请选择有效的未压缩 PCM WAV")
        result.append(str(path))
    return list(dict.fromkeys(result))
