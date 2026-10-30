"""Read the local cursor theme without installing or changing Windows settings."""

import ctypes
import struct
from ctypes import wintypes as W
from pathlib import Path
from PIL import Image
from . import native
from .codec import Frame, SIZES, decode_cur, decode_ani, chunks
from .project import Project, SLOTS, user_dir


def cursor_paths():
    """Only request registry read access, including when the key is absent."""
    if not native.WINDOWS:
        return {}
    registry = native.winreg
    paths = {}
    try:
        with registry.OpenKey(
            registry.HKEY_CURRENT_USER, native.REG_PATH, 0, registry.KEY_READ
        ) as key:
            for role in SLOTS:
                try:
                    value, _ = registry.QueryValueEx(key, role)
                    if isinstance(value, str) and value.strip():
                        paths[role] = Path(
                            registry.ExpandEnvironmentStrings(value).strip('"')
                        )
                except OSError:
                    pass
    except OSError:
        pass
    return paths


def capture_cursor(role):
    """Copy a shared Windows cursor into RGBA; never destroy its shared handle."""
    native.require()
    identifier = native.CURSOR_IDS.get(role)
    if identifier is None:
        return None
    user = native.user32
    gdi = ctypes.WinDLL("gdi32", use_last_error=True)

    class IconInfo(ctypes.Structure):
        _fields_ = [
            ("icon", W.BOOL),
            ("x", W.DWORD),
            ("y", W.DWORD),
            ("mask", W.HBITMAP),
            ("color", W.HBITMAP),
        ]

    class Bitmap(ctypes.Structure):
        _fields_ = [
            ("type", W.LONG),
            ("width", W.LONG),
            ("height", W.LONG),
            ("stride", W.LONG),
            ("planes", W.WORD),
            ("bits", W.WORD),
            ("data", ctypes.c_void_p),
        ]

    class Header(ctypes.Structure):
        _fields_ = [
            ("size", W.DWORD),
            ("width", W.LONG),
            ("height", W.LONG),
            ("planes", W.WORD),
            ("bits", W.WORD),
            ("compression", W.DWORD),
            ("image_size", W.DWORD),
            ("xppm", W.LONG),
            ("yppm", W.LONG),
            ("used", W.DWORD),
            ("important", W.DWORD),
        ]

    user.LoadCursorW.argtypes = [W.HINSTANCE, ctypes.c_void_p]
    user.LoadCursorW.restype = W.HANDLE
    user.GetIconInfo.argtypes = [W.HANDLE, ctypes.POINTER(IconInfo)]
    user.GetIconInfo.restype = W.BOOL
    user.DrawIconEx.argtypes = [
        W.HDC,
        ctypes.c_int,
        ctypes.c_int,
        W.HANDLE,
        ctypes.c_int,
        ctypes.c_int,
        W.UINT,
        W.HBRUSH,
        W.UINT,
    ]
    user.DrawIconEx.restype = W.BOOL
    gdi.GetObjectW.argtypes = [W.HANDLE, ctypes.c_int, ctypes.c_void_p]
    gdi.GetObjectW.restype = ctypes.c_int
    gdi.CreateCompatibleDC.argtypes = [W.HDC]
    gdi.CreateCompatibleDC.restype = W.HDC
    gdi.CreateDIBSection.argtypes = [
        W.HDC,
        ctypes.c_void_p,
        W.UINT,
        ctypes.POINTER(ctypes.c_void_p),
        W.HANDLE,
        W.DWORD,
    ]
    gdi.CreateDIBSection.restype = W.HBITMAP
    gdi.SelectObject.argtypes = [W.HDC, W.HANDLE]
    gdi.SelectObject.restype = W.HANDLE
    gdi.DeleteObject.argtypes = [W.HANDLE]
    gdi.DeleteDC.argtypes = [W.HDC]
    handle = user.LoadCursorW(None, ctypes.c_void_p(identifier))
    info = IconInfo()
    if not handle or not user.GetIconInfo(handle, ctypes.byref(info)):
        return None
    dc = bitmap = previous = None
    try:
        meta = Bitmap()
        if not gdi.GetObjectW(
            info.color or info.mask, ctypes.sizeof(meta), ctypes.byref(meta)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        width, height = meta.width, meta.height if info.color else meta.height // 2
        if not 1 <= width <= 512 or not 1 <= height <= 512:
            raise ValueError("系统指针尺寸超出读取范围")
        dc = gdi.CreateCompatibleDC(None)
        header = Header(ctypes.sizeof(Header), width, -height, 1, 32)
        address = ctypes.c_void_p()
        bitmap = gdi.CreateDIBSection(
            dc, ctypes.byref(header), 0, ctypes.byref(address), None, 0
        )
        if not dc or not bitmap or not address.value:
            raise ctypes.WinError(ctypes.get_last_error())
        previous = gdi.SelectObject(dc, bitmap)
        layers = []
        for fill in (0, 255):
            ctypes.memset(address, fill, width * height * 4)
            if not user.DrawIconEx(dc, 0, 0, handle, width, height, 0, None, 3):
                raise ctypes.WinError(ctypes.get_last_error())
            gdi.GdiFlush()
            layers.append(ctypes.string_at(address, width * height * 4))
        return composite_capture(*layers, width, height), int(info.x), int(info.y)
    finally:
        if previous and dc:
            gdi.SelectObject(dc, previous)
        if bitmap:
            gdi.DeleteObject(bitmap)
        if dc:
            gdi.DeleteDC(dc)
        for owned in (info.mask, info.color):
            if owned:
                gdi.DeleteObject(owned)


def composite_capture(black, white, width, height):
    """Recover alpha from black/white draws, including legacy monochrome masks."""
    rgba = bytearray(width * height * 4)
    for offset in range(0, len(rgba), 4):
        b = black[offset : offset + 3]
        w = white[offset : offset + 3]
        alpha = 255 - max(0, min(255, max(w[i] - b[i] for i in range(3))))
        rgb = [min(255, round(c * 255 / alpha)) if alpha else 0 for c in reversed(b)]
        rgba[offset : offset + 4] = bytes([*rgb, alpha])
    return Image.frombytes("RGBA", (width, height), bytes(rgba))


def read_system_project():
    project = Project(name="本机原有指针", pixel=False)
    project.settings["click_effect"] = "none"
    if not native.WINDOWS:
        return project, []
    paths = cursor_paths()
    sources, warnings = {}, []
    for role in SLOTS:
        captured = None
        try:
            captured = capture_cursor(role)
        except (OSError, ValueError) as error:
            warnings.append(f"{SLOTS[role]}：{error}")
        frames = None
        path = paths.get(role)
        if path:
            try:
                if path.stat().st_size > 64 * 1024 * 1024:
                    raise ValueError("指针文件过大")
                data = path.read_bytes()
                frames = decode_ani(data) if data[:4] == b"RIFF" else [decode_cur(data)]
            except (OSError, ValueError, SyntaxError) as error:
                warnings.append(f"{SLOTS[role]}：文件读取失败，使用系统快照（{error}）")
        if captured:
            image, x, y = captured
            extent = max(image.size)
            if frames and len(frames) > 1:
                sources[role] = (frames, extent)
            else:
                square = Image.new("RGBA", (extent, extent))
                square.paste(image, (0, 0))
                sources[role] = (
                    [
                        Frame(
                            square.resize((256, 256)),
                            min(255, round(x * 256 / extent)),
                            min(255, round(y * 256 / extent)),
                        )
                    ],
                    extent,
                )
        elif frames:
            sources[role] = (frames, source_extent(data))
    if not sources:
        raise RuntimeError("未能读取本机指针；未替换为自带图片，也未修改系统设置。")
    extent = max(value[1] for value in sources.values())
    project.size = next((size for size in SIZES if size >= extent), 256)
    for role, (frames, original_size) in sources.items():
        output = []
        size = min(original_size, project.size)
        for frame in frames:
            canvas = Image.new("RGBA", (project.size, project.size))
            canvas.paste(
                frame.image.resize((size, size), Image.Resampling.LANCZOS), (0, 0)
            )
            output.append(
                Frame(
                    canvas.resize((256, 256), Image.Resampling.NEAREST),
                    min(255, round(frame.x * size / project.size)),
                    min(255, round(frame.y * size / project.size)),
                    frame.duration,
                )
            )
        project.slots[role] = output
    return project, warnings


def source_extent(data):
    """Match the decoder's largest CUR entry, including file-only ANI roles."""
    if data[:4] == b"RIFF":
        for tag, payload in chunks(data, 12, len(data)):
            if tag == b"LIST" and payload[:4] == b"fram":
                for kind, frame in chunks(payload, 4, len(payload)):
                    if kind == b"icon":
                        return source_extent(frame)
        raise ValueError("ANI 缺少图像")
    count = struct.unpack_from("<H", data, 4)[0]
    dimensions = [
        (data[6 + i * 16] or 256, data[7 + i * 16] or 256) for i in range(count)
    ]
    return max(max(dimensions, key=lambda pair: pair[0] * pair[1]))


def bundled_scheme():
    folder = user_dir() / "schemes"
    folder.mkdir(exist_ok=True)
    path = folder / "鲸鱼娘（自带）.whalepack"
    if not path.exists():
        project = Project.defaults()
        project.name = "鲸鱼娘（自带）"
        project.save(path)
    return path
