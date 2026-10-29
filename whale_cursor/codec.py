"""CUR / RIFF ACON codecs. Coordinates always use a 256 px editing canvas."""

from __future__ import annotations
import io
import math
import struct
from dataclasses import dataclass
from pathlib import Path
from PIL import Image, ImageOps, ImageSequence

MAX_FRAMES = 512
MAX_PIXELS = 4096 * 4096
SIZES = (32, 48, 64, 96, 128, 256)


@dataclass
class Frame:
    image: Image.Image
    x: int = 128
    y: int = 128
    duration: int = 100

    def clone(self):
        return Frame(self.image.copy(), self.x, self.y, self.duration)


def normalize(image: Image.Image, pixel=True) -> Image.Image:
    image = image.convert("RGBA")
    if image.width * image.height > MAX_PIXELS:
        raise ValueError("图片过大，最多支持 1600 万像素")
    scaled = ImageOps.contain(
        image,
        (256, 256),
        Image.Resampling.NEAREST if pixel else Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGBA", (256, 256))
    canvas.alpha_composite(
        scaled, ((256 - scaled.width) // 2, (256 - scaled.height) // 2)
    )
    return canvas


def validate(frame: Frame):
    if not 0 <= frame.x < 256 or not 0 <= frame.y < 256:
        raise ValueError("热点必须在 0～255 之间")
    if not 17 <= frame.duration <= 60000:
        raise ValueError("帧时长应为 17～60000 毫秒")
    if frame.image.size != (256, 256):
        raise ValueError("帧画布必须是 256 × 256")


def encode_cur(frame: Frame, sizes=(64,), pixel=True) -> bytes:
    validate(frame)
    blobs, entries = [], []
    offset = 6 + 16 * len(sizes)
    if not sizes or any(not isinstance(s, int) or not 16 <= s <= 256 for s in sizes):
        raise ValueError("不支持的输出尺寸")
    for s in sizes:
        image = frame.image.resize(
            (s, s), Image.Resampling.NEAREST if pixel else Image.Resampling.LANCZOS
        )
        pixels = image.tobytes("raw", "BGRA", 0, -1)
        stride = ((s + 31) // 32) * 4
        mask = bytearray(stride * s)
        alpha = image.getchannel("A").tobytes()
        for y in range(s):
            for x in range(s):
                if alpha[y * s + x] == 0:
                    mask[(s - 1 - y) * stride + x // 8] |= 1 << (7 - x % 8)
        dib = (
            struct.pack(
                "<IiiHHIIiiII",
                40,
                s,
                s * 2,
                1,
                32,
                0,
                len(pixels) + len(mask),
                0,
                0,
                0,
                0,
            )
            + pixels
            + mask
        )
        hx = min(s - 1, math.floor(frame.x * s / 256 + 0.5))
        hy = min(s - 1, math.floor(frame.y * s / 256 + 0.5))
        entries.append(
            struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, hx, hy, len(dib), offset)
        )
        blobs.append(dib)
        offset += len(dib)
    return struct.pack("<HHH", 0, 2, len(blobs)) + b"".join(entries + blobs)


def decode_cur(data: bytes) -> Frame:
    if len(data) < 22:
        raise ValueError("CUR 文件不完整")
    reserved, typ, count = struct.unpack_from("<HHH", data)
    if (
        reserved != 0
        or typ not in (1, 2)
        or not 1 <= count <= 128
        or len(data) < 6 + count * 16
    ):
        raise ValueError("无效的 CUR / ICO 头部")
    entries = []
    for i in range(count):
        w, h, _, _, a, b, length, offset = struct.unpack_from(
            "<BBBBHHII", data, 6 + 16 * i
        )
        w, h = w or 256, h or 256
        if offset < 6 + 16 * count or offset + length > len(data):
            raise ValueError("CUR 图像数据越界")
        entries.append((w * h, w, h, a, b, data[offset : offset + length]))
    _, w, h, hx, hy, blob = max(entries, key=lambda v: v[0])
    if blob.startswith(b"\x89PNG"):
        image = Image.open(io.BytesIO(blob)).convert("RGBA")
    else:
        if len(blob) < 40:
            raise ValueError("CUR 位图不完整")
        header, bw, bh, planes, bits, compression = struct.unpack_from("<IiiHHI", blob)
        if bits != 32 or compression != 0 or bw != w or abs(bh) != h * 2:
            # Pillow supports common palette/legacy CUR variants.
            image = Image.open(io.BytesIO(data)).convert("RGBA")
        else:
            raw = blob[header : header + w * h * 4]
            if len(raw) != w * h * 4:
                raise ValueError("CUR 像素不完整")
            image = Image.frombytes(
                "RGBA", (w, h), raw, "raw", "BGRA", 0, -1 if bh > 0 else 1
            )
            if image.getchannel("A").getextrema() == (0, 0):
                image.putalpha(255)
                stride = ((w + 31) // 32) * 4
                mask = blob[header + w * h * 4 :]
                if len(mask) >= stride * h:
                    for y in range(h):
                        for x in range(w):
                            if mask[(h - 1 - y) * stride + x // 8] & (1 << (7 - x % 8)):
                                image.putpixel((x, y), (0, 0, 0, 0))
    if typ == 1:
        hx, hy = w // 2, h // 2
    if hx >= w or hy >= h:
        raise ValueError("CUR 热点超出图像")
    scale = min(256 / w, 256 / h)
    return Frame(
        normalize(image),
        min(255, round(hx * scale + (256 - w * scale) / 2)),
        min(255, round(hy * scale + (256 - h * scale) / 2)),
    )


def chunk(tag: bytes, data: bytes) -> bytes:
    return tag + struct.pack("<I", len(data)) + data + (b"\0" if len(data) % 2 else b"")


def encode_ani(frames: list[Frame], sizes=(64,), pixel=True, sequence=None) -> bytes:
    if not 1 <= len(frames) <= MAX_FRAMES:
        raise ValueError("动画帧数必须是 1～512")
    seq = list(range(len(frames))) if sequence is None else list(sequence)
    if (
        not seq
        or len(seq) > MAX_FRAMES * 2
        or any(not 0 <= n < len(frames) for n in seq)
    ):
        raise ValueError("动画顺序无效")
    for f in frames:
        validate(f)
    rates = [max(1, round(frames[i].duration * 60 / 1000)) for i in seq]
    header = struct.pack("<9I", 36, len(frames), len(seq), 0, 0, 32, 1, rates[0], 3)
    payload = b"ACON" + chunk(b"anih", header)
    payload += chunk(b"rate", struct.pack("<" + "I" * len(rates), *rates))
    payload += chunk(b"seq ", struct.pack("<" + "I" * len(seq), *seq))
    payload += chunk(
        b"LIST",
        b"fram" + b"".join(chunk(b"icon", encode_cur(f, sizes, pixel)) for f in frames),
    )
    return b"RIFF" + struct.pack("<I", len(payload)) + payload


def chunks(data: bytes, start: int, end: int):
    while start < end:
        if start + 8 > end:
            raise ValueError("ANI 区块头部不完整")
        tag = data[start : start + 4]
        length = struct.unpack_from("<I", data, start + 4)[0]
        a, b = start + 8, start + 8 + length
        if b > end:
            raise ValueError("ANI 区块超出文件")
        yield tag, data[a:b]
        start = b + (length % 2)


def decode_ani(data: bytes) -> list[Frame]:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"ACON":
        raise ValueError("不是有效的 ANI 文件")
    end = 8 + struct.unpack_from("<I", data, 4)[0]
    if end > len(data):
        raise ValueError("ANI 文件被截断")
    header = None
    rates = None
    seq = None
    images = []
    for tag, payload in chunks(data, 12, end):
        if tag == b"anih":
            if len(payload) < 36:
                raise ValueError("ANI 动画头不完整")
            header = struct.unpack_from("<9I", payload)
        elif tag in (b"rate", b"seq "):
            if len(payload) % 4:
                raise ValueError("ANI 时间 / 顺序区块无效")
            values = list(struct.unpack("<" + "I" * (len(payload) // 4), payload))
            if tag == b"rate":
                rates = values
            else:
                seq = values
        elif tag == b"LIST" and payload[:4] == b"fram":
            for t, p in chunks(payload, 4, len(payload)):
                if t == b"icon":
                    if len(images) >= MAX_FRAMES:
                        raise ValueError("动画超过 512 帧")
                    images.append(decode_cur(p))
    if header is None or not images:
        raise ValueError("ANI 缺少动画帧")
    cb, nframes, nsteps, _, _, _, _, rate, flags = header
    if (
        cb < 36
        or nframes != len(images)
        or not 1 <= nsteps <= MAX_FRAMES
        or not flags & 1
    ):
        raise ValueError("不支持或无效的 ANI 帧信息")
    order = seq if flags & 2 else list(range(nsteps))
    if order is None or len(order) != nsteps or any(i >= len(images) for i in order):
        raise ValueError("ANI 帧顺序无效")
    if rates is not None and len(rates) != nsteps:
        raise ValueError("ANI 帧时长缺失")
    result = []
    for step, i in enumerate(order):
        f = images[i].clone()
        f.duration = max(
            17, min(60000, round((rates[step] if rates else rate or 6) * 1000 / 60))
        )
        result.append(f)
    return result


def import_images(path: Path, pixel=True) -> list[Frame]:
    path = Path(path)
    if path.stat().st_size > 128 * 1024 * 1024:
        raise ValueError("文件超过 128 MB")
    if path.suffix.lower() == ".ani":
        return decode_ani(path.read_bytes())
    if path.suffix.lower() in (".cur", ".ico"):
        return [decode_cur(path.read_bytes())]
    frames = []
    with Image.open(path) as image:
        if image.width * image.height > MAX_PIXELS:
            raise ValueError("图片超过 1600 万像素")
        for raw in ImageSequence.Iterator(image):
            if len(frames) >= MAX_FRAMES:
                raise ValueError("最多支持 512 帧")
            frames.append(
                Frame(
                    normalize(raw.copy(), pixel),
                    duration=max(
                        17, min(60000, int(raw.info.get("duration", 100) or 100))
                    ),
                )
            )
    return frames


def split_sheet(image: Image.Image, columns: int, rows: int, count=None, pixel=True):
    if columns < 1 or rows < 1 or image.width % columns or image.height % rows:
        raise ValueError("行列必须能整除精灵图尺寸")
    count = columns * rows if count is None else count
    if not 1 <= count <= min(MAX_FRAMES, columns * rows):
        raise ValueError("切片帧数无效")
    w, h = image.width // columns, image.height // rows
    return [
        Frame(
            normalize(
                image.crop(
                    (
                        (i % columns) * w,
                        (i // columns) * h,
                        (i % columns + 1) * w,
                        (i // columns + 1) * h,
                    )
                ),
                pixel,
            )
        )
        for i in range(count)
    ]


def transform(frame: Frame, op: str, **kwargs) -> Frame:
    f = frame.clone()
    if op == "flip_h":
        f.image = ImageOps.mirror(f.image)
        f.x = 255 - f.x
    elif op == "flip_v":
        f.image = ImageOps.flip(f.image)
        f.y = 255 - f.y
    elif op == "rotate":
        f.image = f.image.transpose(Image.Transpose.ROTATE_270)
        f.x, f.y = 255 - f.y, f.x
    elif op == "offset":
        dx, dy = kwargs.get("dx", 0), kwargs.get("dy", 0)
        if not 0 <= f.x + dx < 256 or not 0 <= f.y + dy < 256:
            raise ValueError("偏移会使热点超出画布")
        canvas = Image.new("RGBA", (256, 256))
        canvas.alpha_composite(f.image, (dx, dy))
        f.image = canvas
        f.x += dx
        f.y += dy
    elif op == "scale":
        factor = kwargs.get("factor", 1.0)
        if not 0.1 <= factor <= 4:
            raise ValueError("缩放比例应在 0.1～4 之间")
        n = max(1, round(256 * factor))
        pad = (256 - n) // 2
        x, y = round(f.x * factor + pad), round(f.y * factor + pad)
        if not 0 <= x < 256 or not 0 <= y < 256:
            raise ValueError("缩放会使热点超出画布")
        canvas = Image.new("RGBA", (256, 256))
        canvas.alpha_composite(
            f.image.resize(
                (n, n),
                Image.Resampling.NEAREST
                if kwargs.get("pixel", True)
                else Image.Resampling.LANCZOS,
            ),
            (pad, pad),
        )
        f.image = canvas
        f.x, f.y = x, y
    elif op == "crop":
        box = kwargs["box"]
        x0, y0, x1, y1 = box
        if not 0 <= x0 < x1 <= 256 or not 0 <= y0 < y1 <= 256:
            raise ValueError("裁剪范围无效")
        if not x0 <= f.x < x1 or not y0 <= f.y < y1:
            raise ValueError("请将热点设置在裁剪范围内")
        ratio = min(256 / (x1 - x0), 256 / (y1 - y0))
        f.image = normalize(f.image.crop(box), kwargs.get("pixel", True))
        f.x = min(255, round((f.x - x0) * ratio + (256 - (x1 - x0) * ratio) / 2))
        f.y = min(255, round((f.y - y0) * ratio + (256 - (y1 - y0) * ratio) / 2))
    else:
        raise ValueError("未知的图像操作")
    return f
