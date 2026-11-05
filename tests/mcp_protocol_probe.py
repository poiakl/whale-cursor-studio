"""Drive stdio MCP through the real running GUI, including packaged Windows EXEs."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
import wave
from PIL import Image
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def probe(root, server=None, editor=None):
    from whale_cursor.project import Project
    from whale_cursor.mcp_connection import LiveClient

    if sys.platform != "win32":
        raise RuntimeError("This probe validates Windows named pipes")
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parents[1]
    fixture = Project.defaults()
    reference = root / "角色参考.png"
    Image.new("RGBA", (32, 32), "blue").save(reference)
    fixture.settings["ai_prompt"] = dict(
        selected={"slots.Arrow": 2, "effects.click": 2},
        counts={"slots.Arrow": 2, "effects.click": 2},
        size=64,
        columns=2,
        style="蓝色角色",
        references=[str(reference)],
    )
    project = root / "当前方案.whalepack"
    fixture.save(project)
    sheet = root / "生成结果.png"
    Image.new("RGBA", (128, 64), "red").save(sheet)
    audio = root / "点击音效.wav"
    with wave.open(str(audio), "wb") as sound:
        sound.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        sound.writeframes(b"\x00\x00" * 800)
    endpoint = "WhaleCursorProbe-" + uuid.uuid4().hex
    gui = (
        [str(Path(editor).resolve())]
        if editor
        else [sys.executable, str(source / "main.py")]
    )
    env = dict(
        os.environ,
        LOCALAPPDATA=str(root / "user"),
        QT_QPA_PLATFORM="offscreen",
        PYINSTALLER_RESET_ENVIRONMENT="1",
    )
    with (root / ".gui.log").open("w", encoding="utf-8") as gui_log:
        process = subprocess.Popen(
            [*gui, "--open", str(project), "--mcp-endpoint", endpoint],
            env=env,
            stdout=gui_log,
            stderr=gui_log,
        )
        try:
            deadline = time.monotonic() + 40
            while True:
                if process.poll() is not None:
                    raise AssertionError(
                        (root / ".gui.log").read_text(encoding="utf-8")
                    )
                try:
                    channel = LiveClient(endpoint=endpoint).connect()
                    channel.close()
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise AssertionError("GUI did not open its live bridge")
                    await asyncio.sleep(0.1)
            command = str(Path(server).resolve()) if server else sys.executable
            args = [] if server else [str(source / "mcp_entry.py")]
            args += ["--endpoint", endpoint]
            log = root / ".mcp-probe.log"
            with log.open("w", encoding="utf-8") as errlog:
                async with stdio_client(
                    StdioServerParameters(command=command, args=args, env=env),
                    errlog=errlog,
                ) as (reader, writer):
                    async with ClientSession(reader, writer) as session:
                        await session.initialize()
                        names = {t.name for t in (await session.list_tools()).tools}
                        assert names == {
                            "get_generation_request",
                            "apply_generated_image",
                            "get_interaction_settings",
                            "set_click_effect",
                            "preview_click_effect",
                            "configure_interaction_audio",
                        }

                        async def call(tool, **kw):
                            result = await session.call_tool(tool, kw)
                            assert not result.isError, (tool, result)
                            return result

                        result = await call("get_generation_request")
                        request = json.loads(
                            next(
                                block.text
                                for block in result.content
                                if block.type == "text"
                            )
                        )
                        assert any(block.type == "image" for block in result.content)
                        assert (
                            len(request["tasks"]) == 2
                            and "蓝色角色" in request["tasks"][0]["prompt"]
                        )
                        for target in ("slots.Arrow", "effects.click"):
                            await call(
                                "apply_generated_image",
                                request_id=request["request_id"],
                                target=target,
                                image_path=str(sheet),
                                hotspots={"hotspot": [30, 40]},
                            )
                        await call(
                            "set_click_effect",
                            effect="custom",
                            keyframes=[
                                {"t": 0, "x": 0},
                                {"t": 0.5, "x": 40, "y": -20},
                                {"t": 1, "x": 90, "opacity": 0},
                            ],
                            duration_ms=1200,
                        )
                        result = await call("preview_click_effect", progress=[0.2, 0.5])
                        assert (
                            sum(block.type == "image" for block in result.content) == 2
                        )
                        await call(
                            "configure_interaction_audio",
                            trigger="click",
                            files=[str(audio)],
                            mode="sequence",
                            wait_finish=True,
                            interval_ms=350,
                        )
                        result = await call("get_interaction_settings")
                        data = result.structuredContent or json.loads(
                            result.content[0].text
                        )
                        assert data["click_material_frames"] == 2
                        assert data["click_effect"]["effect_motion"] == "keyframes"
                        assert data["audio"]["click"]["interval_ms"] == 350
                        assert data["audio"]["click"]["wait_finish"] is True
                        failed = await session.call_tool(
                            "set_click_effect", {"keyframes": [{"t": 0.5}, {"t": 1}]}
                        )
                        assert failed.isError
            assert "Traceback" not in log.read_text(encoding="utf-8"), log.read_text(
                encoding="utf-8"
            )
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    return True


if __name__ == "__main__":
    # Running this file directly puts tests/, rather than the repository, on sys.path.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    parser = argparse.ArgumentParser()
    parser.add_argument("--server")
    parser.add_argument("--editor")
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    asyncio.run(probe(args.workspace, args.server, args.editor))
    print("MCP live GUI roundtrip passed")
