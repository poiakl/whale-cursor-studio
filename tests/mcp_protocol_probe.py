"""Real stdio roundtrip; also run against the frozen Windows companion in CI."""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from PIL import Image
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def probe(root, server=None):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (64, 32), "red").save(root / "sheet.png")
    source = Path(__file__).resolve().parents[1] / "mcp_entry.py"
    command = str(Path(server).resolve()) if server else sys.executable
    args = [] if server else [str(source)]
    args += ["--workspace", str(root)]
    log = root / ".mcp-probe.log"
    with log.open("w", encoding="utf-8") as errlog:
        async with stdio_client(
            StdioServerParameters(command=command, args=args), errlog=errlog
        ) as (
            reader,
            writer,
        ):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                names = {t.name for t in (await session.list_tools()).tools}
                assert len(names) == 16 and "open_in_editor" in names
                assert (await session.read_resource("whale://capabilities")).contents

                async def call(tool_name, **kwargs):
                    result = await session.call_tool(tool_name, kwargs)
                    assert not result.isError, (tool_name, result)
                    return result

                await call("capabilities")
                project = "协议测试.whalepack"
                # CI may reuse the working directory; delete only this probe's own output.
                (root / project).unlink(missing_ok=True)
                await call("create_project", project=project, name="MCP协议测试")
                await call(
                    "split_sprite_sheet",
                    project=project,
                    target="slots.Arrow",
                    image="sheet.png",
                    columns=2,
                    rows=1,
                    count=2,
                )
                await call(
                    "set_hotspots",
                    project=project,
                    target="slots.Arrow",
                    data={"hotspot": [30, 40]},
                )
                await call(
                    "set_frame_timing",
                    project=project,
                    target="slots.Arrow",
                    durations_ms=[80, 120],
                )
                await call(
                    "configure_sequence",
                    project=project,
                    target="slots.Arrow",
                    controls={"pingpong": True},
                )
                result = await call(
                    "inspect_project", project=project, target="slots.Arrow"
                )
                data = result.structuredContent or json.loads(result.content[0].text)
                assert data["frames"][0]["hotspot"] == [30, 40]
                await call(
                    "set_prompt_sections",
                    project=project,
                    style="水彩",
                    sections=[{"name": "衣服", "prompt": "蓝色长裙"}],
                )
                result = await call(
                    "build_generation_brief",
                    project=project,
                    selected={"slots.Arrow": 2, "events.Hand.hold": 4},
                )
                assert "蓝色长裙" in result.content[0].text
                result = await call(
                    "preview_frame", project=project, target="slots.Arrow"
                )
                assert result.content[0].type == "image"
                await call(
                    "export_animation",
                    project=project,
                    target="slots.Arrow",
                    output="导出.whaleanim",
                )
                await call(
                    "export_cursors", project=project, folder="cursors", role="Arrow"
                )
                assert (root / "cursors/Arrow.ani").is_file()
                result = await session.call_tool(
                    "inspect_project", {"project": "../outside.whalepack"}
                )
                assert result.isError
    assert "Traceback" not in log.read_text(encoding="utf-8"), log.read_text(
        encoding="utf-8"
    )
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--server")
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    asyncio.run(probe(args.workspace, args.server))
    print("MCP stdio roundtrip passed")
