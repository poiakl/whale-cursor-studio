"""Windows-only boundary. Importing this module is safe on other platforms."""

from __future__ import annotations
import ctypes
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import shutil
import sys
import uuid
import logging
import time
import subprocess
from contextlib import contextmanager
from .project import SLOTS, user_dir

WINDOWS = sys.platform == "win32"
CURSOR_IDS = {
    "Arrow": 32512,
    "IBeam": 32513,
    "Wait": 32514,
    "Crosshair": 32515,
    "UpArrow": 32516,
    "SizeNWSE": 32642,
    "SizeNESW": 32643,
    "SizeWE": 32644,
    "SizeNS": 32645,
    "SizeAll": 32646,
    "No": 32648,
    "Hand": 32649,
    "AppStarting": 32650,
    "Help": 32651,
    "Pin": 32671,
    "Person": 32672,
}
REG_PATH = r"Control Panel\Cursors"
SCHEME = "鲸鱼娘 · 指针工坊"
if WINDOWS:
    import winreg

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi")
    dwmapi.DwmGetWindowAttribute.argtypes = [W.HWND, W.DWORD, ctypes.c_void_p, W.DWORD]
    dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
    user32.LoadImageW.argtypes = [
        W.HINSTANCE,
        W.LPCWSTR,
        W.UINT,
        ctypes.c_int,
        ctypes.c_int,
        W.UINT,
    ]
    user32.LoadImageW.restype = W.HANDLE
    user32.DestroyCursor.argtypes = [W.HANDLE]
    user32.DestroyCursor.restype = W.BOOL
    user32.SetSystemCursor.argtypes = [W.HANDLE, W.DWORD]
    user32.SetSystemCursor.restype = W.BOOL
    user32.SystemParametersInfoW.argtypes = [W.UINT, W.UINT, ctypes.c_void_p, W.UINT]
    user32.SystemParametersInfoW.restype = W.BOOL
    user32.SetWindowPos.argtypes = [
        W.HWND,
        W.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        W.UINT,
    ]
    user32.SetWindowPos.restype = W.BOOL
    user32.GetWindow.argtypes = [W.HWND, W.UINT]
    user32.GetWindow.restype = W.HWND
    user32.IsWindowVisible.argtypes = [W.HWND]
    user32.IsWindowVisible.restype = W.BOOL
    user32.FindWindowW.argtypes = [W.LPCWSTR, W.LPCWSTR]
    user32.FindWindowW.restype = W.HWND
    user32.GetCursorPos.argtypes = [ctypes.POINTER(W.POINT)]
    user32.GetCursorPos.restype = W.BOOL
    user32.WindowFromPoint.argtypes = [W.POINT]
    user32.WindowFromPoint.restype = W.HWND
    user32.GetAncestor.argtypes = [W.HWND, W.UINT]
    user32.GetAncestor.restype = W.HWND
    user32.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowRect.argtypes = [W.HWND, ctypes.POINTER(W.RECT)]
    user32.GetWindowRect.restype = W.BOOL
    user32.MonitorFromWindow.argtypes = [W.HWND, W.DWORD]
    user32.MonitorFromWindow.restype = W.HANDLE
    user32.GetMonitorInfoW.argtypes = [W.HANDLE, ctypes.c_void_p]
    user32.GetMonitorInfoW.restype = W.BOOL
    user32.GetTopWindow.argtypes = [W.HWND]
    user32.GetTopWindow.restype = W.HWND
    user32.SendMessageTimeoutW.argtypes = [
        W.HWND,
        W.UINT,
        W.WPARAM,
        W.LPARAM,
        W.UINT,
        W.UINT,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t
    user32.GetForegroundWindow.restype = W.HWND
    user32.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
    kernel32.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    kernel32.OpenProcess.restype = W.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        W.HANDLE,
        W.DWORD,
        W.LPWSTR,
        ctypes.POINTER(W.DWORD),
    ]
    kernel32.CloseHandle.argtypes = [W.HANDLE]


