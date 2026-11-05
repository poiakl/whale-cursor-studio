"""Local stdio bridge launch configuration, generated for this installation."""

import json
import sys
from pathlib import Path


def editor_command():
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).with_name("WhaleCursorStudio.exe"))]
    return [sys.executable, str(Path(__file__).resolve().parent.parent / "main.py")]


def client_config(preferences):
    if getattr(sys, "frozen", False):
        command = str(Path(sys.executable).with_name("WhaleCursorMCP.exe"))
        args = []
    else:
        command = sys.executable
        args = [str(Path(__file__).resolve().parent.parent / "mcp_entry.py")]
    args += ["--editor", editor_command()[-1]]
    return {"mcpServers": {"whale-cursor-studio": {"command": command, "args": args}}}


def config_text(preferences):
    return json.dumps(client_config(preferences), ensure_ascii=False, indent=2)
