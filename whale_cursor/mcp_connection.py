"""Headless local IPC client for the running studio, without importing Qt."""

import getpass
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

MAX_MESSAGE = 64 * 1024 * 1024


def endpoint_name():
    return (
        "WhaleCursorMCP-" + hashlib.sha256(getpass.getuser().encode()).hexdigest()[:12]
    )


class LiveClient:
    def __init__(self, editor=None, endpoint=None):
        self.editor = Path(editor).resolve() if editor else None
        self.endpoint = endpoint or endpoint_name()

    def launch_editor(self):
        if not self.editor or not self.editor.is_file():
            raise ValueError("请先打开指针工坊，或从设置复制新的 MCP 连接配置。")
        command = (
            [str(self.editor)]
            if self.editor.suffix.lower() == ".exe"
            else [sys.executable, str(self.editor)]
        )
        subprocess.Popen(
            [*command, "--mcp-endpoint", self.endpoint],
            cwd=str(self.editor.parent),
            env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def call(self, operation, **params):
        payload = (
            json.dumps(
                dict(version=1, operation=operation, params=params), ensure_ascii=False
            ).encode("utf-8")
            + b"\n"
        )
        if len(payload) > MAX_MESSAGE:
            raise ValueError("请求超过 64 MB，请减小图片或改用本地图片路径")
        launched = False
        deadline = time.monotonic() + 25
        while True:
            try:
                channel = self.connect()
                break
            except (OSError, FileNotFoundError):
                if not launched:
                    self.launch_editor()
                    launched = True
                if time.monotonic() > deadline:
                    raise ValueError(
                        "工坊连接超时，请检查软件是否打开以及连接配置是否对应当前版本。"
                    ) from None
                time.sleep(0.1)
        with channel:
            if os.name == "nt":
                view = memoryview(payload)
                while view:
                    written = channel.write(view)
                    if not written:
                        raise OSError("工坊请求写入失败")
                    view = view[written:]
            else:
                channel.sendall(payload)
            response = bytearray()
            deadline = time.monotonic() + 60
            while not response.endswith(b"\n"):
                if time.monotonic() > deadline:
                    raise ValueError("工坊响应超时，请关闭软件中的模态对话框后重试。")
                chunk = self.receive(channel)
                if chunk is None:
                    time.sleep(0.01)
                    continue
                if not chunk:
                    raise ValueError("工坊连接已断开")
                response.extend(chunk)
                if len(response) > MAX_MESSAGE:
                    raise ValueError("工坊响应超过 64 MB")
        result = json.loads(response)
        if not result.get("ok"):
            raise ValueError(result.get("error", "工坊操作失败"))
        return result["result"]

    def connect(self):
        if os.name == "nt":
            return open("\\\\.\\pipe\\" + self.endpoint, "r+b", buffering=0)
        channel = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        channel.settimeout(60)
        try:
            channel.connect(str(Path(tempfile.gettempdir()) / self.endpoint))
        except OSError:
            channel.close()
            raise
        return channel

    @staticmethod
    def receive(channel):
        if os.name != "nt":
            return channel.recv(65536)
        import ctypes
        import msvcrt
        from ctypes import wintypes

        available = wintypes.DWORD()
        peek = ctypes.windll.kernel32.PeekNamedPipe
        peek.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        peek.restype = wintypes.BOOL
        if not peek(
            msvcrt.get_osfhandle(channel.fileno()),
            None,
            0,
            None,
            ctypes.byref(available),
            None,
        ):
            raise OSError("工坊命名管道已断开")
        return channel.read(min(65536, available.value)) if available.value else None
