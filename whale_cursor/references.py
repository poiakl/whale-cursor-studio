"""Reference image validation independent of GUI and network clients."""

import io


def reference_image(image):
    if image.width * image.height > 16_000_000:
        raise ValueError("参考图超过 1600 万像素。")
    data = io.BytesIO()
    image.convert("RGBA").save(data, format="PNG")
    raw = data.getvalue()
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("单张参考图转换后不能超过 8 MB。")
    return raw