def require():
    if not WINDOWS:
        raise RuntimeError("安装和系统互动功能需要 Windows 10 / 11")


@contextmanager
def cursor_key(path):
    # Explicit read+write access: installation also reads back each configured path.
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ | winreg.KEY_WRITE
        )
    except FileNotFoundError:
        key = winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ | winreg.KEY_WRITE
        )
    with key:
        yield key


def install_logger():
    logger = logging.getLogger("whale.install")
    logger.setLevel(logging.INFO)
    destination = user_dir() / "install.log"
    for handler in list(logger.handlers):
        if (
            isinstance(handler, logging.FileHandler)
            and Path(handler.baseFilename) != destination
        ):
            logger.removeHandler(handler)
            handler.close()
    if not logger.handlers:
        handler = logging.FileHandler(destination, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger


def snapshot():
    require()
    values = {}
    with cursor_key(REG_PATH) as k:
        for name in list(SLOTS) + ["", "CursorBaseSize", "Scheme Source"]:
            try:
                v, kind = winreg.QueryValueEx(k, name)
                values[name] = [v, kind]
            except FileNotFoundError:
                values[name] = None
    with cursor_key(REG_PATH + r"\Schemes") as k:
        try:
            scheme = list(winreg.QueryValueEx(k, SCHEME))
        except FileNotFoundError:
            scheme = None
    return {"values": values, "scheme": scheme}


def write_snapshot(snap):
    require()
    for path, values in (
        (REG_PATH, snap["values"]),
        (REG_PATH + r"\Schemes", {SCHEME: snap["scheme"]}),
    ):
        with cursor_key(path) as k:
            for name, v in values.items():
                if v is None:
                    try:
                        winreg.DeleteValue(k, name)
                    except FileNotFoundError:
                        pass
                else:
                    winreg.SetValueEx(k, name, 0, v[1], v[0])
    reload()


def reload():
    require()
    ctypes.set_last_error(0)
    # Paths are already persisted in HKCU. SPI_SETCURSORS only reloads them;
    # asking it to rewrite the profile can fail on some Windows configurations.
    if not user32.SystemParametersInfoW(0x57, 0, None, 0):
        error = ctypes.get_last_error()
        raise OSError(
            error,
            "Windows 无法重新载入指针"
            + ("：" + ctypes.FormatError(error) if error else "（系统未提供错误码）"),
        )


def load_cursor(path, size=0):
    require()
    ctypes.set_last_error(0)
    h = user32.LoadImageW(None, str(path), 2, size, size, 0x10)
    if not h:
        error = ctypes.get_last_error()
        raise OSError(
            error,
            "无法加载指针文件 "
            + str(path)
            + ("：" + ctypes.FormatError(error) if error else "（系统未提供错误码）"),
        )
    return h


def validate_file(path, size=0):
    h = load_cursor(path, size)
    user32.DestroyCursor(h)


def install(project, only=None):
    require()
    logger = install_logger()
    stage = "生成 CUR / ANI"
    try:
        folder = user_dir() / "installed" / uuid.uuid4().hex
        logger.info("开始安装 size=%s only=%s folder=%s", project.size, only, folder)
        paths = project.export(folder, only)
        stage = "Windows 加载检查"
        for path in paths.values():
            validate_file(path, project.size)
        stage = "读取安装前方案"
        before = snapshot()
    except Exception as error:
        logger.exception("安装前检查失败 stage=%s", stage)
        raise RuntimeError(
            f"{stage}失败：{error}\n详细日志：{user_dir() / 'install.log'}"
        ) from error
    backup = user_dir() / "before-install.json"
    new = not backup.exists()
    if new:
        tmp = backup.with_suffix(".tmp")
        tmp.write_text(json.dumps(before), encoding="utf-8")
        os.replace(tmp, backup)
    try:
        stage = "写入当前用户指针路径"
        with cursor_key(REG_PATH) as k:
            for key, path in paths.items():
                winreg.SetValueEx(k, key, 0, winreg.REG_SZ, path)
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, SCHEME)
            winreg.SetValueEx(k, "Scheme Source", 0, winreg.REG_DWORD, 2)
            winreg.SetValueEx(k, "CursorBaseSize", 0, winreg.REG_DWORD, project.size)
            all_paths = []
            for key in SLOTS:
                try:
                    all_paths.append(winreg.QueryValueEx(k, key)[0])
                except FileNotFoundError:
                    all_paths.append("")
        with cursor_key(REG_PATH + r"\Schemes") as k:
            winreg.SetValueEx(k, SCHEME, 0, winreg.REG_SZ, ",".join(all_paths))
        stage = "重新载入系统指针"
        reload()
        logger.info("安装成功 count=%s", len(paths))
    except Exception as error:
        logger.exception("安装失败 stage=%s", stage)
        try:
            write_snapshot(before)
            if new:
                backup.unlink(missing_ok=True)
        except Exception as rollback:
            raise RuntimeError(
                f"安装失败：{error}；恢复失败：{rollback}；备份仍保留"
            ) from error
        raise RuntimeError(
            f"{stage}失败，已恢复原方案：{error}\n详细日志：{user_dir() / 'install.log'}"
        ) from error
    return paths


