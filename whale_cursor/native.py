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
}
REG_PATH = r"Control Panel\Cursors"
SCHEME = "鲸鱼娘 · 指针工坊"
if WINDOWS:
    import winreg

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
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


def snapshot():
    require()
    values = {}
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_PATH) as k:
        for name in list(SLOTS) + ["", "CursorBaseSize", "Scheme Source"]:
            try:
                v, kind = winreg.QueryValueEx(k, name)
                values[name] = [v, kind]
            except FileNotFoundError:
                values[name] = None
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_PATH + r"\Schemes") as k:
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
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as k:
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
    if not user32.SystemParametersInfoW(0x57, 0, None, 3):
        raise ctypes.WinError(ctypes.get_last_error())


def load_cursor(path, size=0):
    require()
    h = user32.LoadImageW(None, str(path), 2, size, size, 0x10)
    if not h:
        raise ctypes.WinError(ctypes.get_last_error())
    return h


def validate_file(path, size=0):
    h = load_cursor(path, size)
    user32.DestroyCursor(h)


def install(project, only=None):
    require()
    folder = user_dir() / "installed" / uuid.uuid4().hex
    paths = project.export(folder, only)
    for path in paths.values():
        validate_file(path, project.size)
    before = snapshot()
    backup = user_dir() / "before-install.json"
    new = not backup.exists()
    if new:
        tmp = backup.with_suffix(".tmp")
        tmp.write_text(json.dumps(before), encoding="utf-8")
        os.replace(tmp, backup)
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_PATH) as k:
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
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_PATH + r"\Schemes") as k:
            winreg.SetValueEx(k, SCHEME, 0, winreg.REG_SZ, ",".join(all_paths))
        # Reload through the scheme. SetSystemCursor can flatten animated cursors.
        reload()
    except Exception as error:
        try:
            write_snapshot(before)
            if new:
                backup.unlink(missing_ok=True)
        except Exception as rollback:
            raise RuntimeError(
                f"安装失败：{error}；恢复失败：{rollback}；备份仍保留"
            ) from error
        raise RuntimeError(f"安装失败，已恢复原方案：{error}") from error
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


def set_live_arrow(path, size):
    h = load_cursor(path, size)
    if not user32.SetSystemCursor(h, 32512):
        user32.DestroyCursor(h)
        raise ctypes.WinError(ctypes.get_last_error())


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