def restore():
    require()
    backup = user_dir() / "before-install.json"
    if not backup.exists():
        raise ValueError("还没有安装前备份")
    snap = json.loads(backup.read_text("utf-8"))
    before = snapshot()
    try:
        write_snapshot(snap)
    except Exception:
        write_snapshot(before)
        raise
    backup.unlink()


def set_live_cursor(role, path, size):
    require()
    if role not in CURSOR_IDS:
        return False
    h = load_cursor(path, size)
    ctypes.set_last_error(0)
    if not user32.SetSystemCursor(h, CURSOR_IDS[role]):
        error = ctypes.get_last_error()
        user32.DestroyCursor(h)
        raise OSError(
            error,
            "无法应用 "
            + role
            + " 互动帧"
            + ("：" + ctypes.FormatError(error) if error else ""),
        )
    return True


def set_live_arrow(path, size):
    return set_live_cursor("Arrow", path, size)


class CursorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", W.DWORD),
        ("flags", W.DWORD),
        ("hCursor", W.HANDLE),
        ("ptScreenPos", W.POINT),
    ]


def current_role():
    if not WINDOWS:
        return "Arrow"
    user32.GetCursorInfo.argtypes = [ctypes.POINTER(CursorInfo)]
    user32.GetCursorInfo.restype = W.BOOL
    user32.LoadCursorW.argtypes = [W.HINSTANCE, ctypes.c_void_p]
    user32.LoadCursorW.restype = W.HANDLE
    info = CursorInfo()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetCursorInfo(ctypes.byref(info)) or not info.flags & 1:
        return None
    for role, identifier in CURSOR_IDS.items():
        if info.hCursor == user32.LoadCursorW(None, ctypes.c_void_p(identifier)):
            return role
    # NWPen has no current standard SetSystemCursor id; its assets still install
    # and animate natively through the registry scheme, and can be previewed.
    return None


def foreground_exe():
    if not WINDOWS:
        return ""
    pid = W.DWORD()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = W.DWORD(len(buf))
        return (
            Path(buf.value).name.lower()
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
            else ""
        )
    finally:
        kernel32.CloseHandle(h)


def button_down(vk):
    return bool(WINDOWS and user32.GetAsyncKeyState(vk) & 0x8000)


def system_settings():
    require()
    os.startfile("main.cpl")


def set_mouse_option(name, value):
    require()
    actions = {
        "trails": 0x005D,
        "shadow": 0x101B,
        "locate": 0x101D,
        "hide_typing": 0x1021,
    }
    if name not in actions:
        raise ValueError("未知系统选项")
    # Mouse trails uses uiParam; the BOOL options use the pvParam value.
    ui, pv = (
        (int(value), None) if name == "trails" else (0, ctypes.c_void_p(int(value)))
    )
    if not user32.SystemParametersInfoW(actions[name], ui, pv, 3):
        raise ctypes.WinError(ctypes.get_last_error())


def recover_runtime():
    """Recover a previous interrupted interaction session before starting a new one."""
    require()
    path = user_dir() / "runtime-recovery.json"
    if not path.exists():
        return False
    info = json.loads(path.read_text("utf-8"))
    write_snapshot(info["snapshot"])
    folder = Path(info["folder"]).resolve()
    root = (user_dir() / "runtime").resolve()
    if folder.parent == root:
        shutil.rmtree(folder, ignore_errors=True)
    path.unlink()
    return True


def restack_overlays(handles):
    """Reassert topmost order without activation, ownership or editor changes."""
    if not WINDOWS or not handles:
        return

    def previous_visible(handle):
        for _ in range(64):
            handle = user32.GetWindow(handle, 3)  # GW_HWNDPREV
            if not handle or user32.IsWindowVisible(handle):
                return handle
        return handle

    top_down = list(reversed(handles))
    if not previous_visible(top_down[0]) and all(
        previous_visible(handle) == above
        for above, handle in zip(top_down, top_down[1:])
    ):
        return
    for handle in handles:
        user32.SetWindowPos(
            handle, W.HWND(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0200
        )


TASKBAR_CLASSES = {
    "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd",
    "TaskListThumbnailWnd",
    "TaskListOverlayWnd",
}
SHELL_CLASSES = {
    "Windows.UI.Core.CoreWindow",
    "XamlExplorerHostIslandWindow",
    "ControlCenterWindow",
    "NotificationCenterWindow",
    "ToastWnd",
    "TopLevelWindowForOverflowXamlIsland",
    "Windows.UI.Composition.DesktopWindowContentBridge",
    "Windows.UI.Input.InputSite.WindowClass",
}
SHELL_PROCESSES = {
    "shellexperiencehost.exe",
    "shellhost.exe",
    "notificationshost.exe",
    "startmenuexperiencehost.exe",
    "searchhost.exe",
    "textinputhost.exe",
    "widgets.exe",
    "widgetservice.exe",
}
_shell_scan_time = -1.0
_shell_candidates = []
_shell_owner_cache = {}


def window_class(window):
    name = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(window, name, len(name))
    return name.value


def window_exe(window):
    now = time.monotonic()
    pid = W.DWORD()
    user32.GetWindowThreadProcessId(window, ctypes.byref(pid))
    cached = _shell_owner_cache.get(pid.value)
    if cached and now - cached[0] < 1:
        return cached[1]
    process = kernel32.OpenProcess(0x1000, False, pid.value)
    result = ""
    if process:
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = W.DWORD(len(buffer))
            if kernel32.QueryFullProcessImageNameW(
                process, 0, buffer, ctypes.byref(size)
            ):
                result = Path(buffer.value).name.lower()
        finally:
            kernel32.CloseHandle(process)
    if len(_shell_owner_cache) > 64:
        _shell_owner_cache.clear()
    _shell_owner_cache[pid.value] = (now, result)
    return result


def visible_rect(window):
    if hasattr(user32, "MonitorFromWindow") and not user32.MonitorFromWindow(window, 0):
        return None
    if not user32.IsWindowVisible(window):
        return None
    cloaked = W.DWORD()
    compositor = globals().get("dwmapi")
    if (
        compositor
        and compositor.DwmGetWindowAttribute(
            window, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
        )
        == 0
        and cloaked.value
    ):
        return None
    rect = W.RECT()
    if (
        user32.GetWindowRect(window, ctypes.byref(rect))
        and rect.right - rect.left > 4
        and rect.bottom - rect.top > 4
    ):
        return rect
    return None


def shell_window(window, name):
    if name in TASKBAR_CLASSES:
        return True
    if name not in SHELL_CLASSES:
        return False
    owner = window_exe(window)
    return owner in SHELL_PROCESSES or owner == "explorer.exe"


def bounded_shell_host(window, rect):
    # Some shell composition hosts stay visible as an empty monitor-sized
    # transparent window. Do not let that disable interaction on the desktop.
    if not hasattr(user32, "MonitorFromWindow"):
        return True

    class MonitorInfo(ctypes.Structure):
        _fields_ = [
            ("size", W.DWORD),
            ("monitor", W.RECT),
            ("work", W.RECT),
            ("flags", W.DWORD),
        ]

    monitor = user32.MonitorFromWindow(window, 0)
    info = MonitorInfo()
    info.size = ctypes.sizeof(info)
    if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return False
    area = max(
        1,
        (info.monitor.right - info.monitor.left)
        * (info.monitor.bottom - info.monitor.top),
    )
    return (rect.right - rect.left) * (rect.bottom - rect.top) < area * 0.9


def reset_shell_scan():
    global _shell_scan_time, _shell_candidates
    _shell_scan_time = -1.0
    _shell_candidates = []
    _shell_owner_cache.clear()


def scan_shell_windows():
    """Enumerate all preview hosts, including secondary displays, without activation."""
    result = []
    window = user32.GetTopWindow(None)
    visited = set()
    while window and window not in visited and len(visited) < 512:
        visited.add(window)
        name = window_class(window)
        if name == "TaskListThumbnailWnd" or (
            name in SHELL_CLASSES and shell_window(window, name)
        ):
            rect = visible_rect(window)
            if rect and (
                name == "TaskListThumbnailWnd" or bounded_shell_host(window, rect)
            ):
                result.append((window, name))
        window = user32.GetWindow(window, 2)  # GW_HWNDNEXT
    return result


def shell_surface_active():
    """Use hardware cursors above taskbar previews and shell notification bands."""
    global _shell_scan_time, _shell_candidates
    if not WINDOWS:
        return False
    point = W.POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        return False
    now = time.monotonic()
    if now < _shell_scan_time or now - _shell_scan_time >= 0.15:
        _shell_candidates = scan_shell_windows()
        _shell_scan_time = now
    for window, name in _shell_candidates:
        rect = visible_rect(window)
        if rect and (
            name == "TaskListThumbnailWnd"
            or rect.left <= point.x < rect.right
            and rect.top <= point.y < rect.bottom
        ):
            return True
    window = user32.WindowFromPoint(point)
    if not window:
        return False
    root = user32.GetAncestor(window, 2) or window
    return shell_window(root, window_class(root)) or shell_window(
        window, window_class(window)
    )


def refresh_pointer_shape():
    """Ask the hovered window to refresh its native cursor after restoring resources."""
    if not WINDOWS:
        return
    point = W.POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        return
    window = user32.WindowFromPoint(point)
    if window:
        result = ctypes.c_size_t()
        user32.SendMessageTimeoutW(
            window,
            0x0020,
            window,
            1 | (0x0200 << 16),
            0x0002 | 0x0020,
            30,
            ctypes.byref(result),
        )


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "WhaleCursorStudio"


def startup_command():
    executable = str(Path(sys.executable).resolve())
    args = [executable]
    if not getattr(sys, "frozen", False):
        args.append(str(Path(__file__).resolve().parent.parent / "main.py"))
    args.append("--startup")
    command = subprocess.list2cmdline(args)
    if len(command) > 260:
        raise ValueError("程序路径过长，无法登记开机自启动。请将程序放到更短的路径。")
    return command


def startup_enabled():
    if not WINDOWS:
        return False
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ
        ) as key:
            value, kind = winreg.QueryValueEx(key, RUN_NAME)
        return (
            kind == winreg.REG_SZ and value.casefold() == startup_command().casefold()
        )
    except (FileNotFoundError, OSError, ValueError):
        return False


def set_startup(enabled):
    require()
    if enabled:
        command = startup_command()
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, command)
    else:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, RUN_NAME)
        except FileNotFoundError:
            pass
